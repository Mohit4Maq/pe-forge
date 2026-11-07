"""forge pcb: board-side helpers that KiCad 10's command line does not have. No model calls, no network.

  forge pcb sync [--board B.kicad_pcb] [--prune] [--dry-run]
      Update the board from the schematic, like KiCad's "Update PCB from Schematic" (F8), headless:
      footprints added or swapped (position kept), reference / value / fields / symbol link set, every pad's net
      set from KiCad's own netlist. New footprints land in a staging grid right of the board outline. Footprints
      no longer in the schematic are reported, and removed only with --prune. Board-only items (mounting
      holes, fiducials, logos: footprints with no symbol link) are never touched. Afterwards DRC with schematic
      parity should show no parity issue.

  forge pcb setup
      Board set-up from project.toml [pcb]: outline, mounting holes (marked "not in schematic"), copper layer
      count and stack-up (copper weight per layer), minimum hole, values moved to the fab layer.
  forge pcb place <placement.json> [--dry-run]
      Place parts from a table {"REF": [x_mm, y_mm, rot_deg], ...}, x / y relative to the board origin, then report
      courtyard overlaps from KiCad's DRC. Keep the table with the project (08_PCB/<board>/placement.json) and
      note the datasheet rule behind each critical position (project Rule 11) in the layout spec.

project.toml
  [pcb]
  outline_mm = [50, 50]          # width, height
  origin_mm = [100, 100]         # top-left corner on KiCad's page
  copper_oz = [2, 1, 1, 2]       # one entry per copper layer, top to bottom (4 entries = 4 layers)
  thickness_mm = 1.6
  min_hole_mm = 0.2              # thermal-via footprints use 0.2 mm
  hole_footprint = "MountingHole:MountingHole_3.2mm_M3"
  hole_inset_mm = 3.5            # hole centres from each corner; 0 or absent = no holes
  fiducials = [[6, 25], [44, 46]]   # board-only fiducials (mm from the origin), Fiducial:Fiducial_1mm_Mask2mm
  marking = [{ t = "BUCK48TO12 REV A", x = 25, y = 2, size = 1.0 }]   # silkscreen text (re-runs replace it)

  forge pcb zones <zones.json>
      Copper pours as data: [{"name", "layer" (F.Cu / In1.Cu / ...), "net", "rect": [x1, y1, x2, y2] or "pts":
      [[x, y], ...], "priority": n}], mm from the board origin. Pours named forge:<name> are replaced on a re-run;
      pours drawn by hand are kept. All zones are filled afterwards.
  forge pcb route <routes.json>
      Tracks and vias as data: {"tracks": [{"net", "layer", "w", "pts": [[x, y], ...]}], "vias": [{"net", "at":
      [x, y], "d": 0.6, "drill": 0.3}]}. They go in the board group "forge_route", which a re-run replaces; hand
      routing outside the group is never touched. Zones are re-filled and DRC reports unconnected items.
  forge pcb rules [--json]
      The layout rules the board is reviewed against: every row whose first cell is an L-number (L-01, L-02, ...)
      in the layout spec (project.toml paths.layout_spec).
  forge pcb review-new <placement|routing|release> [--by NAME]
      Write the review form 01_Program/QC/Reviews/PCB_<milestone>_<date>.md: one row per L-rule (verdict PASS /
      FAIL / DEVIATION-OK, evidence), the board's content hash, the spec revision. The independent reviewer
      (agent pcb-reviewer) fills it; it never edits the board.
  forge pcb review-status [--json]
      PASS only when the latest PCB review covers every L-rule, no rule is FAIL or unanswered, and the board has
      not changed since (hash). Gates G7 / G8 and `forge check` (pcb_review) run it.

The board is changed through KiCad's own Python (pcbnew), so the file stays KiCad's format. Close the board in
KiCad first: KiCad would overwrite the change on its next save.
"""
import json
import os
import re
import subprocess
import sys
import tempfile

from . import kicad

# KiCad renames these legacy simulation fields when it loads a schematic; the footprint must carry the new name
# or schematic parity reports a missing field on every part
LEGACY_FIELDS = {"Sim_Device": "Sim.Device", "Sim_Type": "Sim.Type", "Sim_Pins": "Sim.Pins", "Sim_Params": "Sim.Params"}
SKIP_FIELDS = {"Footprint"}                 # the footprint itself is the FPID


