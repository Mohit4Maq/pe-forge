"""forge hp: the High-Power PCB Rule Book (docs/High_Power_PCB_Rule_Book.md) applied to a project by default.

  forge hp [--all] [--json]      every rule that applies to this board: AUTO PASS / AUTO FAIL / REVIEW / WAIVED / N/A
  forge hp rules                 the catalogue as parsed from the book (id, strength, text)

The book is the single source: rules are parsed from its "- **ID (Strength)** text" lines, so editing the book
edits the rule set. Which rules apply comes from the board profile (project.toml [hp]); rules the tool can measure
are checked on the board (pcbnew), the rest become rows the independent PCB review must grade.

project.toml
  [hp]
  enabled = true              # default: on when [specs] vin_V max >= 50 V or iout_A >= 5 A
  discrete_switches = false   # switches as separate parts (not inside a converter IC) -> PL / GD loop rules
  gate_driver = false         # GD rules         isolation = false     # isolated domains -> GD-12..16, HV-02
  shunts = false              # SNS-01..05, TX-04/05                   paralleled = false    # GD-17, HC-12, SNS-04, TX-09
  emi_filter = false          # FLT rules         heatsink = false      # SW-06..10, TH-08/09/11, HV-07 heatsink part
  strips = false              # HC-01..08 (surface busbars)            wbg = false           # SiC / GaN (WBG rules)
  multiphase = false          # GND-10, SW-11     magnetics = true      # SW-03/04, GD-11, FLT-07
  bus_V = 63                  # default [specs] vin_V max
  sw_nets = ["/SW"]           # default: nets named SW, LX, PHASE, HB (any sheet path)
  sw_related = ["/BOOT", "/SNUB"]   # switch-node-referenced nets (not "signals" for the overlap check)
  sw_edge_mm = 10             # SW-05 distance from the board edge and connectors
  hf_loops = [{ caps = ["C4"], switch = ["U1"], max_mm = 5 }]   # PL-02 HF caps at the switch pads
  cm_chokes = ["L2"]          # FLT-03 no copper under them on any layer
  hot_parts = { "U1" = 0.6 }  # W; TX-02 with precision = ["U3"] and precision_mm = 20
  [hp.budget]                 # PL-06: v_bus_max + L * di/dt <= 0.8 x V(BR)
  l_loop_nH = 1.0
  didt_A_per_ns = 1.0
  v_br = 85
  [hp.waive]                  # flexibility: rule = "reason (owner OK, date)"; a Must waiver needs "owner" in it
  "SW-05" = "50 mm board ... (owner OK 2026-11-08)"
"""
import json
import math
import os
import re

from . import kicad

BOOK = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "High_Power_PCB_Rule_Book.md")
AREAS = {"STK": "Stackup and planes", "PL": "Power commutation loop", "GD": "Gate drive", "SW": "Switch node and CM EMI",
         "GND": "Grounding and return paths", "SNS": "Sensing and control", "FLT": "EMI filter and I/O",
         "TH": "Thermal design", "TX": "Thermal crosstalk", "HC": "High-current routing", "HV": "Creepage and clearance"}
ID_RE = r"(?:STK|PL|GD|SW|GND|SNS|FLT|TH|TX|HC|HV)-\d+"

