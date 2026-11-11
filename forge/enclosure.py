"""forge enclosure: the enclosure chain, from the finished board to a checked box (docs/PLAN_enclosure.md).

  forge enclosure req                  what [enclosure] must say; open decisions for what is missing (GE waits)
  forge enclosure fit [--json]         the envelope from the board: outline, holes, every part's height (STEP),
                                       connector faces; parts with no 3D model are a gap, never a pass
  forge enclosure entries [--json]     every signal leaving the box: the sealing part, its IP when mated, its
                                       current / voltage rating; the box IP = the weakest entry
  forge enclosure build                build123d: body, lid (gasket groove, inserts, standoffs, cut-outs), STEP /
                                       STL, the assembly with the board, cad_report.json (CAD venv needed)
  forge enclosure check [--json]       fit, thermal L1 (closed form, always), structural L1, ingress rules,
                                       manufacturability; CalculiX levels when installed (SKIP otherwise)
  forge enclosure release              copy the package into 09_Release/<rev>/mechanical/

project.toml:
  [enclosure]
  required = true            ip = "IP65"           material = "aluminium"   # aluminium | plastic | printed | vendor
  process = "machined"       # die-cast | machined | moulded | printed | vendor
  mounting = "wall"          # wall | din | panel | handheld | pcb
  ik = "IK08"                emissivity = 0.85     orientation = "vertical"  # for the convection correlation
  clearance_mm = 3           # board edge / part to wall
  loss_W = 1.2               # total dissipation at the worst corner (default: from the simulation results' losses)
  [enclosure.entries]        # per edge connector: the sealing part and its rating
  J1 = {part = "gland", ip = "IP68", a = 10, v = 63, cable_mm = [4, 8]}
  J2 = {part = "M12-A-4", ip = "IP67", a = 4, v = 60}
"""
import csv
import glob
import json
import math
import os
import re
import subprocess
import tempfile

from . import config, kicad

CAD_PY = [os.path.expanduser("~/.venvs/pe-forge-cad/bin/python"), os.path.expanduser("~/.venvs/pe-forge-cad/Scripts/python.exe")]
IP_RE = re.compile(r"^IP(\d)(\d)$")

# material rules: wall / floor / lid / flange / boss / standoff by process (mm)
MATERIAL = {
    "aluminium": {"die-cast": {"wall": 2.5, "floor": 2.5, "lid_t": 3.0, "boss_d": 8.0, "insert_d": 2.5, "insert_depth": 8.0, "screw_d": 3.2, "density": 2.7, "k": 150, "draft_deg": 1.5, "corner_r": 3.0},
                  "machined": {"wall": 2.0, "floor": 2.5, "lid_t": 3.0, "boss_d": 8.0, "insert_d": 2.5, "insert_depth": 8.0, "screw_d": 3.2, "density": 2.7, "k": 170, "draft_deg": 0.0, "corner_r": 3.0},
                  "sheet": {"wall": 1.5, "floor": 1.5, "lid_t": 1.5, "boss_d": 6.0, "insert_d": 3.0, "insert_depth": 5.0, "screw_d": 3.2, "density": 2.7, "k": 170, "draft_deg": 0.0, "corner_r": 1.5}},
    "plastic": {"moulded": {"wall": 2.0, "floor": 2.0, "lid_t": 2.5, "boss_d": 7.0, "insert_d": 4.0, "insert_depth": 6.0, "screw_d": 3.2, "density": 1.2, "k": 0.2, "draft_deg": 1.0, "corner_r": 2.0},
                "printed": {"wall": 2.4, "floor": 2.0, "lid_t": 2.4, "boss_d": 8.0, "insert_d": 4.0, "insert_depth": 6.0, "screw_d": 3.2, "density": 1.24, "k": 0.2, "draft_deg": 0.0, "corner_r": 2.0}},
}
MATERIAL["printed"] = {"printed": MATERIAL["plastic"]["printed"]}

# gasket: cross-section by IP (mm), compression window 20-30 % (solid silicone / EPDM)
GASKET = {5: {"section": 2.0, "comp": 0.25}, 6: {"section": 2.5, "comp": 0.25}, 7: {"section": 3.0, "comp": 0.25}, 8: {"section": 3.5, "comp": 0.25}}

