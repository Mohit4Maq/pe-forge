#!/usr/bin/env python3
"""bom - build the BOM from the schematic netlist + MC_Parts database (the DB is authoritative).

  bom.py <project dir | .kicad_pro | .kicad_sch> [-o OUTDIR] [--boards N] [--spares N]
         [--jlcpcb [--pos FILE] [--allow-missing]]

Writes to OUTDIR (default <project>/release/bom):
  <name>-BOM.csv          grouped by MC_ID: refs, qty per board, qty for N boards, MPN, manufacturer,
                          DigiKey/Mouser PNs, status; DNP parts listed at the end
  <name>-digikey.csv      upload to DigiKey "BOM Manager" (Quantity, Digi-Key Part Number / MPN, Reference)
  <name>-mouser.csv       upload to Mouser "BOM Tool" (Mouser Part Number / MPN, Quantity, Reference)
  with --jlcpcb:
  <name>-jlcpcb-bom.csv   Comment, Designator, Footprint, LCSC Part # (one line per value / footprint / LCSC group)
  <name>-jlcpcb-cpl.csv   Designator, Mid X, Mid Y, Layer, Rotation (from `kicad-cli pcb export pos`, or --pos FILE,
                          with each part's JLC_Rotation offset added). Parts without an LCSC number are listed on
                          stderr and the command exits 1, unless --allow-missing.
--spares N: DigiKey / Mouser quantities become qty x boards + spares; passives (R, C, L, FB) get
max(N, 10 % rounded up). JLCPCB files place exactly what is on the board and ignore it.
Prints parts that can't be ordered (no MPN, not from MC_Parts, unreviewed status).
"""
from __future__ import annotations

import argparse
import csv
import math
import re
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kinet  # noqa: E402
from sch_check import db_rows, is_rail  # noqa: E402


def refkey(r):
    m = re.match(r"([A-Za-z#]+)(\d*)", r)
    return (m.group(1), int(m.group(2) or 0))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("target")
    ap.add_argument("-o", "--out")
    ap.add_argument("--boards", type=int, default=1, help="build quantity (for order quantities)")
    ap.add_argument("--spares", type=int, default=0, help="extra parts per line for DigiKey / Mouser orders")
    ap.add_argument("--jlcpcb", action="store_true", help="also write JLCPCB BoM + CPL")
    ap.add_argument("--pos", help="use this pick-and-place CSV (kicad-cli pcb export pos --format csv --units mm)")
    ap.add_argument("--allow-missing", action="store_true", help="JLCPCB: warn instead of fail on missing LCSC numbers")
    a = ap.parse_args()
    sch = kinet.find_sch(a.target)
    comps, _ = kinet.load(sch)
    db = db_rows()
    out = Path(a.out) if a.out else sch.parent / "release" / "bom"
    out.mkdir(parents=True, exist_ok=True)

    groups, dnp, problems = defaultdict(list), [], []
    for ref, c in comps.items():
        if is_rail(c) or not c.in_bom:
            continue
        if c.dnp:
            dnp.append(c)
            continue
        pid = c.fields.get("MC_ID", "")
        row = db.get(pid)
        if row:
            key = pid
            if row["Status"] in ("Seed", "Legacy", "NRND", "Obsolete"):
                problems.append(f"{ref}: {pid} {row['MPN']} status {row['Status']}")
        else:
            key = c.fields.get("MPN") or f"?{c.value}|{c.footprint}"
            problems.append(f"{ref}: not from MC_Parts" + ("" if c.fields.get("MPN") else ", no MPN"))
        groups[key].append((c, row))

    cols = ["Item", "Refs", "Qty", f"Qty x{a.boards}", "MC_ID", "Value", "MPN", "Manufacturer", "Description",
            "Package", "Footprint", "DigiKey_PN", "Mouser_PN", "Alt_MPN", "Status"]
    lines = []
    for key, members in groups.items():
        c, row = members[0]
        refs = sorted((m[0].ref for m in members), key=refkey)
        src = row or {"MPN": c.fields.get("MPN", ""), "Manufacturer": c.fields.get("Manufacturer", ""),
                      "Description": c.fields.get("Description", ""), "Package": "", "DigiKey_PN": c.fields.get("DigiKey_PN", ""),
                      "Mouser_PN": c.fields.get("Mouser_PN", ""), "Alt_MPN": "", "Status": "not in DB"}
        lines.append({"Refs": ",".join(refs), "Qty": len(refs), f"Qty x{a.boards}": order_qty(refs, a.boards, a.spares),
                      "MC_ID": key if row else "", "Value": c.value, "Footprint": c.footprint,
                      **{k: src[k] for k in ("MPN", "Manufacturer", "Description", "Package", "DigiKey_PN",
                                             "Mouser_PN", "Alt_MPN", "Status")}})
    lines.sort(key=lambda l: refkey(l["Refs"].split(",")[0]))
    name = sch.stem
    with open(out / f"{name}-BOM.csv", "w", newline="") as f:
        w = csv.DictWriter(f, cols)
        w.writeheader()
        for i, l in enumerate(lines, 1):
            w.writerow({"Item": i, **l})
        if dnp:
            w.writerow({})
            w.writerow({"Item": "DNP", "Refs": ",".join(sorted((c.ref for c in dnp), key=refkey)),
                        "Qty": len(dnp), "Description": "Do not populate"})
    with open(out / f"{name}-digikey.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Quantity", "Digi-Key Part Number", "Manufacturer Part Number", "Manufacturer", "Customer Reference"])
        for l in lines:
            w.writerow([l[f"Qty x{a.boards}"], l["DigiKey_PN"], l["MPN"], l["Manufacturer"], l["Refs"]])
    with open(out / f"{name}-mouser.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Mouser Part Number", "Manufacturer Part Number", "Manufacturer", "Quantity", "Customer Part Number"])
        for l in lines:
            w.writerow([l["Mouser_PN"], l["MPN"], l["Manufacturer"], l[f"Qty x{a.boards}"], l["Refs"]])

    rc = 0
    if a.jlcpcb:
        rc = jlcpcb(sch, name, out, groups, a)
    n = sum(l["Qty"] for l in lines)
    print(f"{name}: {len(lines)} line items, {n} placements/board, {len(dnp)} DNP → {out}")
    if problems:
        print("Not ready to order:\n  " + "\n  ".join(problems))
    return rc