def lib_dirs():
    """{nickname: .pretty dir} from KiCad's global footprint table (with ${VAR} expanded) plus KiCad's stock
    footprints."""
    out = {}
    stock = kicad.stock_footprints()
    if stock and os.path.isdir(stock):
        for d in os.listdir(stock):
            if d.endswith(".pretty"):
                out[d[:-7]] = os.path.join(stock, d)
    env = dict(os.environ)
    for base in reversed(glob_kicad_prefs()):         # newest version first (sorted by number: 10.0 > 9.0)
        try:
            common = json.load(open(os.path.join(base, "kicad_common.json")))
            env = dict((common.get("environment", {}).get("vars") or {}), **env)
        except (OSError, ValueError):
            pass
        t = os.path.join(base, "fp-lib-table")
        if os.path.exists(t):
            for name, uri in re.findall(r'\(lib \(name "([^"]+)"\)\s*\(type "[^"]+"\)\s*\(uri "([^"]+)"\)', open(t).read()):
                uri = re.sub(r"\$\{(\w+)\}", lambda m: env.get(m.group(1), m.group(0)), uri)
                out.setdefault(name, uri)
        break                                    # the newest KiCad version's preferences only
    return out


def glob_kicad_prefs():
    roots = [os.path.expanduser("~/Library/Preferences/kicad"), os.path.expanduser("~/.config/kicad")]
    out = []
    for r in roots:
        if os.path.isdir(r):
            out += [os.path.join(r, v) for v in os.listdir(r) if re.match(r"^\d+\.\d+$", v)]
    return sorted(out, key=lambda p: tuple(int(x) for x in os.path.basename(p).split(".")))


def components(text):
    """[{ref, value, fpid, path, fields, empty, dnp, no_bom, no_board, pads{pin: net}}] from a kicadsexpr netlist."""
    tree = kicad.parse_sexpr(text)
    comps = {}
    for c in (kicad._get(tree, "components") or [])[1:]:
        fields = {}
        for f in (kicad._get(c, "fields") or [])[1:]:
            fields[kicad._val(f, "name")] = next((x for x in f[1:] if isinstance(x, str)), "")
        props = {kicad._val(p, "name") for p in c if isinstance(p, list) and p and p[0] == "property"}
        sp = kicad._get(c, "sheetpath")
        sheet = kicad._val(sp, "tstamps", "/") if sp else "/"
        stamps = [x[1] for x in c if isinstance(x, list) and x and x[0] == "tstamps" and len(x) > 1]
        ref = kicad._val(c, "ref")
        comps[ref] = {"ref": ref, "value": kicad._val(c, "value"), "fpid": kicad._val(c, "footprint"),
                      "path": sheet.rstrip("/") + "/" + (stamps[0] if stamps else ""),
                      "fields": {LEGACY_FIELDS.get(k, k): v for k, v in fields.items() if k not in SKIP_FIELDS},
                      "dnp": "dnp" in props, "no_bom": "exclude_from_bom" in props,
                      "no_board": "exclude_from_board" in props, "pads": {}}
    for n in (kicad._get(tree, "nets") or [])[1:]:
        name = kicad._val(n, "name").replace("{slash}", "/")
        for nd in n:
            if isinstance(nd, list) and nd and nd[0] == "node":
                ref, pin = kicad._val(nd, "ref"), kicad._val(nd, "pin")
                if ref in comps:
                    comps[ref]["pads"][pin] = name
    return [c for c in comps.values() if not c["no_board"] and not c["ref"].startswith("#")]


