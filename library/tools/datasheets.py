#!/usr/bin/env python3
"""Datasheet evidence cache: every part's symbol and footprint check is tied to the exact datasheet file it used.

  datasheets.py add <file.pdf> --mpn MPN [--manufacturer M] [--url URL] [--title T]
        copy into $MC_LIB/datasheets/<sha256[:16]>.pdf and add a row to index.csv
        (sha256, mpn, manufacturer, url, retrieved, pages, title). Offline: download the PDF first.
  datasheets.py verify <MC_ID> <sha16> --page N [--section TEXT] [--pads N --pitch MM]
        record the evidence on the part: Verified_Against = <sha16>#p<N>/<section>, Verified_By (git user.name),
        Verified_On (today), Verified_Pads = <pads>@<pitch mm> (checked against the footprint by forge check)
  datasheets.py verify ... --activate                   also set Status=Active (evidence first, then status)
  datasheets.py verify-family <sha16> --match "RC0603*" [--table T] [--footprint LIB:FP] --page N
        [--section TEXT] --pads N --pitch MM [--activate] [--apply]
        one datasheet covering a whole series: every row whose MPN matches the glob (and footprint, if given) gets
        the evidence, after its own footprint's pad count and pitch are checked against --pads / --pitch.
        Preview by default; --apply writes. Rows whose footprint disagrees are listed and left alone.
  datasheets.py attach <MC_ID[,MC_ID...]> <sha16>       point the part's Datasheet field at the cached file
  datasheets.py list [MPN]
  datasheets.py check            every Verified_Against points at a cached file whose hash matches

Every new part gets its datasheet when it is created (`mclib.py add / import --datasheet file.pdf`): the PDF is
cached and the part's Datasheet field becomes ${MC_LIB}/datasheets/<sha16>.pdf, which KiCad opens from the symbol.

Licensing: datasheets are the manufacturers' copyrighted documents. The cache is gitignored in the public pe-forge
repo; keep it in your private library (Git LFS recommended: `git lfs track "datasheets/*.pdf"`).
"""
import argparse
import csv
import datetime as dt
import hashlib
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mclib  # noqa: E402

CACHE = Path(os.environ["MC_DATASHEETS"]) if os.environ.get("MC_DATASHEETS") else mclib.ROOT / "datasheets"
INDEX = CACHE / "index.csv"
COLS = ["sha256", "mpn", "manufacturer", "url", "retrieved", "pages", "title"]


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 16), b""):
            h.update(b)
    return h.hexdigest()


def rows():
    return list(csv.DictReader(open(INDEX, newline=""))) if INDEX.exists() else []


def pages(p):
    try:
        import fitz
        return str(len(fitz.open(p)))
    except Exception:
        n = len(re.findall(rb"/Type\s*/Page[^s]", open(p, "rb").read()))
        return str(n) if n else ""


def cache(file, mpn, manufacturer="", url="", title=""):
    """Copy a PDF into the cache (by content hash) and index it. Returns the 16-character id."""
    src = Path(file).expanduser()
    if not src.exists() or open(src, "rb").read(5) != b"%PDF-":
        sys.exit(f"{src}: not a PDF file (download the datasheet from the manufacturer first)")
    h = sha256(src)
    CACHE.mkdir(parents=True, exist_ok=True)
    dst = CACHE / f"{h[:16]}.pdf"
    if not dst.exists():
        shutil.copy(src, dst)
    have = rows()
    if not any(r["sha256"] == h for r in have):
        have.append({"sha256": h, "mpn": mpn, "manufacturer": manufacturer or "", "url": url or "",
                     "retrieved": dt.date.today().isoformat(), "pages": pages(dst), "title": title or src.stem})
        with open(INDEX, "w", newline="") as fh:
            w = csv.DictWriter(fh, COLS)
            w.writeheader()
            w.writerows(have)
    return h[:16]


