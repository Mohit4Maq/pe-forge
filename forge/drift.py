"""forge drift: the design documents against the drawn schematic. Each finding is {doc, claim, actual, location}.

  forge drift [--json] [--module M-xx]          (also a `forge check` sub-check)

Compared:
  1. BoM vs schematic: every BoM ref exists; value, MPN, MC_ID, footprint and DNP agree; and every schematic part
     that belongs in a BoM is in one (power symbols and parts excluded from the BoM are skipped).
     BoMs: 04_Hardware/BOM/WIP_BOM.csv and modules/M-xx/BOM.csv.
  2. Sheet brief netlist vs KiCad's own netlist: every net's pins, exactly (nets listed under "Sheet interfaces"
     may carry more pins from other sheets). This gives user-drawn sheets the contract check agent-drawn ones get.
  3. Parts tables in sheet briefs and design booklets (any table with a Ref column and a Value / MPN / Footprint
     column) like (1). Free prose is not checked.
Which schematic: a module's Sheet_Brief.md may name its sheet in its frontmatter (`kicad_sheet: 08_PCB/x/y.kicad_sch`);
otherwise the whole design (every top sheet under 08_PCB/) is the reference.
Bootstrap rule: a sheet with no parts yet is not compared (BoMs lead the schematic in P4 to P6), but a module whose
G6 has passed gets a warning when its sheet is empty while its BoM lists parts.
"""
import glob
import json
import os
import re

from . import kicad, tables

PARTS_COLS = ("value", "mpn", "footprint", "mc_id")


def finding(doc, claim, actual, location="", severity="fail"):
    return {"doc": doc, "claim": claim, "actual": actual, "location": location or doc, "severity": severity}


# ------------------------------------------------------------------ the design
class Design:
    def __init__(self, prj):
        from .check import roots
        self.prj = prj
        self.roots = roots(prj, "kicad_sch")
        self.comps, self.nets, self.ok, self.error = {}, [], True, ""
        if self.roots and not kicad.find_cli():
            self.ok, self.error = False, "kicad-cli not found (forge doctor)"
            return
        for r in self.roots:
            d = kicad.design(r)
            if d is None:
                self.ok, self.error = False, f"KiCad netlist export failed for {prj.rel(r)}"
                continue
            comps, nets = d
            for ref, c in comps.items():
                c["root"] = prj.rel(r)
                self.comps[ref] = c
            self.nets += [(name, pins, prj.rel(r)) for name, pins in nets.items()]

    def scope(self, sheet=None):
        """Parts on one sheet file (by name), or the whole design."""
        if not sheet:
            return {r: c for r, c in self.comps.items() if not r.startswith("#")}
        b = os.path.basename(sheet)
        return {r: c for r, c in self.comps.items() if not r.startswith("#") and os.path.basename(c["sheetfile"]) == b}


def where(c):
    return os.path.basename(c.get("sheetfile") or c.get("root") or "the schematic")


# ------------------------------------------------------------------ comparisons
def compare_parts(doc, rows, scope, label, line_of=None):
    """Rows (canonical keys) against schematic parts. Returns findings."""
    F = []
    for i, r in enumerate(rows):
        loc = f"{doc}:{line_of(i, r)}" if line_of else doc
        for ref in tables.refs(r.get("ref", "")):
            c = scope.get(ref)
            if c is None:
                F.append(finding(doc, f"{ref} exists", f"{ref} not in {label}", loc))
                continue
            v = r.get("value", "")
            if v and not tables.placeholder(v) and not tables.same_value(v, c["value"]):
                F.append(finding(doc, f"{ref} = {v}", f"{ref} = {c['value']} in {where(c)}", loc))
            m = r.get("mpn", "")
            sm = c["fields"].get("MPN", "")
            if m and sm and not tables.placeholder(m) and not tables.same_mpn(m, sm):
                F.append(finding(doc, f"{ref} MPN {m}", f"{ref} MPN {sm} in {where(c)}", loc))
            mc = r.get("mc_id", "")
            smc = c["fields"].get("MC_ID", "")
            if mc and smc and not tables.placeholder(mc) and mc.strip() != smc.strip():
                F.append(finding(doc, f"{ref} MC_ID {mc}", f"{ref} MC_ID {smc} in {where(c)}", loc))
            fp = r.get("footprint", "")
            if fp and not tables.placeholder(fp) and not tables.same_footprint(fp, c["footprint"]):
                F.append(finding(doc, f"{ref} footprint {fp}", f"{ref} footprint {c['footprint']} in {where(c)}", loc))
            if "dnp" in r or "fitted" in r:
                dnp = tables.truthy(r.get("dnp")) if "dnp" in r else not tables.truthy(r.get("fitted"))
                if dnp != c["dnp"]:
                    F.append(finding(doc, f"{ref} {'DNP' if dnp else 'fitted'}",
                                     f"{ref} {'DNP' if c['dnp'] else 'fitted'} in {where(c)}", loc))
    return F


