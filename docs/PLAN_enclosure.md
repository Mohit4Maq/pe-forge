# pe-forge enclosure chain: plan (2026-11-11)

Goal: from a finished pe-forge board (KiCad project, BoM, the project's specs) to an enclosure that is **drawn from
the board**, **checked for heat, strength and ingress against the owner's requirement**, with the connectors and
cable entries chosen as part of it, and **manufacturing files** for the chosen process (3D print, CNC, die-cast /
moulded, or an off-the-shelf box machined to fit). Same rules as the rest of the tool: every number anchored,
every check deterministic and repeatable, nothing that could not run reported as a pass, the owner decides.

## 1. Tool choice

| Need | Chosen | Why, and what was rejected |
|---|---|---|
| Parametric CAD, scripted, STEP in and out | **build123d** (Python, OCCT kernel; CadQuery-compatible) | code-first like the rest of pe-forge; reads the KiCad STEP of the board (`kicad-cli pcb export step`, in KiCad 10 already, verified), writes STEP / STL / 3MF / DXF; runs headless in CI; MIT. FreeCAD is heavier and GUI-centric (kept as the viewer / FEM front end); OpenSCAD is mesh-only (no STEP, no fillets on real solids) |
| Enclosure building blocks | **cq_enclosure_builder** (screwable lids, panels, layout) and **cq-enclosure** (gasketed IP66 box, lid, gasket as STEP) as references; pe-forge keeps its own small generator | neither reads a board nor checks anything; they are patterns to borrow, not dependencies |
| Mesh | **Gmsh** (Python API) | the standard open mesher; tets for FEA, polyhedra via snappyHexMesh for CFD |
| Thermal, level 1 (always) | **closed form**: natural-convection and radiation correlations per face (Churchill–Chu, Fujii–Imura), conduction through walls / heatsink / potting, hotspot ΔT from each part's loss | seconds, no install; the anchor every simulation must agree with |
| Thermal, level 2 (conduction + surface convection) | **CalculiX** (ccx) via Gmsh, driven from Python; also usable through FreeCAD's FEM workbench | Antmicro's open thermal flow uses exactly this; free, scriptable, steady state in minutes |
| Thermal, level 3 (sealed box, internal air, fans) | **OpenFOAM** `chtMultiRegionFoam` / buoyant CHT, from Antmicro's cfd-simulation-scripts | needed for IP-rated sealed boxes where the inside air carries the heat to the walls; hours; run only when level 2 cannot answer |
| Structural | **CalculiX** linear static: lid deflection under gasket load, wall stress at the mounting points, drop / vibration first mode (IEC 60068-2-27 / -6 as the test basis); fastener and insert pull-out by closed form | same solver as thermal, one mesh |
| Ingress (IP) | **rules, not simulation**: IEC 60529 digits → gasket compression window, groove geometry, flatness, fastener pitch, vent / pressure-equalisation membrane, every penetration rated (connector, gland, membrane, screws through the wall) | ingress is a design-rule discipline; the proof is the test (IPX7 1 m / 30 min); pe-forge checks the rules and writes the test plan |
| Viewing / review | FreeCAD (STEP), ParaView (results), a rendered PNG per view in the review form | the reviewer sees the same pictures the owner does |
| Printing / machining | 3MF / STL for the printer (Cura is installed), STEP + DXF + drawing PDF for CNC and for the box vendor's machining form (Hammond / Takachi / Bud / Phoenix) | |

What is on this Mac today: KiCad 10.0.6 with STEP export, Cura. Not installed: build123d, Gmsh, CalculiX, OpenFOAM,
FreeCAD. All are `pip` / Homebrew installs (`pip install build123d gmsh`, `brew install calculix-ccx`, FreeCAD app;
OpenFOAM via Docker). `forge doctor` gets a row for each; a missing solver means the level is SKIP, never PASS.

## 2. The chain (what `forge enclosure` will do)

```
project.toml [enclosure]  +  board STEP + pos + outline  +  BoM (connectors, heat sources)  +  [specs]/[corners]
        │
   E1 requirements  ──►  E2 envelope  ──►  E3 connectors / entries  ──►  E4 generate CAD  ──►  E5 checks  ──►  E6 review  ──►  E7 release
```

**E1 Requirements (`forge enclosure req`)**: from the owner, in `[enclosure]`: IP code (IEC 60529), ambient range
(from `[corners]`), mounting (DIN rail / wall / panel / handheld), material class (PC / ABS / PA-GF / aluminium
die-cast / sheet), process (print / CNC / mould / off-the-shelf), flammability (UL 94 V-0 where the product
standard asks), impact (IK code, IEC 62262), UV / outdoor, earthing (a metal box needs a PE stud), max size and
mass, cost target. Written as SYS rows (`ENC-xx`) into the V-model with verification methods (IPX7 test, thermal
soak, drop). Missing answers block E4, exactly like G2.

**E2 Envelope (`forge enclosure fit`)**: reads the board outline, the mounting holes, each part's height (from the
footprint 3D model via the STEP, else the MC_Parts `Height_mm` field, else "unknown: not a pass"), keep-outs
(switch node, HV creepage to the wall, antenna), connector faces on the edge (Rule 13 data already known),
heatsink / potting zones, test-point access. Produces the inner envelope with the clearances from the material
rules (wall thickness by process and size; draft angles for moulding) and the standoff / boss pattern.

