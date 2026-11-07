"""Token meter (zero-token): reads Claude Code's local transcripts and reports GREEN / AMBER / RED against the
caps in project.toml [budget]. Used by the supervisor's autonomous mode to pace work.

Units = input + cache_creation + output + 0.1 * cache_read (an approximate plan weighting; the real plan limit
is not exposed). A real plan-limit hit in a transcript ("resets 9:50pm") forces RED until that time.
  forge meter [--json]
"""
import datetime as dt
import glob
import json
import os
import re

LIMIT_RE = re.compile(r"hit your (?:session|usage) limit[^0-9]{0,20}resets (\d{1,2})(?::(\d{2}))?\s*(am|pm)", re.I)


def ts(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone()


def main(prj, argv):
    b = prj.cfg.get("budget", {})
    day_cap, w5_cap = b.get("daily_cap_units", 20_000_000), b.get("window_5h_cap_units", 8_000_000)
    amber, red = b.get("amber_fraction", 0.7), b.get("red_fraction", 0.9)
    now = dt.datetime.now().astimezone()
    d0, w5 = now.replace(hour=0, minute=0, second=0, microsecond=0), now - dt.timedelta(hours=5)
    files = [f for f in glob.glob(os.path.expanduser("~/.claude/projects/**/*.jsonl"), recursive=True)
             if dt.datetime.fromtimestamp(os.path.getmtime(f)) >= d0.replace(tzinfo=None) - dt.timedelta(days=1)]
    seen, day, win, limited = set(), 0.0, 0.0, None
    for f in files:
        for line in open(f, errors="ignore"):
            if '"usage"' not in line and "resets" not in line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            t = d.get("timestamp")
            if not t:
                continue
            t = ts(t)
            if (d.get("isApiErrorMessage") or d.get("error") == "rate_limit") and (m := LIMIT_RE.search(line)):
                h = int(m.group(1)) % 12 + (12 if m.group(3).lower() == "pm" else 0)
                r = t.replace(hour=h, minute=int(m.group(2) or 0), second=0, microsecond=0)
                r = r + dt.timedelta(days=1) if r <= t else r
                if r > now:
                    limited = min(limited or r, r)
            msg = d.get("message") or {}
            if isinstance(msg, dict) and msg.get("usage"):
                key = msg.get("id") or d.get("uuid")
                if key in seen:
                    continue
                seen.add(key)
                u = msg["usage"]
                units = sum(u.get(k, 0) or 0 for k in ("input_tokens", "cache_creation_input_tokens", "output_tokens")) \
                    + 0.1 * (u.get("cache_read_input_tokens", 0) or 0)
                day += units if t >= d0 else 0
                win += units if t >= w5 else 0
    f = max(day / day_cap, win / w5_cap)
    st = "RED" if limited or f >= red else "AMBER" if f >= amber else "GREEN"
    res = {"status": st, "day_units": int(day), "day_frac": round(day / day_cap, 3), "w5_units": int(win),
           "w5_frac": round(win / w5_cap, 3), "limited_until": limited.isoformat() if limited else None}
    if "--cache" in argv:
        d = prj.p("tools_data", "cache")
        os.makedirs(d, exist_ok=True)
        json.dump(dict(res, at=now.isoformat(timespec="seconds")), open(os.path.join(d, "meter.json"), "w"))
        return 0
    print(json.dumps(res) if "--json" in argv else
          f"TOKENS {st} | today {day / 1e6:.1f}M ({day / day_cap:.0%}) | last 5 h {win / 1e6:.1f}M ({win / w5_cap:.0%})"
          + (f" | plan limit until {limited:%H:%M}" if limited else ""))
    return 0
