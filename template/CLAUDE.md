# {{NAME}}: engineering rules for Claude (read every session)

{{SUMMARY}}

This project was created with **pe-forge**. The supervisor skill `/pe-run` drives it through phases P0–P8 with a
gate at the end of each. Settings live in `project.toml`, program state in `.forge/state.json`
(`forge status` shows it). **{{OWNER}} is the project owner and decides**; the agents inform, challenge and do
the work. The rules below are mandatory for all design work in this project.

## Rule 0: the flow
P0 intake → P1 foundation (market, literature, reference designs, standards) → P2 requirements (SYS → HW / SW /
control) → P3 architecture and topology → P4 tentative BoM and sizing → P5 simulation → P6 schematic → P7 PCB →
P8 manufacturing release. **A big project is split into modules after P2/P3** (`forge module add`); each module
runs P2–P6 as a small project and passes its own gates before the next module starts. Never skip a gate:
`forge gate check G<n>` must be green and the gate review closed in the QC register.

## Rule 1: benchmark every result before it is used
Every simulated or calculated number is checked against a trusted source before it is quoted, used for a
decision or written into a document. Sources, in order: published hardware data (vendor reference designs and
app notes in `03_Foundation/Ref_Design/`) · peer-reviewed papers (`03_Foundation/Literature/`, `Lit_Notes/`) ·
the part's datasheet (latest revision, read the actual table or curve) · an independent closed-form calculation
· earlier verified results in this project.
- State the anchor and the agreement, with its type: MATCH, HW-CONSISTENT, PUBLISHED-EQ, BOUNDED, CLOSED-FORM-ONLY.
- Judge physical sense even without a benchmark: energy balance, units, order of magnitude, trend, sign, limits.
- A disagreement beyond tolerance (> 2× against reference hardware, > 10–15 % against closed form) means **suspect
  the model first**; find the cause before using the number.
- Run an anchor case with known numbers on the same model before the design case.
- Results that fail or cannot be verified go in an explicit "not verified, and why" list, never into a design value.
- **Simulation Verification Record (SVR)** for every simulation in a deliverable: model provenance, anchor case
  and agreement, convergence status, timestep / tolerance check, energy closure, corners covered
  (`project.toml [corners]`, both ends of every range), and the script + netlist that reproduce it.

## Rule 2: independent review at every gate
- Every gate ends with an independent review by `pe-reviewer` (cold, guilty until proven), plus the gate's
  specialists (`forge gate check` lists them). The reviewer sets the tier itself: T1 routine · T2 standard ·
  T3 high · T4 critical / safety (insulation, protection, stored energy, grid, anything irreversible). Only the
  owner may lower a tier.
- Every simulated result is graded ACCEPTED / PROVISIONAL / REJECTED. Design values rest only on ACCEPTED ones.
- Every review adds a row to `01_Program/QC/QC_Register.md`. A Critical or a REJECTED result is closed only by a
  fresh reviewer run on the fixed work, never by the author's word.
- Report findings to the owner: tier, Critical / Major / Minor, SIM-QC verdicts, what was fixed, what still needs
  a bench test. Do not call work done while a Critical, a REJECTED result in a design value, or an OPEN register
  row of the task remains.
- Review cadence: `project.toml process.reviews` = `per-task` (review at the end of every design task) or
  `gate` (a short chair self-check per task, full review at the gate; results labelled PROVISIONAL until then).

**PCB:** every placement and routing milestone, and every board before release, gets an independent review by
`pcb-reviewer` against every L-rule of the layout spec (`forge pcb review-new`, `forge pcb review-status`).
The review is tied to the board's content hash: a later board change needs a new review. Gates G7 / G8 refuse
otherwise.

## Rule 3: parts, BoM and document control
- Use real, stocked MPNs (Mouser / DigiKey / LCSC / element14 APIs; keys in env vars or `~/.mouser_key`, never
  printed). An out-of-stock part is replaced and the swap is stated.
- **Every part has at least one qualified alternate** (prefer an exact drop-in from another manufacturer), with
  price at qty 1 and the volume break, stock, lead time and MOQ for both. Zero stock at one distributor is never
  the final answer: check the others. Custom parts (magnetics) get performance-based specs or a second vendor.
- `04_Hardware/BOM/WIP_BOM.csv` and `.md` are updated after every part decision; no line is complete without
  its Alt columns (`forge bom check`).
- Controlled docs carry `rev:` in frontmatter and a revision-history table; one commit per revision
  (`<area>: Rev X — what/why`); annotated tag for every external release.
- Figures go in the doc's `figures/` folder and are checked visually for overlap and clipping.

## Rule 4: propagate every change
After changing any value, part, decision or file name, update or retire every document it makes obsolete in the
same task. Register the old term in `.forge/superseded_terms.csv`; intended mentions go in
`.forge/consistency_allow.csv`. `forge consistency` must report 0 (a Stop hook enforces it). Also re-read
downstream docs for changed numbers (totals, losses, prices) that a term search cannot catch.

## Rule 5: the owner's manual edits are design intent
A hook reports files changed outside Claude at every turn. Read them, say briefly what changed, propagate them,
re-check them. **Never revert or overwrite a manual edit**; if it conflicts with a verified result or a decided
item, ask, stating the conflict with numbers. Ask when the intent is unclear.

## Rule 6: schematics
`project.toml process.schematic_mode` decides who draws: `user` (the owner draws; Claude writes briefs, places
parts on request with `kicad-place`, checks and reviews), `agent` (the `pe-schematic-drafter` agent draws with
`forge schdraw`), or `ask` (ask per sheet). In every mode:
- **Before any drawing**, the design booklet and the sheet brief exist and passed review: every part chosen,
  its symbol and footprint verified pin-by-pin against the datasheet, the pin-by-pin net table, values with
  calculation and benchmark, layout hints (HV / LV zones, creepage).
