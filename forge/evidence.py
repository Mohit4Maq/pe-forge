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
import json
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


def in_project(prj, sha):
    """Is the datasheet with this content id copied into the project's datasheet folder?"""
    d = prj.p("datasheets")
    return os.path.isdir(d) and any(n.endswith(f"_{sha}.pdf") for n in os.listdir(d))


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
        sha, dsf = cached_file(r.get("Datasheet"))
        if not dsf:
            W.append(finding(f"{ref} ({mid}) has its datasheet in the cache", "no cached datasheet: forge evidence todo", loc))
        elif not in_project(prj, sha):
            W.append(finding(f"{ref} datasheet in {prj.paths['datasheets']}/", "not copied into the project: forge evidence sync", loc))
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




# ------------------------------------------------------------------ project commands
USAGE = """forge evidence todo [--write]   parts on this board still missing a datasheet or its check, grouped by family
forge evidence sync             copy every placed part's cached datasheet into 04_Hardware/Datasheets/ (+ index)"""


def family(r):
    """Group key: manufacturer, MPN series (up to the first '-'), footprint. One datasheet usually covers it."""
    mpn = (r.get("MPN") or "").strip()
    return (r.get("Manufacturer") or "", re.split(r"[-/]", mpn)[0] if mpn else "?", (r.get("Footprints") or "").split(";")[0])


def placed(prj):
    from .drift import Design
    D = Design(prj)
    if not D.ok:
        raise SystemExit(D.error)
    return {r: c for r, c in D.comps.items() if not r.startswith("#") and c["in_bom"] and not c["dnp"]}


def cached_file(ds):
    m = re.search(r"([0-9a-f]{16})\.pdf$", ds or "")
    if not m:
        return None, None
    f = os.path.join(cache_dir(), f"{m.group(1)}.pdf")
    return m.group(1), (f if os.path.exists(f) else None)


def todo(prj, write=False):
    comps, db = placed(prj), rows_by_id() or {}
    groups = {}
    for ref, c in comps.items():
        r = db.get(c["fields"].get("MC_ID", ""))
        if r is None:
            groups.setdefault(("not in MC_Parts", c["fields"].get("MPN") or c["value"], c["footprint"]), []).append((ref, None, c))
            continue
        sha, f = cached_file(r.get("Datasheet"))
        if f and r.get("Verified_Against") and r.get("Status") == "Active":
            continue
        groups.setdefault(family(r), []).append((ref, r, c))
    lines = [f"# Datasheet evidence to-do: {prj.name}", "",
             f"{sum(len(v) for v in groups.values())} placed part(s) in {len(groups)} family / families still need a "
             "cached datasheet, a recorded check, or Status Active. One datasheet usually covers a family.", ""]
    for (mfr, series, fp), items in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        refs = ", ".join(sorted(i[0] for i in items))
        ids = sorted({i[1]["ID"] for i in items if i[1]})
        r0 = next((i[1] for i in items if i[1]), None)
        lines += [f"## {mfr or '?'} {series}  ·  {fp or 'no footprint'}  ·  {len(items)} part(s)", "", f"Refs: {refs}"]
        if not r0:
            lines += ["Not from MC_Parts: add it with `mclib.py add ... --datasheet <file.pdf>` first.", ""]
            continue
        need = []
        sha, f = cached_file(r0.get("Datasheet"))
        url = (r0.get("Datasheet") or "") if (r0.get("Datasheet") or "").startswith("http") else ""
        if not f:
            need.append("datasheet: " + (f"download {url}" if url else "find the latest one on the maker's site")
                        + f", then `datasheets.py add <file.pdf> --mpn {r0['MPN']} --manufacturer \"{mfr}\" --part {','.join(ids)}`")
        fpf = footprint_file(fp)
        n, pitch = pads(open(fpf, errors="ignore").read()) if fpf else (None, None)
        meas = f"footprint has {n} pads" + (f", pitch {pitch:.3g} mm" if pitch else "") if n else "footprint file not found"
        glob_pat = (r0["MPN"].split("-")[0] + "*") if len(ids) > 1 else r0["MPN"]
        need.append(f"check symbol pins and land pattern against the datasheet ({meas}), then "
                    + (f"`datasheets.py verify-family <id> --match \"{glob_pat}\" --footprint {fp} --page N --section \"...\" "
                       f"--pads {n or 'N'}" + (f" --pitch {pitch:.3g}" if pitch else "") + " --activate` (preview, then --apply)"
                       if len(ids) > 1 else
                       f"`datasheets.py verify {ids[0]} <id> --page N --section \"...\" --pads {n or 'N'}"
                       + (f" --pitch {pitch:.3g}" if pitch else "") + " --activate`"))
        lines += [f"Parts: {', '.join(ids)}"] + [f"- {x}" for x in need] + [""]
    text = "\n".join(lines) + "\n"
    if write:
        p = prj.p("datasheets", "Evidence_Todo.md")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "w").write(text)
        print(f"wrote {prj.rel(p)}")
    return text, groups


