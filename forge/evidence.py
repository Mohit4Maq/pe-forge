"""parts_evidence: every placed part's datasheet evidence is real and its sourcing snapshot is recent.

For each part on the drawn schematic, from its MC_Parts row (by MC_ID):
  - Verified_Against (<sha16>#p<page>/<section>) must point at a cached datasheet that exists and whose SHA-256
    starts with that prefix: missing or changed is a FAIL. No evidence at all is a WARN (FAIL with --strict).
  - Verified_Pads (<count>@<pitch>) must match the footprint file: pad count and smallest pad pitch.
  - Sourcing_Date older than project.toml [parts] sourcing_max_age_days (default 90) is a WARN.
Cache: $MC_DATASHEETS or $MC_LIB/datasheets/ (library/tools/datasheets.py fills it). Standard library only.
"""
import datetime as dt
import hashlib
import math
import os
import re
import sqlite3

from . import config, kicad


def db_path():
    return os.environ.get("MC_DB") or os.path.join(config.LIBRARY, "database", "mc_parts.sqlite")


def cache_dir():
    return os.environ.get("MC_DATASHEETS") or os.path.join(config.LIBRARY, "datasheets")


def rows_by_id():
    p = db_path()
    if not os.path.exists(p):
        return None
    con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    out = {}
    for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE '\\_%' ESCAPE '\\'"):
        cols = {r[1] for r in con.execute(f'PRAGMA table_info("{t}")')}
        if "ID" in cols:
            for r in con.execute(f'SELECT * FROM "{t}"'):
                out[r["ID"]] = dict(r)
    con.close()
    return out


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 16), b""):
            h.update(b)
    return h.hexdigest()


def footprint_file(fp):
    if ":" not in fp:
        return None
    lib, name = fp.split(":", 1)
    stock = kicad.stock_symbols()
    for base in (os.path.join(config.LIBRARY, "footprints"),
                 os.path.join(os.path.dirname(stock), "footprints") if stock else None):
        if base:
            f = os.path.join(base, f"{lib}.pretty", f"{name}.kicad_mod")
            if os.path.exists(f):
                return f
    return None


def pads(fp_text):
    """(numbered pad count, smallest centre-to-centre pitch along x or y in mm or None)."""
    pts = {}
    for m in re.finditer(r'\(pad "([^"]*)"[^\n]*?\n?\s*\(at ([-\d.]+) ([-\d.]+)', fp_text):
        if m.group(1):
            pts.setdefault(m.group(1), (float(m.group(2)), float(m.group(3))))
    xy = list(pts.values())
    pitch = None
    for i, a in enumerate(xy):
        for b in xy[i + 1:]:
            if abs(a[0] - b[0]) < 1e-6 or abs(a[1] - b[1]) < 1e-6:
                d = math.hypot(a[0] - b[0], a[1] - b[1])
                if d > 1e-6 and (pitch is None or d < pitch):
                    pitch = d
    return len(pts), pitch


def as_check(prj, opts):
    from .check import finding, result
    from .drift import Design
    D = Design(prj)
    if not D.ok:
        return result("skip", reason=D.error, missing_tool=True)
    comps = {r: c for r, c in D.comps.items() if not r.startswith("#") and c["in_bom"]}
    if not comps:
        return result("skip", reason="no parts drawn yet")
    db = rows_by_id()
    if db is None:
        return result("skip", reason=f"parts database not found ({db_path()})", missing_tool=True)
    max_age = int(prj.cfg.get("parts", {}).get("sourcing_max_age_days", 90))
    today = dt.date.today()
    F, W, hashes = [], [], {}
    for ref, c in sorted(comps.items()):
        mid = c["fields"].get("MC_ID", "")
        r = db.get(mid)
        loc = os.path.basename(c.get("sheetfile") or "")
        if not r:
            W.append(finding(f"{ref} is an MC_Parts part", "not from MC_Parts: no evidence record", loc))
            continue
        ev = (r.get("Verified_Against") or "").strip()
        if not ev:
            W.append(finding(f"{ref} ({mid}) checked against a cached datasheet", "no datasheet evidence recorded", loc))
        else:
            sha = ev.split("#")[0]
            f = os.path.join(cache_dir(), f"{sha}.pdf")
            if not os.path.exists(f):
                F.append(finding(f"{ref} ({mid}) evidence {ev}", f"cached datasheet {sha}.pdf is missing", loc))
            else:
                full = hashes.setdefault(f, sha256(f))
                if not full.startswith(sha):
                    F.append(finding(f"{ref} ({mid}) evidence {ev}", f"{sha}.pdf has changed (hash {full[:16]})", loc))
        vp = (r.get("Verified_Pads") or "").strip()
        if vp:
            fpf = footprint_file(c["footprint"])
            if fpf:
                n, pitch = pads(open(fpf, errors="ignore").read())
                want_n, _, want_p = vp.partition("@")
                if want_n and int(want_n) != n:
                    F.append(finding(f"{ref} footprint has {want_n} pads (datasheet {ev})", f"{c['footprint']} has {n}", loc))
                if want_p and pitch is not None and abs(float(want_p) - pitch) > 0.01:
                    F.append(finding(f"{ref} pad pitch {want_p} mm (datasheet)", f"{c['footprint']} pitch {pitch:.3f} mm", loc))
        sd = (r.get("Sourcing_Date") or "").strip()
        try:
            age = (today - dt.date.fromisoformat(sd[:10])).days if sd else None
        except ValueError:
            age = None
        if age is not None and age > max_age:
            W.append(finding(f"{ref} sourcing snapshot under {max_age} days old", f"{sd}: {age} days (mclib.py sourcing)", loc))
    if opts.get("strict"):
        F, W = F + W, []
    return result("fail" if F else "warn" if W else "pass", F + W, reason=f"{len(comps)} part(s)")


