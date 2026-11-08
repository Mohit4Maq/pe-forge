---
type: layout-spec
rev: 0.1
status: draft
feeds: [08_PCB/<board>/<board>.kicad_pcb, 08_PCB/<board>/<board>.kicad_dru, 08_PCB/<board>/placement.json]
---
# <Project>: layout specification

## 1. Board
| # | Item | Value | Source |
|---|---|---|---|
| L-01 | Outline | <W × H mm> | <SYS-xxx> |
| L-02 | Stack-up | <layers, thickness, copper oz per layer> (project.toml [pcb]) | <SYS-xxx; IC datasheet layout section> |
| L-03 | Layer use | <L1 parts and power · L2 solid GND · …> | <datasheet: ground plane on a middle layer> |
| L-04 | Mounting | <holes, inset, plated or not> | <SYS-xxx> |

## 2. Spacing and copper
| # | Rule | Value | Source |
|---|---|---|---|
| L-05 | <net class> to <net class> | <clearance / creepage mm> | <standard clause, pollution degree, material group> |
| L-06 | Power tracks | <width or pour> | <current, ΔT, IPC-2221B / 2152> |

## 2b. EMI, heat and shielding (project Rule 12)
| # | Rule | Value | Source |
|---|---|---|---|
| L-08 | Outer-layer GND fill | every unused area of top and bottom poured with GND (`forge pcb copper` coverage ≥ <x> %) | <datasheet thermal / layout section> |
| L-09 | Reference plane | L2 solid GND, one piece, ≥ <x> % of the board, no slots under fast nets | <datasheet> |
| L-10 | Stitching | GND via grid ≤ <pitch> mm; vias at every cap ground; edge row | Rule 12 |
| L-11b | Thermal relief | all pads join pours via thermal relief (gap <x> mm, spokes <x> mm); solid only: <part.pad, datasheet ref> | Rule 12 |
| L-11 | Switch node / hot loop | <max area>; no SW copper on quiet layers | <datasheet layout guidelines> |

## 3. Critical-part layout rules (project Rule 11: the datasheet, strictly)
Every rule of this spec has an L-number: the independent PCB review checks each one (`forge pcb rules`).
One row per item of the datasheet's "Layout Guidelines" and "Layout Example", and its recommended land pattern.
| # | Rule | Datasheet (rev, page, section) | Deviation (reason, owner OK) |
|---|---|---|---|
| L-12 | <input capacitor directly at VIN / GND pins, minimum loop> | <…> | none |

## 4. Placement
`08_PCB/<board>/placement.json` (forge pcb place). Each critical position names the L-rule it follows.

## 5. Not verified

## Revision history
| Rev | Date | Change |
|---|---|---|
| 0.1 | <date> | Created |