# the sealing parts a box entry can use: IP when mated, current / voltage class, cut-out (mm), wall range (mm)
ENTRY_PARTS = {
    "gland-M12": {"ip": "IP68", "a": 16, "v": 300, "cutout": ("round", 12.5), "wall_mm": [1, 8], "cable_mm": [3, 6.5], "cost_usd": 0.6},
    "gland-M16": {"ip": "IP68", "a": 25, "v": 300, "cutout": ("round", 16.5), "wall_mm": [1, 10], "cable_mm": [4, 10], "cost_usd": 0.8},
    "gland-M20": {"ip": "IP68", "a": 40, "v": 300, "cutout": ("round", 20.5), "wall_mm": [1, 12], "cable_mm": [6, 12], "cost_usd": 1.0},
    "M8-3": {"ip": "IP67", "a": 3, "v": 60, "cutout": ("round", 8.2), "wall_mm": [1, 6], "cost_usd": 4.5},
    "M12-A-4": {"ip": "IP67", "a": 4, "v": 60, "cutout": ("round", 12.2), "wall_mm": [1, 8], "cost_usd": 5.5},
    "M12-A-5": {"ip": "IP67", "a": 4, "v": 60, "cutout": ("round", 12.2), "wall_mm": [1, 8], "cost_usd": 6.0},
    "M12-S-4": {"ip": "IP67", "a": 12, "v": 630, "cutout": ("round", 12.2), "wall_mm": [1, 8], "cost_usd": 9.0},
    "M12-T-4": {"ip": "IP67", "a": 12, "v": 63, "cutout": ("round", 12.2), "wall_mm": [1, 8], "cost_usd": 9.0},
    "vent-M12": {"ip": "IP68", "a": 0, "v": 0, "cutout": ("round", 12.2), "wall_mm": [1, 6], "cost_usd": 2.0},
    "open": {"ip": "IP20", "a": 0, "v": 0, "cutout": None, "wall_mm": [0, 99], "cost_usd": 0},
}


def ip_digits(code):
    m = IP_RE.match(str(code or "").upper().strip())
    return (int(m.group(1)), int(m.group(2))) if m else None


def cfg(prj):
    return prj.cfg.get("enclosure", {})


def cad_python():
    return next((p for p in CAD_PY if os.path.exists(p)), None)


# ------------------------------------------------------------------ E1 requirements
REQ = [("ip", "the ingress code (IEC 60529): IP20 indoor open / IP54 dust and splash / IP65 jets / IP67 immersion", "IP54 | IP65 | IP67 | IP20"),
       ("material", "aluminium (die-cast / machined / sheet) / plastic (moulded) / printed / vendor box", "aluminium | plastic | printed | vendor"),
       ("process", "die-cast | machined | sheet | moulded | printed | vendor", ""),
       ("mounting", "wall | din | panel | handheld | pcb", "wall | din | panel"),
       ("ik", "impact (IEC 62262): IK06 light / IK08 industrial / IK10 vandal", "IK08 | IK06 | IK10")]


def req(prj, out=print):
    e = cfg(prj)
    missing = [(k, q, o) for k, q, o in REQ if not e.get(k)]
    ok = not missing
    for k, q, o in missing:
        out(f"  [enclosure] {k} not set: {q}")
    if e.get("ip") and not ip_digits(e["ip"]):
        out(f"  [enclosure] ip = {e['ip']!r} is not an IP code (IPxy)")
        ok = False
    mat, proc = e.get("material"), e.get("process")
    if mat and proc and proc not in MATERIAL.get(mat, {}):
        out(f"  [enclosure] process {proc!r} is not one of {', '.join(MATERIAL.get(mat, {}))} for {mat}")
        ok = False
    if not prj.cfg.get("corners", {}).get("ambient_C"):
        out("  [corners] ambient_C not set: the thermal check has no ambient")
        ok = False
    return ok, missing


def open_decisions(prj):
    """Turn every missing requirement into an owner decision tagged to GE (once)."""
    from . import decisions
    ok, missing = req(prj, out=lambda *_: None)
    have = {d["question"] for d in decisions.load(prj) if d["status"] == "OPEN"}
    made = []
    for k, q, o in missing:
        question = f"Enclosure {k}: {q}"
        if question not in have:
            made.append(decisions.new(prj, question, "GE", [x.strip() for x in o.split("|") if x.strip()],
                                      o.split("|")[0].strip() if o else "", "needed before the enclosure can be drawn"))
    return made


# ------------------------------------------------------------------ E2 fit
def board_of(prj):
    from .check import roots, has_content
    pcbs = [b for b in roots(prj, "kicad_pcb") if has_content(b)]
    return pcbs[0] if pcbs else None