**E3 Connectors and entries (`forge enclosure entries`)**: every signal that leaves the box is listed from the
schematic's edge connectors. For each: the connector on the board, the panel part or gland that seals it (M12 /
M8 circular, Phoenix / Harting rectangular, cable gland by cable diameter, a potted lead), its IP rating *when mated*
and the cap for the unmated state, its current / voltage rating vs the net (`forge stress` rules apply), creepage
through the wall, the panel cut-out from the datasheet drawing (Rule 11: datasheet-final), its wall-thickness
range. A box's IP rating is the minimum over its entries; a vent membrane counts as an entry. The BoM gets the
mechanical lines (enclosure, gasket, inserts, fasteners, glands, membrane, PE stud) with price, stock and
alternates like every other line.

**E4 Generate (`forge enclosure build`)**: build123d script from the envelope and entries: shell, lid, gasket
groove to the gasket vendor's compression window, bosses with inserts, PCB standoffs, connector cut-outs, vent,
ribs where E5 asks, PE stud, labels recessed, drip edge for outdoor. Off-the-shelf mode: pick from a catalogue of
vendor boxes (Hammond 1554/1555 IP67, Takachi, Phoenix UCS, Bud, Bopla) by envelope and IP, and generate only the
machining (cut-outs, holes) and the insert plate. Outputs STEP (assembly: board inside the box), STL/3MF, DXF
flat patterns, a drawing PDF with critical dimensions and the IP-relevant notes.

**E5 Checks (`forge enclosure check`, every level reported separately)**
- **Fit (deterministic)**: no part / wall interference, clearance ≥ rule, every connector face flush with its
  cut-out, screw access, lid opens, cable bend radius inside, creepage HV→metal wall, PE continuity path.
- **Thermal L1 (closed form, always)**: total loss from the BoM / simulation results (the same `*.result.json`
  losses pe-forge already holds), box surface area and emissivity, orientation → ΔT_box; per hot part the extra
  ΔT through its path (pad → via → plane → wall or heatsink) → T_j vs the derated limit at the ambient corner
  (`forge stress` STR-6 becomes the real rise, not a 20 K guess). Anchor: a vendor box's published
  dissipation curve, or a measured enclosure of the same class.
- **Thermal L2 (CalculiX)**: the same loads on the mesh, must agree with L1 within tolerance (SVR rules); gives
  the hotspot map and tells whether ribs / fins / a potting change is needed.
- **Thermal L3 (OpenFOAM CHT)**: sealed IP boxes with a trapped-air gap or forced air; run on request or when
  L2's wall-only model cannot meet the margin.
- **Structural (CalculiX + closed form)**: lid deflection at the gasket under fastener preload (compression stays
  in the window along the whole perimeter), stress at mounting ears under the IK / drop load, first mode > 50 Hz
  for vibration, insert pull-out and screw torque vs plastic.
- **Ingress (rules)**: gasket compression window at tolerance extremes (min / max stack), groove fill ratio,
  flatness vs span, fastener pitch vs gasket hardness, every entry rated ≥ the box IP, unmated caps listed,
  membrane sized for the temperature swing (pressure differential vs gasket lift), drain / drip features for
  IPX4+ outdoor, no screw through a wet wall without its own seal. Writes the IP test plan (IEC 60529 clauses,
  pass criteria, sample count) into the DVP&R.
- **Manufacturability**: wall thickness vs process, draft, overhangs for printing, min hole for CNC, insert bosses;
  cost estimate (material volume, machine time) against the target.
- A level that cannot run (solver missing, no 3D model for a part) is SKIP and blocks the gate like every other skip.

**E6 Review**: `pe-mechanical` (new role agent) reviews the drawing, the thermal SVR, the structural results and
the IP rule table cold, with the same QC register row and hash binding (`forge review record`); `pe-compliance`
joins when the product standard sets enclosure clauses (IEC 62368-1 / 62109 openings, fire enclosure, earthing).

**E7 Release (`forge enclosure release`)**: STEP + STL/3MF + DXF + drawing PDF + the mechanical BoM lines + the IP
and thermal test plans into `09_Release/<rev>/mechanical/`, hashed into the manifest like the Gerbers; G8 refuses
a product release without a passed enclosure gate when `[enclosure] required = true`.

