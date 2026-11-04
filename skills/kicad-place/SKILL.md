---
name: kicad-place
description: Place MC_Parts components (unwired) on a KiCad 10 schematic sheet, laid out from a reference schematic figure and a sheet brief, so the user can draw straight wires. Use when the user says "place the parts for X", "drop the BoM parts for U4 on the sheet", "place as you did for the last one", "arrange them so wires are easy", or asks to pre-place a block from a design doc. Never wires, never adds labels.
---

# KiCad pre-placement (unwired)

In draw mode `user` the user draws every wire (project CLAUDE.md Rule 6). This skill only **places symbols** from MC_Parts on a
sheet, positioned like the reference figure so each wire is a short straight run. It is the one explicit exception
to "Claude does not edit .kicad_sch": do it only when the user asks for placement.

Tool: `$PE_FORGE/skills/kicad-place/scripts/sch_place.py` (stdlib + PyMuPDF for the PNG).

```sh
T=$PE_FORGE/skills/kicad-place/scripts/sch_place.py
python3 $T <sheet> --pins <MC_ID> <x> <y> <rot>     # where an IC's pins land (sheet coords, y down)
python3 $T <sheet> <spec.json>                      # dry run: checks + render to $TMPDIR/sch_place/
python3 $T <sheet> <spec.json> --apply              # write (needs a clean dry run of the same sheet first)
```

## Procedure

1. **Inputs.** Read the block's brief (pin table + values table) and the reference figure
   (the module's design booklet figures; look at it). Every part comes from MC_Lib. Find the IDs with
   `sqlite3 $MC_LIB/database/mc_parts.sqlite "select ID,MPN,Value,Symbols from <Table> where MPN in (...)"`.
   If a part is missing, stop and add it with `part-librarian`. Never put a raw MPN on the sheet.
2. **Read the sheet** (lib_ids, refs, positions, paper size). Pick the anchor below / beside existing blocks, as
   asked. Continue the sheet's refdes block (e.g. 2xx for module 2): next free numbers, no reuse.
3. **Anchor the IC.** `--pins` gives its pin coordinates. Then lay the passives out by these rules:
   - all coordinates on the **1.27 mm grid**; MC_Generic R / C / L have pins at ±3.81 mm (vertical at rot 0,
     horizontal at rot 90);
   - **one pin of each passive lines up with the IC pin or the rail it connects to** (same x or y), so the wire is
     a straight segment: input caps hang from the VIN rail, the BST cap spans BST→SW, the FB divider top sits on
     the output line and its tap at the FB-pin row, output caps in a row with their tops on the output line,
     inductors horizontal on the SW row;
   - follow the reference figure's left-to-right order (input, IC, switch node, inductor, divider, CFF, output caps);
     reuse the x columns of blocks already on the sheet (e.g. divider at x 133.35, CFF 146.05, COUT 158.75 + 12.7 n),
     so the blocks stack neatly;
   - DNP parts get `"dnp": true` (brief marks them DNP);
   - label side: `side` R / L / B (rot 0), U / A / D (rot 90). Keep ≥ ~9 mm between neighbouring vertical
     parts whose labels face each other.
4. **Write the spec** to `<project>/.copilot/placements/<sheet>_<ref>_<block>.json`, with a `note` per part
   naming the brief designator (C27, R11 …) and a top-level `reference` naming the figure and brief sections.
   Spec files are the record of what was placed and can be re-used for the next similar block.
5. **Dry run** and **look at the render** (crop the new area at 3–4× with PyMuPDF from
   `$TMPDIR/sch_place/svg/<sheet>.svg`). Fix every overlapping label or pin and re-run until the checks say OK
   and the picture is clean.
6. **Apply** only when the schematic editor is closed (the tool refuses on a `~*.kicad_sch.lck`; the project
   manager's `.kicad_pro.lck` is fine). If it's open, ask the user to close the schematic editor (save first), then
   re-run the dry run (the sheet may have changed) and apply.
7. **Verify**: `kicad-cli sch erc` on the root sheet (new items should only be unconnected pins of the new parts),
   `sch-check .` (no LIB-NOTDB), and the netlist shows the new refs. Report: parts placed (ref → brief designator,
   value), where, the spec path, the backup path, and any brief-vs-sheet issues spotted (do not fix them; tell
   the user). Do not commit unless asked.

## What the tool guarantees
- The lib symbol is embedded as `MC_Parts:<Table>/<ID>` with all DB fields (same as a GUI placement from the
  database library), from MC_Lib symbols or KiCad's stock symbol libraries.
- Refs are new, every pin is on the grid, and **no new pin touches any existing or new pin** (that would create a
  connection the user didn't draw).
- The hierarchy instance path is copied from the sheet's existing symbols (sheet needs at least one placed part).
- `--apply` writes only if the sheet still matches the dry run, and keeps a backup in `$TMPDIR/sch_place/`.

## Known gotchas
- Field angle on rot-90 symbols must be 90 to read horizontal (handled).
- Symbols that `extend` another symbol are refused; flatten them in MC_Lib first.
- After applying, KiCad may show "Update symbols from library" differences only if MC_Lib changed later; that is
  the normal LIB-STALE flow.
