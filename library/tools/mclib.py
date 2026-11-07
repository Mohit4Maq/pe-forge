#!/usr/bin/env python3
"""mclib - manage the MC_Lib KiCad library (SQLite database library + git symbol/footprint libs).

Standard library only. Run `mclib.py -h` or `mclib.py <command> -h`.

  mclib.py find 10k 0603                 search all tables
  mclib.py show RES-0012                 show one part
  mclib.py add Resistors MPN=... Value=10k Symbols=MC_Generic:R Footprints=Resistor_SMD:R_0603_1608Metric
  mclib.py set RES-0012 DigiKey_PN=311-10.0KHRCT-ND
  mclib.py import <folder|zip> --table ICs --lib MC_IC      Altium/KiCad/UL/SnapEDA download -> library + DB row
  mclib.py check                         validate every DB row resolves to a real symbol/footprint
  mclib.py lint-fp                       footprint hygiene (courtyard, fab, ref)
  mclib.py gen-dbl                       regenerate MC_Parts.kicad_dbl from TABLES below
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.environ["MC_DB"]) if os.environ.get("MC_DB") else ROOT / "database" / "mc_parts.sqlite"
SCHEMA = ROOT / "database" / "schema.sql"
SYM_DIR = ROOT / "symbols"
FP_DIR = ROOT / "footprints"
MODEL_DIR = ROOT / "3dmodels"
DBL_PATH = ROOT / "MC_Parts.kicad_dbl"

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kicad_paths  # noqa: E402

KICAD_CLI = Path(kicad_paths.cli())
STOCK = Path(kicad_paths.stock_symbols() or "/usr/share/kicad/symbols").parent   # .../symbols -> its parent
ODBC_DRIVER = "/opt/homebrew/lib/libsqlite3odbc.dylib"

# table -> (ID prefix, extra columns shown in the KiCad chooser)
TABLES: dict[str, tuple[str, list[str]]] = {
    "Resistors":       ("RES", ["Tolerance", "Power_Rating", "Voltage_Rating"]),
    "Capacitors":      ("CAP", ["Voltage_Rating", "Dielectric", "Tolerance"]),
    "Magnetics":       ("MAG", ["Current_Rating", "Tolerance"]),
    "Diodes":          ("DIO", ["Voltage_Rating", "Current_Rating"]),
    "Transistors":     ("TRN", ["Voltage_Rating", "Current_Rating"]),
    "ICs":             ("IC",  []),
    "Connectors":      ("CON", ["Current_Rating"]),
    "Optoelectronics": ("OPT", []),
    "Protection":      ("PRT", ["Voltage_Rating", "Current_Rating"]),
    "Sensors":         ("SNS", ["Tolerance"]),
    "Crystals":        ("XTL", ["Tolerance"]),
    "Mechanical":      ("MEC", []),
}
# fields copied onto the placed schematic symbol (and therefore into BOMs)
SOURCING_FIELDS = ["Mouser_Stock", "Mouser_Price_1", "Mouser_Price_100", "Lifecycle", "Sourcing_Date"]
BOM_FIELDS = ["MPN", "Manufacturer", "DigiKey_PN", "Mouser_PN", "LCSC_PN", "Package", "Tolerance",
              "Voltage_Rating", "Current_Rating", "Power_Rating", "Dielectric", "AEC_Q",
              "Alt_MPN", "Alt_Manufacturer", "Status"]


# ---------------------------------------------------------------- database
def db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def columns() -> list[str]:
    body = SCHEMA.read_text().split("CREATE TABLE IF NOT EXISTS _template (", 1)[1]
    return re.findall(r"^\s+(\w+)\s+TEXT", body, re.M)


SCHEMA_VERSION = 2      # 2: LCSC_PN, JLC_Rotation


def init_db() -> None:
    body = SCHEMA.read_text().split("CREATE TABLE", 1)[1]
    con = db()
    for t in TABLES:
        con.execute("CREATE TABLE" + body.replace("_template", t))
        have = {r[1] for r in con.execute(f"PRAGMA table_info({t})")}
        for c in columns():  # schema grew -> add missing columns
            if c not in have:
                con.execute(f"ALTER TABLE {t} ADD COLUMN {c} TEXT NOT NULL DEFAULT ''")
    con.execute("CREATE TABLE IF NOT EXISTS _meta (key TEXT PRIMARY KEY NOT NULL, value TEXT NOT NULL DEFAULT '')")
    con.execute("INSERT OR REPLACE INTO _meta (key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
    con.commit()


def next_id(con: sqlite3.Connection, table: str) -> str:
    prefix = TABLES[table][0]
    rows = con.execute(f"SELECT ID FROM {table} WHERE ID LIKE ?", (prefix + "-%",)).fetchall()
    n = max((int(r[0].split("-")[1]) for r in rows), default=0)
    return f"{prefix}-{n + 1:04d}"


def insert(con: sqlite3.Connection, table: str, row: dict, validate: bool = True) -> str:
    if table not in TABLES:
        sys.exit(f"unknown table {table!r}; one of {', '.join(TABLES)}")
    bad = set(row) - set(columns())
    if bad:
        sys.exit(f"unknown column(s) {sorted(bad)}; valid: {columns()}")
    if row.get("MPN"):
        dup = con.execute(f"SELECT ID FROM {table} WHERE MPN=?", (row["MPN"],)).fetchone()
        if dup:
            return dup[0]
    row.setdefault("Keywords", " ".join(filter(None, [row.get("MPN"), row.get("Manufacturer"),
                                                      row.get("Value"), row.get("Package")])))
    if validate:
        errs = resolve_errors(row)
        if errs:
            sys.exit("refusing to add part:\n  " + "\n  ".join(errs))
    row["ID"] = row.get("ID") or next_id(con, table)
    cols = list(row)
    con.execute(f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                [row[c] for c in cols])
    return row["ID"]


def record_sourcing(con, table: str, pid: str, quiet: bool = False) -> str:
    """Look the part up at Mouser and store stock/price/lifecycle (+ verified Mouser_PN) on the row."""
    import mouser
    from datetime import date
    r = con.execute(f"SELECT MPN, Manufacturer, Mouser_PN, Exclude_BOM FROM {table} WHERE ID=?", (pid,)).fetchone()
    if not r or not r[0] or r[3] == "1":
        return "skipped"
    p = mouser.lookup(r[0], mfr=r[1] or None)
    today = date.today().isoformat()
    if not p:
        con.execute(f"UPDATE {table} SET Lifecycle=?, Mouser_Stock='', Mouser_Price_1='', Mouser_Price_100='', "
                    f"Mouser_Lead_Time='', Sourcing_Date=? WHERE ID=?", ("Not at Mouser", today, pid))
        msg = "not listed at Mouser"
    else:
        f = lambda q: (lambda v: f"{p['sym']}{v:,.2f}" if v is not None else "")(mouser.unit_price(p, q))
        con.execute(f"UPDATE {table} SET Mouser_PN=?, Mouser_Stock=?, Mouser_Price_1=?, Mouser_Price_100=?, "
                    f"Mouser_Lead_Time=?, Lifecycle=?, Sourcing_Date=? WHERE ID=?",
                    (p["mouser_pn"], str(p["stock"]), f(1), f(100), p["lead_time"], p["lifecycle"] or "Active", today, pid))
        fixed = f" (Mouser_PN was {r[2]})" if r[2] and r[2] != p["mouser_pn"] else ""
        msg = f"{p['mouser_pn']}{fixed}: stock {p['stock']:,}, {f(1)} @1, {f(100)} @100, lead {p['lead_time'] or '?'}"
    if not quiet:
        print(f"  Mouser → {msg}")
    return msg


def cmd_sourcing(a) -> int:
    """Refresh Mouser data for library rows (oldest/missing first), within the daily API allowance."""
    import mouser
    from datetime import date, timedelta
    con = db()
    cutoff = (date.today() - timedelta(days=a.older_than)).isoformat()
    todo = []
    for t in ([a.table] if a.table else TABLES):
        for r in con.execute(f"SELECT ID, MPN, Sourcing_Date FROM {t} WHERE MPN!='' AND Exclude_BOM!='1'"):
            if a.all or r["Sourcing_Date"] < cutoff or not r["Sourcing_Date"]:
                todo.append((r["Sourcing_Date"], t, r["ID"], r["MPN"]))
    todo.sort()
    todo = todo[:a.limit]
    print(f"{len(todo)} parts to look up (≈{len(todo) * 2.2 / 60:.0f} min, cached results are instant)")
    stats = {"found": 0, "missing": 0, "fixed": 0, "error": 0}
    for n, (_, t, pid, mpn) in enumerate(todo, 1):
        try:
            msg = record_sourcing(con, t, pid, quiet=True)
            key = "missing" if msg.startswith("not listed") else "found"
            stats[key] += 1
            stats["fixed"] += "was " in msg
            if a.verbose or "was " in msg or key == "missing":
                print(f"  {pid:9} {mpn[:26]:26} {msg}")
        except Exception as e:
            stats["error"] += 1
            print(f"  {pid:9} {mpn[:26]:26} ERROR {e}")
            if "Too Many" in str(e) or "429" in str(e):
                print("Mouser rate limit reached: stopping; rerun later to continue")
                break
        if n % 25 == 0:
            con.commit()
            print(f"  … {n}/{len(todo)}")
    con.commit()
    print(f"done: {stats['found']} found, {stats['missing']} not at Mouser, {stats['fixed']} Mouser_PN corrected, "
          f"{stats['error']} errors")
    return 0


def find_part(con, pid: str):
    for t in TABLES:
        r = con.execute(f"SELECT * FROM {t} WHERE ID=?", (pid,)).fetchone()
        if r:
            return t, r
    sys.exit(f"no part {pid}")


# ---------------------------------------------------------------- s-expression helpers
def children(text: str) -> list[str]:
    """Top-level child blocks of the root s-expression (string/escape aware)."""
    out, depth, start, i, n = [], 0, None, 0, len(text)
    while i < n:
        ch = text[i]
        if ch == '"':
            i += 1
            while text[i] != '"':
                i += 2 if text[i] == "\\" else 1
        elif ch == "(":
            depth += 1
            if depth == 2:
                start = i
        elif ch == ")":
            if depth == 2:
                out.append(text[start:i + 1])
            depth -= 1
        i += 1
    return out


def sym_name(block: str) -> str:
    return re.match(r'\(symbol "((?:[^"\\]|\\.)*)"', block).group(1)


def sym_props(block: str) -> dict[str, str]:
    return dict(re.findall(r'\n\t\t\(property "([^"]+)" "((?:[^"\\]|\\.)*)"', block))


def set_prop(block: str, key: str, value: str) -> str:
    value = value.replace("\\", "\\\\").replace('"', '\\"')
    pat = re.compile(r'(\n\t\t\(property "%s" ")((?:[^"\\]|\\.)*)(")' % re.escape(key))
    return pat.sub(lambda m: m.group(1) + value + m.group(3), block, count=1)


KEEP_PROPS = {"Reference", "Value", "Footprint", "Datasheet", "Description",
              "ki_keywords", "ki_fp_filters", "ki_locked"}


def strip_props(block: str) -> str:
    """Drop vendor/Altium parameters: the database is the single source of truth."""
    out, i = [], 0
    for m in re.finditer(r'\n\t\t\(property "([^"]+)"', block):
        if m.group(1) in KEEP_PROPS:
            continue
        end = _match_paren(block, m.start() + 3)
        out.append(block[i:m.start()])
        i = end + 1
    out.append(block[i:])
    return "".join(out)


def _match_paren(s: str, i: int) -> int:
    depth = 0
    while True:
        ch = s[i]
        if ch == '"':
            i += 1
            while s[i] != '"':
                i += 2 if s[i] == "\\" else 1
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1


def rename_symbol(block: str, old: str, new: str) -> str:
    o = re.escape(old)
    block = re.sub(r'\(symbol "%s"' % o, f'(symbol "{new}"', block)
    block = re.sub(r'\(symbol "%s_(\d+_\d+)"' % o, lambda m: f'(symbol "{new}_{m.group(1)}"', block)
    return re.sub(r'\(extends "%s"\)' % o, f'(extends "{new}")', block)


def safe_name(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._+-]+", "_", s).strip("_")


def read_symlib(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    return {sym_name(b): b for b in children(path.read_text()) if b.startswith("(symbol ")}


def write_symlib(path: Path, symbols: dict[str, str]) -> None:
    body = "".join("\n\t" + symbols[k] for k in sorted(symbols, key=str.lower))
    path.write_text('(kicad_symbol_lib\n\t(version 20251024)\n\t(generator "mclib")\n'
                    f'\t(generator_version "10.0"){body}\n)\n')


def add_symbols(lib: str, symbols: dict[str, str], overwrite: bool = False) -> None:
    path = SYM_DIR / f"{lib}.kicad_sym"
    cur = read_symlib(path)
    for k, v in symbols.items():
        if k in cur and not overwrite:
            continue
        cur[k] = v
    write_symlib(path, cur)


# ---------------------------------------------------------------- resolution / checks
def _lib_path(kind: str, lib: str) -> Path | None:
    if kind == "sym":
        for p in (SYM_DIR / f"{lib}.kicad_sym", STOCK / "symbols" / f"{lib}.kicad_sym"):
            if p.exists():
                return p
    else:
        for p in (FP_DIR / f"{lib}.pretty", STOCK / "footprints" / f"{lib}.pretty"):
            if p.exists():
                return p
    return None


_sym_cache: dict[Path, set[str]] = {}


def resolve_errors(row) -> list[str]:
    errs = []
    sym = row["Symbols"] if "Symbols" in row.keys() else ""
    if ":" not in sym:
        errs.append(f"Symbols {sym!r} must be Lib:Name")
    else:
        lib, name = sym.split(":", 1)
        p = _lib_path("sym", lib)
        if not p:
            errs.append(f"symbol library {lib} not found")
        else:
            if p not in _sym_cache:
                _sym_cache[p] = set(re.findall(r'^\t\(symbol "((?:[^"\\]|\\.)*)"', p.read_text(), re.M))
            if name not in _sym_cache[p]:
                errs.append(f"symbol {sym} not found")
    fps = row["Footprints"] if "Footprints" in row.keys() else ""
    excl = (row["Exclude_Board"] if "Exclude_Board" in row.keys() else "0") == "1"
    if not fps and not excl:
        errs.append("no Footprints (set Exclude_Board=1 for schematic-only parts)")
    for fp in filter(None, fps.split(";")):
        if ":" not in fp:
            errs.append(f"footprint {fp!r} must be Lib:Name")
            continue
        lib, name = fp.split(":", 1)
        p = _lib_path("fp", lib)
        if not p or not (p / f"{name}.kicad_mod").exists():
            errs.append(f"footprint {fp} not found")
    return errs


def cmd_check(a) -> int:
    con, bad, total = db(), 0, 0
    for t in TABLES:
        seen = {}
        for r in con.execute(f"SELECT * FROM {t} ORDER BY ID"):
            total += 1
            errs = resolve_errors(r)
            if not r["MPN"] and r["Exclude_BOM"] != "1":
                errs.append("missing MPN")
            if r["MPN"] and r["MPN"] in seen:
                errs.append(f"duplicate MPN with {seen[r['MPN']]}")
            seen[r["MPN"]] = r["ID"]
            for e in errs:
                print(f"{t}/{r['ID']} ({r['MPN']}): {e}")
            bad += bool(errs)
    print(f"checked {total} parts, {bad} with problems")
    return 1 if bad else 0


def cmd_fix_fp(a) -> int:
    """Re-apply courtyard clean-up (origin crosshairs, gaps) and KiCad canonical re-save in place."""
    for lib in a.libs or sorted(p.stem for p in FP_DIR.glob("*.pretty")):
        d = FP_DIR / f"{lib}.pretty"
        names = sorted(f.stem for f in d.glob("*.kicad_mod"))
        for n in names:
            f = d / f"{n}.kicad_mod"
            f.write_text(add_courtyard(remap_altium_layers(f.read_text())))
        canonicalize(d, names)
        print(f"{lib}: {len(names)} footprints cleaned and re-saved")
    return 0


def cmd_lint_fp(a) -> int:
    issues = 0
    for f in sorted(FP_DIR.glob("*.pretty/*.kicad_mod")):
        t, probs = f.read_text(), []
        if not re.search(r'\(layer "[FB]\.CrtYd"\)', t):
            probs.append("no courtyard")
        if not re.search(r'\(layer "[FB]\.Fab"\)', t):
            probs.append("no fab layer")
        if not re.search(r'\(layer "[FB]\.SilkS"\)', t):
            probs.append("no silkscreen")
        if "(model " not in t:
            probs.append("no 3D model")
        if probs:
            issues += 1
            print(f"{f.parent.stem}:{f.stem}: {', '.join(probs)}")
    print(f"{issues} footprints with issues")
    return 0


# ---------------------------------------------------------------- import
PARAM_MAP = {  # DB column <- vendor/Altium parameter names, first non-empty wins
    "MPN": ["Manufacturer Part Number", "Manufacturer_Part_Number", "MPN", "Part Number", "PartNumber"],
    "Manufacturer": ["Manufacturer", "Manufacturer_Name", "MANUFACTURER", "Mfr"],
    "Datasheet": ["Datasheet", "ComponentLink1URL", "DatasheetURL"],
    "Tolerance": ["Tolerance"],
    "Voltage_Rating": ["Voltage Rating", "Voltage - Rated", "Voltage"],
    "Current_Rating": ["Current Rating", "Current"],
    "Power_Rating": ["Power Rating", "Power"],
    "Temp_Range": ["Operating Temperature"],
    "Package": ["Case/Package", "Package/Size(H-W-L)/Case", "Package"],
    "Alt_MPN": ["Alt P/N 1", "Alt1 Manufacturer Part Number"],
    "Alt_Manufacturer": ["Alt MFG 1", "Alt1 Manufacturer"],
    "Description": ["Description", "Description_1"],
}
JUNK = {"", "not available", "not applicable", "na", "n/a", "-", "*"}


def params_to_row(props: dict[str, str]) -> dict[str, str]:
    row = {}
    for col, keys in PARAM_MAP.items():
        for k in keys:
            v = props.get(k, "").replace('\\"', '"').strip()
            if v.lower() not in JUNK:
                row[col] = v
                break
    grade = " ".join(props.get(k, "") for k in ("Automotive Grade", "Ratings/Grade"))
    aec = re.search(r"AEC[- ]?Q[- ]?(\d+)", grade.upper())
    if aec:
        row["AEC_Q"] = f"AEC-Q{aec.group(1)}"
    elif "AEC" in grade.upper():
        row["AEC_Q"] = "AEC"
    m = re.search(r"\b(X7R|X5R|X7S|X6S|X8R|C0G|NP0|Y5V)\b", props.get("Description", ""))
    if m:
        row["Dielectric"] = m.group(1)
    return row


def kicad_cli(*args: str) -> None:
    r = subprocess.run([str(KICAD_CLI), *args], capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(f"kicad-cli {' '.join(args)} failed: {r.stdout}{r.stderr}")


def convert_symbols(src: Path) -> dict[str, str]:
    """Any symbol lib KiCad can read (Altium SchLib/IntLib, legacy .lib, Eagle, .kicad_sym)."""
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "out.kicad_sym"
        kicad_cli("sym", "upgrade", "--force", "-o", str(out), str(src))
        return read_symlib(out)


def convert_footprints(src: Path, lib: str) -> list[str]:
    """Convert a footprint source into footprints/<lib>.pretty. Returns footprint names."""
    dest = FP_DIR / f"{lib}.pretty"
    dest.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        if src.suffix == ".kicad_mod":
            files = [src]
        else:
            kicad_cli("fp", "upgrade", "--force", "-o", str(Path(td) / "o.pretty"), str(src))
            files = sorted((Path(td) / "o.pretty").glob("*.kicad_mod"))
        names = []
        for f in files:
            name = safe_name(f.stem)
            t = re.sub(r'^\(footprint "((?:[^"\\]|\\.)*)"', f'(footprint "{name}"', f.read_text(), count=1)
            t = strip_ul_artifacts(t)
            if src.suffix.lower() != ".kicad_mod":
                t = add_courtyard(remap_altium_layers(t))
            (dest / f"{name}.kicad_mod").write_text(t)
            names.append(name)
        canonicalize(dest, names)
    return names


def canonicalize(lib_dir: Path, names: list[str]) -> None:
    """Re-save footprints through KiCad. Text-edited files can parse fine in `fp upgrade` yet make
    KiCad's library loader drop the whole library ("configuration does not include the footprint
    library"); KiCad's own serialisation is always accepted."""
    with tempfile.TemporaryDirectory() as td:
        src, out = Path(td) / "in.pretty", Path(td) / "out.pretty"
        src.mkdir()
        for n in names:
            shutil.copy2(lib_dir / f"{n}.kicad_mod", src / f"{n}.kicad_mod")
        kicad_cli("fp", "upgrade", "--force", "-o", str(out), str(src))
        for n in names:
            shutil.copy2(out / f"{n}.kicad_mod", lib_dir / f"{n}.kicad_mod")


