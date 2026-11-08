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
   **Rule 11:** for every critical part (converter IC, driver, isolator, switch) copy its datasheet "Layout
   Guidelines" and "Layout Example" into the spec as numbered rules (datasheet revision, page, section), and the
   recommended land pattern and copper weight. Placement and routing follow them strictly; a deviation needs a
   reason and the owner's OK, written next to the rule.
   **Rule 12 (EMI, heat, shielding):** the spec has rows for outer-layer GND fill, the solid reference plane,
   stitching pitch, hot-loop and switch-node limits, sensitive routing and thermal copper; `forge pcb copper`
   must pass before a routing review.
   **Rule 13 (edge connectors):** read each connector's drawing for the pin-to-face distance and the mated plug
   envelope; declare it in placement.json; `forge pcb connectors` must pass.
   **Rule 14 (orthogonal signal layers):** declare `[pcb] layer_direction` before routing; `forge pcb crosstalk`
   must pass. Every review grades R-11..R-14; gates G7 / G8 run the Rule 12–14 checks themselves.
   **Rule 15 (high-power rule book):** before placement set `[hp]` (profile) and fill the layout spec's
   before-placement checklist (T_Layout_Spec 2a); `forge hp` before every review; waivers only in `[hp.waive]`.
2. Encode what can be encoded: net classes and custom rules in the `.kicad_dru` (the template has a starting
   set), board setup from the fab's capabilities (`kicad-happy:jlcpcb` / `pcbway`).
   Start the spec from `90_Templates/T_Layout_Spec.md`. Board set-up from project.toml `[pcb]` with
   `forge pcb setup` (outline, holes, stack-up copper, minimum hole). A first placement can be written as data in
   `08_PCB/<board>/placement.json` and applied with `forge pcb place` (it reports courtyard overlaps); the owner
   refines it in KiCad.
   Get the parts onto the board with `forge pcb sync` (headless "Update PCB from Schematic"; close the board in
   KiCad first). It must end with schematic parity 0; mark board-only parts (holes, logos) "not in schematic".
3. **Independent PCB review, always, against the rule set:** at every milestone below, `forge pcb review-new
   <milestone>` then the `pcb-reviewer` agent fills a verdict and evidence for every L-rule. `forge pcb
   review-status` must be PASS (every rule answered, none FAIL, board unchanged since) before G7 / G8.
4. **Placement milestone**: `pcb-reviewer` (placement, loops, zones) + `kicad-happy:emc` risk pass. The owner can
   run `kicad-copilot` to get checks on every save.
5. **Routing milestone**: DRC with schematic parity (0 errors), `$MC_LIB/tools/pcb_check.py`, `pcb-reviewer`,
   `pe-reviewer` (creepage, current, thermal vs the layout spec), `pe-compliance` for insulation.
6. **DFT** with `pe-validation`: test points for every rail and key signal, probe access for the bench tests.
7. Gate G7.

## Changing design files safely

Wrap every change that touches more than one design file in `forge txn begin "<what>"` / `forge txn verify` /
`forge txn commit` (or `close`), and use `forge txn rollback` instead of reverting by hand (see `pe-run` §4).
