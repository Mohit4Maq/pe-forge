---
name: kicad
description: Work on the user's KiCad 10 PCB projects and the MC_Lib component library (SQLite database library + git symbol/footprint libs). Use for anything KiCad: adding or importing parts (Ultra Librarian, SnapEDA, Altium SchLib/PcbLib/IntLib), finding parts, ERC/DRC, BOM, Gerbers/release outputs, footprint checks, new projects, or Altium-to-KiCad questions.
---

# KiCad + MC_Lib

The user is an experienced Altium engineer who is new to KiCad. Explain things with the Altium
equivalent (DbLib ↔ database library, OutJob ↔ jobset, Mech15 ↔ F.CrtYd, Update Parameters from
Database ↔ Update Symbols from Library).

## Where things are
- KiCad 10 (tested on 10.0.6). pe-forge finds `kicad-cli`, KiCad's Python (`pcbnew`) and the stock symbols in one
  place, `forge/kicad.py`: `$KICAD_CLI` / `$KICAD_PYTHON` / `$KICAD_SYMBOL_DIR` first, then PATH, the macOS app
  (`/Applications/KiCad/KiCad.app`) and the usual Linux paths. ERC / DRC results come from KiCad's JSON reports
  (`forge.kicad.erc()` / `drc()`); a report kicad-cli did not write is a failure, never a pass.
