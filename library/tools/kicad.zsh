# KiCad + MC_Lib shell commands. Sourced from ~/.zshrc. Run `khelp` for the list.
# Project commands work from anywhere inside a project folder (they find the .kicad_pro).

export MC_LIB="${MC_LIB:-${PE_FORGE:-$HOME/pe-forge}/library}"
export KICAD_APP="/Applications/KiCad/KiCad.app"
alias kcli="$KICAD_APP/Contents/MacOS/kicad-cli"
alias kpy="$KICAD_APP/Contents/Frameworks/Python.framework/Versions/Current/bin/python3"

# --- helpers -----------------------------------------------------------------------------
_kpro() {  # print path of the project file in this dir or a parent dir
  local d=$PWD f
  while [[ $d != / ]]; do
    f=( $d/*.kicad_pro(N) )
    (( ${#f} == 1 )) && { print -r -- $f[1]; return 0; }
    d=${d:h}
  done
  print -u2 "not inside a KiCad project folder"; return 1
}
_kname() { local p; p=$(_kpro) || return 1; print -r -- ${p%.kicad_pro}; }

# --- project -----------------------------------------------------------------------------
knew() {   # knew <name> [dir]  - new project from the MC_Standard template
  [[ -z $1 ]] && { print "usage: knew <name> [parent dir]"; return 1; }
  local t="$HOME/Documents/KiCad/10.0/template/MC_Standard" dest="${2:-$PWD}/$1" f
  [[ -e $dest ]] && { print "$dest already exists"; return 1; }
  mkdir -p "$dest"
  for f in $t/*(N); do
    [[ ${f:t} == meta ]] && continue
    cp "$f" "$dest/${${f:t}/MC_Standard/$1}"
  done
  (cd "$dest" && git init -q && print -l "release/" ".copilot/" "analysis/" "*-backups/" "*.lck" "fp-info-cache" "_autosave-*" "*.kicad_prl" > .gitignore)
  print "created $dest"; cd "$dest"
}
kopen() { local p; p=$(_kpro) || return 1; open -a "$KICAD_APP" "$p"; }       # open project in KiCad
kcd()   { local p; p=$(_kpro) || return 1; cd "${p:h}"; }                      # jump to project root

# --- checks ------------------------------------------------------------------------------
kcheck() { local n; n=$(_kname) || return 1; "$MC_LIB/tools/sch_check.py" "$n.kicad_sch" "$@"; }
kerc() {
  local n; n=$(_kname) || return 1
  kcli sch erc --format json --severity-error --severity-warning -o /tmp/kerc.json "$n.kicad_sch" >/dev/null
  python3 -c "import json,collections as c;d=json.load(open('/tmp/kerc.json'));v=[x for s in d['sheets'] for x in s['violations']];print(f'ERC: {len(v)} violations');[print(f'  {n:4} {t}') for t,n in c.Counter(x['severity']+'  '+x['type'] for x in v).most_common()]"
}
kdrc() {
  local n; n=$(_kname) || return 1
  kcli pcb drc --format json --schematic-parity --severity-error --severity-warning -o /tmp/kdrc.json "$n.kicad_pcb" >/dev/null
  python3 -c "import json,collections as c;d=json.load(open('/tmp/kdrc.json'));v=d['violations'];u=d['unconnected_items'];p=d['schematic_parity'];print(f'DRC: {len(v)} violations, {len(u)} unrouted, {len(p)} sch/pcb mismatches');[print(f'  {n:4} {t}') for t,n in c.Counter(x['severity']+'  '+x['type'] for x in v+p).most_common()]"
}

# --- outputs -----------------------------------------------------------------------------
kbom()  { local n; n=$(_kname) || return 1; "$MC_LIB/tools/bom.py" "$n.kicad_sch" "$@"; }   # kbom --boards 25
krel()  { local p; p=$(_kpro) || return 1; (cd "${p:h}" && ./release.sh "$@"); }             # krel [--force]
kpdf()  { local n; n=$(_kname) || return 1; kcli sch export pdf -o "/tmp/${n:t}.pdf" "$n.kicad_sch" >/dev/null && open "/tmp/${n:t}.pdf"; }
k3d()   {  # k3d [top|bottom|iso]  - render the board and open the image
  local n v=${1:-iso} args; n=$(_kname) || return 1
  case $v in iso) args=(--rotate '-45,0,45' --perspective);; *) args=(--side $v);; esac
  kcli pcb render --quality high $args -o "/tmp/${n:t}-$v.png" "$n.kicad_pcb" >/dev/null && open "/tmp/${n:t}-$v.png"
}

# --- co-pilot ----------------------------------------------------------------------------
kpilot() {  # Claude co-pilot for this project: watches saves, re-checks, suggests (read-only)
  local p; p=$(_kpro) || return 1
  cd "${p:h}" || return 1
  MOUSER_SEARCH_API_KEY=$(security find-generic-password -s mouser-api -w 2>/dev/null) \
  claude "/kicad-copilot" --allowedTools \
    "Bash(kicad-copilot *)" "Bash(sch-check *)" "Bash(pcb-check *)" "Bash(kibom *)" \
    "Bash(mclib find *)" "Bash(mclib show *)" "Bash(mclib check)" "Bash(fp-qa *)" \
    "Bash(mouser lookup *)" "Bash(mouser bom *)" \
    "Bash(python3 */kicad-happy/*/skills/*/scripts/analyze_*)" \
    "Bash($KICAD_APP/Contents/MacOS/kicad-cli *)" "$@"
}
kstatus() { kicad-copilot status "$@"; }        # last co-pilot snapshot without re-running
kprice()  {  # kprice [boards]: live Mouser cost + stock for the whole design
  local p n; p=$(_kpro) || return 1
  n=${1:-$(cd "${p:h}" && kicad-copilot boards | grep -oE '[0-9]+ board' | grep -oE '[0-9]+')}
  mouser bom "${p:h}" --boards "${n:-1}"
}
alias kstock="mouser lookup"                      # kstock SN6501QDBVRQ1 --qty 100
kboards() { kicad-copilot boards "$@"; }          # kboards 25: build qty for pricing

