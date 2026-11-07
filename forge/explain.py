"""forge explain: deterministic design lookup. No model, no network, no writes.

  forge explain U1 [--json] [--sheet PATH]       part: value, MC_ID, MPN, footprint, sheet, every pin -> net,
                                                 its BoM rows and the brief / booklet lines that mention it
  forge explain VIN                              net: every REF.PIN on it, net class, document mentions
  forge explain U1.7 | U1.VCC                    one pin: its net (a pin name unique in the design needs no ref)
  forge explain --pins IC-0021                   a library part's pin table and unit count (before drawing)
Unknown queries exit 1 with up to 5 near matches. The design is read from KiCad's own netlist export of every top
sheet under 08_PCB/ (or --sheet).
"""
import difflib
import glob
import json
import os
import re

from . import kicad, tables


class Lookup:
    def __init__(self, comps, nets):
        self.comps = {r: c for r, c in comps.items() if not r.startswith("#")}
        self.nets = nets                          # {name: {"REF.PIN"}}

    def pin_index(self):
        idx = {}
        for ref, c in self.comps.items():
            for num, p in c.get("pins", {}).items():
                idx[f"{ref}.{num}"] = (ref, num, p)
                if p["name"]:
                    idx.setdefault(f"{ref}.{p['name']}", (ref, num, p))
        return idx

    def resolve(self, q):
        """(kind, data) or ('unknown', suggestions) or ('ambiguous', matches)."""
        if q in self.comps:
            return "part", self.comps[q]
        net = q if q in self.nets else next((n for n in self.nets if n.lower() == q.lower()), None)
        if net is not None:
            return "net", net
        idx = self.pin_index()
        if "." in q and q in idx:
            return "pin", idx[q]
        if "." not in q:
            hits = [(ref, num, p) for ref, c in self.comps.items() for num, p in c.get("pins", {}).items()
                    if p["name"] and p["name"] == q]
            if len(hits) == 1:
                return "pin", hits[0]
            if len(hits) > 1:
                return "ambiguous", [f"{r}.{n}" for r, n, _ in hits]
        pool = list(self.comps) + list(self.nets) + list(idx)
        return "unknown", difflib.get_close_matches(q, pool, n=5, cutoff=0.4)


def mentions(prj, words, limit=20):
    """file:line of brief / booklet / module doc lines that mention any word (whole word)."""
    if not prj:
        return []
    pats = [re.compile(r"(?<![\w.])" + re.escape(w) + r"(?![\w])") for w in words if w]
    files = glob.glob(prj.p("briefs", "*.md")) + glob.glob(prj.p("booklets", "*.md")) + \
        glob.glob(os.path.join(prj.p("modules"), "*", "*.md"))
    out = []
    for f in sorted(files):
        for i, line in enumerate(open(f, errors="ignore"), 1):
            if any(p.search(line) for p in pats):
                out.append(f"{prj.rel(f)}:{i}: {line.strip()[:110]}")
                if len(out) >= limit:
                    return out
    return out


def bom_rows(prj, ref):
    if not prj:
        return []
    files = [prj.p("bom_csv")] + glob.glob(os.path.join(prj.p("modules"), "*", "BOM.csv"))
    out = []
    for f in files:
        if os.path.exists(f):
            for i, r in enumerate(tables.read_csv(f), 2):
                if ref in tables.refs(r.get("ref", "")):
                    out.append({"file": f"{prj.rel(f)}:{i}", "value": r.get("value", ""), "mpn": r.get("mpn", ""),
                                "alt": r.get("Alt MPN", "")})
    return out