# applicability: rule -> profile flag that must be true (rules not listed apply to every high-power board)
NEEDS = {}
for ids, flag in (
        ("PL-01 PL-03 PL-04 PL-05 PL-07 PL-08 PL-09 PL-11", "discrete_switches"),
        ("PL-08", "through_hole_switches"),
        (" ".join(f"GD-{i:02d}" for i in range(1, 18)), "gate_driver"),
        ("GD-12 GD-13 GD-14 GD-16 HV-02", "isolation"),
        ("GD-17 HC-12 SNS-04 TX-09", "paralleled"),
        ("SNS-01 SNS-02 SNS-03 SNS-04 SNS-05 TX-04 TX-05", "shunts"),
        ("SNS-06", "hall_sensors"),
        ("SNS-08", "hv_dividers"),
        ("SNS-09", "gate_driver"),
        (" ".join(f"FLT-{i:02d}" for i in range(1, 12)), "emi_filter"),
        ("SW-06 SW-07 SW-08 SW-09 SW-10 TH-08 TH-09 TH-11", "heatsink"),
        (" ".join(f"HC-{i:02d}" for i in range(1, 9)), "strips"),
        ("GND-10 SW-11", "multiphase"),
        ("SW-03 SW-04 GD-11 FLT-07", "magnetics"),
        ("GND-08 GND-09", "chassis"),
        ("STK-02 STK-06 HV-10", "bus_over_400"),
        ("STK-08", "hot_over_105"),
        ("TX-10", "fan")):
    for i in ids.split():
        NEEDS.setdefault(i, []).append(flag)


def catalogue(path=BOOK):
    t = open(path).read()
    out = []
    area = None
    for line in t.splitlines():
        m = re.match(r"^- \*\*(" + ID_RE + r") \(([^)]+)\)\*\* (.*)$", line)
        if m:
            s = m.group(2)
            out.append({"id": m.group(1), "strength": s.split(",")[0].strip(), "wbg": "WBG" in s,
                        "area": AREAS[m.group(1).split("-")[0]], "text": m.group(3).strip()})
    return out


def profile(prj):
    hp = dict(prj.cfg.get("hp", {}))
    specs = prj.cfg.get("specs", {})
    vin = specs.get("vin_V")
    vmax = max(vin) if isinstance(vin, list) and vin else vin
    iout = specs.get("iout_A")
    imax = max(iout) if isinstance(iout, list) and iout else iout
    hp.setdefault("bus_V", vmax or 0)
    hp.setdefault("enabled", bool((vmax or 0) >= 50 or (imax or 0) >= 5))
    hp.setdefault("magnetics", True)
    hp["bus_over_400"] = (hp.get("bus_V") or 0) > 400
    return hp


def applies(rule, prof):
    if rule["wbg"] and not prof.get("wbg") and rule["strength"] != "Must":
        return False, "WBG rule, board is not SiC / GaN"
    for flag in NEEDS.get(rule["id"], []):
        if not prof.get(flag):
            return False, f"no {flag.replace('_', ' ')} on this board ([hp] {flag})"
    return True, ""