def attach_model(lib: str, fp: str, step: Path) -> None:
    """Footprints without a model get ${MC_LIB}/3dmodels/<file> (Altium ones arrive embedded)."""
    f = FP_DIR / f"{lib}.pretty" / f"{fp}.kicad_mod"
    t = f.read_text()
    if "(model " in t:
        return
    MODEL_DIR.mkdir(exist_ok=True)
    shutil.copy2(step, MODEL_DIR / step.name)
    model = (f'\t(model "${{MC_LIB}}/3dmodels/{step.name}"\n\t\t(offset\n\t\t\t(xyz 0 0 0)\n\t\t)\n'
             '\t\t(scale\n\t\t\t(xyz 1 1 1)\n\t\t)\n\t\t(rotate\n\t\t\t(xyz 0 0 0)\n\t\t)\n\t)\n')
    f.write_text(t.rstrip()[:-1].rstrip() + "\n" + model + ")\n")


# Altium mechanical layers arrive as User.N. Standard Altium/IPC convention (verified on the
# CVTN and 48V libs: Mech15 encloses the pads, Mech13 is the body, Mech1 the assembly outline).
ALTIUM_LAYERS = {"User.15": "F.CrtYd", "User.13": "F.Fab", "User.1": "F.Fab"}


def remap_altium_layers(t: str) -> str:
    for src, dst in ALTIUM_LAYERS.items():
        t = t.replace(f'(layer "{src}")', f'(layer "{dst}")')

    def origin_cross_to_fab(m):  # Altium libs draw an origin "+" on Mech15; it breaks the courtyard
        blk = m.group(0)
        if '(layer "F.CrtYd")' not in blk:
            return blk
        pts = [tuple(map(float, p)) for p in re.findall(r'\((?:start|end) ([-\d.]+) ([-\d.]+)\)', blk)]
        (x0, y0), (x1, y1) = pts
        axis = abs(x1 - x0) < 0.01 or abs(y1 - y0) < 0.01
        length = abs(x1 - x0) + abs(y1 - y0)
        centred = abs((x0 + x1) / 2) < 0.05 and abs((y0 + y1) / 2) < 0.05 and length <= 2.1
        from_origin = min(abs(x0) + abs(y0), abs(x1) + abs(y1)) < 0.05 and length <= 1.1  # "+" drawn as 4 arms
        return blk.replace('"F.CrtYd"', '"F.Fab"') if axis and (centred or from_origin) else blk
    t = re.sub(r'\n\t\(fp_line\n.*?\n\t\)', origin_cross_to_fab, t, flags=re.S)
    return fix_zero_pads(heal_courtyard(t))