- Library: `$MC_LIB` (git, env var `MC_LIB`). **Read `MC_Lib/CLAUDE.md` first.**
  - `tools/mclib.py find|show|add|set|import|check|lint-fp|stats|gen-dbl` (stdlib Python)
  - `tools/sch_check.py` (`sch-check`): schematic checks — LIB-* (not from MC_Parts, stale vs DB, status),
    NET-* (unnamed IC nets with suggested names, single-pin labels, near-duplicate names, supplies not in
    the Power class, mixed active-low styles), DEC-NOCAP, ERC summary. `--json` for machine use.
  - `tools/bom.py` (`kibom`): BOM from netlist + DB, `--boards N`, DigiKey/Mouser upload CSVs
  - `tools/kicad.zsh`: shell commands sourced from ~/.zshrc (knew, kopen, kcheck, kerc, kdrc, kbom, kpdf, k3d,
    krel, kfind, kpart, kimport, kfp, klibsave; `khelp` lists them). Title-block vars: use ${PROJECTNAME}.
  - `tools/fp_qa.py` runs KiCad DRC on every footprint (re-runs itself under KiCad's Python when `pcbnew` is missing)
  - Open issues: `MC_Lib/docs/TODO.md`
- Project template: `$PE_FORGE/library/template/MC_Standard` (4-layer, net classes
  Default/Power/HV, HV clearance/creepage rules, MPN BOM preset, `Release.kicad_jobset`,
  `release.sh`, project `CLAUDE.md`). New projects: *File → New Project from Template → MC Standard*.
- Config: `~/Library/Preferences/kicad/10.0/` (sym-lib-table, fp-lib-table, kicad_common.json).
  **Only edit these when KiCad is closed**, because KiCad rewrites them on exit.

## Typical flows
- Add a vendor part: `mclib.py import ~/Downloads/<zip or folder> --lib MC_IC --table ICs [--field K=V]`,
  then `mclib.py check`, then `tools/fp_qa.py MC_IC`, render with `kicad-cli fp export svg`, and
  `git commit` in MC_Lib.
- Passive: `mclib.py add Capacitors MPN=... Value=... Symbols=MC_Generic:C Footprints=Capacitor_SMD:C_0603_1608Metric ...`
- Review a design: run ERC/DRC with `--format json` and the netlist export, then reason over the JSON.
- Release: `./release.sh` in the project folder (gate + jobset → `release/<REV>/`).

## Co-pilot, agents and more tools
- `kicad-copilot` skill + `kpilot` (shell) / "MC Co-pilot" toolbar button: Claude watches saves and reports
  new/resolved issues. Engine: `tools/copilot.py` (`kicad-copilot wait|snapshot|status`), state in `<proj>/.copilot/`.
- `tools/pcb_check.py` (`pcb-check`, KiCad Python): PCB-OUTLINE/OFFBOARD/DECAP/THERMAL/PWRTRACE/FID.
- `sch-check` also reports PRJ-VAR (the undefined text variable behind "unresolved text variable").
- Agents (~/.claude/agents): `schematic-reviewer` (datasheet-backed review), `power-reviewer` (pwrlab sizing),
  `pcb-reviewer` (layout + renders), `part-librarian` (find/source/add parts to MC_Lib).

- Mouser: `tools/mouser.py` (`mouser lookup|bom`), key in the Keychain (`mouser-api`). Library rows carry dated
  Mouser_Stock/Price_1/Price_100/Lead_Time/Lifecycle/Sourcing_Date, set by `mclib add|import` and refreshed by `mclib sourcing`.

## Verified third-party tools (each tested on KiCad 10.0.6 on 2026-09-25; only these are adopted)
- **KiKit 1.8.1** (panelization) and **InteractiveHtmlBom 2.12.0**, in the venv `~/.venvs/kicad-tools` (built on KiCad's
  Python, so `pcbnew` works). Shell: `kpanel [rows cols]` (preset `MC_Lib/tools/kikit_panel.json`), `khtml`.
- **Freerouting 2.4.1**: `~/.local/share/freerouting/freerouting-2.4.1.jar`, needs Java 25+
  (`/opt/homebrew/opt/openjdk/bin/java`; the jar will not start on Java 21). `kroute` exports the saved board to DSN
  with pcbnew, routes headless, and writes `<name>.ses`. The user imports it via File → Import → Specctra Session
  (never write the .kicad_pcb while it is open).
- **kicad-happy 2.2.1** Claude Code plugin (skills `kicad-happy:*`: kicad, emc, spice, datasheets, bom, mouser, …).
  Read-only analysers (audited: no writes to design files; network only in the distributor/datasheet scripts). It
  complements sch-check/pcb-check: EMC pre-compliance, SPICE, testability, fab gate, and narrow switching nets.
  `kpilot` passes the Mouser key as MOUSER_SEARCH_API_KEY.
- Deliberately NOT adopted: the KiCad MCP servers (mixelpixx targets KiCad 9 via SWIG and edits files directly;
  the others are new or unverified clones), and code-first languages (atopile, Zener, circuit-synth, SKiDL,
  tscircuit), which are a different workflow from the MC_Lib GUI + database one. Revisit on request.

## KiCad toolbar
MC Tools IPC plugin (runtime "exec"): source `MC_Lib/kicad_plugin/` (mc_action.sh dispatcher, install.py),
installed to `~/Documents/KiCad/10.0/plugins/com.mc.tools/`. After editing ACTIONS run
`python3 kicad_plugin/install.py` and restart KiCad. The open project is found from kicad.json
open_projects/file_history plus the `~<name>.kicad_pro.lck` lock file.

## Known gotchas (verified 2026-09-25)
- Ultra Librarian footprints: (1) a literal "DesignatorNNN" text on F.SilkS would print on the PCB; `mclib import`
  now strips it (strip_ul_artifacts). (2) Their MFG variants carry paste as separate F.Paste polygons sized for their
  own pads; if you resize the pads, delete those polygons and put F.Paste on the pads. (3) Pin types are often wrong
  (power_in on analog inputs, outputs as inputs): always check them against the datasheet pin table.
- The Power net class (0.5 mm) cannot escape 0.5 mm-pitch pins (0.3 mm pads, 0.2 mm gaps): Freerouting leaves those
  connections unrouted. Neck down near fine-pitch ICs, or keep MCU supply nets in the Default class.
- Freerouting + KiCad SES import can pair one class's via pad with another class's drill (seen: 0.6 mm pad with
  0.4 mm drill on an HV net → 0.1 mm annular ring). Fixed in the template by keeping every pad/drill combination
  legal (Default via 0.7/0.3, Power/HV 0.8/0.4, min ring 0.13). Older projects: raise the Default via to 0.7 mm.
- ~/Documents is synced to **iCloud Drive**. Rapid rewrites create "name 2.ext" conflict copies
  (ignored in MC_Lib's .gitignore; delete them, and run `git fsck` if in doubt).
- One unacceptable footprint makes KiCad drop the **whole** library ("configuration does not include
  the footprint library X"). Known cause: Altium NPTH pads with `(size 0 0)`. `pcbnew.FootprintLoad`
  does NOT catch this. `kicad-cli fp upgrade <lib>.pretty` does, and `fp_qa.py` runs it.
  `mclib.py fix-fp` repairs files and re-saves them through KiCad.
- `kicad-cli jobset run` **segfaults on ERC/DRC jobs** in 10.0.6. Run `sch erc` / `pcb drc` standalone.
  Export jobs work. The BOM job setting key is `bom_preset_name`.
- `kicad-cli` cannot load a `.kicad_dbl` (database library). Check DB rows with `mclib.py check`
  and ODBC with `isql`. Final confirmation happens in the schematic editor GUI.
- Altium footprints convert with `kicad-cli fp upgrade`, symbols with `kicad-cli sym upgrade`
  (IntLib works too). Mech layers become `User.N`. `mclib.py import` remaps 15→CrtYd and 1/13→Fab,
  moves origin crosshairs off the courtyard, and closes gaps under 0.1 mm.
- PcbLibs written by the user's `~/Python/altium-automation` tool (`*_reviewed.PcbLib`) are not
  readable by KiCad.
- Never invent MPNs or distributor part numbers.