SYNC = r'''
import json, sys, pcbnew
KEEP = []      # items removed from the board: KiCad 10 frees them (dangling pointers) if Python drops them
spec = json.load(open(sys.argv[1]))
b = pcbnew.LoadBoard(spec["board"])
mm = pcbnew.FromMM
nets = {}
def net(name):
    if name not in nets:
        ni = b.FindNet(name)
        if not ni:
            ni = pcbnew.NETINFO_ITEM(b, name); b.Add(ni)
        nets[name] = ni
    return nets[name]
live = {c["path"] for c in spec["comps"]}
by_path, by_ref = {}, {}
for f in b.GetFootprints():
    p = f.GetPath().AsString()
    if p:
        by_path[p] = f
    by_ref[f.GetReference()] = f
box = b.GetBoardEdgesBoundingBox()
sx = (box.GetRight() if box.GetWidth() > 0 else 0) + mm(15)
sy = box.GetTop() if box.GetWidth() > 0 else 0
report = {"added": [], "swapped": [], "updated": 0, "orphans": [], "removed": [], "missing_fp": []}
seen = set()
k = 0
for c in spec["comps"]:
    lib, _, name = c["fpid"].partition(":")
    f = by_path.get(c["path"])
    if f is None and c["ref"] in by_ref and by_ref[c["ref"]].GetPath().AsString() not in live:
        # no footprint carries this symbol's id, but one has its reference and a link to a symbol that no longer
        # exists (sheet redrawn, symbol replaced) or no link (placed by hand): adopt it, keeping its position
        f = by_ref[c["ref"]]
    want_new = f is None or f.GetFPIDAsString() != c["fpid"]
    if want_new:
        d = spec["libs"].get(lib)
        nf = pcbnew.FootprintLoad(d, name) if d else None
        if nf is None:
            report["missing_fp"].append(f'{c["ref"]}: {c["fpid"]}')
            if f is not None:
                seen.add(f.GetPath().AsString())
            continue
        if f is not None:                     # footprint changed: keep where it was
            nf.SetPosition(f.GetPosition()); nf.SetOrientation(f.GetOrientation())
            if f.IsFlipped(): nf.Flip(nf.GetPosition(), False)
            KEEP.append(f); b.Remove(f); report["swapped"].append(c["ref"])
        else:
            nf.SetPosition(pcbnew.VECTOR2I(sx + mm(12) * (k % 6), sy + mm(12) * (k // 6))); k += 1
            report["added"].append(c["ref"])
        nf.SetFPID(pcbnew.LIB_ID(lib, name))
        b.Add(nf); f = nf
    else:
        report["updated"] += 1
    f.SetReference(c["ref"]); f.SetValue(c["value"])
    f.SetPath(pcbnew.KIID_PATH(c["path"]))
    seen.add(c["path"])
    for kf, v in c["fields"].items():
        f.SetField(kf, v)
    for fld in f.GetFields():
        if fld.GetName() not in ("Reference", "Value") and fld.GetName() in c["fields"]:
            fld.SetVisible(False)
    f.SetDNP(c["dnp"]); f.SetExcludedFromBOM(c["no_bom"])
    for p in f.Pads():
        n = c["pads"].get(p.GetNumber())
        if n:
            p.SetNet(net(n))
        elif p.GetNumber():
            p.SetNetCode(0)
for f in list(b.GetFootprints()):
    if f.GetPath().AsString() in seen or not f.GetPath().AsString():
        continue                               # board-only items (holes, logos) carry no symbol link
    if spec["prune"]:
        report["removed"].append(f.GetReference()); KEEP.append(f); b.Remove(f)
    else:
        report["orphans"].append(f.GetReference())
if not spec["dry"]:
    b.Save(spec["board"])
print("REPORT" + json.dumps(report))
'''


def sync(sch, board, prune=False, dry=False):
    cli, py = kicad.find_cli(), kicad.find_python()
    if not (cli and py):
        raise SystemExit("kicad-cli and KiCad's Python (pcbnew) are needed (forge doctor)")
    for lock in (os.path.join(os.path.dirname(board), "~" + os.path.basename(board) + ".lck"),):
        if os.path.exists(lock):
            raise SystemExit(f"{os.path.basename(board)} is open in KiCad (lock file): close it first")
    out = kicad._out(sch, None, ".sync.net")
    os.remove(out)
    subprocess.run([cli, "sch", "export", "netlist", "--format", "kicadsexpr", "-o", out, sch], capture_output=True)
    if not os.path.exists(out):
        raise SystemExit("netlist export failed")
    comps = components(open(out).read())
    os.remove(out)
    d = tempfile.mkdtemp(prefix="pe-forge-sync-")
    spec = os.path.join(d, "sync.json")
    json.dump({"board": os.path.abspath(board), "comps": comps, "libs": lib_dirs(), "prune": prune, "dry": dry},
              open(spec, "w"))
    script = os.path.join(d, "sync.py")
    open(script, "w").write(SYNC)
    with keep_project(board):
        r = subprocess.run([py, script, spec], capture_output=True, text=True)
    m = re.search(r"REPORT(\{.*\})", r.stdout)
    if not m:
        raise SystemExit("pcbnew sync failed: " + (r.stderr or r.stdout)[-600:])
    return json.loads(m.group(1)), len(comps)


