"""Agent schematic drawing (draw mode "agent"): parts, real wires, junctions, labels and power symbols on a
KiCad 10 sheet, from a drawing spec written by the pe-schematic-drafter agent from the sheet brief.

  forge schdraw check  <spec.json>            style + connectivity checks, render PNG, ERC on a dry copy
  forge schdraw apply  <spec.json>            same, then back up and write the sheet (needs a clean dry run)
  forge schdraw pins   <MC_ID> X Y ROT        where a part's pins land (for planning the layout)
  forge schdraw new    <file.kicad_sch> [--paper A3] [--title T]   empty root sheet from the template

Spec (JSON, coordinates in mm, sheet y down, 1.27 mm grid):
{
  "sheet": "08_PCB/buck/buck.kicad_sch",
  "netlist": {"VIN": ["J1.1", "C1.1", "U1.3"], "GND": [...], ...},   # from the sheet brief: the contract
  "net_class": {"VIN": "power", "SW": "hv", "FB": "signal"},          # optional, for zone / spacing rules
  "zones": [{"name": "HV", "rect": [x1, y1, x2, y2], "nets": ["SW", "VBUS"]}],            # optional
  "parts":  [{"id": "IC-0001", "ref": "U1", "x": 101.6, "y": 76.2, "rot": 0}, ...],         # as kicad-place
  "connect": [{"net": "VIN", "path": ["J1.1", "C1.1", "U1.3"], "route": "hv"},             # pin to pin
              {"net": "SW", "path": ["U1.5", [120.65, 76.2], "L1.1"]}],                    # via a point
  "stubs":  [{"pin": "U1.7", "label": "FB", "dir": "R", "len": 5.08, "kind": "local"},     # pin -> label
             {"pin": "C1.2", "power": "GND", "len": 2.54}],                                 # pin -> power symbol
  "wires":  [{"net": "X", "pts": [[x, y], [x, y], ...]}],                                   # raw polylines
  "labels": [{"net": "EN", "x": 0, "y": 0, "rot": 0, "kind": "global", "shape": "input"}],
  "no_connect": ["U1.4"],
  "text": [{"t": "HV ZONE: 800 V", "x": 30, "y": 20}]
}
"route": "hv" = horizontal first, "vh" = vertical first, between each consecutive pair of path points.

Style rules (strict; any ERROR blocks apply): see skills/pe-schematic/references/style-guide.md.
"""
import collections
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "skills", "kicad-place", "scripts"))
import sch_place as sp  # noqa: E402

GRID = 1.27
U = lambda: str(uuid.uuid4())
SUPPLY_UP = re.compile(r"^(\+|VCC|VDD|V[A-Z0-9_]*\+|VBUS|VIN)", re.I)
GROUND = re.compile(r"GND|VSS|PE$|EARTH|COM$", re.I)


def r2(v):
    return round(v + 0.0, 2)


def on_grid(v):
    return abs(v / GRID - round(v / GRID)) < 1e-3


# ------------------------------------------------------------------ geometry of symbols
def graphics_bbox(block, unit=1):
    xs, ys = [], []
    for m in re.finditer(r'\(symbol "[^"]*_(\d+)_\d+"', block):
        if int(m.group(1)) not in (0, unit):
            continue
        sub = sp.block_at(block, m.start())
        sub = re.sub(r"\(pin .*?\n\t*\)", "", sub, flags=re.S)
        for a, b in re.findall(r"\((?:start|end|xy|center|mid) ([-\d.]+) ([-\d.]+)\)", sub):
            xs.append(float(a)); ys.append(float(b))
        for cx, cy, rad in re.findall(r"\(center ([-\d.]+) ([-\d.]+)\)\s*\(radius ([-\d.]+)\)", sub):
            xs += [float(cx) - float(rad), float(cx) + float(rad)]; ys += [float(cy) - float(rad), float(cy) + float(rad)]
    return (min(xs), min(ys), max(xs), max(ys)) if xs else None


def sheet_bbox(lb, x, y, rot, mir):
    if not lb:
        return None
    pts = [sp.xform(px, py, x, y, rot, mir) for px in (lb[0], lb[2]) for py in (lb[1], lb[3])]
    return (min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts))


