"""forge check: one deterministic project check. No model calls, no network, safe in CI and a pre-commit hook.

  forge check [--json] [--strict] [--fab] [--only a,b] [--module M-xx]
  forge hooks install [--chain] | --uninstall      git pre-commit hook running the fast subset

Sub-checks (each: pass | warn | fail | skip, with findings {claim, actual, location}):
  erc          ERC of every root .kicad_sch under 08_PCB/ (skip: no schematic drawn yet)
  drc          DRC of every root .kicad_pcb, with unconnected items and schematic parity (skip: no board yet)
  consistency  live mentions of superseded terms (Rule 5)
  vmodel       V-model trace / verification links
  bom          WIP BoM and module BoMs complete: price, stock, alternate (skip: no lines yet)
  drift        BoM / brief / booklet vs schematic (forge drift)
  sch_lib      library/tools/sch_check.py: parts against MC_Parts, nets, decoupling, text variables
  pcb_extra    library/tools/pcb_check.py: layout checks DRC does not do (needs KiCad's pcbnew)
  fab          release readiness (only with --fab): routing, schematic/board match, BoM ready, outputs fresh
               (content hashes) and complete, power layout rules, release docs. See forge/fab.py

Exit 0 when nothing failed; --strict also fails on warnings and on checks skipped because a tool is missing.
A skipped check is reported as skipped with its reason, never as passed. The result is cached in
.forge/cache/check.json for the status line.

Contract: this module and everything it imports make no network calls and call no model (a test asserts it).
"""
import csv
import glob
import json
import os
import subprocess
import sys
import time

from . import config, consistency, gates, kicad, vmodel

ALL = ["erc", "drc", "consistency", "vmodel", "bom", "drift", "sch_lib", "pcb_extra"]
PRECOMMIT = "erc,consistency,vmodel,bom,drift"
HOOK_MARK = "# pe-forge pre-commit hook"


def result(status, findings=(), reason="", missing_tool=False):
    return {"status": status, "findings": list(findings), "reason": reason, "missing_tool": missing_tool}


def finding(claim, actual, location="", fix=""):
    f = {"claim": claim, "actual": actual, "location": location}
    if fix:
        f["fix"] = fix
    return f


# ------------------------------------------------------------------ discovery
def roots(prj, ext):
    """Root KiCad files under 08_PCB: <dir>/<dir>.<ext> (a project's top sheet / its board)."""
    allf = [f for f in glob.glob(os.path.join(prj.p("pcb"), "**", "*." + ext), recursive=True)
            if "-backups" not in f and "/release/" not in f]
    r = [f for f in allf if os.path.splitext(os.path.basename(f))[0] == os.path.basename(os.path.dirname(f))]
    return r or allf[:1]


def has_content(path):
    t = open(path, errors="ignore").read()
    if path.endswith(".kicad_sch"):
        return "(lib_id" in t or "\n\t(sheet\n" in t
    return any(k in t for k in ("(footprint ", "(footprint\n", "(segment", "(zone", "(via"))


# ------------------------------------------------------------------ sub-checks
def _kicad(prj, kind):
    ext = "kicad_sch" if kind == "erc" else "kicad_pcb"
    files = roots(prj, ext)
    if not files:
        return result("skip", reason=f"no {'schematic' if kind == 'erc' else 'board'} yet")
    files = [f for f in files if has_content(f)]
    if not files:
        return result("skip", reason="no schematic yet: the sheet is empty" if kind == "erc" else "no board yet: no footprints or tracks")
    if not kicad.find_cli():
        return result("skip", reason="kicad-cli not found (forge doctor)", missing_tool=True)
    F, W = [], []
    for f in files:
        rep = kicad.erc(f) if kind == "erc" else kicad.drc(f)
        loc = prj.rel(f)
        if not rep.tool_ok:
            F.append(finding(f"{kind.upper()} runs on {loc}", rep.error, loc))
            continue
        for v in rep.violations:
            it = next((i for i in v["items"] if i.get("pos")), None)
            where = loc + (f" sheet {v['sheet']}" if v["sheet"] else "") + \
                (f" @ ({it['pos'][0]:g}, {it['pos'][1]:g}) mm" if it else "")
            refs = ", ".join(i["ref"] for i in v["items"] if i.get("ref"))
            F.append(finding(f"no {kind.upper()} errors", f"{v['type']}: {v['description']}" + (f" [{refs}]" if refs else ""),
                             where))
        if kind == "drc":
            W += [finding("all connections routed", v["description"], loc) for v in rep.unconnected]
            W += [finding("schematic and board match", v["description"], loc) for v in rep.parity]
    return result("fail" if F else "warn" if W else "pass", F + W, reason=f"{len(files)} file(s)")


