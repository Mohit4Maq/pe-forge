"""forge learn / feedback / issue: learn from every user of pe-forge, without taking their design.

  forge learn [summary] [--json]     what the tool has learned on this machine / project (local only)
  forge learn show [--n N]           the last N raw events
  forge learn off | on | clear       stop / resume recording; delete the log
  forge feedback [--note TEXT] [--keep-names] [--out FILE]
                                     a redacted summary to send to the pe-forge team; printed for review first,
                                     nothing leaves the machine
  forge issue [--last] [--kind bug|rule|check|idea] [--rule ID] [--title T] [--note TEXT]
              [--with-summary] [--keep-names] [--repo OWNER/NAME] [--dry-run] [--yes]
                                     one-step GitHub issue with the context filled in: shows the issue, asks, then
                                     files it with `gh` (if installed and logged in) or opens the pre-filled
                                     new-issue page in the browser

What is recorded (<config>/learn/<project>-<hash>/events.jsonl, outside the project tree; <config> is
$PE_FORGE_CONFIG_DIR or ~/.config/pe-forge):
  command names and exit codes, check and gate outcomes, rule IDs that fail or are waived (with the waiver reason),
  review verdicts and the count of findings no tool check caught ("escaped"), and tool errors (type, message,
  traceback lines inside pe-forge). Never netlists, geometry, part lists, file contents or project files.
Opt out: `forge learn off`, PE_FORGE_LEARN=0, or project.toml [learn] enabled = false.
Nothing is ever sent automatically; `feedback` and `issue` show everything first and redact by default
(home paths, file paths, net names, e-mail addresses, the project name).
Issue repository: --repo, $PE_FORGE_ISSUE_REPO, project.toml [learn] issue_repo, <config>/config.json issue_repo,
else the default below.
"""
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import traceback
import urllib.parse

DEFAULT_REPO = "Mohit4Maq/pe-forge-feedback"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KINDS = {"bug": "Bug", "rule": "Rule does not fit", "check": "Missing or wrong check", "idea": "Idea"}
RULE_RE = re.compile(r"\b(?:(?:STK|PL|GD|SW|GND|SNS|FLT|TH|TX|HC|HV)-\d+|R-1\d|PROT-\d|INS-\d|NET-\d|CLS-\d|"
                     r"PCB-[A-Z]+|L-\d+[a-z]?|G\d)\b")


# ---------- where ----------
def config_dir():
    return os.environ.get("PE_FORGE_CONFIG_DIR") or os.path.expanduser("~/.config/pe-forge")


def _project_root():
    try:
        from . import config
        return config.find_root()
    except Exception:
        return None


def log_dir(root=None):
    """Per project, but outside the project tree (never in the user's repo, the change watch or a transaction):
    <config>/learn/<folder name>-<hash of the path>/; outside a project <config>/learn/_global/."""
    import hashlib
    root = root or _project_root()
    if not root:
        return os.path.join(config_dir(), "learn", "_global")
    root = os.path.realpath(root)
    key = re.sub(r"[^\w.-]", "_", os.path.basename(root))[:40] + "-" + hashlib.sha1(root.encode()).hexdigest()[:8]
    return os.path.join(config_dir(), "learn", key)


def _settings():
    p = os.path.join(config_dir(), "config.json")
    try:
        return json.load(open(p))
    except (OSError, ValueError):
        return {}


def _save_settings(s):
    os.makedirs(config_dir(), exist_ok=True)
    json.dump(s, open(os.path.join(config_dir(), "config.json"), "w"), indent=1)


def enabled(root=None):
    if os.environ.get("PE_FORGE_LEARN", "1") in ("0", "false", "off"):
        return False
    if _settings().get("learn") is False:
        return False
    root = root or _project_root()
    if root:
        try:
            import tomllib
            cfg = tomllib.load(open(os.path.join(root, "project.toml"), "rb"))
            if cfg.get("learn", {}).get("enabled") is False:
                return False
        except Exception:
            pass
    return True


