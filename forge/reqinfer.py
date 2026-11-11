"""forge req infer: draft system requirements from a design that already exists (an adopted board).

  forge req infer [--json]    read the schematic, write 02_Requirements/Inferred_Requirements.md (a draft, through
                              the write guard), print the summary
  forge req infer --apply     after the owner has confirmed the draft: put its rows into System_Requirements.md
                              (only into a file pe-forge wrote and nobody edited; otherwise a proposed copy)

What it reads: the netlist (kicad-cli), part ratings from MC_Parts (Voltage_Rating, Current_Rating, Clamp_V,
Description) or, for parts not in the library, the value text ("22uF 25V"). What it infers:
  - the board's interfaces: power nets on connectors; which is the input (VIN / VBAT / VBUS / DC_IN ... or the
    highest-rated external rail) and which are outputs;
  - each rail's nominal voltage from its name (+12V, 3V3, VOUT_5V) and the voltage limit the design can take:
    the lowest-rated part on the net (a requirement may not ask more than the parts can stand);
  - current limits: the output inductor's rating, a fuse;
  - protection and isolation present: a fuse, a TVS / clamp, an isolator (they imply a requirement);
  - what the design cannot tell (efficiency, ambient, standards, EMC, life): rows for the owner, marked as such.

Every row is a DRAFT with its evidence and a confidence. Inference is a starting point for the owner, never a
requirement by itself: the owner confirms, edits or deletes each row, then G2 is passed as usual.
"""
import json
import os
import re

GND = re.compile(r"^(A|P|D|S|C|HV|LV|ISO|SEC|PRI)?_?GND\w*$|^0V$|^VSS\w*$|^EARTH$|^PE$|^CHASSIS$|^GND_?(SEC|PRI|ISO|2|B)$", re.I)
INPUT = re.compile(r"V_?IN|VBAT|VBUS|DC_?IN|VSUP|VSUPPLY|VDC_?IN|HV_?DC|^PV|VMAINS|^IN\b|^AC_?L\b|^LINE\b|^L\b|^L1\b|^L2\b|^L3\b|^N\b|^NEUT|^AC_?N\b|^AC_?IN|^MAINS", re.I)
AC = re.compile(r"^AC_?L\b|^LINE\b|^L\b|^L[123]\b|^N\b|^NEUT|^AC_?N\b|^AC_?IN|^MAINS|^U\b|^V\b|^W\b|^PH_?[ABC]\b|^AC_?OUT", re.I)
OUTPUT = re.compile(r"V_?OUT|OUT\b|^AC_?OUT|^U\b|^V\b|^W\b|^PH_?[ABC]\b", re.I)
VOLT = re.compile(r"(?<![\w.])\+?(\d{1,4})V(\d{1,2})?(?![\w])|(?<![\w.])\+?(\d{1,4}(?:\.\d+)?)_?V(?![\w])", re.I)
NUM_V = re.compile(r"(\d+(?:\.\d+)?)\s*V(?!A)\b", re.I)
NUM_A = re.compile(r"(\d+(?:\.\d+)?)\s*A\b")


def short(n):
    return n.split("/")[-1] or n


def nominal(net):
    """+12V -> 12, 3V3 -> 3.3, VOUT_5V -> 5, VIN -> None."""
    s = short(net)
    for m in VOLT.finditer(s.replace("-", "_")):
        if m.group(1):
            return float(m.group(1) + ("." + m.group(2) if m.group(2) else ""))
        if m.group(3):
            return float(m.group(3))
    return None


def _first(rx, *texts):
    for t in texts:
        m = rx.search(t or "")
        if m:
            return float(m.group(1))
    return None


class Parts:
    """Ratings for a netlist's parts: MC_Parts row (by MC_ID when its MPN agrees, else by MPN), else value text."""

    def __init__(self, parts, db):
        self.parts, self.db = parts, db or {}
        self.by_mpn = {(r.get("MPN") or "").upper(): r for r in self.db.values() if r.get("MPN")}

    def row(self, ref):
        p = self.parts[ref]
        r = self.db.get(p["mc_id"])
        if r and (not p["mpn"] or (r.get("MPN") or "").upper() == p["mpn"].upper()):
            return r
        return self.by_mpn.get((p["mpn"] or "").upper()) or {}

    def text(self, ref):
        r = self.row(ref)
        return " ".join(str(x) for x in (self.parts[ref]["value"], r.get("Description"), r.get("Value")) if x)

    def volt(self, ref):
        r = self.row(ref)
        return _first(NUM_V, str(r.get("Voltage_Rating") or ""), self.parts[ref]["value"], r.get("Description"))

    def amp(self, ref):
        r = self.row(ref)
        return _first(NUM_A, str(r.get("Current_Rating") or ""), r.get("Description"), self.parts[ref]["value"])

    def label(self, ref):
        p = self.parts[ref]
        return f"{ref} {p['mpn'] or p['value']}".strip()


