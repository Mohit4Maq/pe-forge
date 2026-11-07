#!/usr/bin/env bash
# pe-forge: one command from a new Mac to a working toolkit and a first project.
#
#   curl -fsSL https://raw.githubusercontent.com/Mohit4Maq/pe-forge/main/install.sh | bash
#   ./install.sh [--check] [--update] [--yes] [--no-project] [--projects DIR]
#
#   --check       report what is installed, missing or out of date; change nothing
#   --update      also upgrade what is out of date (Homebrew tools and apps, Claude Code, pe-forge, its plugins)
#   --yes         accept every default (installs everything missing; still asks about the project)
#   --no-project  skip the "fresh project or reuse one" question
#   --projects    where new projects go (default ~/pe-forge-projects)
#
# What it installs or checks, each only if missing (anything already there, however it was installed, is kept):
#   Apple command-line tools (git), Homebrew, Python 3.11+, ngspice, the SQLite ODBC driver (KiCad parts library),
#   KiCad 10, LTspice, Obsidian (the document manager: every project is a vault), Claude Code;
#   then pe-forge itself (~/pe-forge, or this clone) and its set-up: Python venv with pwrlab, `forge` on PATH,
#   Claude Code settings, the pwrlab and forge MCP servers, the pe-forge and pe-forge-panel plugins, the KiCad library.
# Run it again at any time: it is safe to repeat (`forge update` runs it with --update).
set -uo pipefail

CHECK=0; UPDATE=0; YES=0; PROJECT=1; PROJECTS="$HOME/pe-forge-projects"
while (( $# )); do
  case "$1" in
    --check) CHECK=1 ;; --update) UPDATE=1 ;; --yes) YES=1 ;; --no-project) PROJECT=0 ;;
    --projects) PROJECTS="$2"; shift ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "unknown option $1 (see --help)"; exit 2 ;;
  esac
  shift
done
REPO_URL="https://github.com/Mohit4Maq/pe-forge.git"
TTY=/dev/tty; [[ -r /dev/tty ]] || TTY=/dev/stdin

B=$'\033[1m'; D=$'\033[2m'; A=$'\033[38;2;66;69;245m'; G=$'\033[32m'; Y=$'\033[33m'; R=$'\033[31m'; N=$'\033[0m'
[[ -n "${NO_COLOR:-}" ]] && B= D= A= G= Y= R= N=
say()  { printf '%s▸%s %s\n' "$A" "$N" "$*"; }
ok()   { printf '  %s✓%s %-26s %s\n' "$G" "$N" "$1" "${D}$2${N}"; }
miss() { printf '  %s✗%s %-26s %s\n' "$R" "$N" "$1" "$2"; }
old()  { printf '  %s↑%s %-26s %s\n' "$Y" "$N" "$1" "$2"; }
warn() { printf '%s!%s %s\n' "$Y" "$N" "$*"; }
ask()  { (( YES )) && return 0; local a; read -r -p "$1 [Y/n] " a < "$TTY"; [[ -z "$a" || "$a" =~ ^[Yy] ]]; }
askv() { local a; read -r -p "$1 [$2] " a < "$TTY"; printf '%s' "${a:-$2}"; }

printf '\n%s%smaq%s %s▸%s %spe-forge%s  power-electronics design, idea to manufacturing files\n\n' "$A" "$B" "$N" "$A" "$N" "$B" "$N"
[[ "$(uname)" == "Darwin" ]] || { warn "this installer is for macOS. On Linux: clone $REPO_URL and run ./bootstrap.sh (see README)."; exit 1; }
(( CHECK )) && say "check only: nothing will be changed"

# ------------------------------------------------------------------ 1. Apple command-line tools
say "Apple command-line tools"
if xcode-select -p >/dev/null 2>&1; then ok "command-line tools" "$(xcode-select -p)"
elif (( CHECK )); then miss "command-line tools" "run: xcode-select --install"
else
  miss "command-line tools" "installing: finish the dialog that opens"
  xcode-select --install >/dev/null 2>&1 || true
  for _ in $(seq 1 360); do xcode-select -p >/dev/null 2>&1 && break; sleep 5; done
  xcode-select -p >/dev/null 2>&1 && ok "command-line tools" "installed" || { warn "command-line tools not installed: run xcode-select --install, then this again"; exit 1; }
fi

# ------------------------------------------------------------------ 2. Homebrew
say "Homebrew"
for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do [[ -x "$b" ]] && eval "$("$b" shellenv)" && break; done
if command -v brew >/dev/null; then ok "Homebrew" "$(brew --version | head -1)"
elif (( CHECK )); then miss "Homebrew" "see brew.sh"
else
  miss "Homebrew" "installing (it asks for your Mac password)"
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" < "$TTY" || { warn "Homebrew install failed"; exit 1; }
  for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do [[ -x "$b" ]] && eval "$("$b" shellenv)" && BREW="$b" && break; done
  grep -qs 'brew shellenv' "$HOME/.zprofile" || printf '\neval "$(%s shellenv)"\n' "${BREW:-brew}" >> "$HOME/.zprofile"
