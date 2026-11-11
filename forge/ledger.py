"""Obligations ledger: "keep everything in sync" as a mechanical gate, enforced by Claude Code hooks.

  forge ledger record --hook      PostToolUse (Edit / Write / MultiEdit / Bash): open obligations for touched files
  forge ledger check --hook       Stop: re-run the checks of open obligations; close what passes; block on the rest
  forge ledger show               open and recently closed obligations
  forge ledger clear --reason T   owner escape hatch: closes everything, logged as an override row in the QC register

Touched file                               -> obligations
  *.kicad_sch                              erc:<file>, drift:<module or system>
  *.kicad_pcb                              drc:<file>
  modules/M-xx/BOM.csv, WIP BoM            drift:<module>, bom:<file>
  Design_Booklet.md, Sheet_Brief.md        drift:<module>
  .forge/superseded_terms.csv              consistency
  02_Requirements/**, trace.csv            vmodel
  anything in a module whose G6 passed     changelog:<module> (closed by a line dated today in a module doc)
The Stop check always includes the superseded-term check (it replaces `forge consistency --hook`, which still works).
Edits nobody recorded (the user, a background job) open obligations too: shown as advice on the first Stop,
blocking from the next one. The user's edit is design intent; its consequences still need ERC and drift.
State: .forge/ledger.json. Outside a pe-forge project every command is a silent no-op.
"""
import datetime as dt
import json
import os
import sys

EXTS = (".kicad_sch", ".kicad_pcb", ".csv", ".md")
SKIP = {".git", ".forge", ".claude", "node_modules", ".history", ".venv", "__pycache__"}


def _now():
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _path(prj):
    return prj.p("tools_data", "ledger.json")


def load(prj):
    try:
        d = json.load(open(_path(prj)))
    except (OSError, ValueError):
        d = {}
    d.setdefault("open", [])
    d.setdefault("closed", [])
    d.setdefault("mtimes", {})
    return d


def save(prj, d):
    d["closed"] = d["closed"][-50:]
    os.makedirs(os.path.dirname(_path(prj)), exist_ok=True)
    tmp = _path(prj) + ".tmp"
    json.dump(d, open(tmp, "w"), indent=1)
    os.replace(tmp, _path(prj))


def scan(prj):
    out = {}
    for dp, dn, fn in os.walk(prj.root):
        dn[:] = [x for x in dn if x not in SKIP and not x.endswith("-backups")]
        for f in fn:
            if f.endswith(EXTS):
                p = os.path.join(dp, f)
                try:
                    out[os.path.relpath(p, prj.root).replace(os.sep, "/")] = os.stat(p).st_mtime_ns
                except OSError:
                    pass
    return out


def _modules(prj):
    try:
        from . import state
        return state.load(prj)["modules"]
    except Exception:
        return []


def module_of(prj, rel, mods):
    for m in mods:
        if rel.startswith(m["dir"].replace("\\", "/").rstrip("/") + "/"):     # older Windows state: backslashes
            return m
    if rel.endswith(".kicad_sch"):      # a sheet named in a module brief belongs to that module
        from .drift import frontmatter
        for m in mods:
            b = os.path.join(prj.root, m["dir"], "Sheet_Brief.md")
            if os.path.exists(b) and frontmatter(open(b, errors="ignore").read()).get("kicad_sheet") == rel:
                return m
    return None


def obligations_for(prj, rel, mods):
    m = module_of(prj, rel, mods)
    scope = m["id"] if m else "system"
    base = os.path.basename(rel)
    out = []
    if rel.endswith(".kicad_sch"):
        out += [("erc", rel), ("drift", scope)]
        try:
            from .lint import engine_owned
            if engine_owned(os.path.join(prj.root, rel)):
                out.append(("legibility", rel))      # the engine drew it: its style is ours to keep
        except Exception:
            pass
    elif rel.endswith(".kicad_pcb"):
        out.append(("drc", rel))
    elif base in ("BOM.csv",) or rel == prj.paths["bom_csv"]:
        out += [("drift", scope), ("bom", rel)]
    elif base in ("Design_Booklet.md", "Sheet_Brief.md"):
        out.append(("drift", scope))
    if rel == prj.paths["superseded_terms"]:
        out.append(("consistency", "project"))
    if rel.startswith(prj.paths["requirements"] + "/") or base == "trace.csv":
        out.append(("vmodel", "project"))
    if m and m["phases"].get("P6", {}).get("status") == "passed" and not rel.endswith(".kicad_sch.lck"):
        out.append(("changelog", m["id"]))
    return out