def fix_zero_pads(t: str) -> str:
    """Altium mounting holes arrive as np_thru_hole pads with (size 0 0). pcbnew tolerates that, but
    KiCad's library loader rejects the file and with it the WHOLE library. Size them to the drill."""
    def fix(m):
        blk = m.group(0)
        drill = re.search(r'\(drill ([\d.]+)', blk)
        return blk.replace("(size 0 0)", f"(size {drill.group(1)} {drill.group(1)})") if drill else blk
    return re.sub(r'\n\t\(pad "[^"]*" np_thru_hole[^\n]*\n.*?\n\t\)', fix, t, flags=re.S)


def strip_ul_artifacts(t: str) -> str:
    """Ultra Librarian exports leave a literal "Designator123" text on F.SilkS - it would be printed on the PCB."""
    return re.sub(r'\n\t\(fp_text user "Designator\d*".*?\n\t\)', "", t, flags=re.S)


def heal_courtyard(t: str, tol: float = 0.1) -> str:
    """Snap courtyard line endpoints that miss each other by < tol mm (Altium rounding gaps)."""
    pat = re.compile(r'(\n\t\(fp_line\n\t\t\(start )([-\d.]+) ([-\d.]+)(\)\n\t\t\(end )([-\d.]+) ([-\d.]+)(\).*?\n\t\))', re.S)
    lines = [m for m in pat.finditer(t) if '(layer "F.CrtYd")' in m.group(7)]
    pts = [[float(m.group(2)), float(m.group(3)), float(m.group(5)), float(m.group(6))] for m in lines]
    ends = [(i, j) for i in range(len(pts)) for j in (0, 2)]

    def key(i, j):
        return round(pts[i][j], 4), round(pts[i][j + 1], 4)
    for _ in range(2):
        count = {}
        for i, j in ends:
            count[key(i, j)] = count.get(key(i, j), 0) + 1
        dangling = [(i, j) for i, j in ends if count[key(i, j)] % 2]
        for a, (i, j) in enumerate(dangling):
            best = None
            for k, l in dangling[a + 1:]:
                d = abs(pts[i][j] - pts[k][l]) + abs(pts[i][j + 1] - pts[k][l + 1])
                if 0 < d < tol and (best is None or d < best[0]):
                    best = (d, k, l)
            if best:
                _, k, l = best
                pts[k][l], pts[k][l + 1] = pts[i][j], pts[i][j + 1]
    out, last = [], 0
    for m, p in zip(lines, pts):
        out.append(t[last:m.start()])
        out.append(f"{m.group(1)}{p[0]:g} {p[1]:g}{m.group(4)}{p[2]:g} {p[3]:g}{m.group(7)}")
        last = m.end()
    return "".join(out) + t[last:]