- Agent drawings follow the style guide (`skills/pe-schematic/references/style-guide.md`) strictly: real
  orthogonal wires, left-to-right flow, ground down, supplies up, T-junctions only, no text overlaps. `forge
  schdraw check` must be clean and its netlist must match the brief exactly; the rendered sheet is inspected.
- The KiCad schematic is the master; block diagrams in docs are concept figures only.

## Rule 7: the plan is the single source of truth
`01_Program/Master_Plan.md` holds open work, decisions (D-xx), engineering items (E-xx), sheets (S-xx) and
waiting items (V-xx), mirrored by `.forge/state.json`. Keep it current in the same commit as the work. "What's on
our plate?" → read the plan and `forge status`, brief the owner, start the top unblocked item.

## Rule 8: big decisions go to the council
Any D-xx, architecture, anything irreversible, a material change of cost / NRE / dates, or a sole-source
commitment goes through `/council` (2–3 role agents write independent memos, cross-examine once, the chair
writes the record). Routine choices do not. The owner decides.

## Rule 9: requirements, compliance, traceability
- Every new or changed requirement goes to `pe-systems` (impact), `pe-compliance` (clauses, limits, test
  method) and `pe-validation` (verification method) before it is frozen.
- V-model: SYS (L1, `02_Requirements/System_Requirements.md`) → HWR / SWR / CTL (L2, `V_Model/trace.csv`) →
  design and FRS (L3) → implementation (L4), each with verification. A SYS change runs `forge vmodel diff` then
  `forge vmodel cr CR-nnn --why "…"`; close with `build`, `check` (0 errors), `sync`. Generated notes are never
  edited by hand.
- IDs link to their origin (`forge link --apply`); vendor-facing docs, headings and code are never linked.
- A clause that could not be read is labelled NOT VERIFIED, never quoted as fact.

## Rule 10: never without the owner
Spending, orders, emails / messages, git push, changing decided architecture, closing a D-xx, lowering a
review tier, passing a gate with an override. These go to the owner with a recommendation.

## Rule 11: follow the datasheet strictly for critical parts
Critical parts (converter and controller ICs, gate drivers, isolators, power switches, current-sense amplifiers,
references) are designed **exactly as their datasheet recommends**: application circuit and component values,
recommended land pattern, and every item of the "Layout Guidelines" / "Layout Example" sections (input-cap
placement, hot loop, SW node area, BOOT / FB placement, thermal pad and vias, ground plane, copper weight).
- Before drawing or placing a critical part, turn its datasheet recommendations into numbered rules in the sheet
  brief / layout spec, each citing the datasheet revision, page and section.
- Every review checks the design against those rules, one by one.
- A deviation needs a stated reason and the owner's OK, recorded next to the rule; never a silent one.
- **The datasheet is final.** Where a part's datasheet (land pattern, pin spacing, layout example, application
  values) conflicts with a generic project rule (spacing table, net-class clearance, house style), the datasheet
  wins for that part. Encode it as a rule scoped to that part (e.g. a `.kicad_dru` rule on
  `memberOfFootprint('U1')`) and note it next to the generic rule, citing the datasheet page.

## Rule 12: design every board for the best EMI, heat and shielding it allows
Clean DRC is the floor, not the goal (owner, 2026-11-08). On every board:
- **Copper fill:** every unused area of the outer layers is poured with GND (lowest priority, so power and
  switch copper keep their shapes); inner layers carry solid planes. The datasheets of power ICs ask for it
  (e.g. "as much copper as possible on the top and bottom layers").
- **Reference plane:** a solid, unbroken GND plane on the layer next to the switching layer; no slots or splits
  under fast or sensitive nets; via clearance holes that merge into slots are fixed.
- **Stitching:** GND layers tied with a via grid (≤ 5 mm pitch, `forge pcb zones` stitch block), plus vias at
  every decoupling and bulk capacitor ground and around power pours and board edges.
- **Loops and nodes:** hot loops (input cap to switch to return) smallest possible on one layer; switch nodes
  short, wide and no bigger than needed; no switch-node copper on quiet layers (SMD test pads on SW).
- **Sensitive routing:** feedback, sense and gate-drive returns over solid plane, away from the switch node and
  inductor; Kelvin sense joined only by its own trace.
- **Thermal relief:** every pad (through-hole and SMD) joins a pour or plane through thermal-relief spokes so it
  can be soldered and reworked; spokes sized for the pad's current. A solid joint only where the datasheet asks
  (an exposed thermal pad, a high-current pad), set per part in placement.json and cited.
- **Heat:** hot parts get copper area and via arrays to inner and bottom planes; 2 oz outer copper where the
  datasheet asks.
- `forge pcb copper` measures coverage, plane continuity and stitching density (check `pcb_copper`); the
  independent PCB review judges the rest against the layout spec's EMI / thermal rows.

## Decided architecture (do not re-litigate without a reason)
| Item | Decision |
|---|---|
| _(filled as D-xx decisions close)_ | |

## Where things are
| What | Where |
|---|---|
| Settings | `project.toml` |
| Plan, QC, decisions | `01_Program/` |
| Requirements, V-model, compliance | `02_Requirements/` |
| Market, literature, reference designs | `03_Foundation/` |
| Design notes, BoM, booklets, sheet briefs | `04_Hardware/` |
| Simulations | `06_Simulations/` |
| KiCad project | `08_PCB/` |
| Manufacturing outputs | `09_Release/` |
| Modules | `modules/M-xx_*/` |
