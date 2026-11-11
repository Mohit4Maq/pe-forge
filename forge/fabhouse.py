"""forge fab rules | panel | testpoints: the fab house's capabilities, the panel, and test-point coverage.

  forge fab rules [--house jlcpcb|pcbway] [--json]   the board's design rules (.kicad_pro) against the house's
                                                      capabilities: a rule looser than what the house can make fails
  forge fab panel [--rows N] [--cols N]               KiKit panel (rails, mouse bites, tooling holes, fiducials) into
                                                      release/panel/ (KiKit on KiCad's Python: ~/.venvs/kicad-tools)
  forge fab testpoints [--json]                       every rail and key net has a test point (TP*) or a connector pin

project.toml:
  [fab]
  house = "jlcpcb"              # which capability table applies (forge check fab_rules; G8 item)
  copper_oz = 1                 # outer copper weight the rules are read for (default [pcb] copper_oz[0])
  test_nets = ["VIN", "+12V", "GND", "SW", "EN", "FB"]   # default: every input / output rail, GND, SW, EN, FB, PGOOD
  [fab.rules]                   # override a house value: min_track_mm = 0.2

Capability tables (standard service, from the houses' published capabilities; update them when the house does):
"""
import glob
import json
import os
import re
import subprocess

# min values the house can make (mm) for the standard service; keyed by outer copper weight where it matters
HOUSES = {
    "jlcpcb": {"name": "JLCPCB (standard 2-6 layer FR-4)",
               "track_space": {1: (0.127, 0.127), 2: (0.2, 0.2)},   # (min track, min space) by oz
               "min_drill_mm": 0.2, "min_via_diameter_mm": 0.4, "min_annular_mm": 0.1, "min_hole_to_hole_mm": 0.5,
               "min_copper_edge_mm": 0.3, "min_mask_sliver_mm": 0.2, "min_silk_width_mm": 0.15, "min_silk_text_mm": 1.0,
               "min_pth_diameter_mm": 0.2, "max_layers": 6},
    "pcbway": {"name": "PCBWay (standard FR-4)",
               "track_space": {1: (0.1, 0.1), 2: (0.2, 0.2), 3: (0.25, 0.25)},
               "min_drill_mm": 0.2, "min_via_diameter_mm": 0.4, "min_annular_mm": 0.15, "min_hole_to_hole_mm": 0.45,
               "min_copper_edge_mm": 0.3, "min_mask_sliver_mm": 0.2, "min_silk_width_mm": 0.15, "min_silk_text_mm": 0.8,
               "min_pth_diameter_mm": 0.2, "max_layers": 14},
}

# .kicad_pro rule -> (house key, direction): the board's minimum may not be below the house's
MAP = {"min_track_width": "track", "min_clearance": "space", "min_through_hole_diameter": "min_drill_mm",
       "min_via_diameter": "min_via_diameter_mm", "min_via_annular_width": "min_annular_mm",
       "min_hole_to_hole": "min_hole_to_hole_mm", "min_copper_edge_clearance": "min_copper_edge_mm",
       "min_silk_clearance": None}


def house_of(prj):
    cfg = prj.cfg.get("fab", {})
    name = (cfg.get("house") or "").lower()
    if name not in HOUSES:
        return None, cfg
    h = dict(HOUSES[name])
    oz = cfg.get("copper_oz") or (prj.cfg.get("pcb", {}).get("copper_oz") or [1])[0]
    oz = int(round(float(oz)))
    ts = h["track_space"]
    key = min((k for k in ts if k >= oz), default=max(ts))
    h["track"], h["space"] = ts[key]
    h.update({k: v for k, v in cfg.get("rules", {}).items()})
    h["oz"] = oz
    return h, cfg


def board_rules(pro):
    try:
        p = json.load(open(pro))
    except (OSError, ValueError):
        return None
    bds = p.get("board", {}).get("design_settings", {})
    rules = dict(bds.get("rules", {}))
    rules["_layers"] = p.get("board", {}).get("layer_count") or None
    return rules


def compare(rules, h):
    """[(ok, text)] board rule vs house capability."""
    out = []
    for k, hk in MAP.items():
        if hk is None or k not in rules:
            continue
        have, need = float(rules[k]), float(h[hk])
        out.append((have >= need - 1e-9, f"{k} {have:g} mm vs {h['name'].split(' (')[0]} min {need:g} mm"
                    + ("" if have >= need - 1e-9 else ": the house cannot make it; raise the rule or change house")))
    return out