SETUP = r'''
import json, sys, pcbnew
KEEP = []      # items removed from the board: KiCad 10 frees them (dangling pointers) if Python drops them
s = json.load(open(sys.argv[1]))
b = pcbnew.LoadBoard(s["board"])
mm = pcbnew.FromMM
X0, Y0 = s["origin"]; W, H = s["outline"]
for d in list(b.GetDrawings()):
    if d.GetLayer() == pcbnew.Edge_Cuts:
        KEEP.append(d); b.Remove(d)
r = pcbnew.PCB_SHAPE(b); r.SetShape(pcbnew.SHAPE_T_RECT); r.SetLayer(pcbnew.Edge_Cuts); r.SetWidth(mm(0.1))
r.SetStart(pcbnew.VECTOR2I(mm(X0), mm(Y0))); r.SetEnd(pcbnew.VECTOR2I(mm(X0 + W), mm(Y0 + H))); b.Add(r)
have = {f.GetReference(): f for f in b.GetFootprints()}
if s["hole_dir"] and s["inset"]:
    i_ = s["inset"]
    for i, (hx, hy) in enumerate(((i_, i_), (W - i_, i_), (i_, H - i_), (W - i_, H - i_)), 1):
        f = have.get(f"H{i}")
        if f is None:
            f = pcbnew.FootprintLoad(s["hole_dir"], s["hole_name"]); f.SetReference(f"H{i}"); b.Add(f)
        f.SetPosition(pcbnew.VECTOR2I(mm(X0 + hx), mm(Y0 + hy)))
if s["fid_dir"]:
    for i, (fx, fy) in enumerate(s["fiducials"], 1):
        ref = f"FID{i}"
        f = have.get(ref)
        if f is None:
            f = pcbnew.FootprintLoad(s["fid_dir"], s["fid_name"]); f.SetReference(ref); b.Add(f)
        f.SetPosition(pcbnew.VECTOR2I(mm(X0 + fx), mm(Y0 + fy)))
for d in list(b.GetDrawings()):                     # board marking text written by a previous setup
    if isinstance(d, pcbnew.PCB_TEXT) and d.GetText().startswith(tuple(s["mark_keys"])):
        KEEP.append(d); b.Remove(d)
for m in s["marking"]:
    tx = pcbnew.PCB_TEXT(b); tx.SetText(m["t"]); tx.SetLayer(b.GetLayerID(m.get("layer", "F.SilkS")))
    tx.SetPosition(pcbnew.VECTOR2I(mm(X0 + m["x"]), mm(Y0 + m["y"]))); sz = mm(m.get("size", 1.0))
    tx.SetTextSize(pcbnew.VECTOR2I(sz, sz)); tx.SetTextThickness(int(sz * 0.15)); tx.SetTextAngleDegrees(m.get("rot", 0))
    b.Add(tx)
for f in b.GetFootprints():
    if not f.GetPath().AsString():                  # board-only parts: holes, logos, fiducials
        f.SetBoardOnly(True); f.SetExcludedFromBOM(True); f.SetExcludedFromPosFiles(True)
    f.Value().SetLayer(pcbnew.F_Fab if not f.IsFlipped() else pcbnew.B_Fab)
ds = b.GetDesignSettings()
ds.SetCopperLayerCount(s["layers"])
ds.m_MinThroughDrill = mm(s["min_hole"])
ds.SetBoardThickness(mm(s["thickness"]))
b.Save(s["board"])
print("OK")
'''

PLACE = r'''
import json, sys, pcbnew
KEEP = []      # items removed from the board: KiCad 10 frees them (dangling pointers) if Python drops them
s = json.load(open(sys.argv[1]))
b = pcbnew.LoadBoard(s["board"])
mm = pcbnew.FromMM
X0, Y0 = s["origin"]
done, missing = [], []
refs = {f.GetReference(): f for f in b.GetFootprints()}
for ref, (x, y, rot) in s["place"].items():
    f = refs.get(ref)
    if f is None:
        missing.append(ref); continue
    f.SetPosition(pcbnew.VECTOR2I(mm(X0 + x), mm(Y0 + y))); f.SetOrientationDegrees(rot); done.append(ref)
if not s["dry"]:
    b.Save(s["board"])
print("REPORT" + json.dumps({"placed": done, "missing": missing,
      "unplaced": sorted(r for r, f in refs.items() if r not in s["place"] and f.GetPath().AsString())}))
'''


ZONES = r'''
import json, sys, pcbnew
KEEP = []      # items removed from the board: KiCad 10 frees them (dangling pointers) if Python drops them
s = json.load(open(sys.argv[1]))
b = pcbnew.LoadBoard(s["board"])
mm = pcbnew.FromMM
X0, Y0 = s["origin"]
for z in list(b.Zones()):
    if z.GetZoneName().startswith("forge:"):
        KEEP.append(z); b.Remove(z)
missing = []
for d in s["zones"]:
    lay = b.GetLayerID(d["layer"])
    ni = b.FindNet(d["net"])
    if ni is None:
        missing.append(d["net"]); continue
    z = pcbnew.ZONE(b)
    z.SetLayer(lay); z.SetNet(ni); z.SetZoneName("forge:" + d["name"]); z.SetAssignedPriority(d.get("priority", 0))
    pts = d.get("pts") or [[d["rect"][0], d["rect"][1]], [d["rect"][2], d["rect"][1]], [d["rect"][2], d["rect"][3]], [d["rect"][0], d["rect"][3]]]
    ol = z.Outline(); ol.NewOutline()
    for x, y in pts:
        ol.Append(mm(X0 + x), mm(Y0 + y))
    z.SetMinThickness(mm(d.get("min_width", 0.25)))
    if d.get("solid"):
        z.SetPadConnection(pcbnew.ZONE_CONNECTION_FULL)
    b.Add(z)
pcbnew.ZONE_FILLER(b).Fill(b.Zones())
b.Save(s["board"])
print("REPORT" + json.dumps({"zones": len(s["zones"]) - len(missing), "missing_nets": missing}))
'''

