"""forge insulation: the schematic's insulation, checked from KiCad's own netlist. No model calls, no network.

  forge insulation [--json]          INS-1/2/3 and NET-4 on the project's root schematic
  forge insulation --rules [--json]  CLS-2: prove the project's .kicad_dru enforces the decided table (KiCad DRC)

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
listed in project.toml [insulation] barriers. Net classes come from KiCad's netlist (a net in several classes is
written "HV,HV_DCP"), so the rule is the project's own pattern set, not a re-implementation of it.

project.toml
  [insulation]
  hv_class = "HV"                     # parent class of every hazardous-voltage net
  selv = ["+*", "*/+*", "GND", "*/GND", "PGND*", "*/PGND*"]   # SELV net names (patterns, fnmatch)
  allow_non_hv = ["PE", "*/PE"]       # nets allowed in an HV domain without the HV class (with a reason in a comment)
  barriers = ["ISO-DCDC-1"]           # barrier parts by MPN or value, besides MC_Parts Iso_Rating
  barrier_creepage_mm = 10.0          # the board's reinforced barrier (INS-3)
  package_path = { "ISO-DRV-1" = "slot + conformal coat (layout spec)" }   # INS-3 gaps covered
  islands = ["HV_DCP", "HV_DCN", "HV_NP", "HV_PH_A", "HV_PH_B", "HV_PH_C"]          # NET-4
  [[insulation.rule]]                 # CLS-2, one table per class pair; "!HV" = any net not in class HV
  a = "HV"
  b = "!HV"
  creepage_mm = 10.0
  clearance_mm = 10.0
"""
import collections
import fnmatch
import json
import os
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
        parts[ref] = {"value": kicad._val(c, "value"), "mc_id": fields.get("MC_ID", ""), "mpn": fields.get("MPN", ""),
                      "nets": []}
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


def barrier_parts(parts, cfg, db):
    """{ref: reason} for parts that separate insulation domains."""
    named = set(cfg.get("barriers", []))
    out = {}
    for ref, p in parts.items():
        row = (db or {}).get(p["mc_id"]) if p["mc_id"] else None
        if row and row.get("Iso_Rating"):
            out[ref] = f"{p['mpn'] or p['value']}: {row['Iso_Rating']} (MC_Parts {p['mc_id']})"
        elif p["mpn"] in named or p["value"] in named:
            out[ref] = f"{p['mpn'] or p['value']}: listed in [insulation] barriers"
    return out


def domains(parts, nets, barriers):
    """{net: domain id} joining nets through every part that is not a barrier (DNP parts still join: once
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
    """Shortest part chain from net `start` to any net in goal_set, through non-barrier parts:
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
            out = []
            for a, b in zip(chain, chain[1:]):
                via = next(iter(by_net[a] & by_net[b]))
                out.append((a, via))
            return out + [(chain[-1], None)]
        for ref in sorted(by_net[n]):
            for m in parts[ref]["nets"]:
                if m not in prev:
                    prev[m] = n
                    q.append(m)
    return []


