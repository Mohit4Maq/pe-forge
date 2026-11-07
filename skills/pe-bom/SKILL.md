---
name: pe-bom
description: Phase P4 of a pe-forge project - size every component from the chosen circuit, pick parts with the owner by multiple choice, find an alternate for each, record price / stock / MOQ, and build the tentative BoM and cost roll-up. Use when the supervisor reaches P4, or the user asks to size components, pick parts, find alternates, or build or check a BoM.
---

# pe-bom: tentative BoM and sizing (P4)

1. **Sizing first** (`pe-hw`): `Design_Calculations.md` (module: `modules/M-xx/Design_Calculations.md`) with
   each value's equation, inputs at the worst corner, result, and the Rule 1 anchor. Typical set: device V / I
   stress and losses, magnetics (L, I_pk, I_rms, ripple, core and copper loss, ΔT), capacitors (ripple
   current, voltage, ESR loss, lifetime at temperature), gate drive (Q_g, drive current, bias power), sensing
   (shunt / sensor range, error budget), protection (fuse I²t, TVS / MOV energy), thermal (R_th chain to
   ambient). pwrlab tools: `losses`, `dc_link_capacitor`, `select_devices`, `tolerance`.
2. **Candidates per key part** (`pe-supply`): 2–4 MPNs from the library (`$MC_LIB/tools/mclib.py find`) and
   distributors (Mouser via `$MC_LIB/tools/mouser.py`, DigiKey / LCSC / element14 skills). Stock, price at 1 and
   at the volume break, MOQ, lead time, lifecycle. Zero stock at one distributor is never the answer: check the
   others before rejecting.
3. **Owner picks by multiple choice** for the key parts (switches, controller / driver, magnetics, bulk caps,
   connectors, MCU): each option shows margin at the worst corner, loss, price, stock; recommended first.
   Passives and routine parts: pick yourself and state the choice.
4. **Alternate for every line** (prefer an exact drop-in from another manufacturer); write `Alt match` as
   EXACT or FUNCTIONAL + what differs. Custom magnetics: performance spec + a second vendor or core source.
5. **BoM**: `04_Hardware/BOM/WIP_BOM.csv` (module: `modules/M-xx/BOM.csv`, same columns) and the `.md` view
   with the cost roll-up vs target. `forge bom check` must PASS (no line missing price, stock or alternate).
6. **Library**: every chosen part exists in MC_Lib with a verified symbol and footprint (`part-librarian`);
   this is checked again in P6 before drawing.
7. Gate G4 with `pe-reviewer` and `pe-reliability` (derating, lifetime at the 60 °C or project ambient).

## Changing design files safely

Wrap every change that touches more than one design file in `forge txn begin "<what>"` / `forge txn verify` /
`forge txn commit` (or `close`), and use `forge txn rollback` instead of reverting by hand (see `pe-run` §4).

## Datasheet evidence (cache, cite, verify)
1. **Every part you create gets its datasheet.** Download the latest datasheet from the manufacturer, then create
   the part with it: `mclib.py add <Table> MPN=... --datasheet <file.pdf> --datasheet-url <where it came from>` (or
   `mclib.py import ... --table T --datasheet <file.pdf>`; a vendor zip that ships one PDF uses it). For a series
   that shares one datasheet, cache it once (`datasheets.py add <file.pdf> --mpn "<series>"`) and pass
   `--datasheet-id <id>`. The part's Datasheet field then opens the cached file from KiCad. Creation without a
   datasheet is refused.
2. After checking the symbol pins and the footprint land pattern against it, record the evidence and promote the
   part in one step: `datasheets.py verify <MC_ID> <id> --page <N> --section "<table or figure>" --pads <count>
   --pitch <mm> --activate`. For a whole series: `datasheets.py verify-family <id> --match "RC0603*" --footprint
   <LIB:FP> --page N --section "..." --pads N --pitch MM --activate` (preview, then `--apply`; every part's own
   footprint is checked against the pad count and pitch, mismatches are skipped and listed).
3. Per board: `forge evidence todo` lists the placed parts still missing a datasheet, a check or Status Active,
   grouped by family, with the measured pads and the exact commands. `forge evidence sync` copies every placed
   part's datasheet into the project's `04_Hardware/Datasheets/` with `Datasheets_Index.md`, so reviewers and
   agents read the exact file the part was checked against.
4. `forge check` (parts_evidence) warns on a part without a cached datasheet, a check or a project copy, and fails
   on a pointer to a missing or changed file. The release gate (G8) fails any part that is not Active with recorded
   evidence; status is read from the parts database, not from the copy in the placed symbol.
Datasheets are copyrighted: the cache stays in the private library (Git LFS) and project folders, never in a
public repo.