def as_check(prj, opts):
    from .check import finding, result, roots
    h, cfg = house_of(prj)
    if not h:
        return result("skip", reason="no [fab] house set (jlcpcb | pcbway): the board's rules are not checked against a fab")
    pros = [os.path.splitext(b)[0] + ".kicad_pro" for b in roots(prj, "kicad_pcb")]
    pros = [p for p in pros if os.path.exists(p)]
    if not pros:
        return result("skip", reason="no board project yet")
    F, n = [], 0
    for pro in pros:
        rules = board_rules(pro)
        if rules is None:
            F.append(finding("board design rules readable", "unreadable .kicad_pro", prj.rel(pro)))
            continue
        for ok, text in compare(rules, h):
            n += 1
            if not ok:
                F.append(finding(f"board rule within {h['name']}", text, prj.rel(pro), "raise the rule in Board Setup > Constraints"))
        if rules.get("_layers") and rules["_layers"] > h["max_layers"]:
            F.append(finding(f"layer count within {h['name']}", f"{rules['_layers']} > {h['max_layers']}", prj.rel(pro)))
    return result("fail" if F else "pass", F, reason=f"{n} rule(s) vs {h['name']} at {h['oz']} oz")


# ------------------------------------------------------------------ test points
DEFAULT_TEST = ["GND", "SW", "EN", "FB", "PGOOD", "PG"]


def testpoints(prj):
    from . import insulation, reqinfer
    sch = insulation.root_sheet(prj)
    if not sch:
        return None, "no schematic yet"
    text = insulation.export(sch)
    if text is None:
        return None, "kicad-cli not found"
    parts, nets = insulation.parse(text)
    a = reqinfer.analyse(parts, nets, None)
    want = prj.cfg.get("fab", {}).get("test_nets")
    if not want:
        want = [reqinfer.short(n) for n in a["inputs"] + a["outputs"]] + DEFAULT_TEST
    rows = []
    for n in nets:
        s = reqinfer.short(n)
        if not any(s == w or (w.endswith("*") and s.startswith(w[:-1])) for w in want):
            continue
        tps = [r for r, _, _ in nets[n]["pins"] if re.match(r"^TP\d", r)]
        cons = [r for r, _, _ in nets[n]["pins"] if re.match(r"^(J|P|CN|X)\d", r)]
        rows.append({"net": s, "test_points": tps, "connector": cons, "ok": bool(tps or cons)})
    return rows, ""


def as_check_tp(prj, opts):
    from .check import finding, result
    rows, why = testpoints(prj)
    if rows is None:
        return result("skip", reason=why, missing_tool="kicad" in why)
    if not rows:
        return result("skip", reason="no rail or key net found to require test points on")
    W = [finding(f"test access on {r['net']}", "no test point (TP*) and no connector pin", "schematic",
                 "add a test point (SMD pad or loop) on the net") for r in rows if not r["ok"]]
    return result("warn" if W else "pass", W, reason=f"{len(rows)} net(s), {len(rows) - len(W)} with access")


# ------------------------------------------------------------------ panel
def kikit():
    for p in (os.path.expanduser("~/.venvs/kicad-tools/bin/kikit"), os.path.expanduser("~/.venvs/kicad-tools/Scripts/kikit.exe")):
        if os.path.exists(p):
            return p
    from shutil import which
    return which("kikit")


def panel(prj, board, rows=2, cols=2):
    kk = kikit()
    if not kk:
        raise SystemExit("KiKit not found (~/.venvs/kicad-tools): install it on KiCad's Python, see docs/High_Power_PCB_Rule_Book.md")
    from . import config
    cfgf = os.path.join(config.LIBRARY, "tools", "kikit_panel.json")
    out_dir = os.path.join(os.path.dirname(board), "release", "panel")
    os.makedirs(out_dir, exist_ok=True)
    name = os.path.splitext(os.path.basename(board))[0]
    out = os.path.join(out_dir, f"{name}-panel.kicad_pcb")
    r = subprocess.run([kk, "panelize", "-p", cfgf, "--layout", f"grid; rows: {rows}; cols: {cols}; space: 3mm", board, out],
                       capture_output=True, text=True)
    for lck in glob.glob(os.path.join(out_dir, "~*.lck")):
        os.remove(lck)
    if not os.path.exists(out):
        raise SystemExit("KiKit failed: " + (r.stderr or r.stdout)[-800:])
    return out


def main(prj, argv):
    if argv[:1] == ["rules"]:
        r = as_check(prj, {})
        if "--json" in argv:
            print(json.dumps(r, indent=1))
        else:
            print(f"{r['status'].upper()} ({r['reason']})")
            for f in r["findings"]:
                print(f"  {f['location']}: {f['actual']}")
        return 1 if r["status"] == "fail" else 0
    if argv[:1] == ["testpoints"]:
        r = as_check_tp(prj, {})
        print(json.dumps(r, indent=1) if "--json" in argv else f"{r['status'].upper()} ({r['reason']})\n"
              + "\n".join(f"  {f['claim']}: {f['actual']}" for f in r["findings"]))
        return 1 if r["status"] == "fail" else 0
    if argv[:1] == ["panel"]:
        from .check import roots
        pcbs = roots(prj, "kicad_pcb")
        if not pcbs:
            raise SystemExit("no board")
        g = lambda k, d: int(argv[argv.index(k) + 1]) if k in argv else d
        out = panel(prj, pcbs[0], g("--rows", 2), g("--cols", 2))
        print(f"panel: {prj.rel(out)} (open it in the PCB editor, run DRC, then plot Gerbers)")
        return 0
    print(__doc__)
    return 1
