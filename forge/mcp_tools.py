"""Outcome-level tools behind forge-mcp (standard library only; forge/mcp_server.py registers them over MCP).

Rules: every result follows pwrlab's contract {usable, verdict, checks, warnings, ...}; no tool writes an arbitrary
file, approves a change request or passes a gate (those stay with the owner); every path a host sends is resolved
inside the project root and anything outside is rejected. Each tool runs the real `forge` command line, so MCP and
the terminal can never disagree.
"""
import functools
import hashlib
import json
import os
import subprocess
import sys

# The forge CLI, run by the interpreter that runs this server (an MCP host may start it with a minimal PATH,
# where `python3` can be an old system Python).
FORGE_MAIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "__main__.py")
FORGE = [sys.executable if sys.version_info >= (3, 11) else "python3", FORGE_MAIN]


class Refused(Exception):
    pass


def root_of(project):
    p = os.path.realpath(os.path.expanduser(project or ""))
    d = p if os.path.isdir(p) else os.path.dirname(p)
    while True:
        if os.path.isfile(os.path.join(d, "project.toml")):
            return d
        if os.path.dirname(d) == d:
            raise Refused(f"{project}: not inside a pe-forge project (no project.toml)")
        d = os.path.dirname(d)


def inside(root, path):
    """Absolute real path of `path` (relative to the project root), refused when it leaves the project."""
    p = os.path.realpath(os.path.join(root, os.path.expanduser(path)))
    if p != root and not p.startswith(root + os.sep):
        raise Refused(f"path {path} is outside the project {root}: refused")
    return p


def run(root, *args, stdin=None):
    env = dict(os.environ, CLAUDE_PROJECT_DIR=root)
    r = subprocess.run([*FORGE, *args], cwd=root, capture_output=True, text=True, env=env, input=stdin, timeout=900)
    return r.returncode, r.stdout, r.stderr


def wrap(usable, verdict, checks=(), warnings=(), **data):
    return dict({"usable": bool(usable), "verdict": verdict, "checks": list(checks), "warnings": list(warnings)}, **data)


def guarded(fn):
    @functools.wraps(fn)          # keeps the signature: the MCP tool schema is built from it
    def call(*a, **k):
        try:
            return fn(*a, **k)
        except Refused as e:
            return wrap(False, f"refused: {e}", warnings=[str(e)])
        except subprocess.TimeoutExpired:
            return wrap(False, "the forge command timed out", warnings=["timeout"])
    return call


def _json(out):
    try:
        return json.loads(out)
    except ValueError:
        return None


# ------------------------------------------------------------------ tools (read-only unless stated)
@guarded
def forge_status(project: str) -> dict:
    """Project phases, modules and the next step. Read-only, no network, no model."""
    root = root_of(project)
    rc, out, err = run(root, "status")
    try:
        st = json.load(open(os.path.join(root, ".forge", "state.json")))
    except (OSError, ValueError):
        st = {}
    return wrap(rc == 0, out.strip().splitlines()[-1] if out.strip() else err.strip(), text=out,
                phase=st.get("phase"), active_module=st.get("active_module"),
                modules=[{"id": m["id"], "name": m["name"], "status": m["status"]} for m in st.get("modules", [])])


@guarded
def forge_check(project: str, only: str = "", fab: bool = False, strict: bool = False) -> dict:
    """The deterministic project check (ERC, DRC, consistency, V-model, BoM, drift, library; --fab release gate).
    Read-only, no network, no model. `report` is exactly `forge check --json`."""
    root = root_of(project)
    args = ["check", "--json"] + (["--only", only] if only else []) + (["--fab"] if fab else []) + (["--strict"] if strict else [])
    rc, out, err = run(root, *args)
    rep = _json(out)
    if rep is None:
        return wrap(False, "forge check did not return a report", warnings=[err.strip()[-500:]])
    checks = [{"name": n, "status": r["status"], "reason": r.get("reason", ""), "findings": len(r["findings"])}
              for n, r in rep["checks"].items()]
    bad = [c["name"] for c in checks if c["status"] == "fail"]
    skipped = [f"{c['name']} skipped: {c['reason']}" for c in checks if c["status"] == "skip"]
    return wrap(rep["ok"], "all checks pass" if rep["ok"] else f"failing: {', '.join(bad) or 'strict mode (warnings / missing tools)'}",
                checks, skipped, report=rep)


@guarded
def forge_explain(project: str, query: str, sheet: str = "") -> dict:
    """Look up a part (U1), a net (VIN) or a pin (U1.7, U1.VCC) in the drawn design. Read-only."""
    root = root_of(project)
    args = ["explain", query, "--json"] + (["--sheet", inside(root, sheet)] if sheet else [])
    rc, out, err = run(root, *args)
    d = _json(out) or {"ok": False, "error": err.strip()}
    return wrap(d.get("ok"), d.get("error") or f"{d.get('kind')} {query}", result=d)


