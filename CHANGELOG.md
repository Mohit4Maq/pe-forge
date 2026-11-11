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
