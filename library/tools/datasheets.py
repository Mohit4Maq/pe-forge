#!/usr/bin/env python3
"""Datasheet evidence cache: every part's symbol and footprint check is tied to the exact datasheet file it used.

  datasheets.py add <file.pdf> --mpn MPN [--manufacturer M] [--url URL] [--title T]
        copy into $MC_LIB/datasheets/<sha256[:16]>.pdf and add a row to index.csv
        (sha256, mpn, manufacturer, url, retrieved, pages, title). Offline: download the PDF first.
  datasheets.py verify <MC_ID> <sha16> --page N [--section TEXT] [--pads N --pitch MM]
        record the evidence on the part: Verified_Against = <sha16>#p<N>/<section>, Verified_By (git user.name),
        Verified_On (today), Verified_Pads = <pads>@<pitch mm> (checked against the footprint by forge check)
  datasheets.py list [MPN]
  datasheets.py check            every Verified_Against points at a cached file whose hash matches

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


def cmd_add(a):
    src = Path(a.file)
    if not src.exists() or open(src, "rb").read(5) != b"%PDF-":
        sys.exit(f"{src}: not a PDF file")
    h = sha256(src)
    CACHE.mkdir(parents=True, exist_ok=True)
    dst = CACHE / f"{h[:16]}.pdf"
    if not dst.exists():
        shutil.copy(src, dst)
    have = rows()
    if not any(r["sha256"] == h for r in have):
        have.append({"sha256": h, "mpn": a.mpn, "manufacturer": a.manufacturer or "", "url": a.url or "",
                     "retrieved": dt.date.today().isoformat(), "pages": pages(dst), "title": a.title or src.stem})
        with open(INDEX, "w", newline="") as fh:
            w = csv.DictWriter(fh, COLS)
            w.writeheader()
            w.writerows(have)
    print(f"{h[:16]}  {a.mpn}  {dst}")
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
    con.execute(f"UPDATE {t} SET {','.join(f'{k}=?' for k in fields)} WHERE ID=?", [*fields.values(), a.id])
    con.commit()
    print(f"{a.id}: verified against {ref} by {who}")
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
    s.add_argument("--manufacturer"); s.add_argument("--url"); s.add_argument("--title"); s.set_defaults(fn=cmd_add)
    s = sp.add_parser("verify"); s.add_argument("id"); s.add_argument("sha"); s.add_argument("--page", required=True)
    s.add_argument("--section"); s.add_argument("--pads", type=int); s.add_argument("--pitch", type=float)
    s.set_defaults(fn=cmd_verify)
    s = sp.add_parser("list"); s.add_argument("mpn", nargs="?"); s.set_defaults(fn=cmd_list)
    sp.add_parser("check").set_defaults(fn=cmd_check)
    a = ap.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
