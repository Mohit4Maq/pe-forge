---
name: pe-new
description: Start a new power-electronics project with pe-forge (phase P0). Asks the user targeted multiple-choice questions about the product, builds the standard folder structure, project.toml, CLAUDE.md rules and a KiCad project, then hands over to the supervisor. Use when the user says /pe-new, "start a new project", "I want to build a <converter / inverter / charger / motor drive>", or describes a power-electronics product to develop.
---

# pe-new: intake and project set-up (P0)

## 1. Understand the request
Read what the user wants to build. Restate it in one line (function, power, input → output). If there is no
folder yet, propose `<cwd>/<ProjectName>` and confirm it in the first question batch.

## 2. Cross-question with multiple choice
Use AskUserQuestion, **at most 4 questions per call, at most 3 calls**. Every question: concrete options with a
short consequence, the recommended one first and marked "(Recommended)". Skip any question the user already
answered or that has an obvious default (state the default instead). Pick from this bank, by product type:

**Batch 1: what it is**
- Product type: DC-DC (non-isolated / isolated) · AC-DC (PFC + DC-DC) · DC-AC inverter (1-ph / 3-ph, grid-tied /
  off-grid) · motor drive · battery charger / BMS power path · other.
- Power and voltage class: < 100 W LV · 100 W–1 kW · 1–10 kW · > 10 kW; SELV (< 60 V DC) vs hazardous voltage.
- Input and output ranges (offer typical ranges for the type; "other" lets them type exact numbers).
- Directionality / isolation: unidirectional · bidirectional; isolated · non-isolated (and why: safety, ground).

**Batch 2: constraints**
- Volume and cost: prototype only · < 1k / yr · 1k–10k · > 10k; target BoM cost band.
- Environment: ambient range, cooling (natural convection / fan / liquid / cold plate), ingress.
- Market and certification: region (IN / EU / US / global), standards family (IEC 62368-1, IEC 62109, IEC
  61851, IEC 60335, automotive AEC / ISO 26262, none yet).
- Efficiency / density priority: max efficiency · lowest cost · smallest size · fastest time to prototype.

**Batch 3: how we work**
- Switching device and control preference: Si MOSFET · SiC · GaN · IGBT · "recommend in P3"; MCU family or
  "recommend".
- Schematic drawing: **I draw, agents support** (briefs, part placement, checks) · **agents draw** from the
  booklets in the strict style, I review · **ask per sheet**.
- Review rigour: **full** (independent review at every gate, tiers T1–T4, SVR on every simulation) · **lite**
  (one reviewer per gate, same automated checks; for small low-voltage boards).
- Review cadence: at each gate (recommended) · after every design task.
- Commit check: **install the `forge check` git pre-commit hook** (recommended: a commit with an ERC error, a
  stale superseded term or a broken trace link is refused) · no hook. Answer key `precommit_hook`.

## 3. Create the project
1. Write the answers to `<scratch>/intake.json` (schema in `forge new --help`: name, summary, owner, type,
   specs, corners, standards, volume, target_cost_usd, profile, schematic_mode, reviews, region, distributors,
   precommit_hook, answers{question: answer}). **Corners must hold both ends of every range** (input, output, load, f_sw,
   ambient): every design is verified at both ends.
2. `forge new <dir> --answers <scratch>/intake.json`.
3. Fill `01_Program/Master_Plan.md` §1 and open the first items: E-01 market survey, E-02 literature, E-03
   reference designs, E-04 standards register; any D-xx the answers left open (e.g. D-01 device technology).
4. `forge gate check G0`, then `forge gate pass G0`.
5. Ask once whether you may commit to git in this project; if yes, commit `P0: project created`.
6. Tell the user in ≤ 8 lines: where the project is, what P1 will produce, what you need from them (IEEE /
   paywalled PDFs they can drop into `03_Foundation/Literature/`), and that `/pe-run` continues.
7. Continue straight into P1 with the `pe-run` skill unless the user stops you.
