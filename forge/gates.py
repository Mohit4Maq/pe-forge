"""Automated gate checks. `forge gate check G4 [--module M-02]` prints PASS / FAIL per item.

Artefact rule: each listed file must exist and be filled in. A markdown file counts as filled when it has at
least 8 lines of content that are not template placeholders ("_(fill in)_", "TODO", "{{", "TBD" only rows).
"""
import csv
import glob
import json
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


PLACEHOLDER = re.compile(r"_\(fill in\)_|\{\{[A-Z_]+\}\}|\(fill in\)|lorem ipsum", re.I)
MIN_WORDS = {"md": 120}


def content_problem(path, min_words=MIN_WORDS["md"]):
    """Why a deliverable is not filled in: placeholders left, or too little content to be a deliverable."""
    if not os.path.exists(path):
        return "missing"
    if not path.lower().endswith((".md", ".txt", ".csv")):
        return None
    t = open(path, errors="ignore").read()
    body = [l for l in t.splitlines() if l.strip() and not l.startswith(("---", "#", "|---", "> [!"))]
    ph = PLACEHOLDER.findall(t)
    if ph and not path.lower().endswith(".csv"):
        return f"placeholders left: {', '.join(sorted(set(p.strip() for p in ph))[:3])}"
    if path.lower().endswith(".md") and len(" ".join(body).split()) < min_words:
        return f"{len(' '.join(body).split())} words of content (a deliverable needs {min_words}+)"
    return None


REVIEWS = os.path.join(".forge", "reviews.json")


def _hash_file(p):
    import hashlib
    return hashlib.sha256(open(p, "rb").read()).hexdigest()[:16]


def deliverable_hashes(prj, phase, mod=None):
    """{relpath: hash} of the phase's artefacts, plus every schematic / board (what a review is a review of)."""
    from .phases import PHASE
    out = {}
    for key in PHASE[phase][4]:
        pat = key.format(**{k: prj.paths[k] for k in prj.paths}) if "{" in key else key
        base = os.path.join(prj.root, mod["dir"]) if mod and not pat.startswith(("{", "0", "1", "9", "p")) else prj.root
        for f in glob.glob(os.path.join(prj.root, pat), recursive=True) + glob.glob(os.path.join(base, pat), recursive=True):
            if os.path.isfile(f) and "-backups" not in f:
                out[prj.rel(f)] = _hash_file(f)
    for ext in ("kicad_sch", "kicad_pcb"):
        for f in glob.glob(os.path.join(prj.p("pcb"), "**", "*." + ext), recursive=True):
            if "-backups" not in f and "/release/" not in f:
                out[prj.rel(f)] = _hash_file(f)
    return out


def record_review(prj, gate, review_file, mod=None):
    """Bind a review to what it reviewed: the review file's hash, the reviewer named in it, its verdict, and the
    hashes of every deliverable at that moment. `review_ok` refuses a gate whose record is missing, whose review
    file changed, or whose deliverables changed since (a review of other content is no review)."""
    import datetime as _dt
    p = os.path.join(prj.root, review_file)
    if not os.path.exists(p):
        raise SystemExit(f"review file {review_file} does not exist")
    t = open(p, errors="ignore").read()
    m = re.search(r"^(?:reviewer|agent|by):\s*(.+)$", t, re.M)
    v = re.search(r"^verdict:\s*(.+)$", t, re.M)
    if not m:
        raise SystemExit("the review file must name its reviewer in the frontmatter (reviewer: pe-reviewer)")
    if not v:
        raise SystemExit("the review file must carry its verdict in the frontmatter (verdict: READY | READY WITH MINORS | NOT READY)")
    phase = next(p_ for p_, g in GATE_OF.items() if g == gate)
    rec = {"gate": gate, "module": (mod or {}).get("id", "system"), "review": prj.rel(p), "review_sha": _hash_file(p),
           "reviewer": m.group(1).strip(), "verdict": v.group(1).strip().upper(),
           "date": _dt.date.today().isoformat(), "deliverables": deliverable_hashes(prj, phase, mod)}
    f = os.path.join(prj.root, REVIEWS)
    try:
        d = json.load(open(f))
    except (OSError, ValueError):
        d = []
    d.append(rec)
    os.makedirs(os.path.dirname(f), exist_ok=True)
    json.dump(d, open(f, "w"), indent=1)
    return rec


