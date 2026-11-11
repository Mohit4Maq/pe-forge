# Changelog

## Unreleased

**Half-done projects and the write guard**
- `forge adopt`: survey, then add pe-forge around an existing project without changing a file (verified by hash),
  KiCad files mapped in place, phase from the work done, `--undo`.
- Write guard: pe-forge never overwrites a file it did not write; proposed copies beside it, `forge guard
  status / accept`, `--replace` with a backup. Used by `forge new` (also with `--force`, which used to replace
  CLAUDE.md and the plan), `forge bom build` (used to overwrite a hand-made WIP_BOM.csv) and the template copy.
- `forge pcb setup`: keeps an outline it did not draw, keeps the stack-up unless `copper_oz` is set (it used to reset
  a 4-layer board to 2 layers), never removes copper layers that carry copper.

**Human in the loop, decisions, the enclosure chain**
- The owner verifies and passes every gate: `forge gate pass` refuses an agent; the owner runs it with
  `--comment` (recorded as an OWNER OK row). `forge level hands-free` (owner, at a terminal) switches it off;
  every check still applies.
- `forge decision new | list | answer | withdraw`: questions only the owner can answer (D-xx in the plan); a gate
  waits for every open decision tagged to it or earlier; the placement milestone opens one automatically.
- `forge enclosure req | fit | entries | build | check | release` (docs/PLAN_enclosure.md): requirements become
  decisions; the envelope from the board STEP (part heights; parts without a model are a gap); the box IP is the
  weakest entry (glands, M8 / M12, vent); build123d box (aluminium / plastic / printed rules: walls, gasket groove,
  inserts, standoffs, cut-outs) as STEP / STL with the board assembled; closed-form thermal (convection +
  radiation, inside-air step) against `[enclosure] max_inside_C`; gasket / flatness / vent rules; CalculiX and
  OpenFOAM levels SKIP (never pass) until installed; GE items in G8 when `[enclosure] required`.
- `forge doctor` rows for build123d, Gmsh, CalculiX, OpenFOAM. docs/Workflow_end_to_end.md: the stringent flow.

**No surprises when the boards arrive** (independent review 2026-11-11, F-01..F-17, all closed)
- G7 fails on unrouted connections and schematic / board mismatches (they were warnings); ERC / DRC warnings are
  findings (silk clipped by the edge, a symbol off its library).
- `gate pass --override`: the owner only (`--i-am-the-owner` at a terminal), a `--note` reason required, a QC
  register row OVERRIDE written, never at G7 / G8; strict level takes none.
- G8: a skipped check is never a pass; insulation skip passes only below 60 V or with `[insulation] none = true`;
  the stock snapshot must be under 14 days old; `[process] sketch = true` never releases.
- `forge stress` (STR-1..7): every capacitor, IC pin, transistor, diode, inductor and temperature range against the
  operating corners with derating; a switch node swings 0..Vin; a part or rail with no rating is a gap, not a pass.
  In `forge check`, `forge quick`, G6 / G7 / G8.
- `forge req infer`: the input limit is the lowest-rated part of any kind on the path (IC pins, diodes, caps), gate
  Zeners and boot caps excluded; unrated parts named.