def add_courtyard(t: str, margin: float = 0.25) -> str:
    """No courtyard? Add an IPC-7351 style rectangle: pads + body extents + margin (mm)."""
    if re.search(r'\(layer "[FB]\.CrtYd"\)', t):
        return t
    xs, ys = [], []
    for m in re.finditer(r'\(pad "[^"]*" \w+ \w+\s*\(at ([-\d.]+) ([-\d.]+)(?: ([-\d.]+))?\)\s*'
                         r'\(size ([\d.]+) ([\d.]+)\)', t):
        x, y, rot, w, h = m.groups()
        x, y, w, h = float(x), float(y), float(w), float(h)
        if rot and round(float(rot)) % 180 == 90:
            w, h = h, w
        xs += [x - w / 2, x + w / 2]
        ys += [y - h / 2, y + h / 2]
    for m in re.finditer(r'\n\t\(fp_(?:line|rect|poly|circle|arc)(.*?)\n\t\)', t, re.S):
        if '(layer "F.Fab")' in m.group(1):
            for x, y in re.findall(r'\((?:start|end|xy|mid) ([-\d.]+) ([-\d.]+)\)', m.group(1)):
                xs.append(float(x)); ys.append(float(y))
    if not xs:
        return t
    x0, y0 = min(xs) - margin, min(ys) - margin
    x1, y1 = max(xs) + margin, max(ys) + margin
    rect = (f'\t(fp_rect\n\t\t(start {x0:.3f} {y0:.3f})\n\t\t(end {x1:.3f} {y1:.3f})\n'
            '\t\t(stroke\n\t\t\t(width 0.05)\n\t\t\t(type solid)\n\t\t)\n\t\t(fill no)\n'
            '\t\t(layer "F.CrtYd")\n\t)\n')
    i = t.find("\n\t(pad ")
    return t[:i + 1] + rect + t[i + 1:] if i >= 0 else t


