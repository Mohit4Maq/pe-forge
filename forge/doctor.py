"""forge doctor: check the toolchain and say which features are available.

Each tool is optional except Python 3.11 and git; a missing one disables only the features that need it.
"""
import os
import shutil
import sqlite3
import subprocess
import sys

from . import config

KICAD_CLI = [os.environ.get("KICAD_CLI"), shutil.which("kicad-cli"),
             "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli", "/usr/bin/kicad-cli"]
LTSPICE = ["/Applications/LTspice.app", os.path.expanduser("~/.wine/drive_c/Program Files/ADI/LTspice/LTspice.exe"),
           os.path.expanduser("~/.wine/drive_c/Program Files/LTC/LTspiceXVII/XVIIx64.exe")]


def first(paths):
    return next((p for p in paths if p and os.path.exists(p)), None)


def run(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=20).stdout.strip()
    except Exception:
        return ""


def main(argv):
    rows = []
    ok = lambda c, name, note, feat: rows.append(("OK " if c else "-- ", name, note, feat))
    ok(sys.version_info >= (3, 11), "python ≥ 3.11", sys.version.split()[0], "everything (required)")
    ok(bool(shutil.which("git")), "git", run(["git", "--version"]), "projects, change watch (required)")
    k = first(KICAD_CLI)
    ok(bool(k), "kicad-cli (KiCad 10)", run([k, "version"]) if k else "install KiCad 10: kicad.org/download",
       "P6–P8: schematic drawing, ERC / DRC, release outputs")
    lt = first(LTSPICE)
    ok(bool(lt), "LTspice", lt or "install LTspice (analog.com/ltspice); on Linux also wine",
       "P5: pwrlab simulations")
    venv = os.path.join(config.FORGE_ROOT, ".venv", "bin", "python")
    pl = os.path.exists(venv) and run([venv, "-c", "import pwrlab; print('ok')"]) == "ok"
    ok(pl, "pwrlab venv", venv if pl else "run ./bootstrap.sh", "P5: pwrlab MCP server")
    ng = shutil.which("ngspice")
    ok(bool(ng), "ngspice (optional)", ng or "brew install ngspice / apt install ngspice",
       "fallback analog checks (kicad-happy spice)")
    try:
        import fitz  # noqa: F401
        fz = True
    except Exception:
        fz = False
    ok(fz, "PyMuPDF (optional)", "" if fz else "pip install pymupdf", "rendering sheets to PNG, text-overlap checks")
    db = os.path.join(config.LIBRARY, "database", "mc_parts.sqlite")
    try:
        n = sum(sqlite3.connect(db).execute(f"select count(*) from {t}").fetchone()[0]
                for (t,) in sqlite3.connect(db).execute("select name from sqlite_master where type='table'"))
    except Exception:
        n = 0
    ok(n > 0, "MC_Lib parts database", f"{n} parts at {config.LIBRARY}", "part lookup, kicad-place, schdraw")
    mk = bool(os.environ.get("MOUSER_API_KEY") or os.path.exists(os.path.expanduser("~/.mouser_key")))
    ok(mk, "Mouser API key", "set" if mk else "optional: MOUSER_API_KEY or ~/.mouser_key", "live price / stock")
    dk = bool(os.environ.get("DIGIKEY_CLIENT_ID"))
    ok(dk, "DigiKey API (optional)", "set" if dk else "DIGIKEY_CLIENT_ID / _SECRET", "price / stock, datasheets")
    ok(bool(shutil.which("claude")), "claude CLI", run(["claude", "--version"]), "plugin install, agents")
    w = max(len(r[1]) for r in rows)
    for s, name, note, feat in rows:
        print(f"{s} {name:<{w}}  {note:<48.48}  → {feat}")
    return 0 if all(r[0].startswith("OK") for r in rows[:2]) else 1