# --- verified third-party tools (tested on KiCad 10.0.6, 2026-09-25) ------------------------
KTOOLS=$HOME/.venvs/kicad-tools/bin          # KiKit + InteractiveHtmlBom (venv on KiCad's Python)
FREEROUTING=$HOME/.local/share/freerouting/freerouting-2.4.1.jar
KJAVA=/opt/homebrew/opt/openjdk/bin/java      # Freerouting 2.4 needs Java 25+
kpanel() {  # kpanel [rows] [cols]: KiKit panel with rails, mouse bites, tooling holes, fiducials
  local n; n=$(_kname) || return 1
  mkdir -p "${n:h}/release/panel"
  $KTOOLS/kikit panelize -p "$MC_LIB/tools/kikit_panel.json" \
    --layout "grid; rows: ${1:-2}; cols: ${2:-2}; space: 3mm" \
    "$n.kicad_pcb" "${n:h}/release/panel/${n:t}-panel.kicad_pcb" 2>&1 | grep -vE 'Debug|assert'
  rm -f "${n:h}/release/panel/~${n:t}-panel.kicad_pro.lck"      # KiKit leaves a stale lock behind
  [[ -f "${n:h}/release/panel/${n:t}-panel.kicad_pcb" ]] && print "panel: release/panel/${n:t}-panel.kicad_pcb (open it in the PCB editor, then plot Gerbers)"
}
khtml() {  # interactive HTML BOM (assembly aid: click a part, see where it goes)
  local n; n=$(_kname) || return 1
  $KTOOLS/generate_interactive_bom --no-browser --dest-dir "${n:h}/release/ibom" --name-format '%f_ibom' \
    --extra-fields "MPN,Manufacturer,Mouser_PN" --group-fields "Value,Footprint,MPN" "$n.kicad_pcb" 2>&1 | grep -E 'Created|ERROR'
  open "${n:h}/release/ibom/${n:t}_ibom.html"
}
kroute() {  # autoroute the SAVED board with Freerouting → <name>.ses, then import it in KiCad
  local n passes=${1:-100}; n=$(_kname) || return 1
  kpy - "$n" <<'PY' 2>&1 | grep -vE 'Debug|assert'
import sys, pcbnew
n = sys.argv[1]
print("exported DSN" if pcbnew.ExportSpecctraDSN(pcbnew.LoadBoard(n + ".kicad_pcb"), n + ".dsn") else "DSN export FAILED")
PY
  $KJAVA -jar $FREEROUTING -de "$n.dsn" -do "$n.ses" -mp $passes --gui.enabled=false 2>&1 | grep -E 'Auto-routing stage completed|ERROR' | sed 's/.*\] //'
  [[ -f "$n.ses" ]] && print "→ In the PCB editor: File → Import → Specctra Session… → ${n:t}.ses  (Ctrl+Z undoes it)"
}