ROUTE = r'''
import json, sys, pcbnew
KEEP = []      # items removed from the board: KiCad 10 frees them (dangling pointers) if Python drops them
s = json.load(open(sys.argv[1]))
b = pcbnew.LoadBoard(s["board"])
mm = pcbnew.FromMM
X0, Y0 = s["origin"]
P = lambda xy: pcbnew.VECTOR2I(mm(X0 + xy[0]), mm(Y0 + xy[1]))
for g in list(b.Groups()):
    if g.GetName() == "forge_route":
        for it in list(g.GetItems()):
            KEEP.append(it); b.Remove(it)
        KEEP.append(g); b.Remove(g)
g = pcbnew.PCB_GROUP(b); g.SetName("forge_route"); b.Add(g)
missing, n_t, n_v = [], 0, 0
for t in s["route"].get("tracks", []):
    ni = b.FindNet(t["net"])
    if ni is None:
        missing.append(t["net"]); continue
    for a, c in zip(t["pts"], t["pts"][1:]):
        tr = pcbnew.PCB_TRACK(b); tr.SetStart(P(a)); tr.SetEnd(P(c)); tr.SetWidth(mm(t.get("w", 0.25)))
        tr.SetLayer(b.GetLayerID(t.get("layer", "F.Cu"))); tr.SetNet(ni); b.Add(tr); g.AddItem(tr); n_t += 1
for v in s["route"].get("vias", []):
    ni = b.FindNet(v["net"])
    if ni is None:
        missing.append(v["net"]); continue
    via = pcbnew.PCB_VIA(b); via.SetPosition(P(v["at"])); via.SetWidth(mm(v.get("d", 0.6))); via.SetDrill(mm(v.get("drill", 0.3)))
    via.SetNet(ni); b.Add(via); g.AddItem(via); n_v += 1
pcbnew.ZONE_FILLER(b).Fill(b.Zones())
b.Save(s["board"])
print("REPORT" + json.dumps({"tracks": n_t, "vias": n_v, "missing_nets": sorted(set(missing))}))
'''


def _data_cmd(prj, board, script, key, path):
    board_only(board)
    cfg = prj.cfg.get("pcb", {})
    data = json.load(open(path))
    out, err = _run(script, {"board": os.path.abspath(board), "origin": cfg.get("origin_mm", [100, 100]), key: data})
    m = re.search(r"REPORT(\{.*\})", out)
    if not m:
        raise SystemExit("pcbnew failed: " + (err or out)[-800:])
    rep = json.loads(m.group(1))
    r = kicad.drc(board, parity=True)
    rep["drc_errors"] = [f"{v['type']}: {v['description']} [{', '.join(i['description'][:40] for i in v['items'])}]"
                         for v in r.violations]
    rep["unconnected"] = len(r.unconnected)
    rep["parity"] = len(r.parity)
    return rep


def stackup_block(copper_oz, thickness):
    """KiCad (stackup ...) text: copper per layer from oz (1 oz = 35 um), FR-4 dielectrics sharing the rest of
    the thickness (prepreg 0.2 mm next to each outer layer; ESTIMATE until the fab's stack-up is chosen)."""
    names = ["F.Cu"] + [f"In{i}.Cu" for i in range(1, len(copper_oz) - 1)] + ["B.Cu"]
    cu = [round(0.035 * oz, 4) for oz in copper_oz]
    n_d = len(cu) - 1
    rest = thickness - sum(cu) - 0.02
    outer = 0.2 if n_d > 1 else rest
    core = (rest - 2 * outer) / max(n_d - 2, 1) if n_d > 2 else (rest - outer if n_d == 2 else rest)
    L = ['\t\t(stackup', '\t\t\t(layer "F.SilkS" (type "Top Silk Screen"))', '\t\t\t(layer "F.Paste" (type "Top Solder Paste"))',
         '\t\t\t(layer "F.Mask" (type "Top Solder Mask") (thickness 0.01))']
    for i, (nm, c) in enumerate(zip(names, cu)):
        L.append(f'\t\t\t(layer "{nm}" (type "copper") (thickness {c:g}))')
        if i < n_d:
            th, kind = (outer, "prepreg") if i in (0, n_d - 1) and n_d > 1 else (core, "core")
            L.append(f'\t\t\t(layer "dielectric {i + 1}" (type "{kind}") (thickness {th:.3f}) (material "FR4") '
                     '(epsilon_r 4.5) (loss_tangent 0.02))')
    L += ['\t\t\t(layer "B.Mask" (type "Bottom Solder Mask") (thickness 0.01))',
          '\t\t\t(layer "B.Paste" (type "Bottom Solder Paste"))', '\t\t\t(layer "B.SilkS" (type "Bottom Silk Screen"))',
          '\t\t\t(copper_finish "None")', '\t\t\t(dielectric_constraints no)', '\t\t)']
    return "\n".join(L) + "\n"


