"""Fab release gate: "can I order this board today?", answered without a model. `forge check --fab [--strict]`.

  forge fab manifest [<kicad project dir>]   write release/<REV>/manifest.json (release.sh runs it after the jobset)

Parts (each pass | warn | fail | skip):
  routing           DRC: no unconnected items
  sch_pcb_match     DRC schematic parity is empty, and every schematic part is on the board with the same footprint
  bom_ready         every placed part has MPN, manufacturer, an alternate, and library status Active (Seed, Obsolete or
                    blank fail; NRND warns, fails with --strict)
  outputs_fresh     the release manifest's SHA-256 of the board, every sheet and the BoM still match (content, not mtime)
  outputs_complete  Gerbers for every copper layer + mask, paste, silkscreen, edge cuts; drill file; the pick-and-place
                    file lists every placed SMD part
  power_rules       pcb_check: no PCB-PWRTRACE / PCB-THERMAL findings; an HV net class exists when any net is HV-named
  docs              Release_Notes.md, 13_Validation/DVP_and_R.md and 04_Hardware/Layout/Layout_Spec.md are filled in
Gate G8 runs this with --strict.
"""
import csv
import datetime as dt
import fnmatch
import glob
import hashlib
import json
import os
import re

from . import kicad, tables

PARTS = ["routing", "sch_pcb_match", "bom_ready", "outputs_fresh", "outputs_complete", "power_rules", "docs"]
HV_PATTERNS = ["HV*", "*_HV*"]


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 16), b""):
            h.update(b)
    return h.hexdigest()


def revision(kdir):
    for pro in glob.glob(os.path.join(kdir, "*.kicad_pro")):
        try:
            return (json.load(open(pro)).get("text_variables", {}).get("REVISION") or "A").strip() or "A"
        except ValueError:
            pass
    return "A"


def sources(kdir, prj=None):
    name = os.path.splitext(os.path.basename(glob.glob(os.path.join(kdir, "*.kicad_pro"))[0]))[0] \
        if glob.glob(os.path.join(kdir, "*.kicad_pro")) else os.path.basename(kdir)
    out = {}
    for f in [os.path.join(kdir, name + ".kicad_pcb")] + sorted(glob.glob(os.path.join(kdir, "*.kicad_sch"))) \
            + sorted(glob.glob(os.path.join(kdir, "release", "bom", "*.csv"))):
        if os.path.exists(f):
            out[os.path.relpath(f, kdir)] = sha(f)
    if prj and os.path.exists(prj.p("bom_csv")):
        out["@project/" + prj.paths["bom_csv"]] = sha(prj.p("bom_csv"))
    return out


def _resolve(kdir, rel, prj):
    return os.path.join(prj.root, rel[len("@project/"):]) if rel.startswith("@project/") and prj else os.path.join(kdir, rel)


def write_manifest(kdir, prj=None):
    rev = revision(kdir)
    rdir = os.path.join(kdir, "release", rev)
    os.makedirs(rdir, exist_ok=True)
    outs = {}
    for dp, _, fn in os.walk(rdir):
        for f in fn:
            p = os.path.join(dp, f)
            if f != "manifest.json":
                outs[os.path.relpath(p, kdir)] = sha(p)
    for z in glob.glob(os.path.join(kdir, "release", f"*-{rev}-fab.zip")):
        outs[os.path.relpath(z, kdir)] = sha(z)
    m = {"rev": rev, "created": dt.datetime.now().isoformat(timespec="seconds"), "sources": sources(kdir, prj),
         "outputs": outs}
    p = os.path.join(rdir, "manifest.json")
    json.dump(m, open(p, "w"), indent=1)
    return p, m


def latest_manifest(kdir):
    ms = []
    for p in glob.glob(os.path.join(kdir, "release", "*", "manifest.json")):
        try:
            ms.append((json.load(open(p)).get("created", ""), p))
        except ValueError:
            pass
    return max(ms)[1] if ms else None


# ------------------------------------------------------------------ board reading
def footprints(pcb_text):
    out = {}
    for m in re.finditer(r"\n\t\(footprint ", pcb_text):
        b = kicad.block_at(pcb_text, m.start() + 2)
        ref = re.search(r'\(property "Reference" "([^"]*)"', b)
        lib = re.match(r'\(footprint "([^"]+)"', b)
        attr = re.search(r"\n\t\t\(attr ([^)]*)\)", b)
        layer = re.search(r'\n\t\t\(layer "([^"]+)"\)', b)
        if ref:
            a = attr.group(1).split() if attr else []
            out[ref.group(1)] = {"lib": lib.group(1) if lib else "", "attr": a, "dnp": "dnp" in a or "(dnp yes)" in b,
                                 "bottom": bool(layer and layer.group(1).startswith("B.")),
                                 "smd": "smd" in a, "no_pos": "exclude_from_pos_files" in a, "board_only": "board_only" in a}
    return out


