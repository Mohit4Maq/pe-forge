"""forge bom build / risk: the BoM from the schematic, priced and stock-checked for primary and alternate, with a
supply-chain risk assessment.

  forge bom build [--boards N] [--spares N] [--offline] [--jlcpcb] [--replace]
      1. groups the drawn schematic into BoM lines (MC_Parts is authoritative) and writes the order files
         (DigiKey / Mouser upload, optional JLCPCB BoM + CPL) into 09_Release/<REVISION>/bom/ (REVISION from the
         KiCad project's text variables, default A)
      2. checks every line's primary MPN and its alternate (MC_Parts Alt_MPN / Alt_Manufacturer) at each source:
         Mouser (API key: MOUSER_API_KEY, Keychain "mouser-api" or ~/.mouser_key) and LCSC (jlcsearch, no key);
         DigiKey when DIGIKEY_CLIENT_ID / DIGIKEY_CLIENT_SECRET are set
      3. writes 04_Hardware/BOM/WIP_BOM.csv (the columns forge bom check and gate G4 need), the risk report
         04_Hardware/BOM/Supply_Risk.md and .forge/cache/supply.json
  forge bom risk [--offline] [--json]     the risk assessment again from the last lookups (or fresh)
The BoM files go through the write guard: a BoM you wrote yourself is never overwritten (pe-forge writes
WIP_BOM.forge-proposed.csv beside it); --replace overwrites it after a backup in .forge/backup/.
  forge bom check [file]                  completeness: price, stock and alternate on every line

--offline uses only the parts database fields (Mouser_Stock, prices, lead time, lifecycle, Sourcing_Date) and
the last cached lookups, and says so.

Risk factors per line (each finding names the factor, the evidence and a mitigation):
  SC-1 can't build: stock across all sources (primary + an EXACT alternate) below the build quantity      HIGH
  SC-2 single source: no alternate, or the alternate is from the same manufacturer           HIGH (IC / custom)
                                                                                             MEDIUM (passive)
  SC-3 lifecycle: NRND / not recommended / EOL / obsolete at any source                                   HIGH
  SC-4 lead time: factory lead time over 16 weeks (MEDIUM) or 26 weeks (HIGH) when stock does not cover
  SC-5 thin coverage: in stock at only one source, or stock under 10 x the build quantity               MEDIUM
  SC-6 alternate not usable now: the alternate is FUNCTIONAL only (footprint / rating differs) or has no stock
                                                                                                         MEDIUM
  SC-7 MOQ: minimum order over 5 x the need (cost and waste)                                               LOW
  SC-8 library status: part not released in MC_Parts (Seed / Legacy), so not verified against its datasheet
                                                                                                         MEDIUM
  SC-9 cost concentration: one line over 25 % of the BoM cost                                               LOW
Waive with the reason in project.toml [bom.waive] ("U1" = "on-order stock reserved, PO 1234 (owner OK)").
Gate G4 and G8 fail on an unwaived HIGH.
"""
import csv
import datetime as dt
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request

from . import config

TOOLS = os.path.join(config.FORGE_ROOT, "library", "tools")
LEVEL = {"HIGH": 3, "MEDIUM": 2, "LOW": 1, "OK": 0}
EOL = ("nrnd", "not recommended", "end of life", "eol", "obsolete", "discontinued", "last time buy")
COMMODITY = re.compile(r"^(R|C|FB|RN)\d", re.I)          # many makers, same footprint: single source is LOW
ACTIVE = re.compile(r"^(U|Q|IC|K|Y|X)\d", re.I)           # ICs, transistors, relays, crystals: single source is HIGH


def _num(v):
    """A price as a number: 92.67, "92.67", "₹92.67", "$1,234.5" -> float; None when there is none."""
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r"\d[\d,]*(?:\.\d+)?", str(v))
    return float(m.group(0).replace(",", "")) if m else None


def _cur(v):
    m = re.match(r"\s*([^\d\s.,]+)", str(v or ""))
    return m.group(1) if m else ""


