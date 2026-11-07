"""forge insulation: the schematic's insulation, checked from KiCad's own netlist. No model calls, no network.

  forge insulation [--json]          INS-1/2/3 and NET-4 on the project's root schematic
  forge insulation --rules [--keep DIR] [--json]   CLS-2: prove the project's .kicad_dru enforces the decided
                                     table (KiCad DRC); --keep saves the test board, rules and DRC report

Rules
  INS-1  every net in an insulation domain that holds an HV net is itself in the HV class.
         A domain is the set of nets joined through any part that is not a barrier part.
  INS-2  no domain holds both an HV net and a SELV net (a schematic path across the barrier). The finding
         names the parts on the shortest path.
  INS-3  each barrier part's package creepage (MC_Parts Iso_Creepage_mm) is at least the board barrier, or the
         project records how the gap is covered (slot, coating).
  NET-4  every HV net is in exactly one island class (only when the project lists its islands).
  CLS-2  for each decided class pair, KiCad's DRC with the project's rules flags copper just closer than the
         decided clearance and creepage (a generated two-pad test board per pair).

Barrier parts: an MC_Parts row with Iso_Rating set (looked up by the symbol's MC_ID field), or an MPN / value
listed in project.toml [insulation] barriers. Each has a grade read from its text (reinforced / double / Y1,
basic / supplementary / Y2, functional). Any barrier bounds the HV side for INS-1; only one of barrier_grade
(default reinforced) separates SELV from HV for INS-2, so a single Y2 cap from HV to GND is an INS-2 error.
A duplicated reference is an error (INS-0) and never counts as a barrier.

Net classes come from KiCad's netlist (a net in several classes is written "HV,HV_DCP"), so the rule is the
project's own pattern set, not a re-implementation of it.

project.toml
  [insulation]
  hv_class = "HV"                     # parent class of every hazardous-voltage net
  selv = ["+*", "*/+*", "GND", "*/GND", "PGND*", "*/PGND*"]   # SELV net names (patterns, fnmatch)
  allow_non_hv = ["PE", "*/PE"]       # nets allowed in an HV domain without the HV class (with a reason in a comment)
  barrier_grade = "reinforced"        # grade a barrier needs to separate SELV from HV (INS-2)
  barriers = { "ISO-DCDC-1" = "reinforced 4 kVac" }   # barrier parts by MPN or value with their grade
  barrier_creepage_mm = 10.0          # the board's reinforced barrier (INS-3)
  package_path = { "ISO-DRV-1" = "slot + conformal coat (layout spec)" }   # INS-3 gaps covered
  islands = ["HV_DCP", "HV_DCN", "HV_NP", "HV_PH_A", "HV_PH_B", "HV_PH_C"]          # NET-4
  [[insulation.rule]]                 # CLS-2, one table per class pair; "!HV" = every defined class that is
                                      # neither HV nor an island (Default, Power, ...), each tested
  a = "HV"
  b = "!HV"
  creepage_mm = 10.0
  clearance_mm = 10.0
"""
import collections
import fnmatch
import json
import os
import re
import subprocess
import sys
import tempfile

from . import kicad

DEFAULT_SELV = ["+*", "*/+*", "GND", "*/GND", "PGND*", "*/PGND*"]
SHOW = 40          # findings listed per rule before a summary line


# ------------------------------------------------------------------ netlist
def parse(text):
    """(parts, nets) from a kicadsexpr netlist.
    parts: {ref: {value, mc_id, mpn, nets: [net]}}; nets: {name: {classes: [..], pins: [(ref, pin, function)]}}"""
    tree = kicad.parse_sexpr(text)
    parts = {}
    for c in (kicad._get(tree, "components") or [])[1:]:
        fields = {}
        for f in (kicad._get(c, "fields") or [])[1:]:
            fields[kicad._val(f, "name")] = next((x for x in f[1:] if isinstance(x, str)), "")
        ref = kicad._val(c, "ref")
        if ref in parts:                 # a duplicated reference: its nodes cannot be told apart
            parts[ref]["dup"] += 1
            parts[ref]["values"].append(kicad._val(c, "value"))
            continue
        parts[ref] = {"value": kicad._val(c, "value"), "mc_id": fields.get("MC_ID", ""), "mpn": fields.get("MPN", ""),
                      "nets": [], "dup": 1, "values": [kicad._val(c, "value")]}
    nets = {}
    for n in (kicad._get(tree, "nets") or [])[1:]:
        name = kicad._val(n, "name").replace("{slash}", "/")
        classes = [c for c in kicad._val(n, "class", "Default").split(",") if c]
        pins = []
        for nd in n:
            if isinstance(nd, list) and nd and nd[0] == "node":
                ref, pin, fn = kicad._val(nd, "ref"), kicad._val(nd, "pin"), kicad._val(nd, "pinfunction")
                if fn.endswith("_" + pin):              # KiCad 10 writes pin K of pin 1 as "K_1"
                    fn = fn[:-len(pin) - 1]
                pins.append((ref, pin, fn))
                if ref in parts:
                    parts[ref]["nets"].append(name)
        nets[name] = {"classes": classes, "pins": pins}
    return parts, nets