def explain(prj, q, sheet=None):
    if sheet:
        d = kicad.design(sheet)
    else:
        from .drift import Design
        D = Design(prj)
        if not D.ok:
            return {"ok": False, "kind": "error", "error": D.error}
        d = (D.comps, {n: p for n, p, _ in D.nets})
    if d is None:
        return {"ok": False, "kind": "error", "error": "KiCad netlist export failed (kicad-cli)"}
    L = Lookup(*d)
    kind, data = L.resolve(q)
    if kind == "part":
        c = data
        pins = [{"pin": n, "name": p["name"], "type": p["type"], "net": p["net"]}
                for n, p in sorted(c.get("pins", {}).items(), key=lambda kv: (len(kv[0]), kv[0]))]
        return {"ok": True, "kind": "part", "ref": q, "value": c["value"], "mc_id": c["fields"].get("MC_ID", ""),
                "mpn": c["fields"].get("MPN", ""), "manufacturer": c["fields"].get("Manufacturer", ""),
                "footprint": c["footprint"], "sheet": os.path.basename(c.get("sheetfile", "")), "dnp": c["dnp"],
                "pins": pins, "bom": bom_rows(prj, q), "mentions": mentions(prj, [q])}
    if kind == "net":
        nodes = []
        cls = "Default"
        for p in sorted(L.nets[data]):
            ref, num = p.split(".", 1)
            info = L.comps.get(ref, {}).get("pins", {}).get(num, {})
            cls = info.get("class", cls)
            nodes.append({"pin": p, "name": info.get("name", ""), "type": info.get("type", "")})
        return {"ok": True, "kind": "net", "net": data, "class": cls, "pins": nodes, "mentions": mentions(prj, [data])}
    if kind == "pin":
        ref, num, p = data
        return {"ok": True, "kind": "pin", "pin": f"{ref}.{num}", "name": p["name"], "type": p["type"], "net": p["net"],
                "on_net": sorted(L.nets.get(p["net"], []))}
    if kind == "ambiguous":
        return {"ok": False, "kind": "ambiguous", "query": q, "matches": data,
                "error": f"pin name {q} is on {len(data)} pins: use REF.PIN ({', '.join(data[:6])})"}
    return {"ok": False, "kind": "unknown", "query": q, "suggestions": data,
            "error": f"{q}: no part, net or pin of that name" + (f"; did you mean {', '.join(data)}?" if data else "")}


def lib_pins(mc_id):
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "skills", "kicad-place",
                                    "scripts"))
    import sch_place as sp
    blk = sp.lib_symbol(mc_id)
    units = sorted({int(u) for u in re.findall(r'\(symbol "[^"]*_(\d+)_\d+"', blk)} - {0}) or [1]
    out = []
    for u in units:
        for n, nm, x, y in sp.lib_pins(blk, u):
            out.append({"unit": u, "pin": n, "name": nm, "x": x, "y": y})
    t, row = sp.row(mc_id)
    return {"ok": True, "kind": "library", "mc_id": mc_id, "mpn": row.get("MPN", ""), "symbol": row.get("Symbols", ""),
            "footprint": (row.get("Footprints") or "").split(";")[0], "units": len(units), "pins": out,
            "warning": f"{len(units)} units: place every unit you use (unit field in the spec)" if len(units) > 1 else ""}


def render(r):
    if not r["ok"]:
        return r["error"]
    k = r["kind"]
    if k == "part":
        lines = [f"{r['ref']}  {r['value']}  {r['mc_id']}  {r['mpn']} ({r['manufacturer']})  {r['footprint']}  "
                 f"sheet {r['sheet']}" + ("  DNP" if r["dnp"] else "")]
        lines += [f"  pin {p['pin']:>4} {p['name'] or '-':<12} -> {p['net']}" for p in r["pins"]]
        lines += [f"  BoM {b['file']}: {b['value']} {b['mpn']}" + (f" (alt {b['alt']})" if b["alt"] else "") for b in r["bom"]]
        lines += ["  " + m for m in r["mentions"]]
        return "\n".join(lines)
    if k == "net":
        lines = [f"net {r['net']}  class {r['class']}  {len(r['pins'])} pin(s)"]
        lines += [f"  {p['pin']:<10} {p['name']:<12} {p['type']}" for p in r["pins"]]
        lines += ["  " + m for m in r["mentions"]]
        return "\n".join(lines)
    if k == "pin":
        return f"{r['pin']} ({r['name'] or '-'}, {r['type']}) -> net {r['net']}: {', '.join(r['on_net'])}"
    lines = [f"{r['mc_id']}  {r['mpn']}  symbol {r['symbol']}  footprint {r['footprint']}  units {r['units']}"]
    lines += [f"  unit {p['unit']} pin {p['pin']:>4} {p['name']}" for p in r["pins"]]
    if r["warning"]:
        lines.append("WARNING " + r["warning"])
    return "\n".join(lines)


def main(prj, argv):
    js = "--json" in argv
    args = [a for a in argv if a != "--json"]
    sheet = None
    if "--sheet" in args:
        i = args.index("--sheet")
        sheet = os.path.abspath(args[i + 1])
        del args[i:i + 2]
    if "--pins" in args:
        r = lib_pins(args[args.index("--pins") + 1])
    elif not args:
        print(__doc__)
        return 1
    else:
        if prj is None and not sheet:
            raise SystemExit("not inside a pe-forge project: pass --sheet <file.kicad_sch>")
        r = explain(prj, args[0], sheet)
    print(json.dumps(r, indent=1, ensure_ascii=False) if js else render(r))
    return 0 if r["ok"] else 1