def analyse(parts, nets, cfg, db):
    """{rule: [finding]} with findings {net|ref, claim, actual, fix}. Pure: no KiCad, no files."""
    hv = cfg.get("hv_class", "HV")
    selv_pats = cfg.get("selv", DEFAULT_SELV)
    allow = cfg.get("allow_non_hv", [])
    bars = barrier_parts(parts, cfg, db)
    dom = domains(parts, nets, bars)
    is_hv = {n: hv in v["classes"] for n, v in nets.items()}
    is_selv = {n: (not is_hv[n]) and matches(n, selv_pats) for n in nets}
    members = collections.defaultdict(list)
    for n, d in dom.items():
        members[d].append(n)
    out = {"INS-1": [], "INS-2": [], "INS-3": [], "NET-4": []}

    def where(n):
        pins = nets[n]["pins"]
        p = ", ".join(f"{r}.{fn or pin}" for r, pin, fn in pins[:3] if not r.startswith("#"))
        return p + ("…" if len(pins) > 3 else "")

    for d, ns in members.items():
        hv_nets = [n for n in ns if is_hv[n]]
        if not hv_nets:
            continue
        selv = [n for n in ns if is_selv[n]]
        for s in selv:
            chain = path(parts, nets, bars, s, set(hv_nets))
            text = " → ".join(f"{short(n)}" + (f" via {r} ({parts[r]['mpn'] or parts[r]['value']})" if r else "")
                              for n, r in chain)
            out["INS-2"].append({"net": short(s), "claim": f"SELV net {short(s)} is separated from HV by a barrier part",
                                 "actual": f"joined to HV without a barrier: {text}" if text else "same domain as HV",
                                 "fix": "Fix the connection, or mark the isolating part on the path as a barrier "
                                        "(MC_Parts Iso_Rating, or [insulation] barriers)"})
        for n in sorted(ns):
            if is_hv[n] or is_selv[n] or matches(n, allow):
                continue
            out["INS-1"].append({"net": short(n), "claim": f"{short(n)} is on the HV side, so it is in class {hv}",
                                 "actual": f"class {','.join(nets[n]['classes'])}; joined to {short(hv_nets[0])}"
                                           f" ({where(n)})",
                                 "fix": f"Name it HV_… so the {hv} rules apply (or list it in allow_non_hv with a reason)"})
    # INS-3: package creepage of each barrier part
    need = cfg.get("barrier_creepage_mm")
    covered = cfg.get("package_path", {})
    if need:
        seen = set()
        for ref in sorted(bars):
            p = parts[ref]
            key = p["mpn"] or p["value"]
            if key in seen:
                continue
            seen.add(key)
            n_refs = sum(1 for r in bars if (parts[r]["mpn"] or parts[r]["value"]) == key)
            row = (db or {}).get(p["mc_id"]) or {}
            raw = row.get("Iso_Creepage_mm", "")
            try:
                have = float(raw)
            except ValueError:
                have = None
            if key in covered:
                continue
            if have is None:
                out["INS-3"].append({"ref": key, "claim": f"{key} package creepage is recorded",
                                     "actual": f"Iso_Creepage_mm empty ({n_refs} placed)",
                                     "fix": "Read it from the datasheet and set it in MC_Parts"})
            elif have < float(need):
                out["INS-3"].append({"ref": key, "claim": f"{key} package creepage ≥ the {need:g} mm barrier",
                                     "actual": f"{have:g} mm ({n_refs} placed): the board slot does not lengthen the "
                                               "path over the package body",
                                     "fix": "Record how it is covered in [insulation] package_path (slot, conformal "
                                            "coat, component certification)"})
    # NET-4: islands
    islands = cfg.get("islands", [])
    if islands:
        for n in sorted(x for x in nets if is_hv[x]):
            got = [c for c in nets[n]["classes"] if c in islands]
            if len(got) != 1:
                out["NET-4"].append({"net": short(n), "claim": f"{short(n)} is in exactly one island class",
                                     "actual": "in none" if not got else "in " + ", ".join(got),
                                     "fix": "Add or narrow a net-class pattern in the .kicad_pro so it lands in one "
                                            "island (fail-closed: DRC treats it as its own potential)"})
    return out, {"barriers": bars, "domains": len(members), "hv_nets": sum(is_hv.values()),
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
    if not any(hv in v["classes"] for v in nets.values()):
        return result("skip", reason=f"no net in class {hv}")
    found, info = analyse(parts, nets, cfg, db_rows())
    F, W = [], []
    loc = prj.rel(sch)
    for rule in ("INS-2", "INS-1", "NET-4", "INS-3"):
        items = found[rule]
        bucket = W if rule == "INS-3" else F
        for x in items[:SHOW]:
            bucket.append(finding(f"{rule}: {x['claim']}", x["actual"], loc, x["fix"]))
        if len(items) > SHOW:
            bucket.append(finding(f"{rule}: (more)", f"{len(items) - SHOW} more like these (forge insulation --json)", loc))
    if not info["barriers"]:
        W.append(finding("barrier parts are known", "no part is marked as a barrier, so the whole board is one domain",
                         loc, "Set Iso_Rating on isolators, isolated DC-DC and Y-caps in MC_Parts"))
    reason = (f"{info['nets']} nets, {info['hv_nets']} HV, {info['domains']} domains, "
              f"{len(info['barriers'])} barrier part(s)")
    return result("fail" if F else "warn" if W else "pass", F + W, reason=reason)


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


def rule_cases(cfg):
    """One test pair per (rule, constraint): pads just closer than the decided value must be flagged."""
    hv = cfg.get("hv_class", "HV")
    islands = set(cfg.get("islands", []))
    cases = []
    for i, r in enumerate(cfg.get("rule", [])):
        def classes(c):
            if c.startswith("!"):
                return []                                        # any net outside that class: Default
            return [hv, c] if c in islands else [c]
        for kind in ("clearance", "creepage"):
            v = r.get(f"{kind}_mm")
            if v:
                cases.append({"rule": i, "a": r["a"], "b": r["b"], "kind": kind, "need": float(v),
                              "a_cls": classes(r["a"]), "b_cls": classes(r["b"]),
                              "net_a": f"T{i}{kind[:2].upper()}_A", "net_b": f"T{i}{kind[:2].upper()}_B"})
    return cases


def test_rules(dru, pro, cfg, workdir=None, margin=0.05):
    """[(case, flagged: bool, detail)] by running KiCad's DRC on a generated board with the project's rules."""
    py, cli = kicad.find_python(), kicad.find_cli()
    if not (py and cli):
        return None
    cases = rule_cases(cfg)
    d = workdir or tempfile.mkdtemp(prefix="pe-forge-cls2-")
    stem = os.path.join(d, "cls2")
    proj = json.load(open(pro))
    ns = proj.setdefault("net_settings", {})
    known = {c["name"] for c in ns.get("classes", [])}
    pats = [p for p in ns.get("netclass_patterns") or []]
    for c in cases:                                  # each test net gets exactly the classes the case names
        for net, cls in ((c["net_a"], c["a_cls"]), (c["net_b"], c["b_cls"])):
            for k in cls:
                pats.append({"netclass": k, "pattern": net})
    ns["netclass_patterns"] = pats
    pads, y = [], 10.0
    for c in cases:
        gap = max(c["need"] - margin, 0.05)
        pads += [{"x": 10.0, "y": y, "net": c["net_a"]}, {"x": 10.0 + 1.0 + gap, "y": y, "net": c["net_b"]}]
        c["gap"] = round(gap, 3)
        y += max(30.0, 3 * c["need"])               # rows far apart: only the pair itself can interact
    spec = {"pads": pads, "w": 40.0 + max([c["need"] for c in cases] or [0]), "h": y + 10.0,
            "out": stem + ".kicad_pcb"}
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
    out = []
    for c in cases:
        hit = [x for x in v if x.get("type") == c["kind"] and
               all(any(f"[{n}]" in i.get("description", "") for i in x.get("items", [])) for n in (c["net_a"], c["net_b"]))]
        missing = [k for k in c["a_cls"] + c["b_cls"] if k not in known]
        out.append((c, bool(hit), hit[0]["description"] if hit else
                    ("class not defined in the .kicad_pro: " + ", ".join(missing) if missing else "not flagged")))
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
    res = test_rules(dru, pro, cfg)
    if res is None:
        return result("skip", reason="KiCad's Python (pcbnew) or kicad-cli not found", missing_tool=True)
    F = []
    for c, ok, detail in res:
        if not ok:
            F.append(finding(f"CLS-2: {c['a']} ↔ {c['b']} {c['kind']} ≥ {c['need']:g} mm is enforced",
                             f"copper {c['gap']:g} mm apart is not flagged ({detail})", prj.rel(dru),
                             f"Add or fix a {c['kind']} rule for this pair in the .kicad_dru"))
    return result("fail" if F else "pass", F, reason=f"{len(res)} case(s) on a generated test board")


# ------------------------------------------------------------------ CLI
def main(prj, argv):
    as_json = "--json" in argv
    if "--rules" in argv:
        r = as_check_rules(prj, {})
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
    return 1 if any(found[k] for k in ("INS-1", "INS-2", "NET-4")) else 0


def render(r):
    head = f"{r['status'].upper()}" + (f" ({r['reason']})" if r["reason"] else "")
    return "\n".join([head] + [f"  {f['claim']}: {f['actual']}" for f in r["findings"]])
