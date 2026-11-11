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

> **Agent names:** installed as a plugin, every agent is prefixed with the plugin name: spawn `pe-forge:pe-hw`, `pe-forge:pe-reviewer`, `pe-forge:schematic-reviewer` and so on (subagent_type). This file uses the short names.

> **No `project.toml` here but KiCad files** (the session-start note says "not a pe-forge project yet"), or a
> request like "help me with this board" on a folder that is not a project: follow `/pe-adopt`. Help first with
> `forge quick` (read-only), offer adoption once afterwards, the full process only before boards are made.
>
> **Light mode** (`forge level` says light, as after `forge adopt`): checks are advice and nothing blocks. Keep the
> owner's question first; mention open findings briefly; do not push phases. Release (G8) is refused until the
> level is raised: `forge req infer` + the owner's confirmation, then `forge level standard`.

> **Human in the loop (default).** The owner verifies and comments at every critical step: every gate pass
> (`forge gate pass` refuses an agent; show them the evidence, they run it with `--comment`), the placement
> milestone (a D-xx opened by `forge pcb review-new placement`), every open `forge decision` (connector family, IP
> code, material, part swaps, waivers). Present each as: what was checked, the numbers and their anchors, what
> is open, your recommendation; then **stop and wait**. Never answer a decision or pass a gate for them. Only
> when the owner has set `[process] approval = "hands-free"` themselves (`forge level hands-free` at their
> terminal) do you pass gates; every check still applies.

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
6. **Multi-file design changes run in a transaction** (a part swap, a value change through BoM + booklet + brief,
   a sheet edit): `forge txn begin "<what>"` (add `--allow-dirty` if the owner has uncommitted work: it is
   snapshotted), make the edits, `forge txn verify`, then `forge txn commit -m "…"` (when commits are allowed)
   or `forge txn close`. If verify cannot be made to pass, `forge txn rollback --reason "…"`: never hand-revert.
   The failed work stays in `git stash list` and `.forge/runs/<id>/summary.md`.

## 5. Running a gate
1. `forge check` (the whole project: ERC, DRC, consistency, V-model, BoM, drift, library checks; deterministic,
   seconds) then `forge gate check G<n> [--module M-xx]`, which uses the same sub-checks. Fix every FAIL that is
   yours before calling reviewers. A SKIP is not a pass: say why it was skipped.
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

## 7. Obligations (hooks)
Every edit opens obligations (`forge ledger show`): ERC after a sheet edit, DRC after a board edit, drift after a
BoM / brief / booklet edit, the V-model after a requirement edit, a dated revision line after any change to a
module past G6. The Stop hook re-runs them and blocks finishing while one fails. Fix the cause; never clear the
ledger yourself (`forge ledger clear --reason` is the owner's escape hatch and is logged in the QC register).

## 8. Change control after baseline
Once a module passes G6 its sheet, BoM, booklet, brief and calculations are locked; once the board passes G7 every
KiCad file is. An edit to a locked file is denied until an **approved** change request covers it:
1. `forge cr new "<what and why>" --modules M-02 --files "<globs>" [--requirements SYS-..]`, fill in the CR file
   (`01_Program/Changes/CR-NNN.md`), `forge cr validate CR-NNN`.
2. Ask the owner to approve it (multiple choice, with the impact). **Only the owner runs `forge cr approve`.**
   Never pass `--i-am-the-owner`, never edit a CR's status yourself.
3. Make the change in a transaction (§4.6), re-run the affected gate checks, then `forge cr close CR-NNN`.
`project.toml [process] change_control` = enforce (full) / warn (lite) / off.

## 9. Failure handling
- A tool that is missing (`forge doctor`) disables only its features: say so and offer the manual path.
- If a step fails twice the same way, stop and ask the owner with what you tried and the evidence.
- Never report work as done that a gate has not passed; call it "done, pending gate G<n>".