# ---------- board geometry (read-only pcbnew) ----------
GEO = r'''
import json, sys, pcbnew, re, math
s = json.load(open(sys.argv[1]))
b = pcbnew.LoadBoard(s["board"])
T = pcbnew.ToMM
mm = pcbnew.FromMM
bb = b.GetBoardEdgesBoundingBox()
w = max([T(d.GetWidth()) for d in b.GetDrawings() if d.GetLayer() == pcbnew.Edge_Cuts] or [0]) / 2
E = [T(bb.GetX()) + w, T(bb.GetY()) + w, T(bb.GetRight()) - w, T(bb.GetBottom()) - w]
CU = [l for l in pcbnew.LSET.AllCuMask().CuStack() if b.IsLayerEnabled(l)]
sw = set(s["sw_nets"])
sw_codes = {n.GetNetCode() for nm, n in b.GetNetsByName().items() if str(nm) in sw}
out = {"edges": E, "sw": {}, "layers": [b.GetLayerName(l) for l in CU]}
union = pcbnew.SHAPE_POLY_SET()
pts = []
for lay in CU:
    ps = pcbnew.SHAPE_POLY_SET()
    for z in b.Zones():
        if not z.GetIsRuleArea() and z.GetNetCode() in sw_codes and z.IsOnLayer(lay):
            ps.Append(z.GetFilledPolysList(lay))
    for f in b.GetFootprints():
        for p in f.Pads():
            if p.GetNetCode() in sw_codes and p.IsOnLayer(lay):
                p.TransformShapeToPolygon(ps, lay, 0, mm(0.005), pcbnew.ERROR_INSIDE)
    for t in b.GetTracks():
        if t.GetNetCode() in sw_codes and t.IsOnLayer(lay):
            t.TransformShapeToPolygon(ps, lay, 0, mm(0.005), pcbnew.ERROR_INSIDE)
    ps.Simplify()
    a = ps.Area() / 1e12
    if a > 0:
        out["sw"][b.GetLayerName(lay)] = round(a, 2)
        union.Append(ps)
        for i in range(ps.OutlineCount()):
            o = ps.Outline(i)
            for k in range(o.PointCount()):
                q = o.CPoint(k); pts.append((T(q.x), T(q.y)))
union.Simplify()
out["sw_edge_mm"] = round(min([min(x - E[0], E[2] - x, y - E[1], E[3] - y) for x, y in pts] or [999]), 2)
con = []
for f in b.GetFootprints():
    if re.match(r"^(J|P|X|CN)\d", f.GetReference()):
        c = f.GetCourtyard(pcbnew.F_CrtYd).BBox()
        x0, y0, x1, y1 = T(c.GetX()), T(c.GetY()), T(c.GetRight()), T(c.GetBottom())
        d = min([math.hypot(max(x0 - x, 0, x - x1), max(y0 - y, 0, y - y1)) for x, y in pts] or [999])
        con.append((f.GetReference(), round(d, 2)))
out["sw_connector_mm"] = con
# signal copper over / under switch-node copper (GD-08, STK-04)
rel = set(s["sw_related"]) | sw
hits = {}
for t in b.GetTracks():
    if t.GetClass() != "PCB_TRACK" or t.GetNetname() in rel or T(t.GetWidth()) > s["signal_w"]:
        continue
    a_, e_ = t.GetStart(), t.GetEnd()
    L = math.hypot(e_.x - a_.x, e_.y - a_.y)
    n = max(1, int(L / mm(0.25)))
    for k in range(n + 1):
        p = pcbnew.VECTOR2I(int(a_.x + (e_.x - a_.x) * k / n), int(a_.y + (e_.y - a_.y) * k / n))
        if union.Contains(p):
            key = t.GetNetname() + "@" + b.GetLayerName(t.GetLayer())
            hits[key] = hits.get(key, 0) + L / n / 1e6
out["sw_overlap"] = {k: round(v, 2) for k, v in hits.items()}
# exposed pads and via-in-pad (TH-02 / TH-03)
vias = [v for v in b.GetTracks() if v.GetClass() == "PCB_VIA"]
eps, vip = [], 0
for f in b.GetFootprints():
    for p in f.Pads():
        if p.GetAttribute() != pcbnew.PAD_ATTRIB_SMD:
            continue
        inside = [v for v in vias if p.HitTest(v.GetPosition())] + \
                 [q for q in f.Pads() if q is not p and q.GetAttribute() == pcbnew.PAD_ATTRIB_PTH and p.HitTest(q.GetPosition())]
        vip += len(inside)
        area = T(p.GetSize().x) * T(p.GetSize().y)
        if area >= 4 and f.GetReference()[:1] in "UQ" and p.GetNetCode() > 0:
            pitch = math.sqrt(area / len(inside)) if inside else None
            eps.append({"pad": f"{f.GetReference()}.{p.GetNumber()}", "area": round(area, 1), "vias": len(inside),
                        "pitch": round(pitch, 2) if pitch else None})
out["exposed_pads"] = eps
out["via_in_pad"] = vip
out["notes"] = [t.GetText() for t in b.GetDrawings() if t.GetClass() in ("PCB_TEXT", "PTEXT")
                and b.GetLayerName(t.GetLayer()) in ("User.Drawings", "Dwgs.User", "User.Comments", "Cmts.User", "F.Fab")]
# copper under CM chokes (FLT-03)
cm = {}
for f in b.GetFootprints():
    if f.GetReference() in s["cm_chokes"]:
        c = f.GetCourtyard(pcbnew.F_CrtYd)
        hit = []
        for lay in CU:
            for z in b.Zones():
                if not z.GetIsRuleArea() and z.IsOnLayer(lay):
                    x = pcbnew.SHAPE_POLY_SET(z.GetFilledPolysList(lay)); x.BooleanIntersection(c)
                    if x.Area() / 1e12 > 0.5:
                        hit.append(f"{b.GetLayerName(lay)} {z.GetNetname()}")
        cm[f.GetReference()] = hit
out["cm_copper"] = cm
pos = {}
for f in b.GetFootprints():
    pos[f.GetReference()] = [[T(p.GetPosition().x), T(p.GetPosition().y), p.GetNetname()] for p in f.Pads()]
out["pads"] = pos
print("REPORT" + json.dumps(out))
'''


