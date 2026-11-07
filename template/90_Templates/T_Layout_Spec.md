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

## 3. Critical-part layout rules (project Rule 11: the datasheet, strictly)
Every rule of this spec has an L-number: the independent PCB review checks each one (`forge pcb rules`).
One row per item of the datasheet's "Layout Guidelines" and "Layout Example", and its recommended land pattern.
| # | Rule | Datasheet (rev, page, section) | Deviation (reason, owner OK) |
|---|---|---|---|
| L-07 | <input capacitor directly at VIN / GND pins, minimum loop> | <…> | none |

## 4. Placement
`08_PCB/<board>/placement.json` (forge pcb place). Each critical position names the L-rule it follows.

## 5. Not verified

## Revision history
| Rev | Date | Change |
|---|---|---|
| 0.1 | <date> | Created |
