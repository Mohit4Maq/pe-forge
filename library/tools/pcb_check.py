#!/usr/bin/env python3
"""pcb_check - layout checks KiCad's DRC doesn't do (reads the saved .kicad_pcb).

  pcb_check.py <project dir | .kicad_pro | .kicad_pcb> [--json] [--decap-mm 3]

  PCB-OUTLINE   no board outline yet
  PCB-OFFBOARD  footprints (partly) outside the outline
  PCB-DECAP     IC supply pin whose nearest capacitor on the same net is far away
  PCB-THERMAL   exposed/thermal pad with no vias to spread heat
  PCB-PWRTRACE  tracks on supply/HV nets thinner than the Power/HV rule
  PCB-FID       SMD board with fewer than 2 fiducials per assembled side
"""
import argparse
import fnmatch
import json
import math
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import pcbnew
except ImportError:   # not KiCad's Python: re-run under it (found by kicad_paths, same order as forge/kicad.py)
    import os
    import kicad_paths
    _py = kicad_paths.python()
    if _py and not os.environ.get("PE_FORGE_KPY_REEXEC") and os.path.realpath(_py) != os.path.realpath(sys.executable):
        os.environ["PE_FORGE_KPY_REEXEC"] = "1"
        os.execv(_py, [_py, __file__, *sys.argv[1:]])
    sys.exit("pcbnew (KiCad's Python module) not found: install KiCad 10 or set KICAD_PYTHON to its python3")

GROUND = re.compile(r"^(?:[ADPS]?GND\w*|VSS\w*|0V)$", re.I)
POWER_PATTERNS = ["+*", "VBAT*", "VIN*", "PGND*"]      # MC_Standard Power class (matched on the name after the last "/")
HV_PATTERNS = ["HV*", "*_HV*"]
mm = pcbnew.ToMM


def find_pcb(arg):
    p = Path(arg).expanduser().resolve()
    if p.is_dir():
        pros = list(p.glob("*.kicad_pro"))
        if len(pros) != 1:
            sys.exit(f"expected one .kicad_pro in {p}")
        p = pros[0]
    p = p.with_suffix(".kicad_pcb")
    if not p.exists():
        sys.exit(f"no board {p}")
    return p