SYM_EXT = (".kicad_sym", ".schlib", ".intlib", ".lib")
FP_EXT = (".pcblib", ".kicad_mod", ".mod", ".intlib")


def import_files(syms, fps, steps, lib, table=None, fp_lib=None, default_fp=None, name=None,
                 value=None, fields=None, src_label="", overwrite=False, validate=True, quiet=False):
    """Convert symbol/footprint files into MC libs and (optionally) add DB rows. Returns row IDs."""
    fp_lib = fp_lib or lib
    fp_names = []
    for f in fps:
        try:
            fp_names += convert_footprints(Path(f), fp_lib)
        except RuntimeError as e:
            print(f"warning: {e.args[0].splitlines()[0]} ({Path(f).name})")
    for fp in fp_names:
        if steps:
            attach_model(fp_lib, fp, Path(steps[0]))
    symbols = {}
    for s in syms:
        symbols.update(convert_symbols(Path(s)))
    symbols = {k: v for k, v in symbols.items() if "(Altium Display 2)" not in k}

    con, ids = db(), []
    for old, block in symbols.items():
        props = sym_props(block)
        row = params_to_row(props)
        sname = safe_name(name or row.get("MPN") or props.get("Value") or old)
        block = rename_symbol(block, old, sname)
        dfp = safe_name(default_fp or props.get("Footprint", "").split(":")[-1])
        ordered = ([dfp] if dfp in fp_names else []) + [f for f in fp_names if f != dfp]
        block = set_prop(block, "Footprint", f"{fp_lib}:{ordered[0]}" if ordered else "")
        block = strip_props(block)
        add_symbols(lib, {sname: block}, overwrite=overwrite)
        row.update({
            "MPN": row.get("MPN") or props.get("Value", sname),
            "Symbols": f"{lib}:{sname}",
            "Footprints": ";".join(f"{fp_lib}:{f}" for f in ordered),
            "Source": src_label,
            "Status": "Seed",  # until a human checks the footprint vs datasheet
        })
        row["Value"] = value or row["MPN"]
        row.update(fields or {})
        if table:
            pid = insert(con, table, row, validate=validate)
            ids.append(pid)
            if not quiet:
                print(f"{table}/{pid}: {row['MPN']}  sym={row['Symbols']}  fp={row['Footprints'] or '-'}")
                if not os.environ.get("MCLIB_NO_MOUSER"):
                    try:
                        record_sourcing(con, table, pid)
                    except Exception as e:
                        print(f"  Mouser lookup skipped: {e}")
        elif not quiet:
            print(f"symbol {lib}:{sname}  fp={row['Footprints'] or '-'}  (no --table, DB not touched)")
    con.commit()
    return ids


