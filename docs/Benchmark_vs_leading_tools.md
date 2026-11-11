# pe-forge against the leading tools (2026-11-11)

What the strongest commercial and AI-first tools do that bears on "no surprise when the boards arrive", and where
pe-forge stands after the 2026-11-11 independent review and fix pass. Claims about other tools come from their
public material (sources at the end); none were run side by side.

## 1. The field

| Tool | What it is | Strength that matters here |
|---|---|---|
| Altium Designer / 365 | the professional CAD standard | rule sets with batch DRC and reports, Z-axis clearance, ActiveBOM with live supply data, a Stress Analysis tool (max V / I / power per part) listed as upcoming, Draftsman assembly drawings, MCAD co-design |
| Cadence Allegro X / OrCAD X | enterprise CAD with analysis in the canvas | in-design SI / PI (Sigrity: IR drop, impedance, return path), thermal and EM in one tool, fab-partner DFM rule import, constraint manager |
| Siemens Xpedition + Valor NPI | enterprise CAD + the DFM reference | Valor NPI: the deepest manufacturability analysis (fab, assembly, test), used by fabs themselves |
| Flux.ai | browser CAD with an AI Copilot | schematic / part-selection help, auto-layout for simple boards, collaboration; credits-metered AI |
| Quilter | autonomous layout | placement and routing of real boards (a 843-part, 8-layer i.MX8 computer booted), physics-aware; the user still owns schematic and review |
| JITX | design as code (Python) | requirements and rules in code, systematic change propagation, reuse; hands off to CAD for final layout |
| PLECS / SIMetrix / LTspice + vendor tools | power-stage simulation | loss and thermal models, control-loop analysis, device libraries |
| KiCad 10 | the open CAD pe-forge drives | ERC / DRC with custom rules (.kicad_dru), netlist and pos exports, Python API |

## 2. Where pe-forge is at least as strong

- **The process is enforced, not advised.** Gates with automated checks and independent reviews bound to file hashes;
  owner-only overrides, recorded, never at board or release; a sketch never ships. No CAD tool enforces who may pass
  a gate or ties a review to content; PLM systems do, at far higher cost.
- **Derating against the operating corners from the netlist** (`forge stress`): comparable to Altium's upcoming
  Stress Analysis, already running, with the gaps reported (a part with no rating is not a pass).
- **Simulation evidence:** provenance (versioned model, toolchain hash) on every number, closed forms recomputed by
  the tool, a required set per converter type (stability, start-up, short). Simulation tools report numbers; none of
  the CAD tools ask where a number came from.
- **Supply chain at the gate:** live Mouser / LCSC / DigiKey pricing and stock, alternates per line, a risk score
  (SC-1..9) that blocks G4 / G8, stale snapshots refused. Equal to ActiveBOM in coverage, stricter at the gate.
- **High-power rule book** (~130 rules, measured where the geometry allows: switch-node area and edge distance,
  thermal vias, HF loop distance, overshoot budget, copper weights, insulation classes with a generated DRU proof
  board). No general CAD tool ships power-electronics layout rules; this is the kind of content Valor sells per
  industry.
- **Data safety for a customer's own files:** quick checks on a copy, adoption without a changed byte, a write guard,
  transactional edits with rollback. Cloud CAD tools version; none promise not to touch a file.
- **Traceability:** requirements → derived → verification with IDs that link (V-model), change requests after
  baseline, a learning loop from waivers and escaped findings.

## 3. Where the leading tools are ahead (and what pe-forge does about it)

| Capability | Best in class | pe-forge today | Gap closed by |
|---|---|---|---|
| Manufacturability analysis | Valor NPI (hundreds of fab / assembly / test checks, per-fab profiles) | KiCad DRC + fab-house capability tables (JLCPCB, PCBWay), silk / mask / edge rules, thermal vias, test-point access, panel | add more houses and their assembly rules; import a fab's DRU when offered |
| In-canvas power integrity (IR drop, current density) | Cadence Sigrity, Altium PDN Analyzer | PCB-PWRTRACE width vs net class; copper coverage and plane integrity; no current-density solve | a current-density estimate per net from track geometry (next) |
| Autonomous layout | Quilter | placement / zones / routing from tables, by rule, reviewed; not autonomous | not a goal: the engineer owns the layout, pe-forge verifies it |
| 3D / MCAD, enclosure fit | Altium, Cadence | outline, holes, connector face on the edge; no 3D clearance | KiCad 3D export in the release; height check from footprint models (next) |
| Thermal simulation | Cadence Celsius, Ansys | temperature-range check with a rise margin; losses via pwrlab | pwrlab heatsink sizing in the required simulation set |
| Component library governance | Altium 365 managed components | MC_Parts with status, evidence (datasheet hash), Verified_Pads, sourcing snapshots | pad-geometry fields filled per part (a library gate) |
| Signal integrity | Cadence, Altium | orthogonal layers, crosstalk pairs; no impedance solve | out of scope for power boards below 100 MHz; note it |

## 4. The "boards arrive" checklist after the fix pass

| Item | Was | Now |
|---|---|---|
| Unrouted nets / parity at G7 | passed | fail |
| Gate override | any shell, no record | owner, reason, QC row, never G7 / G8 |
| Skipped check at release | pass | fail |
| ERC / DRC warnings | dropped | findings (strict: fail) |
| Derating vs corners | nothing | STR-1..7 |
| Pin 1 / polarity / rotation | nothing | FP-2 / FP-3 |
| Symbol pin ↔ pad | nothing | FP-1 |
| Land pattern vs datasheet | pad count | + pad size (FP-4) |
| Simulation evidence | regex for "SVR" | provenance, recomputed closed forms, required set |
| Multi-board | first board | every board |
| Reviews | a table row | bound to hashes |
| Fab-house rules, panel, test points | none | `forge fab rules / panel / testpoints` |
| Stale stock | date shown | > 14 days fails G8 |
| Process drift (board before G6) | allowed | refused unless a declared sketch; a sketch never ships |

Still partial (named, not hidden): enclosure / height fit, current density, loop stability is required but
computed by the simulator not by pe-forge, reverse-polarity and inrush are required simulations rather than
netlist checks, assembly rotation table verified per package family only on the first order.

## Sources

- Altium: [What's new / coming soon](https://altium.com/altium-designer/coming-soon), [Improvements in beta (rule sets, Z-axis clearance)](https://www.altium.com/documentation/altium-designer/improvements-beta)
- Cadence: [Allegro X datasheet](https://cadmicro.com/wp-content/uploads/allegro-x-datasheet.pdf), [OrCAD X](https://www.goengineer.com/en-us/cadence/orcad-x), [Design for reliability with OrCAD X](https://resources.pcb.cadence.com/home/design-for-reliability-tools-with-orcad-x-cadence)
- AI-first tools: [JLCPCB: AI tools for PCB design](https://jlcpcb.com/blog/ai-tools-for-pcb-design), [Quilter: the AI PCB tool landscape in 2026](https://www.quilter.ai/blog/the-ai-pcb-tool-landscape-in-2026-from-hobbyist-co-pilots-to-professional-automation), [Quilter vs JITX vs Celus](https://www.quilter.ai/blog/a-2026-ranking-of-ai-first-pcb-design-platforms-quilter-vs-jitx-vs-celus), [Flux EDA](https://electronicsforu.com/technology-trends/flux-eda-combines-pcb-design-and-ai)
