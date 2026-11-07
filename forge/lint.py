"""Legibility lint for any KiCad 10 sheet, hand drawn or engine drawn, with an advisory score.

  forge schdraw lint <sheet.kicad_sch> [--json]

The geometry is rebuilt from the saved sheet (symbols with their bodies, wires, junctions, labels, power symbols,
no-connects, text) and checked with every pe-forge style rule that needs no drawing spec (S1 to S9, S12 to S16; see
skills/pe-schematic/references/style-guide.md), plus five families from copperhead's legibility checker:
  L1 out-of-frame       a symbol body outside the drawing area (frame margin or title block)
  L2 empty-title-block  no title, or an unset text variable in it on a project sheet
  L3 low-utilization    the drawing uses under 12 % of the sheet (a smaller paper size reads better; advice only)
  L4 crowding           two symbol bodies overlap or sit closer than 1.27 mm
  L5 label-orientation  a label on a horizontal wire end turned vertically
Score 0 to 100, deterministic. Who owns the sheet decides what a finding means: a sheet that `forge schdraw apply`
wrote and nobody has saved since is engine-owned and its findings are errors (exit 1); once a person saves it in
KiCad it is user-owned and the findings are advice (exit 0). Ownership is kept in .forge/schdraw_owned.json
(sheet path -> SHA-256 of the content schdraw wrote), so nothing is added to the drawing itself.
"""
import collections
import hashlib
import json
import os
import re
import tempfile

from . import kicad
from . import schdraw as sd

sp = sd.sp
WEIGHT = {"S1": 2, "S2": 4, "S3": 4, "S4": 3, "S5": 4, "S6": 2, "S7": 3, "S8": 1, "S9": 2, "S12": 2, "S13": 3,
          "S14": 2, "S15": 2, "S16": 3, "L1": 4, "L2": 2, "L3": 5, "L4": 3, "L5": 1}
FAMILY_CAP = 20
ADVICE_ONLY = ("L3", "S8")      # never blocking, even on an engine-owned sheet
PAPER = {"A4": (297, 210), "A3": (420, 297), "A2": (594, 420), "A1": (841, 594), "A0": (1189, 841),
         "A": (279.4, 215.9), "B": (431.8, 279.4), "C": (558.8, 431.8), "D": (863.6, 558.8)}


# ------------------------------------------------------------------ ownership
def _owned_file(sheet):
    from . import config
    root = config.find_root(os.path.dirname(os.path.abspath(sheet)))
    base = os.path.join(root, ".forge") if root else os.path.join(sp.SCRATCH)
    return os.path.join(base, "schdraw_owned.json"), root


def _key(sheet, root):
    return os.path.relpath(os.path.realpath(sheet), os.path.realpath(root)) if root else os.path.realpath(sheet)


def stamp(sheet):
    """Record that schdraw wrote this exact content (called by schdraw apply)."""
    f, root = _owned_file(sheet)
    try:
        d = json.load(open(f))
    except (OSError, ValueError):
        d = {}
    d[_key(sheet, root)] = hashlib.sha256(open(sheet, "rb").read()).hexdigest()
    os.makedirs(os.path.dirname(f), exist_ok=True)
    json.dump(d, open(f, "w"), indent=1)


def engine_owned(sheet):
    f, root = _owned_file(sheet)
    try:
        h = json.load(open(f)).get(_key(sheet, root))
    except (OSError, ValueError):
        return False
    return bool(h) and h == hashlib.sha256(open(sheet, "rb").read()).hexdigest()


