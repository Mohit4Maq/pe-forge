---
type: sheet-brief
sheet: S-xx
module: M-xx
kicad_sheet: 08_PCB/<project>/<sheet>.kicad_sch
draw_mode: user | agent
rev: 0.1
status: draft
---
# S-xx <sheet name>: sheet brief

## Parts (all from MC_Lib, verified in the booklet §4)
| Ref | MC_Lib ID | Value | MPN | Footprint | DNP |
|---|---|---|---|---|---|

## Netlist (the contract: the drawing must match this exactly)
| Net | Class (power / hv / signal / gnd) | Pins (Ref.pin) | Notes |
|---|---|---|---|

## Sheet interfaces (labels to other sheets)
| Net | Direction | Other sheet |
|---|---|---|

## Layout of the drawing
- Paper size, title.
- Flow, left to right: input → … → output.
- Zones: HV / LV, isolation barrier position.
- Groups: which parts sit together (decoupling next to its IC pin, etc.).
