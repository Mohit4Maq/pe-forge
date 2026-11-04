"""forge kicad-setup [--apply]: register the pe-forge library in KiCad 10 (global tables), set the MC_LIB path
variable, and point the parts database at this OS's SQLite ODBC driver. Dry run by default; --apply writes
after backing up every file it touches (<file>.pe-forge.bak).

Needs the SQLite ODBC driver: macOS `brew install sqliteodbc`, Debian / Ubuntu `apt install libsqliteodbc`,
Fedora `dnf install sqliteodbc`, Windows: install from http://www.ch-werner.de/sqliteodbc/.
"""
import glob
import json
import os
import platform
import re
import shutil

from . import config

DRIVERS = ["/opt/homebrew/lib/libsqlite3odbc.dylib", "/usr/local/lib/libsqlite3odbc.dylib",
           "/usr/lib/x86_64-linux-gnu/odbc/libsqlite3odbc.so", "/usr/lib/aarch64-linux-gnu/odbc/libsqlite3odbc.so",
           "/usr/lib64/libsqlite3odbc.so", "/usr/lib/libsqlite3odbc.so"]


def kicad_config_dir():
    home = os.path.expanduser("~")
    c = {"Darwin": f"{home}/Library/Preferences/kicad/10.0", "Windows": os.path.join(os.environ.get("APPDATA", ""), "kicad", "10.0")}
    return c.get(platform.system(), f"{home}/.config/kicad/10.0")


def main(argv):
    apply = "--apply" in argv
    lib = os.path.join(config.FORGE_ROOT, "library")
    cfg = kicad_config_dir()
    todo = []
    syms = sorted(glob.glob(os.path.join(lib, "symbols", "*.kicad_sym")))
    fps = sorted(glob.glob(os.path.join(lib, "footprints", "*.pretty")))
    sym_rows = [(os.path.basename(s)[:-10], "KiCad", f"${{MC_LIB}}/symbols/{os.path.basename(s)}") for s in syms]
    sym_rows.append(("MC_Parts", "Database", "${MC_LIB}/MC_Parts.kicad_dbl"))
    fp_rows = [(os.path.basename(f)[:-7], "KiCad", f"${{MC_LIB}}/footprints/{os.path.basename(f)}") for f in fps]
    for table, rows in (("sym-lib-table", sym_rows), ("fp-lib-table", fp_rows)):
        path = os.path.join(cfg, table)
        txt = open(path).read() if os.path.exists(path) else f"({table.replace('-', '_')}\n  (version 7)\n)\n"
        add = [r for r in rows if f'(name "{r[0]}")' not in txt]
        if add:
            ins = "".join(f'  (lib (name "{n}")(type "{t}")(uri "{u}")(options "")(descr "pe-forge"))\n' for n, t, u in add)
            todo.append((path, txt[:txt.rstrip().rindex(")")] + ins + ")\n", f"{table}: add {', '.join(r[0] for r in add)}"))
    kc = os.path.join(cfg, "kicad_common.json")
    if os.path.exists(kc):
        j = json.load(open(kc))
        cur = j.setdefault("environment", {}).setdefault("vars", {}) or {}
        if cur.get("MC_LIB") != lib:
            j["environment"]["vars"] = dict(cur, MC_LIB=lib)
            todo.append((kc, json.dumps(j, indent=2) + "\n",
                         f"kicad_common.json: MC_LIB = {lib}" + (f" (was {cur['MC_LIB']})" if cur.get("MC_LIB") else "")))
    else:
        print(f"KiCad 10 config not found at {cfg}: start KiCad once, then re-run")
    drv = next((d for d in DRIVERS if os.path.exists(d)), None)
    dbl = os.path.join(lib, "MC_Parts.kicad_dbl")
    t = open(dbl).read()
    if drv:
        n = re.sub(r"Driver=[^;]*;", f"Driver={drv};", t)
        if n != t:
            todo.append((dbl, n, f"MC_Parts.kicad_dbl: ODBC driver {drv}"))
    else:
        print("SQLite ODBC driver not found: " + __doc__.split("Needs the SQLite ODBC driver: ")[1].strip())
    if not todo:
        print("KiCad already set up for pe-forge")
        return 0
    for path, new, what in todo:
        print(("write " if apply else "would ") + what)
        if apply:
            if os.path.exists(path) and path != dbl:
                shutil.copy(path, path + ".pe-forge.bak")
            open(path, "w").write(new)
    if not apply:
        print("dry run: re-run with --apply (KiCad must be closed)")
    if os.environ.get("MC_LIB") and os.path.abspath(os.environ["MC_LIB"]) != lib:
        print(f"note: your MC_LIB env var points at {os.environ['MC_LIB']}; agents will use that parts database")
    return 0