def open_for(prj, d, files, by):
    mods = _modules(prj)
    have = {(o["kind"], o["target"]) for o in d["open"]}
    added = []
    for rel in files:
        for kind, target in obligations_for(prj, rel, mods):
            if (kind, target) in have:
                for o in d["open"]:       # a user edit on an agent obligation keeps it blocking
                    if (o["kind"], o["target"]) == (kind, target) and by == "agent":
                        o["opened_by"] = "agent"
                continue
            have.add((kind, target))
            o = {"kind": kind, "target": target, "opened_by": by, "opened_at": _now(), "file": rel}
            d["open"].append(o)
            added.append(o)
    return added


def record(prj, payload):
    d = load(prj)
    ti = payload.get("tool_input") or {}
    files = []
    p = ti.get("file_path") or ti.get("notebook_path")
    if p:
        from .config import inside
        rel = inside(os.path.join(payload.get("cwd") or prj.root, p), prj.root)
        if rel and rel != ".":
            files.append(rel)
    now = scan(prj)
    old = d["mtimes"]
    if payload.get("tool_name") == "Bash" or not old:
        files += [f for f, t in now.items() if old and old.get(f) != t]
        files += [f for f in old if f not in now]      # deleted
    d["mtimes"] = now if (payload.get("tool_name") == "Bash" or not old) else dict(old, **{f: now[f] for f in files if f in now})
    open_for(prj, d, sorted(set(files)), "agent")
    save(prj, d)


# ------------------------------------------------------------------ closing
def _close(d, o, note):
    d["open"].remove(o)
    if o.get("opened_by") != "stop":          # the per-Stop superseded-term check is not history worth keeping
        d["closed"].append(dict(o, closed_at=_now(), note=note))


def evaluate(prj, d):
    """Re-run checks for open obligations; close those that pass. Returns {(kind, target): reason} still failing."""
    from . import check, gates
    failing, cache = {}, {}

    def run(name, mod=None):
        k = (name, mod)
        if k not in cache:
            cache[k] = check.CHECKS[name](prj, {"module": None if mod in (None, "system") else mod})
        return cache[k]

    for o in list(d["open"]):
        kind, target = o["kind"], o["target"]
        if kind in ("erc", "drc", "consistency", "vmodel"):
            r = run(kind)
        elif kind == "drift":
            r = run("drift", target)
        elif kind == "bom":
            f = os.path.join(prj.root, target)
            if not os.path.exists(f):
                r = check.result("pass")
            else:
                res = gates.bom_ok(f)
                rows_none = any("no rows" in m for _, m in res)
                r = check.result("skip" if rows_none else "pass" if all(ok for ok, _ in res) else "fail",
                                 [check.finding("BoM complete", m, target) for ok, m in res if not ok])
        elif kind == "legibility":
            from .lint import lint
            p = os.path.join(prj.root, target)
            lr = lint(p) if os.path.exists(p) else {"blocking": False, "findings": []}
            r = check.result("fail" if lr["blocking"] else "pass",
                             [check.finding("engine-drawn sheet meets the style guide", x, target) for x in lr["findings"][:5]])
        elif kind == "changelog":
            today = dt.date.today().isoformat()
            m = next((m for m in _modules(prj) if m["id"] == target), None)
            hit = False
            if m:
                for f in ("Design_Booklet.md", "Module_Brief.md", "Sheet_Brief.md"):
                    p = os.path.join(prj.root, m["dir"], f)
                    if os.path.exists(p) and today in open(p, errors="ignore").read():
                        hit = True
            r = check.result("pass" if hit else "fail", [] if hit else [check.finding(
                f"{target} changed after G6: a dated revision line", f"no line dated {today} in its booklet or briefs",
                m["dir"] if m else target)])
        else:
            continue
        if r["status"] in ("pass", "warn"):
            _close(d, o, f"{kind} {r['status']}")
        elif r["status"] == "skip":
            _close(d, o, f"not checked: {r['reason']}")
        else:
            f = r["findings"][:3]
            failing[(kind, target)] = "; ".join(f"{x['location']}: {x['actual']}" if x.get("location") else x["actual"]
                                                for x in f)
    return failing