def c_erc(prj, opts):
    return _kicad(prj, "erc")


def c_drc(prj, opts):
    return _kicad(prj, "drc")


def c_consistency(prj, opts):
    hits = consistency.scan(prj)
    return result("fail" if hits else "pass",
                  [finding(f"'{t}' is superseded by '{by}'", ln, f"{r}:{i}") for r, i, t, by, ln in hits])


def c_vmodel(prj, opts):
    try:
        errs = vmodel.problems(vmodel.Model(prj))
    except Exception as e:
        return result("fail", [finding("V-model sources parse", str(e), prj.paths["vmodel"])])
    return result("fail" if errs else "pass", [finding("trace links resolve", e, prj.paths["vmodel"] + "/trace.csv")
                                                for e in errs])


def bom_files(prj, module=None):
    out = [] if module else [prj.p("bom_csv")]
    for m in _modules(prj):
        if module and m["id"] != module:
            continue
        out.append(os.path.join(prj.root, m["dir"], "BOM.csv"))
    return [f for f in out if os.path.exists(f)]


def _modules(prj):
    try:
        from . import state
        return state.load(prj)["modules"]
    except Exception:
        return []


def c_bom(prj, opts):
    F, checked = [], 0
    for f in bom_files(prj, opts.get("module")):
        rows = list(csv.DictReader(open(f, newline="")))
        if not rows:
            continue
        checked += 1
        for ok, msg in gates.bom_ok(f):
            if not ok:
                F.append(finding("every BoM line has price, stock and an alternate", msg, prj.rel(f)))
    if not checked:
        return result("skip", reason="no BoM lines yet")
    return result("fail" if F else "pass", F, reason=f"{checked} BoM file(s)")


def c_drift(prj, opts):
    try:
        from . import drift
    except ImportError:
        return result("skip", reason="drift check not available in this version")
    return drift.as_check(prj, opts)


def _tool(prj, script, target, skip_msg):
    """Run a library check tool with --json. Returns (data, None) or (None, skip result)."""
    exe = os.path.join(config.LIBRARY, "tools", script)
    if not os.path.exists(exe):
        return None, result("skip", reason=f"{script} not found in the library", missing_tool=True)
    py = (kicad.find_python() or "python3") if script == "pcb_check.py" else sys.executable
    args = [py, exe, "--json", target] + (["--no-erc"] if script == "sch_check.py" else [])
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=300, env=dict(os.environ, MC_LIB=config.LIBRARY))
    except Exception as e:
        return None, result("skip", reason=f"{skip_msg}: {e}", missing_tool=True)
    try:
        return json.loads(r.stdout), None
    except ValueError:
        tail = ((r.stderr or r.stdout).strip().splitlines() or [""])[-1]
        return None, result("skip", reason=f"{skip_msg}: {tail[:160]}", missing_tool=True)


def _items(prj, data, loc):
    F, W = [], []
    for i in data.get("items", []):
        f = finding(i["code"], i["message"], loc, i.get("fix", ""))
        (F if i["severity"] == "ERROR" else W).append(f)
    return F, W


def c_sch_lib(prj, opts):
    files = [f for f in roots(prj, "kicad_sch") if has_content(f)]
    if not files:
        return result("skip", reason="no schematic yet")
    F, W = [], []
    for f in files:
        data, skip = _tool(prj, "sch_check.py", f, "sch_check could not run")
        if skip:
            return skip
        a, b = _items(prj, data, prj.rel(f))
        F += a
        W += b
    return result("fail" if F else "warn" if W else "pass", F + W)


def c_pcb_extra(prj, opts):
    files = [f for f in roots(prj, "kicad_pcb") if has_content(f)]
    if not files:
        return result("skip", reason="no board yet")
    F, W = [], []
    for f in files:
        data, skip = _tool(prj, "pcb_check.py", f, "pcb_check could not run (needs KiCad's pcbnew)")
        if skip:
            return skip
        a, b = _items(prj, data, prj.rel(f))
        F += a
        W += b
    return result("fail" if F else "warn" if W else "pass", F + W)


def c_fab(prj, opts):
    try:
        from . import fab
    except ImportError:
        return result("skip", reason="fab release gate not available in this version")
    return fab.as_check(prj, opts)


CHECKS = {"erc": c_erc, "drc": c_drc, "consistency": c_consistency, "vmodel": c_vmodel, "bom": c_bom,
          "drift": c_drift, "sch_lib": c_sch_lib, "pcb_extra": c_pcb_extra, "fab": c_fab}