def sw_nets(prj, board_nets=None):
    hp = prj.cfg.get("hp", {})
    if hp.get("sw_nets"):
        return hp["sw_nets"]
    return [n for n in (board_nets or []) if re.search(r"(^|/)(SW|LX|PHASE|HB|SWN)\w*$", n, re.I)]


def geometry(prj, board):
    from . import pcb
    from .insulation import export, parse, root_sheet
    nets = []
    sch = root_sheet(prj)
    if sch:
        text = export(sch)
        if text:
            nets = list(parse(text)[1].keys())
    hp = prj.cfg.get("hp", {})
    data = {"board": os.path.abspath(board), "sw_nets": sw_nets(prj, nets),
            "sw_related": hp.get("sw_related", [n for n in nets if re.search(r"(^|/)(BOOT|BST|SNUB|CB)\w*$", n, re.I)]),
            "signal_w": prj.cfg.get("pcb", {}).get("signal_max_width_mm", 0.4), "cm_chokes": hp.get("cm_chokes", [])}
    out, err = pcb._run(GEO, data)
    m = re.search(r"REPORT(\{.*\})", out)
    if not m:
        raise SystemExit("pcbnew high-power geometry failed: " + (err or out)[-600:])
    g = json.loads(m.group(1))
    g["sw_nets"] = data["sw_nets"]
    return g


def _dist(pads_a, pads_b, nets=None):
    best = 999
    for xa, ya, na in pads_a:
        for xb, yb, nb in pads_b:
            if nets is None or (na == nb and na):
                best = min(best, math.hypot(xa - xb, ya - yb))
    return best