def netclass_of(board, net_name):
    base = net_name.split("/")[-1]
    try:
        cls = board.FindNet(net_name).GetNetClassName()
        if cls and cls != "Default":
            return cls
    except Exception:
        pass
    if any(fnmatch.fnmatch(base, p) for p in HV_PATTERNS):
        return "HV"
    if any(fnmatch.fnmatch(base, p) for p in POWER_PATTERNS):
        return "Power"
    return "Default"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("target")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--decap-mm", type=float, default=3.0, help="max IC-pin to capacitor distance")
    ap.add_argument("--power-width", type=float, default=0.3, help="min track width on Power/HV nets (mm)")
    a = ap.parse_args()
    path = find_pcb(a.target)
    board = pcbnew.LoadBoard(str(path))
    items = []

    def add(sev, code, msg, fix=""):
        items.append({"severity": sev, "code": code, "message": msg, "fix": fix})

    fps = list(board.GetFootprints())
    edges = [d for d in board.GetDrawings() if d.GetLayer() == pcbnew.Edge_Cuts]
    if not edges:
        add("WARN", "PCB-OUTLINE", "No board outline on Edge.Cuts yet", "Draw the outline (or import a DXF from mechanical)")
    else:
        bb = board.GetBoardEdgesBoundingBox()
        # an edge connector declared with forge (Rule 13, field Forge_Edge) is meant to sit on the outline
        off = [f.GetReference() for f in fps if not bb.Contains(f.GetBoundingBox(False))
               and f.GetReference()[:1] not in "#" and not f.HasField("Forge_Edge")]
        if off:
            add("WARN", "PCB-OFFBOARD", f"{len(off)} footprints outside the outline: {', '.join(sorted(off)[:15])}"
                + (" …" if len(off) > 15 else ""), "Place them on the board (or they are still in the F8 import pile)")

    # decoupling distance: IC supply pins vs nearest capacitor pad on the same net
    caps = {}
    for f in fps:
        if f.GetReference().startswith("C"):
            for p in f.Pads():
                if p.GetNetname():
                    caps.setdefault(p.GetNetname(), []).append((f.GetReference(), p.GetPosition()))
    for f in fps:
        ref = f.GetReference()
        if not ref.startswith(("U", "IC")) or not edges:
            continue
        for p in f.Pads():
            net = p.GetNetname()
            if not net or GROUND.match(net.split("/")[-1]) or p.GetPinType() not in ("power_in", "power_out"):
                continue
            cands = caps.get(net, [])
            if not cands:
                continue  # sch-check reports missing decoupling
            d, cref = min((math.dist((mm(p.GetPosition().x), mm(p.GetPosition().y)),
                                     (mm(q.x), mm(q.y))), c) for c, q in cands)
            if d > a.decap_mm:
                add("WARN", "PCB-DECAP", f"{ref} pin {p.GetNumber()} ({p.GetPinFunction() or net}) — nearest cap {cref} is {d:.1f} mm away",
                    f"Move {cref} within ~{a.decap_mm:g} mm, short fat connection to the pin and a GND via at the cap")

    # thermal / exposed pads without vias
    vias = [t for t in board.GetTracks() if t.GetClass() == "PCB_VIA"]
    for f in fps:
        if not f.GetReference().startswith(("U", "IC", "Q", "D")):
            continue
        smd = [p for p in f.Pads() if p.GetAttribute() == pcbnew.PAD_ATTRIB_SMD]
        if len(smd) < 3:
            continue
        areas = sorted(((mm(p.GetSize().x) * mm(p.GetSize().y), p) for p in smd), key=lambda x: x[0])
        big_area, big = areas[-1]
        typical = areas[len(areas) // 2][0]
        if big_area < 2.0 or big_area < 4 * typical or not big.GetNetname():
            continue
        inside = [v for v in vias if big.HitTest(v.GetPosition())]
        inside += [p for p in f.Pads() if p.GetAttribute() == pcbnew.PAD_ATTRIB_PTH and big.HitTest(p.GetPosition())]
        if not inside:
            add("WARN", "PCB-THERMAL", f"{f.GetReference()} exposed pad {big.GetNumber()} ({big.GetNetname()}, "
                f"{big_area:.1f} mm²) has no vias", "Add a via array (e.g. 0.3 mm drill, 1.0–1.2 mm pitch) to the inner/bottom plane")

    # supply / HV track widths
    thin = {}
    for t in board.GetTracks():
        if t.GetClass() != "PCB_TRACK" or not t.GetNetname():
            continue
        cls = netclass_of(board, t.GetNetname())
        if cls in ("Power", "HV") and mm(t.GetWidth()) < a.power_width - 1e-6:
            thin.setdefault(t.GetNetname(), set()).add(round(mm(t.GetWidth()), 3))
    for net, ws in sorted(thin.items()):
        add("WARN", "PCB-PWRTRACE", f"'{net}' has tracks of {', '.join(f'{w:g}' for w in sorted(ws))} mm",
            f"Widen to ≥ {a.power_width:g} mm (or check current: IPC-2152), or use a zone")

    # fiducials for assembly
    smd_sides = {f.GetLayer() for f in fps if any(p.GetAttribute() == pcbnew.PAD_ATTRIB_SMD for p in f.Pads())
                 and not f.GetReference().startswith(("FID", "#"))}
    for side, name in ((pcbnew.F_Cu, "top"), (pcbnew.B_Cu, "bottom")):
        if side in smd_sides and edges:
            n = sum(1 for f in fps if f.GetLayer() == side and
                    ("fiducial" in str(f.GetFPID().GetLibItemName()).lower() or f.GetReference().startswith("FID")))
            if n < 2:
                add("INFO", "PCB-FID", f"{n} fiducial(s) on {name} side with SMD parts",
                    "Add 2–3 fiducials (MC_Parts → Mechanical) for machine assembly, unless panel fiducials are used")

    if a.json:
        print(json.dumps({"board": str(path), "footprints": len(fps), "items": items}, indent=1))
    else:
        print(f"{path.name}: {len(fps)} footprints")
        for i in items:
            print(f"{i['severity']:5} {i['code']:13} {i['message']}")
            if i["fix"]:
                print(f"{'':19} → {i['fix']}")
        print(f"{sum(i['severity'] == 'ERROR' for i in items)} errors, {sum(i['severity'] == 'WARN' for i in items)} warnings")
    return 1 if any(i["severity"] == "ERROR" for i in items) else 0


if __name__ == "__main__":
    sys.exit(main())