- Simulation gate: every result needs provenance (pwrlab's block with evidence_eligible, or netlist + hash),
  `err_pct` must be the computed error, ACCEPTED needs a closed form or an anchor; the required set per converter
  type (ripple, peak current, output ripple, efficiency, loop stability, start-up, short / overload) must be shown;
  `forge calc` (buck / boost closed forms, `calc design`, `calc check`) recomputes every closed form from the
  schematic and refuses what it cannot reproduce.
- Multi-board projects: copper, connectors, crosstalk, the rule book and the PCB review run on every board.
- Reviews are bound to content: `forge review record G<n> <file>` hashes the review file (reviewer and verdict in
  its frontmatter) and every deliverable; a QC row alone no longer passes a gate (standard / strict), and a changed
  deliverable or review file fails it. Deliverables with placeholders or under 120 words are not filled in.
- Board work (`forge pcb setup / place / zones / route / sync`) waits for G6 at standard / strict unless
  `[process] sketch = true`; a sketch is never released.
- `forge check fp_pins` (FP-1..4, G7 item): every symbol pin has a pad, polarized parts on polarity-marked
  footprints, an assembly rotation source for every part (JLC_Rotation, `[fab.rotation]`, or the package table
  `library/tools/jlc_rotations.json`, now used by the JLCPCB CPL export), land pattern vs `Verified_Pads` pad size.
- `forge fab rules` (JLCPCB / PCBWay capability tables vs the board's design rules; G8 item), `forge fab panel`
  (KiKit), `forge fab testpoints` (every rail and key net has test access; G8 item).
- Rule book SW-02 counts routed copper, not via barrels; `forge quick` inside a project reads its [specs];
  stray `* 2.py` modules removed.

**Help first, process later**
- `forge quick [dir]`: pe-forge's checks on any KiCad folder, no project, run on a copy (nothing written, verified
  by hash), findings ranked SAFETY > errors > warnings, plus what the board is (rails, limits).
- Session start in an unmanaged KiCad folder: Claude is told to help directly, use `forge quick`, and offer
  adoption once afterwards (`/pe-adopt`, new skill).
- `forge level light|standard|strict` (`[process] level`): light = advice only, no blocking, no release; adoption
  starts light. Lowering needs the owner at a terminal.
- G8 needs the requirements and V-model in place (P2 passed, not skipped) and refuses in light mode.
- `forge req infer [--apply]`: draft system requirements from the design for the owner to confirm.

**Windows**: native support work (W1-W4): launchers, UTF-8, LF, paths, KiCad discovery; the suite passes on
Windows 11 ARM64 with KiCad 10; owner-only CR approval hardened against a NUL stdin.

## v0.3 (2026-11-09)

**PCB layout as data, reviewed against rules**
- `forge pcb sync | setup | place | zones | route`: board set-up, placement, pours (thermal relief by default, GND
  stitching) and routing from project data, DRC with schematic parity after each step.
- Project rules 11-15 in every new project, measured where possible:
  - 11: the datasheet is final for critical parts;
  - 12: best EMI / heat / shielding (`forge pcb copper`);
  - 13: edge connectors on the outline from the maker's drawing (`forge pcb connectors`);
  - 14: orthogonal signal layers (`forge pcb crosstalk`);
  - 15: the High-Power PCB Rule Book, ~130 rules, applied by default at 50 V / 5 A (`forge hp`, waivers on record).
- Independent PCB review forms with one row per layout rule, project rule and applicable rule-book rule, tied to the
  board's content hash; gates G7 / G8 re-run the Rule 12-15 checks themselves.

**BoM and supply chain**
- `forge bom build`: BoM from the drawn schematic; every part and its alternate checked at Mouser and LCSC
  (DigiKey optional): stock, price, lead time, lifecycle, MOQ, on-order dates. DigiKey / Mouser / JLCPCB order files.
- Supply-chain risk SC-1..SC-9 per line with mitigations (`04_Hardware/BOM/Supply_Risk.md`); gates G4 / G8 refuse
  an unwaived HIGH. `forge bom risk`, `--offline`.

**Protection and safety**
- `forge protection`: surge clamps sized against the system and every protected pin's absolute maximum
  (PROT-1..4), including "no standard TVS can do this".

**Learning from every user**
- `forge learn` (local log, no design data), `forge feedback`, `forge issue` (one-step GitHub issue, redacted,
  shown first) and `/pe-issue`; public feedback repo github.com/Mohit4Maq/pe-forge-feedback.

**UI**
- Results panel 0.2: big maq logo, spaced layout, five tabs, clearer simulation table and checks.
- `forge vault open` opens the project's own vault by id; `[ui] meter = false`; simulation bar charts without totals
  for comparisons.

**Fixes**
- The BoM generator read the bundled sample parts database instead of MC_Lib.
- Order files no longer list PCB features (MC_Parts `Exclude_BOM`).
- Recording an HV-01 safety basis no longer looks like an HV insulation set-up.