def export(sch):
    """kicadsexpr netlist text of a schematic's whole hierarchy, or None."""
    cli = kicad.find_cli()
    if not cli:
        return None
    out = kicad._out(sch, None, ".ins.net")
    os.remove(out)
    subprocess.run([cli, "sch", "export", "netlist", "--format", "kicadsexpr", "-o", out, sch], capture_output=True)
    if not os.path.exists(out):
        return None
    text = open(out).read()
    os.remove(out)
    return text


# ------------------------------------------------------------------ the analysis
def short(name):
    return name.lstrip("/") or name


def matches(name, pats):
    return any(fnmatch.fnmatchcase(name, p) or fnmatch.fnmatchcase(short(name), p) for p in pats)


GRADE = {"functional": 0, "basic": 1, "reinforced": 2}


def grade_of(text):
    """Insulation grade named in an Iso_Rating / barriers entry: 'reinforced', 'basic', 'functional' or None.
    Y1 capacitors bridge reinforced insulation, Y2 basic (IEC 60384-14)."""
    t = (text or "").lower()
    if "reinforced" in t or "double" in t or re.search(r"\by1\b", t):
        return "reinforced"
    if "basic" in t or "supplementary" in t or re.search(r"\by[2-4]\b", t):
        return "basic"
    if "functional" in t:
        return "functional"
    return None


def barrier_parts(parts, cfg, db):
    """{ref: {"why", "grade"}} for parts that separate insulation domains. A duplicated reference is never a
    barrier: its pins cannot be told apart, so it joins conservatively."""
    named = cfg.get("barriers", [])
    named = named if isinstance(named, dict) else {k: "" for k in named}
    out = {}
    for ref, p in parts.items():
        if p["dup"] > 1:
            continue
        row = (db or {}).get(p["mc_id"]) if p["mc_id"] else None
        key = p["mpn"] if p["mpn"] in named else p["value"] if p["value"] in named else None
        if row and row.get("Iso_Rating"):
            out[ref] = {"why": f"{row['Iso_Rating']} (MC_Parts {p['mc_id']})", "grade": grade_of(row["Iso_Rating"])}
        elif key is not None:
            out[ref] = {"why": f"[insulation] barriers: {named[key] or 'grade not given'}", "grade": grade_of(named[key])}
    return out


def domains(parts, nets, barriers):
    """{net: domain id} joining nets through every part that is not in `barriers` (DNP parts still join: once
    fitted they would)."""
    par = {n: n for n in nets}

    def root(x):
        while par[x] != x:
            par[x] = par[par[x]]
            x = par[x]
        return x
    for ref, p in parts.items():
        if ref in barriers or ref.startswith("#"):
            continue
        ns = [n for n in p["nets"] if n in par]
        for n in ns[1:]:
            par[root(n)] = root(ns[0])
    return {n: root(n) for n in nets}


def path(parts, nets, barriers, start, goal_set):
    """Shortest part chain from net `start` to any net in goal_set, through parts not in `barriers`:
    [(net, ref), ..., (goal net, None)]."""
    by_net = collections.defaultdict(set)
    for ref, p in parts.items():
        if ref not in barriers and not ref.startswith("#"):
            for n in p["nets"]:
                by_net[n].add(ref)
    prev, q = {start: None}, collections.deque([start])
    while q:
        n = q.popleft()
        if n in goal_set:
            chain, cur = [], n
            while cur is not None:
                chain.append(cur)
                cur = prev[cur]
            chain.reverse()
            return [(a, sorted(by_net[a] & by_net[b])[0]) for a, b in zip(chain, chain[1:])] + [(chain[-1], None)]
        for ref in sorted(by_net[n]):
            for m in parts[ref]["nets"]:
                if m not in prev:
                    prev[m] = n
                    q.append(m)
    return []