@guarded
def forge_gate_check(project: str, gate: str, module: str = "") -> dict:
    """Automated checks of a gate (G0 to G8). Read-only: passing a gate is the owner's call, not a tool's."""
    root = root_of(project)
    rc, out, err = run(root, "gate", "check", gate, *(["--module", module] if module else []))
    lines = out.strip().splitlines()
    checks = [{"ok": l.startswith("PASS"), "item": l[5:]} for l in lines if l.startswith(("PASS", "FAIL"))]
    return wrap(rc == 0, lines[-1] if lines else err.strip(), checks,
                [c["item"] for c in checks if not c["ok"]])


@guarded
def forge_drift(project: str, module: str = "") -> dict:
    """BoM / brief / booklet against the drawn schematic, as {doc, claim, actual} findings. Read-only."""
    root = root_of(project)
    rc, out, err = run(root, "drift", "--json", *(["--module", module] if module else []))
    d = _json(out)
    if d is None:
        return wrap(False, (out + err).strip()[:300] or "drift did not run")
    fails = [f for f in d["findings"] if f["severity"] == "fail"]
    return wrap(d["ok"], "documents match the drawing" if d["ok"] else f"{len(fails)} drift finding(s)",
                [{"doc": f["doc"], "claim": f["claim"], "actual": f["actual"]} for f in d["findings"]], d["notes"])


def _spec_file(root, spec):
    if not isinstance(spec, dict) or "sheet" not in spec:
        raise Refused("spec must be a drawing spec object with a 'sheet' path")
    spec = dict(spec, sheet=inside(root, spec["sheet"]))
    if not spec["sheet"].endswith(".kicad_sch"):
        raise Refused("spec.sheet must be a .kicad_sch file")
    d = os.path.join(root, ".forge", "mcp")
    os.makedirs(d, exist_ok=True)
    body = json.dumps(spec, indent=1, sort_keys=True)
    p = os.path.join(d, "spec-" + hashlib.sha1(body.encode()).hexdigest()[:12] + ".json")
    open(p, "w").write(body)
    return p


@guarded
def forge_schdraw_check(project: str, spec: dict) -> dict:
    """Dry-run a schematic drawing spec: style rules, connectivity twice, ERC, render. Writes no design file."""
    root = root_of(project)
    rc, out, err = run(root, "schdraw", "check", _spec_file(root, spec))
    errors = [l.strip()[6:] for l in out.splitlines() if l.strip().startswith("ERROR")]
    warns = [l.strip()[5:].strip() for l in out.splitlines() if l.strip().startswith("warn")]
    return wrap(rc == 0, (out.strip().splitlines() or [err.strip()])[0], [{"error": e} for e in errors], warns, output=out)


@guarded
def forge_schdraw_apply(project: str, spec: dict) -> dict:
    """WRITES the sheet named in the spec, only after a clean check, inside a forge transaction, under change control
    (a baselined module needs an approved CR). Rolled back automatically if the real project fails the re-check."""
    root = root_of(project)
    rc, out, err = run(root, "schdraw", "apply", _spec_file(root, spec))
    text = (out + err).strip()
    return wrap(rc == 0, text.splitlines()[-1] if text else "no output", output=text)


@guarded
def forge_cr_new(project: str, title: str, modules: str, files: str = "", requirements: str = "", reason: str = "") -> dict:
    """WRITES one new draft change request file in 01_Program/Changes/. It does not approve it: only the owner can."""
    root = root_of(project)
    for g in [x for x in files.split(",") if x.strip()]:
        inside(root, g.strip().split("*")[0] or ".")
    args = ["cr", "new", title, "--modules", modules] + (["--files", files] if files else []) + \
        (["--requirements", requirements] if requirements else []) + (["--reason", reason] if reason else [])
    rc, out, err = run(root, *args)
    return wrap(rc == 0, (out + err).strip().splitlines()[0] if (out + err).strip() else "")


@guarded
def forge_cr_status(project: str, cr_id: str = "") -> dict:
    """List change requests, or one CR's fields. Read-only."""
    root = root_of(project)
    rc, out, err = run(root, "cr", *(["status", cr_id] if cr_id else ["list"]))
    return wrap(rc == 0, (out + err).strip().splitlines()[-1] if (out + err).strip() else "", text=out)


TOOLS = [forge_status, forge_check, forge_explain, forge_gate_check, forge_drift, forge_schdraw_check,
         forge_schdraw_apply, forge_cr_new, forge_cr_status]

