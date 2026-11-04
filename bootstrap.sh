#!/usr/bin/env bash
# pe-forge bootstrap: one command from a fresh clone to a working toolkit.
#   ./bootstrap.sh            interactive (asks before touching Claude Code or KiCad settings)
#   ./bootstrap.sh --yes      accept every default
# What it does (each step is skipped if already done):
#   1. checks python ≥ 3.11 and git; reports KiCad 10, LTspice, ngspice
#   2. builds .venv and installs pwrlab (vendor/pwrlab) + PyMuPDF
#   3. links bin/forge into ~/.local/bin
#   4. sets PE_FORGE (and MC_LIB if unset) in ~/.claude/settings.json "env"
#   5. registers the pwrlab MCP server (user scope) unless one named "pwrlab" exists
#   6. adds this repo as a Claude Code plugin marketplace and installs the pe-forge plugin
#   7. optionally registers the parts library in KiCad (forge kicad-setup --apply)
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
YES=0; [[ "${1:-}" == "--yes" ]] && YES=1
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
fi
"$HERE/.venv/bin/python" -c "import fitz" 2>/dev/null || warn "PyMuPDF missing in .venv: schematic renders and text checks need it"

# 3. forge on PATH
mkdir -p "$HOME/.local/bin"
ln -sf "$HERE/bin/forge" "$HOME/.local/bin/forge"
[[ ":$PATH:" == *":$HOME/.local/bin:"* ]] || warn "add ~/.local/bin to PATH to use 'forge' directly"

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
  if ask "Install the pe-forge Claude Code plugin from this folder?"; then
    claude plugin marketplace add "$HERE" 2>/dev/null || true
    claude plugin install pe-forge@pe-forge 2>/dev/null && say "plugin installed" \
      || warn "plugin install failed: inside Claude Code run  /plugin marketplace add $HERE  then  /plugin install pe-forge@pe-forge"
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
say "done. In Claude Code, in an empty folder:  /pe-new  (then /pe-run to continue a project)"