def analyse(parts, nets, cfg, db):
    """{rule: [finding]} with findings {net|ref, claim, actual, fix}. Pure: no KiCad, no files.
    INS-0 holds integrity / configuration findings (duplicate references are errors, the rest warnings)."""
    hv = cfg.get("hv_class", "HV")
    selv_pats = cfg.get("selv", DEFAULT_SELV)
    allow = cfg.get("allow_non_hv", [])
    need_grade = cfg.get("barrier_grade", "reinforced")
    bars = barrier_parts(parts, cfg, db)
    strong = {r: b for r, b in bars.items() if GRADE.get(b["grade"], -1) >= GRADE.get(need_grade, 2)}
    dom = domains(parts, nets, bars)            # INS-1: any barrier bounds the HV circuit
    dom2 = domains(parts, nets, strong)         # INS-2: only a barrier of the required grade separates SELV
    is_hv = {n: hv in v["classes"] for n, v in nets.items()}
    is_selv = {n: (not is_hv[n]) and matches(n, selv_pats) for n in nets}
    out = {"INS-0": [], "INS-1": [], "INS-2": [], "INS-3": [], "NET-4": []}
    label = lambda r: parts[r]["mpn"] or parts[r]["value"]

    for ref, p in sorted(parts.items()):
        if p["dup"] > 1 and not ref.startswith("#"):
            out["INS-0"].append({"ref": ref, "severity": "error", "claim": f"reference {ref} is unique",
                                 "actual": f"{p['dup']} parts share it ({', '.join(sorted(set(p['values'])))}): pins "
                                           "cannot be told apart, so it never counts as a barrier",
                                 "fix": "Annotate the schematic (Tools → Annotate) so every reference is unique"})
    for ref, b in sorted(bars.items()):
        if b["grade"] is None:
            out["INS-0"].append({"ref": ref, "severity": "warn", "claim": f"{ref} ({label(ref)}) has an insulation grade",
                                 "actual": f"{b['why']}: no grade, so it does not separate SELV from HV (INS-2)",
                                 "fix": "Say 'reinforced', 'basic' or 'functional' (or Y1 / Y2) in Iso_Rating or the "
                                        "[insulation] barriers entry"})

    def where(n):
        pins = nets[n]["pins"]
        p = ", ".join(f"{r}.{fn or pin}" for r, pin, fn in pins[:3] if not r.startswith("#"))
        return p + ("…" if len(pins) > 3 else "")

    members = collections.defaultdict(list)
    for n, d in dom.items():
        members[d].append(n)
    for d, ns in members.items():
        hv_nets = [n for n in ns if is_hv[n]]
        if not hv_nets:
            continue
        for n in sorted(ns):
            if is_hv[n] or is_selv[n] or matches(n, allow):
                continue
            pad = n.startswith("unconnected-")
            out["INS-1"].append({"net": short(n), "claim": f"{short(n)} is on the HV side, so it is in class {hv}",
                                 "actual": f"class {','.join(nets[n]['classes'])}; joined to {short(hv_nets[0])}"
                                           f" ({where(n)})",
                                 "fix": ("An unconnected pin of an HV-side part: KiCad gives its pad Default rules. Wire "
                                         "it to a named HV_ net (a no-connect flag alone keeps it netless), or add a "
                                         ".kicad_dru rule for that footprint's pads") if pad else
                                        f"Name it HV_… so the {hv} rules apply (or list it in allow_non_hv with a reason)"})
    members2 = collections.defaultdict(list)
    for n, d in dom2.items():
        members2[d].append(n)
    for d, ns in members2.items():
        hv_nets = [n for n in ns if is_hv[n]]
        if not hv_nets:
            continue
        for s in [n for n in ns if is_selv[n]]:
            chain = path(parts, nets, strong, s, set(hv_nets))
            weak = [r for _, r in chain if r in bars]
            text = " → ".join(short(n) + (f" via {r} ({label(r)}" + (f", {bars[r]['grade'] or 'no grade'}"
                                                                      if r in bars else "") + ")" if r else "")
                              for n, r in chain)
            out["INS-2"].append({"net": short(s),
                                 "claim": f"SELV net {short(s)} is separated from HV by {need_grade} insulation",
                                 "actual": (f"only by a weaker barrier: {text}" if weak else
                                            f"joined to HV without a barrier: {text}") if text else "same domain as HV",
                                 "fix": (f"A {need_grade} barrier is needed here (two basic barriers in series, or a "
                                         f"{need_grade}-rated part)") if weak else
                                        "Fix the connection, or mark the isolating part on the path as a barrier "
                                        "(MC_Parts Iso_Rating, or [insulation] barriers) with its grade"})
    # INS-3: package creepage of each barrier part
    need = cfg.get("barrier_creepage_mm")
    covered = cfg.get("package_path", {})
    if need and bars:
        seen = set()
        for ref in sorted(bars):
            key = label(ref)
            if key in seen or key in covered:
                continue
            seen.add(key)
            n_refs = sum(1 for r in bars if label(r) == key)
            row = (db or {}).get(parts[ref]["mc_id"]) or {}
            try:
                have = float(row.get("Iso_Creepage_mm", ""))
            except ValueError:
                have = None
            if have is None:
                out["INS-3"].append({"ref": key, "claim": f"{key} package creepage is recorded",
                                     "actual": f"Iso_Creepage_mm empty ({n_refs} placed)",
                                     "fix": "Read it from the datasheet and set it in MC_Parts"})
            elif have < float(need):
                out["INS-3"].append({"ref": key, "claim": f"{key} package creepage ≥ the {need:g} mm barrier",
                                     "actual": f"{have:g} mm ({n_refs} placed): a board slot does not lengthen the "
                                               "path over the package body",
                                     "fix": "Record how it is covered in [insulation] package_path (slot, conformal "
                                            "coat, component certification)"})
    # NET-4: islands
    islands = cfg.get("islands", [])
    for n in sorted(x for x in nets if is_hv[x]) if islands else []:
        got = [c for c in nets[n]["classes"] if c in islands]
        if len(got) != 1:
            out["NET-4"].append({"net": short(n), "claim": f"{short(n)} is in exactly one island class",
                                 "actual": "in none" if not got else "in " + ", ".join(got),
                                 "fix": "Add or narrow a net-class pattern in the .kicad_pro so it lands in one "
                                        "island (fail-closed: DRC treats it as its own potential)"})
    return out, {"barriers": bars, "strong": strong, "domains": len(members), "hv_nets": sum(is_hv.values()),
                 "nets": len(nets)}


