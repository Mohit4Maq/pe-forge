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
| `bin/forge-mcp` | MCP server for any MCP host (Claude Desktop, Cursor, Codex, other agents): status, check, explain, gate check, drift, schematic check / apply, change requests. No generic file-edit tool, no gate pass, no CR approval, so a host cannot route around the gates |
| `hooks/` | Reports your manual edits every turn; after each edit records what must be re-checked (ERC, DRC, drift, BoM, V-model, changelog) and blocks finishing while any obligation is open or a superseded part or value is still mentioned (`forge ledger show`) |

## Install

### On a Mac: one command

```sh
curl -fsSL https://raw.githubusercontent.com/Mohit4Maq/pe-forge/main/install.sh | bash
```

It checks the Mac and installs only what is missing, asking first: Apple's command-line tools, Homebrew,
Python 3.11+, ngspice, the SQLite ODBC driver, **KiCad 10**, **LTspice**, **Obsidian** and **Claude Code**. Anything
you already have, however you installed it, is kept. Then it downloads pe-forge to `~/pe-forge` and sets it up
(Python venv with pwrlab, `forge` on your PATH, the Claude Code plugins `pe-forge` and `pe-forge-panel`, the MCP
servers, the KiCad parts library). Last, it asks:

1. **Start a fresh project**: a new folder, set up as an Obsidian vault and opened in Obsidian; Claude Code then
   starts there and runs the intake (`/pe-forge:pe-new`).
2. **Reuse an existing pe-forge project**: adds the vault settings, the status line and the commit check, opens it
   in Obsidian and in Claude Code (`/pe-forge:pe-run`).
3. Not now.

### Help on a board first, the process later

Open Claude Code in a KiCad folder that is not a pe-forge project and ask "help me with this board": pe-forge
notices (session start), Claude answers first and backs it with **`forge quick`**: ERC, DRC, the library and layout
checks, protection, insulation and the High-Power PCB Rule Book, run **on a copy** of the KiCad files (your folder is
never written to, checked by hash), findings ranked SAFETY > errors > warnings, checks that could not run named.
No set-up, no questions first. Claude offers once, afterwards, to keep track of the board (`/pe-adopt`).

The process comes in three levels (`forge level`, project.toml `[process] level`):

| Level | When | Enforcement |
|---|---|---|
| light | after `forge adopt` | every check runs and is reported as advice; nothing blocks; **no release** (G8 refuses) |
| standard | new projects (default) | open obligations block the end of a turn; gates as written; G8 needs the requirements and V-model (P2 passed, not skipped) |
| strict | on request | standard, plus warnings fail `forge check`, gates take no `--override` |

Raising the level is always allowed; lowering it is the owner's, typed in a terminal (Claude cannot). Before a
release, `forge req infer` drafts the system requirements from the design (rails and the voltage the parts take,
output current, protection, isolation, each with its evidence; efficiency, ambient, standards and life left to the
owner). The owner fills the blanks and confirms; `forge req infer --apply` refuses rows with blanks.

### A project that already has work in it

`forge adopt <folder>` brings a half-done project under pe-forge **without changing any of its files**. Run it once
to see the plan (nothing is written), then `forge adopt <folder> --apply`:
- your KiCad files stay where they are: `project.toml [paths]` points pe-forge at them (nothing is moved);
- the project starts at the phase the work is in (a drawn schematic: P6; a board with parts: P7); earlier phases
  are marked skipped, not passed, so their gates stay yours to pass;
- pe-forge's folders and files are only added; where you already have one (your `CLAUDE.md`, a plan) yours is
  kept and pe-forge's goes beside it as `CLAUDE.forge-proposed.md` for you to merge;
- every existing file is hashed before and after and must be byte-for-byte unchanged;
- `forge adopt --undo` removes exactly what was added (a file you edited since is kept).

**The write guard.** Everywhere else too, pe-forge never overwrites a file it did not write. It records what it
generates (`.forge/generated.json`); a file you wrote, or one of its files you edited since, is left alone and the new
version goes beside it (`WIP_BOM.forge-proposed.csv`). `forge guard status` lists both; `forge guard accept <path>`
takes a proposed version (your previous one is backed up in `.forge/backup/`); `--replace` on a command overwrites
after a backup. `forge pcb setup` never replaces a board outline it did not draw (`--replace-outline` to allow
it), never changes the stack-up unless `[pcb] copper_oz` asks for it, and never removes copper layers that carry
copper.

