#!/usr/bin/env python3
"""mouser - real-time Mouser price, stock and lifecycle for MC_Lib parts and KiCad projects.

  mouser lookup <MPN>... [--qty N]           price/stock for parts
  mouser bom <project> [--boards N] [--json] [--update-db]
        cost and availability of the whole design at N boards; flags stock shortfalls, NRND/EOL,
        not-found parts. --update-db writes the verified Mouser part number into MC_Lib.

API key: env MOUSER_API_KEY, else macOS Keychain item "mouser-api"
  (security add-generic-password -a "$USER" -s mouser-api -w <key> -U).
Results are cached for 12 h in ~/Library/Caches/mclib/mouser.json (Mouser allows ~30 calls/min, 1000/day).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import urllib.request
from collections import defaultdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

API = "https://api.mouser.com/api/v1/search/partnumber?apiKey={key}"
CACHE = Path.home() / "Library/Caches/mclib/mouser.json"
TTL = 12 * 3600
EOL_WORDS = ("obsolete", "end of life", "eol", "discontinued", "not recommended", "nrnd", "last time buy")
_last_call = 0.0


def api_key() -> str:
    k = os.environ.get("MOUSER_API_KEY")
    if not k:
        r = subprocess.run(["security", "find-generic-password", "-s", "mouser-api", "-w"], capture_output=True, text=True)
        k = r.stdout.strip()
    if not k:
        sys.exit("No Mouser API key (env MOUSER_API_KEY or Keychain item 'mouser-api')")
    return k


def _cache():
    try:
        return json.loads(CACHE.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _price(s: str) -> float | None:
    m = re.search(r"[\d.,]+", s or "")
    return float(m.group(0).replace(",", "")) if m else None


def _normalize(p: dict) -> dict:
    breaks = [(int(b["Quantity"]), _price(b["Price"])) for b in p.get("PriceBreaks") or [] if _price(b["Price"]) is not None]
    sym = re.sub(r"[\d.,\s]+", "", (p.get("PriceBreaks") or [{}])[0].get("Price", "")) if p.get("PriceBreaks") else ""
    life = (p.get("LifecycleStatus") or "").strip()
    info = " ".join(str(m) for m in p.get("InfoMessages") or [])
    return {
        "mpn": p.get("ManufacturerPartNumber", ""), "mouser_pn": p.get("MouserPartNumber", ""),
        "manufacturer": p.get("Manufacturer", ""), "description": p.get("Description", ""),
        "stock": int(p.get("AvailabilityInStock") or 0), "on_order": [(o["Quantity"], o["Date"][:10]) for o in p.get("AvailabilityOnOrder") or []],
        "lead_time": p.get("LeadTime") or "", "lifecycle": life, "info": info,
        "replacement": p.get("SuggestedReplacement") or "", "min": int(p.get("Min") or 1), "mult": int(p.get("Mult") or 1),
        "currency": (p.get("PriceBreaks") or [{}])[0].get("Currency", "") if p.get("PriceBreaks") else "", "sym": sym,
        "breaks": breaks, "url": p.get("ProductDetailUrl", ""),
    }


def _fold(s: str) -> str:
    """lower-case, accents removed: 'Würth' == 'Wurth'."""
    import unicodedata
    return "".join(ch for ch in unicodedata.normalize("NFKD", s.lower()) if not unicodedata.combining(ch))


def _pick(cands: list, mfr: str | None) -> dict | None:
    if mfr:
        word = re.split(r"[\s,.(]+", _fold(mfr).strip())[0]
        same = [c for c in cands if word and word in _fold(c["manufacturer"])]
        cands = same or ([] if len(cands) > 1 else cands)   # several makers share this MPN: don't guess
    return max(cands, key=lambda c: c["stock"]) if cands else None


def lookup(mpn: str, fresh: bool = False, mfr: str | None = None) -> dict | None:
    """Best Mouser match: exact MPN, the given manufacturer if several makers use the MPN, most stock."""
    global _last_call
    cache = _cache()
    hit = cache.get(mpn.upper())
    if hit and not fresh and time.time() - hit["t"] < TTL:
        cands = hit.get("parts") if "parts" in hit else ([hit["part"]] if hit.get("part") else [])
        return _pick(cands, mfr)
    def search(term, option):
        global _last_call
        wait = 2.1 - (time.time() - _last_call)
        if wait > 0:
            time.sleep(wait)
        body = json.dumps({"SearchByPartRequest": {"mouserPartNumber": term, "partSearchOptions": option}}).encode()
        req = urllib.request.Request(API.format(key=api_key()), data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.load(r)
        _last_call = time.time()
        if d.get("Errors"):
            raise RuntimeError(f"Mouser API: {d['Errors']}")
        return (d.get("SearchResults") or {}).get("Parts") or []

    parts = [p for p in search(mpn, "Exact") if p.get("ManufacturerPartNumber", "").upper() == mpn.upper()]
    if not parts:  # Molex-style dashes / spacing: "559350410" is sold as "55935-0410"
        norm = lambda x: re.sub(r"[\s\-_/.]", "", x).upper()
        parts = [p for p in search(mpn, "None") if norm(p.get("ManufacturerPartNumber", "")) == norm(mpn)]
    cands = [_normalize(p) for p in parts]
    cache[mpn.upper()] = {"t": time.time(), "parts": cands}
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(cache))
    return _pick(cands, mfr)


def unit_price(part: dict, qty: int) -> float | None:
    q = max(qty, part["min"])
    ok = [p for b, p in part["breaks"] if b <= q]
    return ok[-1] if ok else (part["breaks"][0][1] if part["breaks"] else None)


def design_parts(target: str):
    """{MPN: {'refs': [...], 'mc_id': str}} for fitted, in-BOM parts of a KiCad project."""
    import kinet
    from sch_check import is_rail
    comps, _ = kinet.load(kinet.find_sch(target))
    out = defaultdict(lambda: {"refs": [], "mc_id": "", "mfr": ""})
    skipped = []
    for ref, c in comps.items():
        if is_rail(c) or c.dnp or not c.in_bom:
            continue
        mpn = c.fields.get("MPN", "").strip()
        if not mpn:
            skipped.append(ref)
            continue
        out[mpn]["refs"].append(ref)
        out[mpn]["mc_id"] = c.fields.get("MC_ID", "")
        out[mpn]["mfr"] = c.fields.get("Manufacturer", "")
    return dict(out), skipped


def analyse(target: str, boards: int):
    parts, skipped = design_parts(target)
    rows, items, total, sym = [], [], 0.0, ""
    for mpn, d in sorted(parts.items(), key=lambda kv: kv[1]["refs"][0]):
        refs = ",".join(sorted(d["refs"]))
        need = len(d["refs"]) * boards
        try:
            p = lookup(mpn, mfr=d["mfr"])
        except Exception as e:  # network/API trouble must not break the co-pilot
            items.append({"severity": "INFO", "code": "SRC-API", "message": f"Mouser lookup failed for {mpn}: {e}", "fix": ""})
            continue
        if not p:
            items.append({"severity": "WARN", "code": "SRC-NOTFOUND", "message": f"{refs} {mpn}: not listed at Mouser",
                          "fix": "Check the MPN spelling or source it elsewhere (DigiKey/manufacturer)"})
            rows.append({"mpn": mpn, "refs": refs, "need": need, "found": False, "mc_id": d["mc_id"]})
            continue
        up = unit_price(p, need)
        ext = up * max(need, p["min"]) if up is not None else None
        total += ext or 0
        sym = sym or p["sym"]
        life = (p["lifecycle"] + " " + p["info"]).lower()
        if any(w in life for w in EOL_WORDS):
            sev = "ERROR" if any(w in life for w in ("obsolete", "discontinued", "end of life", "eol")) else "WARN"
            items.append({"severity": sev, "code": "SRC-LIFECYCLE", "message": f"{refs} {mpn}: {p['lifecycle'] or p['info']}",
                          "fix": f"Replacement: {p['replacement']}" if p["replacement"] else "Pick an active alternative (part-librarian agent)"})
        if p["stock"] < need:
            eta = f", {p['on_order'][0][0]} due {p['on_order'][0][1]}" if p["on_order"] else ""
            items.append({"severity": "WARN", "code": "SRC-STOCK",
                          "message": f"{refs} {mpn}: Mouser stock {p['stock']} < {need} needed{eta}, lead time {p['lead_time'] or '?'}",
                          "fix": "Second source, alternative part, or order early"})
        rows.append({"mpn": mpn, "refs": refs, "need": need, "found": True, "mc_id": d["mc_id"], "mouser_pn": p["mouser_pn"],
                     "stock": p["stock"], "unit": up, "ext": ext, "sym": p["sym"], "currency": p["currency"],
                     "lifecycle": p["lifecycle"], "lead_time": p["lead_time"], "url": p["url"]})
    summary = {"boards": boards, "lines": len(parts), "priced": sum(1 for r in rows if r.get("unit") is not None),
               "no_mpn": skipped, "total": round(total, 2), "per_board": round(total / boards, 2) if boards else None,
               "sym": sym, "date": date.today().isoformat()}
    return rows, items, summary


def update_db(rows):
    from mclib import DB_PATH, TABLES
    con = sqlite3.connect(DB_PATH)
    n = 0
    for r in rows:
        if not r.get("found") or not r["mc_id"]:
            continue
        for t in TABLES:
            cur = con.execute(f"SELECT Mouser_PN FROM {t} WHERE ID=?", (r["mc_id"],)).fetchone()
            if cur is not None:
                if cur[0] != r["mouser_pn"]:
                    con.execute(f"UPDATE {t} SET Mouser_PN=?, Notes=trim(Notes || ' Mouser_PN verified via API ' || ?) WHERE ID=?",
                                (r["mouser_pn"], date.today().isoformat(), r["mc_id"]))
                    print(f"  {r['mc_id']}: Mouser_PN {cur[0] or '(empty)'} → {r['mouser_pn']}")
                    n += 1
                break
    con.commit()
    print(f"{n} Mouser part numbers updated in MC_Lib (commit with klibsave)")


def fmt(v, sym):
    return "-" if v is None else f"{sym}{v:,.2f}"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    s = sp.add_parser("lookup"); s.add_argument("mpn", nargs="+"); s.add_argument("--qty", type=int, default=1)
    s.add_argument("--fresh", action="store_true")
    s = sp.add_parser("bom"); s.add_argument("target", nargs="?", default=".")
    s.add_argument("--boards", type=int, default=1); s.add_argument("--json", action="store_true")
    s.add_argument("--update-db", action="store_true")
    a = ap.parse_args()
    if a.cmd == "lookup":
        for m in a.mpn:
            p = lookup(m, fresh=a.fresh)
            if not p:
                print(f"{m}: not found at Mouser")
                continue
            print(f"{p['mpn']} ({p['manufacturer']}) — Mouser {p['mouser_pn']}")
            print(f"  stock {p['stock']:,}" + (f", on order {p['on_order'][0][0]:,} due {p['on_order'][0][1]}" if p["on_order"] else "")
                  + f", lead time {p['lead_time'] or '?'}, lifecycle {p['lifecycle'] or 'Active/unspecified'}")
            print("  price: " + ", ".join(f"{q}+ {fmt(v, p['sym'])}" for q, v in p["breaks"][:6])
                  + f"  → {a.qty} pcs @ {fmt(unit_price(p, a.qty), p['sym'])}")
        return 0
    rows, items, summary = analyse(a.target, a.boards)
    if a.json:
        print(json.dumps({"summary": summary, "rows": rows, "items": items}, indent=1))
    else:
        sym = summary["sym"]
        print(f"Mouser @ {a.boards} board(s), {summary['date']}:")
        for r in rows:
            if r["found"]:
                print(f"  {r['refs'][:18]:18} {r['mpn'][:24]:24} need {r['need']:>5}  stock {r['stock']:>8,}  "
                      f"{fmt(r['unit'], sym):>10}/pc  {fmt(r['ext'], sym):>11}  {r['lifecycle'] or ''}")
            else:
                print(f"  {r['refs'][:18]:18} {r['mpn'][:24]:24} need {r['need']:>5}  NOT AT MOUSER")
        print(f"  total {fmt(summary['total'], sym)} = {fmt(summary['per_board'], sym)}/board "
              f"({summary['priced']}/{summary['lines']} lines priced" + (f", no MPN: {', '.join(summary['no_mpn'])}" if summary["no_mpn"] else "") + ")")
        for i in items:
            print(f"  {i['severity']:5} {i['code']:14} {i['message']}" + (f"  → {i['fix']}" if i["fix"] else ""))
    if a.update_db:
        update_db(rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