# ------------------------------------------------------------------ forge check sub-check
def project_cfg(prj):
    return prj.cfg.get("insulation", {})


def root_sheet(prj):
    from . import check
    f = [x for x in check.roots(prj, "kicad_sch") if check.has_content(x)]
    return f[0] if f else None


def db_rows():
    from . import evidence
    return evidence.rows_by_id()


def as_check(prj, opts):
    from .check import finding, result
    sch = root_sheet(prj)
    if not sch:
        return result("skip", reason="no schematic yet")
    text = export(sch)
    if text is None:
        return result("skip", reason="kicad-cli not found or the netlist export failed", missing_tool=True)
    cfg = project_cfg(prj)
    parts, nets = parse(text)
    hv = cfg.get("hv_class", "HV")
    loc = prj.rel(sch)
    if not any(hv in v["classes"] for v in nets.values()):
        if cfg:          # the project says it has HV, but no net reached the class: names or patterns are wrong
            return result("fail", [finding(f"some nets are in class {hv} ([insulation] is set)", f"no net is in class {hv}",
                                           loc, f"Check the HV net names and the {hv} net-class patterns in the .kicad_pro")])
        return result("skip", reason=f"no net in class {hv} and no [insulation] table")
    found, info = analyse(parts, nets, cfg, db_rows())
    F, W = [], []
    for rule in ("INS-0", "INS-2", "INS-1", "NET-4", "INS-3"):
        items = found[rule]
        for x in items[:SHOW]:
            bucket = W if rule == "INS-3" or x.get("severity") == "warn" else F
            bucket.append(finding(f"{rule}: {x['claim']}", x["actual"], loc, x["fix"]))
        if len(items) > SHOW:
            (W if rule == "INS-3" else F).append(finding(f"{rule}: (more)", f"{len(items) - SHOW} more like these "
                                                         "(forge insulation --json)", loc))
    if not info["barriers"]:
        W.append(finding("barrier parts are known", "no part is marked as a barrier, so the whole board is one domain",
                         loc, "Set Iso_Rating (with its grade) on isolators, isolated DC-DC and Y-caps in MC_Parts"))
    if not cfg.get("islands"):
        W.append(finding("NET-4 runs", "not configured: no [insulation] islands, so island classes are not checked",
                         loc, "List the island classes in project.toml [insulation] islands"))
    if not cfg.get("barrier_creepage_mm"):
        W.append(finding("INS-3 runs", "not configured: no [insulation] barrier_creepage_mm, so barrier package "
                         "creepage is not checked", loc, "Set the board's barrier creepage in project.toml"))
    reason = (f"{info['nets']} nets, {info['hv_nets']} HV, {info['domains']} domains, {len(info['barriers'])} barrier "
              f"part(s), {len(info['strong'])} of grade {cfg.get('barrier_grade', 'reinforced')}")
    return result("fail" if F else "warn" if W else "pass", F + W, reason=reason)