def cmd_import(a) -> int:
    src = Path(a.source).expanduser()
    tmp = None
    if src.suffix.lower() == ".zip":
        tmp = tempfile.TemporaryDirectory()
        zipfile.ZipFile(src).extractall(tmp.name)
        src = Path(tmp.name)
    files = [src] if src.is_file() else \
        [p for p in src.rglob("*") if p.is_file() and "history" not in str(p).lower()]
    # prefer KiCad-native files if the vendor shipped them, else Altium/legacy
    syms = [p for p in files if p.suffix.lower() == ".kicad_sym"] or \
           [p for p in files if p.suffix.lower() in SYM_EXT]
    fps = [p for p in files if p.suffix.lower() == ".kicad_mod"] or \
          [p for p in files if p.suffix.lower() in (".pcblib", ".intlib", ".mod")]
    steps = [p for p in files if p.suffix.lower() in (".step", ".stp")]
    if not syms:
        sys.exit(f"no symbol library found in {a.source}")
    import_files(syms, fps, steps, a.lib, a.table, a.fp_lib, a.footprint, a.name, a.value,
                 parse_kv(a.field or []), a.src_label or f"import {Path(a.source).name}", a.overwrite)
    if tmp:
        tmp.cleanup()
    return 0


# ---------------------------------------------------------------- simple commands
def parse_kv(items: list[str]) -> dict[str, str]:
    row = {}
    for kv in items:
        if "=" not in kv:
            sys.exit(f"expected Column=Value, got {kv!r}")
        k, v = kv.split("=", 1)
        row[k] = v
    return row