def auto(prj, g, prof):
    """{rule_id: (ok, evidence)} for the rules the tool can measure."""
    pcfg, hp = prj.cfg.get("pcb", {}), prj.cfg.get("hp", {})
    R = {}
    if g is not None:
        sw = g["sw"]
        area = sum(sw.values())
        dvdt = hp.get("dvdt_V_per_ns", 50 if prof.get("wbg") else 10)
        cm = area / 100 * 19 * dvdt / 1000          # 19 pF/cm2 (book section 2), A peak
        lim = hp.get("sw_area_max_mm2")
        R["SW-01"] = (lim is None or area <= lim, f"SW copper {area:.1f} mm2 ({', '.join(f'{k} {v}' for k, v in sw.items())}); "
                      f"~{cm:.2f} A CM peak at {dvdt} V/ns over a plane" + (f"; limit {lim} mm2" if lim else
                      "; reviewer judges 'only as wide as the current needs'"))
        R["SW-02"] = (len(sw) <= 1, f"SW copper on {len(sw)} layer(s): {', '.join(sw) or 'none'}"
                      + ("" if len(sw) <= 1 else " (a through-hole pad or via on SW puts it on every layer)"))
        dmin = hp.get("sw_edge_mm", 10)
        near = [f"{r} {d} mm" for r, d in g["sw_connector_mm"] if d < dmin]
        R["SW-05"] = (g["sw_edge_mm"] >= dmin and not near,
                      f"SW copper {g['sw_edge_mm']} mm from the board edge" + (f"; near connectors: {', '.join(near)}" if near else "")
                      + f" (want >= {dmin} mm)")
        ov = g["sw_overlap"]
        R["GD-08"] = R["STK-04"] = (not ov, "no signal copper over / under switch-node copper" if not ov else
                                    "signal over / under SW copper: " + ", ".join(f"{k} {v} mm" for k, v in ov.items()))
        eps = g["exposed_pads"]
        bad = [e for e in eps if not e["vias"] or (e["pitch"] or 9) > 1.2]
        R["TH-02"] = (not bad, "; ".join(f"{e['pad']} {e['area']} mm2, {e['vias']} vias, pitch ~{e['pitch']} mm" for e in eps)
                      or "no exposed power pads")
        notes = " ".join(g["notes"])
        R["TH-03"] = (g["via_in_pad"] == 0 or "4761" in notes,
                      f"{g['via_in_pad']} via(s) in SMD pads; fab note " + ("IPC-4761 present" if "4761" in notes else "missing"))
        R["STK-09"] = (bool(re.search(r"\boz\b", notes)) and pcfg.get("copper_finish", "None") != "None",
                       "fab note with copper weights " + ("present" if re.search(r"\boz\b", notes) else "missing")
                       + f"; finish {pcfg.get('copper_finish', 'None')}")
        if hp.get("cm_chokes"):
            bad = {k: v for k, v in g["cm_copper"].items() if v}
            R["FLT-03"] = (not bad, "copper under CM chokes: " + (json.dumps(bad) if bad else "none"))
        pads = g["pads"]
        loops = hp.get("hf_loops", [])
        if loops:
            ev, ok = [], True
            for lp in loops:
                for c in lp["caps"]:
                    d = min(_dist(pads.get(c, []), pads.get(s_, []), nets=True) for s_ in lp["switch"])
                    ok &= d <= lp.get("max_mm", 5)
                    ev.append(f"{c} {d:.1f} mm from {'/'.join(lp['switch'])} (max {lp.get('max_mm', 5)})")
            R["PL-02"] = (ok, "; ".join(ev))
        hot, prec = hp.get("hot_parts", {}), hp.get("precision", [])
        if hot and prec:
            lim = hp.get("precision_mm", 20)
            close = [f"{p} {d:.1f} mm from {h}" for p in prec for h, w in hot.items() if w > 1
                     for d in [_dist(pads.get(p, []), pads.get(h, []))] if d < lim]
            R["TX-02"] = (not close, "; ".join(close) or f"precision parts >= {lim} mm from parts > 1 W")
    oz = pcfg.get("copper_oz")
    if oz:
        R["STK-05"] = (list(oz) == list(reversed(oz)), f"copper weights {oz} " + ("symmetric" if list(oz) == list(reversed(oz)) else "NOT symmetric"))
    ins = prj.cfg.get("insulation", {})
    keys = [k for k in ("standard", "pd", "ovc", "altitude_m", "cti") if k not in ins and k not in hp]
    R["HV-01"] = (not keys, "recorded: " + ", ".join(k for k in ("standard", "pd", "ovc", "altitude_m", "cti") if k not in keys)
                  + ("; missing: " + ", ".join(keys) if keys else ""))
    bud = hp.get("budget")
    if bud:
        vb = bud.get("v_bus_max", prof.get("bus_V") or 0)
        pk = vb + bud["l_loop_nH"] * bud["didt_A_per_ns"]
        lim = bud.get("ratio", 0.8) * bud["v_br"]
        R["PL-06"] = (pk <= lim, f"{vb:g} V + {bud['l_loop_nH']:g} nH x {bud['didt_A_per_ns']:g} A/ns = {pk:.1f} V vs "
                      f"{bud.get('ratio', 0.8):g} x {bud['v_br']:g} V = {lim:.1f} V")
    return R