class Sheet:
    """Parts on the sheet (existing + new) with pin coordinates and body boxes."""

    def __init__(self, text, parts):
        self.pins, self.boxes, self.pin_xy, self.libs = collections.defaultdict(list), {}, {}, {}
        syms, libs = sp.existing(text)
        self.libs.update(libs)
        for ref, lib, x, y, rot, mir, un in syms:
            if lib in libs:
                self._add(ref, libs[lib], x, y, rot, mir, un, power=ref.startswith("#"))
        self.rows = {}
        for p in parts:
            t, r = sp.row(p["id"])
            key = f"MC_Parts:{t}/{p['id']}"
            if key not in self.libs:
                self.libs[key] = sp.lib_symbol(p["id"])
            self.rows[p["ref"]] = (t, r, key)
            self._add(p["ref"], self.libs[key], p["x"], p["y"], p.get("rot", 0), p.get("mirror"), p.get("unit", 1))

    def _add(self, ref, blk, x, y, rot, mir, unit, power=False):
        for n, nm, px, py in sp.lib_pins(blk, unit):
            xy = sp.xform(px, py, x, y, rot, mir)
            self.pin_xy[f"{ref}.{n}"] = xy
            self.pins[xy].append(f"{ref}.{n}")
        if not power:
            b = sheet_bbox(graphics_bbox(blk, unit), x, y, rot, mir)
            if b:
                self.boxes[ref] = b

    def at(self, item):
        if isinstance(item, (list, tuple)):
            return (r2(item[0]), r2(item[1]))
        if item not in self.pin_xy:
            raise SystemExit(f"unknown pin {item} (check the ref and pin number; `forge schdraw pins` lists them)")
        return self.pin_xy[item]


# ------------------------------------------------------------------ spec -> primitives
def route(a, b, how):
    if a[0] == b[0] or a[1] == b[1]:
        return [a, b]
    c = (b[0], a[1]) if how == "hv" else (a[0], b[1])
    return [a, c, b]


DIRS = {"R": (1, 0, 0), "L": (-1, 0, 180), "U": (0, -1, 90), "D": (0, 1, 270)}


def expand(spec, sh):
    wires, labels, powers, ncs = [], [], [], []
    for w in spec.get("wires", []):
        wires.append((w["net"], [tuple(map(r2, p)) for p in w["pts"]]))
    for c in spec.get("connect", []):
        pts = [sh.at(x) for x in c["path"]]
        poly = [pts[0]]
        for a, b in zip(pts, pts[1:]):
            poly += route(a, b, c.get("route", "hv"))[1:]
        wires.append((c["net"], poly))
    for s in spec.get("stubs", []):
        a = sh.at(s["pin"])
        net = s.get("label") or s.get("power")
        d = s.get("dir") or ("D" if s.get("power") and GROUND.search(net) else "U" if s.get("power") else "R")
        dx, dy, rot = DIRS[d]
        L = s.get("len", 2.54)
        b = (r2(a[0] + dx * L), r2(a[1] + dy * L))
        wires.append((net, [a, b]))
        if s.get("power"):
            powers.append({"net": net, "x": b[0], "y": b[1], "rot": 0 if d in ("U", "D") else (90 if d == "R" else 270),
                           "dir": d})
        else:
            labels.append({"net": net, "x": b[0], "y": b[1], "rot": rot, "kind": s.get("kind", "local"),
                           "shape": s.get("shape", "bidirectional")})
    labels += spec.get("labels", [])
    powers += [dict(p, dir=p.get("dir", "D" if GROUND.search(p["net"]) else "U")) for p in spec.get("power", [])]
    ncs = [sh.at(x) for x in spec.get("no_connect", [])]
    for f in spec.get("flags", []):
        f = f if isinstance(f, dict) else {"at": f}
        a = sh.at(f["at"])
        d = f.get("dir", "U")
        dx, dy, _ = DIRS[d]
        L = f.get("len", 2.54)
        b = (r2(a[0] + dx * L), r2(a[1] + dy * L))
        wires.append((f.get("net", "FLAG"), [a, b]))
        powers.append({"net": "PWR_FLAG", "x": b[0], "y": b[1], "dir": d, "flag": True})
    return wires, labels, powers, ncs


def segments(wires):
    for net, pts in wires:
        for a, b in zip(pts, pts[1:]):
            if a != b:
                yield net, a, b


def on_seg(p, a, b, interior=True):
    if a[0] == b[0] == p[0]:
        lo, hi = sorted((a[1], b[1]))
        return lo < p[1] < hi if interior else lo <= p[1] <= hi
    if a[1] == b[1] == p[1]:
        lo, hi = sorted((a[0], b[0]))
        return lo < p[0] < hi if interior else lo <= p[0] <= hi
    return False


def seg_hits_box(a, b, box, pad=0.3):
    x1, y1, x2, y2 = box[0] + pad, box[1] + pad, box[2] - pad, box[3] - pad
    if x1 >= x2 or y1 >= y2:
        return False
    if a[1] == b[1]:
        lo, hi = sorted((a[0], b[0]))
        return y1 < a[1] < y2 and lo < x2 and hi > x1
    if a[0] == b[0]:
        lo, hi = sorted((a[1], b[1]))
        return x1 < a[0] < x2 and lo < y2 and hi > y1
    return False


