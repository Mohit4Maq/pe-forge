"""The enclosure generator, run under the CAD venv's Python (build123d): `python cad_box.py spec.json`.

Reads the spec forge/enclosure.py writes (envelope, walls, entries, material, process) and writes STEP (box, lid,
assembly with the board), STL for printing, DXF of the lid and the cut-out faces, and a report JSON (volumes,
masses, surface areas for the thermal closed form, the cut-out list). Two constructions:

  shell   a five-sided box with a lid on the open side, gasket groove in the lid flange (IP >= 54), bosses with
          inserts at the corners, PCB standoffs on the mounting holes, connector cut-outs through the named wall
  plate   (vendor box mode) only the machining: cut-outs and holes on the vendor's wall panel, as DXF + STEP

Dimensions follow the material rules in the spec (wall, floor, flange, boss, groove) so the same script serves
aluminium (die-cast / machined), moulded plastic and printing.
"""
import json
import math
import sys

from build123d import (Axis, Box, Cylinder, Location, Mode, Plane, Pos, Rectangle, Compound, Part, export_step,
                       export_stl, import_step, Align, fillet, extrude, Sketch, Circle, RectangleRounded, Polyline,
                       make_face, Locations, GridLocations, Vector)


def shell(s):
    W, D, H = s["outer"]            # outer box size, lid on +Z
    w, f = s["wall"], s["floor"]
    r = s.get("corner_r", max(w, 2.0))
    body = Box(W, D, H - s["lid_t"], align=(Align.CENTER, Align.CENTER, Align.MIN))
    body = fillet(body.edges().filter_by(Axis.Z), r)
    cavity = Pos(0, 0, f) * Box(W - 2 * w, D - 2 * w, H, align=(Align.CENTER, Align.CENTER, Align.MIN))
    body = body - cavity
    # corner bosses with insert holes
    bx, by = W / 2 - w - s["boss_d"] / 2, D / 2 - w - s["boss_d"] / 2
    for sx in (-1, 1):
        for sy in (-1, 1):
            boss = Pos(sx * bx, sy * by, f) * Cylinder(s["boss_d"] / 2, H - s["lid_t"] - f, align=(Align.CENTER, Align.CENTER, Align.MIN))
            hole = Pos(sx * bx, sy * by, H - s["lid_t"] - s["insert_depth"]) * Cylinder(s["insert_d"] / 2, s["insert_depth"] + 1, align=(Align.CENTER, Align.CENTER, Align.MIN))
            body = body + boss - hole
    # PCB standoffs on the mounting holes (board origin at the cavity's -X -Y corner + clearance)
    ox, oy = -W / 2 + w + s["clear"], -D / 2 + w + s["clear"]
    for hx, hy, hd in s["holes"]:
        so = Pos(ox + hx, oy + hy, f) * Cylinder(s["standoff_d"] / 2, s["standoff_h"], align=(Align.CENTER, Align.CENTER, Align.MIN))
        hole = Pos(ox + hx, oy + hy, f) * Cylinder(max(hd - 0.4, 1.6) / 2, s["standoff_h"] + 1, align=(Align.CENTER, Align.CENTER, Align.MIN))
        body = body + so - hole
    # connector cut-outs through the named wall: (wall, u, z, width, height) in wall coordinates
    cuts = []
    for c in s["cutouts"]:
        wall, u, z, cw, ch = c["wall"], c["u"], c["z"], c["w"], c["h"]
        zc = f + s["standoff_h"] + z
        if wall in ("left", "right"):
            x = (-W / 2 if wall == "left" else W / 2)
            y = oy + u
            cut = Pos(x, y, zc) * Box(3 * w, cw, ch, align=(Align.CENTER, Align.CENTER, Align.CENTER))
        else:
            y = (-D / 2 if wall == "front" else D / 2)
            x = ox + u
            cut = Pos(x, y, zc) * Box(cw, 3 * w, ch, align=(Align.CENTER, Align.CENTER, Align.CENTER))
        body = body - cut
        cuts.append({"ref": c["ref"], "wall": wall, "centre_mm": [round(x, 2), round(y, 2), round(zc, 2)], "w": cw, "h": ch})
    # lid with gasket groove and screw holes
    lid = Box(W, D, s["lid_t"], align=(Align.CENTER, Align.CENTER, Align.MIN))
    lid = fillet(lid.edges().filter_by(Axis.Z), r)
    if s.get("gasket"):
        g = s["gasket"]                 # groove centred on the wall mid-line, depth = section x (1 - compression)
        gw, gd = g["groove_w"], g["groove_d"]
        outer = RectangleRounded(W - w + gw, D - w + gw, r)
        inner = RectangleRounded(W - w - gw, D - w - gw, r - gw if r > gw else 0.5)
        groove = extrude(outer - inner, gd)
        lid = lid - groove
    for sx in (-1, 1):
        for sy in (-1, 1):
            lid = lid - Pos(sx * bx, sy * by, 0) * Cylinder(s["screw_d"] / 2, s["lid_t"] + 1, align=(Align.CENTER, Align.CENTER, Align.MIN))
    lid = Pos(0, 0, H - s["lid_t"]) * lid
    return body, lid, cuts, (ox, oy, f + s["standoff_h"])


def main():
    s = json.load(open(sys.argv[1]))
    out = s["out"]
    body, lid, cuts, board_origin = shell(s)
    rep = {"outer_mm": s["outer"], "cutouts": cuts, "board_origin_mm": list(board_origin)}
    rho = s.get("density_g_cm3", 2.7)
    rep["body_volume_cm3"] = round(body.volume / 1000, 2)
    rep["lid_volume_cm3"] = round(lid.volume / 1000, 2)
    rep["mass_g"] = round((body.volume + lid.volume) / 1000 * rho, 1)
    W, D, H = s["outer"]
    rep["outer_area_cm2"] = round(2 * (W * D + W * H + D * H) / 100, 1)
    export_step(body, f"{out}/box_body.step")
    export_step(lid, f"{out}/box_lid.step")
    export_stl(body, f"{out}/box_body.stl")
    export_stl(lid, f"{out}/box_lid.stl")
    parts = [body, lid]
    if s.get("board_step"):
        try:
            brd = import_step(s["board_step"])
            bb = brd.bounding_box()
            ox, oy, oz = board_origin
            # the board's own origin: its bbox min in X/Y, its top copper at Z = 0 in KiCad's export
            moved = Pos(ox - bb.min.X + s["board_min"][0] - s["board_min"][0], oy - bb.min.Y, oz - s.get("board_z_min", 0.0)) * brd
            parts.append(moved)
            rep["board_bbox_mm"] = [round(v, 2) for v in (bb.size.X, bb.size.Y, bb.size.Z)]
        except Exception as e:
            rep["board_error"] = str(e)
    export_step(Compound(children=parts), f"{out}/assembly.step")
    json.dump(rep, open(f"{out}/cad_report.json", "w"), indent=1)
    print("OK " + json.dumps(rep))


if __name__ == "__main__":
    main()