def field_value(sha16):
    """What goes in the part's Datasheet field: the cached file through KiCad's ${MC_LIB} variable."""
    return "${MC_LIB}/datasheets/" + f"{sha16}.pdf"


def cached(sha16):
    return (CACHE / f"{sha16}.pdf").exists()


def cmd_add(a):
    h = cache(a.file, a.mpn, a.manufacturer, a.url, a.title)
    print(f"{h}  {a.mpn}  {CACHE / (h + '.pdf')}")
    if a.part:
        attach(a.part.split(","), h)
    return 0


def attach(ids, sha16):
    if not cached(sha16):
        sys.exit(f"{sha16}.pdf is not in {CACHE}: add the datasheet first")
    con = mclib.db()
    for pid in ids:
        t, _ = mclib.find_part(con, pid)
        con.execute(f"UPDATE {t} SET Datasheet=? WHERE ID=?", (field_value(sha16), pid))
    con.commit()
    print(f"datasheet {sha16} attached to {', '.join(ids)}")


def cmd_attach(a):
    attach(a.ids.split(","), a.sha)
    return 0


def cmd_verify(a):
    if not (CACHE / f"{a.sha}.pdf").exists():
        sys.exit(f"{a.sha}.pdf is not in {CACHE}: add the datasheet first (datasheets.py add)")
    who = subprocess.run(["git", "config", "user.name"], capture_output=True, text=True).stdout.strip() or "unknown"
    ref = f"{a.sha}#p{a.page}" + (f"/{a.section}" if a.section else "")
    con = mclib.db()
    t, _ = mclib.find_part(con, a.id)
    fields = {"Verified_Against": ref, "Verified_By": who, "Verified_On": dt.date.today().isoformat()}
    if a.pads:
        fields["Verified_Pads"] = f"{a.pads}@{a.pitch}" if a.pitch else str(a.pads)
    row = con.execute(f"SELECT * FROM {t} WHERE ID=?", (a.id,)).fetchone()
    if not (row["Datasheet"] or "").strip():
        fields["Datasheet"] = field_value(a.sha)
    if a.activate:
        fields["Status"] = "Active"
    con.execute(f"UPDATE {t} SET {','.join(f'{k}=?' for k in fields)} WHERE ID=?", [*fields.values(), a.id])
    con.commit()
    print(f"{a.id}: verified against {ref} by {who}" + (", now Active" if a.activate else ""))
    return 0


def _forge_evidence():
    for root in (os.environ.get("PE_FORGE"), str(Path(__file__).resolve().parents[2])):
        if root and (Path(root) / "forge" / "evidence.py").exists():
            sys.path.insert(0, root)
            from forge import evidence
            return evidence
    sys.exit("verify-family needs pe-forge (set PE_FORGE) to read footprints")


