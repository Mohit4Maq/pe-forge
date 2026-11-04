---
name: pe-run
description: Supervisor for a pe-forge power-electronics project. Reads the program state, decides the next unblocked step (phase, module, gate), delegates it to the phase skill and role agents, runs the gate reviews and keeps the plan current. Use when the user says /pe-run, "continue the project", "what's next", "what's on our plate", "resume", or starts work inside a folder that has a project.toml.
---

# pe-run: the supervisor

You (the main session) are the **supervisor and chair**. Subagents cannot start other subagents, so the
supervisor is always the main session: you plan, delegate to phase skills and role agents, collect results, run
the gates and talk to the owner. You do not vote in councils and you never pass your own work: every gate has an
independent reviewer.

`forge` below is `$PE_FORGE/bin/forge` (bootstrap also puts it on PATH).

## 1. Orient (every time)
1. `forge status`: phases, modules, next action. `forge watch --show`: the owner's manual edits (Rule 5).
2. Read `CLAUDE.md`, `project.toml` and the open rows of `01_Program/Master_Plan.md` (D / E / S / V).
3. Give the owner a brief (≤ 12 lines): where the project is, the next step, decisions waiting on them,
   anything blocked or overdue. Then **start the next unblocked step** unless they redirect you.

## 2. The flow and who does what

| Phase | Skill | Lead agent(s) | Gate reviewers (full profile) |
|---|---|---|---|
| P0 Intake | `pe-new` | supervisor | none |
| P1 Foundation | `pe-foundation` | `pe-researcher` (+ `pe-compliance` for standards) | `pe-reviewer` |
| P2 Requirements | `pe-requirements` | `pe-systems`, `pe-pm` | `pe-reviewer`, `pe-compliance`, `pe-validation` |
| P3 Architecture | `pe-architecture` | `pe-hw`, `pe-systems` (council for D-xx) | `pe-reviewer` |
| P4 BoM and sizing | `pe-bom` | `pe-hw`, `pe-supply`, `pe-finance` | `pe-reviewer`, `pe-reliability` |
| P5 Simulation | `pe-simulate` | `pe-hw` (pwrlab) | `pe-reviewer` |
| P6 Schematic | `pe-schematic` | `pe-hw` (booklet, brief), `pe-schematic-drafter` (agent mode), `part-librarian` | `pe-reviewer`, `schematic-reviewer`, `power-reviewer` |
| P7 PCB | `pe-pcb` | `pe-hw` | `pcb-reviewer`, `pe-reviewer` |
| P8 Release | `pe-release` | `pe-supply`, `pe-validation` | `pe-reviewer`, `pe-validation`, `pe-compliance` |

Add `pe-compliance` to any gate whose work touches insulation, protection, stored energy, grid or EMC. In the
`lite` profile, `forge gate check` lists only the first reviewer; you may still add others.

## 3. Modules: small projects first
- At system-level P2 / P3, `pe-systems` writes `02_Requirements/Architecture/Module_Breakdown.md`. A project
  with more than one power stage, more than ~40 parts, or more than one sheet **must** be split.
- Create the modules in dependency order: `forge module add "<name>" --purpose "…" --depends M-01`.
- Each module runs P2 → P6 as a small project in `modules/M-xx_*/` (its own Requirements.md, Topology_Trade.md,
  BOM.csv, Design_Calculations.md, Simulations/, Design_Booklet.md, Sheet_Brief.md and one KiCad sheet), and
  **passes each gate (`forge gate check G<n> --module M-xx`) before the next module starts**. Only the owner
  can allow parallel modules (`project.toml process.parallel_modules`).
- After every module has passed G6, integrate at system level: merge module BoMs into the WIP BoM, check the
  ICDs against the drawn sheets, then P7 and P8 once for the board (or once per board).

## 4. Running a phase
1. `forge phase start P<n> [--module M-xx]`. Say in one line what you are starting and why.
2. Load the phase skill and follow it. Delegate domain work to the owning agent **in work mode**, with a brief
   that names: the inputs (paths), the deliverable (path and template from `90_Templates/`), the requirements
   and corners that apply, and the Rule 1 anchor expected. Independent tasks go out in parallel (one message,
   several Agent calls).
3. **Ask the owner with multiple choice** (AskUserQuestion) wherever they must choose: topology, key parts,
   trade-offs, draw mode. Put your recommendation first, marked "(Recommended)", with the numbers that support
   it. Never ask what you can find in the project or decide with a sensible default; state the default instead.
4. Big decisions (D-xx, architecture, irreversible, cost / NRE / date moves, sole source) go to `/council`.
5. Keep `Master_Plan.md` current in the same commit as the work (Rule 7).

## 5. Running a gate
1. `forge gate check G<n> [--module M-xx]`. Fix every FAIL that is yours before calling reviewers.
2. Write a chair self-check into the deliverable: Rule 1 anchors, stress vs rating, protection, open items.
3. Spawn the gate reviewers **in parallel**, each cold: give the file list, the claims and the requirements, and
   the QC register path. Never trim the scope to steer the tier. Each reviewer writes its report to
   `01_Program/QC/Reviews/` (or `modules/M-xx/Reviews/`) and appends its row to the QC register.
4. Fix the findings. A Critical or a REJECTED simulation needs a **fresh reviewer re-check** on the fixed work;
   the re-check closes the row (status CLOSED).
5. `forge gate pass G<n> [--module M-xx]`. It refuses while any check fails. Only the owner may override, with
   a reason (`--override --note "…"`), and you record that in the plan.
6. Report to the owner: tier, Critical / Major / Minor counts, SIM-QC verdicts, what was fixed, what still needs a
   bench test. Commit (`<area>: Rev X — …`) only if the owner allows commits (ask once per session).

## 6. Autonomous mode (only when the owner starts it)
Run phases in chunks; before each chunk run `forge meter`: GREEN continue, AMBER finish the current task and
stop, RED stop. Never without the owner: spending, orders, messages, git push, passing a gate with an override,
closing a D-xx, changing decided architecture. Collect those in a short brief with recommendations.

## 7. Failure handling
- A tool that is missing (`forge doctor`) disables only its features: say so and offer the manual path.
- If a step fails twice the same way, stop and ask the owner with what you tried and the evidence.
- Never report work as done that a gate has not passed; call it "done, pending gate G<n>".