PASSIVE = re.compile(r"^(R|C|L|FB|RN|CN)\d", re.I)


def order_qty(refs, boards, spares):
    """qty x boards + spares; passives get at least 10 % extra (they get lost in assembly)."""
    q = len(refs) * boards
    if not spares:
        return q
    if refs and PASSIVE.match(refs[0]):
        return q + max(spares, math.ceil(q * 0.10))
    return q + spares


def lcsc_of(c, row):
    return ((row or {}).get("LCSC_PN") or c.fields.get("LCSC_PN") or c.fields.get("LCSC") or "").strip()


def rot_of(c, row):
    v = ((row or {}).get("JLC_Rotation") or c.fields.get("JLC_Rotation") or "").strip()
    try:
        return float(v) if v else 0.0
    except ValueError:
        return 0.0


def pos_rows(sch, a):
    """Rows of a KiCad pick-and-place CSV (Ref, Val, Package, PosX, PosY, Rot, Side)."""
    if a.pos:
        return list(csv.DictReader(open(a.pos, newline="")))
    pcb = sch.with_suffix(".kicad_pcb")
    if not pcb.exists():
        raise SystemExit(f"--jlcpcb needs the board {pcb.name} (or --pos FILE)")
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "pos.csv"
        subprocess.run([kinet.CLI, "pcb", "export", "pos", "--format", "csv", "--units", "mm", "--side", "both",
                        "--exclude-dnp", "-o", str(f), str(pcb)], capture_output=True)
        if not f.exists():
            raise SystemExit("kicad-cli pcb export pos produced no file")
        return list(csv.DictReader(open(f, newline="")))


def jlcpcb(sch, name, out, groups, a):
    if a.spares:
        print("note: --spares does not apply to JLCPCB files (they place exactly what is on the board)")
    by_ref, missing, jgroups = {}, [], {}
    for members in groups.values():
        for c, row in members:
            by_ref[c.ref] = (c, row)
            lc = lcsc_of(c, row)
            if not lc:
                missing.append(f"{c.ref} ({c.value}, {(row or {}).get('MPN') or c.fields.get('MPN', '')})")
            fp = c.footprint.split(":")[-1]
            jgroups.setdefault((c.value, fp, lc), []).append(c.ref)
    with open(out / f"{name}-jlcpcb-bom.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Comment", "Designator", "Footprint", "LCSC Part #"])
        for (val, fp, lc), refs in sorted(jgroups.items(), key=lambda kv: refkey(sorted(kv[1], key=refkey)[0])):
            w.writerow([val, ",".join(sorted(refs, key=refkey)), fp, lc])
    n = 0
    with open(out / f"{name}-jlcpcb-cpl.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Designator", "Mid X", "Mid Y", "Layer", "Rotation"])
        for r in pos_rows(sch, a):
            ref = (r.get("Ref") or "").strip()
            if ref not in by_ref:          # DNP, excluded from the BoM, or not a schematic part
                continue
            c, row = by_ref[ref]
            rot = (float(r.get("Rot") or 0) + rot_of(c, row)) % 360
            side = "Bottom" if (r.get("Side") or "").strip().lower() == "bottom" else "Top"
            w.writerow([ref, f"{float(r['PosX']):.4f}mm", f"{float(r['PosY']):.4f}mm", side, f"{rot:g}"])
            n += 1
    print(f"JLCPCB: {len(jgroups)} BoM lines, {n} placements → {out}")
    if missing:
        print("No LCSC part number (set LCSC_PN in MC_Parts: mclib.py set <ID> LCSC_PN=Cxxxx):\n  "
              + "\n  ".join(missing), file=sys.stderr)
        if not a.allow_missing:
            return 1
        print(f"warning: {len(missing)} part(s) without an LCSC number (--allow-missing)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