def gate_items(prj, rules=False):
    """[(ok, text)] for gates G6 (schematic) and G7 / G8 (rules=True: the DRU proof too). A skip passes only when
    the project has no HV net and no [insulation] table; a missing tool or an unset rule table fails."""
    out = []
    r = as_check(prj, {})
    hv = r["status"] != "skip"
    if r["status"] == "skip":
        out.append((not r["missing_tool"], f"insulation: skipped ({r['reason']})"))
    else:
        bad = [f for f in r["findings"] if not f["claim"].startswith(("NET-4 runs", "INS-3 runs", "barrier parts"))]
        out.append((r["status"] != "fail", f"insulation ({r['reason']}): {r['status']}"
                    + "".join(f"; {f['claim']}: {f['actual']}" for f in bad[:3])))
    if rules:
        r2 = as_check_rules(prj, {})
        if r2["status"] == "skip":
            ok = not r2["missing_tool"] and not hv
            out.append((ok, f"insulation rules (CLS-2): skipped ({r2['reason']})"
                        + ("" if ok else ": the board has HV nets, so the .kicad_dru must be proven")))
        else:
            out.append((r2["status"] != "fail", f"insulation rules (CLS-2): {r2['status']} ({r2['reason']})"
                        + "".join(f"; {f['claim']}" for f in r2["findings"][:3])))
    return out


# ------------------------------------------------------------------ CLS-2: the rules really enforce the table
TEST_BOARD = r'''
import json, sys, pcbnew
spec = json.load(open(sys.argv[1]))
b = pcbnew.BOARD()
mm = pcbnew.FromMM
def net(name):
    ni = b.FindNet(name)
    if not ni:
        ni = pcbnew.NETINFO_ITEM(b, name); b.Add(ni)
    return ni
W, H = spec["w"], spec["h"]
for a, c in (((0, 0), (W, 0)), ((W, 0), (W, H)), ((W, H), (0, H)), ((0, H), (0, 0))):
    s = pcbnew.PCB_SHAPE(b); s.SetShape(pcbnew.SHAPE_T_SEGMENT); s.SetLayer(pcbnew.Edge_Cuts)
    s.SetStart(pcbnew.VECTOR2I(mm(a[0]), mm(a[1]))); s.SetEnd(pcbnew.VECTOR2I(mm(c[0]), mm(c[1]))); s.SetWidth(mm(0.1))
    b.Add(s)
for i, p in enumerate(spec["pads"]):
    f = pcbnew.FOOTPRINT(b); f.SetReference("TP%d" % (i + 1)); f.SetValue("pad")
    f.SetPosition(pcbnew.VECTOR2I(mm(p["x"]), mm(p["y"])))
    b.Add(f)
    pad = pcbnew.PAD(f); pad.SetNumber("1"); pad.SetShape(pcbnew.PAD_SHAPE_RECT); pad.SetAttribute(pcbnew.PAD_ATTRIB_SMD)
    ls = pcbnew.LSET(); ls.AddLayer(pcbnew.F_Cu); ls.AddLayer(pcbnew.F_Mask); pad.SetLayerSet(ls); pad.SetSize(pcbnew.VECTOR2I(mm(1), mm(1)))
    pad.SetPosition(pcbnew.VECTOR2I(mm(p["x"]), mm(p["y"])))
    f.Add(pad); pad.SetNet(net(p["net"]))
b.Save(spec["out"])
'''