def review_record_ok(prj, gate, mod=None):
    """(ok, why) from .forge/reviews.json for this gate and module: present, verdict READY*, file and deliverables unchanged."""
    want = (mod or {}).get("id", "system")
    try:
        recs = [r for r in json.load(open(os.path.join(prj.root, REVIEWS))) if r["gate"] == gate and r["module"] == want]
    except (OSError, ValueError):
        recs = []
    if not recs:
        return False, f"no recorded {gate} review for {want} (forge review record {gate} <review file>)"
    r = recs[-1]
    p = os.path.join(prj.root, r["review"])
    if not os.path.exists(p) or _hash_file(p) != r["review_sha"]:
        return False, f"the recorded {gate} review {r['review']} changed since it was recorded"
    if not r["verdict"].startswith("READY"):
        return False, f"recorded {gate} review verdict {r['verdict']}"
    phase = next(p_ for p_, g in GATE_OF.items() if g == gate)
    now = deliverable_hashes(prj, phase, mod)
    changed = sorted(k for k in set(now) | set(r["deliverables"]) if now.get(k) != r["deliverables"].get(k))
    if changed:
        return False, f"{len(changed)} deliverable(s) changed since the {gate} review by {r['reviewer']}: " + ", ".join(changed[:4]) + ": review again"
    return True, f"{gate} review by {r['reviewer']} ({r['date']}) bound to {len(r['deliverables'])} unchanged file(s)"


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
            mw = 0 if phase == "P0" else MIN_WORDS["md"]          # P0 holds forms and the plan, not deliverables
            empty = [f"{prj.rel(h)} ({content_problem(h, mw) or 'template lines only'})" for h in hits if not filled(h) or content_problem(h, mw)]
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
    msg = f"latest {gate} review ({r.get('date')}): verdict {r.get('verdict')}, status {r.get('status')}"
    if ok and prj.level != "light":          # a row in a table is a claim; the record binds it to the content (F-09)
        ok2, why = review_record_ok(prj, gate, mod)
        return ok2, msg + "; " + why
    return ok, msg


def record_owner_pass(prj, gate, module, comment):
    """The owner's verification of a gate is part of the record: a QC row with their comment."""
    import datetime as _dt
    f = prj.p("qc_register")
    if not os.path.exists(f):
        return
    open(f, "a").write(f"| {_dt.date.today().isoformat()} | {gate} | {module} | owner verification | - | OWNER OK | - | - | "
                       f"{comment.replace('|', '/')} | ACCEPTED |\n")


def record_override(prj, gate, module, bad, note):
    """An owner override is a QC event: one row in the register, verdict OVERRIDE, listing what was failing."""
    import datetime as _dt
    f = prj.p("qc_register")
    if not os.path.exists(f):
        return
    what = "; ".join(bad)[:300].replace("|", "/")
    row = (f"| {_dt.date.today().isoformat()} | {gate} | {module} | owner override: {what} | - | OVERRIDE | - | - | "
           f"{note.replace('|', '/')} | ACCEPTED |\n")
    open(f, "a").write(row)


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


def main_review(prj, argv):
    """forge review record G<n> <review file> [--module M-xx]"""
    if argv[:1] != ["record"] or len(argv) < 3:
        print("forge review record G<n> <review file> [--module M-xx]   bind an independent review to the deliverables it reviewed")
        return 1
    from . import state
    gate, f = argv[1], argv[2]
    mod = None
    if "--module" in argv:
        st = state.load(prj)
        mod = next((m for m in st["modules"] if m["id"] == argv[argv.index("--module") + 1]), None)
    rec = record_review(prj, gate, f, mod)
    print(f"recorded {gate} review {rec['review']} by {rec['reviewer']}: verdict {rec['verdict']}, "
          f"{len(rec['deliverables'])} deliverable(s) hashed")
    return 0


