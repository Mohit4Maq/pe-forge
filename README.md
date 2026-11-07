# pe-forge

**Power-electronics product development with Claude Code, from idea to manufacturing files.**

You describe the product ("48 V to 12 V, 1 kW bidirectional DC-DC for e-bikes"). A supervisor agent takes it
through a gated engineering process: it asks you the right questions, sets up the project, surveys the market,
finds and reads the papers and reference designs, writes and traces the requirements, plans the circuits, lets
you choose topologies and parts by multiple choice, sizes and sources a BoM with alternates, runs verified
LTspice simulations, verifies every symbol and footprint against its datasheet, draws (or helps you draw) a
readable KiCad schematic, supports the layout and produces the Gerbers, BoM and pick-and-place files.

Every number is benchmarked, and every phase ends in a gate with an independent reviewer. Large projects are
split into modules, and each module is designed and verified as a small project before the next one starts.

```
 P0 Intake ─► P1 Foundation ─► P2 Requirements ─► P3 Architecture ─┐
 questions     market survey     SYS → HW/SW/CTL    topology trade   │  split into modules
 folders       papers + notes    V-model trace      your MCQ choice  │
 project.toml  reference designs module breakdown   ADRs / council   ▼
                standards                                ┌─ per module: P2 → P3 → P4 → P5 → P6 ─┐
                                                         │  reqs  topology BoM  sim   schematic  │
                                                         └─────────── gate after each ──────────┘
                                         P7 PCB layout ─► P8 Release (Gerbers, drill, BoM, CPL, test plan)
```

## What's inside

| Part | What it is |
|---|---|
| `skills/pe-run` | **Supervisor**: reads state, picks the next unblocked step, delegates, runs gates, briefs you |
| `skills/pe-new` … `pe-release` | One skill per phase (intake, foundation, requirements, architecture, BoM, simulate, schematic, PCB, release) |
| `skills/council` | Decision council: 2–5 role agents write independent memos, cross-examine, the chair recommends; you decide |
| `agents/` | Role agents: PM, systems, hardware, firmware, supply chain, finance, compliance, reliability, validation, researcher, schematic drafter; independent reviewers (design, schematic, power, PCB) and a part librarian |
| `forge/` | Standard-library Python engine: scaffolding, phase / module / gate state, gate checks, V-model traceability, ID linking, change propagation, edit watch, token meter, **schematic drawing engine** |
| `vendor/pwrlab` | LTspice simulator + MCP server: buck, DAB, H-bridge, T-type / NPC, totem-pole PFC; every result cross-checked against closed form, with timestep, steady-state and power-balance checks |
| `library/` | KiCad 10 parts library: SQLite database library (~570 parts with stock / price fields), symbols, footprints, project template with a release job, library and check tools |
| `skills/kicad*`, `falstad-circuit` | KiCad work, live co-pilot, part placement, interactive Falstad circuits |
| `hooks/` | Reports your manual edits every turn; after each edit records what must be re-checked (ERC, DRC, drift, BoM, V-model, changelog) and blocks finishing while any obligation is open or a superseded part or value is still mentioned (`forge ledger show`) |

## Install

Requirements: **Claude Code**, **Python 3.11+**, **git**. For the full flow: **KiCad 10**, **LTspice** (on Linux
under Wine), the **SQLite ODBC driver** (for the KiCad database library). Optional: ngspice, PyMuPDF, a Mouser /
DigiKey API key.

```sh
git clone https://github.com/Mohit4Maq/pe-forge.git ~/pe-forge
cd ~/pe-forge && ./bootstrap.sh
```

`bootstrap.sh` asks before each change. It builds a venv with pwrlab, puts `forge` on your PATH, sets
`PE_FORGE` / `MC_LIB` for Claude Code, registers the pwrlab MCP server, installs the plugin, and (optionally)
registers the library in KiCad. `forge doctor` shows what is available. A missing tool disables only the features
that need it.

Plugin only (without bootstrap): in Claude Code run `/plugin marketplace add Mohit4Maq/pe-forge`, then
`/plugin install pe-forge@pe-forge`. Simulation and library features still need `./bootstrap.sh`.

## Use

```text
mkdir my-converter && cd my-converter && claude
> /pe-new  I want a 3 kW bidirectional 48 V ↔ 400 V DC-DC for a home battery
```

The intake asks up to three rounds of multiple-choice questions: type, ratings and ranges, isolation, volume and
cost, environment, market and standards, device preference, **who draws the schematics**, and review strictness.
It then creates the project:

```
00_Inbox  01_Program (plan, QC register, decisions)  02_Requirements (SYS, V-model, compliance)
03_Foundation (market, literature, reference designs)  04_Hardware (architecture, BoM, booklets, briefs)
05_Firmware  06_Simulations  07_Mechanical  08_PCB (KiCad project)  09_Release  12_Reliability
13_Validation  90_Templates  modules/M-xx_*  project.toml  CLAUDE.md  .forge/state.json
```

Then run `/pe-run` (or just "continue") to move through the phases. `forge status` shows where you are:

```
Demo48  ·  profile full  ·  schematic mode agent
  ✓ P0 Intake and project set-up  [G0 passed 2026-11-04]
  ▶ P1 Foundation: market survey, literature, reference designs, standards
Modules:
  · M-01 Aux supply                   P2· P3· P4· P5· P6·
  · M-02 Power stage                  P2· P3· P4· P5· P6·  depends M-01
Next: work P1 and pass its gate  (skill pe-foundation, gate G1)
```

## Schematics: you draw, or the agents draw

