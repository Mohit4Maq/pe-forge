"""Change control after baseline: a module's design files are locked once its G6 has passed (the board's once G7
has passed). Changing them needs an approved change request (CR) that names the module and the files.

  forge cr new "<title>" --modules M-02[,M-03|board] [--files "08_PCB/x/*.kicad_sch,modules/M-02_*/BOM.csv"]
               [--requirements SYS-004,HWR-PWR-01] [--reason TEXT]
  forge cr validate CR-NNN        fields present, modules exist; attaches `vmodel impact` when requirements change
  forge cr approve CR-NNN         OWNER ONLY. Refuses inside an agent session (no TTY / CLAUDECODE set) unless the
                                  owner adds --i-am-the-owner. Agents never pass that flag.
  forge cr close CR-NNN [--commit SHA]   needs `forge check` to pass; logs the CR in the module revision history
  forge cr reject CR-NNN --reason TEXT
  forge cr list | status CR-NNN
  forge cr guard --hook           PreToolUse hook: denies Edit / Write on a locked file without an approved CR

Locked after the module's G6: files inside modules/M-xx_*/ that define the design (BOM.csv, Design_Booklet.md,
Sheet_Brief.md, Design_Calculations.md) and the module's KiCad sheet (Sheet_Brief `kicad_sheet:`). Locked after the
board's G7: every .kicad_pcb and every .kicad_sch under 08_PCB/.
project.toml [process] change_control = "enforce" (default for profile full) | "warn" (default for lite) | "off".
Edits made through a shell bypass the PreToolUse hook; `forge txn verify` applies the same rule to every file a
transaction touched, and `forge schdraw apply` checks it before writing.
CR files: 01_Program/Changes/CR-NNN.md (template 90_Templates/T_Change_Request.md). Numbers are shared with the
requirement packets `forge vmodel cr` writes, so a CR id is unique across the project.
"""
import datetime as dt
import fnmatch
import glob
import json
import os
import re
import subprocess
import sys

DESIGN_FILES = ("BOM.csv", "Design_Booklet.md", "Sheet_Brief.md", "Design_Calculations.md")
FIELDS = ["id", "title", "status", "modules", "files_allowed", "requirements", "reason", "approved_by",
          "approved_on", "commit"]


def cdir(prj):
    return os.path.join(prj.root, prj.paths["program"], "Changes")


def mode(prj):
    m = prj.process.get("change_control")
    if m in ("enforce", "warn", "off"):
        return m
    return "warn" if prj.process.get("profile") == "lite" else "enforce"


def _list(v):
    return [x.strip() for x in re.split(r"[,\s]+", (v or "").strip("[] ")) if x.strip()]


def parse(path):
    t = open(path, errors="ignore").read()
    m = re.match(r"---\n(.*?)\n---\n?(.*)", t, re.S)
    meta = {}
    for line in (m.group(1).split("\n") if m else []):
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip().strip('"')
    meta["_body"] = m.group(2) if m else t
    meta["_path"] = path
    return meta


def write(cr):
    head = "---\ntype: change-request\n" + "".join(f"{k}: {cr.get(k, '')}\n" for k in FIELDS) + "---\n"
    open(cr["_path"], "w").write(head + cr["_body"])


def crs(prj):
    return {c["id"]: c for c in (parse(f) for f in sorted(glob.glob(os.path.join(cdir(prj), "CR-*.md")))) if c.get("id")}


def get(prj, cid):
    c = crs(prj).get(cid)
    if not c:
        raise SystemExit(f"{cid}: no such change request in {prj.rel(cdir(prj))}")
    return c


def next_id(prj):
    used = [int(m) for f in glob.glob(os.path.join(cdir(prj), "CR-*.md")) + glob.glob(prj.p("vmodel", "CR", "CR-*.md"))
            for m in re.findall(r"CR-(\d+)", os.path.basename(f))]
    return f"CR-{max(used or [0]) + 1:03d}"


def _modules(prj):
    try:
        from . import state
        st = state.load(prj)
        return st["modules"], st
    except Exception:
        return [], {"phases": {}}


# ------------------------------------------------------------------ what is locked
def lock_of(prj, rel):
    """(scope, why) when `rel` is locked by a passed baseline, else None. scope: M-xx or 'board'."""
    mods, st = _modules(prj)
    from .ledger import module_of
    m = module_of(prj, rel, mods)
    if m and m["phases"].get("P6", {}).get("status") == "passed":
        base = os.path.basename(rel)
        if base in DESIGN_FILES or rel.endswith(".kicad_sch"):
            return m["id"], f"{m['id']} {m['name']} is baselined (G6 passed {m['phases']['P6'].get('date', '')})"
    if rel.startswith(prj.paths["pcb"] + "/") and rel.endswith((".kicad_pcb", ".kicad_sch")):
        if st["phases"].get("P7", {}).get("status") == "passed":
            return "board", f"the board is baselined (G7 passed {st['phases']['P7'].get('date', '')})"
    return None