def netlist_mismatch(expected, kicad_nets, interface=()):
    """[(net, claim, actual)] where KiCad's nets disagree with the expected {net: [REF.PIN]}.
    kicad_nets: iterable of pin sets. Nets in `interface` may hold extra pins (they leave the sheet)."""
    groups = [set(g) for g in kicad_nets]
    out = []
    for net, pins in expected.items():
        pins = set(pins)
        if not pins:
            continue
        hit = [g for g in groups if g & pins]
        if not hit:
            out.append((net, f"net {net} joins {', '.join(sorted(pins))}", "none of these pins is on the schematic"))
            continue
        if len(hit) > 1:
            out.append((net, f"net {net} joins {', '.join(sorted(pins))}",
                        "split over KiCad nets " + " | ".join(", ".join(sorted(g & pins)) for g in hit)))
            continue
        g = hit[0]
        missing, extra = pins - g, (g - pins if net not in interface else set())
        if missing or extra:
            out.append((net, f"net {net} joins {', '.join(sorted(pins))}",
                        "KiCad has " + ", ".join(sorted(g)) + (f" (missing {', '.join(sorted(missing))})" if missing else "")
                        + (f" (extra {', '.join(sorted(extra))})" if extra else "")))
    return out


def frontmatter(text):
    m = re.match(r"---\n(.*?)\n---", text, re.S)
    out = {}
    for line in (m.group(1).split("\n") if m else []):
        if ":" in line:
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip()
    return out


def brief_netlist(text):
    """({net: [pins]}, interface nets) from a sheet brief's tables."""
    nets, iface = {}, set()
    for _, hdr, rows in tables.md_tables(text):
        keys = {tables.key_of(h) for h in hdr}
        low = [h.lower() for h in hdr]
        if "net" in keys and "pins" in keys:
            for r in rows:
                if r.get("net") and not tables.placeholder(r["net"]):
                    nets[r["net"].strip("`")] = tables.pins(r.get("pins", ""))
        elif "net" in keys and any(h.startswith("direction") or h.startswith("other sheet") for h in low):
            iface |= {r["net"].strip("`") for r in rows if r.get("net") and not tables.placeholder(r["net"])}
    return nets, iface


def parts_tables(text):
    """Rows of every table with a Ref column and at least one of Value / MPN / Footprint / MC_ID."""
    out = []
    for _, hdr, rows in tables.md_tables(text):
        keys = {tables.key_of(h) for h in hdr}
        if "ref" in keys and keys & set(PARTS_COLS):
            out += [r for r in rows if tables.refs(r.get("ref", ""))]
    return out


# ------------------------------------------------------------------ the run
def _modules(prj):
    try:
        from . import state
        return state.load(prj)["modules"]
    except Exception:
        return []