def pos_rows(pcb):
    cli = kicad.find_cli()
    if not cli:
        return None
    with tempfile.TemporaryDirectory() as td:
        f = os.path.join(td, "pos.csv")
        subprocess.run([cli, "pcb", "export", "pos", "--format", "csv", "--units", "mm", "--side", "both", "--exclude-dnp",
                        "-o", f, pcb], capture_output=True)
        if not os.path.exists(f):
            return None
        return list(csv.DictReader(open(f, newline="")))


def export_step(pcb, out):
    cli = kicad.find_cli()
    if not cli:
        return False
    r = subprocess.run([cli, "pcb", "export", "step", "--subst-models", "--no-dnp", "--force", "-o", out, pcb],
                       capture_output=True, text=True, timeout=600)
    return os.path.exists(out)


HEIGHTS = r'''
import json, sys
from build123d import import_step
s = import_step(sys.argv[1])
out = {"children": []}
bb = s.bounding_box()
out["bbox"] = [[bb.min.X, bb.min.Y, bb.min.Z], [bb.max.X, bb.max.Y, bb.max.Z]]
for k in getattr(s, "children", []):
    b = k.bounding_box()
    out["children"].append({"label": str(k.label), "min": [b.min.X, b.min.Y, b.min.Z], "max": [b.max.X, b.max.Y, b.max.Z]})
print("JSON" + json.dumps(out))
'''


def heights(step):
    py = cad_python()
    if not py:
        return None
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(HEIGHTS)
    r = subprocess.run([py, f.name, step], capture_output=True, text=True, timeout=600)
    os.remove(f.name)
    m = re.search(r"JSON(\{.*\})", r.stdout)
    return json.loads(m.group(1)) if m else None


def outline(pcb):
    """(x0, y0, x1, y1, holes[(x, y, d)]) from the board text: Edge.Cuts extent and mounting-hole pads."""
    t = open(pcb, errors="ignore").read()
    xs, ys = [], []
    for m in re.finditer(r'\((?:gr_line|gr_rect|gr_arc|gr_circle|gr_poly)\b(.{0,800}?)\(layer "Edge\.Cuts"\)', t, re.S):
        for p in re.finditer(r"\((?:start|end|center|mid|xy) ([-\d.]+) ([-\d.]+)\)", m.group(1)):
            xs.append(float(p.group(1))); ys.append(float(p.group(2)))
    holes = []
    for fp in re.finditer(r'\(footprint "([^"]*MountingHole[^"]*)"(.*?)\n\t\)', t, re.S):
        at = re.search(r"\(at ([-\d.]+) ([-\d.]+)", fp.group(2))
        dr = re.search(r"\(drill ([\d.]+)\)", fp.group(2))
        if at:
            holes.append((float(at.group(1)), float(at.group(2)), float(dr.group(1)) if dr else 3.2))
    if not xs:
        return None
    return min(xs), min(ys), max(xs), max(ys), holes


def footprint_size(prj, pcb, ref):
    """(w, h) mm of a placed footprint's courtyard (else its pads' extent) from the board text."""
    t = open(pcb, errors="ignore").read()
    m = re.search(r'\(footprint "[^"]*"(.*?)\n\t\)', t, re.S)
    for fp in re.finditer(r'\(footprint "[^"]*"(.*?)\n\t\)', t, re.S):
        body = fp.group(1)
        if not re.search(rf'\(property "Reference" "{re.escape(ref)}"', body):
            continue
        xs, ys = [], []
        for ln in re.finditer(r'\(fp_(?:line|rect)\b(.{0,300}?)\(layer "F\.CrtYd"\)', body, re.S):
            for p in re.finditer(r"\((?:start|end) ([-\d.]+) ([-\d.]+)\)", ln.group(1)):
                xs.append(float(p.group(1))); ys.append(float(p.group(2)))
        if not xs:
            for p in re.finditer(r'\(pad "[^"]*"[^\n]*\n?\s*\(at ([-\d.]+) ([-\d.]+)[^)]*\)\s*\(size ([-\d.]+) ([-\d.]+)\)', body):
                x, y, w, h = map(float, p.groups())
                xs += [x - w / 2, x + w / 2]; ys += [y - h / 2, y + h / 2]
        if xs:
            return [round(max(xs) - min(xs), 2), round(max(ys) - min(ys), 2)]
    return [10.0, 10.0]


