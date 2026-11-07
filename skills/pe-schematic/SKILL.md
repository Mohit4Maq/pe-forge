---
name: pe-schematic
description: Phase P6 of a pe-forge project - design booklet, datasheet-verified symbols and footprints, the sheet brief and netlist, then the KiCad sheet drawn either by the user (agents support) or by the pe-schematic-drafter agent in a strict readable style, checked by forge schdraw, ERC and reviewers. Use when the supervisor reaches P6, or the user asks to draw, place, check or review a schematic sheet.
---

# pe-schematic: from booklet to a verified sheet (P6)

One module = one sheet (large modules may use 2–3 sheets under one hierarchical block).

## Step 1: design booklet (`modules/M-xx/Design_Booklet.md`, template `T_Design_Booklet.md`)
Everything is decided here, **before anyone draws**: function and allocated requirements, circuit description,
part decisions (from P4), values with calculations and benchmarks, stress vs rating at the worst corner,
protection and single-fault behaviour, layout notes, bench tests.

## Step 2: symbols and footprints, verified independently against the datasheet
For every part on the sheet (`part-librarian` does the library work, `pe-hw` checks):
1. Download the **latest datasheet** (manufacturer site) into its own folder under `04_Hardware/Datasheets/`.
2. **Symbol**: every pin number and name matches the datasheet pinout table, pin electrical types are right
   (power in / out, passive, open collector…), multi-unit split is sensible, hidden pins are none.
3. **Footprint**: pad count, pitch, pad size and the **recommended land pattern** from the datasheet (not a
   generic IPC guess where the maker gives one), exposed pad and thermal vias, pin-1 mark, courtyard,
   creepage slots where required, 3D height. Run `$MC_LIB/tools/fp_qa.py` / `mclib.py lint-fp`.
4. Record each check in booklet §4 (datasheet revision and page). A part that is not verified is not drawn.

## Step 3: sheet brief (`modules/M-xx/Sheet_Brief.md`, template `T_Sheet_Brief.md`)
Parts table (MC_Lib IDs), **netlist table** (net → every Ref.pin; this is the contract), net classes, sheet
interfaces, and the drawing layout: flow left to right, zones, groups. Net names follow the style guide.

## Step 4: draw (mode from `project.toml process.schematic_mode`; `ask` → ask the owner per sheet)
**User draws:** give the owner the brief, offer `kicad-place` to pre-place parts so wires are short and straight,
answer questions while they draw. When they save, run step 5. Changes to their sheet go to them as precise
instructions; never edit their sheet unless they ask.

**Agent draws:** spawn `pe-schematic-drafter` with the booklet, the brief and the sheet path. It writes a drawing
spec, iterates `forge schdraw check` until there are 0 errors and the render looks right, then `forge schdraw
apply`. The owner reviews the rendered PNG and the sheet in KiCad; their edits afterwards are design intent.

Critical parts follow their datasheet's application circuit and values strictly (project Rule 11): the brief
lists each recommendation with its page, and a deviation carries a reason and the owner's OK.

## Step 5: verify the sheet
1. `kicad-cli sch erc` (0 errors), `$MC_LIB/tools/sch_check.py` (library discipline, net naming, decoupling).
2. Netlist vs brief: `forge drift --module M-xx` compares KiCad's own netlist with the brief's netlist table, and
   the BoM, brief and booklet parts tables (value, MPN, MC_ID, footprint, DNP) with the drawn parts. Same contract
   in both modes (agent mode also runs it inside `schdraw check`). Put the sheet path in the brief's frontmatter
   (`kicad_sheet:`) so the module is compared against its own sheet.
3. Power boards with HV nets: `forge insulation` (0 INS-1 / INS-2 / NET-4) and `forge insulation --rules`
   (the `.kicad_dru` enforces every `[[insulation.rule]]`). An INS-1 net gets an `HV_` name; an INS-2 path is a
   wiring error or an isolator missing its MC_Parts `Iso_Rating`. Never silence one with `allow_non_hv` without
   a reason in the comment.
4. Surge protection: `forge protection` (0 PROT-1/2/3). Fill `Abs_Max_V` for every active part on a protected net
   and `Clamp_V` for every TVS from their datasheets, and the system sizing in `[protection]`.
5. `forge schdraw lint <sheet>` (score and findings; advice on a user-drawn sheet, errors on an engine-drawn one),
   then look at the rendered sheet (`kicad-cli sch export svg` → PNG): readability per the style guide.
6. Reviews in parallel: `schematic-reviewer` (datasheet-backed), `power-reviewer` (power stages),
   `pe-reviewer` (cold, against booklet and requirements); `pe-compliance` for insulation, protection, HV.
7. `forge evidence sync` (copies every placed part's datasheet into `04_Hardware/Datasheets/`), then sync the BoM from the schematic (`$MC_LIB/tools/bom.py`) into the module BoM; propagate changes (Rule 4).
8. Gate G6 for the module.

## Changing design files safely

Wrap every change that touches more than one design file in `forge txn begin "<what>"` / `forge txn verify` /
`forge txn commit` (or `close`), and use `forge txn rollback` instead of reverting by hand (see `pe-run` §4).
`forge schdraw apply` does this itself: it opens a transaction, writes the sheet, re-checks the real project
(no new ERC errors on the top sheet, KiCad netlist matches the brief) and rolls back on failure. On success it
closes the transaction without committing; commit under the project's commit rule.

## Design lookup
Use `forge explain` instead of reading `.kicad_sch` s-expressions or netlists by hand: `forge explain U1` (value,
MPN, footprint, every pin -> net, BoM rows, brief / booklet mentions), `forge explain VIN` (every REF.PIN on a net),
`forge explain U1.7` or `U1.VCC` (one pin), `forge explain --pins <MC_ID>` (a library part's pins and units before
you place it). Add `--json` for structured output. It is deterministic and costs no tokens beyond its output.