def write_stackup(board, copper_oz, thickness):
    t = open(board).read()
    t = re.sub(r"\n\t\t\(stackup\n.*?\n\t\t\)\n", "\n", t, count=1, flags=re.S)
    t = re.sub(r"(\n\t\(setup\n)", lambda m: m.group(1) + stackup_block(copper_oz, thickness), t, count=1)
    open(board, "w").write(t)


def keep_project(board):
    """Context manager: KiCad's Python rewrites <board>.kicad_pro (net classes, patterns, rules) when it saves the
    board; the project file is the owner's, so it is put back byte for byte afterwards."""
    import contextlib

    @contextlib.contextmanager
    def cm():
        pro = os.path.splitext(board)[0] + ".kicad_pro"
        saved = open(pro, "rb").read() if os.path.exists(pro) else None
        try:
            yield
        finally:
            if saved is not None:
                open(pro, "wb").write(saved)
    return cm()


def _run(script, spec):
    py = kicad.find_python()
    if not py:
        raise SystemExit("KiCad's Python (pcbnew) is needed (forge doctor)")
    d = tempfile.mkdtemp(prefix="pe-forge-pcb-")
    sp, sc = os.path.join(d, "spec.json"), os.path.join(d, "run.py")
    json.dump(spec, open(sp, "w"))
    open(sc, "w").write(script)
    with keep_project(spec["board"]):
        r = subprocess.run([py, sc, sp], capture_output=True, text=True)
    return r.stdout, r.stderr


def board_only(board):
    for lock in (os.path.join(os.path.dirname(board), "~" + os.path.basename(board) + ".lck"),):
        if os.path.exists(lock):
            raise SystemExit(f"{os.path.basename(board)} is open in KiCad (lock file): close it first")


def setup(prj, board):
    cfg = prj.cfg.get("pcb", {})
    if not cfg.get("outline_mm"):
        raise SystemExit("project.toml [pcb] outline_mm is not set (see forge pcb --help)")
    board_only(board)
    oz = cfg.get("copper_oz", [1, 1])
    lib, _, name = cfg.get("hole_footprint", "MountingHole:MountingHole_3.2mm_M3").partition(":")
    flib, _, fname = cfg.get("fiducial_footprint", "Fiducial:Fiducial_1mm_Mask2mm").partition(":")
    marking = cfg.get("marking", [])
    spec = {"fiducials": cfg.get("fiducials", []), "fid_dir": lib_dirs().get(flib) if cfg.get("fiducials") else None,
            "fid_name": fname, "marking": marking, "mark_keys": [m["t"] for m in marking],
            "board": os.path.abspath(board), "outline": cfg["outline_mm"], "origin": cfg.get("origin_mm", [100, 100]),
            "layers": len(oz), "min_hole": cfg.get("min_hole_mm", 0.2), "thickness": cfg.get("thickness_mm", 1.6),
            "hole_dir": lib_dirs().get(lib), "hole_name": name, "inset": cfg.get("hole_inset_mm", 0)}
    out, err = _run(SETUP, spec)
    if "OK" not in out:
        raise SystemExit("pcbnew set-up failed: " + (err or out)[-600:])
    write_stackup(board, oz, spec["thickness"])
    return spec


def place(prj, board, table, dry=False):
    cfg = prj.cfg.get("pcb", {})
    board_only(board)
    data = json.load(open(table))
    pl = {k: v for k, v in data.items() if not k.startswith("_")}
    out, err = _run(PLACE, {"board": os.path.abspath(board), "origin": cfg.get("origin_mm", [100, 100]),
                            "place": pl, "dry": dry})
    m = re.search(r"REPORT(\{.*\})", out)
    if not m:
        raise SystemExit("pcbnew placement failed: " + (err or out)[-600:])
    rep = json.loads(m.group(1))
    if not dry:
        r = kicad.drc(board, parity=False)
        rep["overlaps"] = [" / ".join(i["ref"] or i["description"] for i in v["items"])
                           for v in r.violations + r.warnings if v["type"] == "courtyards_overlap"]
    return rep


# ------------------------------------------------------------------ independent review against the rule set
RULE_ROW = re.compile(r"^\|\s*(L-\d+[A-Za-z]?)\s*\|\s*(.*?)\s*\|")
VERDICTS = ("PASS", "FAIL", "DEVIATION-OK", "N/A")


def rules(prj):
    """[(id, text)] from the layout spec, in order."""
    spec = prj.p("layout_spec")
    if not os.path.exists(spec):
        return []
    out = []
    for line in open(spec):
        m = RULE_ROW.match(line)
        if m and m.group(1) not in [i for i, _ in out]:
            out.append((m.group(1), m.group(2)))
    return out


