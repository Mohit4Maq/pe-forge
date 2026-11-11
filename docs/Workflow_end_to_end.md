# The end-to-end workflow, made stringent (2026-11-11)

"Let's build a non-isolated 48 V to 12 V converter, 1 A out." What happens, step by step, and what makes each
step's output verified rather than written. The rule throughout: **a step produces evidence or it produces a
question for the owner; it never produces prose that stands in for either.**

## 0. Intake (P0, G0): define everything before any design work

The tool asks, in batches of at most four multiple-choice questions, and **does not guess**:
- what it is: type, power class, input and output ranges with both corners, isolation, direction, load type
  (constant / pulsed / motor), hold-up, start-up behaviour wanted;
- environment: ambient corners, cooling (natural convection by default), **ingress (IP code) and where it is
  used**, mounting, vibration / shock class, altitude, pollution degree;
- market: region, standards family, volume, cost target, **enclosure material and process preference**
  (aluminium die-cast / extruded / sheet, plastic moulded, printed, vendor box);
- interfaces: connector family preference (screw terminal, pluggable, M12 circular, cable gland + lead),
  indicators, enable / power-good signals, test access;
- how we work: who draws the schematic, review rigour, the approvals the owner wants to give in person.

Every answer lands in `project.toml` (`[specs]`, `[corners]`, `[enclosure]`, `[fab]`, `[process]`) and in the
intake record. Anything the owner leaves open becomes a **decision (`forge decision new`)** tagged to the first
gate that needs it; that gate refuses while it is open. G0 passes only when the intake record and the plan are
complete (`forge gate check G0`).

## 1. Foundation (P1, G1): market, references, standards

Reference designs (TI / ADI / vendor EVMs for the IC class), the standards register (`pe-compliance` reads the
clauses that set limits: IEC 62368-1 for a SELV DC-DC, EMC class), literature. G1 needs the four documents
filled (placeholders and under-120-word stubs are refused) and `pe-reviewer`'s review **recorded against the
files** (`forge review record`).

## 2. Requirements (P2, G2): testable rows, both corners, traced

System requirements (SYS-xxx) with a limit, a condition and a verification method each; derived HWR / SWR rows
in `trace.csv`; `forge vmodel build && check` with 0 errors. `pe-compliance` and `pe-validation` review. For an
adopted board `forge req infer` drafts from the design and the owner confirms every row.

## 3. Architecture (P3, G3): topology and device choice with the trade written down

Topology trade (sync buck vs controller + FETs, frequency, device technology) with closed-form losses per option
(`forge calc`), an ADR per decision, a council (`/council`) for anything irreversible. Open choices the owner
should make (controller family, cost vs efficiency) are decisions, not assumptions.

## 4. Parts and sizing (P4, G4): every line sourced, every rating checked

Design calculations (inductor, caps, FET losses, thermal at ambient max) anchored to the reference design and
datasheets; the WIP BoM built from the library with price, stock, lead time and an alternate per line
(`forge bom build`), supply risk SC-1..9 with no unwaived HIGH; `forge stress` on the chosen parts against the
corners with derating. `pe-reliability` reviews the margins. A part not in MC_Parts goes in through
`part-librarian` with its datasheet evidence (hash) and verified land pattern.

## 5. Simulation (P5, G5): anchored, citable, the required set