def sync(prj):
    """Copy the cached datasheet of every placed part into the project, with an index. Idempotent."""
    comps, db = placed(prj), rows_by_id() or {}
    out = prj.p("datasheets")
    os.makedirs(out, exist_ok=True)
    rows, missing, copied = {}, [], 0
    for ref, c in sorted(comps.items()):
        r = db.get(c["fields"].get("MC_ID", ""))
        sha, f = cached_file((r or {}).get("Datasheet"))
        if not f:
            missing.append(f"{ref} ({c['fields'].get('MPN') or c['value']})")
            continue
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", r["MPN"] or r["ID"])
        name = f"{safe}_{sha}.pdf"
        dst = os.path.join(out, name)
        if not os.path.exists(dst) or sha256(dst)[:16] != sha:
            import shutil
            shutil.copy(f, dst)
            copied += 1
        e = rows.setdefault(name, {"refs": [], "mpn": r["MPN"], "id": r["ID"], "mfr": r.get("Manufacturer", ""),
                                   "ev": r.get("Verified_Against") or "", "status": r.get("Status", "")})
        e["refs"].append(ref)
    idx = [f"# Datasheets used on {prj.name}", "",
           "Copied from the library cache by `forge evidence sync` (file name = MPN + content id). These are the exact",
           "files each part was checked against. Manufacturers' copyright: keep this folder out of public repositories.", "",
           "| Refs | MPN | Manufacturer | MC_ID | File | Checked against | Status |", "|---|---|---|---|---|---|---|"]
    for name, e in sorted(rows.items(), key=lambda kv: kv[1]["refs"][0]):
        idx.append(f"| {', '.join(e['refs'])} | {e['mpn']} | {e['mfr']} | {e['id']} | [{name}]({name}) | "
                   f"{e['ev'] or 'NOT CHECKED'} | {e['status']} |")
    if missing:
        idx += ["", "## No cached datasheet yet", "", *[f"- {m}" for m in missing], "",
                "Run `forge evidence todo` for the commands."]
    open(os.path.join(out, "Datasheets_Index.md"), "w").write("\n".join(idx) + "\n")
    return copied, len(rows), missing


def main(prj, argv):
    if not argv or argv[0] not in ("todo", "sync"):
        print(USAGE)
        return 1
    if argv[0] == "todo":
        text, groups = todo(prj, "--write" in argv)
        data = {"at": __import__("time").strftime("%Y-%m-%dT%H:%M:%S"),
                "parts": sum(len(v) for v in groups.values()),
                "groups": [{"family": f"{k[0] or '?'} {k[1]}".strip(), "footprint": k[2], "count": len(v),
                            "refs": sorted(i[0] for i in v),
                            "need": "add to MC_Parts" if not any(i[1] for i in v) else
                            ("datasheet + check" if not any(cached_file((i[1] or {}).get("Datasheet"))[1] for i in v)
                             else "check + Active")} for k, v in sorted(groups.items(), key=lambda kv: -len(kv[1]))]}
        try:
            os.makedirs(prj.p("tools_data", "cache"), exist_ok=True)
            json.dump(data, open(prj.p("tools_data", "cache", "parts.json"), "w"))
        except OSError:
            pass
        if "--json" in argv:
            print(json.dumps(data))
        elif "--write" not in argv:
            print(text)
        return 1 if groups else 0
    copied, n, missing = sync(prj)
    print(f"{n} datasheet(s) in {prj.paths['datasheets']}/ ({copied} copied now)"
          + (f"; {len(missing)} part(s) have none cached: {', '.join(missing[:8])}" if missing else ""))
    return 1 if missing else 0