def run(prj, only=None, fab=False, strict=False, module=None):
    t0 = time.time()
    names = only or ALL + (["fab"] if fab else [])
    opts = {"strict": strict, "module": module}
    out = {}
    for n in names:
        if n not in CHECKS:
            raise SystemExit(f"unknown check {n} (one of {', '.join(CHECKS)})")
        try:
            out[n] = CHECKS[n](prj, opts)
        except Exception as e:     # a check that crashes is a failure with the reason, never silent
            out[n] = result("fail", [finding(f"{n} check runs", f"{type(e).__name__}: {e}")])
    bad = lambda r: r["status"] == "fail" or (strict and (r["status"] == "warn" or
                                                          (r["status"] == "skip" and r["missing_tool"])))
    ok = not any(bad(r) for r in out.values())
    res = {"ok": ok, "checks": out, "duration_s": round(time.time() - t0, 2)}
    cache(prj, res)
    return res


def cache(prj, res):
    try:
        d = prj.p("tools_data", "cache")
        os.makedirs(d, exist_ok=True)
        json.dump({"ok": res["ok"], "failed": [n for n, r in res["checks"].items() if r["status"] == "fail"],
                   "at": time.strftime("%Y-%m-%dT%H:%M:%S")}, open(os.path.join(d, "check.json"), "w"))
    except OSError:
        pass


MARK = {"pass": "PASS", "warn": "WARN", "fail": "FAIL", "skip": "SKIP"}


def render(res):
    lines = []
    for n, r in res["checks"].items():
        head = f"{MARK[r['status']]} {n}"
        if r["status"] == "skip":
            head += f": skipped ({r['reason']})"
        elif r["reason"]:
            head += f" ({r['reason']})"
        lines.append(head)
        for f in r["findings"][:25]:
            lines.append("     " + (f"{f['location']}: " if f["location"] else "") + f"{f['claim']}, {f['actual']}"
                         + (f"  (fix: {f['fix']})" if f.get("fix") else ""))
        if len(r["findings"]) > 25:
            lines.append(f"     ... +{len(r['findings']) - 25} more (forge check --json)")
    lines.append(f"{'OK' if res['ok'] else 'FAILED'} in {res['duration_s']} s")
    return "\n".join(lines)


def main(prj, argv):
    opt = lambda k: argv[argv.index(k) + 1] if k in argv else None
    only = [x for x in (opt("--only") or "").split(",") if x] or None
    res = run(prj, only, "--fab" in argv, "--strict" in argv, opt("--module"))
    print(json.dumps(res, indent=1) if "--json" in argv else render(res))
    return 0 if res["ok"] else 1


# ------------------------------------------------------------------ git hook
def hooks(prj, argv):
    gd = subprocess.run(["git", "-C", prj.root, "rev-parse", "--absolute-git-dir"], capture_output=True,
                        text=True).stdout.strip()
    if not gd:
        print("not a git repository: run `git init` first")
        return 1
    hook = os.path.join(gd, "hooks", "pre-commit")
    prev = hook + ".pe-forge-prev"
    os.makedirs(os.path.dirname(hook), exist_ok=True)
    ours = os.path.exists(hook) and HOOK_MARK in open(hook, errors="ignore").read()
    if "--uninstall" in argv:
        if not ours:
            print("no pe-forge pre-commit hook installed")
            return 0
        os.remove(hook)
        if os.path.exists(prev):
            os.rename(prev, hook)
            print("pe-forge hook removed; your previous pre-commit hook is restored")
        else:
            print("pe-forge hook removed")
        return 0
    if not argv or argv[0] != "install":
        print("forge hooks install [--chain] | forge hooks --uninstall")
        return 1
    chain = ""
    if os.path.exists(hook) and not ours:
        if "--chain" not in argv:
            print(f"{hook} already exists and is not pe-forge's. Not overwritten.\n"
                  "Run `forge hooks install --chain` to keep it and run it first, or remove it yourself.")
            return 1
        os.rename(hook, prev)
    if os.path.exists(prev):
        chain = f'"{prev}" "$@" || exit $?\n'
    forge = os.path.join(config.FORGE_ROOT, "bin", "forge")
    open(hook, "w").write(f"#!/bin/sh\n{HOOK_MARK} (forge hooks install). Remove with: forge hooks --uninstall\n"
                          f"{chain}"
                          f'echo "pe-forge: checking before commit ({PRECOMMIT})"\n'
                          f'exec "{forge}" check --only {PRECOMMIT}\n')
    os.chmod(hook, 0o755)
    print(f"installed {hook}" + (" (runs your previous hook first)" if chain else ""))
    return 0