pwrlab / LTspice: the anchor case first (the reference design's published numbers reproduced), then both corners.
Every result file carries provenance (model version, toolchain hash, evidence-eligible), the parameters used, the
closed form, and `forge calc check` must reproduce that closed form from the schematic. The set must include
ripple, peak current, output ripple, efficiency, **loop stability, start-up, output short / overload**; a missing
one is a gate failure, waivable only by the owner with a reason. SVR per result.

## 6. Schematic (P6, G6): booklet, brief, drawn in the readable style, checked and reviewed

Per sheet: a design booklet (function, every value with its calculation and anchor) and a sheet brief (pin table,
net names, layout hints, the `## Simulation` assertions). The sheet is drawn (by the owner, or by the engine in
the strict style: signal flow left to right, real wires, HV / LV zones, legibility score) and must pass ERC with
0 errors and 0 unexplained warnings, the library check (every part from MC_Parts, status Active with evidence),
`forge drift` (BoM, brief and booklet say what the sheet says), `forge protection`, `forge insulation`,
`forge stress`. `schematic-reviewer`, `power-reviewer` and `pe-reviewer` review; the reviews are recorded against
the sheet hash. **Board work does not start before G6** (unless the owner declares a sketch, which never ships).

## 7. PCB (P7, G7): rules first, placement reviewed before routing, every board

`forge pcb setup` from `[pcb]` (outline, stack-up from the rule book, DRU from the insulation table and the fab
house), placement by the power-layout rules (HF loops, switch-node area, thermal pads, connector faces on the
edge) and **a placement review milestone the owner approves** (`forge pcb review-new placement`, `pcb-reviewer`,
then the owner's decision if the review leaves a choice). Routing by the rule book (Rules 11–15 measured: copper,
stitching, orthogonal layers, SW edge distance, thermal vias); DRC 0 errors, **0 unrouted, parity clean**; the
high-power rule book Musts green or waived by the owner; `fp_pins` (pins vs pads, polarity, rotation source);
the final PCB review bound to the board hash. Multi-board projects: every board.

## E. Enclosure (GE, new, before release): drawn from the board, checked, IP by the weakest entry

`forge enclosure req` (IP code, material, mounting, ambient, IK), `fit` (envelope from the board STEP and part
heights), `entries` (every signal leaving the box gets a sealed panel part or gland with its own IP, current and
creepage rating; the box's IP is the minimum), `build` (build123d: aluminium / plastic / vendor box machined),
`check` (fit, thermal closed form always, CalculiX when installed, structural, ingress rules, manufacturability),
`pe-mechanical` review recorded, the IP and thermal test plans into the DVP&R. Detail: `docs/PLAN_enclosure.md`.

## 8. Release (P8, G8): nothing skipped, nothing stale, nothing unapproved

Gerbers, drill, BoM, CPL (rotations from a known source), stencil, fab notes with the stack-up, the panel, the
assembly drawing, the mechanical package, the test plan (DVP&R), release notes; the manifest hashes everything.
G8 refuses on: any skipped check, a fab-house rule the house cannot make, stock data older than 14 days, an
unwaived HIGH supply risk, no test access on a rail, an open decision, a sketch flag, requirements not passed, an
enclosure gate not passed when required, light process level. No override exists at G7 / G8.

## Approval points (what the owner is asked, in person)

| Where | What the owner decides |
|---|---|
| G0 | the intake answers; anything left open becomes D-xx |
| G3 | topology / controller when the trade is close; anything irreversible (council) |
| G4 | a part swap that changes cost or a single-source part; a waiver of a supply HIGH |
| G5 | a waiver of a required simulation |
| G6 | the schematic (if they draw: theirs; if the engine draws: they accept it) |
| G7 placement | the placement milestone: connector positions, heatsink side, board size |
| G7 routing | a Must rule waiver of the rule book |
| GE | IP code, material, vendor box vs custom, connector / gland family |
| G8 | the release itself (`forge gate pass G8` at a terminal) |

Decisions are typed by the owner in their terminal (`forge decision answer`); an agent cannot answer for them,
exactly as it cannot approve a change request or lower the process level.

## What "verified, not prose" means at each gate (the mechanical rules)

- a deliverable with placeholders or under 120 words is not filled in;
- a review is a file with a reviewer and a verdict, hashed with the files it reviewed; a changed file voids it;
- a number is a simulation result with provenance, or a closed form pe-forge recomputes, or a datasheet value with
  the page; otherwise it is labelled NOT VERIFIED and cannot sit in a design value;
- a check that could not run is SKIP; at G8 a skip fails;
- an override is the owner's, with a reason, recorded, and never at G7 / G8 / GE.