Afterwards: `forge update` updates everything (tools, apps, Claude Code, pe-forge, its plugins); `forge doctor` shows
what works; `./install.sh --check` reports without changing anything. If Claude Code has never run on the Mac, it
asks you to log in the first time (Claude Pro, Max or an API key).

**Obsidian is the document manager.** Every pe-forge project folder is also an Obsidian vault: the requirements,
plan, booklets, reviews and V-model notes link to each other with `[[wikilinks]]`, and the tool folders (`.forge/`,
KiCad backups, release outputs) are hidden from search and the graph. `forge vault open` opens a project in Obsidian
as its own vault, by the vault's id, never inside an enclosing vault (for example one set on your whole home folder,
which it warns about). `--file <note>` opens a given note; `--restart` quits a running Obsidian first so a new
folder can be registered.

### Linux, or by hand

Requirements: **Claude Code**, **Python 3.11+**, **git**. For the full flow: **KiCad 10**, **LTspice** (on Linux
under Wine), the **SQLite ODBC driver**. Optional: ngspice, Obsidian, a Mouser / DigiKey API key.

```sh
git clone https://github.com/Mohit4Maq/pe-forge.git ~/pe-forge
cd ~/pe-forge && ./bootstrap.sh
```

`bootstrap.sh` asks before each change. A missing tool disables only the features that need it (`forge doctor`).

**Windows (WSL2).** Install Ubuntu with `wsl --install -d Ubuntu-24.04` and follow the Linux steps inside it; keep
projects in the Linux home, not under `/mnt/c`. For simulations, install **LTspice for Windows** (analog.com) and
let pwrlab call it from WSL: it finds `/mnt/c/Program Files/ADI/LTspice/LTspice.exe` and passes Windows paths. Put
its work folder on the Windows drive: `export PWRLAB_WORKROOT=/mnt/c/Users/<you>/pwrlab-work`. Native Windows
support is planned in `docs/PLAN_windows.md`.

## Use

```text
mkdir my-converter && cd my-converter && claude
> /pe-forge:pe-new  I want a 3 kW bidirectional 48 V ↔ 400 V DC-DC for a home battery
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

Then run `/pe-forge:pe-run` (or just "continue") to move through the phases. `forge status` shows where you are:

```
Demo48  ·  profile full  ·  schematic mode agent
  ✓ P0 Intake and project set-up  [G0 passed 2026-11-04]
  ▶ P1 Foundation: market survey, literature, reference designs, standards
Modules:
  · M-01 Aux supply                   P2· P3· P4· P5· P6·
  · M-02 Power stage                  P2· P3· P4· P5· P6·  depends M-01