def _weeks(lead):
    m = re.search(r"(\d+)\s*(day|week|wk)", lead or "", re.I)
    if not m:
        return None
    n = int(m.group(1))
    return n / 7 if m.group(2).lower().startswith("day") else n


# ------------------------------------------------------------------ sources
def mouser(mpn, mfr=None, offline=False):
    if offline:
        return None
    sys.path.insert(0, TOOLS)
    try:
        import mouser as m
        p = m.lookup(mpn, mfr=mfr)
    except SystemExit:                       # no API key
        return {"error": "no Mouser API key"}
    except Exception as e:
        return {"error": f"Mouser: {e}"}
    if not p:
        return {"found": False}
    return {"found": True, "stock": p["stock"], "price_1": m.unit_price(p, 1), "price_fn": None, "currency": p.get("sym", ""),
            "lead_time": p.get("lead_time", ""), "lifecycle": f"{p.get('lifecycle', '')} {p.get('info', '')}".strip(),
            "min": p.get("min", 1), "on_order": p.get("on_order", []), "replacement": p.get("replacement", ""),
            "pn": p.get("mouser_pn", ""), "_raw": p}


def lcsc(mpn, offline=False):
    if offline:
        return None
    url = "https://jlcsearch.tscircuit.com/api/search?" + urllib.parse.urlencode({"q": mpn, "limit": 10})
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "pe-forge"})
        d = json.load(urllib.request.urlopen(req, timeout=15))
    except Exception as e:
        return {"error": f"LCSC: {e}"}
    norm = lambda s: re.sub(r"[^A-Z0-9]", "", (s or "").upper())
    hits = [c for c in d.get("components", []) if norm(c.get("mfr")) == norm(mpn)
            or norm(c.get("mfr")).startswith(norm(mpn)) and len(norm(c.get("mfr"))) - len(norm(mpn)) <= 4]
    if not hits:
        return {"found": False}
    c = max(hits, key=lambda c: c.get("stock") or 0)
    return {"found": True, "stock": int(c.get("stock") or 0), "price_1": c.get("price"), "currency": "$",
            "lead_time": "", "lifecycle": "", "min": 1, "on_order": [], "pn": f"C{c.get('lcsc')}",
            "match": c.get("mfr")}


def fixture():
    """Tests (and demos without a network): PE_FORGE_SUPPLY_FIXTURE = json {mpn: {source: {...}}}."""
    p = os.environ.get("PE_FORGE_SUPPLY_FIXTURE")
    return json.load(open(p)) if p else None


def availability(mpn, mfr, row, offline):
    if not mpn:
        return {}
    fx = fixture()
    if fx is not None:
        return fx.get(mpn, {})
    out = {}
    for name, fn in (("Mouser", lambda: mouser(mpn, mfr, offline)), ("LCSC", lambda: lcsc(mpn, offline))):
        r = fn()
        if r is not None:
            r.pop("_raw", None)
            out[name] = r
    if offline and row and row.get("MPN") == mpn and row.get("Mouser_Stock") not in (None, ""):
        out["Mouser (DB)"] = {"found": True, "stock": int(float(row.get("Mouser_Stock") or 0)),
                              "price_1": _num(row.get("Mouser_Price_1")), "currency": _cur(row.get("Mouser_Price_1")),
                              "lead_time": row.get("Mouser_Lead_Time") or "",
                              "lifecycle": row.get("Lifecycle") or "", "min": 1, "on_order": [],
                              "as_of": row.get("Sourcing_Date") or "?"}
    return out


