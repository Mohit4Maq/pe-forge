---
name: pe-requirements
description: Phase P2 of a pe-forge project - write the system requirements (SYS, L1), break them down into hardware, software and control requirements (HWR / SWR / CTL, L2) with verification items, decide the module split and freeze the baseline. Also runs per module. Use when the supervisor reaches P2, or the user asks to write, break down, trace or change requirements.
---

# pe-requirements: system → hardware / software / control (P2)

## System level (once)
1. **SYS rows** in `02_Requirements/System_Requirements.md` (owner: `pe-systems` with `pe-pm`). Each row:
   one testable statement with a number, the condition and the source (intake answer, market benchmark,
   standard clause, decision). Cover: ratings and ranges (both ends: these become `project.toml [corners]`),
   efficiency, ripple / THD, transient response, protection (OV, UV, OC, short, OT, reverse polarity, fault
   ride-through if grid), isolation and insulation, EMC, interfaces and communication, start-up / shutdown,
   thermal and ambient, mechanical, lifetime, cost.
2. **Multiple choice where a target is a choice**, not a fact (e.g. efficiency target 96 / 97 / 98 % with the
   cost consequence of each from the market table). Recommended first.
3. **Intake for every SYS row** (spawn in parallel): `pe-systems` (impact, budgets: power and loss, thermal,
   timing / protection latency, sensing error, MCU pins), `pe-compliance` (clauses, limits, test method,
   conflicts), `pe-validation` (verification method and level). Fix conflicts before freezing.
4. **Break down** into `02_Requirements/V_Model/trace.csv` with Python `csv` only (never by hand-splitting
   strings): id `HWR-<BLK>-nn` / `SWR-…` / `CTL-…`, title, requirement, parents (SYS ids, `;`-separated), block
   (module id once known), owner (agent), design (doc names), frs (FW items or `NEW`), verif (ids), method,
   status (O open / B baselined / D decided), applies, impl, impl_note. Verification items go in
   `verification.csv` (id `UT-` / `AN-` / `SIM-` / `TP-` / `IT-` / `SV-` / `VAL-`, level L4…L0, procedure
   doc, owner, status).
5. `forge vmodel build` then `forge vmodel check` → 0 errors.
6. **Module split**: `pe-systems` writes `Architecture/Module_Breakdown.md` with the ICD table (see the rules
   in the file). Confirm the split with the owner (multiple choice: proposed split · fewer, larger modules ·
   single module). Then `forge module add …` per module in dependency order and set each trace row's `block`.
7. Gate G2 (system), then `forge vmodel sync` to freeze the baseline. Afterwards SYS rows change only through
   `forge vmodel diff` → `forge vmodel cr CR-nnn --why "…"` (the CR file is each owner agent's work packet).

## Module level (P2 for module M-xx)
`modules/M-xx/Requirements.md`: the trace rows whose block is M-xx, its ICD rows, and any module-only derived
requirement (added to `trace.csv` like any other). Gate: `forge gate check G2 --module M-xx`.