def spec_rev(prj):
    m = re.search(r"^rev:\s*(\S+)", open(prj.p("layout_spec")).read(), re.M) if os.path.exists(prj.p("layout_spec")) else None
    return m.group(1) if m else "?"


def board_hash(board):
    import hashlib
    t = open(board, "rb").read()
    # the content that matters: drop KiCad's volatile header lines (generator version, uuid of the file)
    t = re.sub(rb"\(generator_version \"[^\"]*\"\)", b"", t)
    return hashlib.sha256(t).hexdigest()[:16]


def reviews_dir(prj):
    return prj.p("reviews")


def review_new(prj, board, milestone, by="pcb-reviewer"):
    import datetime
    rs = rules(prj)
    if not rs:
        raise SystemExit(f"no L-rules in {prj.paths['layout_spec']}: write the layout spec first (90_Templates/T_Layout_Spec.md)")
    d = reviews_dir(prj)
    os.makedirs(d, exist_ok=True)
    day = datetime.date.today().isoformat()
    path = os.path.join(d, f"PCB_{milestone}_{day}.md")
    n = 2
    while os.path.exists(path):
        path = os.path.join(d, f"PCB_{milestone}_{day}_{n}.md"); n += 1
    rows = "\n".join(f"| {i} | {txt[:90]} | | |" for i, txt in rs)
    open(path, "w").write(f"""---
type: pcb-review
milestone: {milestone}
board: {prj.rel(board)}
board_sha: {board_hash(board)}
layout_spec_rev: {spec_rev(prj)}
reviewer: {by}
date: {day}
verdict: OPEN
---
# PCB review: {milestone}, {day}

Independent review against every rule of {prj.paths['layout_spec']} (rev {spec_rev(prj)}). Verdict per rule: PASS,
FAIL, DEVIATION-OK (deviation with the owner's OK, cited) or N/A (with the reason). Evidence: refs, nets,
coordinates in mm, the image or report used. The board is never edited by the reviewer.

## Rules
| Rule | Requirement | Verdict | Evidence |
|---|---|---|---|
{rows}

## Other findings (ranked Critical / Major / Minor)

## Verdict
Set `verdict:` above to READY, READY WITH FIXES or NOT READY.
""")
    return path


def latest_review(prj):
    d = reviews_dir(prj)
    if not os.path.isdir(d):
        return None
    fs = [os.path.join(d, f) for f in os.listdir(d) if f.startswith("PCB_") and f.endswith(".md")]
    return max(fs, key=os.path.getmtime) if fs else None


def review_status(prj, board):
    """(ok, [problem]) for the latest PCB review against the rule set and the current board."""
    rs = [i for i, _ in rules(prj)]
    if not rs:
        return False, [f"no L-rules in {prj.paths['layout_spec']}: there is nothing to review against"]
    f = latest_review(prj)
    if not f:
        return False, ["no independent PCB review yet (forge pcb review-new <milestone>, then the pcb-reviewer agent)"]
    t = open(f).read()
    probs = []
    got = {}
    for line in t.splitlines():
        m = re.match(r"^\|\s*(L-\d+[A-Za-z]?)\s*\|[^|]*\|\s*([^|]*?)\s*\|", line)
        if m:
            got[m.group(1)] = m.group(2).upper()
    missing = [i for i in rs if not got.get(i)]
    if missing:
        probs.append(f"rules without a verdict: {', '.join(missing)}")
    bad = [i for i in rs if got.get(i) and not got[i].startswith(VERDICTS)]
    if bad:
        probs.append(f"unknown verdicts on {', '.join(bad)} (use {', '.join(VERDICTS)})")
    failed = [i for i in rs if got.get(i, "").startswith("FAIL")]
    if failed:
        probs.append(f"rules FAIL: {', '.join(failed)}")
    m = re.search(r"^board_sha:\s*(\w+)", t, re.M)
    if not m or m.group(1) != board_hash(board):
        probs.append("the board changed after the review: review again")
    m = re.search(r"^verdict:\s*(.+)$", t, re.M)
    v = (m.group(1).strip() if m else "OPEN").upper()
    if v != "READY":           # READY WITH FIXES needs the fixes and a fresh review before a gate
        probs.append(f"review verdict {v}" + (": fix the findings and review again" if v.startswith("READY") else ""))
    return not probs, probs, os.path.relpath(f, prj.root)


def as_check_review(prj, opts):
    from .check import finding, result, roots, has_content
    pcbs = [b for b in roots(prj, "kicad_pcb") if has_content(b)]
    if not pcbs:
        return result("skip", reason="no board yet")
    ok, probs, *where = review_status(prj, pcbs[0])
    loc = where[0] if where else prj.paths["reviews"]
    return result("pass" if ok else "warn", [finding("independent PCB review covers every layout rule, board unchanged",
                                                      p, loc) for p in probs], reason=loc)