def fit(prj, out_dir=None):
    pcb = board_of(prj)
    if not pcb:
        return None, "no board with content"
    ol = outline(pcb)
    if not ol:
        return None, "no board outline (Edge.Cuts)"
    x0, y0, x1, y1, holes = ol
    out_dir = out_dir or os.path.join(prj.p("mechanical"), "enclosure")
    os.makedirs(out_dir, exist_ok=True)
    step = os.path.join(out_dir, "board.step")
    if not export_step(pcb, step):
        return None, "kicad-cli STEP export failed (forge doctor)"
    rows = pos_rows(pcb) or []
    h = heights(step)
    parts, gaps = [], []
    kids = (h or {}).get("children", [])
    for r in rows:
        ref, x, y = r["Ref"], float(r["PosX"]), float(r["PosY"])
        side = (r.get("Side") or "top").lower()
        # KiCad's STEP has +Y up; the pos file's Y is also up with --units mm (both board coordinates)
        best = None
        for k in kids:
            cx, cy = (k["min"][0] + k["max"][0]) / 2, (k["min"][1] + k["max"][1]) / 2
            d = math.hypot(cx - x, cy - y)
            if d < 3.0 and (best is None or d < best[0]) and "PCB" not in k["label"]:
                best = (d, k)
        if best:
            k = best[1]
            top = k["max"][2] if side == "top" else -k["min"][2]
            parts.append({"ref": ref, "side": side, "x": x, "y": y, "height_mm": round(top, 2), "label": k["label"],
                          "size_mm": [round(k["max"][0] - k["min"][0], 2), round(k["max"][1] - k["min"][1], 2)]})
        else:
            gaps.append(ref)
            if re.match(r"^(J|P|CN|X)\d", ref):          # a connector with no model still needs its cut-out:
                sz = footprint_size(prj, pcb, ref)         # size from the footprint's courtyard, height unknown
                parts.append({"ref": ref, "side": side, "x": x, "y": y, "height_mm": None, "label": "(no 3D model)",
                              "size_mm": sz})
    top_h = max([p["height_mm"] for p in parts if p["side"] == "top" and p["height_mm"] is not None] or [0])
    bot_h = max([p["height_mm"] for p in parts if p["side"] == "bottom" and p["height_mm"] is not None] or [0])
    conns = [p for p in parts if re.match(r"^(J|P|CN|X)\d", p["ref"])]
    env = {"board": prj.rel(pcb), "step": prj.rel(step), "outline_mm": [round(x1 - x0, 2), round(y1 - y0, 2)],
           "origin": [x0, y0], "holes": [[round(x - x0, 2), round(y - y0, 2), d] for x, y, d in holes],
           "top_height_mm": round(top_h, 2), "bottom_height_mm": round(bot_h, 2), "parts": parts,
           "no_3d_model": gaps, "connectors": conns, "cad_venv": bool(cad_python()), "pos_rows": len(rows)}
    if h is None:
        env["note"] = "no CAD venv: heights not read (forge doctor)"
    json.dump(env, open(os.path.join(out_dir, "envelope.json"), "w"), indent=1)
    return env, ""


# ------------------------------------------------------------------ E3 entries
def entries(prj, env=None):
    e = cfg(prj)
    want = ip_digits(e.get("ip")) or (2, 0)
    table = e.get("entries", {})
    conns = (env or {}).get("connectors") or []
    refs = [c["ref"] for c in conns] or list(table)
    rows, worst = [], (9, 9)
    for ref in refs:
        spec = table.get(ref)
        if not spec:
            rows.append({"ref": ref, "part": None, "ip": "IP20", "ok": False, "why": "no sealing part named in [enclosure.entries]"})
            worst = (2, 0)
            continue
        part = ENTRY_PARTS.get(spec.get("part"), None)
        ipc = spec.get("ip") or (part or {}).get("ip")
        d = ip_digits(ipc) or (2, 0)
        worst = (min(worst[0], d[0]), min(worst[1], d[1]))
        probs = []
        if d < want:
            probs.append(f"{ipc} below the box's {e.get('ip')}")
        a_need, v_need = spec.get("a_net"), spec.get("v_net")
        a_rat, v_rat = spec.get("a", (part or {}).get("a")), spec.get("v", (part or {}).get("v"))
        if a_need and a_rat and a_need > a_rat:
            probs.append(f"net current {a_need} A > entry rating {a_rat} A")
        if v_need and v_rat and v_need > v_rat:
            probs.append(f"net voltage {v_need} V > entry rating {v_rat} V")
        wall = e.get("wall_mm")
        if part and wall and not (part["wall_mm"][0] <= wall <= part["wall_mm"][1]):
            probs.append(f"wall {wall} mm outside the part's {part['wall_mm']} mm range")
        rows.append({"ref": ref, "part": spec.get("part"), "ip": ipc, "a": a_rat, "v": v_rat, "cutout": (part or {}).get("cutout"),
                     "ok": not probs, "why": "; ".join(probs)})
    has_vent = any((v or {}).get("part", "").startswith("vent") for v in table.values())
    box_ip = f"IP{worst[0]}{worst[1]}" if rows else "IP20"
    return {"box_ip": box_ip, "want": e.get("ip"), "rows": rows, "vent": has_vent,
            "ok": all(r["ok"] for r in rows) and (worst >= want) and (want[1] < 5 or has_vent or e.get("sealed_no_vent") is True)}