# ------------------------------------------------------------------ BoM lines from the drawn schematic
def bom_lines(prj, boards, spares, jlcpcb=False):
    from .insulation import root_sheet
    sch = root_sheet(prj)
    if not sch:
        raise SystemExit("no schematic yet: the BoM is built from the drawn sheets")
    from .fab import revision                         # order files go with the release: 09_Release/<REVISION>/bom/
    out = os.path.join(prj.p("release"), revision(os.path.dirname(sch)), "bom")
    os.makedirs(out, exist_ok=True)
    cmd = [sys.executable, os.path.join(TOOLS, "bom.py"), sch, "-o", out, "--boards", str(boards), "--spares", str(spares)]
    if jlcpcb:
        cmd += ["--jlcpcb", "--allow-missing"]
    r = subprocess.run(cmd, capture_output=True, text=True,       # MC_DB: the project's parts library, not the sample
                       env=dict(os.environ, MC_LIB=config.LIBRARY,
                                MC_DB=os.path.join(config.LIBRARY, "database", "mc_parts.sqlite")))
    name = os.path.splitext(os.path.basename(sch))[0]
    f = os.path.join(out, f"{name}-BOM.csv")
    if not os.path.exists(f):
        raise SystemExit("bom.py failed: " + (r.stderr or r.stdout)[-600:])
    lines = [l for l in csv.DictReader(open(f, newline="")) if l.get("Item") and l["Item"] != "DNP"]
    return lines, out, (r.stdout + r.stderr).strip()


# ------------------------------------------------------------------ risk
def assess(line, prim, alt, alt_match, row, need, total_cost=None, ext=None):
    """[(level, code, evidence, mitigation)] for one BoM line."""
    F = []
    ref = line["Refs"]
    src_stock = {s: (v.get("stock") or 0) for s, v in prim.items() if v.get("found")}
    alt_stock = {s: (v.get("stock") or 0) for s, v in alt.items() if v.get("found")}
    best = max(src_stock.values() or [0])
    best_alt = max(alt_stock.values() or [0])
    exact_alt = bool(line.get("Alt_MPN")) and alt_match == "EXACT"
    covered = best >= need or (exact_alt and best_alt >= need)
    errors = [v["error"] for v in prim.values() if v.get("error")]
    if not src_stock and errors:
        F.append(("MEDIUM", "SC-0", "availability not checked: " + "; ".join(errors), "set the API key, or run --offline"))
    elif not covered:
        oo = [o for v in prim.values() for o in (v.get("on_order") or [])]
        lead = next((v.get("lead_time") for v in prim.values() if v.get("lead_time")), "")
        F.append(("HIGH", "SC-1", f"stock {best} (best source) < {need} needed" + (f"; alternate {best_alt}" if line.get("Alt_MPN") else "")
                  + (f"; on order {oo[0][0]} due {oo[0][1]}" if oo else "") + (f"; lead time {lead}" if lead else ""),
                  "order the alternate, buy from another distributor or the maker, or reserve on-order stock now"))
    r0 = ref.split(",")[0]
    if not line.get("Alt_MPN"):
        if COMMODITY.match(r0):
            F.append(("LOW", "SC-2", "no alternate recorded (commodity part: many makers, same footprint)",
                      "record a second maker's MPN in MC_Parts Alt_MPN (Rule 4)"))
        else:
            F.append(("HIGH" if ACTIVE.match(r0) else "MEDIUM", "SC-2", "no alternate recorded (single source)",
                      "add an alternate from another manufacturer (exact drop-in preferred) to MC_Parts Alt_MPN"))
    elif row and row.get("Alt_Manufacturer") and row.get("Manufacturer") and \
            row["Alt_Manufacturer"].split()[0].lower() == row["Manufacturer"].split()[0].lower():
        F.append(("MEDIUM", "SC-2", f"alternate {line['Alt_MPN']} is from the same manufacturer", "find a second maker"))
    life = " ".join(v.get("lifecycle", "") for v in prim.values()).lower()
    if any(w in life for w in EOL):
        repl = next((v.get("replacement") for v in prim.values() if v.get("replacement")), "")
        F.append(("HIGH", "SC-3", f"lifecycle: {life.strip()}", f"replace it{(' (maker suggests ' + repl + ')') if repl else ''}"))
    if not covered:
        wk = max([w for w in (_weeks(v.get("lead_time")) for v in prim.values()) if w] or [0])
        if wk > 26:
            F.append(("HIGH", "SC-4", f"factory lead time ~{wk:.0f} weeks", "plan the build around it or use the alternate"))
        elif wk > 16:
            F.append(("MEDIUM", "SC-4", f"factory lead time ~{wk:.0f} weeks", "order early"))
    in_stock = [s for s, n in src_stock.items() if n >= need]
    if covered and len(in_stock) == 1 and best < 100 * need:     # deep stock at one big distributor is not a risk
        F.append(("MEDIUM", "SC-5", f"in stock only at {in_stock[0]}", "check a second distributor before ordering"))
    if covered and best < 10 * need:
        F.append(("MEDIUM", "SC-5", f"thin stock: {best} for {need} needed (< 10x)", "order soon, or qualify the alternate"))
    if line.get("Alt_MPN"):
        if alt_match and alt_match != "EXACT":
            F.append(("MEDIUM", "SC-6", f"alternate {line['Alt_MPN']} is {alt_match or 'not graded'}",
                      "an exact drop-in needs no board change; record what differs"))
        elif alt and best_alt < need:
            F.append(("MEDIUM", "SC-6", f"alternate {line['Alt_MPN']} stock {best_alt} < {need}", "find a stocked alternate"))
    mins = [v.get("min") or 1 for v in prim.values() if v.get("found")]
    if mins and min(mins) > 5 * need:
        F.append(("LOW", "SC-7", f"minimum order {min(mins)} for {need} needed", "accept the spares or use another source"))
    if row and row.get("Status") in ("Seed", "Legacy"):
        F.append(("MEDIUM", "SC-8", f"MC_Parts status {row['Status']}: not verified against its datasheet",
                  "verify symbol, footprint and ratings (part-librarian), then release it"))
    if total_cost and ext and ext / total_cost > 0.25:
        F.append(("LOW", "SC-9", f"{100 * ext / total_cost:.0f} % of the BoM cost", "worth a second quote"))
    return F