## 3. Where connectors change everything (the coupling the chain enforces)

- The **IP rating is set by the weakest entry**: a board-mount USB-C or a plain pluggable terminal block caps the box
  at IP20 however good the gasket is; E3 forces a sealed panel part, a gland, or a potted lead before E4 runs.
- **Connector face position** (Rule 13 data) fixes the wall the cut-out sits in and the box orientation for drip.
- **Current through the entry**: the gland / circular connector pin rating and the cable gauge it accepts; a 15 A
  net through an M8 is refused by `forge stress`.
- **Creepage through the wall** at HV: the panel connector's own creepage and the wall thickness it is rated for.
- **Thermal**: a sealed box has no airflow, so the losses must leave through the walls; a connector-limited IP
  choice directly sets how much the box can dissipate, which L1 shows the moment the IP code is entered.
- **Service**: the lid must open without unplugging the sealed connectors; test points reachable through a port
  or a removable plug for IP boxes.

## 4. Repositories to use (checked 2026-11-11, all reachable)

| Repo | Use | Licence to verify |
|---|---|---|
| gumyr/build123d, CadQuery/cadquery | the CAD kernel wrappers | Apache-2.0 |
| raphael-isvelin/cq_enclosure_builder | screwable-lid / panel patterns | check |
| cq-enclosure (PyPI) | gasketed IP66 box, lid, gasket STEP | check |
| diorcety/TestBenchGenerator | PCB-outline-driven box from SVG; test fixture idea for E7 | check |
| antmicro/cfd-simulation-scripts | OpenFOAM CHT scripts (level 3) | Apache-2.0 |
| Antmicro thermal-simulation flow (CalculiX + ParaView + Blender; blog 2025-03 / 2025-10) | level 2 pattern | find the repo link in the post |
| KiCad `kicad-cli pcb export step` | the board STEP (components, with `--subst-models`) | GPL tool, output ours |

Nothing found that does the checks (fit / thermal / IP / structural) from a KiCad board; that is pe-forge's part.

## 5. Steps and order

| Step | What | Done when |
|---|---|---|
| M0 | `[enclosure]` schema, ENC requirements into the V-model, `forge doctor` rows for build123d / Gmsh / ccx / OpenFOAM / FreeCAD; `MC_Parts Height_mm` and 3D-model evidence per part | `forge enclosure req` blocks on missing answers; parts without a height are listed |
| M1 | E2 envelope from the board STEP + pos; fit check; one page of rendered views | pilot envelope correct to 0.1 mm against KiCad's 3D view |
| M2 | E3 entries: connector / gland / membrane catalogue in MC_Parts (IP when mated, cut-out, wall range, ratings), the IP-by-weakest-entry rule, mechanical BoM lines | pilot gets a sealed entry set for IP65 and the BoM lines priced |
| M3 | E4 generator: printed box first (pilot), gasket groove rules, inserts, standoffs; STEP / 3MF / DXF / drawing | the pilot box prints on the Cura printer and the board fits |
| M4 | Thermal L1 closed form + anchor; `forge stress` STR-6 uses it | pilot ΔT within 15 % of a measured box (bench) |
| M5 | Thermal L2 CalculiX (Gmsh mesh, SVR against L1); structural (lid, ears, first mode) | pilot runs in < 10 min; agrees with L1 |
| M6 | Ingress rules + IP test plan into the DVP&R; `pe-mechanical` reviewer; gate E (GE) with hash-bound review | pilot IP65 rule table all green; the test plan written |
| M7 | Off-the-shelf box catalogue and machining mode; CNC / mould DFM rules | one vendor box machined from the generated DXF |
| M8 | Thermal L3 OpenFOAM for sealed boxes; vibration | an IronTop-class sealed box case runs on the Mac (Docker) |
| M9 | Release into `09_Release/<rev>/mechanical/`, manifest, G8 coupling | `forge check --fab` includes the enclosure |

M0–M4 give a usable chain for the pilot (a printed IP54/IP65 box with checked heat and fit); M5–M6 make it
reviewable like the rest; M7–M9 finish it for products. Every step lands with tests on Mac and Windows.

## 6. Decisions for the owner

1. **First target**: the Buck48to12 pilot in a printed box (IP54 indoor) vs an IP65 off-the-shelf aluminium box with
   glands. Recommendation: printed first (M1–M4 verifiable on the desk), aluminium second (M7).
2. **Solvers on the Mac**: install build123d + Gmsh + CalculiX now (pip / brew, ~10 min); OpenFOAM later via Docker.
3. **Mechanical reviewer**: a new `pe-mechanical` agent (recommended) vs widening `pe-reviewer`.
4. **Height data**: allow footprints' 3D models as the height source (fast, present for most parts) with
   MC_Parts `Height_mm` as the override and the gap list as the gate.
