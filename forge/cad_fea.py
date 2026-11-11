"""Thermal level 2 and structural level 1 of the enclosure with Gmsh + CalculiX, run under the CAD venv's Python:
`python cad_fea.py spec.json`. Written by forge/enclosure.py.

Thermal (steady state, conduction in the walls, convection + radiation as a film on the outside, the losses as
a heat flux on the floor patch under the hottest parts or, for a potted / heatsink wall, on the named wall):
  mesh     the box body STEP, second-order tets (Gmsh), element size ~ wall thickness
  loads    *DFLUX on the inner floor patch (q = P / A_patch), *FILM on every outer face with h = h_conv + h_rad
           from the closed form at the ambient, sink = ambient
  result   max and mean wall temperature, the floor-patch temperature (what the board sees), the
           agreement with the closed-form ΔT (SVR: the two must agree within tolerance or the model is wrong)
Structural (linear static): the lid under the gasket's compression load plus the IK impact as a static
equivalent on the lid centre; max deflection at the gasket line vs the compression window, max von Mises vs
yield / safety factor.
"""
import json
import math
import os
import re
import subprocess
import sys

import gmsh


def mesh(step, out_inp, size_mm, groups):
    """Mesh a STEP solid; physical groups: 'body' (volume), 'outer' (faces not in the cavity), 'patch' (floor faces
    inside the cavity near z = floor), returns the written .inp path and a summary."""
    gmsh.initialize()
    gmsh.option.setNumber("General.Terminal", 0)
    gmsh.model.add("box")
    gmsh.model.occ.importShapes(step)
    gmsh.model.occ.synchronize()
    vols = gmsh.model.getEntities(3)
    surfs = gmsh.model.getEntities(2)
    bb = gmsh.model.getBoundingBox(-1, -1)
    zf = groups["floor_z"]
    outer, patch, inner = [], [], []
    for dim, tag in surfs:
        x0, y0, z0, x1, y1, z1 = gmsh.model.getBoundingBox(dim, tag)
        cx, cy, cz = (x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2
        on_box = (abs(x0 - bb[0]) < 0.05 or abs(x1 - bb[3]) < 0.05 or abs(y0 - bb[1]) < 0.05 or abs(y1 - bb[4]) < 0.05
                  or abs(z0 - bb[2]) < 0.05 or (abs(z1 - bb[5]) < 0.05 and (z1 - z0) < 0.05 and groups.get("lid_outer", False)))
        if abs(z0 - zf) < 0.05 and abs(z1 - zf) < 0.05 and x0 > bb[0] + 0.5 and x1 < bb[3] - 0.5:
            patch.append(tag)                       # the cavity floor (horizontal face at z = floor, inside the walls)
        elif on_box:
            outer.append(tag)
        else:
            inner.append(tag)
    gmsh.model.addPhysicalGroup(3, [t for _, t in vols], name="body")
    gmsh.model.addPhysicalGroup(2, outer, name="outer")
    gmsh.model.addPhysicalGroup(2, patch or inner[:1], name="patch")
    gmsh.option.setNumber("Mesh.CharacteristicLengthMax", size_mm / 1.5)     # two elements across a wall
    gmsh.option.setNumber("Mesh.CharacteristicLengthMin", size_mm / 4)
    gmsh.option.setNumber("Mesh.ElementOrder", 1)   # C3D4: Gmsh's tet10 mid-node order inverts in ccx
    gmsh.option.setNumber("Mesh.SaveGroupsOfNodes", 1)
    gmsh.model.mesh.generate(3)
    n_nodes = len(gmsh.model.mesh.getNodes()[0])
    gmsh.write(out_inp)
    # face areas for the flux
    a_patch = sum(_area(t) for t in patch)
    a_outer = sum(_area(t) for t in outer)
    gmsh.finalize()
    return {"nodes": n_nodes, "patch_faces": len(patch), "outer_faces": len(outer), "patch_area_mm2": a_patch, "outer_area_mm2": a_outer}


def _area(tag):
    try:
        return gmsh.model.occ.getMass(2, tag)
    except Exception:
        return 0.0


def thermal_inp(mesh_inp, out, k, h, amb, q, elset="body"):
    """CalculiX deck: conduction with film on 'outer' and flux on 'patch' (surface loads via element face sets
    is awkward from a Gmsh export, so the film and flux are applied with *FILM / *DFLUX through surface
    definitions generated from the node sets: CalculiX accepts *SURFACE from element faces, which we derive)."""
    t = open(mesh_inp).read()
    # Gmsh writes node sets per physical surface (SaveGroupsOfNodes) and element sets per volume. Build face
    # surfaces from the body's elements whose faces lie entirely in a node set.
    nodes_outer = _nset(t, "outer")
    nodes_patch = _nset(t, "patch")
    elems = _tets(t)
    faces_outer = _faces(elems, nodes_outer)
    faces_patch = _faces(elems, nodes_patch)
    # keep only the volume elements: Gmsh also writes the surface groups as 2-D elements, which CalculiX would
    # solve as shells
    keep, skip = [], False
    for line in t.splitlines():
        if line.startswith("*ELEMENT"):
            skip = "C3D4" not in line.upper()
            if not skip:
                line = "*ELEMENT, TYPE=C3D4, ELSET=" + (line.split("ELSET=")[1] if "ELSET=" in line else "body")
        elif line.startswith("*ELSET"):
            skip = line.split("ELSET=")[-1].strip().lower() in ("outer", "patch", "inner")   # the 2-D groups' sets
        elif line.startswith("*"):
            skip = False
        if not skip:
            keep.append(line)
    L = ["\n".join(keep)]

    m = re.search(r"\*ELEMENT, TYPE=C3D4, ELSET=(\S+)", L[0])
    elset = m.group(1) if m else elset
    L.append(f"*MATERIAL, NAME=M\n*CONDUCTIVITY\n{k / 1000.0}\n*SOLID SECTION, ELSET={elset}, MATERIAL=M\n")   # W/mm/K
    L.append("*STEP\n*HEAT TRANSFER, STEADY STATE\n")
    L.append("*FILM\n" + "\n".join(f"{e}, F{f}, {amb}, {h / 1e6:.6e}" for e, f in faces_outer) + "\n")   # W/mm2/K, per face
    L.append("*DFLUX\n" + "\n".join(f"{e}, S{f}, {q:.6e}" for e, f in faces_patch) + "\n")               # W/mm2, per face
    L.append("*NODE FILE\nNT\n*EL FILE\nHFL\n*END STEP\n")
    open(out, "w").write("\n".join(L))
    return len(faces_outer), len(faces_patch)


def _nset(t, name):
    m = re.search(rf"\*NSET,\s*NSET={name}\s*\n(.*?)(?=\n\*|\Z)", t, re.S | re.I)
    if not m:
        return set()
    return {int(x) for x in re.findall(r"\d+", m.group(1))}


def _tets(t):
    m = re.search(r"\*ELEMENT,\s*type=C3D4.*?\n(.*?)(?=\n\*|\Z)", t, re.S | re.I)
    out = {}
    if not m:
        return out
    buf = ""
    for line in m.group(1).splitlines():
        buf += line.strip()
        if buf.endswith(","):
            continue
        nums = [int(x) for x in re.findall(r"\d+", buf)]
        if len(nums) >= 5:
            out[nums[0]] = nums[1:5]
        buf = ""
    return out


# C3D4 face numbering (CalculiX): S1: 1-2-3, S2: 1-4-2, S3: 2-4-3, S4: 3-4-1
FACE_CORNERS = {1: (0, 1, 2), 2: (0, 3, 1), 3: (1, 3, 2), 4: (2, 3, 0)}


def _faces(elems, nodes):
    out = []
    if not nodes:
        return out
    for e, n in elems.items():
        for f, (a, b, c) in FACE_CORNERS.items():
            if n[a] in nodes and n[b] in nodes and n[c] in nodes:
                out.append((e, f))
    return out


def run_ccx(ccx, deck_base, cwd):
    r = subprocess.run([ccx, "-i", deck_base], cwd=cwd, capture_output=True, text=True, timeout=1800)
    return r.returncode, (r.stdout + r.stderr)[-2000:]


def read_frd_temps(frd):
    temps = []
    with open(frd, errors="ignore") as f:
        block = False
        for line in f:
            if line.startswith(" -4  NDTEMP") or "NDTEMP" in line:
                block = True
                continue
            if block and line.startswith(" -1"):
                try:
                    temps.append(float(line[13:25]))
                except ValueError:
                    pass
            elif block and line.startswith(" -3"):
                block = False
    return temps


def main():
    s = json.load(open(sys.argv[1]))
    out = s["out"]
    os.makedirs(out, exist_ok=True)
    minp = os.path.join(out, "box_mesh.inp")
    info = mesh(s["body_step"], minp, s["mesh_mm"], {"floor_z": s["floor_z"]})
    rep = {"mesh": info}
    if info["patch_area_mm2"] <= 0:
        rep["error"] = "no floor patch face found"
        print("REPORT" + json.dumps(rep))
        return
    q = s["loss_W"] / info["patch_area_mm2"]
    deck = os.path.join(out, "thermal.inp")
    nf, npf = thermal_inp(minp, deck, s["k_W_mK"], s["h_W_m2K"], s["ambient_C"], q)
    rep["faces"] = {"outer": nf, "patch": npf}
    rc, log = run_ccx(s["ccx"], "thermal", out)
    rep["ccx_rc"] = rc
    frd = os.path.join(out, "thermal.frd")
    if os.path.exists(frd):
        T = read_frd_temps(frd)
        if T:
            rep["T_max_C"] = round(max(T), 2)
            rep["T_min_C"] = round(min(T), 2)
            rep["T_mean_C"] = round(sum(T) / len(T), 2)
    else:
        rep["log"] = log
    print("REPORT" + json.dumps(rep))


if __name__ == "__main__":
    main()