def _alt_match(row):
    notes = (row or {}).get("Notes", "") or ""
    m = re.search(r"\b(EXACT|FUNCTIONAL)\b", notes, re.I)
    return m.group(1).upper() if m else ""


def run(prj, boards=None, spares=0, offline=False, jlcpcb=False, replace=False):
    from .evidence import rows_by_id
    cfg = prj.cfg.get("bom", {})
    boards = boards or int(cfg.get("boards", 5))
    lines, outdir, gen_log = bom_lines(prj, boards, spares, jlcpcb)
    db = rows_by_id()
    waive = cfg.get("waive", {})
    res, skipped = [], []
    for l in lines:
        row = db.get(l.get("MC_ID", "")) or {}
        if str(row.get("Exclude_BOM") or "").strip().lower() in ("1", "true", "yes", "y"):
            skipped.append(f"{l['Refs']} {l['MPN']}")          # a PCB feature (probe pads etc.): nothing to buy
            continue
        need = int(l.get(f"Qty x{boards}") or int(l["Qty"]) * boards)
        prim = availability(l["MPN"], l.get("Manufacturer"), row, offline)
        alt = availability(l.get("Alt_MPN", ""), row.get("Alt_Manufacturer"), None, offline) if l.get("Alt_MPN") else {}
        price = next((_num(v["price_1"]) for s, v in prim.items() if v.get("found") and _num(v.get("price_1")) is not None), None)
        res.append({"line": l, "row_status": row.get("Status", ""), "alt_mfr": row.get("Alt_Manufacturer", ""),
                    "alt_match": _alt_match(row), "need": need, "prim": prim, "alt": alt, "price_1": price,
                    "ext": price * need if price is not None else None, "row": row})
    total = sum(r["ext"] or 0 for r in res)                    # the order: build quantity plus spares
    per_board = sum((r["price_1"] or 0) * int(r["line"]["Qty"]) for r in res)
    for r in res:
        F = assess(r["line"], r["prim"], r["alt"], r["alt_match"], r["row"], r["need"], total, r["ext"])
        refs = r["line"]["Refs"]
        w = next((v for k, v in waive.items() if k in refs.split(",") or k == r["line"]["MPN"]), None)
        r["findings"] = F
        r["level"] = max([f[0] for f in F], key=lambda x: LEVEL[x], default="OK")
        r["waived"] = w
        r.pop("row")
    summary = {"date": dt.datetime.now().isoformat(timespec="seconds"), "boards": boards, "spares": spares,
               "offline": offline, "lines": len(res), "cost_total": round(total, 2),
               "cost_per_board": round(per_board, 2),
               "currency": next((v.get("currency") for r in res for v in r["prim"].values() if v.get("currency")), ""),
               "levels": {k: sum(1 for r in res if r["level"] == k and not r["waived"]) for k in LEVEL},
               "waived": sum(1 for r in res if r["waived"]), "order_files": prj.rel(outdir),
               "not_purchased": skipped, "replace": replace}
    write(prj, res, summary)
    return res, summary, gen_log


