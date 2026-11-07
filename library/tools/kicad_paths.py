"""Where KiCad lives, for the library tools. Uses pe-forge's forge.kicad (the single source of truth) when it can be
imported: from $PE_FORGE, or from the repo this library sits in. Otherwise (library copied elsewhere, no pe-forge)
it falls back to the same search order, kept identical to forge/kicad.py by hand.
"""
import os
import shutil
import sys
from pathlib import Path

_kc = None
for _root in (os.environ.get("PE_FORGE"), str(Path(__file__).resolve().parents[2])):
    if _root and (Path(_root) / "forge" / "kicad.py").exists():
        sys.path.insert(0, _root)
        try:
            from forge import kicad as _kc  # noqa: E402
        except Exception:
            _kc = None
        sys.path.remove(_root)
        if _kc:
            break

# Fallback copy of forge/kicad.py's search order (documented there).
_MAC = "/Applications/KiCad/KiCad.app"
_CLI = [_MAC + "/Contents/MacOS/kicad-cli", "/usr/bin/kicad-cli", "/usr/local/bin/kicad-cli", "/snap/bin/kicad-cli",
        "/var/lib/flatpak/exports/bin/kicad-cli"]
_PY = [_MAC + "/Contents/Frameworks/Python.framework/Versions/Current/bin/python3"]
_SYM = [_MAC + "/Contents/SharedSupport/symbols", "/usr/share/kicad/symbols", "/usr/local/share/kicad/symbols"]


def _first(c):
    return next((x for x in c if x and os.path.exists(x)), None)


def cli():
    """kicad-cli path (or the bare name, so a missing KiCad fails loudly at the call)."""
    if _kc:
        return _kc.find_cli() or "kicad-cli"
    return _first([os.environ.get("KICAD_CLI"), shutil.which("kicad-cli"), *_CLI]) or "kicad-cli"


def python():
    if _kc:
        return _kc.find_python() or "python3"
    return _first([os.environ.get("KICAD_PYTHON"), *_PY, shutil.which("python3")]) or "python3"


def stock_symbols():
    if _kc:
        return _kc.stock_symbols()
    return _first([os.environ.get("KICAD_SYMBOL_DIR"), os.environ.get("KICAD10_SYMBOL_DIR"), *_SYM])