Next: work P1 and pass its gate  (skill pe-foundation, gate G1)
```

## Your terminal

Inside a pe-forge project, Claude Code shows the pe-forge bar at the bottom of the screen, starting with the
MaqLabs mark `maq` in the accent blue (#4245f5) (`forge status --banner` shows the full MaqLabs wordmark):

```
maq ▸ Buck48to12 · P6 Schematic · M-02 Power stage · G6 ? review · ⚠ 2 open: erc, drift · check ✓ 14:02 · ctx 41% · Opus · main
```

Project, phase, module, gate, open obligations, an open transaction, change control, the last `forge check`, the
token meter, context use, model and branch. Segments that do not fit the width are dropped whole, lowest priority
first. Session start and each finished turn also print one short `pe-forge ▸` line.

- `forge new` installs it for that project on this machine (`.claude/settings.local.json`); add it to an existing
  project with `forge statusline install`, remove it with `forge statusline install --uninstall`. Restart Claude
  Code (or open `/statusline`) to see it.
- Options in `project.toml [ui]`: `mark = "<text>"` (the bar's marker; `""` removes it), `logo = false`,
  `accent = "#RRGGBB"`, `color = false`, `glyphs = "ascii"`,
  `statusline_lines = 2` (adds a `Next:` line), `meter = false` (hides the token-budget segment). `NO_COLOR` is honoured.
- A custom status line hides Claude Code's footer key hints (`esc to interrupt`, `? for shortcuts`), and a folder
  you have not trusted shows it blank. Layout spec: `docs/ui-layout.md`.

### The results panel

A second plugin, `pe-forge-panel` (`mods/pe-forge-panel`), puts a panel beside the chat. Its header carries the
**maq** mark as a large block logo in the accent blue, with the project, phase and next step beside it
(`[ui] logo = false` gives a one-line header). Five tabs:
**V-cycle** (every stage of the V, where the project is now, what needs attention; pick a stage to see its facts,
items and documents, and press `a` to ask Claude about it), **Simulations** (grade, key numbers per corner against
the hand calculation, charts), **Checks**, **Gates** and **Parts**. It opens by itself when a simulation result or a
full check lands; `/results [project folder]` opens it at any time. `forge report --open` shows the same as an
interactive page in the browser, offline. Install: `/plugin install pe-forge-panel@pe-forge`.

Keys: ctrl+x then Tab moves the keyboard into the panel; 1-5 switch tabs, the letter on a button presses it, Esc
returns to the prompt. Inside tmux, Claude Code draws in 256 colours; set `focus-events on` for the panel, and for
true colour start Claude with `TMUX` unset (the demo kit does both).

## Schematics: you draw, or the agents draw

Set at intake (`process.schematic_mode` = `user`, `agent` or `ask` per sheet). In both modes nothing is drawn
until the module's **design booklet** and **sheet brief** exist: every part chosen, every symbol pin and footprint
land pattern checked against the latest datasheet, every value calculated and benchmarked, and the full netlist
written down.

- **You draw**: the agents write the brief, pre-place parts on request so wires are short and straight, answer
  questions, then check your sheet (ERC, netlist against the brief, reviewers).
- **Agents draw**: `pe-schematic-drafter` writes a drawing spec and `forge schdraw` turns it into a real KiCad 10
  sheet: real orthogonal wires, T-junctions, labels on stubs, ground down, supplies up, signal flow left to right.
  `forge schdraw lint` scores any sheet, including yours, for readability (advice only on sheets you drew).
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
11. **The datasheet is final** for critical parts: layout guidelines, land pattern, example layout, values.
12. **Best EMI, heat and shielding**: outer layers filled with GND, a one-piece reference plane, stitching, minimal
    hot loop and switch node, thermal relief on every pad (solid only where the datasheet asks).
13. **Edge connectors** sit on the outline from the maker's drawing, open outward, with the mated plug or wire
    envelope and tool access clear.
14. **Orthogonal signal layers**: adjacent signal layers run horizontal / vertical; no broadside parallel runs.
15. **The High-Power PCB Rule Book** (`docs/High_Power_PCB_Rule_Book.md`, about 130 rules: stack-up, power loop,
    gate drive, switch node, grounding, sensing, EMI filter, thermal, thermal crosstalk, high current, creepage)
    applies by default at 50 V or 5 A and above.

`forge drift` holds the documents to the drawing: every BoM line, brief and booklet parts row and brief netlist
is compared with the schematic KiCad exports, and each disagreement is reported as `{doc, claim, actual}` (for
example `BOM.csv: R5 = 10k, actual R5 = 4.7k in power.kicad_sch`). Values compare by meaning (`4u7` = `4.7 µF`).

`forge insulation` checks the schematic's insulation from KiCad's own netlist. Nets are joined through every part
that is not a barrier (an MC_Parts row with `Iso_Rating`: isolators, isolated DC-DC, Y-caps). Each barrier has a
grade (reinforced, basic, functional; Y1 counts as reinforced, Y2 as basic), and only the grade the project asks
for (default reinforced) separates SELV from HV. Duplicate references are errors. Then:
- every net on the HV side must be in the HV class (INS-1);
- no SELV net may reach HV without a barrier, and the finding names the parts on the path (INS-2);
- each barrier part's package creepage is compared with the board barrier (INS-3);
- each HV net sits in exactly one island class (NET-4).

`forge insulation --rules` (CLS-2) proves the project's `.kicad_dru` really enforces the decided creepage and
clearance per class pair. It builds a two-pad test board 0.05 mm inside each value and runs KiCad's DRC on it.
"Not HV" is tested against every defined non-HV class (Default, Power, …), and each value also gets a pair just
wider than it, which must stay clean. Use `--keep DIR` to save the test board and the DRC report as evidence.
Set both up in `project.toml` `[insulation]`; new projects get a commented example. Both run in `forge check`. Gate
G6 runs the schematic checks, and G7 and G8 also need the rules proof. A check that isn't set up says so: it never
passes silently.

`forge protection` sizes every surge clamp (TVS) against the system and the parts it protects. Its standoff must
cover the maximum operating voltage, and its clamp voltage at the design surge current must stay within the
margin of every protected pin's absolute maximum. The values come from the parts DB (`Clamp_V`, `Abs_Max_V`,
copied from the datasheets) and project.toml `[protection]`. When no standard TVS can meet both limits, it says
so. Gate G6 refuses a failing schematic.

### PCB layout as data, reviewed against the rules

The board is built from project data, so every step can be re-run and reviewed:
`forge pcb sync` (Update PCB from Schematic, headless), `forge pcb setup` (outline, stack-up with copper weights and
finish, holes with copper keep-outs, fiducials, marking and fab notes from `project.toml [pcb]`), `forge pcb place
placement.json` (positions, label offsets, edge-connector declarations, solid pads with a datasheet reason),
`forge pcb zones zones.json` (pours with thermal relief by default, plus GND stitching) and `forge pcb route
routes.json`. Each step runs DRC with schematic parity.

The rules are measured, not just written down:
- `forge pcb copper`: copper coverage per layer, one-piece reference plane, stitching, pads joined solidly
  without a reason (Rule 12).
- `forge pcb connectors`: each edge connector's mating face on the outline, from the datasheet's pin-to-face
  distance (Rule 13).
- `forge pcb crosstalk`: signal direction per layer, adjacent layers orthogonal, broadside overlaps (Rule 14).
- `forge hp`: the rule book. A `[hp]` profile decides which rules apply. The tool measures switch-node area,
  layers and edge distance, signal copper over the switch node, exposed-pad via arrays, the via-in-pad fab note,
  stack-up symmetry, HF-cap distance, copper under CM chokes, the overshoot budget and the safety basis. A rule
  that does not fit is waived in `[hp.waive]` with the reason; a Must waiver needs your recorded OK.

`forge pcb review-new <milestone>` writes the independent review form: one row per layout-spec rule, per project
rule (R-11 to R-15) and per applicable rule-book rule, with the tool results filled in, and the board's content
hash. The `pcb-reviewer` agent grades every row. Gates G7 / G8 pass only when the review says READY for this exact
board and the Rule 12–15 checks pass themselves.

`forge bom build --boards N` builds the BoM from the drawn schematic and checks every line's part **and its alternate**
at Mouser and LCSC (DigiKey when configured): stock, 1-off price, lead time, lifecycle, MOQ, on-order dates. It
writes the BoM (with the alternate columns gate G4 needs), DigiKey / Mouser upload files (`--jlcpcb` adds the JLCPCB
BoM + CPL) and a **supply-chain risk report** (`04_Hardware/BOM/Supply_Risk.md`). Each line is graded HIGH / MEDIUM /
LOW for nine factors: can't build at that quantity, single source (an IC with no second maker is HIGH, a commodity
resistor LOW), lifecycle (NRND / EOL), long lead time, thin or one-distributor stock, an alternate that needs a board
change or has no stock, MOQ, a part not yet verified in the library, and cost concentration. Each finding has a
mitigation; a HIGH is fixed or waived with the reason in `project.toml [bom.waive]`, and gates G4 / G8 enforce it.
`--offline` uses the database and the last lookups.

`forge check --fab --strict` answers "can I order this board today?": routing complete, schematic and board
match, every placed part released with an alternate, the outputs still match the design by content hash, every
Gerber layer, drill and pick-and-place file present, power layout rules, release documents filled in. Gate G8
runs it.

Every part is created with its datasheet (`mclib.py add --datasheet file.pdf`; refused without one), and its
symbol and footprint check is tied to that exact file: `library/tools/datasheets.py` caches the PDF by content hash
and records page and section on the part (one check can cover a whole series with `verify-family`). Per board,
`forge evidence todo` lists what is still missing and `forge evidence sync` copies every placed part's datasheet
into the project's `04_Hardware/Datasheets/`. The release gate needs every part Active with recorded evidence.
Datasheets stay out of this repo (they are the makers' copyright).

Small analog circuits (sense amplifiers, dividers into ADCs, gate-drive RC, UVLO thresholds) are checked with
ngspice from the drawn schematic: a `## Simulation` block in the sheet brief states bounds and the closed-form
value, and `forge spice check` reports each assertion as ACCEPTED, PROVISIONAL (ngspice and the closed form differ
by more than 2 %) or REJECTED.

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