def covering(prj, rel, scope):
    for c in crs(prj).values():
        if c.get("status") != "approved":
            continue
        if scope not in _list(c.get("modules")):
            continue
        if any(fnmatch.fnmatch(rel, g) for g in _list(c.get("files_allowed"))):
            return c
    return None


def block_reason(prj, path):
    """Why writing `path` is not allowed now (enforce mode), or None."""
    if mode(prj) == "off":
        return None
    from .config import inside
    rel = inside(path, prj.root)
    if rel is None or rel == ".":
        return None
    lk = lock_of(prj, rel)
    if not lk or covering(prj, rel, lk[0]):
        return None
    msg = (f"{rel} is under change control: {lk[1]}. Open a change request first: "
           f"forge cr new \"<what and why>\" --modules {lk[0]} --files \"{rel}\", then `forge cr validate`, and ask "
           "the owner to approve it (forge cr approve is owner-only).")
    if mode(prj) == "warn":
        print(json.dumps({"systemMessage": "pe-forge change control (warn): " + msg}))
        return None
    return msg


def violations(prj, rels):
    """[(rel, reason)] for files a transaction touched that change control does not allow (enforce mode)."""
    if mode(prj) != "enforce":
        return []
    out = []
    for rel in rels:
        lk = lock_of(prj, rel)
        if lk and not covering(prj, rel, lk[0]):
            out.append((rel, lk[1]))
    return out


# ------------------------------------------------------------------ commands
def agent_context():
    return bool(os.environ.get("CLAUDECODE") or os.environ.get("CLAUDE_CODE_ENTRYPOINT")) or not sys.stdin.isatty()


def new(prj, title, modules, files, reqs, reason):
    os.makedirs(cdir(prj), exist_ok=True)
    mods, _ = _modules(prj)
    known = {m["id"] for m in mods} | {"board"}
    bad = [m for m in modules if m not in known]
    if bad:
        raise SystemExit(f"unknown module(s) {', '.join(bad)} (forge module list; use 'board' for the PCB)")
    if not files:
        files = []
        for mid in modules:
            m = next((x for x in mods if x["id"] == mid), None)
            files += [f"{m['dir']}/*"] if m else [f"{prj.paths['pcb']}/**"]
    cid = next_id(prj)
    tpl = os.path.join(prj.root, prj.paths["templates"], "T_Change_Request.md")
    body = parse(tpl)["_body"] if os.path.exists(tpl) else "# {{ID}}: {{TITLE}}\n"
    body = body.replace("{{ID}}", cid).replace("{{TITLE}}", title)
    cr = {"id": cid, "title": title, "status": "draft", "modules": ", ".join(modules), "files_allowed": ", ".join(files),
          "requirements": ", ".join(reqs), "reason": reason, "_body": body,
          "_path": os.path.join(cdir(prj), f"{cid}.md")}
    write(cr)
    return cr


def validate(prj, cid):
    c = get(prj, cid)
    errs = []
    for k in ("title", "reason", "modules", "files_allowed"):
        if not c.get(k):
            errs.append(f"{k} is empty")
    mods, _ = _modules(prj)
    known = {m["id"] for m in mods} | {"board"}
    errs += [f"module {m} does not exist" for m in _list(c.get("modules")) if m not in known]
    reqs = _list(c.get("requirements"))
    if reqs and "## Requirement impact" not in c["_body"]:
        from . import vmodel
        try:
            out, ag = vmodel.impact_lines(vmodel.Model(prj), reqs)
            c["_body"] += "\n## Requirement impact (forge vmodel impact)\n\n" + "\n".join(out + vmodel.agent_block(ag)) + "\n"
            write(c)
        except Exception as e:
            errs.append(f"vmodel impact for {', '.join(reqs)} failed: {e}")
    return errs


def approve(prj, cid, owner_flag):
    if agent_context() and not owner_flag:
        raise SystemExit("forge cr approve is for the project owner only. Run it in your own terminal, or here as\n"
                         f"  ! forge cr approve {cid} --i-am-the-owner\n(Agents must ask the owner and never pass that flag.)")
    errs = validate(prj, cid)
    if errs:
        raise SystemExit(f"{cid} does not validate: " + "; ".join(errs))
    c = get(prj, cid)
    if c.get("status") not in ("draft", "approved"):
        raise SystemExit(f"{cid} is {c.get('status')}: only a draft can be approved")
    who = subprocess.run(["git", "-C", prj.root, "config", "user.name"], capture_output=True, text=True).stdout.strip()
    c.update(status="approved", approved_by=who or "owner", approved_on=dt.date.today().isoformat())
    write(c)
    return c


