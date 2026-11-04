# KiCad project: rules for agents

KiCad 10 project built from the MC_Standard template. Parts come from **MC_Lib**
(`$PE_FORGE/library` by default, env var `MC_LIB`). Read `$MC_LIB/CLAUDE.md` before touching parts.

## Tools
- CLI: `kicad-cli` (macOS: `/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli`; alias it `K`)
- Parts DB: `$MC_LIB/tools/mclib.py find|show|add|import|check`
- Release: `./release.sh` runs the ERC + DRC + BOM gate, then `Release.kicad_jobset` → `release/<REV>/`

## Checks (run these after every change)
```sh
sch-check . --json      # MC_Lib checks + ERC; each item has code, message, fix
kibom . --boards N      # DB-authoritative BOM + distributor upload files
```
Fix NET-UNNAMED by adding the suggested labels, LIB-STALE by asking the user to run Tools → Update
Symbols from Library, and LIB-NOTDB by swapping in the MC_Parts equivalent.

Raw KiCad commands:
```sh
K sch erc --format json --severity-all -o /tmp/erc.json <name>.kicad_sch
K pcb drc --format json --schematic-parity --severity-all -o /tmp/drc.json <name>.kicad_pcb
K sch export netlist --format kicadsexpr -o /tmp/net.net <name>.kicad_sch   # connectivity for review
K sch export bom --preset "MC BOM (grouped by MPN)" -o /tmp/bom.csv <name>.kicad_sch
```
Read the JSON reports instead of guessing. Summarise violations by type and location.

## Co-pilot and specialist agents
`kpilot` (or the MC Co-pilot toolbar button) starts Claude in watch mode (skill `kicad-copilot`).
`pcb-check .` covers layout checks. For deep reviews, delegate to the agents `schematic-reviewer`,
`power-reviewer`, `pcb-reviewer` and `part-librarian`.

## Rules
- Place parts from `MC_Parts` (database library) only. If a part is missing, add it to MC_Lib first
  (`mclib.py import` or `mclib.py add`) and commit MC_Lib. Never put MPNs straight into the schematic.
- Never edit `.kicad_sch` / `.kicad_pcb` while KiCad has the file open (check for a `~<name>.*.lck`
  file). KiCad will overwrite your edit or refuse it. Ask the user to close the editor, or drive the
  running editor through the KiCad IPC API (enabled in Preferences → Plugins).
- Net names drive design rules: `+*`, `VBAT*`, `VIN*` and `PGND*` → Power; `HV*` and `*_HV*` → HV (2.5 mm
  clearance / 4 mm creepage to LV, see `*.kicad_dru`). Name HV nets accordingly.
- Title block values come from project variables (`Schematic Setup → Text Variables`):
  COMPANY, REVISION, DESIGNER, PCB_PN, RELEASE_DATE. Update REVISION before a release.
- Do not "fix" DRC by loosening rules or adding exclusions without asking.