def copper_layers(pcb_text):
    m = re.search(r"\n\t\(layers\n(.*?)\n\t\)", pcb_text, re.S)
    return [n for n, kind in re.findall(r'\(\d+ "([^"]+)" (signal|power|mixed|jumper)', m.group(1) if m else "")]


def _part(status, findings=(), reason=""):
    return {"status": status, "findings": list(findings), "reason": reason}


def _f(claim, actual, location=""):
    return {"claim": claim, "actual": actual, "location": location}


# ------------------------------------------------------------------ parts
def p_routing(ctx):
    rep = ctx["drc"]
    if not rep.tool_ok:
        return _part("fail", [_f("DRC runs", rep.error, ctx["rel_pcb"])])
    F = [_f("every connection routed", v["description"], ctx["rel_pcb"]) for v in rep.unconnected]
    return _part("fail" if F else "pass", F, f"{len(rep.unconnected)} unconnected")


def p_match(ctx):
    rep, F = ctx["drc"], []
    if rep.tool_ok:
        F += [_f("schematic and board agree", v["description"], ctx["rel_pcb"]) for v in rep.parity]
    comps, fps = ctx["comps"], ctx["fps"]
    if comps is None:
        return _part("skip", reason="schematic could not be read (kicad-cli)")
    for ref, c in sorted(comps.items()):
        if ref.startswith("#") or c.get("board_excluded"):
            continue
        fp = fps.get(ref)
        if not fp:
            F.append(_f(f"{ref} is on the board", f"{ref} is in the schematic only", ctx["rel_pcb"]))
        elif c["footprint"] and not tables.same_footprint(c["footprint"], fp["lib"]):
            F.append(_f(f"{ref} footprint {c['footprint']}", f"board has {fp['lib']}", ctx["rel_pcb"]))
    for ref, fp in sorted(fps.items()):
        if ref not in comps and not fp["board_only"] and not ref.startswith(("#", "REF**")):
            F.append(_f(f"{ref} is in the schematic", f"{ref} is on the board only", ctx["rel_pcb"]))
    return _part("fail" if F else "pass", F)


def p_bom(ctx):
    comps = ctx["comps"]
    if comps is None:
        return _part("skip", reason="schematic could not be read (kicad-cli)")
    wip = {}
    if os.path.exists(ctx["prj"].p("bom_csv")):
        for r in tables.read_csv(ctx["prj"].p("bom_csv")):
            for ref in tables.refs(r.get("ref", "")):
                wip[ref] = r
    from .evidence import rows_by_id
    db = rows_by_id() or {}
    F, W = [], []
    for ref, c in sorted(comps.items()):
        if ref.startswith("#") or not c["in_bom"] or c["dnp"]:
            continue
        row = db.get(c["fields"].get("MC_ID", ""))
        # the parts database is authoritative; the placed symbol's fields are a snapshot from placement time
        fl = dict(c["fields"], **{k: row[k] for k in ("MPN", "Manufacturer", "Status", "Alt_MPN") if row and row.get(k)}) \
            if row else c["fields"]
        loc = os.path.basename(c.get("sheetfile") or "")
        for k in ("MPN", "Manufacturer"):
            if not (fl.get(k) or (wip.get(ref, {}).get(k) or "")).strip():
                F.append(_f(f"{ref} has a {k}", "blank", loc))
        st = (fl.get("Status") or "").strip()
        if st in ("Seed", "Obsolete", "EOL"):
            F.append(_f(f"{ref} ({fl.get('MC_ID') or c['value']}) library status Active", f"status {st}", loc))
        elif st == "NRND":
            W.append(_f(f"{ref} library status Active", "status NRND (not recommended for new designs)", loc))
        elif not st:
            W.append(_f(f"{ref} library status Active", "no status: part not reviewed", loc))
        elif st == "Active" and row is not None and not (row.get("Verified_Against") or "").strip():
            F.append(_f(f"{ref} ({row['ID']}) checked against its datasheet", "Active but no datasheet evidence recorded "
                        "(forge evidence todo)", loc))
        alt = (fl.get("Alt_MPN") or (wip.get(ref, {}).get("Alt MPN") or "")).strip()
        if not alt:
            F.append(_f(f"{ref} has an alternate (Rule 3)", "no Alt_MPN in the symbol or the WIP BoM", loc))
    if ctx["strict"]:
        F, W = F + W, []
    return _part("fail" if F else "warn" if W else "pass", F + W)


