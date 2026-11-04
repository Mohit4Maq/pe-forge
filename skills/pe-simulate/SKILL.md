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
