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

## 2a. High-power profile and before-placement checklist (project Rule 15; rule book section 14)
| # | Item | Value | Rule |
|---|---|---|---|
| L-HP1 | Safety basis: product standard, PD, OVC, altitude, insulation types, laminate CTI | <...> (also project.toml [insulation]) | HV-01 |
| L-HP2 | Stack-up: dielectric per layer pair, copper weights, laminate, hipot | <...> | STK-01/02/09 |
| L-HP3 | Loop inductance budget and overshoot per switching cell | V_bus + L·di/dt ≤ 0.8 V(BR): <...> ([hp.budget]) | PL-06 |
| L-HP4 | Ground domains and star point | <PGND / SGND / driver refs / PE; star at ...> | GND-01/02 |
| L-HP5 | Heatsink connection | <PE / DC− / none> | SW-06 |
| L-HP6 | Thermal budget per hot part (allowed PCB °C/W) | <...> | TH-01 |
| L-HP7 | Hot / warm / cool zones on the placement plan | <...> | TX-01 |
| L-HP8 | Conductor per path > 30 A | <layers / heavy Cu / strips / busbar> | HC table |

## 2b. EMI, heat and shielding (project Rule 12)
| # | Rule | Value | Source |
|---|---|---|---|
| L-08 | Outer-layer GND fill | every unused area of top and bottom poured with GND (`forge pcb copper` coverage ≥ <x> %) | <datasheet thermal / layout section> |
| L-09 | Reference plane | L2 solid GND, one piece, ≥ <x> % of the board, no slots under fast nets | <datasheet> |
| L-10 | Stitching | GND via grid ≤ <pitch> mm; vias at every cap ground; edge row | Rule 12 |
| L-11b | Thermal relief | all pads join pours via thermal relief (gap <x> mm, spokes <x> mm); solid only: <part.pad, datasheet ref> | Rule 12 |
| L-11 | Switch node / hot loop | <max area>; no SW copper on quiet layers | <datasheet layout guidelines> |

## 2c. Edge connectors and terminals (project Rule 13)
| # | Connector | Edge | Pin-1 row to mating face | Mated envelope (length × height, underside, tool access) | Source |
|---|---|---|---|---|---|
| L-13a | <J1> | <left> | <x mm> → flush | <plug 22 × 15 mm, underside at board surface; screws from top> | <datasheet, page> |

## 2d. Signal layer directions (project Rule 14)
| # | Layer | Role | Direction | Note |
|---|---|---|---|---|
| L-14a | L1 | parts, short signals | <H> | fan-out stubs exempt |
| L-14b | L2 | solid GND plane | — | shields L1 from L3 |
| L-14c | L3 | signals | <H> | |
| L-14d | L4 | signals | <V> | orthogonal to L3 (adjacent, no plane between) |

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