# ------------------------------------------------------------------ checks
def style_check(spec, sh, wires, labels, powers, ncs, style):
    E, W = [], []
    segs = list(segments(wires))
    ends = collections.Counter()
    for net, pts in wires:
        ends[pts[0]] += 1; ends[pts[-1]] += 1
        for p in pts[1:-1]:
            ends[p] += 2
    # S1 grid, S2 orthogonal
    for net, a, b in segs:
        for p in (a, b):
            if not (on_grid(p[0]) and on_grid(p[1])):
                E.append(f"S1 off-grid wire point {p} on {net}")
        if a[0] != b[0] and a[1] != b[1]:
            E.append(f"S2 diagonal wire {a}->{b} on {net}: use horizontal / vertical segments only")
    for l in labels + powers:
        if not (on_grid(l["x"]) and on_grid(l["y"])):
            E.append(f"S1 off-grid {l['net']} at ({l['x']}, {l['y']})")
    # S3 a wire never runs over a pin it does not end on; S5 never through a symbol body
    for net, a, b in segs:
        for xy, owners in sh.pins.items():
            if on_seg(xy, a, b):
                E.append(f"S3 wire {net} {a}->{b} runs over pin {owners[0]} at {xy}: end the wire on the pin or reroute")
        for ref, box in sh.boxes.items():
            if seg_hits_box(a, b, box):
                E.append(f"S5 wire {net} {a}->{b} crosses the body of {ref}")
    # S4 junctions: no 4-way junctions; T-junctions get a dot (added automatically)
    junctions = set()
    pts_all = list(ends) + list(sh.pins)
    for p in set(pts_all):
        touching = sum(1 for _, a, b in segs if on_seg(p, a, b))
        deg = ends.get(p, 0) + 2 * touching + (1 if p in sh.pins else 0)
        if deg >= 3:
            junctions.add(p)
        if deg >= 4 and style.get("no_four_way", True):
            E.append(f"S4 four-way junction at {p}: offset one branch by 2.54 mm so each node is a T")
    # S6 power symbols: ground points down, supplies point up, never sideways
    for p in powers:
        if p.get("flag"):
            continue
        if GROUND.search(p["net"]) and p["dir"] != "D":
            E.append(f"S6 ground symbol {p['net']} at ({p['x']}, {p['y']}) must hang below its pin (dir D)")
        if SUPPLY_UP.search(p["net"]) and not GROUND.search(p["net"]) and p["dir"] != "U":
            E.append(f"S6 supply symbol {p['net']} at ({p['x']}, {p['y']}) must point up (dir U)")
    # S7 labels sit on a wire end or pin; label text reads left-to-right (rot 0 / 180) unless on a vertical stub
    points = set(ends) | set(sh.pins)
    for l in labels:
        if (r2(l["x"]), r2(l["y"])) not in points:
            E.append(f"S7 label {l['net']} at ({l['x']}, {l['y']}) is not on a wire end or pin")
    # S8 crossings of different nets: allowed (no dot) but limited
    cross = 0
    for i, (n1, a1, b1) in enumerate(segs):
        for n2, a2, b2 in segs[i + 1:]:
            if n1 == n2:
                continue
            if a1[1] == b1[1] and a2[0] == b2[0]:
                h, v = (a1, b1), (a2, b2)
            elif a1[0] == b1[0] and a2[1] == b2[1]:
                h, v = (a2, b2), (a1, b1)
            else:
                continue
            x, y = v[0][0], h[0][1]
            if min(h[0][0], h[1][0]) < x < max(h[0][0], h[1][0]) and min(v[0][1], v[1][1]) < y < max(v[0][1], v[1][1]):
                cross += 1
    if cross > style.get("max_crossings", 4):
        W.append(f"S8 {cross} wire crossings between different nets (max {style.get('max_crossings', 4)}): "
                 "use a label pair for long cross-sheet nets")
    # S9 parallel wires of different nets: ≥ 2.54 mm apart (readability), ≥ zone spacing for hv
    for i, (n1, a1, b1) in enumerate(segs):
        for n2, a2, b2 in segs[i + 1:]:
            if n1 == n2:
                continue
            for ax in (0, 1):
                o = 1 - ax
                if a1[o] == b1[o] and a2[o] == b2[o] and abs(a1[o] - a2[o]) < style.get("min_parallel_mm", 2.54) - 1e-6:
                    lo1, hi1 = sorted((a1[ax], b1[ax])); lo2, hi2 = sorted((a2[ax], b2[ax]))
                    if min(hi1, hi2) - max(lo1, lo2) > 0:
                        E.append(f"S9 {n1} and {n2} run parallel {abs(a1[o] - a2[o]):.2f} mm apart")
    # S10 zones: parts / nets of a zone stay inside it
    cls = spec.get("net_class", {})
    for z in spec.get("zones", []):
        x1, y1, x2, y2 = z["rect"]
        inside = lambda p: x1 <= p[0] <= x2 and y1 <= p[1] <= y2
        for net, a, b in segs:
            if net in z.get("nets", []) and not (inside(a) and inside(b)):
                E.append(f"S10 {z['name']} net {net} leaves the {z['name']} zone ({a}->{b})")
            if net not in z.get("nets", []) and cls.get(net) == "signal" and inside(a) and inside(b):
                W.append(f"S10 signal net {net} drawn inside the {z['name']} zone")
    # S11 flow: the sheet's declared inputs sit left of outputs
    flow = spec.get("flow")
    if flow:
        xin = [sh.at(p)[0] for p in flow.get("inputs", [])]
        xout = [sh.at(p)[0] for p in flow.get("outputs", [])]
        if xin and xout and max(xin) >= min(xout):
            E.append("S11 signal flow: an input connector / pin is right of an output (flow must run left to right)")
    for p in ncs:
        if p not in sh.pins:
            E.append(f"S12 no-connect at {p} is not on a pin")
    return E, W, junctions