def cmd_add(a) -> int:
    con = db()
    pid = insert(con, a.table, parse_kv(a.fields))
    con.commit()
    print(pid)
    if not a.no_mouser:
        try:
            record_sourcing(con, a.table, pid)
            con.commit()
        except Exception as e:
            print(f"  Mouser lookup skipped: {e}")
    return 0


def cmd_set(a) -> int:
    con = db()
    t, _ = find_part(con, a.id)
    row = parse_kv(a.fields)
    con.execute(f"UPDATE {t} SET {','.join(f'{k}=?' for k in row)} WHERE ID=?", [*row.values(), a.id])
    errs = resolve_errors(con.execute(f"SELECT * FROM {t} WHERE ID=?", (a.id,)).fetchone())
    if errs:
        con.rollback()
        sys.exit("not saved:\n  " + "\n  ".join(errs))
    con.commit()
    return 0


def cmd_show(a) -> int:
    t, r = find_part(db(), a.id)
    print(f"[{t}]")
    for k in r.keys():
        if r[k]:
            print(f"  {k:17} {r[k]}")
    return 0


def cmd_find(a) -> int:
    con, terms = db(), [t.lower() for t in a.terms]
    tables = [a.table] if a.table else TABLES
    hits = []
    for t in tables:
        for r in con.execute(f"SELECT * FROM {t}"):
            hay = " ".join(str(v) for v in r).lower()
            if all(term in hay for term in terms):
                hits.append((t, r))
    if a.json:
        print(json.dumps([dict(r, Table=t) for t, r in hits], indent=1))
    else:
        for t, r in hits[:a.limit]:
            print(f"{r['ID']:9} {r['MPN'][:26]:26} {r['Value'][:10]:10} {r['Package'][:10]:10} {r['Description'][:60]}")
        print(f"-- {len(hits)} match(es)" + (f", showing {a.limit}" if len(hits) > a.limit else ""))
    return 0