def cmd_family(a):
    if not cached(a.sha):
        sys.exit(f"{a.sha}.pdf is not in {CACHE}: add the datasheet first")
    ev = _forge_evidence()
    con = mclib.db()
    tables = [a.table] if a.table else list(mclib.TABLES)
    who = subprocess.run(["git", "config", "user.name"], capture_output=True, text=True).stdout.strip() or "unknown"
    ref = f"{a.sha}#p{a.page}" + (f"/{a.section}" if a.section else "")
    ok, bad = [], []
    for t in tables:
        for r in con.execute(f"SELECT * FROM {t} WHERE MPN GLOB ? ORDER BY ID", (a.match,)):
            fp = (r["Footprints"] or "").split(";")[0]
            if a.footprint and fp != a.footprint:
                continue
            f = ev.footprint_file(fp)
            if not f:
                bad.append((r["ID"], r["MPN"], f"footprint {fp or '(none)'} not found"))
                continue
            n, pitch = ev.pads(open(f, errors="ignore").read())
            if n != a.pads or (a.pitch and (pitch is None or abs(pitch - a.pitch) > 0.01)):
                bad.append((r["ID"], r["MPN"], f"{fp}: {n} pads, pitch {pitch if pitch is None else round(pitch, 3)}"))
                continue
            ok.append((t, r["ID"], r["MPN"], r["Datasheet"]))
    print(f"{len(ok)} part(s) match {a.match!r} and their footprints agree with {a.pads} pads"
          + (f" at {a.pitch} mm" if a.pitch else ""))
    for _, pid, mpn, _ in ok[:12]:
        print(f"  {pid}  {mpn}")
    if len(ok) > 12:
        print(f"  ... +{len(ok) - 12} more")
    for pid, mpn, why in bad:
        print(f"  SKIP {pid} {mpn}: {why}")
    if not a.apply:
        print("preview only: add --apply to record the evidence" + (" and set them Active" if a.activate else ""))
        return 0
    today = dt.date.today().isoformat()
    for t, pid, _, ds in ok:
        fields = {"Verified_Against": ref, "Verified_By": who, "Verified_On": today,
                  "Verified_Pads": f"{a.pads}@{a.pitch}" if a.pitch else str(a.pads)}
        if not (ds or "").strip():
            fields["Datasheet"] = field_value(a.sha)
        if a.activate:
            fields["Status"] = "Active"
        con.execute(f"UPDATE {t} SET {','.join(f'{k}=?' for k in fields)} WHERE ID=?", [*fields.values(), pid])
    con.commit()
    print(f"recorded evidence {ref} on {len(ok)} part(s)" + (", now Active" if a.activate else ""))
    return 0


def cmd_list(a):
    for r in rows():
        if not a.mpn or a.mpn.lower() in r["mpn"].lower():
            print(f"{r['sha256'][:16]}  {r['mpn']:<24} {r['manufacturer']:<20} {r['retrieved']}  {r['pages']:>4} p  {r['url']}")
    return 0


def cmd_check(a):
    con, bad = mclib.db(), 0
    for t in mclib.TABLES:
        for r in con.execute(f"SELECT ID, Verified_Against FROM {t} WHERE Verified_Against != ''"):
            sha = r["Verified_Against"].split("#")[0]
            f = CACHE / f"{sha}.pdf"
            if not f.exists() or not sha256(f).startswith(sha):
                print(f"{r['ID']}: {r['Verified_Against']} -> {'missing' if not f.exists() else 'hash mismatch'}")
                bad += 1
    print(f"{bad} broken evidence pointer(s)")
    return 1 if bad else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    s = sp.add_parser("add"); s.add_argument("file"); s.add_argument("--mpn", required=True)
    s.add_argument("--manufacturer"); s.add_argument("--url"); s.add_argument("--title")
    s.add_argument("--part", help="MC_ID[,MC_ID] to attach it to"); s.set_defaults(fn=cmd_add)
    s = sp.add_parser("verify"); s.add_argument("id"); s.add_argument("sha"); s.add_argument("--page", required=True)
    s.add_argument("--section"); s.add_argument("--pads", type=int); s.add_argument("--pitch", type=float)
    s.add_argument("--activate", action="store_true"); s.set_defaults(fn=cmd_verify)
    s = sp.add_parser("verify-family"); s.add_argument("sha"); s.add_argument("--match", required=True)
    s.add_argument("--table", choices=list(mclib.TABLES)); s.add_argument("--footprint")
    s.add_argument("--page", required=True); s.add_argument("--section")
    s.add_argument("--pads", type=int, required=True); s.add_argument("--pitch", type=float)
    s.add_argument("--activate", action="store_true"); s.add_argument("--apply", action="store_true")
    s.set_defaults(fn=cmd_family)
    s = sp.add_parser("attach"); s.add_argument("ids"); s.add_argument("sha"); s.set_defaults(fn=cmd_attach)
    s = sp.add_parser("list"); s.add_argument("mpn", nargs="?"); s.set_defaults(fn=cmd_list)
    sp.add_parser("check").set_defaults(fn=cmd_check)
    a = ap.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
