#!/usr/bin/env python3
"""bom - build the BOM from the schematic netlist + MC_Parts database (the DB is authoritative).

  bom.py <project dir | .kicad_pro | .kicad_sch> [-o OUTDIR] [--boards N]

Writes to OUTDIR (default <project>/release/bom):
  <name>-BOM.csv          grouped by MC_ID: refs, qty per board, qty for N boards, MPN, manufacturer,
                          DigiKey/Mouser PNs, status; DNP parts listed at the end
  <name>-digikey.csv      upload to DigiKey "BOM Manager" (Quantity, Digi-Key Part Number / MPN, Reference)
  <name>-mouser.csv       upload to Mouser "BOM Tool" (Mouser Part Number / MPN, Quantity, Reference)
Prints parts that can't be ordered (no MPN, not from MC_Parts, unreviewed status).
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
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
        lines.append({"Refs": ",".join(refs), "Qty": len(refs), f"Qty x{a.boards}": len(refs) * a.boards,
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

    n = sum(l["Qty"] for l in lines)
    print(f"{name}: {len(lines)} line items, {n} placements/board, {len(dnp)} DNP → {out}")
    if problems:
        print("Not ready to order:\n  " + "\n  ".join(problems))
    return 0


if __name__ == "__main__":
    sys.exit(main())
