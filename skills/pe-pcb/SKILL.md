---
name: pe-pcb
description: Phase P7 of a pe-forge project - layout spec (stack-up, creepage / clearance, current widths, thermal, EMC rules), placement and routing support in KiCad, DRC, and layout reviews. Use when the supervisor reaches P7, or the user asks about stack-up, placement, routing, creepage, DRC or a layout review.
---

# pe-pcb: layout (P7)

The owner (or a layout engineer) places and routes in KiCad; agents write the rules, watch and review.

1. **Layout spec** `04_Hardware/Layout/Layout_Spec.md` (`pe-hw` + `pe-compliance`): board size and mounting,
   stack-up (layers, copper weights, dielectric), creepage / clearance per voltage pair with the standard's
   table and pollution degree / material group, current widths and copper area per net class (with ΔT),
   commutation-loop placement rules, gate-drive loops, Kelvin connections, shunt routing, isolation barrier
   (slots, keep-outs), thermal (device pads, vias, heatsink interface), EMC (filter placement, ground strategy,
   return paths), DFM / assembly (fab house limits, test points, fiducials, panelisation).
2. Encode what can be encoded: net classes and custom rules in the `.kicad_dru` (the template has a starting
   set), board setup from the fab's capabilities (`kicad-happy:jlcpcb` / `pcbway`).
3. **Placement milestone**: `pcb-reviewer` (placement, loops, zones) + `kicad-happy:emc` risk pass. The owner can
   run `kicad-copilot` to get checks on every save.
4. **Routing milestone**: DRC with schematic parity (0 errors), `$MC_LIB/tools/pcb_check.py`, `pcb-reviewer`,
   `pe-reviewer` (creepage, current, thermal vs the layout spec), `pe-compliance` for insulation.
5. **DFT** with `pe-validation`: test points for every rail and key signal, probe access for the bench tests.
6. Gate G7.

## Changing design files safely

Wrap every change that touches more than one design file in `forge txn begin "<what>"` / `forge txn verify` /
`forge txn commit` (or `close`), and use `forge txn rollback` instead of reverting by hand (see `pe-run` §4).
