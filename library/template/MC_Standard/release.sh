#!/bin/zsh
# Release gate + outputs (the Altium OutJob equivalent).
#   ./release.sh            ERC + DRC must be clean, then run Release.kicad_jobset
#   ./release.sh --force    produce outputs even with violations (prototype spins)
#   BOARDS=25 ./release.sh  order quantities for 25 boards in the DigiKey/Mouser upload files (SPARES=5 adds spares)
#   project.toml [process] fab_house = "jlcpcb"  also writes the JLCPCB BoM + CPL (bom.py --jlcpcb)
# Outputs: release/<REVISION>/ (PDF, BOM, pick&place, STEP, gerbers, drill) + release/<name>-<rev>-fab.zip
# ERC/DRC run here rather than in the jobset: kicad-cli 10.0.6 crashes on ERC/DRC jobs inside `jobset run`.
set -uo pipefail
cd "${0:A:h}"
K=${KICAD_CLI:-$(command -v kicad-cli || echo /Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli)}
pro=( *.kicad_pro(N) ); [[ ${#pro} -eq 1 ]] || { echo "expected one .kicad_pro here"; exit 2; }
name=${pro[1]%.kicad_pro}
force=0; [[ "${1:-}" == "--force" ]] && force=1
mkdir -p release/reports
fail=0

$K sch erc --severity-error --exit-code-violations -o release/reports/$name-erc.rpt $name.kicad_sch >/dev/null \
  || { echo "ERC: errors, see release/reports/$name-erc.rpt"; fail=1; }
$K pcb drc --schematic-parity --refill-zones --severity-error --exit-code-violations \
  -o release/reports/$name-drc.rpt $name.kicad_pcb >/dev/null \
  || { echo "DRC: errors or schematic/PCB mismatch, see release/reports/$name-drc.rpt"; fail=1; }

# MC_Lib checks: library discipline, DB sync, net naming, decoupling (ERC already ran above)
MC_LIB=${MC_LIB:-${PE_FORGE:-$HOME/pe-forge}/library}
$MC_LIB/tools/sch_check.py --no-erc $name.kicad_sch | tee release/reports/$name-sch_check.txt | grep -E '^(ERROR|[0-9]+ errors)' \
  ; (( ${pipestatus[1]} )) && { echo "sch_check: errors, see release/reports/$name-sch_check.txt"; fail=1; }
jlc=(); [[ -f ../../project.toml ]] && grep -Eq '^fab_house *= *"jlcpcb"' ../../project.toml && jlc=(--jlcpcb)
$MC_LIB/tools/bom.py $name.kicad_sch -o release/bom --boards ${BOARDS:-1} --spares ${SPARES:-0} $jlc || fail=1

if (( fail && !force )); then echo "Release blocked. Fix the above or rerun with --force."; exit 1; fi
$K jobset run -f Release.kicad_jobset $name.kicad_pro > release/reports/jobset.log 2>&1 \
  || { echo "jobset failed, see release/reports/jobset.log"; exit 1; }
# release manifest: SHA-256 of the board, sheets, BoM and every output, for the fab gate (forge check --fab)
FORGE=${PE_FORGE:+$PE_FORGE/bin/forge}; FORGE=${FORGE:-$(command -v forge)}
if [[ -n "$FORGE" ]]; then "$FORGE" fab manifest . || echo "warning: release manifest not written"
else echo "warning: forge not found (set PE_FORGE): no release manifest, the fab gate will ask for one"; fi
echo "Release outputs written to $(pwd)/release"