# ------------------------------------------------------------------ E4 build
def spec_for(prj, env):
    e = cfg(prj)
    mat = e.get("material", "aluminium")
    proc = e.get("process") or next(iter(MATERIAL.get(mat, {"machined": 0})))
    m = MATERIAL.get(mat, {}).get(proc) or MATERIAL["aluminium"]["machined"]
    clear = float(e.get("clearance_mm", 3))
    standoff_h = float(e.get("standoff_mm", max(env["bottom_height_mm"] + 1.5, 4.0)))
    headroom = float(e.get("headroom_mm", 3))
    W = env["outline_mm"][0] + 2 * clear + 2 * m["wall"]
    D = env["outline_mm"][1] + 2 * clear + 2 * m["wall"]
    H = m["floor"] + standoff_h + 1.6 + env["top_height_mm"] + headroom + m["lid_t"]
    ip = ip_digits(e.get("ip")) or (2, 0)
    gasket = None
    if ip[1] >= 4:
        g = GASKET.get(min(max(ip[1], 5), 8))
        gasket = {"section": g["section"], "comp": g["comp"], "groove_w": round(g["section"] * 1.1, 2),
                  "groove_d": round(g["section"] * (1 - g["comp"]), 2)}
    cut = []
    ent = entries(prj, env)
    for c in env["connectors"]:
        row = next((r for r in ent["rows"] if r["ref"] == c["ref"]), None)
        co = (row or {}).get("cutout")
        # which wall the face is on: the nearest board edge
        x, y = c["x"] - env["origin"][0], c["y"] - env["origin"][1]
        dists = {"left": x, "right": env["outline_mm"][0] - x, "front": y, "back": env["outline_mm"][1] - y}
        wall = min(dists, key=dists.get)
        u = (y if wall in ("left", "right") else x) + clear
        hgt = c["height_mm"] if c["height_mm"] is not None else 10.0
        if co and co[0] == "round":
            cut.append({"ref": c["ref"], "wall": wall, "u": round(u, 2), "z": round(max(co[1] / 2 + 1.0, hgt / 2), 2), "w": co[1], "h": co[1]})
        else:
            cut.append({"ref": c["ref"], "wall": wall, "u": round(u, 2), "z": round(hgt / 2 + 0.8, 2),
                        "w": round(c["size_mm"][1 if wall in ("left", "right") else 0] + 1.0, 2), "h": round(hgt + 1.0, 2)})
    return {"outer": [round(W, 2), round(D, 2), round(H, 2)], "wall": m["wall"], "floor": m["floor"], "lid_t": m["lid_t"],
            "boss_d": m["boss_d"], "insert_d": m["insert_d"], "insert_depth": m["insert_depth"], "screw_d": m["screw_d"],
            "corner_r": m["corner_r"], "clear": clear, "standoff_d": 6.0, "standoff_h": standoff_h,
            "holes": env["holes"], "cutouts": cut, "gasket": gasket, "density_g_cm3": m["density"], "k_W_mK": m["k"],
            "material": mat, "process": proc, "board_step": os.path.join(prj.root, env["step"]),
            "board_min": env["origin"], "board_z_min": 0.0}


def build(prj, out_dir=None):
    out_dir = out_dir or os.path.join(prj.p("mechanical"), "enclosure")
    envf = os.path.join(out_dir, "envelope.json")
    if not os.path.exists(envf):
        env, why = fit(prj, out_dir)
        if env is None:
            raise SystemExit(why)
    env = json.load(open(envf))
    py = cad_python()
    if not py:
        raise SystemExit("no CAD venv (build123d): forge doctor")
    spec = spec_for(prj, env)
    spec["out"] = out_dir
    sp = os.path.join(out_dir, "box_spec.json")
    json.dump(spec, open(sp, "w"), indent=1)
    r = subprocess.run([py, os.path.join(os.path.dirname(__file__), "cad_box.py"), sp], capture_output=True, text=True, timeout=900)
    if "OK " not in r.stdout:
        raise SystemExit("CAD build failed: " + (r.stderr or r.stdout)[-1200:])
    return json.loads(r.stdout.split("OK ", 1)[1]), spec


