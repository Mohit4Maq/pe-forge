"""The project's V-cycle: every stage of the V, what is in it, where the project is, and what needs attention.

  forge vcycle [--json]

Left side (definition) and right side (verification) pair up by level:
  L0  Concept & needs            <->  Validation            (field, certification, release)
  L1  System requirements        <->  System verification
  L2  Architecture + HW / SW / control requirements  <->  Integration (HW + FW, board)
  L3  Module design              <->  Block verification    (simulations, analyses)
  L4  Build: schematic, PCB, firmware  <->  Unit checks     (ERC, DRC, library, drift)
Every number comes from the project's own files: the intake, Foundation folders, System_Requirements.md, the V-model
trace.csv and verification.csv (by level), module phases and documents, simulation results, the last forge check.
"""
import glob
import json
import os

from . import state as S

ORDER = ["concept", "sysreq", "arch", "design", "build", "unit", "block", "integration", "sysver", "validation"]
TITLE = {"concept": "Concept & needs", "sysreq": "System requirements", "arch": "Architecture & HW/SW reqs",
         "design": "Module design", "build": "Build: schematic, PCB, firmware", "unit": "Unit checks",
         "block": "Block verification", "integration": "Integration", "sysver": "System verification",
         "validation": "Validation"}
LEVEL = {"concept": "L0", "validation": "L0", "sysreq": "L1", "sysver": "L1", "arch": "L2", "integration": "L2",
         "design": "L3", "block": "L3", "build": "L4", "unit": "L4"}
PAIR = {"concept": "validation", "sysreq": "sysver", "arch": "integration", "design": "block", "build": "unit"}
PAIR.update({v: k for k, v in PAIR.items()})
# which phase puts the project in which stage of the V
STAGE_OF_PHASE = {"P0": "concept", "P1": "concept", "P2": "sysreq", "P3": "arch", "P4": "design", "P5": "block",
                  "P6": "build", "P7": "integration", "P8": "validation"}
PHASES_OF = {"concept": ["P0", "P1"], "sysreq": ["P2"], "arch": ["P3"], "design": ["P4", "P6"], "build": ["P6", "P7"],
             "unit": ["P6", "P7"], "block": ["P5"], "integration": ["P7"], "sysver": ["P8"], "validation": ["P8"]}


def _count(pattern):
    return len([f for f in glob.glob(pattern, recursive=True) if not f.endswith(".gitkeep")])


def _filled(path):
    from .gates import filled
    return os.path.exists(path) and filled(path)


def _json(p, default=None):
    try:
        return json.load(open(p))
    except (OSError, ValueError):
        return default


def _rel(prj, p):
    return prj.rel(p) if os.path.exists(p) else None