# ------------------------------------------------------------------ geometry from a saved sheet
def geometry(text):
    sh = sd.Sheet(text, [])
    segs = []
    for m in re.finditer(r"\n\t\(wire\n", text):
        b = sp.block_at(text, m.start() + 2)
        pts = [(sd.r2(float(a)), sd.r2(float(c))) for a, c in re.findall(r"\(xy ([-\d.]+) ([-\d.]+)\)", b)]
        if len(pts) >= 2:
            segs.append(pts)
    labels = []
    for kind in ("label", "global_label", "hierarchical_label"):
        for m in re.finditer(r"\n\t\(" + kind + r' "([^"]*)"', text):
            b = sp.block_at(text, m.start() + 2)
            a = re.search(r"\(at ([-\d.]+) ([-\d.]+) ([-\d.]+)\)", b)
            labels.append({"net": m.group(1), "x": sd.r2(float(a.group(1))), "y": sd.r2(float(a.group(2))),
                           "rot": int(float(a.group(3))), "kind": kind})
    powers = []
    for m in re.finditer(r'\n\t\(symbol\n\t\t\(lib_id "(power:[^"]+)"\)\n\t\t\(at ([-\d.]+) ([-\d.]+) ([-\d.]+)\)', text):
        b = sp.block_at(text, m.start() + 2)
        val = re.search(r'\(property "Value" "([^"]*)"', b)
        net, rot = (val.group(1) if val else m.group(1)[6:]), int(float(m.group(4)))
        flag = m.group(1) == "power:PWR_FLAG"
        gnd = bool(sd.GROUND.search(net)) or "GND" in m.group(1) or "Earth" in m.group(1)
        d = {0: "D" if gnd else "U", 180: "U" if gnd else "D", 270: "R", 90: "L"}.get(rot % 360, "U")
        powers.append({"net": net, "x": sd.r2(float(m.group(2))), "y": sd.r2(float(m.group(3))), "dir": d, "flag": flag})
    ncs = [(sd.r2(float(a)), sd.r2(float(c))) for a, c in
           re.findall(r"\n\t\(no_connect\n\t\t\(at ([-\d.]+) ([-\d.]+)\)", text)]
    junc = {(sd.r2(float(a)), sd.r2(float(c))) for a, c in
            re.findall(r"\n\t\(junction\n\t\t\(at ([-\d.]+) ([-\d.]+)\)", text)}
    # name each wire by its connected group (same group = same net), so S8 / S9 compare different nets only
    parent = list(range(len(segs)))

    def f(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    ends = collections.defaultdict(list)
    for i, pts in enumerate(segs):
        for p in (pts[0], pts[-1]):
            ends[p].append(i)
    for i, pts in enumerate(segs):
        for p in (pts[0], pts[-1]):
            for j in ends[p]:
                parent[f(i)] = f(j)
        for j, other in enumerate(segs):       # a wire ending on another wire's middle joins it at a junction
            if j != i and any(q in junc and sd.on_seg(q, a, b) for q in (pts[0], pts[-1]) for a, b in zip(other, other[1:])):
                parent[f(i)] = f(j)
    name = {}
    for l in labels + [p for p in powers if not p["flag"]]:
        for i, pts in enumerate(segs):
            if (l["x"], l["y"]) in (pts[0], pts[-1]):
                name.setdefault(f(i), l["net"])
    wires = [(name.get(f(i), f"~net{f(i)}"), pts) for i, pts in enumerate(segs)]
    return sh, wires, labels, powers, ncs


def paper_size(text):
    m = re.search(r'\(paper "([^"]+)"(?: ([\d.]+) ([\d.]+))?', text)
    if not m:
        return PAPER["A3"]
    if m.group(1) == "User" and m.group(2):
        return float(m.group(2)), float(m.group(3))
    w, h = PAPER.get(m.group(1), PAPER["A3"])
    return (h, w) if "portrait" in text[m.start():m.start() + 80] else (w, h)


def families(text, sh, wires, labels, powers, sheet):
    out = []
    off = sorted({k.split(".")[0] for k, (x, y) in sh.pin_xy.items() if not (sd.on_grid(x) and sd.on_grid(y))})
    out += [f"S1 {ref} is off the 1.27 mm grid (its pins do not land on grid points)" for ref in off if not ref.startswith("#")]
    pw, ph = paper_size(text)
    for ref, b in sh.boxes.items():
        if b[0] < 12.7 or b[1] < 12.7 or b[2] > pw - 12.7 or b[3] > ph - 12.7 or (b[2] > pw - 122 and b[3] > ph - 45):
            out.append(f"L1 {ref} body is outside the drawing area (frame margin or title block)")
    tb = re.search(r'\(title_block.*?\(title "([^"]*)"', text, re.S)
    title = tb.group(1).strip() if tb else ""
    has_pro = os.path.exists(os.path.splitext(sheet)[0] + ".kicad_pro")
    if not title or (title.startswith("${") and not has_pro):
        out.append("L2 empty title block: give the sheet a title (Page Settings)")
    xs = [v for b in sh.boxes.values() for v in (b[0], b[2])] + [p[0] for _, pts in wires for p in pts]
    ys = [v for b in sh.boxes.values() for v in (b[1], b[3])] + [p[1] for _, pts in wires for p in pts]
    if xs and ys:
        area = (max(xs) - min(xs)) * (max(ys) - min(ys))
        if area < 0.12 * (pw - 25.4) * (ph - 25.4):
            out.append(f"L3 low utilization: the drawing covers {100 * area / ((pw - 25.4) * (ph - 25.4)):.0f} % of the "
                       "sheet; use a smaller paper size or spread the groups")
    boxes = sorted(sh.boxes.items())
    for i, (r1, b1) in enumerate(boxes):
        for r2_, b2 in boxes[i + 1:]:
            if sd.overlap(b1, b2, -1.27 + 0.01):
                out.append(f"L4 {r1} and {r2_} are crowded (bodies overlap or closer than 1.27 mm)")
    for l in labels:
        horiz = any((l["x"], l["y"]) in (a, b) and a[1] == b[1] for _, a, b in sd.segments(wires))
        if horiz and l["rot"] in (90, 270):
            out.append(f"L5 label {l['net']} at ({l['x']}, {l['y']}) is vertical on a horizontal wire")
    return out


def score(findings):
    per = collections.Counter()
    for f in findings:
        fam = f.split()[0]
        per[fam] = min(FAMILY_CAP, per[fam] + WEIGHT.get(fam, 2))
    return max(0, 100 - sum(per.values()))


def lint(sheet, render_text=True):
    text = open(sheet).read()
    sh, wires, labels, powers, ncs = geometry(text)
    E, W, _ = sd.style_check({}, sh, wires, labels, powers, ncs, {})
    F = E + W
    F += families(text, sh, wires, labels, powers, sheet)
    if render_text and kicad.find_cli():
        try:
            te, tw = sd.text_check(text, sheet, sh, wires, tempfile.mkdtemp(prefix="forge-lint-"))
            F += te + tw
        except ImportError:
            F.append("S13 not checked: PyMuPDF missing (text placement checks need it)")
    F = list(dict.fromkeys(F))
    owned = engine_owned(sheet)
    fam = collections.Counter(f.split()[0] for f in F)
    errors = [f for f in F if f.split()[0] not in ADVICE_ONLY]
    return {"sheet": sheet, "owner": "engine" if owned else "user", "score": score(F), "findings": F,
            "errors": errors, "families": dict(fam), "top": F[:5], "blocking": owned and bool(errors)}


def main(argv):
    if not argv:
        print(__doc__)
        return 1
    r = lint(os.path.abspath(argv[0]))
    if "--json" in argv:
        print(json.dumps(r, indent=1))
    else:
        print(f"{os.path.basename(r['sheet'])}: legibility score {r['score']}/100 · {len(r['findings'])} finding(s) · "
              f"{r['owner']}-owned ({'findings block' if r['owner'] == 'engine' else 'advice only'})")
        for f in r["findings"]:
            print(("  ERROR " if r["owner"] == "engine" and f in r["errors"] else "  advice ") + f)
    return 1 if r["blocking"] else 0
