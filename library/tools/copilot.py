#!/usr/bin/env python3
"""kicad-copilot - the engine behind the Claude co-pilot (usable on its own too).

  kicad-copilot wait [DIR] [--timeout 1800]   block until a .kicad_sch/.kicad_pcb is saved (exit 0),
                                              or timeout (exit 3). Prints what changed.
  kicad-copilot snapshot [DIR] [--all] [--json]
        run every check (sch_check + ERC, pcb_check, DRC with schematic parity), store the result in
        DIR/.copilot/state.json and print progress plus NEW / RESOLVED / CHANGED issues since the last
        snapshot (--all: every open issue).
  kicad-copilot status [DIR]                  last snapshot, without re-running anything
  kicad-copilot boards [N]                    show/set build quantity for Mouser pricing/stock (default 1)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
CLI = "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli"
KPY = "/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3"
GROUPED = ("ERC-", "DRC-")   # one entry per violation type; the count may change


def project(arg) -> Path:
    p = Path(arg or ".").expanduser().resolve()
    if p.is_file():
        p = p.parent
    while p != p.parent:
        pros = list(p.glob("*.kicad_pro"))
        if len(pros) == 1:
            return pros[0]
        p = p.parent
    sys.exit("not inside a KiCad project folder")


def config(pro: Path) -> dict:
    f = pro.parent / ".copilot" / "config.json"
    try:
        return json.loads(f.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def boards(pro: Path) -> int:
    return int(config(pro).get("boards", 1))


def cmd_boards(a):
    pro = project(a.dir)
    d = pro.parent / ".copilot"
    d.mkdir(exist_ok=True)
    cfg = config(pro)
    if a.n:
        cfg["boards"] = a.n
        (d / "config.json").write_text(json.dumps(cfg, indent=1))
    print(f"build quantity for {pro.stem}: {cfg.get('boards', 1)} board(s)")
    return 0


def design_files(pro: Path):
    return sorted(pro.parent.glob("*.kicad_sch")) + sorted(pro.parent.glob("*.kicad_pcb"))


def cmd_wait(a):
    pro = project(a.dir)
    def stamp():
        return {f.name: f.stat().st_mtime for f in design_files(pro)}
    base, t0 = stamp(), time.time()
    while time.time() - t0 < a.timeout:
        time.sleep(2)
        cur = stamp()
        if cur != base:
            time.sleep(3)            # debounce: KiCad writes several files on save
            changed = sorted(k for k, v in stamp().items() if base.get(k) != v)
            print(f"saved: {', '.join(changed) or 'files'} at {datetime.now():%H:%M:%S}")
            return 0
    print(f"no save in {a.timeout}s")
    return 3


def _run_json(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return {"items": [{"severity": "ERROR", "code": "TOOL-FAIL", "message": f"{Path(cmd[1]).name}: {r.stderr.strip()[:300]}", "fix": ""}]}


def collect(pro: Path):
    name = pro.with_suffix("")
    items, progress = [], {}
    sch = name.with_suffix(".kicad_sch")
    if sch.exists():
        d = _run_json([sys.executable, str(TOOLS / "sch_check.py"), str(sch), "--json"])
        items += d.get("items", [])
        progress.update(parts=d.get("parts"), nets=d.get("nets"),
                        unnamed_ic_nets=sum(i["code"] == "NET-UNNAMED" for i in d.get("items", [])))
        sourcing = None
        if not os.environ.get("KICAD_COPILOT_NO_MOUSER"):
            try:
                import mouser
                rows, src_items, summary = mouser.analyse(str(sch), boards(pro))
                items += src_items
                sourcing = {"rows": rows, "summary": summary}
                if summary["priced"]:
                    progress["BOM"] = f"{summary['sym']}{summary['per_board']:,.2f}/board @{summary['boards']}"
            except SystemExit as e:        # no API key
                items.append({"severity": "INFO", "code": "SRC-API", "message": str(e), "fix": ""})
            except Exception as e:
                items.append({"severity": "INFO", "code": "SRC-API", "message": f"Mouser check skipped: {e}", "fix": ""})
        progress["_sourcing"] = sourcing
    pcb = name.with_suffix(".kicad_pcb")
    if pcb.exists():
        d = _run_json([KPY, str(TOOLS / "pcb_check.py"), str(pcb), "--json"])
        items += d.get("items", [])
        progress["footprints"] = d.get("footprints")
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "drc.json"
            subprocess.run([CLI, "pcb", "drc", "--format", "json", "--schematic-parity", "--severity-error",
                            "--severity-warning", "-o", str(out), str(pcb)], capture_output=True)
            if out.exists():
                drc = json.loads(out.read_text())
                groups = {}
                for v in drc.get("violations", []) + drc.get("schematic_parity", []):
                    groups.setdefault((v["severity"], v["type"]), []).append(
                        "; ".join(i["description"] for i in v.get("items", [])[:2]))
                for (sev, typ), where in sorted(groups.items()):
                    items.append({"severity": "ERROR" if sev == "error" else "WARN", "code": f"DRC-{typ}",
                                  "message": f"{len(where)}× {typ}: " + " | ".join(where[:3]) + (" …" if len(where) > 3 else ""),
                                  "fix": "Inspect → Design Rules Checker"})
                progress["unrouted"] = len(drc.get("unconnected_items", []))
    return items, progress


def key(i):
    """Identity of an issue across snapshots: keep refs/IDs (R3, IC-0017), drop measurements that move."""
    if i["code"].startswith(GROUPED):
        return i["code"]
    if i["code"].startswith("SRC-"):
        return i["code"] + "|" + i["message"].split(":")[0]          # refs + MPN
    return i["code"] + "|" + re.sub(r"\d[\d,]*\.\d+|\d{1,3}(?:,\d{3})+", "#", i["message"])


def _count(msg):
    m = re.match(r"(\d+)×", msg)
    return m.group(1) if m else None


def cmd_snapshot(a):
    pro = project(a.dir)
    state_dir = pro.parent / ".copilot"
    state_dir.mkdir(exist_ok=True)
    state_file = state_dir / "state.json"
    prev = json.loads(state_file.read_text()) if state_file.exists() else None
    items, progress = collect(pro)
    sourcing = progress.pop("_sourcing", None)
    prev_mpns = {r["mpn"] for r in ((prev or {}).get("sourcing") or {}).get("rows", [])}
    new_parts = [r for r in (sourcing or {}).get("rows", []) if r["mpn"] not in prev_mpns] if prev is not None else []
    now = {key(i): i for i in items}
    old = {key(i): i for i in (prev or {}).get("items", [])}
    new = [now[k] for k in now if k not in old]
    resolved = [old[k] for k in old if k not in now]
    changed = [(old[k], now[k]) for k in now if k in old and (
        _count(old[k]["message"]) != _count(now[k]["message"]) if k.startswith(GROUPED)
        else k.startswith("SRC-STOCK") and old[k]["message"] != now[k]["message"])]
    state_file.write_text(json.dumps({"time": datetime.now().isoformat(timespec="seconds"),
                                      "progress": progress, "items": items, "sourcing": sourcing}, indent=1))
    counts = {s: sum(i["severity"] == s for i in items) for s in ("ERROR", "WARN", "INFO")}
    if a.json:
        print(json.dumps({"project": pro.stem, "first_run": prev is None, "progress": progress, "counts": counts,
                          "new": new, "resolved": resolved, "new_parts_sourcing": new_parts,
                          "changed": [{"from": o["message"], "to": n["message"], "code": n["code"]} for o, n in changed],
                          "open": items if (a.all or prev is None) else None}, indent=1))
        return 0
    prog = ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in progress.items() if v is not None)
    print(f"{pro.stem} @ {datetime.now():%H:%M:%S} — {prog}")
    print(f"open: {counts['ERROR']} errors, {counts['WARN']} warnings, {counts['INFO']} info")
    show = items if (a.all or prev is None) else new
    if show:
        print("\nALL OPEN" if (a.all or prev is None) else "\nNEW")
        for i in sorted(show, key=lambda i: ("ERROR", "WARN", "INFO").index(i["severity"])):
            print(f"  {i['severity']:5} {i['code']:18} {i['message']}")
            if i.get("fix"):
                print(f"  {'':24} → {i['fix']}")
    if new_parts:
        print("\nNEW PARTS (Mouser, live)")
        for r in new_parts:
            if r["found"]:
                print(f"  {r['refs'][:16]:16} {r['mpn'][:24]:24} {r['sym']}{r['unit']:,.2f}/pc @{r['need']}  stock {r['stock']:,}"
                      f"  lead {r['lead_time'] or '?'}  {r['lifecycle'] or ''}")
            else:
                print(f"  {r['refs'][:16]:16} {r['mpn'][:24]:24} not listed at Mouser")
    for o, n in changed:
        print(f"  CHANGED {n['code']}: {o['message'][:70]}  ⇒  {n['message'][:70]}")
    if resolved:
        print("\nRESOLVED")
        for i in resolved:
            print(f"  ✓ {i['code']:18} {i['message'][:110]}")
    if prev is not None and not (new or resolved or changed):
        print("no change since last check")
    return 0


def cmd_status(a):
    pro = project(a.dir)
    f = pro.parent / ".copilot" / "state.json"
    if not f.exists():
        sys.exit("no snapshot yet: run `kicad-copilot snapshot`")
    s = json.loads(f.read_text())
    print(f"{pro.stem} — last check {s['time']} — {s['progress']}")
    for i in s["items"]:
        print(f"  {i['severity']:5} {i['code']:18} {i['message'][:120]}")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    s = sp.add_parser("wait"); s.add_argument("dir", nargs="?"); s.add_argument("--timeout", type=int, default=1800)
    s.set_defaults(fn=cmd_wait)
    s = sp.add_parser("snapshot"); s.add_argument("dir", nargs="?"); s.add_argument("--all", action="store_true")
    s.add_argument("--json", action="store_true"); s.set_defaults(fn=cmd_snapshot)
    s = sp.add_parser("status"); s.add_argument("dir", nargs="?"); s.set_defaults(fn=cmd_status)
    s = sp.add_parser("boards", help="show/set build quantity used for pricing and stock checks")
    s.add_argument("n", nargs="?", type=int); s.add_argument("dir", nargs="?"); s.set_defaults(fn=cmd_boards)
    a = ap.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