def connectivity(sh, wires, labels, powers, junctions):
    """Union-find over the drawn geometry the way KiCad connects it."""
    parent = {}

    def f(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def u(a, b):
        parent[f(a)] = f(b)

    segs = list(segments(wires))
    for net, pts in wires:
        for a, b in zip(pts, pts[1:]):
            u(("p", a), ("p", b))
    nodes = set(sh.pins) | {p for _, pts in wires for p in pts} | junctions
    for p in nodes:
        for _, a, b in segs:
            if on_seg(p, a, b, interior=False) and (p in junctions or p == a or p == b or p in sh.pins):
                u(("p", p), ("p", a))
    for l in labels:
        u(("p", (r2(l["x"]), r2(l["y"]))), ("n", l["net"]))
    for p in powers:
        if not p.get("flag"):
            u(("p", (p["x"], p["y"])), ("n", p["net"]))
    groups = collections.defaultdict(set)
    for xy, owners in sh.pins.items():
        for o in owners:
            if not o.startswith("#"):
                groups[f(("p", xy))].add(o)
    names = collections.defaultdict(set)
    for k in list(parent):
        if k[0] == "n":
            names[f(k)].add(k[1])
    return [(sorted(names.get(g, [])), pins) for g, pins in groups.items()]


def compare(expected, nets):
    E = []
    by_pin = {}
    for names, pins in nets:
        for p in pins:
            by_pin[p] = (names, pins)
    for net, pins in expected.items():
        got = [by_pin.get(p) for p in pins]
        if any(g is None for g in got):
            E.append(f"C1 {net}: pin(s) {', '.join(p for p, g in zip(pins, got) if g is None)} not on the sheet")
            continue
        grp = got[0][1]
        if not all(g[1] is grp for g in got):
            E.append(f"C2 {net}: brief pins are split over several drawn nets")
        extra = grp - set(pins)
        if extra:
            E.append(f"C3 {net}: drawn net also touches {', '.join(sorted(extra))} (not in the brief)")
        if got[0][0] and net not in got[0][0]:
            E.append(f"C4 {net}: drawn with name(s) {got[0][0]}, brief says {net}")
    exp_pins = {p for v in expected.values() for p in v}
    for names, pins in nets:
        if len(pins) > 1 and not pins & exp_pins:
            E.append(f"C5 drawn net {names or '(unnamed)'} {sorted(pins)} is not in the brief")
    return E


# ------------------------------------------------------------------ writer
def sx_wire(a, b):
    return (f"\t(wire\n\t\t(pts\n\t\t\t(xy {a[0]:g} {a[1]:g}) (xy {b[0]:g} {b[1]:g})\n\t\t)\n"
            f"\t\t(stroke\n\t\t\t(width 0)\n\t\t\t(type default)\n\t\t)\n\t\t(uuid \"{U()}\")\n\t)\n")


def sx_junction(p):
    return f"\t(junction\n\t\t(at {p[0]:g} {p[1]:g})\n\t\t(diameter 0)\n\t\t(color 0 0 0 0)\n\t\t(uuid \"{U()}\")\n\t)\n"


def sx_label(l):
    rot = int(l.get("rot", 0))
    just = {0: "left bottom", 180: "right bottom", 90: "left bottom", 270: "right bottom"}[rot]
    font = "\t\t(effects\n\t\t\t(font\n\t\t\t\t(size 1.27 1.27)\n\t\t\t)\n\t\t\t(justify {j})\n\t\t)\n"
    if l.get("kind") == "global":
        j = "left" if rot in (0, 90) else "right"
        return (f"\t(global_label \"{l['net']}\"\n\t\t(shape {l.get('shape', 'bidirectional')})\n"
                f"\t\t(at {l['x']:g} {l['y']:g} {rot})\n\t\t(fields_autoplaced yes)\n" + font.format(j=j)
                + f"\t\t(uuid \"{U()}\")\n\t\t(property \"Intersheetrefs\" \"${{INTERSHEET_REFS}}\"\n"
                f"\t\t\t(at {l['x']:g} {l['y']:g} 0)\n\t\t\t(effects\n\t\t\t\t(font\n\t\t\t\t\t(size 1.27 1.27)\n"
                f"\t\t\t\t)\n\t\t\t\t(hide yes)\n\t\t\t)\n\t\t)\n\t)\n")
    return f"\t(label \"{l['net']}\"\n\t\t(at {l['x']:g} {l['y']:g} {rot})\n" + font.format(j=just) + f"\t\t(uuid \"{U()}\")\n\t)\n"


def sx_noconnect(p):
    return f"\t(no_connect\n\t\t(at {p[0]:g} {p[1]:g})\n\t\t(uuid \"{U()}\")\n\t)\n"


POWER_SYM = {"GND": "GND", "GNDA": "GNDA", "GNDPWR": "GNDPWR", "PE": "Earth_Protective", "EARTH": "Earth"}


def power_lib(net):
    """power:<sym> for a net: a stock symbol when one exists, else the generic +V / GND style with the value set."""
    if net == "PWR_FLAG":
        return "power:PWR_FLAG"
    name = POWER_SYM.get(net.upper()) or ("GND" if GROUND.search(net) else ("+12V" if net.upper() == "+12V" else "VCC"))
    return f"power:{name}"


def power_libblock(libid):
    name, blk = sp.source_symbol(libid)
    blk = blk.replace(f'(symbol "{name}"', f'(symbol "{libid}"', 1)
    return "\t" + blk.replace("\n\t", "\n\t\t") + "\n"


def sx_power(p, n, project, path):
    libid = power_lib(p["net"])
    ref = f"#FLG{n:03d}" if p.get("flag") else f"#PWR{n:03d}"
    prop = sp.prop
    if p.get("flag"):     # PWR_FLAG always stands upright at the end of its stub, its name above it
        rot, off = 0, 5.08
        p = dict(p, dir="U")
    else:
        rot = {"U": 0, "D": 0, "R": 270, "L": 90}[p["dir"]] if "GND" in libid else {"U": 0, "D": 180, "R": 270, "L": 90}[p["dir"]]
        off = 3.81
    vx, vy, vj = {"U": (p["x"], p["y"] - off, None), "D": (p["x"], p["y"] + off, None),
                  "R": (p["x"] + off, p["y"], "left"), "L": (p["x"] - off, p["y"], "right")}[p["dir"]]
    t = (f"\t(symbol\n\t\t(lib_id \"{libid}\")\n\t\t(at {p['x']:g} {p['y']:g} {rot})\n\t\t(unit 1)\n\t\t(body_style 1)\n"
         "\t\t(exclude_from_sim no)\n\t\t(in_bom yes)\n\t\t(on_board yes)\n\t\t(in_pos_files yes)\n\t\t(dnp no)\n"
         f"\t\t(uuid \"{U()}\")\n")
    t += prop("Reference", ref, f"{p['x']:g} {p['y']:g} 0", True, "\t\t")
    fa = 90 if rot in (90, 270) else 0          # field angle relative to the symbol: text always reads horizontally
    t += prop("Value", p["net"], f"{vx:g} {vy:g} {fa}", False, "\t\t", vj)
    t += prop("Footprint", "", f"{p['x']:g} {p['y']:g} 0", True, "\t\t")
    t += prop("Datasheet", "", f"{p['x']:g} {p['y']:g} 0", True, "\t\t")
    t += f"\t\t(pin \"1\"\n\t\t\t(uuid \"{U()}\")\n\t\t)\n"
    t += (f"\t\t(instances\n\t\t\t(project \"{project}\"\n\t\t\t\t(path \"{path}\"\n\t\t\t\t\t(reference \"{ref}\")\n"
          "\t\t\t\t\t(unit 1)\n\t\t\t\t)\n\t\t\t)\n\t\t)\n\t)\n")
    return libid, t


def instance_path(text, sheet, spec):
    if "instance_path" in spec:
        return spec["project"], spec["instance_path"]
    m = re.findall(r'\(instances\s*\(project "([^"]+)"\s*\(path "([^"]+)"', text)
    if m:
        return m[0]
    root_uuid = re.search(r'\(uuid "([^"]+)"\)', text).group(1)
    return os.path.splitext(os.path.basename(sheet))[0], "/" + root_uuid   # root sheet of its own project


def build(spec, sheet):
    text = open(sheet).read()
    text = text.replace("\n\t(lib_symbols)\n", "\n\t(lib_symbols\n\t)\n", 1)
    sh = Sheet(text, spec.get("parts", []))
    wires, labels, powers, ncs = expand(spec, sh)
    style = spec.get("style", {})
    E, W, junctions = style_check(spec, sh, wires, labels, powers, ncs, style)
    nets = connectivity(sh, wires, labels, powers, junctions)
    E += compare(spec.get("netlist", {}), nets)
    project, path = instance_path(text, sheet, spec)
    spec2 = dict(spec, parts=spec.get("parts", []), project=project, instance_path=path)
    tmp = sheet + ".forge_tmp"
    open(tmp, "w").write(text)
    if spec2["parts"]:
        text, perr, _ = sp.build(tmp, spec2)
        E += [e for e in perr if "coincide" not in e]   # pins meeting is how wires are avoided: checked above
    os.remove(tmp)
    body, newlib = "", ""
    have = set(re.findall(r'\n\t\t\(symbol "([^"]+)"\n', text))
    n0 = max([int(x) for x in re.findall(r'"#(?:PWR|FLG)(\d+)"', text)] or [0])
    for i, p in enumerate(powers, 1):
        libid, t = sx_power(p, n0 + i, project, path)
        if libid not in have:
            newlib += power_libblock(libid); have.add(libid)
        body += t
    for _, a, b in segments(wires):
        body += sx_wire(a, b)
    for p in sorted(junctions):
        if p not in sh.pins or sum(1 for _, a, b in segments(wires) if on_seg(p, a, b, interior=False)) >= 2:
            body += sx_junction(p)
    body += "".join(sx_label(l) for l in labels) + "".join(sx_noconnect(p) for p in ncs)
    for t in spec.get("text", []):
        body += (f"\t(text \"{t['t']}\"\n\t\t(exclude_from_sim no)\n\t\t(at {t['x']:g} {t['y']:g} 0)\n\t\t(effects\n"
                 f"\t\t\t(font\n\t\t\t\t(size {t.get('size', 2.54)} {t.get('size', 2.54)})\n\t\t\t)\n"
                 f"\t\t\t(justify left bottom)\n\t\t)\n\t\t(uuid \"{U()}\")\n\t)\n")
    if newlib:
        ls = text.index("\n\t(lib_symbols") + 1
        end = ls + len(sp.block_at(text, ls)) - 1
        text = text[:end - 1].rstrip("\t") + newlib + "\t" + text[end:]
    k = text.index("\n\t(sheet_instances") + 1 if "\n\t(sheet_instances" in text else text.rstrip().rindex(")")
    text = text[:k] + body + text[k:]
    return text, E, W, nets, len(junctions), sh, wires


def text_items(text):
    """Visible texts on the sheet as (owner, string, box) with an approximate box (KiCad stroke font)."""
    out = []

    def box(t, x, y, h, horiz, just, extra=0.0):
        w = 0.92 * h * len(t) + extra
        j = just or ""
        if horiz:
            x0 = x if "left" in j else x - w if "right" in j else x - w / 2
            y0 = y - h if "bottom" in j else y if "top" in j else y - h / 2
            return (x0, y0, x0 + w, y0 + h)
        y0 = y - w if "left" in j else y if "right" in j else y - w / 2
        x0 = x - h if "bottom" in j else x if "top" in j else x - h / 2
        return (x0, y0, x0 + h, y0 + w)

    for m in re.finditer(r'\n\t\(symbol\n\t\t\(lib_id "([^"]+)"\)\n\t\t\(at ([-\d.]+) ([-\d.]+) ([-\d.]+)\)', text):
        blk = sp.block_at(text, m.start() + 2)
        srot = float(m.group(4))
        ref = re.search(r'\(property "Reference" "([^"]*)"', blk).group(1)
        for pm in re.finditer(r'\n\t\t\(property "(Reference|Value)" "([^"]*)"', blk):
            pb = sp.block_at(blk, pm.start() + 3)
            if "(hide yes)" in pb or not pm.group(2):
                continue
            a = re.search(r"\(at ([-\d.]+) ([-\d.]+) ([-\d.]+)\)", pb)
            j = re.search(r"\(justify ([^)]*)\)", pb)
            horiz = (float(a.group(3)) - srot) % 180 == 0
            out.append((ref, f"{ref} {pm.group(1).lower()} '{pm.group(2)}'",
                        box(pm.group(2), float(a.group(1)), float(a.group(2)), 1.27, horiz, j and j.group(1)),
                        horiz, (float(m.group(2)), float(m.group(3)))))
    for kind in ("label", "global_label"):
        for m in re.finditer(r'\n\t\(' + kind + r' "([^"]+)"', text):
            blk = sp.block_at(text, m.start() + 2)
            a = re.search(r"\(at ([-\d.]+) ([-\d.]+) ([-\d.]+)\)", blk)
            j = re.search(r"\(justify ([^)]*)\)", blk)
            rot = float(a.group(3))
            jj = (j.group(1) if j else "left bottom") + (" bottom" if kind == "label" else "")
            out.append(("label:" + m.group(1), f"label '{m.group(1)}'",
                        box(m.group(1), float(a.group(1)), float(a.group(2)), 1.27, rot % 180 == 0, jj,
                            2.6 if kind == "global_label" else 0), True, None))
    for m in re.finditer(r'\n\t\(text "([^"]+)"', text):
        blk = sp.block_at(text, m.start() + 2)
        a = re.search(r"\(at ([-\d.]+) ([-\d.]+)", blk)
        h = float(re.search(r"\(size ([\d.]+)", blk).group(1))
        out.append(("text", f"note '{m.group(1)[:20]}'", box(m.group(1), float(a.group(1)), float(a.group(2)), h, True,
                                                              "left bottom"), True, None))
    return out


def overlap(a, b, pad=0.15):
    return a[0] + pad < b[2] and b[0] + pad < a[2] and a[1] + pad < b[3] and b[1] + pad < a[3]


def pdf_lines(cli, sch, workdir):
    """Text lines KiCad actually draws: [(text, (x0, y0, x1, y1) mm, horizontal)], page size (mm)."""
    import fitz
    pdf = os.path.join(workdir, os.path.basename(sch) + ".pdf")
    subprocess.run([cli, "sch", "export", "pdf", "-o", pdf, sch], capture_output=True)
    if not os.path.exists(pdf):
        return None, None
    pg = fitz.open(pdf)[0]
    k = 25.4 / 72
    out = []
    for blk in pg.get_text("dict")["blocks"]:
        for ln in blk.get("lines", []):
            t = "".join(sp_["text"] for sp_ in ln["spans"]).strip()
            if t:
                x0, y0, x1, y1 = ln["bbox"]
                out.append((t, (x0 * k, y0 * k, x1 * k, y1 * k), abs(ln["dir"][1]) < 1e-3))
    return out, (pg.rect.width * k, pg.rect.height * k)


def text_check(text, sheet_path, sh, wires, workdir):
    """S13-S16 on the text KiCad really renders (PDF export), minus the frame / title block of an empty sheet."""
    cli = sp.KCLI
    lines, (pw, ph) = pdf_lines(cli, sheet_path, workdir)
    if lines is None:
        return ["S13 could not export a PDF to check text placement"], []
    paper = re.search(r'\(paper "([^"]+)"\)', text).group(1)
    tb = re.search(r"\n\t\(title_block.*?\n\t\)", text, re.S)
    base_sch = os.path.join(workdir, "frame_only", "frame.kicad_sch")
    os.makedirs(os.path.dirname(base_sch), exist_ok=True)
    empty = EMPTY.format(uuid=U(), paper=paper, title="x")
    empty = re.sub(r"\n\t\(title_block.*?\n\t\)", lambda _m: tb.group(0) if tb else "", empty, count=1, flags=re.S)
    open(base_sch, "w").write(empty)
    base, _ = pdf_lines(cli, base_sch, os.path.dirname(base_sch))
    key = lambda t, b: (t, round(b[0]), round(b[1]))
    frame = {key(t, b) for t, b, _ in base or []}
    drawn, seen = [], set()
    for t, b, h in lines:
        k2 = (t, round(b[0], 1), round(b[1], 1))
        if key(t, b) in frame or t.startswith(("Sheet", "File:")) or k2 in seen:
            continue
        seen.add(k2)
        drawn.append((t, b, h))
    E, W = [], []
    for i, (t1, b1, h1) in enumerate(drawn):
        if not h1:
            E.append(f"S15 '{t1}' reads vertically: all schematic text reads left to right")
        if b1[0] < 12.7 or b1[1] < 12.7 or b1[2] > pw - 12.7 or b1[3] > ph - 12.7 or (b1[2] > pw - 122 and b1[3] > ph - 45):
            E.append(f"S16 '{t1}' is outside the drawing area (frame margin or title block) at x {b1[0]:.1f} y {b1[1]:.1f}")
        for t2, b2, _ in drawn[i + 1:]:
            if overlap(b1, b2, 0.1):
                E.append(f"S13 text overlap: '{t1}' and '{t2}' near x {b1[0]:.1f} y {b1[1]:.1f}")
        for ref, body in sh.boxes.items():
            if overlap(b1, body, 0.3):
                E.append(f"S13 '{t1}' sits on the body of {ref}")
        for net, a, b in segments(wires):
            seg = (min(a[0], b[0]) - 0.01, min(a[1], b[1]) - 0.01, max(a[0], b[0]) + 0.01, max(a[1], b[1]) + 0.01)
            if overlap(b1, seg, 0.12):
                E.append(f"S13 '{t1}' is crossed by wire {net} {a}->{b}")
                break
    # S14: each part's reference and value are drawn next to it
    for owner, name, _b, _h, anchor in text_items(text):
        if not anchor or owner.startswith("#"):
            continue
        want = re.search(r"'(.*)'$", name).group(1)
        cands = [b for t, b, _ in drawn if t == want]
        if not cands:
            continue
        d = min(max(max(b[0] - anchor[0], anchor[0] - b[2], 0), max(b[1] - anchor[1], anchor[1] - b[3], 0)) for b in cands)
        if d > 8.89:
            E.append(f"S14 {name} is drawn {d:.1f} mm from {owner} (keep fields next to the symbol, ≤ 8.89 mm)")
    return E, W


def kicad_netlist(cli, sch):
    out = sch + ".net"
    subprocess.run([cli, "sch", "export", "netlist", "--format", "kicadsexpr", "-o", out, sch], capture_output=True)
    if not os.path.exists(out):
        return None
    t = open(out).read()
    nets = {}
    for m in re.finditer(r"\(net\s*\(code", t):
        blk = sp.block_at(t, m.start())
        name = re.search(r'\(name "([^"]*)"\)', blk).group(1).lstrip("/")
        nodes = re.findall(r'\(node\s*\(ref "([^"]+)"\)\s*\(pin "([^"]+)"\)', blk)
        nets[name] = {f"{r}.{p}" for r, p in nodes if not r.startswith("#")}
    return nets


def erc_errors(rpt, has_project):
    """[(type, where)] of ERC errors; title-block variables are ignored on a bare sheet without a .kicad_pro."""
    if not os.path.exists(rpt):
        return None
    out = []
    for m in re.finditer(r"\[(\w+)\]: ([^\n]*)\n\s*; error(?:\n\s*(@[^\n]*))?", open(rpt).read()):
        if m.group(1) == "unresolved_variable" and not has_project:
            continue
        out.append(f"{m.group(1)}: {m.group(2)} {m.group(3) or ''}".strip())
    return out


def main(argv):
    if not argv or argv[0] not in ("check", "apply", "pins", "new"):
        print(__doc__)
        return 1
    if argv[0] == "pins":
        sys.argv = ["sch_place", "x.kicad_sch", "--pins", *argv[1:]]
        sp.main()
        return 0
    if argv[0] == "new":
        f = argv[1]
        if os.path.exists(f):
            sys.exit(f"{f} exists")
        paper = argv[argv.index("--paper") + 1] if "--paper" in argv else "A3"
        title = argv[argv.index("--title") + 1] if "--title" in argv else "${PROJECTNAME}"
        os.makedirs(os.path.dirname(os.path.abspath(f)), exist_ok=True)
        open(f, "w").write(EMPTY.format(uuid=U(), paper=paper, title=title))
        print("created", f)
        return 0
    spec_path = argv[1]
    spec = json.load(open(spec_path))
    sheet = os.path.abspath(spec["sheet"] if os.path.isabs(spec["sheet"]) else
                            os.path.join(os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd()), spec["sheet"]))
    orig = open(sheet, "rb").read()
    text, E, W, nets, nj, sh, wires = build(spec, sheet)
    scratch = os.path.join(sp.SCRATCH, "schdraw")
    shutil.rmtree(scratch, True)
    proj = os.path.dirname(sheet)
    shutil.copytree(proj, os.path.join(scratch, "proj"), ignore=shutil.ignore_patterns(".git", "release", "*.lck", "*-backups"))
    dry = os.path.join(scratch, "proj", os.path.basename(sheet))
    open(dry, "w").write(text)
    te, tw = text_check(text, dry, sh, wires, scratch)
    E += te; W += tw
    # second, independent check: KiCad's own netlist of the dry copy against the brief
    kn = kicad_netlist(sp.KCLI, dry)
    if kn is not None:
        for net, pins in spec.get("netlist", {}).items():
            grp = next((g for g in kn.values() if set(pins) & g), set())
            if set(pins) - grp or grp - set(pins):
                E.append(f"K1 KiCad netlist disagrees on {net}: expected {sorted(pins)}, KiCad has {sorted(grp)}")
    erc = os.path.join(scratch, "erc.rpt")
    subprocess.run([sp.KCLI, "sch", "erc", "--severity-error", "-o", erc, dry], capture_output=True)
    ercs = erc_errors(erc, bool(glob.glob(os.path.join(proj, "*.kicad_pro"))))
    E += [f"ERC {e}" for e in (ercs or [])]
    png = sp.render(dry, os.path.join(scratch, "sheet.png"))
    print(f"parts {len(spec.get('parts', []))} · nets drawn {len(nets)} · junctions {nj} · "
          f"errors {len(E)} (ERC {len(ercs) if ercs is not None else '?'}) · warnings {len(W)}")
    for e in E:
        print("  ERROR", e)
    for w in W:
        print("  warn ", w)
    print("render:", png, "\nERC report:", erc)
    stamp = os.path.join(sp.SCRATCH, "schdraw.sha1")
    import hashlib
    h = hashlib.sha1(orig + open(spec_path, "rb").read()).hexdigest()
    if argv[0] == "check":
        if not E:
            open(stamp, "w").write(h)
        return 1 if E else 0
    if E:
        sys.exit("not applied: fix the errors above")
    if glob.glob(os.path.join(proj, "~*.kicad_sch.lck")):
        sys.exit("not applied: the schematic editor has this project open. Save and close it first.")
    if not os.path.exists(stamp) or open(stamp).read() != h:
        sys.exit("not applied: run `forge schdraw check` on this spec first (sheet or spec changed since)")
    bak = os.path.join(sp.SCRATCH, os.path.basename(sheet) + ".bak")
    open(bak, "wb").write(orig)
    open(sheet, "w").write(text)
    print(f"applied → {sheet}\nbackup  → {bak}")
    return 0


EMPTY = """(kicad_sch
\t(version 20250114)
\t(generator "eeschema")
\t(generator_version "10.0")
\t(uuid "{uuid}")
\t(paper "{paper}")
\t(title_block
\t\t(title "{title}")
\t\t(date "${{RELEASE_DATE}}")
\t\t(rev "${{REVISION}}")
\t\t(company "${{COMPANY}}")
\t\t(comment 1 "Designer: ${{DESIGNER}}")
\t)
\t(lib_symbols)
\t(sheet_instances
\t\t(path "/"
\t\t\t(page "1")
\t\t)
\t)
\t(embedded_fonts no)
)
"""
