"""Automated gate checks. `forge gate check G4 [--module M-02]` prints PASS / FAIL per item.

Artefact rule: each listed file must exist and be filled in. A markdown file counts as filled when it has at
least 8 lines of content that are not template placeholders ("_(fill in)_", "TODO", "{{", "TBD" only rows).
"""
import csv
import glob
import os
import re
import shutil
import subprocess

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


def sims_ok(paths):
    out = []
    for p in paths:
        t = open(p, errors="ignore").read()
        has_svr = re.search(r"simulation verification record|\bSVR\b", t, re.I)
        rejected = re.search(r"\bREJECTED\b", t)
        out.append((bool(has_svr) and not rejected, f"{os.path.basename(p)}: SVR {'present' if has_svr else 'MISSING'}"
                    + (", contains a REJECTED result" if rejected else "")))
    return out or [(False, "no simulation result documents")]


def kicad(cmd, path):
    cli = shutil.which("kicad-cli") or "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli"
    if not os.path.exists(cli) and not shutil.which("kicad-cli"):
        return False, "kicad-cli not found (run forge doctor)"
    out = path + (".erc.rpt" if cmd == "erc" else ".drc.rpt")
    kind = "sch" if cmd == "erc" else "pcb"
    subprocess.run([cli, kind, cmd, "--severity-error", "-o", out, path], capture_output=True, text=True)
    txt = open(out, errors="ignore").read() if os.path.exists(out) else ""
    m = re.search(r"\*\* Found (\d+) (?:ERC|DRC) violations", txt) or re.search(r"Found (\d+)", txt)
    n = int(m.group(1)) if m else -1
    return n == 0, f"{cmd.upper()} {os.path.basename(path)}: {n if n >= 0 else '?'} error(s)"


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
    if gate == "G5":
        base = os.path.join(prj.root, mod["dir"], "Simulations") if mod else prj.p("sim_results")
        res += sims_ok(glob.glob(os.path.join(base, "*.md")))
    if gate in ("G6", "G7"):
        ext = "*.kicad_sch" if gate == "G6" else "*.kicad_pcb"
        roots = [f for f in glob.glob(os.path.join(prj.p("pcb"), "**", ext), recursive=True)
                 if os.path.basename(f)[:-len(ext) + 1] == os.path.basename(os.path.dirname(f))]
        for f in roots or glob.glob(os.path.join(prj.p("pcb"), "**", ext), recursive=True)[:1]:
            res.append(kicad("erc" if gate == "G6" else "drc", f))
    hits = consistency.scan(prj)
    res.append((not hits, f"consistency: {len(hits)} live mention(s) of superseded terms"))
    from .phases import GATE_REVIEWERS
    if GATE_REVIEWERS.get(gate):
        res.append(review_ok(prj, gate, mod))
    return res
