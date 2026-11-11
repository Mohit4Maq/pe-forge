"""forge models: every placed part has a 3D model (the enclosure is drawn from them; no model = unknown height).

  forge models check [--json]          every placed footprint: its (model ...) entries resolve to a file; else which
  forge models find [--apply]          for each part without one, search: the library's 3dmodels/, KiCad's stock
                                       3dshapes (by footprint name, then by package family), the project's own
                                       folder; --apply writes the model line into the MC_Lib footprint (never
                                       into KiCad's stock libraries; a write guard backs the file up)
  forge models ask                     for what is still missing: one owner decision per part (GE waits) with
                                       where to get the STEP (the manufacturer's page, Ultra Librarian, SnapEDA)
                                       and where to put it (library/3dmodels/<Lib>.3dshapes/<footprint>.step)

The rule (library gate, G6 / G7 item, GE item): a part placed on a board has a 3D model, or the owner has decided
to go without (recorded). Height, enclosure fit and the assembly drawing depend on it.
"""
import glob
import json
import os
import re

from . import config, kicad

STOCK_VARS = ("KICAD10_3DMODEL_DIR", "KICAD9_3DMODEL_DIR", "KICAD8_3DMODEL_DIR", "KISYS3DMOD")


def stock_3d():
    return kicad.stock_3dmodels()


def lib_3d():
    return os.path.join(config.LIBRARY, "3dmodels")


def resolve(path, prj_dir=None):
    """Expand ${VAR} in a model path the way KiCad does; return the file path (may not exist)."""
    p = path
    st = stock_3d()
    for v in STOCK_VARS:
        p = p.replace("${%s}" % v, st or "/__missing__")
    p = p.replace("${MC_LIB}", config.LIBRARY).replace("${KIPRJMOD}", prj_dir or ".")
    p = re.sub(r"\$\{[^}]+\}", "/__missing__", p)
    return p


def models_of(fp_text):
    return re.findall(r'\(model "([^"]+)"', fp_text)


def placed(prj):
    """[(ref, footprint, fp_file, fp_text, board_dir)] for every placed, populated part on every board."""
    from .check import roots, has_content
    from . import evidence
    out = []
    for pcb in [b for b in roots(prj, "kicad_pcb") if has_content(b)]:
        t = open(pcb, errors="ignore").read()
        for fp in re.finditer(r'\(footprint "([^"]+)"(.*?)\n\t\)', t, re.S):
            body = fp.group(2)
            ref = re.search(r'\(property "Reference" "([^"]+)"', body)
            if not ref or "(attr" in body and "dnp" in body.split("(attr", 1)[1].split(")", 1)[0]:
                continue
            ref = ref.group(1)
            if ref.startswith(("#", "H", "MH", "FID", "LOGO")):
                continue
            f = evidence.footprint_file(fp.group(1))
            out.append((ref, fp.group(1), f, open(f, errors="ignore").read() if f else body, os.path.dirname(pcb)))
    return out


def check(prj):
    rows = []
    for ref, fpname, f, text, bdir in placed(prj):
        ms = models_of(text)
        found = [resolve(m, bdir) for m in ms]
        ok = any(os.path.exists(p) for p in found)
        rows.append({"ref": ref, "footprint": fpname, "fp_file": prj.rel(f) if f and f.startswith(prj.root) else f,
                     "models": ms, "exists": ok, "stock": bool(f) and not f.startswith(config.LIBRARY),
                     "why": "" if ok else ("no (model ...) in the footprint" if not ms else "model file(s) missing: " + ", ".join(found[:2]))})
    return rows


def candidates(fpname):
    """Model files that could serve this footprint, best first."""
    lib, _, name = fpname.partition(":")
    out = []
    for base in (lib_3d(), stock_3d()):
        if not base:
            continue
        for ext in (".step", ".STEP", ".stp", ".wrl"):
            out += glob.glob(os.path.join(base, f"{lib}.3dshapes", name + ext))
            out += glob.glob(os.path.join(base, "*.3dshapes", name + ext))
    # by package family: the stock name a vendor footprint was derived from (TI_DDA0008B -> SOIC-8 PowerPAD)
    fam = re.sub(r"_LandPattern|_HV|_HandSolder|_Pad\d.*$", "", name)
    if fam != name and stock_3d():
        out += glob.glob(os.path.join(stock_3d(), "*.3dshapes", fam + "*.step"))
    return list(dict.fromkeys(out))


