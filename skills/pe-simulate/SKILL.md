---
name: pe-simulate
description: Phase P5 of a pe-forge project - verified simulation of the chosen design with pwrlab / LTspice (or ngspice), an anchor case first, both corner ends, and a Simulation Verification Record for every result. Use when the supervisor reaches P5, or the user asks to simulate, verify losses, ripple, efficiency, THD, transients or faults.
---

# pe-simulate: simulate and verify (P5)

**A simulation result is a hypothesis until it is anchored.** Load the `pwrlab` skill for tool details.

1. **Plan** the cases from the requirements: each SYS / HWR number that simulation can check, at every corner
   end in `project.toml [corners]` (both ends of each range; worst load; worst temperature).
2. **Anchor case first, on the same model**: reproduce a known result (a reference design's published
   efficiency or ripple, a paper's figure, or a closed form at an operating point where it holds). Use
   `pwrlab benchmark` where a benchmark exists. Record the agreement. If it misses tolerance, fix the model
   before running any design case (Rule 1: suspect the model first).
3. **Design cases**: `pwrlab analyze` / `sweep` / `tolerance` / `fault` / `spectrum`. Call `validate_params`
   before sweeps. Run LTspice jobs serially. A result with `usable=false` or `evidence_eligible=false` is not a
   result: report the verdict and fix the cause.
4. **One results doc per study** in `06_Simulations/Results/` (module: `modules/M-xx/Simulations/`) with the
   question, setup, results table vs requirement, figures (checked visually), and the **SVR table**
   (`90_Templates/T_SVR.md`). Scripts and netlists go in `06_Simulations/Scripts/` so the reviewer can re-run.
5. Unsupported topology in pwrlab: write the netlist yourself (LTspice or ngspice), apply the same checks by
   hand (timestep halving, energy balance, steady state, corner coverage) and say that it is a hand model.
6. Results that fail or cannot be verified: an explicit "not verified, and why" list; never a design value.
7. Gate G5: `pe-reviewer` runs SIM-QC on every result (ACCEPTED / PROVISIONAL / REJECTED) and re-runs at least
   one key case itself.

## Small analog circuits (ngspice assertions)

Sense amplifiers, dividers into ADCs, gate-drive RC, UVLO / OVP thresholds, feedback and NTC bias are not
pwrlab's job. Write a `## Simulation` block in the sheet brief (grammar: `references/assertions.md`), give every
assertion an `expect:` closed form, then `forge spice check --module M-xx`. The record lands in the module's
`Simulations/` with its SVR, and `forge check` runs it whenever ngspice is installed.

## Results as data (the side panel and the report)

Next to every simulation record, write `<record stem>.result.json` (schema in `forge/results.py`): the grade, the
corners, one metric per key number with its values per corner, the closed-form value, the difference in % and the
metric's own grade, the charts (sweeps as `line`, loss breakdowns as `bars`) and the notes. Run
`forge results check`. **A result is evidence only when it is citable:** copy the pwrlab payload's `provenance`
block into the result file (`model_version`, `toolchain_hash`, `run_utc`, `evidence_eligible` must be true), or for
a netlist you wrote `"provenance": {"tool": "ltspice", "netlist": "<path>", "sha256": "<hash>"}`. Write `"params"`
(`f_sw_Hz`, `L_H`, `C_out_F`, `esr_ohm`) so `forge calc check` can recompute the closed forms exactly; `err_pct` must
be the computed error, never rounded to look good. **G5 requires the set** (`forge results check` lists what is
missing): for a DC-DC converter inductor ripple, peak current, output ripple, efficiency, loop stability (phase /
gain margin), start-up / inrush, output short / overload; `[sims] required` / `[sims.waive]` in project.toml change
the list with the owner's reason. The pe-forge side panel (V-cycle, Simulations, Checks, Gates, Parts tabs) opens on it, and
`forge report --open` shows it as an interactive page. `forge spice check` writes its own result files.