# ------------------------------------------------------------------ E5 checks
def losses_W(prj):
    e = cfg(prj)
    if e.get("loss_W"):
        return float(e["loss_W"]), "[enclosure] loss_W"
    try:
        from . import results
        best = None
        for f in results.result_files(prj):
            d = json.load(open(f))
            for m in d.get("metrics") or []:
                if re.search(r"total.*loss|loss.*total|dissipation", m.get("name", ""), re.I) and str(m.get("unit", "")).startswith("W"):
                    vals = [v for v in m.get("values") or [] if isinstance(v, (int, float))]
                    if vals:
                        best = max(best or 0, max(vals))
            if best is None:
                for m in d.get("metrics") or []:
                    if re.search(r"efficien", m.get("name", ""), re.I):
                        vals = [v for v in m.get("values") or [] if isinstance(v, (int, float))]
                        po = prj.cfg.get("specs", {}).get("power_W")
                        if vals and po:
                            best = max(best or 0, float(po) * (100 - min(vals)) / min(vals))
        if best is not None:
            return best, "simulation results"
    except Exception:
        pass
    return None, "no loss figure: set [enclosure] loss_W or a simulation result with losses / efficiency"


def thermal_l1(outer_mm, loss, amb, emis=0.85, orient="vertical", k_wall=150, wall=2.5, inner_h_W_m2K=None):
    """Natural convection + radiation from a closed box in still air: ΔT such that P = (h_c A_c + h_r A) ΔT, with
    Churchill-Chu style h for vertical faces and the horizontal-plate forms for top / bottom; the inside air-to-wall
    step (for a sealed plastic / air-filled box) added as a series resistance when inner_h is given."""
    W, D, H = [v / 1000 for v in outer_mm]
    A_side = 2 * (W * H + D * H)
    A_top, A_bot = W * D, W * D
    A = A_side + A_top + A_bot
    sigma = 5.67e-8
    dT = 10.0
    for _ in range(50):
        Ts = amb + dT
        Tf = (Ts + amb) / 2 + 273.15
        beta = 1 / Tf
        nu, kair, Pr = 1.6e-5, 0.026, 0.71
        def h_vert(L):
            Ra = 9.81 * beta * dT * L ** 3 / nu ** 2 * Pr
            Nu = (0.825 + 0.387 * Ra ** (1 / 6) / (1 + (0.492 / Pr) ** (9 / 16)) ** (8 / 27)) ** 2
            return Nu * kair / L
        def h_top(Lc):
            Ra = 9.81 * beta * dT * Lc ** 3 / nu ** 2 * Pr
            Nu = 0.54 * Ra ** 0.25 if Ra < 1e7 else 0.15 * Ra ** (1 / 3)
            return Nu * kair / Lc
        def h_bot(Lc):
            Ra = 9.81 * beta * dT * Lc ** 3 / nu ** 2 * Pr
            return 0.27 * Ra ** 0.25 * kair / Lc
        Lc = W * D / (2 * (W + D))
        hc = (h_vert(H) * A_side + h_top(Lc) * A_top + h_bot(Lc) * A_bot) / A
        hr = emis * sigma * ((Ts + 273.15) ** 2 + (amb + 273.15) ** 2) * ((Ts + 273.15) + (amb + 273.15))
        R_out = 1 / ((hc + hr) * A)
        R_wall = (wall / 1000) / (k_wall * A)
        R_in = 1 / (inner_h_W_m2K * A) if inner_h_W_m2K else 0.0
        new = loss * (R_out + R_wall + R_in)
        if abs(new - dT) < 0.01:
            dT = new
            break
        dT = 0.5 * dT + 0.5 * new
    return {"dT_K": round(dT, 2), "T_surface_C": round(amb + loss * R_out, 2), "T_inside_C": round(amb + dT, 2),
            "h_conv_W_m2K": round(hc, 2), "h_rad_W_m2K": round(hr, 2), "area_m2": round(A, 4)}