Set at intake (`process.schematic_mode` = `user`, `agent` or `ask` per sheet). In both modes nothing is drawn
until the module's **design booklet** and **sheet brief** exist: every part chosen, every symbol pin and footprint
land pattern checked against the latest datasheet, every value calculated and benchmarked, and the full netlist
written down.

- **You draw**: the agents write the brief, pre-place parts on request so wires are short and straight, answer
  questions, then check your sheet (ERC, netlist against the brief, reviewers).
- **Agents draw**: `pe-schematic-drafter` writes a drawing spec and `forge schdraw` turns it into a real KiCad 10
  sheet: real orthogonal wires, T-junctions, labels on stubs, ground down, supplies up, signal flow left to right.
  It checks 16 style rules (grid, no diagonals, no wire over a pin or through a body, no four-way junctions, no
  overlapping text, measured on the text KiCad actually renders, …). It checks connectivity **twice**: its own
  geometry and KiCad's exported netlist must both match the brief exactly. It runs ERC and renders the sheet for
  visual inspection. A sheet is written only after a clean check. See
  [`skills/pe-schematic/references/style-guide.md`](skills/pe-schematic/references/style-guide.md).

## The rules every project gets (`CLAUDE.md`)

1. **Benchmark every result** against a reference design, paper, datasheet or closed form before it is used;
   every simulation carries a Simulation Verification Record (anchor case, convergence, energy closure, corners).
2. **Independent review at every gate**, graded T1–T4 by the reviewer itself; each simulated result is ACCEPTED /
   PROVISIONAL / REJECTED; a QC register tracks every review.
3. **Every part has an alternate**, with price, stock and MOQ for both.
4. **Propagate every change**: no superseded part or value left in any document (a hook enforces it).
5. **Your manual edits are design intent**: they are detected each turn, propagated and never reverted.
6. Schematic discipline (above). 7. One master plan. 8. Big decisions go to the council. 9. V-model traceability
   and standards checks for every requirement. 10. Never without you: spending, orders, messages, git push.

`forge drift` holds the documents to the drawing: every BoM line, brief and booklet parts row and brief netlist
is compared with the schematic KiCad exports, and each disagreement is reported as `{doc, claim, actual}` (for
example `BOM.csv: R5 = 10k, actual R5 = 4.7k in power.kicad_sch`). Values compare by meaning (`4u7` = `4.7 µF`).

`forge check --fab --strict` answers "can I order this board today?": routing complete, schematic and board
match, every placed part released with an alternate, the outputs still match the design by content hash, every
Gerber layer, drill and pick-and-place file present, power layout rules, release documents filled in. Gate G8
runs it.

The BoM tool writes DigiKey and Mouser upload files with build quantity and spares, and JLCPCB BoM + CPL files
(`bom.py --jlcpcb`, LCSC numbers and per-part rotation offsets from the parts database).

Design edits that span several files run as transactions (`forge txn begin / verify / commit / rollback`): a
rollback restores the tree byte for byte, including your own uncommitted work from before the transaction, and
keeps the failed attempt as a named `git stash` entry. `forge schdraw apply` always runs inside one.

After a module passes G6 (and the board G7) its design files are under change control: edits are refused
until you approve a change request (`forge cr new / validate`, then you run `forge cr approve`). Agents cannot
approve one.

Profiles: `full` (all of the above) or `lite` (one reviewer per gate, same automated checks) for small
low-voltage boards. Lite projects get warnings instead of blocks from change control (`process.change_control`).

## Command line

```
forge new <dir> --answers intake.json      forge status
forge module add NAME [--depends M-01]     forge gate check|pass G<n> [--module M-xx]
forge vmodel build|check|impact|diff|cr|sync   forge link [--apply]   forge consistency
forge schdraw check|apply|pins|new         forge bom check   forge doctor   forge kicad-setup [--apply]
forge check [--json] [--strict] [--fab] [--only erc,drc,...]   forge hooks install [--chain] | --uninstall
```

`forge check` is one deterministic command (no model, no network, seconds) over the whole project: ERC, DRC with
schematic parity, superseded terms, V-model links, BoM completeness, drift and the library checks. Each part
reports pass / warn / fail / skip; a skipped check says why and is never counted as passed. New projects get a
git pre-commit hook that runs the fast subset, and a GitHub Actions workflow (`.github/workflows/forge-check.yml`)
that installs KiCad 10 and uploads the JSON report.

## Status and limits

v0.1. The process, engine, drawing engine and library are in daily use on a real 11 kW product. ERC / DRC are read
from KiCad's JSON reports through one layer (`forge/kicad.py`; set `KICAD_CLI` if KiCad is not in a standard place).
Known limits:
- pwrlab covers buck, DAB, H-bridge, T-type / NPC and totem-pole PFC; other topologies are hand-built netlists
  with the same checks done by hand (the skill says so).
- `forge schdraw` draws new sheets or adds to a sheet. It does not yet rearrange parts already placed.
- The bundled library is small. Vendor-generated symbols and footprints cannot be redistributed (see
  `library/EXCLUDED.md`); the part-librarian agent builds the rest from manufacturers' files.
- Paywalled papers are listed for you to fetch; the researcher reads only what it can open.
- Engineering judgement stays with you. Verify footprints, ratings and safety margins before ordering anything.

## Licence

Code: [MIT](LICENSE). Library assets: CC-BY-SA 4.0 with the KiCad design exception ([library/LICENSE.md](library/LICENSE.md)).
Third-party tools: [THIRD_PARTY.md](THIRD_PARTY.md).