def _stock_txt(d):
    return "; ".join(f"{s} {v.get('stock')}" + (f" ({v.get('pn')})" if v.get("pn") else "")
                     for s, v in d.items() if v.get("found")) or ("not found" if d else "")


def write(prj, res, s):
    bdir = os.path.dirname(prj.p("bom_csv"))
    os.makedirs(bdir, exist_ok=True)
    cols = ["Ref", "Qty", "MPN", "Manufacturer", "Description", "Price_1", "Stock", "Source", "Alt MPN",
            "Alt manufacturer", "Alt price / stock", "Alt match", "Lead time", "Lifecycle", "Risk", "Risk factors", "MC_ID"]
    import io
    from . import guard
    replace = bool(s.get("replace"))
    say = lambda m: print("  " + m)
    f = io.StringIO(newline="")
    if True:                                        # the CSV is built in memory, then written under the guard
        w = csv.DictWriter(f, cols)
        w.writeheader()
        for r in res:
            l = r["line"]
            best = max(((v.get("stock") or 0, s) for s, v in r["prim"].items() if v.get("found")), default=(None, ""))
            alt_best = max(((v.get("stock") or 0, s, v.get("price_1")) for s, v in r["alt"].items() if v.get("found")),
                           default=None)
            w.writerow({"Ref": l["Refs"], "Qty": l["Qty"], "MPN": l["MPN"], "Manufacturer": l["Manufacturer"],
                        "Description": l["Description"], "Price_1": "" if r["price_1"] is None else f"{r['price_1']:.4g}",
                        "Stock": "" if best[0] is None else best[0], "Source": _stock_txt(r["prim"]) + f" · {s['date'][:10]}",
                        "Alt MPN": l.get("Alt_MPN", ""), "Alt manufacturer": r["alt_mfr"],
                        "Alt price / stock": (f"{alt_best[2]} / {alt_best[0]} ({alt_best[1]})" if alt_best else
                                              ("not found" if l.get("Alt_MPN") else "")),
                        "Alt match": r["alt_match"] or ("" if not l.get("Alt_MPN") else "not graded"),
                        "Lead time": next((v.get("lead_time") for v in r["prim"].values() if v.get("lead_time")), ""),
                        "Lifecycle": next((v.get("lifecycle") for v in r["prim"].values() if v.get("lifecycle")), ""),
                        "Risk": ("WAIVED " if r["waived"] else "") + r["level"],
                        "Risk factors": "; ".join(f"{c}: {e}" for _, c, e, _ in r["findings"]), "MC_ID": l.get("MC_ID", "")})
    s["written"] = {}
    st, wp = guard.write(prj.root, prj.p("bom_csv"), f.getvalue(), replace, say)
    s["written"]["bom_csv"] = [st, prj.rel(wp)]
    cur = s["currency"]
    B = ["---", "type: bom", f"generated: {s['date']}", "source: forge bom build (do not edit by hand: edit MC_Parts or the schematic)",
         "---", f"# BoM: {prj.cfg.get('project', {}).get('name', '')}", "",
         f"Build of {s['boards']} board(s)" + (f" + {s['spares']} spares per line" if s.get("spares") else "")
         + f" · {s['lines']} lines · **{cur}{s['cost_per_board']:,.2f} per board** · order {cur}{s['cost_total']:,.2f}"
         " (Mouser 1-off prices; volume breaks are lower)", "",
         "| Refs | Qty | MPN | Manufacturer | Description | Price (1) | Order qty | Line cost | Stock | Alternate | Alt match | Risk |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in res:
        l = r["line"]
        best = max([v.get("stock") or 0 for v in r["prim"].values() if v.get("found")] or [0])
        price = "" if r["price_1"] is None else f"{cur}{r['price_1']:,.2f}"
        ext = "" if r["ext"] is None else f"{cur}{r['ext']:,.2f}"
        B.append(f"| {l['Refs']} | {l['Qty']} | {l['MPN']} | {l['Manufacturer']} | {l['Description'][:60]} | "
                 f"{price} | {r['need']} | {ext} | {best} | {l.get('Alt_MPN') or '—'} | "
                 f"{r['alt_match'] or ('—' if not l.get('Alt_MPN') else 'not graded')} | {('WAIVED ' if r['waived'] else '') + r['level']} |")
    if s.get("not_purchased"):
        B += ["", "Not purchased (PCB features): " + ", ".join(s["not_purchased"])]
    B += ["", "Supply-chain risk and mitigations: [[Supply_Risk]]. Order files: `" + s["order_files"] + "/`."]
    st, wp = guard.write(prj.root, os.path.splitext(prj.p("bom_csv"))[0] + ".md", "\n".join(B) + "\n", replace, say)
    s["written"]["bom_md"] = [st, prj.rel(wp)]
    L = [f"# Supply-chain risk: {prj.cfg.get('project', {}).get('name', '')}", "",
         f"{s['date']} · build of {s['boards']} board(s)" + (f" + {s['spares']} spares per line" if s.get("spares") else "")
         + f" · {s['lines']} lines · order {cur}{s['cost_total']:,.2f} · {cur}{s['cost_per_board']:,.2f} per board (Mouser 1-off prices)" + (" · OFFLINE (database / cache only)" if s["offline"] else ""),
         "", f"**HIGH {s['levels']['HIGH']} · MEDIUM {s['levels']['MEDIUM']} · LOW {s['levels']['LOW']} · OK {s['levels']['OK']}**"
         + (f" · waived {s['waived']}" if s["waived"] else ""), "",
         "| Risk | Refs | MPN | Stock (by source) | Alternate | Findings | Mitigation |", "|---|---|---|---|---|---|---|"]
    for r in sorted(res, key=lambda r: -LEVEL[r["level"]]):
        l = r["line"]
        L.append(f"| {('WAIVED ' if r['waived'] else '')}{r['level']} | {l['Refs']} | {l['MPN']} | {_stock_txt(r['prim'])} | "
                 f"{l.get('Alt_MPN') or '—'}{(' (' + r['alt_match'] + ')') if r['alt_match'] else ''}"
                 f"{(': ' + _stock_txt(r['alt'])) if r['alt'] else ''} | "
                 + "<br>".join(f"{lv} {c}: {e}" for lv, c, e, _ in r["findings"]) + " | "
                 + "<br>".join(dict.fromkeys(m for _, _, _, m in r["findings"])) + (f"<br>waived: {r['waived']}" if r["waived"] else "") + " |")
    if s.get("not_purchased"):
        L += ["", "Not purchased (MC_Parts Exclude_BOM, PCB features): " + ", ".join(s["not_purchased"])]
    L += ["", "Factors: SC-1 can't build · SC-2 single source · SC-3 lifecycle · SC-4 lead time · SC-5 thin coverage · "
              "SC-6 alternate not usable now · SC-7 MOQ · SC-8 library status · SC-9 cost concentration.",
          f"Order files (DigiKey / Mouser uploads): `{s['order_files']}/`. Regenerate: `forge bom build --boards {s['boards']}`."]
    st, wp = guard.write(prj.root, os.path.join(bdir, "Supply_Risk.md"), "\n".join(L) + "\n", replace, say)
    s["written"]["risk"] = [st, prj.rel(wp)]
    d = prj.p("tools_data", "cache")
    os.makedirs(d, exist_ok=True)
    json.dump({"summary": s, "lines": [{k: v for k, v in r.items()} for r in res]}, open(os.path.join(d, "supply.json"), "w"),
              indent=1, default=str)


def last(prj):
    try:
        return json.load(open(prj.p("tools_data", "cache", "supply.json")))
    except (OSError, ValueError):
        return None


def as_check(prj, opts):
    from .check import finding, result
    d = last(prj)
    if not d:
        return result("skip", reason="no supply assessment yet (forge bom build)")
    s = d["summary"]
    F = [finding(f"{r['line']['Refs']} {r['line']['MPN']}: supply risk {r['level']}",
                 "; ".join(f"{c}: {e}" for lv, c, e, m in r["findings"] if lv == r["level"]),
                 "04_Hardware/BOM/Supply_Risk.md", r["findings"][0][3] if r["findings"] else "")
         for r in d["lines"] if r["level"] in ("HIGH", "MEDIUM") and not r["waived"]]
    high = any(r["level"] == "HIGH" and not r["waived"] for r in d["lines"])
    age = (dt.datetime.now() - dt.datetime.fromisoformat(s["date"])).days
    return result("fail" if high else "warn" if F else "pass", F,
                  reason=f"{s['lines']} lines, HIGH {s['levels']['HIGH']}, MEDIUM {s['levels']['MEDIUM']}, "
                         f"{s['date'][:10]}" + (f" ({age} days old: rerun forge bom build)" if age > 14 else ""))


def gate_items(prj):
    d = last(prj)
    if not d:
        return [(False, "supply-chain risk: not assessed (forge bom build)")]
    hi = [f"{r['line']['Refs']} {r['line']['MPN']}" for r in d["lines"] if r["level"] == "HIGH" and not r["waived"]]
    return [(not hi, f"supply-chain risk ({d['summary']['date'][:10]}): {len(hi)} unwaived HIGH" + (f": {', '.join(hi[:5])}" if hi else ""))]


def main(prj, argv):
    arg = lambda f, d=None: argv[argv.index(f) + 1] if f in argv and argv.index(f) + 1 < len(argv) else d
    if argv[:1] == ["risk"] and "--offline" not in argv and last(prj) and "--fresh" not in argv:
        d = last(prj); res, s = d["lines"], d["summary"]
    else:
        res, s, gen = run(prj, int(arg("--boards", 0)) or None, int(arg("--spares", 0)), "--offline" in argv,
                          "--jlcpcb" in argv, replace="--replace" in argv)
    if "--json" in argv:
        print(json.dumps({"summary": s, "lines": res}, indent=1, default=str))
        return 0
    cur = s["currency"]
    print(f"BoM: {s['lines']} lines · build of {s['boards']}" + (f" + {s['spares']} spares" if s.get("spares") else "")
          + f" · order {cur}{s['cost_total']:,.2f} · {cur}{s['cost_per_board']:,.2f} per board"
          + (" · OFFLINE" if s["offline"] else ""))
    for r in sorted(res, key=lambda r: -LEVEL[r["level"]]):
        l = r["line"]
        print(f"  {('WAIVED ' if r['waived'] else '') + r['level']:7s} {l['Refs'][:18]:18s} {l['MPN'][:22]:22s} "
              f"stock {_stock_txt(r['prim'])[:44]:44s} alt {(l.get('Alt_MPN') or '—')[:20]}")
        for lv, c, e, m in r["findings"]:
            if lv in ("HIGH", "MEDIUM"):
                print(f"           {lv} {c}: {e}  → {m}")
    print(f"  HIGH {s['levels']['HIGH']} · MEDIUM {s['levels']['MEDIUM']} · LOW {s['levels']['LOW']} · OK {s['levels']['OK']}"
          f"   report: 04_Hardware/BOM/Supply_Risk.md · BoM: {prj.rel(prj.p('bom_csv'))} · order files: {s['order_files']}/")
    return 1 if s["levels"]["HIGH"] else 0