def check_hook(prj, payload):
    d = load(prj)
    now = scan(prj)
    if d["mtimes"]:        # edits nobody recorded since the last snapshot: the user (or a background job)
        changed = [f for f, t in now.items() if d["mtimes"].get(f) != t] + [f for f in d["mtimes"] if f not in now]
        open_for(prj, d, sorted(set(changed)), "user")
    d["mtimes"] = now
    if not any(o["kind"] == "consistency" for o in d["open"]):
        d["open"].append({"kind": "consistency", "target": "project", "opened_by": "stop", "opened_at": _now(), "file": ""})
    ran = sorted({o["kind"] for o in d["open"]})
    failing = evaluate(prj, d)
    blocking, advisory = [], []
    for o in d["open"]:
        line = f"{o['kind']}:{o['target']}" + (f" ({failing[(o['kind'], o['target'])]})" if (o["kind"], o["target"]) in failing else "")
        if o["opened_by"] == "user" and not o.get("advised"):
            o["advised"] = _now()
            advisory.append(line)
        else:
            blocking.append(line)
    save(prj, d)
    active = payload.get("stop_hook_active", False)
    if prj.level == "light":             # light: the same checks, reported as advice; nothing blocks (forge level)
        advisory, blocking = advisory + blocking, []
    if blocking and not active:
        print("pe-forge: open obligations (fix them, then stop again; `forge ledger show` for details):\n"
              + "\n".join("  open: " + b for b in blocking)
              + ("\nAlso changed outside your edits (advice now, blocking next turn):\n" + "\n".join("  " + a for a in advisory)
                 if advisory else ""), file=sys.stderr)
        return 2
    msg = []
    line = stop_line(prj, ran, failing)
    if line:
        msg.append(line)
    if blocking:
        msg.append("pe-forge: obligations still open: " + "; ".join(blocking))
    if advisory:
        msg.append(("pe-forge (light mode, advice): checks to look at: " if prj.level == "light" else
                    "pe-forge: files changed outside Claude need checks (blocking next turn): ") + "; ".join(advisory))
    if msg:
        print(json.dumps({"systemMessage": "\n".join(msg)[:1500]}))
    return 0


NAMES = {"erc": "ERC", "drc": "DRC", "drift": "drift", "bom": "BoM", "vmodel": "V-model", "consistency": "consistency",
         "changelog": "changelog", "legibility": "legibility"}


def stop_line(prj, ran, failing):
    """'pe-forge ▸ ERC ✓ · drift ✓ · consistency ✓', only when it differs from the last Stop's line."""
    from . import ui
    t = ui.Theme(color=False)
    bad = {k for k, _ in failing}
    text = " · ".join(f"{NAMES.get(k, k)} {t.g('fail') if k in bad else t.g('ok')}" for k in ran)
    if not text:
        return ""
    line = ui.hook_message(text, t)["systemMessage"]
    f = prj.p("tools_data", "cache", "stop_line.txt")
    try:
        if open(f).read() == line:
            return ""
    except OSError:
        pass
    os.makedirs(os.path.dirname(f), exist_ok=True)
    open(f, "w").write(line)
    return line


def show(prj):
    d = load(prj)
    print(f"{len(d['open'])} open obligation(s)")
    for o in d["open"]:
        print(f"  open   {o['kind']}:{o['target']}  (by {o['opened_by']} at {o['opened_at']}, file {o.get('file') or '-'})")
    for o in d["closed"][-10:]:
        print(f"  closed {o['kind']}:{o['target']}  at {o['closed_at']}: {o.get('note', '')}")
    return 0


def clear(prj, reason):
    if not reason:
        print("forge ledger clear --reason TEXT   (the reason is logged in the QC register)")
        return 1
    d = load(prj)
    n = len(d["open"])
    for o in list(d["open"]):
        _close(d, o, f"cleared by owner: {reason}")
    save(prj, d)
    qc = prj.p("qc_register")
    if os.path.exists(qc):
        with open(qc, "a") as fh:
            fh.write(f"| {dt.date.today().isoformat()} | ledger | system | {n} obligation(s) cleared | - | OVERRIDE "
                     f"| - | - | - | OVERRIDE: {reason.replace('|', '/')} |\n")
    print(f"cleared {n} obligation(s); logged in the QC register")
    return 0


def main(prj, argv):
    if not argv:
        print(__doc__)
        return 1
    payload = {}
    if "--hook" in argv:
        try:
            payload = json.load(sys.stdin)
        except Exception:
            payload = {}
    if argv[0] == "record":
        try:
            record(prj, payload)
        except Exception:
            pass      # a recording failure must never break the user's tool call
        return 0
    if argv[0] == "check":
        return check_hook(prj, payload)
    if argv[0] == "show":
        return show(prj)
    if argv[0] == "clear":
        return clear(prj, argv[argv.index("--reason") + 1] if "--reason" in argv else "")
    print(__doc__)
    return 1