def gate_items(prj):
    from .check import roots
    pcbs = roots(prj, "kicad_pcb")
    if not pcbs:
        return [(False, "PCB review: no board")]
    ok, probs, *where = review_status(prj, pcbs[0])
    return [(ok, "independent PCB review against the layout rules" + (f" ({where[0]})" if where else "")
             + ("" if ok else ": " + "; ".join(probs)))]


def main(prj, argv):
    if argv[:1] == ["rules"]:
        rs = rules(prj)
        print(json.dumps(rs, indent=1) if "--json" in argv else
              "\n".join(f"{i}  {txt}" for i, txt in rs) or f"no L-rules in {prj.paths['layout_spec']}")
        return 0 if rs else 1
    if not argv or argv[0] not in ("sync", "setup", "place", "zones", "route", "review-new", "review-status"):
        print(__doc__)
        return 1
    from . import check
    board = argv[argv.index("--board") + 1] if "--board" in argv else None
    if not board:
        pcbs = check.roots(prj, "kicad_pcb")
        if not pcbs:
            raise SystemExit("no board file under 08_PCB")
        board = pcbs[0]
    if argv[0] in ("zones", "route"):
        if len(argv) < 2:
            raise SystemExit(f"forge pcb {argv[0]} <{argv[0]}.json>")
        rep = _data_cmd(prj, board, ZONES if argv[0] == "zones" else ROUTE, argv[0], argv[1])
        what = f"{rep['zones']} pour(s)" if argv[0] == "zones" else f"{rep['tracks']} track segment(s), {rep['vias']} via(s)"
        print(f"{what} written and zones filled" + (f"; nets not on the board: {', '.join(rep['missing_nets'])}" if rep["missing_nets"] else ""))
        print(f"  DRC: {len(rep['drc_errors'])} error(s), {rep['unconnected']} unconnected, parity {rep['parity']}")
        for e in rep["drc_errors"][:25]:
            print("   ", e)
        return 0 if not rep["drc_errors"] and not rep["missing_nets"] else 1
    if argv[0] == "review-new":
        if len(argv) < 2:
            raise SystemExit("forge pcb review-new <placement|routing|release>")
        by = argv[argv.index("--by") + 1] if "--by" in argv else "pcb-reviewer"
        print(prj.rel(review_new(prj, board, argv[1], by)))
        return 0
    if argv[0] == "review-status":
        ok, probs, *where = review_status(prj, board)
        if "--json" in argv:
            print(json.dumps({"ok": ok, "problems": probs, "review": where[0] if where else None}))
        else:
            print(("PASS" if ok else "OPEN") + (f" ({where[0]})" if where else "") + "".join(f"\n  {p}" for p in probs))
        return 0 if ok else 1
    if argv[0] == "setup":
        s = setup(prj, board)
        print(f"board set up: {s['outline'][0]:g} x {s['outline'][1]:g} mm, {s['layers']} copper layers "
              f"({'/'.join(str(x) for x in prj.cfg['pcb'].get('copper_oz', [1, 1]))} oz), min hole {s['min_hole']:g} mm"
              + (", 4 mounting holes" if s["inset"] else ""))
        return 0
    if argv[0] == "place":
        if len(argv) < 2:
            raise SystemExit("forge pcb place <placement.json>")
        rep = place(prj, board, argv[1], dry="--dry-run" in argv)
        print(f"placed {len(rep['placed'])} part(s)" + (f"; not on the board: {', '.join(rep['missing'])}" if rep["missing"] else "")
              + (f"; not in the table: {', '.join(rep['unplaced'])}" if rep["unplaced"] else ""))
        for o in rep.get("overlaps", []):
            print(f"  courtyard overlap: {o}")
        return 1 if rep.get("overlaps") or rep["missing"] else 0
    sch = os.path.splitext(board)[0] + ".kicad_sch"
    if not os.path.exists(sch):
        raise SystemExit(f"no schematic next to {board}")
    rep, n = sync(sch, board, prune="--prune" in argv, dry="--dry-run" in argv)
    print(f"{'would sync' if '--dry-run' in argv else 'synced'} {n} part(s) into {prj.rel(board) if prj else board}")
    for k in ("added", "swapped", "removed", "orphans", "missing_fp"):
        if rep[k]:
            label = {"orphans": "not in the schematic (kept; --prune removes)", "missing_fp": "footprint not found"}.get(k, k)
            print(f"  {label}: {', '.join(rep[k])}")
    print(f"  updated in place: {rep['updated']}")
    if not ("--dry-run" in argv):
        rpt = kicad.drc(board)
        print(f"  DRC schematic parity: {len(rpt.parity)} issue(s)" + "".join(f"\n    {v['description']}" for v in rpt.parity[:5]))
    return 1 if rep["missing_fp"] else 0