def release_process_items(prj):
    """The firm stop before boards are made: never in light mode, and only with the requirements and V-model in
    place (P2 passed, not skipped by an adoption). Light mode helps on any board; releasing one needs the process."""
    from . import state
    res = []
    if prj.level == "light":
        res.append((False, "process level is light (advice only): raise it before a release, `forge level standard`"))
    try:
        p2 = state.load(prj)["phases"]["P2"]["status"]
    except Exception:
        p2 = "unknown"
    enc = prj.cfg.get("enclosure", {})
    if enc.get("required") or enc.get("ip"):
        from . import enclosure
        res += enclosure.gate_items(prj)
    if prj.process.get("sketch") is True:
        res.append((False, "[process] sketch = true: a sketch is never released; finish the phases and set sketch = false"))
    res.append((p2 == "passed", f"requirements and V-model in place before release: P2 is {p2}"
                + ("" if p2 == "passed" else " (draft them from the design: forge req infer; then pass G2)")))
    return res


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
        from . import results             # results as data: valid, citable (provenance), the required set shown,
        res += results.gate_items(prj)    # closed forms reproduced by pe-forge itself (F-07)
    if gate in ("G6", "G7"):   # the same sub-check `forge check` runs, so the two never disagree
        from . import check as fc
        r = (fc.c_erc if gate == "G6" else fc.c_drc)(prj, {})
        kind = "ERC" if gate == "G6" else "DRC"
        if r["status"] == "skip":
            res.append((False, f"{kind}: skipped ({r['reason']}): nothing to check yet is not a pass"))
        else:
            bad = [f for f in r["findings"] if not f["claim"].startswith(("no ERC warnings", "no DRC warnings"))]
            warn = len(r["findings"]) - len(bad)
            res.append((r["status"] != "fail", f"{kind} ({r['reason']}): {len(bad)} error(s)"
                        + (" (unrouted / parity count as errors)" if gate == "G7" else "") + f", {warn} warning(s)"
                        + "".join(f"; {f['location']}: {f['actual']}" for f in bad[:3])))
        from . import protection          # surge clamps vs the system and every protected pin (PROT-1..3)
        pr = protection.as_check(prj, {})
        res.append((pr["status"] != "fail", f"protection ({pr['reason'] or pr['status']}): {pr['status']}"
                    + "".join(f"; {f['claim']}: {f['actual']}" for f in pr["findings"][:2] if f["claim"].startswith("PROT-"))))
        from . import insulation          # INS-0/1/2, NET-4 at G6; the .kicad_dru proof (CLS-2) too at G7
        res += insulation.gate_items(prj, rules=gate == "G7")
        from . import stress              # STR-1..7: ratings vs the operating corners, with derating
        res += stress.gate_items(prj)
        if gate == "G7":
            from . import pcb                # independent PCB review against every layout rule, on this board
            res += pcb.gate_items(prj)
            from . import fpcheck            # the board as drawn matches the symbols, polarity and the house's zero
            res += fpcheck.gate_items(prj)
            from . import models             # every part has a 3D model (height, fit, assembly drawing) or a waiver
            res += models.gate_items(prj)
    if gate == "G8":           # the fab release gate, strict: NRND parts and unrun power checks fail too
        res += release_process_items(prj)
        from . import supply
        res += supply.gate_items(prj, release=True)
        from . import check as fc
        from . import insulation
        res += insulation.gate_items(prj, rules=True)
        from . import stress
        res += stress.gate_items(prj)
        from . import pcb
        res += pcb.gate_items(prj)
        from . import fabhouse            # the house must be able to make the board as drawn
        fr = fabhouse.as_check(prj, {})
        res.append((fr["status"] == "pass", f"fab house rules: {fr['status']}" + (f" ({fr['reason']})" if fr["status"] != "pass" else "")
                    + "".join(f"; {f['actual']}" for f in fr["findings"][:2])
                    + (": set [fab] house = jlcpcb | pcbway" if fr["status"] == "skip" else "")))
        tp = fabhouse.as_check_tp(prj, {})
        res.append((tp["status"] != "fail", f"test points: {tp['status']} ({tp['reason']})"
                    + "".join(f"; {f['claim']}" for f in tp["findings"][:3])))
        r = fc.c_fab(prj, {"strict": True})
        if r["status"] == "skip":
            res.append((False, f"fab release gate: skipped ({r['reason']})"))
        for name, part in (r.get("parts") or {}).items():
            first = "; ".join(f"{f['location']}: {f['claim']}, {f['actual']}" for f in part["findings"][:2])
            # at the release gate a check that could not run is a failure, never a pass (F-03)
            res.append((part["status"] == "pass", f"fab {name}: {part['status']}"
                        + (f" (skipped: {part.get('reason', '')}: fix the tool or the input, a skip is not a pass)" if part["status"] == "skip" else "")
                        + (f" ({first})" if first else "")))
    hits = consistency.scan(prj)
    res.append((not hits, f"consistency: {len(hits)} live mention(s) of superseded terms"))
    from . import decisions           # a question only the owner can answer blocks its gate (forge decision)
    res += decisions.gate_items(prj, gate, mod)
    from .phases import GATE_REVIEWERS
    if GATE_REVIEWERS.get(gate):
        res.append(review_ok(prj, gate, mod))
    from . import learn
    learn.record_gate(prj, gate, res)
    return res
