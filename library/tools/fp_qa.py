#!/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3
"""Footprint QA with KiCad's own DRC: place every MC footprint on a scratch board and report
footprint-level defects (malformed courtyards, pad/hole problems, ...).

Run with KiCad's bundled Python (the shebang does this):  tools/fp_qa.py [LIB ...]
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pcbnew

ROOT = Path(__file__).resolve().parent.parent
CLI = "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli"
# Violations caused by the scratch board having no nets or overlapping neighbours, not by the footprint.
IGNORE = {"clearance", "solder_mask_bridge", "courtyards_overlap", "shorting_items",
          "unresolved_variable", "lib_footprint_issues", "lib_footprint_mismatch", "silk_overlap",
          "silk_over_copper", "silk_edge_clearance", "text_height", "text_thickness", "copper_edge_clearance",
          "hole_clearance", "hole_to_hole", "drill_out_of_range", "via_diameter", "annular_width"}


def library_loads(lib):
    """KiCad's library loader rejects a whole library if any footprint is unacceptable to it."""
    with tempfile.TemporaryDirectory() as td:
        r = subprocess.run([CLI, "fp", "upgrade", "-o", f"{td}/o.pretty", str(ROOT / "footprints" / f"{lib}.pretty")],
                           capture_output=True, text=True)
        return r.returncode == 0


def main(libs):
    libs = libs or sorted(p.stem for p in (ROOT / "footprints").glob("*.pretty"))
    dead = [lib for lib in libs if not library_loads(lib)]
    for lib in dead:
        print(f"{lib}: LIBRARY DOES NOT LOAD IN KICAD (run `mclib.py fix-fp {lib}`, then re-check)")
    b = pcbnew.BOARD()
    placed, x, y = {}, 0, 0
    for lib in libs:
        for f in sorted((ROOT / "footprints" / f"{lib}.pretty").glob("*.kicad_mod")):
            fp = pcbnew.FootprintLoad(str(f.parent), f.stem)
            ref = f"FP{len(placed) + 1}"
            fp.SetReference(ref)
            fp.SetPosition(pcbnew.VECTOR2I_MM(x, y))
            b.Add(fp)
            placed[ref] = f"{lib}:{f.stem}"
            x += 60
            if x > 1200:
                x, y = 0, y + 60
    with tempfile.TemporaryDirectory() as td:
        pcb, rpt = Path(td) / "qa.kicad_pcb", Path(td) / "qa.json"
        pcbnew.SaveBoard(str(pcb), b)
        subprocess.run([CLI, "pcb", "drc", "--format", "json", "-o", str(rpt), str(pcb)],
                       capture_output=True)
        report = json.loads(rpt.read_text())
    bad = {}
    for v in report["violations"]:
        if v["type"] in IGNORE:
            continue
        for item in v["items"]:
            for ref, name in placed.items():
                if f" {ref} " in item["description"] + " " or item["description"].endswith(ref):
                    bad.setdefault(name, set()).add(v["type"])
    for name in sorted(bad):
        print(f"{name}: {', '.join(sorted(bad[name]))}")
    print(f"{len(placed)} footprints checked, {len(bad)} with DRC defects, {len(dead)} libraries failing to load")
    return 1 if bad or dead else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