def rule_cases(cfg, known):
    """Test pairs per (rule, constraint, other-side class). `known` = classes defined in the .kicad_pro.
    "!X" on a side means every defined class that is neither X nor an island: Default, Power, ... each tested,
    so a rule that only covers Default is caught. Each case also has an "outside" pair (just wider than the
    value) that should not be flagged by that constraint."""
    hv = cfg.get("hv_class", "HV")
    islands = set(cfg.get("islands", []))

    def sides(c):
        if c.startswith("!"):
            x = c[1:]
            return [(f"{c} as {k}", [k] if k != "Default" else []) for k in sorted(known)
                    if k != x and k != hv and k not in islands]
        return [(c, [hv, c] if c in islands else [c])]
    cases = []
    for i, r in enumerate(cfg.get("rule", [])):
        for kind in ("clearance", "creepage"):
            v = r.get(f"{kind}_mm")
            if not v:
                continue
            for la, ca in sides(r["a"]):
                for lb, cb in sides(r["b"]):
                    j = len(cases)
                    cases.append({"rule": i, "a": la, "b": lb, "kind": kind, "need": float(v), "a_cls": ca,
                                  "b_cls": cb, "net_a": f"T{j}_A", "net_b": f"T{j}_B", "out_a": f"T{j}_OA",
                                  "out_b": f"T{j}_OB"})
    return cases


def test_rules(dru, pro, cfg, workdir=None, margin=0.05, wide=0.5):
    """[(case, flagged, detail, outside_flagged)] from KiCad's DRC on a generated board with the project's rules.
    The pads sit on F.Cu (outer layer only: inner-layer clearance is not tested)."""
    py, cli = kicad.find_python(), kicad.find_cli()
    if not (py and cli):
        return None
    proj = json.load(open(pro))
    ns = proj.setdefault("net_settings", {})
    known = {c["name"] for c in ns.get("classes", [])} | {"Default"}
    cases = rule_cases(cfg, known)
    d = workdir or tempfile.mkdtemp(prefix="pe-forge-cls2-")
    os.makedirs(d, exist_ok=True)
    stem = os.path.join(d, "cls2")
    pats = list(ns.get("netclass_patterns") or [])
    for c in cases:                                  # each test net gets exactly the classes the case names
        for nets_, cls in (((c["net_a"], c["out_a"]), c["a_cls"]), ((c["net_b"], c["out_b"]), c["b_cls"])):
            for net in nets_:
                pats += [{"netclass": k, "pattern": net} for k in cls]
    ns["netclass_patterns"] = pats
    pads, y = [], 10.0
    for c in cases:
        c["gap"], c["wide"] = round(max(c["need"] - margin, 0.05), 3), round(c["need"] + wide, 3)
        step = max(30.0, 3 * c["need"])
        pads += [{"x": 10.0, "y": y, "net": c["net_a"]}, {"x": 11.0 + c["gap"], "y": y, "net": c["net_b"]},
                 {"x": 10.0, "y": y + step, "net": c["out_a"]}, {"x": 11.0 + c["wide"], "y": y + step, "net": c["out_b"]}]
        y += 2 * step                               # rows far apart: only the pair itself can interact
    spec = {"pads": pads, "w": 40.0 + max([c["need"] for c in cases] or [0]), "h": y + 10.0, "out": stem + ".kicad_pcb"}
    json.dump(spec, open(stem + ".json", "w"))
    script = stem + "_mk.py"
    open(script, "w").write(TEST_BOARD)
    r = subprocess.run([py, script, stem + ".json"], capture_output=True, text=True)
    if not os.path.exists(spec["out"]):
        raise RuntimeError("test board not written: " + (r.stderr or r.stdout)[-400:])
    # after the board: saving it writes a default .kicad_pro, which must not survive
    json.dump(proj, open(stem + ".kicad_pro", "w"), indent=2)
    open(stem + ".kicad_dru", "w").write(open(dru).read() if dru and os.path.exists(dru) else "(version 1)\n")
    rep = stem + ".drc.json"
    subprocess.run([cli, "pcb", "drc", "--format", "json", "--severity-all", "-o", rep, spec["out"]],
                   capture_output=True)
    v = json.load(open(rep)).get("violations", [])

    def hits(kind, a, b):
        return [x for x in v if x.get("type") == kind and
                all(any(f"[{n}]" in i.get("description", "") for i in x.get("items", [])) for n in (a, b))]
    out = []
    for c in cases:
        h = hits(c["kind"], c["net_a"], c["net_b"])
        missing = sorted({k for k in c["a_cls"] + c["b_cls"] if k not in known})
        c["undefined"] = missing
        out.append((c, bool(h), h[0]["description"] if h else "not flagged", bool(hits(c["kind"], c["out_a"], c["out_b"]))))
    return out