# --- library -----------------------------------------------------------------------------
alias kfind="$MC_LIB/tools/mclib.py find"        # kfind 100n 0603 X7R
alias kpart="$MC_LIB/tools/mclib.py show"        # kpart RES-0272
alias kadd="$MC_LIB/tools/mclib.py add"          # kadd Capacitors MPN=... Value=... Symbols=... Footprints=...
alias kset="$MC_LIB/tools/mclib.py set"          # kset CAP-0012 Status=Active
alias kimport="$MC_LIB/tools/mclib.py import"    # kimport ~/Downloads/ul_X.zip --lib MC_IC --table ICs
alias klib="cd $MC_LIB"
klibcheck() { "$MC_LIB/tools/mclib.py" check && "$MC_LIB/tools/mclib.py" lint-fp | tail -1; }
klibsave() {  # klibsave "message"  - check, then commit the library
  klibcheck || { print "fix the problems above first"; return 1; }
  git -C "$MC_LIB" add -A && git -C "$MC_LIB" commit -q -m "${1:-library update}" && git -C "$MC_LIB" log --oneline -1
}
kfp() {  # kfp MC_IC:ADIBMS6830  - render a footprint and open it
  local lib=${1%%:*} fp=${1#*:}
  mkdir -p /tmp/kfp && kcli fp export svg -o /tmp/kfp --layers F.Cu,F.SilkS,F.CrtYd,F.Fab --fp "$fp" \
    "$MC_LIB/footprints/$lib.pretty" >/dev/null && open "/tmp/kfp/$fp.svg"
}

khelp() {
  cat <<'EOF'
Project (run anywhere inside a project folder)
  knew <name>        new project from MC_Standard template (+ git init), cd into it
  kopen / kcd        open project in KiCad / jump to project root
  kcheck [--json]    MC_Lib schematic checks + ERC (net naming, DB sync, decoupling)
  kerc / kdrc        ERC / DRC summary (DRC includes schematic<->PCB parity)
  kbom [--boards N]  BOM from the database + DigiKey/Mouser upload files
  kpdf               schematic PDF, opened
  k3d [iso|top|bottom]  3D render, opened
  krel [--force]     full release gate + outputs (release.sh)
  kpilot             start the Claude co-pilot here (watches saves, reports new/resolved issues)
  kprice [N]         live Mouser cost + stock for the whole design (N boards)
  kstock <MPN>       live Mouser price/stock for one part   kboards N   set build quantity
  kroute [passes]    autoroute saved board with Freerouting → import .ses in PCB editor
  kpanel [rows cols] KiKit production panel      khtml   interactive HTML BOM for assembly
  kstatus            last co-pilot snapshot      pcb-check .   layout checks (decap, thermal vias, widths)
Library
  kfind <terms>      search parts            kpart <ID>     show a part
  kimport <zip> --lib MC_IC --table ICs      import a vendor download
  kadd / kset        add a part / edit fields
  kfp LIB:FP         render a footprint      klibcheck      validate library
  klibsave "msg"     check + git commit the library        klib   cd to MC_Lib
Raw: kcli (kicad-cli), kpy (KiCad Python with pcbnew)
EOF
}
