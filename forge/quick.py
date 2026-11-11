"""forge quick: pe-forge's checks on any KiCad folder, no project needed, nothing written into it.

  forge quick [dir] [--vin V] [--json] [--out FILE] [--only a,b]
  forge quick --hint                 (SessionStart hook) tell Claude this is an unmanaged KiCad folder

For the customer who opens Claude Code in their KiCad folder and says "help me with this board": answer first,
backed by evidence, with no set-up and no process. It
  - copies the KiCad files (schematics, boards, project, rules, local libraries) to a temporary folder and runs the
    checks there, so the customer's folder cannot be changed (and checks that it was not: hashes before / after);
  - runs ERC, DRC, the library and layout checks, protection, insulation, the High-Power PCB Rule Book, copper /
    connectors / crosstalk and sheet legibility (QUICK below), and reads what the board is (forge req infer);
  - ranks the findings: SAFETY (protection, insulation, a Must rule of the rule book) first, then errors, then
    warnings; a check that could not run is listed as not run, never as passed.
--vin gives the input voltage when the schematic does not say (the rule book's high-power rules key off it).
The report is printed; --out writes it to a file you name (outside the folder is fine).
Later, `forge adopt` brings the board under pe-forge in light mode, without changing a file.
"""
import hashlib
import json
import os
import shutil
import tempfile

from . import config

QUICK = ["erc", "drc", "sch_lib", "protection", "insulation", "pcb_hp", "pcb_copper", "pcb_connectors",
         "pcb_crosstalk", "pcb_extra", "legibility"]
SAFETY = {"protection", "insulation"}
KICAD_EXT = (".kicad_sch", ".kicad_pcb", ".kicad_pro", ".kicad_dru", ".kicad_sym", ".kicad_mod", ".kicad_prl",
             ".kicad_jobset", ".kicad_wks")
KICAD_NAMES = ("sym-lib-table", "fp-lib-table")


class Virtual(config.Project):
    """A pe-forge project held in memory: the KiCad folder as [paths] pcb, no project.toml anywhere."""

    def __init__(self, root, pcb_rel, name, specs=None):
        self.root = root
        self.cfg = {"project": {"name": name}, "process": {"level": "light"}, "specs": specs or {}}
        self.paths = dict(config.DEFAULT_PATHS, pcb=pcb_rel or ".")


def _kicad_files(src):
    from .adopt import _walk
    for p in _walk(src):
        b = os.path.basename(p)
        if b.endswith(KICAD_EXT) or b in KICAD_NAMES or ".pretty" + os.sep in p:
            yield p


def _hashes(paths):
    out = {}
    for p in paths:
        try:
            out[p] = hashlib.sha256(open(p, "rb").read()).hexdigest()
        except OSError:
            pass
    return out


def copy_kicad(src, dst):
    files = list(_kicad_files(src))
    for p in files:
        t = os.path.join(dst, os.path.relpath(p, src))
        os.makedirs(os.path.dirname(t), exist_ok=True)
        shutil.copy2(p, t)
    return files


def run(root, vin=None, only=None):
    from . import adopt, check, reqinfer
    root = os.path.abspath(root)
    sv = adopt.survey(root)
    if sv["pcb_root"] is None:
        raise SystemExit(f"no KiCad files under {root}")
    tmp = tempfile.mkdtemp(prefix="pe-forge-quick-")
    try:
        files = list(_kicad_files(root))
        before = _hashes(files)
        copy_kicad(root, tmp)
        specs = {"vin_V": vin} if vin else {}
        prj = Virtual(tmp, sv["pcb_root"], os.path.basename(root.rstrip(os.sep)), specs)
        res = check.run(prj, only or QUICK)
        board = None
        sch = [f for f in check.roots(prj, "kicad_sch") if check.has_content(f)]
        if sch:
            try:
                r = reqinfer.infer_sheet(sch[0])
                if r:
                    board = reqinfer.summary(r[0])
                    if not vin:              # the rule book needs the bus voltage: take the input rail's limit
                        a = r[0]
                        v = max([a["rails"][n]["nominal_V"] or 0 for n in a["inputs"]] or [0])
                        if v and "pcb_hp" in (only or QUICK):
                            prj.cfg["specs"]["vin_V"] = v
                            res["checks"]["pcb_hp"] = check.CHECKS["pcb_hp"](prj, {})
            except Exception as e:      # the summary is a nice-to-have; never let it hide the checks
                board = f"(could not read the board: {type(e).__name__})"
        after = _hashes(files)
        changed = [os.path.relpath(p, root) for p, h in before.items() if after.get(p) != h]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if changed:
        raise SystemExit("forge quick: files in your folder changed while it ran, which it must never do: "
                         + ", ".join(changed[:5]) + ". Report it: forge issue --last")
    return {"root": root, "survey": sv, "board": board, "checks": res["checks"], "ranked": rank(res["checks"]),
            "duration_s": res["duration_s"]}