def p_fresh(ctx):
    man = latest_manifest(ctx["kdir"])
    if not man:
        return _part("fail", [_f("a release manifest exists", "none: run ./release.sh", ctx["rel_kdir"])])
    m = json.load(open(man))
    F = []
    now = sources(ctx["kdir"], ctx["prj"])
    for rel, h in m.get("sources", {}).items():
        if rel not in now:
            F.append(_f(f"{rel} unchanged since release {m['rev']}", "file is gone; re-run ./release.sh", rel))
        elif now[rel] != h:
            F.append(_f(f"{rel} unchanged since release {m['rev']}", "content changed; re-run ./release.sh", rel))
    for rel in now:
        if rel not in m.get("sources", {}) and rel.endswith((".kicad_sch", ".kicad_pcb")):
            F.append(_f(f"{rel} is in release {m['rev']}", "new file since the release; re-run ./release.sh", rel))
    for rel, h in m.get("outputs", {}).items():
        p = _resolve(ctx["kdir"], rel, ctx["prj"])
        if not os.path.exists(p) or sha(p) != h:
            F.append(_f(f"output {rel} as released", "missing or changed; re-run ./release.sh", rel))
    return _part("fail" if F else "pass", F, f"release {m['rev']} of {m['created']}")


def p_complete(ctx):
    man = latest_manifest(ctx["kdir"])
    if not man:
        return _part("fail", [_f("release outputs exist", "no release yet: run ./release.sh", ctx["rel_kdir"])])
    rdir = os.path.dirname(man)
    files = [os.path.relpath(os.path.join(dp, f), rdir) for dp, _, fn in os.walk(rdir) for f in fn]
    names = [os.path.basename(f) for f in files]
    has = lambda layer: any(f"-{layer.replace('.', '_')}." in n for n in names)
    F, W = [], []
    need = copper_layers(ctx["pcb_text"]) + ["F.Mask", "B.Mask", "F.Paste", "F.Silkscreen", "Edge.Cuts"]
    if any(fp["smd"] and fp["bottom"] and not fp["dnp"] for fp in ctx["fps"].values()):
        need.append("B.Paste")
    for layer in need:
        if not has(layer):
            F.append(_f(f"Gerber for {layer}", "missing", prj_rel(ctx, rdir)))
    if not has("B.Silkscreen"):
        W.append(_f("Gerber for B.Silkscreen", "missing (fine if the bottom has no markings)", prj_rel(ctx, rdir)))
    if not any(n.lower().endswith((".drl", ".xln", ".exc")) for n in names):
        F.append(_f("a drill file", "missing", prj_rel(ctx, rdir)))
    pos = [f for f in files if re.search(r"pos|cpl|centroid", os.path.basename(f), re.I) and f.lower().endswith((".csv", ".pos"))]
    want = {r for r, fp in ctx["fps"].items() if fp["smd"] and not fp["dnp"] and not fp["no_pos"] and not fp["board_only"]}
    if not pos:
        F.append(_f("a pick-and-place file", "missing", prj_rel(ctx, rdir)))
    else:
        got = set()
        for p in pos:
            rows = list(csv.reader(open(os.path.join(rdir, p), newline="", errors="ignore")))
            if rows:
                i = next((k for k, h in enumerate(rows[0]) if h.strip().lower() in ("ref", "designator")), 0)
                got |= {r[i].strip().strip('"') for r in rows[1:] if len(r) > i}
        miss = sorted(want - got)
        if miss:
            F.append(_f(f"pick-and-place lists all {len(want)} placed SMD parts", "missing " + ", ".join(miss[:12]),
                        prj_rel(ctx, rdir)))
    return _part("fail" if F else "warn" if W else "pass", F + W)


def prj_rel(ctx, p):
    return ctx["prj"].rel(p)