def cmd_gen_dbl(a) -> int:
    libs = []
    for t, (_, extra) in TABLES.items():
        fields = [{"column": "Value", "name": "Value", "visible_on_add": True,
                   "visible_in_chooser": True, "show_name": False, "inherit_properties": False},
                  {"column": "Datasheet", "name": "Datasheet", "visible_on_add": False,
                   "visible_in_chooser": False, "show_name": False, "inherit_properties": False},
                  {"column": "ID", "name": "MC_ID", "visible_on_add": False,
                   "visible_in_chooser": False, "show_name": False, "inherit_properties": False}]
        chooser = {"MPN", "Manufacturer", "Package", *extra, *SOURCING_FIELDS}
        for c in BOM_FIELDS + SOURCING_FIELDS:
            fields.append({"column": c, "name": c, "visible_on_add": False,
                           "visible_in_chooser": c in chooser, "show_name": False,
                           "inherit_properties": False})
        libs.append({"name": t, "table": t, "key": "ID", "symbols": "Symbols",
                     "footprints": "Footprints", "fields": fields,
                     "properties": {"description": "Description", "keywords": "Keywords",
                                    "exclude_from_bom": "Exclude_BOM",
                                    "exclude_from_board": "Exclude_Board"}})
    dbl = {"meta": {"version": 0}, "name": "MC Parts",
           "description": "MC_Lib parts database (SQLite via ODBC). Edit with tools/mclib.py.",
           "source": {"type": "odbc", "dsn": "", "username": "", "password": "", "timeout_seconds": 2,
                      "connection_string": f"Driver={ODBC_DRIVER};Database=${{CWD}}/database/mc_parts.sqlite"},
           "cache": {"max_size": 1024, "max_age": 10},
           "libraries": libs}
    DBL_PATH.write_text(json.dumps(dbl, indent=4) + "\n")
    print(f"wrote {DBL_PATH.name} ({len(libs)} tables)")
    return 0


def cmd_stats(a) -> int:
    con = db()
    for t in TABLES:
        n = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        by = con.execute(f"SELECT Status, COUNT(*) FROM {t} GROUP BY Status").fetchall()
        print(f"{t:16} {n:5}   " + ", ".join(f"{s}:{c}" for s, c in by))
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = p.add_subparsers(dest="cmd", required=True)
    s = sp.add_parser("find", help="search parts (all terms must match)")
    s.add_argument("terms", nargs="+"); s.add_argument("--table"); s.add_argument("--limit", type=int, default=40)
    s.add_argument("--json", action="store_true"); s.set_defaults(fn=cmd_find)
    s = sp.add_parser("show", help="show one part"); s.add_argument("id"); s.set_defaults(fn=cmd_show)
    s = sp.add_parser("add", help="add a part: add <Table> Col=Val ...")
    s.add_argument("table", choices=TABLES); s.add_argument("fields", nargs="+")
    s.add_argument("--no-mouser", action="store_true"); s.set_defaults(fn=cmd_add)
    s = sp.add_parser("set", help="update fields: set <ID> Col=Val ...")
    s.add_argument("id"); s.add_argument("fields", nargs="+"); s.set_defaults(fn=cmd_set)
    s = sp.add_parser("import", help="import a vendor download (folder/zip/SchLib/IntLib/kicad_sym)")
    s.add_argument("source"); s.add_argument("--lib", required=True, help="symbol lib, e.g. MC_IC")
    s.add_argument("--fp-lib", help="footprint lib (default: same as --lib)")
    s.add_argument("--table", choices=TABLES, help="also add a DB row to this table")
    s.add_argument("--footprint", help="default footprint name if several were imported")
    s.add_argument("--name", help="symbol name (default: MPN)"); s.add_argument("--value")
    s.add_argument("--field", action="append", help="extra Column=Value for the DB row")
    s.add_argument("--src-label"); s.add_argument("--overwrite", action="store_true")
    s.set_defaults(fn=cmd_import)
    sp.add_parser("check", help="validate DB rows").set_defaults(fn=cmd_check)
    s = sp.add_parser("sourcing", help="refresh Mouser stock/price/lifecycle on library rows")
    s.add_argument("--all", action="store_true", help="every row, not just stale/missing ones")
    s.add_argument("--older-than", type=int, default=7, help="refresh rows older than N days (default 7)")
    s.add_argument("--table", choices=TABLES); s.add_argument("--limit", type=int, default=900, help="max API lookups")
    s.add_argument("-v", "--verbose", action="store_true"); s.set_defaults(fn=cmd_sourcing)
    sp.add_parser("lint-fp", help="footprint hygiene report").set_defaults(fn=cmd_lint_fp)
    s = sp.add_parser("fix-fp", help="clean courtyards + KiCad re-save (all libs or given ones)")
    s.add_argument("libs", nargs="*"); s.set_defaults(fn=cmd_fix_fp)
    sp.add_parser("gen-dbl", help="regenerate MC_Parts.kicad_dbl").set_defaults(fn=cmd_gen_dbl)
    sp.add_parser("init", help="create/upgrade database tables").set_defaults(fn=lambda a: init_db() or 0)
    sp.add_parser("stats", help="part counts per table").set_defaults(fn=cmd_stats)
    a = p.parse_args()
    if a.cmd not in ("init", "gen-dbl"):
        init_db()
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