def kind(ref):
    m = re.match(r"^[A-Z]+", ref)
    return m.group(0) if m else ""


def analyse(parts, nets, db):
    P = Parts(parts, db)
    power = {n for n in nets if not GND.match(short(n)) and (nominal(n) is not None or INPUT.search(short(n))
                                                                or OUTPUT.search(short(n)))}
    conns = [r for r in parts if kind(r) in ("J", "P", "CN", "X", "TB")]
    external = {n for r in conns for n in parts[r]["nets"] if n in power or (not GND.match(short(n)) and n in nets
                and any(kind(x) == "C" for x, _, _ in nets[n]["pins"]))}
    rails = {}
    from . import stress
    for n in sorted(power | external):
        lim, by, unrated = stress.net_limit(P, nets, n)     # the lowest-rated part of any kind (IC pins, diodes, caps)
        rails[n] = {"net": short(n), "nominal_V": nominal(n), "external": n in external, "ac": bool(AC.match(short(n))),
                    "limit_V": lim, "limit_by": by, "unrated": [P.label(r) for r in unrated],
                    "pins": len(nets[n]["pins"])}
    path = [n for n in rails if INPUT.search(short(n))]       # the input and the nets behind its fuse / filter
    if not path:
        ext = [n for n in rails if rails[n]["external"]]
        path = sorted(ext, key=lambda n: -(rails[n]["limit_V"] or rails[n]["nominal_V"] or 0))[:1]
    ins = sorted(path, key=lambda n: (not rails[n]["external"], len(short(n))))[:1]   # the one on the connector
    if ins:                       # the input voltage reaches every net of the path: the lowest-rated part on any
        rated = [rails[n] for n in path if rails[n]["limit_V"]]
        if rated:
            low = min(rated, key=lambda r: r["limit_V"])
            rails[ins[0]].update(path_limit_V=low["limit_V"], path_limit_by=f"{low['limit_by']} on {low['net']}")
        rails[ins[0]]["path"] = [short(n) for n in path]
        rails[ins[0]]["path_unrated"] = sorted({u for n in path for u in rails[n]["unrated"]})
    outs = [n for n in rails if n not in path and (rails[n]["external"] or OUTPUT.search(short(n)))]
    for n in rails:
        rails[n]["role"] = "input" if n in ins else "input path" if n in path else "output" if n in outs else "internal"
    # current: an inductor touching an output rail; a fuse on the input
    ind = {}
    for r in parts:
        if kind(r) == "L":
            for n in parts[r]["nets"]:
                if n in outs and P.amp(r):
                    ind[n] = (r, P.amp(r))
    fuses = [r for r in parts if kind(r) == "F"]
    tvs = [r for r in parts if (P.row(r).get("Clamp_V") or re.search(r"\bTVS\b|transient|SMBJ|SMCJ|SMAJ|ESD",
                                                                      P.text(r) + " " + (parts[r]["mpn"] or ""), re.I))]
    iso = [r for r in parts if re.search(r"isolat", P.text(r), re.I) and kind(r) in ("U", "IC", "T")]
    return {"rails": rails, "inputs": ins, "outputs": outs, "inductors": ind, "fuses": fuses, "tvs": tvs,
            "isolators": iso, "parts": len(parts), "P": P}