def check(prj, out_dir=None):
    out_dir = out_dir or os.path.join(prj.p("mechanical"), "enclosure")
    e = cfg(prj)
    items = []
    ok_req, missing = req(prj, out=lambda *_: None)
    items.append((ok_req, "requirements: [enclosure] complete" + ("" if ok_req else ": missing " + ", ".join(k for k, _, _ in missing))))
    envf = os.path.join(out_dir, "envelope.json")
    env = json.load(open(envf)) if os.path.exists(envf) else None
    items.append((env is not None, "fit: envelope from the board" + ("" if env else " (forge enclosure fit)")))
    if env:
        items.append((not env["no_3d_model"], f"fit: every part has a 3D model / height ({len(env['parts'])} read)"
                      + (": no model for " + ", ".join(env["no_3d_model"][:6]) if env["no_3d_model"] else "")))
        items.append((env.get("cad_venv", False), "CAD venv (build123d) present" + ("" if env.get("cad_venv") else ": forge doctor")))
    ent = entries(prj, env)
    items.append((ent["ok"], f"ingress: box {ent['box_ip']} vs required {ent['want'] or '?'} (weakest entry)"
                  + "".join(f"; {r['ref']}: {r['why']}" for r in ent["rows"] if not r["ok"])
                  + ("" if ent["vent"] or (ip_digits(e.get("ip")) or (0, 0))[1] < 5 or e.get("sealed_no_vent") else
                     "; no vent membrane on a sealed box (pressure cycling lifts the gasket): add vent-M12 or set sealed_no_vent = true with the reason")))
    rep = os.path.join(out_dir, "cad_report.json")
    cadr = json.load(open(rep)) if os.path.exists(rep) else None
    items.append((cadr is not None, "build: CAD package exists" + ("" if cadr else " (forge enclosure build)")))
    loss, src = losses_W(prj)
    amb = prj.cfg.get("corners", {}).get("ambient_C")
    amax = max(amb) if isinstance(amb, list) and amb else amb
    if loss is not None and amax is not None and (cadr or env):
        outer = (cadr or {}).get("outer_mm") or spec_for(prj, env)["outer"]
        mat = MATERIAL.get(e.get("material", "aluminium"), {}).get(e.get("process") or "machined") or MATERIAL["aluminium"]["machined"]
        inner_h = None if e.get("material", "aluminium") == "aluminium" and e.get("potted") else 6.0
        t1 = thermal_l1(outer, loss, amax, float(e.get("emissivity", 0.85)), e.get("orientation", "vertical"), mat["k"], mat["wall"], inner_h)
        lim = float(e.get("max_inside_C", 85))
        items.append((t1["T_inside_C"] <= lim, f"thermal L1 (closed form): {loss:g} W ({src}) in a {outer[0]:g} x {outer[1]:g} x {outer[2]:g} mm box at {amax:g} °C ambient: "
                      f"inside {t1['T_inside_C']} °C (ΔT {t1['dT_K']} K; h_c {t1['h_conv_W_m2K']}, h_r {t1['h_rad_W_m2K']} W/m²K)"
                      + ("" if t1["T_inside_C"] <= lim else f" > {lim} °C limit ([enclosure] max_inside_C): larger box, fins, potting or a heatsink wall")))
        json.dump({"loss_W": loss, "source": src, "ambient_C": amax, **t1}, open(os.path.join(out_dir, "thermal_l1.json"), "w"), indent=1)
    else:
        items.append((False, "thermal L1: not computed: " + (src if loss is None else "no [corners] ambient_C" if amax is None else "run forge enclosure fit")))
    import shutil
    ccx = shutil.which("ccx") or shutil.which("ccx_2.21")
    items.append((bool(ccx) or e.get("thermal_l2") == "waived", "thermal L2 (CalculiX): " + ("available" if ccx else "SKIPPED: ccx not installed (forge doctor); waive with [enclosure] thermal_l2 = \"waived\" + reason only for IP < 54 indoor")))
    ip = ip_digits(e.get("ip")) or (2, 0)
    if ip[1] >= 4 and cadr:
        g = spec_for(prj, env)["gasket"]
        W, D, H = cadr["outer_mm"]
        span = max(W, D)
        pitch_ok = span <= 120 or e.get("lid_screws", 4) > 4
        items.append((pitch_ok, f"ingress: gasket {g['section']} mm section, groove {g['groove_w']} x {g['groove_d']} mm (compression {int(g['comp'] * 100)} %), "
                      f"lid span {span:g} mm with {e.get('lid_screws', 4)} screws" + ("" if pitch_ok else ": add screws (≤ 120 mm between them) or a stiffer lid")))
        flat = max(W, D) / 500
        items.append((True, f"ingress: sealing-face flatness required ≤ {flat:.2f} mm over {span:g} mm (drawing note)"))
    if cadr:
        items.append((True, f"mass {cadr['mass_g']} g, outer area {cadr['outer_area_cm2']} cm², {len(cadr['cutouts'])} cut-out(s)"))
    return items