def version():
    try:
        v = open(os.path.join(ROOT, "VERSION")).read().strip()
    except OSError:
        v = "unknown"
    try:
        sha = subprocess.run(["git", "-C", ROOT, "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                             timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        sha = ""
    return f"{v}+{sha}" if sha else v


# ---------- record ----------
def record(kind, root=None, **fields):
    """Append one event. Never raises: learning must not break the tool."""
    try:
        if not enabled(root):
            return
        d = log_dir(root)
        os.makedirs(d, exist_ok=True)
        ev = {"t": time.strftime("%Y-%m-%dT%H:%M:%S"), "kind": kind}
        ev.update(fields)
        with open(os.path.join(d, "events.jsonl"), "a") as f:
            f.write(json.dumps(ev, default=str) + "\n")
    except Exception:
        pass


def rule_ids(text):
    return sorted(set(RULE_RE.findall(text or "")))


def record_check(prj, res):
    for n, r in res["checks"].items():
        ids = sorted({i for f in r["findings"] for i in rule_ids(f"{f.get('claim', '')} {f.get('actual', '')}")})
        record("check", prj.root, check=n, status=r["status"], findings=len(r["findings"]), rules=ids)


def record_hp(prj, rows):
    for r in rows:
        if r["status"] in ("AUTO FAIL", "WAIVED", "WAIVER INVALID"):
            record("rule", prj.root, rule=r["id"], strength=r["strength"], status=r["status"],
                   reason=r["evidence"] if r["status"] != "AUTO FAIL" else "")


def record_gate(prj, gate, res):
    record("gate", prj.root, gate=gate, ok=all(ok for ok, _ in res), failed=sum(1 for ok, _ in res if not ok))


def record_review(prj, path, verdict):
    """Verdict plus the findings no rule row covered (escaped: candidates for new checks)."""
    try:
        t = open(path).read()
    except OSError:
        return
    other = t.split("## Other findings", 1)[1].split("\n## ", 1)[0] if "## Other findings" in t else ""
    # finding IDs that open a finding line (C-n Critical, M-n Major, m-n Minor); rows that only report the status
    # of an earlier review's finding (CLOSED / OPEN / PARTLY) are not new findings
    ids = set()
    for line in other.splitlines():
        m = re.match(r"^\s*(?:[-*|#]+\s*)*\**\s*([CMm])-(\d+)\b", line)
        if m and not re.search(r"\b(CLOSED|PARTLY|OPEN|KNOWN GAP)\b", line):
            ids.add(m.groups())
    sev = {k: sum(1 for c, _ in ids if c == s) for k, s in (("Critical", "C"), ("Major", "M"), ("Minor", "m"))}
    failed = sorted({m.group(1) for m in re.finditer(r"^\|\s*(" + RULE_RE.pattern[2:-2] + r")\s*\|[^|]*\|\s*FAIL",
                                                      t, re.M)})
    key = (os.path.basename(path), verdict)
    seen = os.path.join(log_dir(prj.root), "reviews_seen.json")
    try:
        done = json.load(open(seen))
    except (OSError, ValueError):
        done = []
    if list(key) in done:
        return
    record("review", prj.root, form=os.path.basename(path), verdict=verdict, escaped=sev, failed_rules=failed)
    try:
        json.dump(done + [list(key)], open(seen, "w"))
    except OSError:
        pass


def record_error(cmd, exc):
    tb = [ln for ln in traceback.format_exception(type(exc), exc, exc.__traceback__)]
    mine = [ln.strip().replace(ROOT, "pe-forge").replace("\\", "/") for ln in tb
            if ROOT in ln or "/forge/" in ln or "\\forge\\" in ln]
    err = {"t": time.strftime("%Y-%m-%dT%H:%M:%S"), "command": cmd, "type": type(exc).__name__,
           "message": str(exc)[:500], "trace": mine[-8:] + [tb[-1].strip()], "version": version(),
           "python": platform.python_version(), "os": f"{platform.system()} {platform.release()}"}
    record("error", **{k: v for k, v in err.items() if k != "t"})
    try:
        os.makedirs(config_dir(), exist_ok=True)
        json.dump(err, open(os.path.join(config_dir(), "last_error.json"), "w"), indent=1)
    except OSError:
        pass


def last_error():
    try:
        return json.load(open(os.path.join(config_dir(), "last_error.json")))
    except (OSError, ValueError):
        return None


# ---------- read ----------
def events(root=None):
    p = os.path.join(log_dir(root), "events.jsonl")
    out = []
    try:
        for line in open(p):
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    except OSError:
        pass
    return out


def summary(evs):
    from collections import Counter
    s = {"events": len(evs), "since": evs[0]["t"] if evs else None}
    s["commands"] = dict(Counter(e["command"] for e in evs if e["kind"] == "command").most_common(12))
    s["failing_rules"] = dict(Counter(e["rule"] for e in evs if e["kind"] == "rule" and e["status"] == "AUTO FAIL")
                              .most_common(15))
    ck = Counter(r for e in evs if e["kind"] == "check" and e["status"] in ("fail", "warn") for r in e.get("rules", []))
    s["check_rule_hits"] = dict(ck.most_common(15))
    s["failing_checks"] = dict(Counter(e["check"] for e in evs if e["kind"] == "check" and e["status"] == "fail")
                               .most_common(10))
    w = {}
    for e in evs:
        if e["kind"] == "rule" and e["status"] in ("WAIVED", "WAIVER INVALID"):
            w[e["rule"]] = e.get("reason", "")
    s["waivers"] = w
    rv = [e for e in evs if e["kind"] == "review"]
    s["reviews"] = {"count": len(rv), "verdicts": dict(Counter(e["verdict"] for e in rv)),
                    "escaped": {k: sum(e["escaped"].get(k, 0) for e in rv) for k in ("Critical", "Major", "Minor")},
                    "failed_rules": dict(Counter(r for e in rv for r in e.get("failed_rules", [])).most_common(10))}
    g = [e for e in evs if e["kind"] == "gate"]
    s["gates"] = {k: {"runs": sum(1 for e in g if e["gate"] == k), "passed": sum(1 for e in g if e["gate"] == k and e["ok"])}
                  for k in sorted({e["gate"] for e in g})}
    er = [e for e in evs if e["kind"] == "error"]
    s["errors"] = [{"command": e.get("command"), "type": e.get("type"), "message": e.get("message", "")[:160]}
                   for e in er[-5:]]
    return s


# ---------- redaction ----------
def redact(text, prj_name=None, keep_names=False):
    home = os.path.expanduser("~")
    text = text.replace(home, "~")
    text = re.sub(r"[\w.+-]+@[\w-]+\.[\w.]+", "<email>", text)
    if keep_names:
        return text
    text = re.sub(r"(~|/(?:Users|home|private|tmp|var|Volumes))(/[^\s'\"|,;:)]+)+", "<path>", text)
    text = re.sub(r"(?<![\w<])/[A-Za-z_][\w+.~-]*", "<net>", text)       # KiCad net names /VIN, /SW ...
    if prj_name:
        text = re.sub(re.escape(prj_name), "<project>", text, flags=re.I)
    return text


def environment():
    from . import kicad
    kv = ""
    try:
        cli = kicad.find_cli() if hasattr(kicad, "find_cli") else None
        if cli:
            kv = subprocess.run([cli, "version"], capture_output=True, text=True, timeout=20).stdout.strip()
    except Exception:
        kv = ""
    return {"pe-forge": version(), "os": f"{platform.system()} {platform.release()} {platform.machine()}",
            "python": platform.python_version(), "kicad": kv or "not found"}


def _prj_name(root):
    if not root:
        return None
    try:
        import tomllib
        cfg = tomllib.load(open(os.path.join(root, "project.toml"), "rb"))
        return cfg.get("project", {}).get("name") or cfg.get("name") or os.path.basename(root)
    except Exception:
        return os.path.basename(root)


def feedback_text(note="", keep_names=False, root=None):
    root = root or _project_root()
    s = summary(events(root))
    env = environment()
    L = ["## pe-forge feedback", "", "| | |", "|---|---|"] + [f"| {k} | {v} |" for k, v in env.items()]
    if note:
        L += ["", "### Note", note]
    L += ["", f"### What the tool saw ({s['events']} events since {s['since']})"]
    if s["failing_rules"]:
        L += ["Rule-book rules failing most: " + ", ".join(f"{k} x{v}" for k, v in s["failing_rules"].items())]
    if s["check_rule_hits"]:
        L += ["Rule IDs in check findings: " + ", ".join(f"{k} x{v}" for k, v in s["check_rule_hits"].items())]
    if s["failing_checks"]:
        L += ["Checks failing: " + ", ".join(f"{k} x{v}" for k, v in s["failing_checks"].items())]
    if s["waivers"]:
        L += ["", "Waivers (rules that did not fit, with the reason):"] + [f"- {k}: {v}" for k, v in s["waivers"].items()]
    r = s["reviews"]
    if r["count"]:
        L += ["", f"Reviews: {r['count']} ({', '.join(f'{k} {v}' for k, v in r['verdicts'].items())}); findings no check "
                  f"caught: {r['escaped']}; rule rows failed: {r['failed_rules'] or 'none'}"]
    if s["gates"]:
        L += ["Gates: " + ", ".join(f"{k} {v['passed']}/{v['runs']} passed" for k, v in s["gates"].items())]
    if s["errors"]:
        L += ["", "Tool errors:"] + [f"- `{e['command']}`: {e['type']}: {e['message']}" for e in s["errors"]]
    return redact("\n".join(L), _prj_name(root), keep_names)


# ---------- issue ----------
def issue_repo(prj_cfg=None):
    return (os.environ.get("PE_FORGE_ISSUE_REPO") or (prj_cfg or {}).get("learn", {}).get("issue_repo")
            or _settings().get("issue_repo") or DEFAULT_REPO)


def issue_text(kind="bug", title=None, note="", rule=None, use_last=False, with_summary=False, keep_names=False,
               root=None):
    root = root or _project_root()
    env = environment()
    err = last_error() if use_last else None
    if not title:
        if err:
            title = f"{err['command']}: {err['type']}: {err['message'][:80]}"
        elif rule:
            title = f"{rule} does not fit this board"
        else:
            title = (note.splitlines()[0][:80] if note else "pe-forge feedback")
    title = f"[{kind}] {title}"
    B = [f"**Kind:** {KINDS.get(kind, kind)}"]
    if rule:
        B.append(f"**Rule:** {rule}")
    B += ["", note or "_(describe what you expected and what happened)_", "", "**Environment**", ""]
    B += [f"- {k}: {v}" for k, v in env.items()]
    if err:
        B += ["", f"**Last error** ({err['t']}, `forge {err['command']}`)", "", "```",
              f"{err['type']}: {err['message']}"] + err.get("trace", []) + ["```"]
    if rule:
        evs = [e for e in events(root) if e.get("rule") == rule]
        if evs:
            e = evs[-1]
            B += ["", f"**Last result for {rule}:** {e['status']}" + (f" ({e.get('reason')})" if e.get("reason") else "")]
    if with_summary:
        B += ["", "<details><summary>What the tool saw</summary>", "", feedback_text(keep_names=keep_names, root=root),
              "", "</details>"]
    B += ["", "_Filed with `forge issue`. Paths, net names and the project name are redacted unless --keep-names._"]
    name = _prj_name(root)
    return redact(title, name, keep_names), redact("\n".join(B), name, keep_names)


def issue_url(repo, title, body, limit=6000):
    if len(body) > limit:
        body = body[:limit] + "\n\n_(cut to fit the link: run `forge issue` with `gh` installed to send it whole)_"
    return f"https://github.com/{repo}/issues/new?" + urllib.parse.urlencode({"title": title, "body": body})


def gh_ready():
    gh = shutil.which("gh")
    if not gh:
        return None
    try:
        ok = subprocess.run([gh, "auth", "status"], capture_output=True, text=True, timeout=20).returncode == 0
    except (OSError, subprocess.SubprocessError):
        ok = False
    return gh if ok else None


def file_issue(repo, title, body, out=print):
    """Create it with gh, else open the pre-filled page. Returns (how, url_or_output)."""
    gh = gh_ready()
    if gh:
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
            f.write(body)
        try:
            r = subprocess.run([gh, "issue", "create", "--repo", repo, "--title", title, "--body-file", f.name],
                               capture_output=True, text=True)
        except OSError as e:                          # gh present but not runnable: fall back, never crash
            r = subprocess.CompletedProcess([gh], 1, "", str(e))
        os.unlink(f.name)
        if r.returncode == 0:
            return "gh", r.stdout.strip()
        out(f"gh could not create it ({(r.stderr or r.stdout).strip()[:200]}); opening the browser instead")
    url = issue_url(repo, title, body)
    if os.environ.get("PE_FORGE_NO_BROWSER") != "1":
        try:
            import webbrowser
            webbrowser.open(url)
        except Exception:
            pass
    return "browser", url


def _arg(argv, flag, default=None):
    return argv[argv.index(flag) + 1] if flag in argv and argv.index(flag) + 1 < len(argv) else default


def main_learn(argv):
    root = _project_root()
    sub = argv[0] if argv and not argv[0].startswith("-") else "summary"
    if sub == "off":
        s = _settings(); s["learn"] = False; _save_settings(s)
        print("learning off on this machine (forge learn on to resume)"); return 0
    if sub == "on":
        s = _settings(); s["learn"] = True; _save_settings(s)
        print("learning on: events go to " + log_dir(root)); return 0
    if sub == "clear":
        p = os.path.join(log_dir(root), "events.jsonl")
        if os.path.exists(p):
            os.remove(p)
        print("learning log cleared"); return 0
    evs = events(root)
    if sub == "show":
        for e in evs[-int(_arg(argv, "--n", 20)):]:
            print(json.dumps(e))
        return 0
    s = summary(evs)
    if "--json" in argv:
        print(json.dumps(s, indent=1)); return 0
    print(f"learning {'on' if enabled(root) else 'OFF'}: {len(evs)} events in {log_dir(root)}")
    for k in ("failing_rules", "check_rule_hits", "failing_checks", "waivers", "reviews", "gates", "errors"):
        if s[k]:
            print(f"  {k.replace('_', ' ')}: {json.dumps(s[k])}")
    print("send it to the pe-forge team: forge feedback   |   report one thing: forge issue")
    return 0


def main_feedback(argv):
    txt = feedback_text(note=_arg(argv, "--note", ""), keep_names="--keep-names" in argv)
    out = _arg(argv, "--out") or os.path.join(log_dir(), f"feedback-{time.strftime('%Y%m%d-%H%M')}.md")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    open(out, "w").write(txt + "\n")
    print(txt)
    print(f"\nSaved to {out}. Nothing was sent. Read it, edit it if you like, then send it to the pe-forge team, "
          "or file it as an issue: forge issue --kind idea --with-summary")
    return 0


def main_issue(argv, prj_cfg=None):
    kind = _arg(argv, "--kind", "bug")
    if kind not in KINDS:
        raise SystemExit(f"--kind is one of {', '.join(KINDS)}")
    title, body = issue_text(kind, _arg(argv, "--title"), _arg(argv, "--note", ""), _arg(argv, "--rule"),
                             use_last="--last" in argv, with_summary="--with-summary" in argv,
                             keep_names="--keep-names" in argv)
    repo = _arg(argv, "--repo") or issue_repo(prj_cfg)
    print(f"Issue for github.com/{repo}\n\nTitle: {title}\n\n{body}\n")
    if "--dry-run" in argv:
        print(issue_url(repo, title, body))
        return 0
    if "--yes" not in argv:
        from .config import interactive
        if not interactive():
            print("not a terminal: add --yes to file it, or --dry-run to only show it"); return 1
        try:                                          # Windows reports NUL as a terminal: EOF means no answer
            ans = input("File this issue? [y/N] ")
        except EOFError:
            print("\nno answer (not a terminal?): add --yes to file it, or --dry-run to only show it"); return 1
        if ans.strip().lower() not in ("y", "yes"):
            print("not filed"); return 1
    how, where = file_issue(repo, title, body)
    print(("Filed: " if how == "gh" else "Opened the pre-filled issue page (press Submit): ") + where)
    record("issue", issue_kind=kind, how=how)
    return 0