def evaluate(prj, board=None, with_geometry=True):
    prof = profile(prj)
    cat = catalogue()
    waive = prj.cfg.get("hp", {}).get("waive", {})
    g = None
    if with_geometry and board and prof["enabled"] and kicad.find_python():
        g = geometry(prj, board)
    A = auto(prj, g, prof) if prof["enabled"] else {}
    rows = []
    for r in cat:
        ok_app, why = applies(r, prof)
        row = dict(r)
        if not prof["enabled"]:
            row.update(status="N/A", evidence="high-power rules off ([hp] enabled = false)")
        elif not ok_app:
            row.update(status="N/A", evidence=why)
        elif r["id"] in waive:
            w = waive[r["id"]]
            good = r["strength"] != "Must" or "owner" in w.lower()
            row.update(status="WAIVED" if good else "WAIVER INVALID",
                       evidence=w if good else f"{w} (a Must waiver needs the owner's recorded OK)")
        elif r["id"] in A:
            ok, ev = A[r["id"]]
            row.update(status="AUTO PASS" if ok else "AUTO FAIL", evidence=ev)
        else:
            row.update(status="REVIEW", evidence="")
        rows.append(row)
    return prof, rows


def as_check(prj, opts):
    from .check import finding, result, roots, has_content
    pcbs = [b_ for b_ in roots(prj, "kicad_pcb") if has_content(b_)]
    prof = profile(prj)
    if not prof["enabled"]:
        return result("skip", reason="[hp] high-power rules off")
    try:
        _, rows = evaluate(prj, pcbs[0] if pcbs else None)
    except SystemExit as e:
        return result("skip", reason=str(e), missing_tool=True)
    F = [finding(f"{r['id']} ({r['strength']}): {r['text'][:110]}", r["evidence"], prj.paths.get("layout_spec", ""),
                 "fix the layout, or waive it in project.toml [hp.waive] with the reason (a Must needs the owner's OK)")
         for r in rows if r["status"] in ("AUTO FAIL", "WAIVER INVALID")]
    must_fail = any(r["status"] in ("AUTO FAIL", "WAIVER INVALID") and r["strength"] == "Must" for r in rows)
    n = {s: sum(1 for r in rows if r["status"] == s) for s in ("AUTO PASS", "AUTO FAIL", "REVIEW", "WAIVED", "N/A")}
    return result("fail" if must_fail else "warn" if F else "pass", F,
                  reason=", ".join(f"{v} {k.lower()}" for k, v in n.items() if v))


def review_rows(prj, board):
    """Rows for the PCB review form: every applicable rule, auto result pre-filled."""
    _, rows = evaluate(prj, board)
    return [r for r in rows if r["status"] != "N/A"]


def main(prj, argv):
    if argv[:1] == ["rules"]:
        for r in catalogue():
            print(f"{r['id']:7s} {r['strength']:10s} {r['text'][:100]}")
        return 0
    from .check import roots, has_content
    pcbs = [b_ for b_ in roots(prj, "kicad_pcb") if has_content(b_)]
    prof, rows = evaluate(prj, pcbs[0] if pcbs else None)
    if "--json" in argv:
        print(json.dumps({"profile": prof, "rules": rows}, indent=1))
        return 0
    if not prof["enabled"]:
        print("high-power rules off ([hp] enabled = false, or bus < 50 V and current < 5 A)")
        return 0
    show = rows if "--all" in argv else [r for r in rows if r["status"] != "N/A"]
    for r in show:
        print(f"  {r['status']:10s} {r['id']:7s} {r['strength']:6s} {r['text'][:80]}" + (f"\n             {r['evidence']}" if r["evidence"] else ""))
    n = {s: sum(1 for r in rows if r["status"] == s) for s in ("AUTO PASS", "AUTO FAIL", "REVIEW", "WAIVED", "N/A")}
    print("  " + ", ".join(f"{v} {k.lower()}" for k, v in n.items()))
    bad = [r for r in rows if r["status"] in ("AUTO FAIL", "WAIVER INVALID") and r["strength"] == "Must"]
    return 1 if bad else 0