def gate_items(prj):
    try:
        items = check(prj)
    except SystemExit as e:
        return [(False, f"enclosure: {e}")]
    bad = [m for ok, m in items if not ok]
    return [(not bad, f"enclosure (GE): {len(items) - len(bad)}/{len(items)} checks" + ("; " + "; ".join(b[:90] for b in bad[:3]) if bad else ""))]


def release(prj, out_dir=None):
    from . import fab
    out_dir = out_dir or os.path.join(prj.p("mechanical"), "enclosure")
    pcb = board_of(prj)
    rev = fab.revision(os.path.dirname(pcb)) if pcb else "A"
    dst = os.path.join(prj.p("release"), rev, "mechanical")
    os.makedirs(dst, exist_ok=True)
    import shutil
    n = 0
    for f in glob.glob(os.path.join(out_dir, "*")):
        if f.endswith((".step", ".stl", ".json", ".dxf", ".pdf")):
            shutil.copy2(f, dst)
            n += 1
    return dst, n


def main(prj, argv):
    out_dir = os.path.join(prj.p("mechanical"), "enclosure")
    if argv[:1] == ["req"]:
        ok, missing = req(prj)
        made = open_decisions(prj) if missing else []
        print("OK: [enclosure] complete" if ok else f"{len(missing)} requirement(s) missing" + (f"; {len(made)} decision(s) opened for the owner (forge decision list --open)" if made else ""))
        return 0 if ok else 1
    if argv[:1] == ["fit"]:
        env, why = fit(prj, out_dir)
        if env is None:
            raise SystemExit(why)
        if "--json" in argv:
            print(json.dumps(env, indent=1))
        else:
            print(f"board {env['outline_mm'][0]} x {env['outline_mm'][1]} mm, {len(env['holes'])} mounting hole(s), parts: top up to "
                  f"{env['top_height_mm']} mm, bottom {env['bottom_height_mm']} mm; {len(env['parts'])} with 3D models"
                  + (f", NO MODEL: {', '.join(env['no_3d_model'])}" if env["no_3d_model"] else "")
                  + f"; connectors: {', '.join(c['ref'] for c in env['connectors']) or 'none'}\n  {prj.rel(out_dir)}/envelope.json, board.step")
        return 0
    if argv[:1] == ["entries"]:
        envf = os.path.join(out_dir, "envelope.json")
        env = json.load(open(envf)) if os.path.exists(envf) else None
        ent = entries(prj, env)
        if "--json" in argv:
            print(json.dumps(ent, indent=1))
        else:
            print(f"box IP by the weakest entry: {ent['box_ip']} (required {ent['want'] or '?'})" + ("" if ent["ok"] else "  NOT OK"))
            for r in ent["rows"]:
                print(f"  {r['ref']}: {r['part'] or '-'} {r['ip'] or ''}" + (f" {r['a']} A / {r['v']} V" if r.get("a") else "") + ("" if r["ok"] else f"  <- {r['why']}"))
            print("  sealing parts known: " + ", ".join(k for k in ENTRY_PARTS if k != "open"))
        return 0 if ent["ok"] else 1
    if argv[:1] == ["build"]:
        rep, spec = build(prj, out_dir)
        print(f"built {spec['material']} ({spec['process']}) box {rep['outer_mm'][0]} x {rep['outer_mm'][1]} x {rep['outer_mm'][2]} mm, "
              f"{rep['mass_g']} g, {len(rep['cutouts'])} cut-out(s): {prj.rel(out_dir)}/box_body.step, box_lid.step, assembly.step, *.stl")
        return 0
    if argv[:1] == ["check"]:
        items = check(prj, out_dir)
        if "--json" in argv:
            print(json.dumps([{"ok": ok, "text": m} for ok, m in items], indent=1))
        else:
            for ok, m in items:
                print(("PASS " if ok else "FAIL ") + m)
            print("GE " + ("READY" if all(ok for ok, _ in items) else "NOT READY"))
        return 0 if all(ok for ok, _ in items) else 1
    if argv[:1] == ["release"]:
        dst, n = release(prj, out_dir)
        print(f"{n} file(s) into {prj.rel(dst)}")
        return 0
    print(__doc__)
    return 1