def p_power(ctx):
    from . import check
    F, W = [], []
    data, skip = check._tool(ctx["prj"], "pcb_check.py", ctx["pcb"], "pcb_check could not run (needs KiCad's pcbnew)")
    if skip:
        W.append(_f("power-layout checks ran", skip["reason"], ctx["rel_pcb"]))
    else:
        for i in data.get("items", []):
            if i["code"] in ("PCB-PWRTRACE", "PCB-THERMAL"):
                F.append(_f(i["code"], i["message"], ctx["rel_pcb"]))
    nets = set(re.findall(r'\(net "([^"]+)"\)', ctx["pcb_text"])) | set(re.findall(r'\(net \d+ "([^"]+)"\)', ctx["pcb_text"]))
    hv = sorted(n for n in nets if any(fnmatch.fnmatch(n.split("/")[-1], p) for p in HV_PATTERNS))
    if hv:
        classes = set()
        for pro in glob.glob(os.path.join(ctx["kdir"], "*.kicad_pro")):
            try:
                classes |= {c.get("name") for c in json.load(open(pro)).get("net_settings", {}).get("classes", [])}
            except ValueError:
                pass
        if "HV" not in classes:
            F.append(_f("an HV net class for HV nets", f"no HV class; HV nets {', '.join(hv[:6])}", ctx["rel_pcb"]))
    if ctx["strict"] and W:
        F, W = F + W, []
    return _part("fail" if F else "warn" if W else "pass", F + W)


def p_docs(ctx):
    from .gates import filled
    prj = ctx["prj"]
    notes = sorted(glob.glob(prj.p("release", "*", "Release_Notes.md")))
    want = [("Release_Notes.md", notes[-1] if notes else prj.p("release", "<REV>", "Release_Notes.md")),
            ("DVP_and_R.md", prj.p("validation", "DVP_and_R.md")),
            ("Layout_Spec.md", prj.p("hardware", "Layout", "Layout_Spec.md"))]
    F = []
    for name, p in want:
        if not os.path.exists(p):
            F.append(_f(f"{name} exists", "missing", prj.rel(p)))
        elif not filled(p):
            F.append(_f(f"{name} is filled in", "template placeholders or under 8 lines", prj.rel(p)))
    return _part("fail" if F else "pass", F)


FUNCS = {"routing": p_routing, "sch_pcb_match": p_match, "bom_ready": p_bom, "outputs_fresh": p_fresh,
         "outputs_complete": p_complete, "power_rules": p_power, "docs": p_docs}


def boards(prj):
    from .check import roots, has_content
    return [f for f in roots(prj, "kicad_pcb") if has_content(f)]


def run(prj, strict=False):
    """{part: result} for the first board with content (multi-board projects: one result per board, merged)."""
    out = {}
    bs = boards(prj)
    if not bs:
        return None, "no board yet"
    for pcb in bs:
        kdir = os.path.dirname(pcb)
        sch = os.path.splitext(pcb)[0] + ".kicad_sch"
        d = kicad.design(sch) if os.path.exists(sch) else None
        ctx = {"prj": prj, "pcb": pcb, "kdir": kdir, "rel_pcb": prj.rel(pcb), "rel_kdir": prj.rel(kdir),
               "pcb_text": open(pcb, errors="ignore").read(), "drc": kicad.drc(pcb), "strict": strict,
               "comps": d[0] if d else None}
        ctx["fps"] = footprints(ctx["pcb_text"])
        for name in PARTS:
            r = FUNCS[name](ctx)
            if name in out:       # merge boards: worst status, all findings
                order = ["pass", "skip", "warn", "fail"]
                out[name]["status"] = max(out[name]["status"], r["status"], key=order.index)
                out[name]["findings"] += r["findings"]
            else:
                out[name] = r
    return out, ""


def as_check(prj, opts):
    from .check import result
    parts, why = run(prj, opts.get("strict", False))
    if parts is None:
        return result("skip", reason=why)
    order = ["pass", "skip", "warn", "fail"]
    worst = max((p["status"] for p in parts.values()), key=order.index)
    findings = [dict(f, part=n) for n, p in parts.items() for f in p["findings"]]
    r = result("pass" if worst == "skip" else worst, findings,
               reason=", ".join(f"{n} {p['status']}" for n, p in parts.items()))
    r["parts"] = parts
    return r


def main(prj, argv):
    if argv[:1] == ["manifest"]:
        kdir = os.path.abspath(argv[1]) if len(argv) > 1 else None
        if not kdir:
            bs = boards(prj) if prj else []
            if not bs:
                raise SystemExit("forge fab manifest <kicad project dir>")
            kdir = os.path.dirname(bs[0])
        p, m = write_manifest(kdir, prj)
        print(f"wrote {p}: {len(m['sources'])} source(s), {len(m['outputs'])} output(s), rev {m['rev']}")
        return 0
    print(__doc__)
    return 1