def find(prj, apply=False, out=print):
    from . import guard
    rows = check(prj)
    res = []
    for r in rows:
        if r["exists"]:
            continue
        cands = candidates(r["footprint"])
        if not cands:
            res.append({"ref": r["ref"], "footprint": r["footprint"], "found": None})
            continue
        c = cands[0]
        res.append({"ref": r["ref"], "footprint": r["footprint"], "found": c, "exact": os.path.splitext(os.path.basename(c))[0] == r["footprint"].split(":")[-1]})
        f = r["fp_file"]
        if apply and f and not r["stock"]:
            path = os.path.join(prj.root, f) if not os.path.isabs(f) else f
            text = open(path).read()
            rel = c.replace(stock_3d() or "\0", "${KICAD10_3DMODEL_DIR}").replace(config.LIBRARY, "${MC_LIB}")
            line = f'\t(model "{rel}"\n\t\t(offset (xyz 0 0 0))\n\t\t(scale (xyz 1 1 1))\n\t\t(rotate (xyz 0 0 0))\n\t)\n'
            new = re.sub(r"\n\)\s*$", "\n" + line + ")\n", text, count=1)
            st, _ = guard.write(None if not path.startswith(config.LIBRARY) else config.LIBRARY, path, new, replace=True)
            out(f"  {r['ref']}: {r['footprint']} <- {os.path.basename(c)} ({'exact name' if res[-1]['exact'] else 'package family: check the body size'}) written into the footprint")
        elif apply and r["stock"]:
            out(f"  {r['ref']}: {r['footprint']} is a KiCad stock footprint: its model line is missing or broken in KiCad itself; "
                f"copy the footprint into MC_Lib (mclib.py) and point it at {os.path.basename(c)}")
    return res


def sources(ref, prj):
    """Where the STEP for this part is likely to be, from its MPN / manufacturer on the schematic."""
    try:
        from . import insulation
        sch = insulation.root_sheet(prj)
        parts, _ = insulation.parse(insulation.export(sch))
        p = parts.get(ref, {})
        mpn = p.get("mpn") or ""
        row = (insulation.db_rows() or {}).get(p.get("mc_id", "")) or {}
        mfr = (row.get("Manufacturer") or "").lower()
    except Exception:
        mpn, mfr = "", ""
    if not mpn:
        return ""
    q = mpn.replace(" ", "%20")
    urls = []
    if "texas" in mfr or mpn.upper().startswith(("LM", "TPS", "UCC", "LMR", "TL", "ISO", "SN")):
        urls.append(f"ti.com/product/{mpn.rstrip('R').rstrip('T')} > Design & development > 3D model")
    urls += [f"app.ultralibrarian.com/search?queryText={q}", f"snapeda.com/search/?q={q}", f"componentsearchengine.com/search?term={q}"]
    return " | ".join(urls)


def ask(prj):
    from . import decisions
    missing = [x for x in find(prj, apply=False, out=lambda *_: None) if not x["found"]]
    have = {d["question"] for d in decisions.load(prj) if d["status"] == "OPEN"}
    made = []
    for x in missing:
        lib, _, name = x["footprint"].partition(":")
        src = sources(x["ref"], prj)
        q = (f"3D model for {x['ref']} ({name}): add library/3dmodels/{lib}.3dshapes/{name}.step"
             + (f" (search: {src})" if src else " (manufacturer STEP, Ultra Librarian or SnapEDA)") + ", or go without (height unknown)")
        if q not in have:
            made.append(decisions.new(prj, q, "GE", ["added", "go without (height unknown, say why)"], "added",
                                      "the enclosure envelope and the assembly drawing need the part's height"))
    return made, missing


def as_check(prj, opts):
    from .check import finding, result
    rows = check(prj)
    if not rows:
        return result("skip", reason="no placed parts yet")
    bad = [r for r in rows if not r["exists"]]
    W = [finding(f"{r['ref']} has a 3D model", f"{r['footprint']}: {r['why']}", r["fp_file"] or "",
                 "forge models find --apply, else forge models ask") for r in bad]
    return result("warn" if W else "pass", W, reason=f"{len(rows) - len(bad)}/{len(rows)} parts with a model")


def gate_items(prj):
    from . import decisions
    rows = check(prj)
    bad = [r["ref"] for r in rows if not r["exists"]]
    waived = {d["question"].split(" for ", 1)[1].split(" (")[0] for d in decisions.load(prj)
              if d["status"] == "ANSWERED" and d["question"].startswith("3D model for") and "without" in d["answer"].lower()}
    left = [b for b in bad if b not in waived]
    return [(not left, f"3D models: {len(rows) - len(bad)}/{len(rows)} parts" + (f"; missing {', '.join(left[:6])} (forge models find --apply | ask)" if left else "")
             + (f"; {len(waived)} waived by the owner" if waived else ""))]


def main(prj, argv):
    if argv[:1] == ["check"]:
        rows = check(prj)
        if "--json" in argv:
            print(json.dumps(rows, indent=1))
        else:
            for r in rows:
                print(("OK   " if r["exists"] else "NONE ") + f"{r['ref']:6s} {r['footprint']}" + ("" if r["exists"] else f"  <- {r['why']}"))
            print(f"{sum(r['exists'] for r in rows)}/{len(rows)} with a model; stock 3D: {stock_3d() or 'not found'}; library 3D: {lib_3d()}")
        return 0 if all(r["exists"] for r in rows) else 1
    if argv[:1] == ["find"]:
        res = find(prj, "--apply" in argv)
        for x in res:
            print(f"  {x['ref']}: " + (f"candidate {x['found']}" + ("" if x.get("exact") else " (package family, verify the body)") if x["found"] else "nothing found: forge models ask"))
        return 0
    if argv[:1] == ["ask"]:
        made, missing = ask(prj)
        print(f"{len(missing)} part(s) without a model; {len(made)} decision(s) opened for the owner (forge decision list --open)")
        return 0
    print(__doc__)
    return 1