def project_files(prj):
    sch = root_sheet(prj)
    from . import check
    pcbs = check.roots(prj, "kicad_pcb")
    base = os.path.splitext(sch or (pcbs[0] if pcbs else ""))[0]
    if not base:
        return None, None
    return base + ".kicad_dru", base + ".kicad_pro"


def as_check_rules(prj, opts):
    from .check import finding, result
    cfg = project_cfg(prj)
    if not cfg.get("rule"):
        return result("skip", reason="no [[insulation.rule]] table in project.toml")
    dru, pro = project_files(prj)
    if not pro or not os.path.exists(pro):
        return result("skip", reason="no KiCad project yet")
    res = test_rules(dru, pro, cfg, workdir=opts.get("keep"))
    if res is None:
        return result("skip", reason="KiCad's Python (pcbnew) or kicad-cli not found", missing_tool=True)
    F, W = [], []
    loc = prj.rel(dru)
    for c, ok, detail, wide_hit in res:
        pair = f"{c['a']} ↔ {c['b']}"
        if not ok:
            F.append(finding(f"CLS-2: {pair} {c['kind']} ≥ {c['need']:g} mm is enforced",
                             f"copper {c['gap']:g} mm apart is not flagged ({detail})", loc,
                             f"Add or fix a {c['kind']} rule for this pair in the .kicad_dru"))
        if c["undefined"]:
            W.append(finding(f"CLS-2: {pair} uses defined net classes",
                             f"{', '.join(c['undefined'])} not defined in the .kicad_pro: the result rests on whatever "
                             "rule fires for an undefined class", prj.rel(pro), "Define the class in Board Setup → Net Classes"))
        if wide_hit:
            W.append(finding(f"CLS-2: {pair} {c['kind']} at {c['wide']:g} mm (wider than {c['need']:g}) is clean",
                             "flagged: the rules are stricter than the table, or another rule applies", loc,
                             "Check which rule fires; tighten the table or relax the rule"))
    return result("fail" if F else "warn" if W else "pass", F + W,
                  reason=f"{len(res)} case(s) on a generated test board, outer layer only")


# ------------------------------------------------------------------ CLI
def main(prj, argv):
    as_json = "--json" in argv
    if "--rules" in argv:
        keep = argv[argv.index("--keep") + 1] if "--keep" in argv else None
        r = as_check_rules(prj, {"keep": os.path.abspath(keep) if keep else None})
        if keep:
            print(f"test board, rules and DRC report kept in {keep}")
        print(json.dumps(r, indent=1) if as_json else render(r))
        return 0 if r["status"] != "fail" else 1
    sch = root_sheet(prj)
    if not sch:
        print("no schematic yet")
        return 0
    text = export(sch)
    if text is None:
        print("netlist export failed (kicad-cli)")
        return 1
    parts, nets = parse(text)
    found, info = analyse(parts, nets, project_cfg(prj), db_rows())
    if as_json:
        print(json.dumps({"info": {k: v for k, v in info.items()}, "findings": found}, indent=1))
    else:
        print(f"{info['nets']} nets, {info['hv_nets']} HV, {info['domains']} domains, "
              f"{len(info['barriers'])} barrier part(s)")
        for rule, items in found.items():
            print(f"{rule}: {len(items)}")
            for x in items[:10]:
                print(f"  {x.get('net') or x.get('ref')}: {x['actual']}")
    return 1 if any(found[k] for k in ("INS-1", "INS-2", "NET-4")) or \
        any(x.get("severity") == "error" for x in found["INS-0"]) else 0


def render(r):
    head = f"{r['status'].upper()}" + (f" ({r['reason']})" if r["reason"] else "")
    return "\n".join([head] + [f"  {f['claim']}: {f['actual']}" for f in r["findings"]])
