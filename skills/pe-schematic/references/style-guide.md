# Schematic style guide (strict)

The sheet must read like a careful engineer drew it by hand: a reviewer follows the power path and every signal
without hunting. `forge schdraw check` enforces the rules marked with an ID; the rest the drafter and the
reviewer judge on the rendered sheet.

## Layout
- **Signal flow left to right**: inputs and their connectors on the left, outputs on the right (S11).
  Power flows along one main horizontal rail per voltage; the return / ground is at the bottom.
- **One function per area.** Group each block (input filter, power stage, gate drive, sensing, MCU, aux supply)
  in its own region with a short title text (2.54 mm) above it. Keep ≥ 10 mm between groups.
- **Hazardous-voltage zone**: HV parts and nets inside a marked zone rectangle; LV signal nets never run through
  it; the isolation barrier is a vertical line of space with the isolators straddling it (S10).
- Supplies at the top, ground at the bottom of each group. Decoupling caps sit next to the pin they decouple,
  drawn hanging from the supply line to ground.
- Use A3 for a module; A4 only for small sheets. Leave the right ~25 % free for notes on the first draft.

## Wires and connections
- **S1** everything on the 1.27 mm grid. **S2** wires are horizontal or vertical, never diagonal.
- **S3** a wire ends on a pin; it never runs over a pin it does not connect to.
- **S4** junctions are T-junctions with a dot; no four-way junctions (offset one branch by 2.54 mm).
- **S5** wires never cross a symbol body.
- **S8** few crossings between different nets (≤ 4 per sheet by default); a long run is replaced by a pair of
  net labels.
- **S9** parallel wires of different nets ≥ 2.54 mm apart.
- Pin-to-pin straight runs are best: place parts so the pins line up (same x or y) and the wire is one segment.
- Power and high-current paths drawn as short, straight, obvious lines; the commutation loop components sit
  together as they will on the board.

## Symbols, labels and text
- **S6** ground symbols point down, supply symbols point up, never sideways.
- **S7** labels sit on a wire end or a pin (on a short stub), never floating.
- **S13** no text overlaps other text, a wire or a symbol body (checked on the text KiCad renders).
- **S14** reference and value sit next to their symbol (≤ 8.89 mm). **S15** all text reads left to right.
- **S16** nothing in the frame margin or the title block.
- Net names: UPPER_CASE, meaningful (`VIN`, `VBUS`, `SW_A`, `I_SENSE_P`, `GATE_HS`, `EN`, `PGOOD`); power nets
  start with `+`/`V` or end with the rail voltage (`+12V`, `+3V3`); HV nets carry a prefix agreed in the booklet
  (e.g. `HV_DC+`). Global labels only for nets that leave the sheet; local labels for the rest.
- Every externally supplied net carries a `PWR_FLAG` on a stub (ERC).
- Unused pins get a no-connect marker (S12); never leave a pin silently open.
- Reference designators follow the sheet's block numbering (e.g. 2xx for module 2) when the booklet says so.

## Drafting workflow (agent mode)
1. Read booklet + brief. List the groups and their left-to-right order; sketch columns (x) and rows (y).
2. Place the main IC / switch of each group first, query its pins (`forge schdraw pins`), then place passives
   so one pin of each lines up with the pin or rail it connects to.
3. Connect with `connect` paths (pin → pin, pin → point → pin), `stubs` for labels and power symbols, `flags`
   for PWR_FLAGs.
4. `forge schdraw check spec.json` → fix every ERROR, look at the render (crop at 3–4×) → repeat.
5. Only a clean check is applied. The netlist must match the brief exactly (C1–C5 and KiCad's own netlist K1).

## Linting any sheet, and who owns it

`forge schdraw lint <sheet>` checks a saved sheet, hand drawn or engine drawn, against every rule above that needs
no drawing spec (S1 to S9, S12 to S16), plus: L1 symbol outside the drawing area, L2 empty title block, L3 low
utilization (advice only), L4 crowded symbols, L5 vertical label on a horizontal wire. It prints a score from 0 to
100 (the same sheet always gets the same score) and the findings.

Ownership decides what the findings mean. A sheet that `forge schdraw apply` wrote, and nobody has saved since, is
**engine-owned**: its findings are errors, and the Stop hook keeps a legibility obligation open until they are
fixed. Saving the sheet in KiCad is the deliberate one-way door to **user-owned**: from then on the findings are
advice only and never block. The record lives in `.forge/schdraw_owned.json` (sheet path to the SHA-256 of what
schdraw wrote), so nothing is added to the drawing itself.
