"""Automated gate checks. `forge gate check G4 [--module M-02]` prints PASS / FAIL per item.

Artefact rule: each listed file must exist and be filled in. A markdown file counts as filled when it has at
least 8 lines of content that are not template placeholders ("_(fill in)_", "TODO", "{{", "TBD" only rows).
"""
import csv
import glob
import os
import re

from . import consistency
from .phases import GATE_OF, PHASE

PLACEHOLDER = re.compile(r"_\(fill in\)_|_\(filled by|\bTODO\b|\bTBD\b|\{\{|<fill>")
MODULE_ARTEFACTS = {
    "P2": ["Module_Brief.md", "Requirements.md"],
    "P3": ["Topology_Trade.md"],
    "P4": ["BOM.csv", "Design_Calculations.md"],
    "P5": ["Simulations/*.md"],
    "P6": ["Design_Booklet.md", "Sheet_Brief.md"],
}
BOM_REQUIRED = ["Ref", "Qty", "MPN", "Manufacturer", "Description", "Price_1", "Stock", "Source",
                "Alt MPN", "Alt manufacturer", "Alt price / stock", "Alt match"]


def filled(path):
    if os.path.isdir(path):
        return bool(os.listdir(path))
    if not path.endswith(".md"):
        return os.path.getsize(path) > 0
    lines = [l for l in open(path, errors="ignore").read().split("\n") if l.strip() and not l.startswith("---")]
    real = [l for l in lines if not PLACEHOLDER.search(l) and not re.fullmatch(r"[#>|\-\s:]*", l)]
    return len(real) >= 8 and not any(PLACEHOLDER.search(l) for l in lines)


def artefacts(prj, phase, mod=None):
    out = []
    pats = [os.path.join(mod["dir"], a) for a in MODULE_ARTEFACTS.get(phase, [])] if mod else \
        [a.format(**prj.paths) for a in PHASE[phase][4]]
    for pat in pats:
        hits = glob.glob(os.path.join(prj.root, pat), recursive=True)
        hits = [h for h in hits if "/_superseded" not in h]
        if not hits:
            out.append((False, f"missing: {pat}"))
        else:
            empty = [prj.rel(h) for h in hits if not filled(h)]
            out.append((not empty, f"{pat}: {len(hits)} file(s)" + (f", not filled in: {', '.join(empty[:4])}" if empty else "")))
    return out


def qc_rows(prj):
    f = prj.p("qc_register")
    if not os.path.exists(f):
        return []
    rows, hdr = [], None
    for line in open(f):
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if hdr is None:
            hdr = [c.lower() for c in cells]
            continue
        if set(cells[0]) <= set("-: "):
            continue
        rows.append(dict(zip(hdr, cells)))
    return rows


def review_ok(prj, gate, mod=None):
    want = (mod or {}).get("id", "system")
    rows = [r for r in qc_rows(prj) if r.get("gate") == gate and r.get("module", "system") in (want, want.lower())]
    if not rows:
        return False, f"no {gate} review row for {want} in the QC register"
    r = rows[-1]
    ok = r.get("verdict", "").upper().startswith("READY") and r.get("status", "").upper() in ("CLOSED", "ACCEPTED")
    return ok, f"latest {gate} review ({r.get('date')}): verdict {r.get('verdict')}, status {r.get('status')}"


def bom_ok(path):
    if not os.path.exists(path):
        return [(False, f"missing BoM {path}")]
    rows = list(csv.DictReader(open(path, newline="")))
    if not rows:
        return [(False, "BoM has no rows")]
    miss = [c for c in BOM_REQUIRED if c not in rows[0]]
    if miss:
        return [(False, f"BoM columns missing: {', '.join(miss)}")]
    bad = [r.get("Ref") or r.get("MPN") for r in rows if any(not (r.get(c) or "").strip() for c in BOM_REQUIRED)
           and (r.get("DNP") or "").lower() not in ("yes", "y", "1")]
    return [(not bad, f"BoM {len(rows)} lines, {len(bad)} incomplete (price, stock or alternate missing)"
             + (f": {', '.join(bad[:6])}" if bad else ""))]