def rank(checks):
    out = {"safety": [], "error": [], "warning": [], "not_run": []}
    for n, r in checks.items():
        if r["status"] == "skip":
            out["not_run"].append({"check": n, "reason": r["reason"]})
            continue
        for f in r["findings"]:
            item = dict(f, check=n)
            if n == "pcb_hp":                   # the rule book's own file is not there: point at the board
                item["location"] = "board"
            for k in ("actual", "fix"):          # no project.toml yet: that is where it goes after adoption
                if item.get(k):
                    item[k] = item[k].replace("project.toml", "project.toml (after forge adopt)")
            advice = f.get("claim", "").startswith("PROT-4")          # a rating to look up, not a fault
            if advice:
                out["warning"].append(item)
            elif r["status"] == "fail" and (n in SAFETY or (n == "pcb_hp" and "Must" in (f.get("claim", "") + f.get("actual", "")))):
                out["safety"].append(item)
            elif r["status"] == "fail" and not f.get("claim", "").startswith(("all connections", "schematic and board")):
                out["error"].append(item)
            else:
                out["warning"].append(item)
    return out


def render(q, limit=12):
    sv, rk = q["survey"], q["ranked"]
    L = [f"pe-forge quick check of {q['root']} (read-only: run on a copy, your files are unchanged)",
         f"  KiCad: {', '.join(sv['kicad_projects'][:3]) or sv['pcb_root']}  ·  schematics {sv['schematics']}  ·  boards "
         f"{sv['boards']} ({len(sv['placed_boards'])} with parts, {len(sv['routed_boards'])} routed)"]
    if q["board"]:
        L.append(f"  the board: {q['board']}")
    for key, title in (("safety", "SAFETY"), ("error", "ERRORS"), ("warning", "WARNINGS")):
        items = rk[key]
        if not items:
            continue
        L += ["", f"{title} ({len(items)})"]
        for f in items[:limit]:
            L.append(f"  [{f['check']}] " + (f"{f['location']}: " if f.get("location") else "") + f"{f['claim']}: {f['actual']}"
                     + (f"  (fix: {f['fix']})" if f.get("fix") else ""))
        if len(items) > limit:
            L.append(f"  ... +{len(items) - limit} more (forge quick --json)")
    ran = [n for n, r in q["checks"].items() if r["status"] != "skip"]
    L += ["", "checks run: " + (", ".join(ran) or "none")]
    if rk["not_run"]:
        L.append("not run: " + "; ".join(f"{x['check']} ({x['reason']})" for x in rk["not_run"]))
    hp = q["checks"].get("pcb_hp", {})
    if hp.get("status") == "skip" and "off" in hp.get("reason", ""):
        L.append("  the High-Power PCB Rule Book did not apply (input below 50 V or not known): "
                 "forge quick --vin <max input V> if it is higher")
    if not (rk["safety"] or rk["error"] or rk["warning"]) and ran:
        L.append("no findings in the checks that ran")
    L.append(f"{q['duration_s']} s · to keep track of this board (no file changed, undo any time): forge adopt")
    return "\n".join(L)


# ---------------------------------------------------------------- the session-start hint
HINT = ("[pe-forge] This folder holds a KiCad design that is not a pe-forge project yet ({what}). "
        "Help the user with what they ask, directly; do not start the pe-forge process or ask set-up questions first. "
        "For evidence, run `forge quick` (pe-forge's checks on a copy: ERC, DRC, protection, insulation, the "
        "High-Power PCB Rule Book; it writes nothing here) and lead with the findings that bear on their question, "
        "SAFETY first. Once you have helped (not before), offer once, in one line, to keep track of the board: "
        "`forge adopt` shows the plan, `forge adopt --apply` on their yes (light mode: advice only, no file of theirs "
        "changed, undo any time). Never adopt without their yes, never run `forge new` here.")


def hint(start=None, limit=4000):
    """additionalContext for SessionStart when the folder is an unmanaged KiCad design, else None. Bounded scan."""
    root = os.path.abspath(start or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    if config.find_root(root):
        return None
    from .adopt import SKIP_DIRS
    pro = sch = pcb = seen = 0
    base = root.rstrip(os.sep).count(os.sep)
    for dp, dn, fn in os.walk(root):
        if dp.count(os.sep) - base >= 4:
            dn[:] = []
        dn[:] = [d for d in dn if d not in SKIP_DIRS and not d.endswith("-backups")]
        for f in fn:
            seen += 1
            pro += f.endswith(".kicad_pro")
            sch += f.endswith(".kicad_sch")
            pcb += f.endswith(".kicad_pcb")
        if seen > limit:
            break
    if not (pro or sch or pcb):
        return None
    what = ", ".join(x for x in (f"{pro} KiCad project(s)" if pro else "", f"{sch} schematic(s)" if sch else "",
                                 f"{pcb} board(s)" if pcb else "") if x)
    return HINT.format(what=what)


def main(argv):
    if "--hint" in argv:
        h = hint()
        if h:
            from . import ui
            out = {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": h}}
            try:
                out.update(ui.hook_message("pe-forge: KiCad design found · ask anything · `forge quick` checks it "
                                           "without changing a file"))
            except Exception:
                pass
            print(json.dumps(out))
        return 0
    opt = lambda k: argv[argv.index(k) + 1] if k in argv else None
    vals = {opt(k) for k in ("--vin", "--out", "--only")}
    pos = [a for a in argv if not a.startswith("--") and a not in vals]
    vin = float(opt("--vin")) if opt("--vin") else None
    only = [x for x in (opt("--only") or "").split(",") if x] or None
    q = run(pos[0] if pos else os.getcwd(), vin, only)
    text = json.dumps(q, indent=1, default=str) if "--json" in argv else render(q)
    if opt("--out"):
        open(opt("--out"), "w").write(text + "\n")
        print(f"wrote {opt('--out')}")
    else:
        print(text)
    return 1 if q["ranked"]["safety"] or q["ranked"]["error"] else 0