fi
command -v brew >/dev/null || { (( CHECK )) || exit 1; }

# ------------------------------------------------------------------ 3. tools and apps
say "tools and apps"
py_ok() { for c in python3.13 python3.12 python3.11 python3; do command -v "$c" >/dev/null && "$c" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null && { "$c" --version; return 0; }; done; return 1; }
have_odbc() { local p; p="$(brew --prefix 2>/dev/null)"; [[ -e "$p/lib/libsqlite3odbc.dylib" ]]; }
# key | label | kind | brew name | present test | version / note
TOOLS=(
  "git|git|formula|git|command -v git|git --version"
  "python|Python 3.11+|formula|python@3.12|py_ok|py_ok"
  "ngspice|ngspice|formula|ngspice|command -v ngspice|ngspice -v 2>/dev/null | grep -o 'ngspice-[0-9.]*' | head -1"
  "odbc|SQLite ODBC driver|formula|sqliteodbc|have_odbc|echo for the KiCad parts library"
  "kicad|KiCad 10|cask|kicad|test -d /Applications/KiCad/KiCad.app|defaults read /Applications/KiCad/KiCad.app/Contents/Info CFBundleShortVersionString"
  "ltspice|LTspice|cask|ltspice|test -d /Applications/LTspice.app|defaults read /Applications/LTspice.app/Contents/Info CFBundleShortVersionString"
  "obsidian|Obsidian|cask|obsidian|test -d /Applications/Obsidian.app|defaults read /Applications/Obsidian.app/Contents/Info CFBundleShortVersionString"
)
TODO_INSTALL=(); TODO_UPGRADE=()
for row in "${TOOLS[@]}"; do
  IFS='|' read -r key label kind name test ver <<< "$row"
  if eval "$test" >/dev/null 2>&1; then
    v="$(eval "$ver" 2>/dev/null | head -1)"
    managed=0; outdated=0
    if command -v brew >/dev/null; then
      if [[ $kind == cask ]]; then brew list --cask "$name" >/dev/null 2>&1 && managed=1
      else brew list --formula "$name" >/dev/null 2>&1 && managed=1; fi
      flag=--formula; [[ $kind == cask ]] && flag=--cask
      (( managed )) && [[ -n "$(brew outdated --quiet "$flag" "$name" 2>/dev/null)" ]] && outdated=1
    fi
    if (( outdated )); then old "$label" "$v: a newer version is available"; TODO_UPGRADE+=("$kind:$name:$label")
    else ok "$label" "$v$( (( managed )) || echo ' (installed outside Homebrew: update it from the app)')"; fi
  else
    miss "$label" "brew install $([[ $kind == cask ]] && echo '--cask ')$name"; TODO_INSTALL+=("$kind:$name:$label")
  fi
done
if command -v claude >/dev/null; then ok "Claude Code" "$(claude --version 2>/dev/null | head -1)"; CLAUDE_OK=1
else miss "Claude Code" "curl -fsSL https://claude.ai/install.sh | bash"; CLAUDE_OK=0; fi