## Making pe-forge better from your use

pe-forge learns where it fits and where it does not, on your machine, without taking your design:
- `forge learn` shows what it recorded: rules that fail or are waived (with your reason), findings reviews caught
  that no check did, gate results and tool errors. Command names, rule IDs and counts only; never netlists,
  geometry, part lists or files. `forge learn off` (or `PE_FORGE_LEARN=0`) stops it.
- `forge issue` files a GitHub issue in one step with the context filled in (pe-forge, OS, Python and KiCad
  versions, the error trace inside pe-forge, the rule and its last result). It shows you the issue first, then
  creates it with `gh`, or opens the pre-filled page in your browser for you to Submit. After an internal error
  pe-forge prints the exact command: `forge issue --last`. In Claude Code just say "report this" (`/pe-issue`).
- `forge feedback` writes a redacted summary for the pe-forge team and shows it; nothing is ever sent by itself.
Paths, net names, e-mail addresses and the project name are redacted unless you add `--keep-names`.

## Command line

```
forge new <dir> --answers intake.json      forge status
forge module add NAME [--depends M-01]     forge gate check|pass G<n> [--module M-xx]
forge vmodel build|check|impact|diff|cr|sync   forge link [--apply]   forge consistency
forge schdraw check|apply|pins|new         forge bom check   forge doctor   forge kicad-setup [--apply]
forge check [--json] [--strict] [--fab] [--only erc,drc,...]   forge hooks install [--chain] | --uninstall
forge insulation [--rules] [--json]   forge protection [--json]   forge hp [--all|--json|rules]
forge pcb sync|setup|place|zones|route|rules|copper|connectors|crosstalk|review-new|review-status
forge drift [--module M-xx]   forge explain U1 | NET | U1.7 | --pins MC_ID   forge txn begin|verify|commit|rollback
forge cr new|validate|approve|close   forge ledger show|clear   forge fab manifest
forge bom build [--boards N] [--offline] [--jlcpcb]   forge bom risk [--fresh]
forge adopt [dir] [--apply|--undo]   forge guard status|accept <path>
forge learn [show|off|on|clear]   forge feedback [--note T]   forge issue [--last] [--kind K] [--rule ID] [--dry-run]
```