def rows(a):
    """Draft SYS rows: (title, requirement, evidence, confidence, method)."""
    R, P, rl = [], a["P"], a["rails"]
    for n in a["inputs"]:
        r = rl[n]
        v, by = r.get("path_limit_V") or r["limit_V"], r.get("path_limit_by") or r["limit_by"]
        via = r.get("path", [r["net"]])
        lim = f"; the input path ({', '.join(via)}) takes at most {v:g} V (lowest-rated part: {by})" if v else ""
        if r.get("path_unrated"):
            lim += f"; NOT rated in the library, check their datasheets: {', '.join(r['path_unrated'][:5])}"
        nom = f"nominal {r['nominal_V']:g} V, " if r["nominal_V"] else ""
        R.append(("Input voltage range", f"Operate from {nom}_(min)_ to _(max)_ V on {r['net']}"
                  + (f", max below {v:g} V" if v else ""), f"input rail {r['net']}" + lim,
                  "medium" if v else "low", "T"))
    for n in a["outputs"]:
        r = rl[n]
        if r["nominal_V"]:
            R.append((f"Output {r['net']}", f"Regulate {r['net']} at {r['nominal_V']:g} V ± _(tol)_ %",
                      f"net name {r['net']}" + (f"; parts on it rated {r['limit_V']:g} V min ({r['limit_by']})"
                                                if r["limit_V"] else ""), "medium", "T"))
        if n in a["inductors"]:
            ref, amp = a["inductors"][n]
            R.append((f"Output current {r['net']}", f"Deliver up to _(I)_ A on {r['net']} (≤ {amp:g} A: the "
                      f"inductor's rating)", f"{P.label(ref)} rated {amp:g} A", "low", "T"))
    for ref in a["fuses"]:
        amp = P.amp(ref)
        R.append(("Input overcurrent protection", "Interrupt an input fault current" + (f" (fuse {amp:g} A)" if amp else ""),
                  f"fuse {P.label(ref)}", "medium", "T"))
    if a["tvs"]:
        R.append(("Input surge / transient protection", "Withstand _(surge per standard)_ without damage",
                  "clamp(s): " + ", ".join(P.label(r) for r in a["tvs"][:4]), "medium", "T"))
    if a["isolators"]:
        R.append(("Isolation", "_(basic / reinforced)_ isolation between _(domains)_, _(V)_ working",
                  "isolator(s): " + ", ".join(P.label(r) for r in a["isolators"][:4]), "medium", "C"))
    for t, q in (("Efficiency", "≥ _(η)_ % at _(load)_"), ("Ambient temperature", "Full rating from _(min)_ to _(max)_ °C"),
                 ("Standards and EMC", "Comply with _(safety standard)_ and _(EMC class)_"),
                 ("Design life", "_(years)_ at _(mission profile)_")):
        R.append((t, q, "not in the design: the owner sets it", "owner", "T" if t != "Standards and EMC" else "C"))
    return R


def render(a, R, name=""):
    L = ["---", "type: requirements-draft", "status: draft", "generated: forge req infer", "---",
         f"# {name + ': ' if name else ''}requirements inferred from the design (DRAFT)", "",
         "> Read from the schematic as it is. **Nothing here is a requirement until the owner confirms it.** Edit the",
         "> rows, fill the _(…)_ blanks, delete what is wrong, then `forge req infer --apply` (or copy them into",
         "> System_Requirements.md) and pass G2. Confidence: medium = read from parts and nets, low = a bound only,",
         "> owner = the design cannot tell.", "",
         f"Board: {a['parts']} parts; input {', '.join(short(n) for n in a['inputs']) or 'not found'}; outputs "
         f"{', '.join(short(n) for n in a['outputs']) or 'not found'}.", "",
         "## Rails", "", "| Net | Role | Nominal | Max the parts take | Limited by |", "|---|---|---|---|---|"]
    for n, r in sorted(a["rails"].items(), key=lambda x: (x[1]["role"], x[1]["net"])):
        L.append(f"| {r['net']} | {r['role']} | {r['nominal_V'] if r['nominal_V'] is not None else '?'} V | "
                 f"{str(r['limit_V']) + ' V' if r['limit_V'] else '?'} | {r['limit_by'] or '-'} |")
    L += ["", "## Draft system requirements", "", "| ID | Title | Requirement | Evidence | Confidence | Method |",
          "|---|---|---|---|---|---|"]
    for i, (t, q, e, c, m) in enumerate(R, 1):
        L.append(f"| SYS-{i:03d} | {t} | {q} | {e} | {c} | {m} |")
    return "\n".join(L) + "\n"