if (( ! CHECK )); then
  for t in "${TODO_INSTALL[@]+"${TODO_INSTALL[@]}"}"; do
    IFS=: read -r kind name label <<< "$t"
    note=""; [[ $name == kicad ]] && note=" (a large download, about 1 GB)"
    if ask "Install $label$note?"; then
      if [[ $kind == cask ]]; then brew install --cask "$name" < "$TTY"; else brew install "$name"; fi || warn "$label: install failed"
    else warn "$label skipped: the features that need it stay off (forge doctor lists them)"; fi
  done
  if (( UPDATE )); then
    for t in "${TODO_UPGRADE[@]+"${TODO_UPGRADE[@]}"}"; do
      IFS=: read -r kind name label <<< "$t"
      ask "Update $label?" && { if [[ $kind == cask ]]; then brew upgrade --cask "$name"; else brew upgrade "$name"; fi || warn "$label: update failed"; }
    done
  elif (( ${#TODO_UPGRADE[@]} )); then
    say "${#TODO_UPGRADE[@]} tool(s) can be updated: run again with --update (or: forge update)"
  fi
  if (( ! CLAUDE_OK )) && ask "Install Claude Code?"; then
    curl -fsSL https://claude.ai/install.sh | bash && export PATH="$HOME/.local/bin:$PATH"
  elif (( CLAUDE_OK && UPDATE )); then
    claude update >/dev/null 2>&1 && say "Claude Code updated" || true
  fi
fi

# ------------------------------------------------------------------ 4. pe-forge itself
say "pe-forge"
SELF="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd)"
if [[ -n "$SELF" && -f "$SELF/forge/__main__.py" ]]; then PE="$SELF"; else PE="${PE_FORGE:-$HOME/pe-forge}"; fi
if [[ -d "$PE/.git" ]]; then
  if (( UPDATE && ! CHECK )); then
    if [[ -z "$(git -C "$PE" status --porcelain)" ]]; then
      git -C "$PE" pull --ff-only -q && ok "pe-forge" "updated: $(git -C "$PE" log -1 --format='%h %s' | cut -c1-60)" \
        || warn "pe-forge: could not update (local commits or no network): git -C $PE pull"
    else warn "pe-forge at $PE has local changes: not updated (commit or stash them, then run this again)"; fi
  else ok "pe-forge" "$PE ($(git -C "$PE" log -1 --format='%h %cs'))"; fi
elif (( CHECK )); then miss "pe-forge" "git clone $REPO_URL $PE"
else
  say "downloading pe-forge into $PE"
  git clone -q "$REPO_URL" "$PE" || { warn "could not clone $REPO_URL. If the repository is private, ask its owner for access and log in to GitHub (brew install gh; gh auth login), then run this again."; exit 1; }
  ok "pe-forge" "$PE"
fi

if (( CHECK )); then
  [[ -x "$PE/bin/forge" ]] && "$PE/bin/forge" doctor 2>/dev/null | sed 's/^/  /'
  say "check done"; exit 0
fi
say "setting up pe-forge (venv, PATH, Claude Code, plugins, KiCad library)"
BOOT=(); (( YES )) && BOOT+=(--yes); (( UPDATE )) && BOOT+=(--update)
bash "$PE/bootstrap.sh" "${BOOT[@]+"${BOOT[@]}"}" < "$TTY" || warn "bootstrap reported a problem (see above)"
FORGE="$PE/bin/forge"

# ------------------------------------------------------------------ 5. a project (an Obsidian vault)
if (( PROJECT )) && (( ! UPDATE )); then
  echo
  say "your project (every pe-forge project is also an Obsidian vault)"
  echo "  1) start a fresh project      2) reuse an existing pe-forge project      3) not now"
  choice="$(askv 'Choose' 1)"
  if [[ "$choice" == 1 ]]; then
    name="$(askv 'Project name' 'MyConverter')"
    dir="$(askv 'Folder' "$PROJECTS/$name")"; dir="${dir/#\~/$HOME}"
    desc="$(askv 'One line: what are you building?' '48 V to 12 V, 1 A DC-DC converter')"
    if [[ -e "$dir" && -n "$(ls -A "$dir" 2>/dev/null | grep -v -e '^\.obsidian$' -e '^\.DS_Store$')" ]]; then
      warn "$dir is not empty: choose a new folder"; exit 1
    fi
    mkdir -p "$dir"
    "$FORGE" vault open "$dir" | sed 's/^/  /'
    ok "vault" "$dir"
    if command -v claude >/dev/null && ask "Start Claude Code there now (it asks the intake questions and builds the project)?"; then
      echo "  ${D}If this is Claude Code's first run, it asks you to log in first.${N}"
      cd "$dir" && exec claude "/pe-forge:pe-new $desc" < "$TTY"
    fi
    echo "  later: cd \"$dir\" && claude, then type /pe-forge:pe-new $desc"
  elif [[ "$choice" == 2 ]]; then
    dir="$(askv 'Project folder' "$PROJECTS")"; dir="${dir/#\~/$HOME}"
    if [[ ! -f "$dir/project.toml" ]]; then
      warn "$dir has no project.toml, so it is not a pe-forge project (projects made elsewhere: forge adopt, coming)"; exit 1
    fi
    "$FORGE" vault open "$dir" | sed 's/^/  /'
    (cd "$dir" && "$FORGE" statusline install >/dev/null && ok "status line" "installed for this project")
    if [[ -d "$dir/.git" ]] && ask "Install the commit check (forge check before each git commit)?"; then
      (cd "$dir" && "$FORGE" hooks install --chain | sed 's/^/  /')
    fi
    if command -v claude >/dev/null && ask "Start Claude Code there now?"; then
      cd "$dir" && exec claude "/pe-forge:pe-run" < "$TTY"
    fi
    echo "  later: cd \"$dir\" && claude, then /pe-forge:pe-run (and /results for the panel)"
  fi
fi

echo
say "done. Start a project:  mkdir my-converter && cd my-converter && claude   then   /pe-forge:pe-new <what you are building>"
say "update everything later:  forge update        check the toolchain:  forge doctor"