`forge check` is one deterministic command (no model, no network, seconds) over the whole project: ERC, DRC with
schematic parity, superseded terms, V-model links, BoM completeness, drift and the library checks. Each part
reports pass / warn / fail / skip; a skipped check says why and is never counted as passed. New projects get a
git pre-commit hook that runs the fast subset, and a GitHub Actions workflow (`.github/workflows/forge-check.yml`)
that installs KiCad 10 and uploads the JSON report.

## What changed

See [CHANGELOG.md](CHANGELOG.md).

## Status and limits

v0.3. The process, engine, drawing engine and library are in daily use on a real 11 kW product; the PCB layout
chain and rules 11–15 were proven on a 4-layer 48 V → 12 V pilot board. ERC / DRC are read
from KiCad's JSON reports through one layer (`forge/kicad.py`; set `KICAD_CLI` if KiCad is not in a standard place).
Known limits:
- pwrlab covers buck, DAB, H-bridge, T-type / NPC and totem-pole PFC; other topologies are hand-built netlists
  with the same checks done by hand (the skill says so). `forge spice` handles small linear / analog circuits only.
- `forge schdraw` draws new sheets or adds to a sheet. It does not yet rearrange parts already placed.
- The bundled library is small. Vendor-generated symbols and footprints cannot be redistributed (see
  `library/EXCLUDED.md`); the part-librarian agent builds the rest from manufacturers' files.
- Paywalled papers are listed for you to fetch; the researcher reads only what it can open.
- Engineering judgement stays with you. Verify footprints, ratings and safety margins before ordering anything.

## Licence

Code: [MIT](LICENSE). Library assets: CC-BY-SA 4.0 with the KiCad design exception ([library/LICENSE.md](library/LICENSE.md)).
Third-party tools: [THIRD_PARTY.md](THIRD_PARTY.md).
