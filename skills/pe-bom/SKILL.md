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
