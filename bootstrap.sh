#!/usr/bin/env bash
# pe-forge bootstrap: one command from a fresh clone to a working toolkit.
#   ./bootstrap.sh            interactive (asks before touching Claude Code or KiCad settings)
#   ./bootstrap.sh --yes      accept every default
#   ./bootstrap.sh --update   also refresh the Python packages and update the installed plugins
# install.sh (the one-command installer) runs this after it has installed the tools.
# What it does (each step is skipped if already done):
#   1. checks python ≥ 3.11 and git; reports KiCad 10, LTspice, ngspice
#   2. builds .venv and installs pwrlab (vendor/pwrlab) + PyMuPDF
#   3. links bin/forge into ~/.local/bin
#   4. sets PE_FORGE (and MC_LIB if unset) in ~/.claude/settings.json "env"
#   5. registers the pwrlab and forge MCP servers (user scope) unless they exist
#   6. adds this repo as a Claude Code plugin marketplace and installs the pe-forge plugin
#   7. optionally registers the parts library in KiCad (forge kicad-setup --apply)
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
YES=0; UPDATE=0
for a in "$@"; do [[ "$a" == "--yes" ]] && YES=1; [[ "$a" == "--update" ]] && UPDATE=1; done
say()  { printf '\033[1m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[33m!!\033[0m %s\n' "$*"; }
ask()  { (( YES )) && return 0; read -r -p "$1 [Y/n] " a; [[ -z "$a" || "$a" =~ ^[Yy] ]]; }

say "pe-forge at $HERE"
PY=""
for c in python3.13 python3.12 python3.11 python3; do
  if command -v "$c" >/dev/null && "$c" -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then PY=$(command -v "$c"); break; fi
done
[[ -n "$PY" ]] || { warn "Python 3.11+ is required (python.org or your package manager)"; exit 1; }
command -v git >/dev/null || { warn "git is required"; exit 1; }
say "python: $PY"

# 2. venv + pwrlab
if [[ ! -x "$HERE/.venv/bin/pwrlab-mcp" ]]; then
  say "building .venv with pwrlab"
  "$PY" -m venv "$HERE/.venv"
  "$HERE/.venv/bin/python" -m pip install -q --upgrade pip
  "$HERE/.venv/bin/python" -m pip install -q -e "$HERE/vendor/pwrlab" pymupdf pytest
elif (( UPDATE )); then
  say "refreshing the .venv packages"
  "$HERE/.venv/bin/python" -m pip install -q --upgrade pip
  "$HERE/.venv/bin/python" -m pip install -q --upgrade -e "$HERE/vendor/pwrlab" pymupdf pytest
fi
"$HERE/.venv/bin/python" -c "import fitz" 2>/dev/null || warn "PyMuPDF missing in .venv: schematic renders and text checks need it"

# 3. forge on PATH
mkdir -p "$HOME/.local/bin"
ln -sf "$HERE/bin/forge" "$HOME/.local/bin/forge"
if [[ ":$PATH:" != *":$HOME/.local/bin:"* ]]; then
  RC="$HOME/.zshrc"; [[ "${SHELL:-}" == */bash ]] && RC="$HOME/.bash_profile"
  if grep -qs 'pe-forge: forge on PATH' "$RC"; then :
  elif ask "Add ~/.local/bin to PATH in $RC (so 'forge' works in every terminal)?"; then
    printf '\n# pe-forge: forge on PATH\nexport PATH="$HOME/.local/bin:$PATH"\n' >> "$RC" && say "PATH line added to $RC (new terminals)"
  else warn "add ~/.local/bin to PATH to use 'forge' directly"; fi
  export PATH="$HOME/.local/bin:$PATH"
fi

# 4. Claude Code env
SETTINGS="$HOME/.claude/settings.json"
if ask "Set PE_FORGE (and MC_LIB if unset) in $SETTINGS?"; then
  mkdir -p "$HOME/.claude"
  "$PY" - "$SETTINGS" "$HERE" <<'EOF'
import json, os, shutil, sys
p, here = sys.argv[1], sys.argv[2]
d = json.load(open(p)) if os.path.exists(p) else {}
if os.path.exists(p): shutil.copy(p, p + ".pe-forge.bak")
env = d.setdefault("env", {})
env["PE_FORGE"] = here
env.setdefault("MC_LIB", os.path.join(here, "library"))
json.dump(d, open(p, "w"), indent=2)
print(f"   PE_FORGE={here}  MC_LIB={env['MC_LIB']}")
EOF
fi

# 5. pwrlab MCP + 6. plugin
if command -v claude >/dev/null; then
  if claude mcp get pwrlab >/dev/null 2>&1; then
    say "MCP server 'pwrlab' already registered: kept"
  elif ask "Register the pwrlab MCP server (user scope)?"; then
    claude mcp add pwrlab -s user -- "$HERE/.venv/bin/pwrlab-mcp" && say "pwrlab MCP registered"
  fi
  if claude mcp get forge >/dev/null 2>&1; then
    say "MCP server 'forge' already registered: kept"
  elif ask "Register the forge MCP server (user scope; outcome-level tools for any MCP host)?"; then
    claude mcp add forge -s user -- "$HERE/bin/forge-mcp" && say "forge MCP registered"
  fi
  if ask "Install or update the pe-forge plugins (pe-forge, and pe-forge-panel: the results panel beside the chat)?"; then
    claude plugin marketplace add "$HERE" >/dev/null 2>&1 || claude plugin marketplace update pe-forge >/dev/null 2>&1 || true
    for p in pe-forge pe-forge-panel; do
      if claude plugin list 2>/dev/null | grep -q "$p@pe-forge"; then
        claude plugin update "$p@pe-forge" >/dev/null 2>&1 && say "plugin $p updated (restart Claude Code to apply)" || say "plugin $p: up to date"
      else
        claude plugin install "$p@pe-forge" >/dev/null 2>&1 && say "plugin $p installed" \
          || warn "plugin $p: install failed; inside Claude Code run  /plugin marketplace add $HERE  then  /plugin install $p@pe-forge"
      fi
    done
  fi
else
  warn "claude CLI not found: install Claude Code (https://claude.com/claude-code), then re-run"
fi

# simulator on Linux
if [[ "$(uname)" == "Linux" && -z "${PWRLAB_LTSPICE:-}" ]]; then
  exe=$(ls "$HOME"/.wine/drive_c/Program\ Files/ADI/LTspice/LTspice.exe 2>/dev/null || true)
  [[ -n "$exe" ]] && warn "set PWRLAB_LTSPICE=\"wine '$exe'\" in your shell profile or Claude settings env"
fi

# 7. KiCad
"$HERE/bin/forge" kicad-setup || true
if ask "Apply the KiCad library setup above (KiCad must be closed)?"; then "$HERE/bin/forge" kicad-setup --apply; fi

say "toolchain:"
"$HERE/bin/forge" doctor || true
say "done. In Claude Code, in an empty folder:  /pe-forge:pe-new  (then /pe-forge:pe-run to continue a project)"