def close(prj, cid, commit=None):
    from . import check
    c = get(prj, cid)
    if c.get("status") != "approved":
        raise SystemExit(f"{cid} is {c.get('status')}: only an approved CR can be closed")
    res = check.run(prj)
    if not res["ok"]:
        bad = [n for n, r in res["checks"].items() if r["status"] == "fail"]
        raise SystemExit(f"cannot close {cid}: forge check fails ({', '.join(bad)})")
    sha = commit or subprocess.run(["git", "-C", prj.root, "rev-parse", "--short", "HEAD"], capture_output=True,
                                   text=True).stdout.strip()
    c.update(status="closed", commit=sha)
    write(c)
    today = dt.date.today().isoformat()
    mods, _ = _modules(prj)
    for mid in _list(c.get("modules")):
        m = next((x for x in mods if x["id"] == mid), None)
        if not m:
            continue
        f = os.path.join(prj.root, m["dir"], "Module_Brief.md")
        t = open(f).read() if os.path.exists(f) else f"# {mid}\n"
        if "## Revision history" not in t:
            t = t.rstrip("\n") + "\n\n## Revision history\n"
        t = t.rstrip("\n") + f"\n- {today} {cid}: {c['title']} (commit {sha}, approved by {c.get('approved_by')})\n"
        open(f, "w").write(t)
    reqs = _list(c.get("requirements"))
    if reqs:
        from . import vmodel
        try:
            vmodel.main(prj, ["cr", cid, *reqs, "--why", c.get("reason") or c["title"]])
        except SystemExit:
            pass
    return c


def guard_hook(prj, payload):
    ti = payload.get("tool_input") or {}
    p = ti.get("file_path") or ti.get("notebook_path")
    if not p:
        return 0
    p = os.path.join(payload.get("cwd") or prj.root, p)
    why = block_reason(prj, p)
    if why:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                                 "permissionDecisionReason": why}}))
    return 0


def main(prj, argv):
    if not argv:
        print(__doc__)
        return 1
    opt = lambda k: argv[argv.index(k) + 1] if k in argv else None
    cmd = argv[0]
    if cmd == "guard":
        try:
            payload = json.load(sys.stdin)
        except Exception:
            return 0
        return guard_hook(prj, payload)
    if cmd == "new":
        title = argv[1] if len(argv) > 1 and not argv[1].startswith("--") else None
        if not title or not opt("--modules"):
            print('forge cr new "<title>" --modules M-02 [--files GLOBS] [--requirements IDS] [--reason TEXT]')
            return 1
        c = new(prj, title, _list(opt("--modules")), _list(opt("--files")), _list(opt("--requirements")),
                opt("--reason") or "")
        print(f"wrote {prj.rel(c['_path'])} (draft). Fill in the reason and plan, then `forge cr validate {c['id']}` "
              "and ask the owner to approve it.")
        return 0
    if cmd in ("list", "ls"):
        for c in crs(prj).values():
            print(f"{c['id']}  {c.get('status', ''):<9} {c.get('modules', ''):<12} {c.get('title', '')}")
        print(f"change control: {mode(prj)}")
        return 0
    cid = argv[1] if len(argv) > 1 else None
    if not cid:
        print(__doc__)
        return 1
    if cmd == "status":
        c = get(prj, cid)
        print("\n".join(f"{k}: {c.get(k, '')}" for k in FIELDS))
        return 0
    if cmd == "validate":
        errs = validate(prj, cid)
        print("\n".join(f"FAIL {e}" for e in errs) or f"{cid} valid")
        return 1 if errs else 0
    if cmd == "approve":
        c = approve(prj, cid, "--i-am-the-owner" in argv)
        print(f"{cid} approved by {c['approved_by']} on {c['approved_on']}")
        return 0
    if cmd == "close":
        c = close(prj, cid, opt("--commit"))
        print(f"{cid} closed at {c['commit']}")
        return 0
    if cmd == "reject":
        c = get(prj, cid)
        c.update(status="rejected")
        c["_body"] += f"\n## Rejected {dt.date.today().isoformat()}\n\n{opt('--reason') or ''}\n"
        write(c)
        print(f"{cid} rejected")
        return 0
    print(__doc__)
    return 1