def infer_sheet(sch):
    from . import insulation
    text = insulation.export(sch)
    if text is None:
        return None
    parts, nets = insulation.parse(text)
    a = analyse(parts, nets, insulation.db_rows())
    return a, rows(a)


def summary(a):
    """One line: what this board looks like."""
    def rail(n):
        r = a["rails"][n]
        v = r.get("path_limit_V") or r["limit_V"]
        return r["net"] + (f" ({r['nominal_V']:g} V)" if r["nominal_V"] else "") + \
            (f", parts rated ≥ {v:g} V" if v else "")
    return (f"{a['parts']} parts · in: " + ("; ".join(rail(n) for n in a["inputs"]) or "?") + " · out: "
            + ("; ".join(rail(n) for n in a["outputs"]) or "?"))


def apply(prj, draft):
    """Copy the draft's SYS rows into System_Requirements.md section 1 (replacing the template's fill-in rows)."""
    from . import guard
    rowsx = [l for l in open(draft).read().splitlines() if re.match(r"^\| SYS-\d{3} \|", l)]
    if not rowsx:
        raise SystemExit(f"no SYS rows in {prj.rel(draft)}")
    blank = [l.split("|")[1].strip() for l in rowsx if "_(" in l]
    if blank:
        raise SystemExit(f"{len(blank)} row(s) still have blanks to fill (_(…)_): {', '.join(blank[:8])}. The owner "
                         f"fills them in {prj.rel(draft)} (or deletes the row), then --apply again")
    import datetime as _dt
    out = []
    for l in rowsx:
        c = [x.strip() for x in l.strip("|").split("|")]
        if len(c) >= 6:
            out.append(f"| {c[0]} | {c[1]} | {c[2]} | inferred from the design ({c[3]}); owner-confirmed draft "
                       f"{_dt.date.today().isoformat()} | {c[5]} |")
    sr = prj.p("sys_req")
    t = open(sr).read() if os.path.exists(sr) else "# System requirements (L1)\n\n## 1. Function and ratings\n" \
        "| ID | Title | Requirement | Source | Method |\n|---|---|---|---|---|\n"
    t2 = re.sub(r"(?m)^\| SYS-\d{3} \| _\(fill in\)_.*\n?", "", t)
    t2 = re.sub(r"(?m)^\| SYS-\d{3} \|.*\| inferred from the design .*\n?", "", t2)   # a second --apply replaces
    m = re.search(r"(?m)^## 1\..*\n\| ID .*\n\|---.*\n", t2)
    if m:
        t2 = t2[:m.end()] + "\n".join(out) + "\n" + t2[m.end():]
    else:
        t2 = t2.rstrip("\n") + "\n\n## 1. Function and ratings (inferred)\n| ID | Title | Requirement | Source | Method |\n" \
             "|---|---|---|---|---|\n" + "\n".join(out) + "\n"
    return guard.write(prj.root, sr, t2), len(out)


def main(prj, argv):
    from . import guard, insulation
    if argv[:1] != ["infer"]:
        print(__doc__)
        return 1
    draft = os.path.join(prj.p("requirements"), "Inferred_Requirements.md")
    if "--apply" in argv:
        if not os.path.exists(draft):
            raise SystemExit("no draft yet: run `forge req infer` first and have the owner confirm it")
        (st, path), n = apply(prj, draft)
        print(f"{n} requirement(s) into {prj.rel(path)} ({st})"
              + (": your System_Requirements.md was kept; merge the proposed copy" if st == "proposed" else "")
              + "\n  next: derive HWR / SWR rows (/pe-requirements), forge vmodel build && forge vmodel check, pass G2")
        return 0
    sch = insulation.root_sheet(prj)
    if not sch:
        raise SystemExit("no drawn schematic found: nothing to infer from")
    r = infer_sheet(sch)
    if r is None:
        raise SystemExit("kicad-cli not found or the netlist export failed (forge doctor)")
    a, R = r
    if "--json" in argv:
        print(json.dumps({"summary": summary(a), "rails": a["rails"], "rows": R}, indent=1, default=str))
        return 0
    st, path = guard.write(prj.root, draft, render(a, R, prj.name), out=print)
    print(f"{summary(a)}\n{len(R)} draft requirement(s) in {prj.rel(path)} ({st}). Show them to the owner; on their OK: "
          "forge req infer --apply")
    return 0