def sims_ok(paths, prj=None):
    out = []
    for p in paths:
        t = open(p, errors="ignore").read()
        has_svr = re.search(r"simulation verification record|\bSVR\b", t, re.I)
        rejected = re.search(r"\bREJECTED\b", t)
        problem = None
        if prj is not None:                  # forge spice records: also refuse NOT RUN and stale ones
            from .spice import record_problem
            problem = record_problem(prj, p)
        prov = re.search(r"^result: PROVISIONAL", t, re.M)
        out.append((bool(has_svr) and not rejected and not problem,
                    f"{os.path.basename(p)}: SVR {'present' if has_svr else 'MISSING'}"
                    + (", contains a REJECTED result" if rejected else "") + (f", {problem}" if problem else "")
                    + (", PROVISIONAL: its bench tests must be in the test plan" if prov and not problem else "")))
    return out or [(False, "no simulation result documents")]


def kicad(cmd, path):
    """(ok, message) for ERC / DRC of one file. A report kicad-cli did not produce is a FAIL, never a pass."""
    from . import kicad as kc
    rep = kc.erc(path) if cmd == "erc" else kc.drc(path)
    msg = rep.summary()
    if rep.tool_ok and rep.violations:
        msg += "; " + "; ".join(rep.lines(3))
    return rep.ok, msg


def check(prj, gate, mod=None):
    phase = next(p for p, g in GATE_OF.items() if g == gate)
    res = artefacts(prj, phase, mod)
    if gate == "G2":
        from . import vmodel
        try:
            res.append((vmodel.check(vmodel.Model(prj), quiet=True) == 0, "V-model check (trace / verification links)"))
        except Exception as e:  # malformed sources count as a failure, not a crash
            res.append((False, f"V-model check could not run: {e}"))
    if gate == "G4":
        res += bom_ok(os.path.join(prj.root, mod["dir"], "BOM.csv") if mod else prj.p("bom_csv"))
        from . import supply              # supply-chain risk: an unwaived HIGH (can't build, single-source IC, EOL) fails
        res += supply.gate_items(prj)
    if gate == "G5":
        base = os.path.join(prj.root, mod["dir"], "Simulations") if mod else prj.p("sim_results")
        res += sims_ok(glob.glob(os.path.join(base, "*.md")), prj)
    if gate in ("G6", "G7"):   # the same sub-check `forge check` runs, so the two never disagree
        from . import check as fc
        r = (fc.c_erc if gate == "G6" else fc.c_drc)(prj, {})
        kind = "ERC" if gate == "G6" else "DRC"
        if r["status"] == "skip":
            res.append((False, f"{kind}: skipped ({r['reason']}): nothing to check yet is not a pass"))
        else:
            bad = [f for f in r["findings"] if f["claim"].startswith(("no ", kind))]
            res.append((r["status"] != "fail", f"{kind} ({r['reason']}): {len(bad)} error(s)"
                        + "".join(f"; {f['location']}: {f['actual']}" for f in bad[:3])))
        from . import protection          # surge clamps vs the system and every protected pin (PROT-1..3)
        pr = protection.as_check(prj, {})
        res.append((pr["status"] != "fail", f"protection ({pr['reason'] or pr['status']}): {pr['status']}"
                    + "".join(f"; {f['claim']}: {f['actual']}" for f in pr["findings"][:2] if f["claim"].startswith("PROT-"))))
        from . import insulation          # INS-0/1/2, NET-4 at G6; the .kicad_dru proof (CLS-2) too at G7
        res += insulation.gate_items(prj, rules=gate == "G7")
        if gate == "G7":
            from . import pcb                # independent PCB review against every layout rule, on this board
            res += pcb.gate_items(prj)
    if gate == "G8":           # the fab release gate, strict: NRND parts and unrun power checks fail too
        from . import supply
        res += supply.gate_items(prj)
        from . import check as fc
        from . import insulation
        res += insulation.gate_items(prj, rules=True)
        from . import pcb
        res += pcb.gate_items(prj)
        r = fc.c_fab(prj, {"strict": True})
        if r["status"] == "skip":
            res.append((False, f"fab release gate: skipped ({r['reason']})"))
        for name, part in (r.get("parts") or {}).items():
            first = "; ".join(f"{f['location']}: {f['claim']}, {f['actual']}" for f in part["findings"][:2])
            res.append((part["status"] in ("pass", "skip"), f"fab {name}: {part['status']}" + (f" ({first})" if first else "")))
    hits = consistency.scan(prj)
    res.append((not hits, f"consistency: {len(hits)} live mention(s) of superseded terms"))
    from .phases import GATE_REVIEWERS
    if GATE_REVIEWERS.get(gate):
        res.append(review_ok(prj, gate, mod))
    from . import learn
    learn.record_gate(prj, gate, res)
    return res