def run(prj, module=None):
    """(findings, notes, design). notes: why parts of the comparison were skipped."""
    D = Design(prj)
    F, notes = [], []
    if not D.ok:
        return None, [D.error], D
    whole = D.scope()
    units = []      # (label, dir, past_g6, sheet)
    for m in _modules(prj):
        if module and m["id"] != module:
            continue
        units.append((m["id"], os.path.join(prj.root, m["dir"]), m["phases"].get("P6", {}).get("status") == "passed"))
    bom_refs, any_bom = set(), False
    docs = []
    if not module:
        docs.append(("bom", prj.p("bom_csv"), None, False))
        for f in glob.glob(prj.p("briefs", "*.md")) + glob.glob(prj.p("booklets", "*.md")):
            docs.append(("md", f, None, False))
    for mid, d, g6 in units:
        brief = os.path.join(d, "Sheet_Brief.md")
        sheet = frontmatter(open(brief).read()).get("kicad_sheet") if os.path.exists(brief) else None
        sheet = sheet if sheet and not tables.placeholder(sheet) and not sheet.startswith("08_PCB/<") else None
        docs.append(("bom", os.path.join(d, "BOM.csv"), sheet, g6))
        for f in ("Sheet_Brief.md", "Design_Booklet.md"):
            docs.append(("md", os.path.join(d, f), sheet, g6))
    for kind, path, sheet, g6 in docs:
        if not os.path.exists(path):
            continue
        rel = prj.rel(path)
        scope = D.scope(sheet) if sheet else whole
        label = os.path.basename(sheet) if sheet else "the schematic"
        if kind == "bom":
            rows = tables.read_csv(path)
            rows = [r for r in rows if tables.refs(r.get("ref", ""))]
            if not rows:
                continue
            any_bom = True
            for r in rows:
                bom_refs |= set(tables.refs(r["ref"]))
            if not scope:
                if g6:
                    F.append(finding(rel, f"{label} holds the {sum(len(tables.refs(r['ref'])) for r in rows)} BoM parts",
                                     f"{label} has no parts, and this module's G6 has passed", rel, "warn"))
                else:
                    notes.append(f"{rel}: {label} has no parts yet (BoM leads the schematic): not compared")
                continue
            F += compare_parts(rel, rows, scope, label, line_of=lambda i, r: i + 2)
            continue
        text = open(path, errors="ignore").read()
        rows = parts_tables(text)
        if rows and scope:
            F += compare_parts(rel, rows, scope, label, line_of=lambda i, r: r.get("_line", "?"))
        if os.path.basename(path).startswith("Sheet_Brief") or "/Schematic_Briefs/" in path:
            nets, iface = brief_netlist(text)
            if nets and scope:
                pool = [p for _, p, _ in D.nets]
                if sheet:      # a module sheet: nets of that sheet's parts
                    refs = set(scope)
                    pool = [p for p in pool if any(x.split(".")[0] in refs for x in p)]
                for net, claim, actual in netlist_mismatch(nets, pool, iface):
                    F.append(finding(rel, claim, actual, rel))
            elif nets and not scope:
                notes.append(f"{rel}: no parts drawn yet: netlist not compared")
    if any_bom and whole and not module:
        for ref, c in sorted(whole.items()):
            if c["in_bom"] and ref not in bom_refs:
                F.append(finding(prj.rel(prj.p("bom_csv")), f"{ref} is in a BoM",
                                 f"{ref} ({c['value']}) is on {where(c)} but in no BoM", prj.rel(prj.p("bom_csv"))))
    return F, notes, D


def as_check(prj, opts):
    from .check import result
    F, notes, D = run(prj, opts.get("module"))
    if F is None:
        return result("skip", reason=notes[0], missing_tool=True)
    if not D.roots:
        return result("skip", reason="no schematic yet")
    if not D.comps and not F:
        return result("skip", reason="no schematic yet: the sheet is empty (documents lead the drawing)")
    out = [{"claim": f["claim"], "actual": f["actual"], "location": f["location"], "doc": f["doc"]} for f in F]
    fails = [f for f in F if f["severity"] == "fail"]
    return result("fail" if fails else "warn" if F else "pass", out, reason="; ".join(notes[:2]))


def main(prj, argv):
    mod = argv[argv.index("--module") + 1] if "--module" in argv else None
    F, notes, D = run(prj, mod)
    if F is None:
        print(f"drift: not checked ({notes[0]})")
        return 1
    fails = [f for f in F if f["severity"] == "fail"]
    if "--json" in argv:
        print(json.dumps({"ok": not fails, "findings": F, "notes": notes}, indent=1, ensure_ascii=False))
    else:
        if not D.comps:
            print("drift: no parts drawn yet; documents are not compared until they are")
        for f in F:
            print(f"{'FAIL' if f['severity'] == 'fail' else 'WARN'} {f['location']}: {f['claim']}, actual {f['actual']}")
        for n in notes:
            print(f"note {n}")
        print(f"{len(fails)} drift finding(s)" + (f", {len(F) - len(fails)} warning(s)" if len(F) > len(fails) else ""))
    return 1 if fails else 0