def build(prj):
    st = S.load(prj)
    scope, pid, nxt = S.next_action(st)
    mods = st["modules"]
    current = STAGE_OF_PHASE.get(pid, "validation" if pid == "-" else "concept")

    def phase_status(pids):
        sts = []
        for p in pids:
            if scope != "system" and p in ("P2", "P3", "P4", "P5", "P6") and mods:
                m = S.module(st, scope)
                sts.append(m["phases"].get(p, {}).get("status", "todo") if m else st["phases"][p]["status"])
            else:
                sts.append(st["phases"][p]["status"])
        return sts

    N = {k: {"id": k, "title": TITLE[k], "level": LEVEL[k], "pair": PAIR[k], "side": "left" if k in
             ("concept", "sysreq", "arch", "design") else "bottom" if k in ("build", "unit") else "right",
             "facts": [], "items": [], "issues": [], "docs": [], "phases": PHASES_OF[k]} for k in ORDER}

    # ---- L0 concept
    n = N["concept"]
    lit, notes, refs = _count(prj.p("literature", "**", "*.pdf")), _count(prj.p("lit_notes", "*.md")), \
        _count(prj.p("ref_design", "**", "*.*"))
    survey = prj.p("market", "Market_Survey.md")
    n["facts"] = [f"market survey {'written' if _filled(survey) else 'not written yet'}",
                  f"{lit} paper(s), {notes} literature note(s), {refs} reference-design file(s)",
                  f"standards register {'filled in' if _filled(prj.p('standards')) else 'not filled in yet'}"]
    n["docs"] = [d for d in (_rel(prj, prj.p("foundation", "Intake_Answers.md")), _rel(prj, survey),
                             _rel(prj, prj.p("background")), _rel(prj, prj.p("standards"))) if d]
    if not _filled(survey):
        n["issues"].append("market survey missing (P1)")

    # ---- V-model data
    sys_rows, trace, ver, vm_err = {}, [], [], []
    try:
        from . import vmodel
        m = vmodel.Model(prj)
        sys_rows, trace, ver = m.sys, m.tr, list(m.ver.values())
        vm_err = vmodel.problems(m)
    except Exception as e:      # malformed sources are an issue to show, not a crash
        vm_err = [f"V-model sources do not parse: {e}"]
    real = {k: v for k, v in sys_rows.items() if "fill in" not in (v.get("text", "") + v.get("title", ""))}

    # ---- L1 system requirements
    n = N["sysreq"]
    n["facts"] = [f"{len(real)} system requirement(s)" + (f" ({len(sys_rows) - len(real)} still a template row)"
                                                          if len(sys_rows) > len(real) else "")]
    n["items"] = [f"{k}: {v.get('title') or v.get('text', '')}"[:70] for k, v in list(real.items())[:8]]
    n["docs"] = [d for d in (_rel(prj, prj.p("sys_req")), _rel(prj, prj.p("vmodel", "trace.csv"))) if d]
    if not real:
        n["issues"].append("no system requirement written yet")

    # ---- L2 architecture + derived requirements
    n = N["arch"]
    by = {}
    for r in trace:
        by.setdefault(r["id"].split("-")[0], []).append(r)
    adrs = _count(prj.p("decisions", "ADR-*.md"))
    trade = prj.p("architecture", "Topology_Trade.md")
    n["facts"] = [", ".join(f"{len(v)} {k}" for k, v in sorted(by.items())) + " derived requirement(s)" if by
                  else "no HW / SW / control requirement yet",
                  f"topology trade {'written' if _filled(trade) else 'not written yet'} · {adrs} ADR(s) · "
                  f"{len(mods)} module(s)"]
    n["items"] = [f"{m_['id']} {m_['name']} ({m_['status']})" for m_ in mods[:8]]
    n["issues"] = [e for e in vm_err if "parent" in e or "duplicate" in e][:6]
    n["docs"] = [d for d in (_rel(prj, trade), _rel(prj, prj.p("requirements", "Architecture", "Module_Breakdown.md"))) if d]

    # ---- L3 module design
    n = N["design"]
    for m_ in mods:
        d = os.path.join(prj.root, m_["dir"])
        bom = os.path.join(d, "BOM.csv")
        lines = max(0, sum(1 for _ in open(bom)) - 1) if os.path.exists(bom) else 0
        have = [x for x, f in (("calcs", "Design_Calculations.md"), ("booklet", "Design_Booklet.md"),
                               ("brief", "Sheet_Brief.md")) if _filled(os.path.join(d, f))]
        n["items"].append(f"{m_['id']} {m_['name']}: BoM {lines} line(s); " + (", ".join(have) or "no design docs yet")
                          + " · " + " ".join(f"{k}{'✓' if v['status'] == 'passed' else '·'}" for k, v in m_["phases"].items()))
    n["facts"] = [f"{len(mods)} module(s)" if mods else "single-board project: design docs at system level",
                  f"{_count(prj.p('booklets', '*.md'))} system booklet(s), {_count(prj.p('briefs', '*.md'))} sheet brief(s)"]

    # ---- L4 build
    n = N["build"]
    from .check import has_content
    sheets = [f for f in glob.glob(os.path.join(prj.p("pcb"), "**", "*.kicad_sch"), recursive=True) if "-backups" not in f]
    boards = [f for f in glob.glob(os.path.join(prj.p("pcb"), "**", "*.kicad_pcb"), recursive=True) if "-backups" not in f]
    drawn = [f for f in sheets if has_content(f)]
    laid = [f for f in boards if has_content(f)]
    fw = _count(os.path.join(prj.p("firmware"), "**", "*.[ch]")) + _count(os.path.join(prj.p("firmware"), "**", "*.cpp"))
    n["facts"] = [f"{len(drawn)} of {len(sheets)} sheet(s) drawn · {len(laid)} of {len(boards)} board(s) with parts",
                  f"{fw} firmware source file(s)"]
    n["items"] = [prj.rel(f) for f in drawn[:6]]

    # ---- right side
    ck = _json(prj.p("tools_data", "cache", "check_full.json"))
    led = _json(prj.p("tools_data", "ledger.json"), {}) or {}
    n = N["unit"]
    if ck:
        for name in ("erc", "drc", "sch_lib", "pcb_extra", "drift", "consistency", "legibility", "parts_evidence"):
            c = ck["checks"].get(name)
            if c:
                n["items"].append(f"{name}: {c['status']}" + (f" ({c.get('reason')})" if c.get("reason") else ""))
                if c["status"] == "fail":
                    n["issues"].append(f"{name} fails" + (f": {c['findings'][0].get('actual', '')}"[:80] if c["findings"] else ""))
        n["facts"] = [f"last forge check {ck.get('at', '')[:16].replace('T', ' ')}: {'OK' if ck['ok'] else 'FAILED'}"]
    else:
        n["facts"] = ["no full forge check yet"]
    opn = [o for o in led.get("open", []) if o.get("opened_by") != "stop"]
    if opn:
        n["issues"].append(f"{len(opn)} open re-check(s): " + ", ".join(f"{o['kind']}:{o['target']}" for o in opn[:4]))

    def ver_level(L):
        rows = [v for v in ver if v.get("level") == L]
        done = [v for v in rows if v.get("status", "").lower() in ("passed", "done", "pass")]
        failed = [v for v in rows if v.get("status", "").lower() in ("failed", "fail")]
        return rows, done, failed

    from .results import load_results
    res = load_results(prj)
    n = N["block"]
    grades = {}
    for r in res:
        grades[r.get("grade")] = grades.get(r.get("grade"), 0) + 1
    rows, done, failed = ver_level("L3")
    n["facts"] = [f"{len(res)} simulation result(s): " + (", ".join(f"{v} {k}" for k, v in grades.items()) or "none"),
                  f"{len(rows)} L3 verification item(s), {len(done)} done"]
    n["items"] = [f"{r.get('grade', '?')}: {r.get('title', r.get('id'))}"[:70] for r in res[:6]]
    n["issues"] = [f"{r.get('title', r.get('id'))}: {r['grade']}" for r in res if r.get("grade") in ("REJECTED", "NOT RUN")][:4] + \
        [f"{r.get('title', r.get('id'))}: stale" for r in res if r.get("stale")][:2] + [f"{v['id']} failed" for v in failed][:3]

    for key, L in (("integration", "L2"), ("sysver", "L1"), ("validation", "L0")):
        n = N[key]
        rows, done, failed = ver_level(L)
        n["facts"] = [f"{len(rows)} {L} verification item(s), {len(done)} done, {len(failed)} failed"]
        n["items"] = [f"{v['id']}: {v.get('title', '')} ({v.get('status', '')})"[:70] for v in rows[:8]]
        n["issues"] = [f"{v['id']} failed" for v in failed][:4]
        n["docs"] = [d for d in (_rel(prj, prj.p("validation", "DVP_and_R.md")), _rel(prj, prj.p("vmodel", "verification.csv"))) if d]
    N["validation"]["facts"].append(f"release: {st['phases']['P8']['status']}")
    unc = [k for k in real if not any(k in (r.get("parents") or "") for r in trace)]
    if unc:
        N["sysver"]["facts"].append(f"{len(unc)} SYS row(s) verified at system level only (no derived requirement)")

    # ---- status per stage
    for k in ORDER:
        n = N[k]
        sts = phase_status(n["phases"])
        if k == current:
            n["status"] = "active"
        elif sts and all(s in ("passed", "skipped") for s in sts):
            n["status"] = "done"
        elif any(s in ("active", "in-review") for s in sts):
            n["status"] = "active" if k == current else "open"
        else:
            n["status"] = "todo"
        if n["issues"] and n["status"] != "todo":
            n["attention"] = True
        n["ask"] = (f"pe-forge V-cycle, {n['title']} ({n['level']}, {'definition' if n['side'] == 'left' else 'verification' if n['side'] == 'right' else 'build / unit'}): "
                    f"what is happening in this stage of {prj.name}? Give the status, what is done, what is open or "
                    f"failing, and the next step, from the project files" + (f" ({', '.join(n['docs'][:3])})" if n["docs"] else "") + ".")
    return {"current": current, "next": nxt, "phase": pid, "scope": scope, "order": ORDER, "nodes": N}


def main(prj, argv):
    v = build(prj)
    if "--json" in argv:
        print(json.dumps(v, default=str))
        return 0
    for k in ORDER:
        n = v["nodes"][k]
        mark = "▶" if k == v["current"] else {"done": "✓", "open": "·", "todo": " ", "active": "▶"}[n["status"]]
        print(f"{mark} {n['level']} {n['title']:<34} " + " · ".join(n["facts"]) + ("  ⚠ " + "; ".join(n["issues"]) if n["issues"] else ""))
    print(f"Now: {v['nodes'][v['current']]['title']} ({v['phase']}) · next: {v['next']}")
    return 0
