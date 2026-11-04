#!/bin/zsh
# MC Tools toolbar dispatcher. KiCad runs one small wrapper per button, which calls:
#   mc_action.sh <check|ercdrc|bom|pdf|render3d|release|find|import|term|copilot>
# Finds the project that is open in KiCad, then either opens a Terminal window running the
# kicad.zsh command (output you want to read) or runs silently and opens the result (PDF, 3D).
# MC_TOOLS_DRYRUN=1 writes the Terminal script without opening it (for testing).

MC_LIB=${MC_LIB:-${PE_FORGE:-$HOME/pe-forge}/library}
KAPP=/Applications/KiCad/KiCad.app
KPY=$KAPP/Contents/Frameworks/Python.framework/Versions/Current/bin/python3
KCLI=$KAPP/Contents/MacOS/kicad-cli
action=${1:-term}
LOG=/tmp/mc_tools.log

notify() { osascript -e "display notification \"$2\" with title \"MC Tools\" subtitle \"$1\"" 2>/dev/null; }

# Open project: first entry in open_projects/file_history that has a KiCad lock file beside it.
pro=$($KPY - <<'EOF'
import json
from pathlib import Path
try:
    s = json.load(open(Path.home() / "Library/Preferences/kicad/10.0/kicad.json"))["system"]
except Exception:
    s = {}
cands = [Path(p) for p in s.get("open_projects", []) + s.get("file_history", [])]
locked = [p for p in cands if (p.parent / f"~{p.name}.lck").exists()]
pick = (locked or [p for p in cands if p.exists()] or [None])[0]
print(pick or "")
EOF
)
if [[ -z $pro || ! -f $pro ]]; then
  notify "No project" "Open a project in the KiCad project manager first"
  [[ $action != term && $action != find && $action != import ]] && exit 1
fi
dir=${pro:h}; name=${${pro:t}%.kicad_pro}
[[ -z $pro ]] && dir=$MC_LIB name="(no project)"

term() {  # term <title> <shell lines...>: run in a new Terminal window, then leave an interactive shell there
  local title=$1; shift
  local f=/tmp/mc_tools_$action.command
  {
    print '#!/bin/zsh'
    print 'export PATH="/opt/anaconda3/bin:/opt/homebrew/bin:$HOME/.local/bin:/usr/local/bin:$PATH"'
    print "source ${(q)MC_LIB}/tools/kicad.zsh"
    print "cd ${(q)dir}"
    print "printf '\\\\e]0;MC Tools – $title\\\\a'"
    print "print -P '%F{cyan}%B== $title — $name ==%b%f'"
    print "print -P '%F{242}(reads the saved files: press Cmd+S in KiCad first)%f'; print"
    print -l -- "$@"
    print '[[ -z $MC_TOOLS_DRYRUN ]] && { print; exec zsh -i }'
  } > $f
  chmod +x $f
  [[ -n $MC_TOOLS_DRYRUN ]] && { print $f; return }
  open -a Terminal $f
}

case $action in
  check)   term "Design check" "kcheck" ;;
  ercdrc)  term "ERC + DRC" "kerc" "print" "kdrc" ;;
  bom)     term "BOM" 'read "n?Build quantity [1]: "' 'kbom --boards ${n:-1} && open release/bom' ;;
  release) term "Release" 'krel && open release' ;;
  term)    term "Terminal" "khelp" ;;
  copilot) term "Co-pilot" "kpilot" ;;
  find)    term "Find part" \
             'while read "q?Search MC_Parts (e.g. 10k 0603), empty to quit: " && [[ -n $q ]]; do kfind ${=q}; print; done' ;;
  import)  term "Import part" \
             'print "Drag a vendor download (zip / folder / SchLib / IntLib) into this window, then press Enter:"' \
             'read "src?> "; src=${(Q)${src%% }}' \
             'tables=(ICs Transistors Diodes Optoelectronics Protection Magnetics Connectors Capacitors Resistors Sensors Crystals Mechanical)' \
             'print "Table:"; select t in $tables; do [[ -n $t ]] && break; done' \
             'case $t in ICs|Sensors) lib=MC_IC;; Transistors|Diodes|Optoelectronics|Protection|Crystals) lib=MC_Discrete;; Magnetics) lib=MC_Magnetic;; Connectors) lib=MC_Connector;; *) lib=MC_Passive;; esac' \
             'read "mfr?Manufacturer (Enter to keep vendor value): "' \
             'kimport "$src" --lib $lib --table $t ${mfr:+--field Manufacturer=$mfr} && klibcheck && fp-qa $lib | tail -3' \
             'read -q "ok?Commit to MC_Lib? [y/N] " && { print; klibsave "Import $(basename $src)"; }' \
             'print; print "In KiCad: reopen the symbol chooser (A) to see the new part."' ;;
  pdf)
    out=/tmp/$name-schematic.pdf
    if $KCLI sch export pdf -o $out $dir/$name.kicad_sch >$LOG 2>&1; then open $out
    else notify "PDF failed" "See $LOG"; open -a TextEdit $LOG; fi ;;
  render3d|3d)
    out=/tmp/$name-3d.png
    notify "3D render" "Rendering $name…"
    if $KCLI pcb render --quality high --rotate '-45,0,45' --perspective -o $out $dir/$name.kicad_pcb >$LOG 2>&1; then open $out
    else notify "3D render failed" "See $LOG"; open -a TextEdit $LOG; fi ;;
  *) notify "Unknown action" "$action"; exit 2 ;;
esac
