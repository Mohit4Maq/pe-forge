"""forge pcb: board-side helpers that KiCad 10's command line does not have. No model calls, no network.

  forge pcb sync [--board B.kicad_pcb] [--prune] [--dry-run]
      Update the board from the schematic, like KiCad's "Update PCB from Schematic" (F8), headless:
      footprints added or swapped (position kept), reference / value / fields / symbol link set, every pad's net
      set from KiCad's own netlist. New footprints land in a staging grid right of the board outline. Footprints
      no longer in the schematic are reported, and removed only with --prune. Board-only items (mounting
      holes, fiducials, logos: footprints with no symbol link) are never touched. Afterwards DRC with schematic
      parity should show no parity issue.

  forge pcb setup [--replace-outline]
      Board set-up from project.toml [pcb]: outline, mounting holes (marked "not in schematic"), copper layer
      count and stack-up (copper weight per layer), minimum hole, values moved to the fab layer.
  forge pcb place <placement.json> [--dry-run]
      Place parts from a table {"REF": [x_mm, y_mm, rot_deg] or [x, y, rot, {"ref": [dx, dy(, angle)],
      "no_zone_pads": true}], ...}, x / y relative to the board origin ("ref" moves the reference text,
      "edge": {"side": "left", "overhang": 0, "face_from_pin": 10.0, "source": "..."} declares an edge connector
      (Rule 13; face_from_pin = datasheet distance from the pin-1 row to the mating face);
      "no_zone_pads" keeps pours off the part's pads, e.g. Kelvin test points; "solid_pads": ["9"] or true joins
      those pads solidly where the datasheet asks, e.g. an exposed thermal pad), then report
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
  hole_keepout_mm = 3.5          # copper keep-out radius around each hole, all layers (screw head / standoff);
                                 # default 3.5 mm for M3 (5.5 mm head + margin); 0 = none
  fiducials = [[6, 25], [44, 46]]   # board-only fiducials (mm from the origin), Fiducial:Fiducial_1mm_Mask2mm
  marking = [{ t = "BUCK48TO12 REV A", x = 25, y = 2, size = 1.0 }]   # silkscreen text (re-runs replace it)
  copper_finish = "ENIG"         # written into the stack-up (fab reads it); "None" until chosen
                                 # fab notes: marking entries with layer = "Dwgs.User" (or "Cmts.User")

  forge pcb zones <zones.json>
      Copper pours as data: [{"name", "layer" (F.Cu / In1.Cu / ...), "net", "rect": [x1, y1, x2, y2] or "pts":
      [[x, y], ...], "priority": n}], mm from the board origin. Pours named forge:<name> are replaced on a re-run;
      pours drawn by hand are kept. Pads join through thermal relief (Rule 12) unless the zone says
      "pads": "solid" | "smd_solid" | "none" ("relief_gap", "spoke_width" in mm). All zones are filled afterwards. The file may instead be {"zones": [...],
      "stitch": {"net": "GND", "pitch": 4.0, "d": 0.6, "drill": 0.3, "avoid": [[x1, y1, x2, y2], ...]}}: after the
      fill, vias of that net on a grid wherever the net's copper covers the via on every copper layer (clear of
      other nets, pads and vias; avoid boxes for hot loops), in group forge_stitch, replaced on a re-run.
  forge pcb route <routes.json>
      Tracks and vias as data: {"tracks": [{"net", "layer", "w", "pts": [[x, y], ...]}], "vias": [{"net", "at":
      [x, y], "d": 0.6, "drill": 0.3}]}. They go in the board group "forge_route", which a re-run replaces; hand
      routing outside the group is never touched. Zones are re-filled and DRC reports unconnected items.
  forge pcb crosstalk
      Rule 14: signal tracks (width <= [pcb] signal_max_width_mm, 0.4) per layer by direction; adjacent signal layers
      with no plane between must be orthogonal (one mostly H, the other mostly V, >= 70 %), no broadside parallel
      runs between them; [pcb] layer_direction declares the standard per layer. Also check pcb_crosstalk.
  forge pcb connectors
      Rule 13: every connector within 5 mm of the edge is declared in placement.json ({"edge": {"side", "overhang",
      "source"}}); its mating face (F.Fab body) sits on the outline within [pcb] edge_tol_mm (0.1) or at the
      datasheet overhang; the opening faces out; nothing in the corridor to the edge. Also check pcb_connectors.
  forge pcb copper
      Rule 12 metrics (percentages of the board area outside copper keep-outs): copper coverage per layer, GND reference plane coverage and pieces, GND via count, against
      project.toml [pcb] outer_copper_min_pct (60), ref_plane (In1.Cu), ref_plane_min_pct (85),
      stitch_pitch_max_mm (5). Also forge check sub-check pcb_copper.
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
# The outline: replaced only when pe-forge drew it (its group "forge_outline", or - boards set up before the group
# existed - a single rectangle starting at the configured origin). Someone else's outline (notches, cut-outs, a
# drawing from mechanical) is never touched unless the person asked: --replace-outline.
edge = [d for d in b.GetDrawings() if d.GetLayer() == pcbnew.Edge_Cuts]
grp = [g for g in b.Groups() if g.GetName() == "forge_outline"]
in_grp = set()
for g in grp:
    for it in g.GetItems():
        in_grp.add(it.m_Uuid.AsString())
legacy = (len(edge) == 1 and edge[0].GetShape() == pcbnew.SHAPE_T_RECT
          and edge[0].GetStart() == pcbnew.VECTOR2I(mm(X0), mm(Y0)))
foreign = [d for d in edge if d.m_Uuid.AsString() not in in_grp and not legacy]
if foreign and not s.get("replace_outline"):
    print("REPORT" + json.dumps({"error": "outline", "foreign": len(foreign)}))
    sys.exit(0)
for d in edge:
    KEEP.append(d); b.Remove(d)
for g in grp:
    KEEP.append(g); b.Remove(g)
r = pcbnew.PCB_SHAPE(b); r.SetShape(pcbnew.SHAPE_T_RECT); r.SetLayer(pcbnew.Edge_Cuts); r.SetWidth(mm(0.1))
r.SetStart(pcbnew.VECTOR2I(mm(X0), mm(Y0))); r.SetEnd(pcbnew.VECTOR2I(mm(X0 + W), mm(Y0 + H))); b.Add(r)
og = pcbnew.PCB_GROUP(b); og.SetName("forge_outline"); b.Add(og); og.AddItem(r)
have = {f.GetReference(): f for f in b.GetFootprints()}
if s["hole_dir"] and s["inset"]:
    i_ = s["inset"]
    for i, (hx, hy) in enumerate(((i_, i_), (W - i_, i_), (i_, H - i_), (W - i_, H - i_)), 1):
        f = have.get(f"H{i}")
        if f is None:
            f = pcbnew.FootprintLoad(s["hole_dir"], s["hole_name"]); f.SetReference(f"H{i}"); b.Add(f)
        f.SetPosition(pcbnew.VECTOR2I(mm(X0 + hx), mm(Y0 + hy)))
# copper keep-out around each mounting hole, all copper layers: a screw head, washer or standoff sits there
for z in list(b.Zones()):
    if z.GetZoneName().startswith("forge-keepout:"):
        KEEP.append(z); b.Remove(z)
import math
if s["inset"] and s["keepout"]:
    i_ = s["inset"]
    for i, (hx, hy) in enumerate(((i_, i_), (W - i_, i_), (i_, H - i_), (W - i_, H - i_)), 1):
        z = pcbnew.ZONE(b); z.SetIsRuleArea(True); z.SetZoneName(f"forge-keepout:H{i}")
        z.SetDoNotAllowTracks(True); z.SetDoNotAllowVias(True); z.SetDoNotAllowZoneFills(True)
        z.SetDoNotAllowPads(False); z.SetDoNotAllowFootprints(False)
        z.SetLayerSet(pcbnew.LSET.AllCuMask())
        ol = z.Outline(); ol.NewOutline()
        for k in range(32):
            a_ = 2 * math.pi * k / 32
            ol.Append(mm(X0 + hx + s["keepout"] * math.cos(a_)), mm(Y0 + hy + s["keepout"] * math.sin(a_)))
        b.Add(z)
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
# only what the project sets explicitly; never fewer copper layers on a board that already has copper on them
if s["layers"] is not None and s["layers"] != ds.GetCopperLayerCount():
    has_copper = any(True for _ in b.GetTracks()) or any(not z.GetIsRuleArea() for z in b.Zones())
    if s["layers"] < ds.GetCopperLayerCount() and has_copper:
        print("REPORT" + json.dumps({"error": "layers", "have": ds.GetCopperLayerCount(), "want": s["layers"]}))
        sys.exit(0)
    ds.SetCopperLayerCount(s["layers"])
if s["min_hole"] is not None:
    ds.m_MinThroughDrill = mm(s["min_hole"])
if s["thickness"] is not None:
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
for ref, entry in s["place"].items():
    x, y, rot = entry[:3]
    opt = entry[3] if len(entry) > 3 else {}
    f = refs.get(ref)
    if f is None:
        missing.append(ref); continue
    f.SetPosition(pcbnew.VECTOR2I(mm(X0 + x), mm(Y0 + y))); f.SetOrientationDegrees(rot); done.append(ref)
    if "ref" in opt:                     # reference text offset from the footprint origin (mm), e.g. out of a stack
        dx, dy = opt["ref"][:2]
        f.Reference().SetPosition(pcbnew.VECTOR2I(mm(X0 + x + dx), mm(Y0 + y + dy)))
        if len(opt["ref"]) > 2:
            f.Reference().SetTextAngleDegrees(opt["ref"][2])
    if opt.get("no_zone_pads"):          # e.g. a Kelvin test point: joined only by its own trace, not by pours
        for p in f.Pads():
            p.SetLocalZoneConnection(pcbnew.ZONE_CONNECTION_NONE)
    if "edge" in opt:                    # Rule 13: edge connector, checked by forge pcb connectors
        e = opt["edge"]
        val = f'{e["side"]}:{e.get("overhang", 0)}:{e.get("face_from_pin", "")}:{e.get("source", "")}'
        f.SetField("Forge_Edge", val)
        for fld in f.GetFields():
            if fld.GetName() == "Forge_Edge":
                fld.SetVisible(False); fld.SetLayer(b.GetLayerID("F.Fab"))
    sp = opt.get("solid_pads")           # datasheet asks for a solid joint (e.g. an exposed thermal pad): pad numbers or true
    if sp:
        for p in f.Pads():
            if sp is True or p.GetNumber() in [str(x) for x in sp]:
                p.SetLocalZoneConnection(pcbnew.ZONE_CONNECTION_FULL)
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
data = s["zones"]
zl = data["zones"] if isinstance(data, dict) else data
stitch = data.get("stitch") if isinstance(data, dict) else None
for z in list(b.Zones()):
    if z.GetZoneName().startswith("forge:"):
        KEEP.append(z); b.Remove(z)
for g in list(b.Groups()):                     # stitching vias from a previous run
    if g.GetName() == "forge_stitch":
        for it in list(g.GetItems()):
            KEEP.append(it); b.Remove(it)
        KEEP.append(g); b.Remove(g)
missing = []
for d in zl:
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
    # Rule 12: pads join pours through thermal relief (solderable); "solid" only for a datasheet reason
    conn = d.get("pads", "solid" if d.get("solid") else "thermal")
    z.SetPadConnection({"thermal": pcbnew.ZONE_CONNECTION_THERMAL, "solid": pcbnew.ZONE_CONNECTION_FULL,
                        "smd_solid": pcbnew.ZONE_CONNECTION_THT_THERMAL, "none": pcbnew.ZONE_CONNECTION_NONE}[conn])
    z.SetThermalReliefGap(mm(d.get("relief_gap", 0.3)))
    z.SetThermalReliefSpokeWidth(mm(d.get("spoke_width", 0.5)))
    b.Add(z)
b.BuildConnectivity(); pcbnew.ZONE_FILLER(b).Fill(b.Zones())   # fresh connectivity, or the fill keeps stale knock-outs
n_st = 0
if stitch:
    import math
    ni = b.FindNet(stitch.get("net", "GND"))
    r = stitch.get("d", 0.6) / 2.0
    margin = mm(r + stitch.get("clearance", 0.3))
    layers = [l for l in pcbnew.LSET.AllCuMask().CuStack() if b.IsLayerEnabled(l)]
    polys = []
    for lay in layers:                                  # the net's fill on every copper layer
        ps = pcbnew.SHAPE_POLY_SET()
        for z in b.Zones():
            if not z.GetIsRuleArea() and z.GetNetCode() == ni.GetNetCode() and z.IsOnLayer(lay):
                ps.Append(z.GetFilledPolysList(lay))
        polys.append(ps)
    ring = [(math.cos(2 * math.pi * k / 16), math.sin(2 * math.pi * k / 16)) for k in range(16)]
    vias = [v.GetPosition() for v in b.GetTracks() if v.GetClass() == "PCB_VIA"]
    pads = [p.GetPosition() for f in b.GetFootprints() for p in f.Pads()]
    avoid = [[mm(X0 + a[0]), mm(Y0 + a[1]), mm(X0 + a[2]), mm(Y0 + a[3])] for a in stitch.get("avoid", [])]
    g = pcbnew.PCB_GROUP(b); g.SetName("forge_stitch"); b.Add(g)
    pitch = stitch.get("pitch", 4.0)
    W, H = s.get("outline", [0, 0])
    gx = pitch / 2
    while gx < (W or 200):
        gy = pitch / 2
        while gy < (H or 200):
            c = pcbnew.VECTOR2I(mm(X0 + gx), mm(Y0 + gy))
            ok = not any(a[0] <= c.x <= a[2] and a[1] <= c.y <= a[3] for a in avoid)
            ok = ok and all(math.hypot(c.x - v.x, c.y - v.y) > mm(1.2) for v in vias)
            ok = ok and all(math.hypot(c.x - p.x, c.y - p.y) > mm(1.5) for p in pads)
            if ok:
                for ps in polys:
                    if not all(ps.Contains(pcbnew.VECTOR2I(int(c.x + margin * dx), int(c.y + margin * dy))) for dx, dy in ring) \
                            or not ps.Contains(c):
                        ok = False; break
            if ok:
                v = pcbnew.PCB_VIA(b); v.SetPosition(c); v.SetWidth(mm(2 * r)); v.SetDrill(mm(stitch.get("drill", 0.3)))
                v.SetNet(ni); b.Add(v); g.AddItem(v); vias.append(c); n_st += 1
            gy += pitch
        gx += pitch
    b.BuildConnectivity(); pcbnew.ZONE_FILLER(b).Fill(b.Zones())   # fresh connectivity, or the fill keeps stale knock-outs
b.Save(s["board"])
print("REPORT" + json.dumps({"zones": len(zl) - len(missing), "missing_nets": missing, "stitch_vias": n_st}))
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
# stitching vias (forge pcb zones) that a new track touches would join that track's net: remove them;
# a later `forge pcb zones` re-stitches around the routing
import math
dropped = 0
new_tracks = [it for it in g.GetItems() if it.GetClass() == "PCB_TRACK"]
for sg in [x for x in b.Groups() if x.GetName() == "forge_stitch"]:
    for v in list(sg.GetItems()):
        if v.GetClass() != "PCB_VIA":
            continue
        c = v.GetPosition()
        for tr in new_tracks:
            a_, e_ = tr.GetStart(), tr.GetEnd()
            dx, dy = e_.x - a_.x, e_.y - a_.y
            L2 = dx * dx + dy * dy
            u = 0 if L2 == 0 else max(0, min(1, ((c.x - a_.x) * dx + (c.y - a_.y) * dy) / L2))
            d = math.hypot(c.x - (a_.x + u * dx), c.y - (a_.y + u * dy))
            if d < v.GetWidth(pcbnew.F_Cu) / 2 + tr.GetWidth() / 2 + mm(0.3):
                KEEP.append(v); b.Remove(v); dropped += 1
                break
b.BuildConnectivity(); pcbnew.ZONE_FILLER(b).Fill(b.Zones())   # fresh connectivity, or the fill keeps stale knock-outs
b.Save(s["board"])
print("REPORT" + json.dumps({"tracks": n_t, "vias": n_v, "missing_nets": sorted(set(missing)), "stitch_dropped": dropped}))
'''


COPPER = r'''
import json, sys, pcbnew
s = json.load(open(sys.argv[1]))
b = pcbnew.LoadBoard(s["board"])
mm2 = lambda a: a / 1e12                          # nm^2 -> mm^2
box = b.GetBoardEdgesBoundingBox()
area = pcbnew.ToMM(box.GetWidth()) * pcbnew.ToMM(box.GetHeight())
out = {"board_mm2": area, "layers": {}}
gnd = b.FindNet(s.get("net", "GND"))
for lay in pcbnew.LSET.AllCuMask().CuStack():
    if not b.IsLayerEnabled(lay):
        continue
    allc, g, pieces = 0.0, 0.0, 0
    for z in b.Zones():
        if z.GetIsRuleArea() or not z.IsOnLayer(lay):
            continue
        fp = z.GetFilledPolysList(lay)
        a_ = mm2(fp.Area())
        allc += a_
        if gnd and z.GetNetCode() == gnd.GetNetCode():
            g += a_; pieces += fp.OutlineCount()
    ko = pcbnew.SHAPE_POLY_SET()                   # copper keep-outs (rule areas) do not count against coverage
    for z in b.Zones():
        if z.GetIsRuleArea() and z.GetDoNotAllowZoneFills() and z.IsOnLayer(lay):
            ko.Append(z.Outline())
    ko.Simplify()
    usable = max(area - mm2(ko.Area()), 1e-9)
    out["layers"][b.GetLayerName(lay)] = {"copper_pct": round(100 * allc / usable, 1), "gnd_pct": round(100 * g / usable, 1),
                                          "gnd_pieces": pieces, "keepout_mm2": round(mm2(ko.Area()), 1)}
vias = [v for v in b.GetTracks() if v.GetClass() == "PCB_VIA" and gnd and v.GetNetCode() == gnd.GetNetCode()]
out["gnd_vias"] = len(vias)
CU = [l for l in pcbnew.LSET.AllCuMask().CuStack() if b.IsLayerEnabled(l)]
solid = []                                         # pads joined solidly to a pour (no thermal relief)
INH = pcbnew.ZONE_CONNECTION_INHERITED
for f in b.GetFootprints():
    for p in f.Pads():
        if p.GetNetCode() <= 0:
            continue
        th = p.GetAttribute() == pcbnew.PAD_ATTRIB_PTH
        for z in b.Zones():
            if z.GetIsRuleArea() or z.GetNetCode() != p.GetNetCode() or not any(z.IsOnLayer(l) and p.IsOnLayer(l) for l in CU):
                continue
            c = p.GetLocalZoneConnection()
            if c == INH:
                c = f.GetLocalZoneConnection()
            if c == INH:
                c = z.GetPadConnection()
            if c == pcbnew.ZONE_CONNECTION_FULL or (c == pcbnew.ZONE_CONNECTION_THT_THERMAL and not th):
                solid.append({"pad": f"{f.GetReference()}.{p.GetNumber()}", "th": th, "zone": z.GetZoneName(),
                              "local": p.GetLocalZoneConnection() == pcbnew.ZONE_CONNECTION_FULL})
                break
out["solid_pads"] = solid
print("REPORT" + json.dumps(out))
'''


CONNECTORS = r'''
import json, sys, pcbnew
s = json.load(open(sys.argv[1]))
b = pcbnew.LoadBoard(s["board"])
T = pcbnew.ToMM
bb = b.GetBoardEdgesBoundingBox()
w = max([T(d.GetWidth()) for d in b.GetDrawings() if d.GetLayer() == pcbnew.Edge_Cuts] or [0])
h = w / 2                                         # the fab cuts on the outline's centre line, not its outer edge
E = {"left": T(bb.GetX()) + h, "right": T(bb.GetRight()) - h, "top": T(bb.GetY()) + h, "bottom": T(bb.GetBottom()) - h}
def box(f, lay):
    L = b.GetLayerID(lay); r = None
    for g in f.GraphicalItems():
        if g.GetLayer() == L:
            x = g.GetBoundingBox()
            r = [T(x.GetX()), T(x.GetY()), T(x.GetRight()), T(x.GetBottom())] if r is None else \
                [min(r[0], T(x.GetX())), min(r[1], T(x.GetY())), max(r[2], T(x.GetRight())), max(r[3], T(x.GetBottom()))]
    return r
out = []
fps = list(b.GetFootprints())
for f in fps:
    ref = f.GetReference()
    fld = f.GetFieldText("Forge_Edge") if f.HasField("Forge_Edge") else None
    body = box(f, "F.Fab") or box(f, "F.Courtyard")
    if body is None:
        continue
    near = min(body[0] - E["left"], E["right"] - body[2], body[1] - E["top"], E["bottom"] - body[3])
    if fld is None:
        if ref.startswith(("J", "P", "X", "CN")) and near < 5:
            out.append({"ref": ref, "declared": False, "near": round(near, 2)})
        continue
    side, oh, ffp, src = (fld.split(":", 3) + ["", "", ""])[:4]
    oh = float(oh or 0)
    fab_face = {"left": body[0], "right": body[2], "top": body[1], "bottom": body[3]}[side]
    face = fab_face
    p1 = [p for p in f.Pads() if p.GetNumber() == "1"]
    if ffp and p1:                       # datasheet distance from the pin row to the mating face is final
        x1, y1 = T(p1[0].GetPosition().x), T(p1[0].GetPosition().y)
        face = {"left": x1 - float(ffp), "right": x1 + float(ffp), "top": y1 - float(ffp), "bottom": y1 + float(ffp)}[side]
    inboard = (face - E[side]) if side in ("left", "top") else (E[side] - face)   # + = face inside the board
    pads = [(T(p.GetPosition().x), T(p.GetPosition().y)) for p in f.Pads()]
    cx = sum(x for x, _ in pads) / len(pads); cy = sum(y for _, y in pads) / len(pads)
    mid = ((body[0] + body[2]) / 2, (body[1] + body[3]) / 2)
    # the opening faces out: the pins sit on the inboard side of the body centre
    outward = {"left": cx >= mid[0], "right": cx <= mid[0], "top": cy >= mid[1], "bottom": cy <= mid[1]}[side]
    # corridor between the face and the edge, widened 1 mm each side for the plug / wire body
    if side in ("left", "right"):
        cor = [min(face, E[side]), body[1] - 1, max(face, E[side]), body[3] + 1]
    else:
        cor = [body[0] - 1, min(face, E[side]), body[2] + 1, max(face, E[side])]
    block = []
    for g in fps:
        if g is f:
            continue
        c = box(g, "F.Courtyard")
        if c and c[0] < cor[2] and c[2] > cor[0] and c[1] < cor[3] and c[3] > cor[1] and cor[2] - cor[0] > 0.05 and cor[3] - cor[1] > 0.05:
            block.append(g.GetReference())
    out.append({"ref": ref, "declared": True, "side": side, "overhang": oh, "source": src,
                "inboard": round(inboard, 3), "outward": outward, "datasheet_face": bool(ffp),
                "fab_vs_datasheet": round(abs(fab_face - face), 3), "blocked_by": block, "body": body})
print("REPORT" + json.dumps({"edges": E, "connectors": out}))
'''


def connectors(prj, board):
    out, err = _run(CONNECTORS, {"board": os.path.abspath(board)})
    m = re.search(r"REPORT(\{.*\})", out)
    if not m:
        raise SystemExit("pcbnew connector check failed: " + (err or out)[-600:])
    return json.loads(m.group(1))


def connector_findings(prj, rep):
    """Rule 13: edge connectors exactly on the outline, opening outward, mating corridor clear, every edge connector
    declared. Tolerance [pcb] edge_tol_mm (0.1)."""
    tol = prj.cfg.get("pcb", {}).get("edge_tol_mm", 0.1)
    F = []
    for c in rep["connectors"]:
        if not c["declared"]:
            F.append((f"{c['ref']} at the board edge is declared (side, datasheet overhang) in placement.json",
                      f"body {c['near']} mm from the edge, no edge entry",
                      '{"edge": {"side": "left", "overhang": 0, "source": "<datasheet page>"}}'))
            continue
        want = -c["overhang"]              # overhang beyond the edge = negative inboard
        if abs(c["inboard"] - want) > tol:
            F.append((f"{c['ref']} mating face on the {c['side']} edge "
                      + (f"overhanging {c['overhang']:g} mm" if c["overhang"] else "flush") + f" (+/- {tol:g} mm, Rule 13)",
                      f"face {c['inboard']:+.2f} mm " + ("inside" if c["inboard"] > 0 else "outside") + " the outline",
                      f"move it {c['inboard'] - want:+.2f} mm toward the {c['side']} edge" if c["inboard"] > want
                      else f"move it {want - c['inboard']:.2f} mm inboard"))
        if not c["outward"]:
            F.append((f"{c['ref']} opening (plug / wire entry) faces the {c['side']} edge", "pins are on the outer side: "
                      "it faces into the board", "rotate it 180 degrees"))
        if c["blocked_by"]:
            F.append((f"{c['ref']} mating corridor to the edge is clear", "blocked by " + ", ".join(c["blocked_by"]),
                      "move those parts out of the plug / wire path"))
        if not c["source"] or not c.get("datasheet_face"):
            F.append((f"{c['ref']} edge position comes from the datasheet drawing (face_from_pin + source)",
                      "footprint outline only" if not c.get("datasheet_face") else "no source",
                      'add "face_from_pin": <mm from pin 1 row to the mating face> and "source": "<datasheet page>"'))
    return F


def every_board(prj, one):
    """Run a one-board check on every board with content and merge: worst status, all findings, reasons joined.
    A multi-board project (power + control + driver) gets every board checked, not the first one (F-08)."""
    from .check import result, roots, has_content
    pcbs = [b_ for b_ in roots(prj, "kicad_pcb") if has_content(b_)]
    if not pcbs:
        return result("skip", reason="no board yet")
    if not kicad.find_python():
        return result("skip", reason="KiCad's Python (pcbnew) not found", missing_tool=True)
    order = ["skip", "pass", "warn", "fail"]
    out = None
    for b_ in pcbs:
        r = one(b_)
        if out is None:
            out = dict(r, reason=(f"{os.path.basename(b_)}: " if len(pcbs) > 1 else "") + r["reason"])
            continue
        out["status"] = max(out["status"], r["status"], key=order.index)
        out["findings"] += r["findings"]
        out["missing_tool"] = out["missing_tool"] or r["missing_tool"]
        out["reason"] += f"; {os.path.basename(b_)}: {r['reason']}"
    return out


def as_check_connectors(prj, opts):
    return every_board(prj, lambda b_: _check_connectors(prj, b_))


def _check_connectors(prj, board):
    from .check import finding, result
    pcbs = [board]
    rep = connectors(prj, pcbs[0])
    if not rep["connectors"]:
        return result("skip", reason="no connector at the board edge")
    F = connector_findings(prj, rep)
    return result("fail" if F else "pass", [finding(c, a, prj.rel(pcbs[0]), f) for c, a, f in F],
                  reason=f"{len(rep['connectors'])} edge connector(s)")


XTALK = r'''
import json, sys, pcbnew
s = json.load(open(sys.argv[1]))
b = pcbnew.LoadBoard(s["board"])
T = pcbnew.ToMM
bb = b.GetBoardEdgesBoundingBox()
area = T(bb.GetWidth()) * T(bb.GetHeight())
layers = []
for lay in pcbnew.LSET.AllCuMask().CuStack():
    if not b.IsLayerEnabled(lay):
        continue
    best = {}
    for z in b.Zones():
        if not z.GetIsRuleArea() and z.IsOnLayer(lay):
            best[z.GetNetname()] = best.get(z.GetNetname(), 0) + z.GetFilledPolysList(lay).Area() / 1e12
    segs = [{"net": tr.GetNetname(), "w": T(tr.GetWidth()), "a": [T(tr.GetStart().x), T(tr.GetStart().y)],
             "b": [T(tr.GetEnd().x), T(tr.GetEnd().y)]}
            for tr in b.GetTracks() if tr.GetClass() == "PCB_TRACK" and tr.GetLayer() == lay]
    layers.append({"name": b.GetLayerName(lay), "fill_pct": round(100 * max(best.values() or [0]) / area, 1), "segs": segs})
print("REPORT" + json.dumps({"layers": layers}))
'''


def xtalk(prj, board):
    out, err = _run(XTALK, {"board": os.path.abspath(board)})
    m = re.search(r"REPORT(\{.*\})", out)
    if not m:
        raise SystemExit("pcbnew crosstalk check failed: " + (err or out)[-600:])
    return json.loads(m.group(1))


def _dir(s):
    import math
    dx, dy = s["b"][0] - s["a"][0], s["b"][1] - s["a"][1]
    a = abs(math.degrees(math.atan2(dy, dx))) % 180
    a = min(a, 180 - a)
    return ("H" if a <= 22.5 else "V" if a >= 67.5 else "D"), math.hypot(dx, dy), a


def xtalk_analyse(prj, rep):
    """Rule 14. Signal tracks: width <= [pcb] signal_max_width_mm (0.4) or net in signal_nets. Per layer: length by
    direction (segments shorter than fanout_mm, 2.0, are pad fan-out and not judged). A layer is a plane when one
    net fills >= plane_fill_pct (80) and it carries no signal. Adjacent signal layers with no plane between them
    must run orthogonally (dominant direction >= dominance_pct, 70, and opposite); broadside overlaps (parallel within
    20 deg, offset < xtalk_offset_mm 1.0, overlap > xtalk_len_mm 2.0) on such a pair fail. Declared directions
    ([pcb] layer_direction = {"In2.Cu" = "H"}) must hold on every layer that carries signals."""
    import math
    cfg = prj.cfg.get("pcb", {})
    wmax, fan = cfg.get("signal_max_width_mm", 0.4), cfg.get("fanout_mm", 2.0)
    nets = set(cfg.get("signal_nets", []))
    dom, decl = cfg.get("dominance_pct", 70), cfg.get("layer_direction", {})
    off, ovl = cfg.get("xtalk_offset_mm", 1.0), cfg.get("xtalk_len_mm", 2.0)
    stats, F = [], []
    for L in rep["layers"]:
        sig = [s for s in L["segs"] if s["w"] <= wmax + 1e-6 or s["net"] in nets]
        ln = {"H": 0.0, "V": 0.0, "D": 0.0}
        for s in sig:
            d, l, _ = _dir(s)
            if l >= fan:
                ln[d] += l
        tot = sum(ln.values())
        d0 = max(ln, key=ln.get) if tot else None
        st = {"layer": L["name"], "signal": bool(sig), "plane": not sig and L["fill_pct"] >= cfg.get("plane_fill_pct", 80),
              "len": {k: round(v, 1) for k, v in ln.items()}, "dir": d0,
              "dom_pct": round(100 * ln[d0] / tot, 0) if tot else None, "declared": decl.get(L["name"]), "segs": sig}
        stats.append(st)
        if st["declared"] and tot >= 5 and (d0 != st["declared"] or st["dom_pct"] < dom):
            F.append((f"{L['name']} signal routing mostly {'horizontal' if st['declared'] == 'H' else 'vertical'} "
                      f"(declared, >= {dom} %, Rule 14)", f"{st['len']} mm by direction",
                      "re-route the off-direction runs on the orthogonal neighbour layer through vias"))
    pairs = []
    sigl = [i for i, s in enumerate(stats) if s["signal"]]
    for i, j in zip(sigl, sigl[1:]):
        shield = any(stats[k]["plane"] for k in range(i + 1, j))
        pairs.append({"a": stats[i]["layer"], "b": stats[j]["layer"], "shielded": shield})
        if shield:
            continue
        A, B = stats[i], stats[j]
        for X in (A, B):
            if not X["declared"] and sum(X["len"].values()) >= 5 and (X["dom_pct"] or 0) < dom:
                F.append((f"{X['layer']} has one dominant signal direction (>= {dom} %, Rule 14)",
                          f"{X['len']} mm by direction", 'declare [pcb] layer_direction and re-route'))
        if A["dir"] and A["dir"] == B["dir"] and A["dir"] != "D":
            F.append((f"{A['layer']} and {B['layer']} (adjacent, no plane between) route orthogonally (Rule 14)",
                      f"both mostly {A['dir']}", "make one layer horizontal and the other vertical"))
        hits = []
        for s in A["segs"]:
            for u in B["segs"]:
                d1, l1, a1 = _dir(s); d2, l2, a2 = _dir(u)
                if l1 < fan or l2 < fan or abs(a1 - a2) > 20 or s["net"] == u["net"]:
                    continue
                ux, uy = (s["b"][0] - s["a"][0]) / l1, (s["b"][1] - s["a"][1]) / l1
                p = lambda q: (q[0] - s["a"][0]) * ux + (q[1] - s["a"][1]) * uy
                lo, hi = max(0, min(p(u["a"]), p(u["b"]))), min(l1, max(p(u["a"]), p(u["b"])))
                mid = (u["a"][0] + u["b"][0]) / 2, (u["a"][1] + u["b"][1]) / 2
                dist = abs((mid[0] - s["a"][0]) * uy - (mid[1] - s["a"][1]) * ux)
                if hi - lo > ovl and dist < off:
                    hits.append(f"{s['net']} / {u['net']} {hi - lo:.1f} mm at {dist:.2f} mm")
        if hits:
            F.append((f"no broadside parallel signal runs between {A['layer']} and {B['layer']} (Rule 14)",
                      "; ".join(hits[:4]), "cross at 90 degrees, or put a plane between the layers"))
    for st in stats:
        st.pop("segs")
    return stats, pairs, F


def as_check_xtalk(prj, opts):
    return every_board(prj, lambda b_: _check_xtalk(prj, b_))


def _check_xtalk(prj, board):
    from .check import finding, result
    pcbs = [board]
    stats, pairs, F = xtalk_analyse(prj, xtalk(prj, pcbs[0]))
    if not any(s["signal"] for s in stats):
        return result("skip", reason="no signal tracks yet")
    return result("fail" if F else "pass", [finding(c, a, prj.rel(pcbs[0]), f) for c, a, f in F],
                  reason=", ".join(f"{s['layer']} {s['dir'] or '-'}" + (" plane" if s["plane"] else "") for s in stats))


def copper(prj, board):
    """Copper coverage per layer, GND plane pieces, GND via count (Rule 12 metrics)."""
    out, err = _run(COPPER, {"board": os.path.abspath(board), "net": prj.cfg.get("pcb", {}).get("gnd_net", "GND")})
    m = re.search(r"REPORT(\{.*\})", out)
    if not m:
        raise SystemExit("pcbnew copper metrics failed: " + (err or out)[-600:])
    return json.loads(m.group(1))


def copper_findings(prj, rep):
    """[(claim, actual, fix)] against project.toml [pcb] thresholds (defaults: outer 60 %, plane 85 % in one piece,
    stitching pitch 5 mm)."""
    cfg = prj.cfg.get("pcb", {})
    outer_min = cfg.get("outer_copper_min_pct", 60)
    plane, plane_min = cfg.get("ref_plane", "In1.Cu"), cfg.get("ref_plane_min_pct", 85)
    pitch = cfg.get("stitch_pitch_max_mm", 5.0)
    L = rep["layers"]
    F = []
    for name in ("F.Cu", "B.Cu"):
        if name in L and L[name]["copper_pct"] < outer_min:
            F.append((f"{name} copper coverage >= {outer_min} % (Rule 12)", f"{L[name]['copper_pct']} %",
                      "Pour the unused area with GND at the lowest priority"))
    if plane in L:
        pl = L[plane]
        if pl["gnd_pct"] < plane_min:
            F.append((f"{plane} reference plane covers >= {plane_min} % of the board", f"{pl['gnd_pct']} %",
                      "Fill the plane; remove splits and merged clearance holes"))
        if pl["gnd_pieces"] > 1:
            F.append((f"{plane} reference plane is one piece", f"{pl['gnd_pieces']} pieces",
                      "Join the islands: no slots or splits in the reference plane"))
    for sp in rep.get("solid_pads", []):
        if sp["local"]:
            continue                                   # set per part in placement.json (datasheet reason)
        F.append((f"{sp['pad']} joins {sp['zone']} through thermal relief (Rule 12: solderable"
                  + (", through-hole" if sp["th"] else "") + ")", "solid connection",
                  'Zone "pads": "thermal" (default); a datasheet-required solid joint goes in placement.json solid_pads'))
    need = int(rep["board_mm2"] / (pitch * pitch) * 0.5)
    if rep["gnd_vias"] < need:
        F.append((f"GND stitching about one via per {pitch:g} x {pitch:g} mm", f"{rep['gnd_vias']} GND vias (want >= {need})",
                  "Add a stitch block to zones.json (forge pcb zones)"))
    return F


def as_check_copper(prj, opts):
    return every_board(prj, lambda b_: _check_copper(prj, b_))


def _check_copper(prj, board):
    from .check import finding, result
    pcbs = [board]
    rep = copper(prj, pcbs[0])
    if not any(v["copper_pct"] for v in rep["layers"].values()):
        return result("skip", reason="no copper pours yet")
    F = copper_findings(prj, rep)
    summary = ", ".join(f"{k} {v['copper_pct']} %" for k, v in rep["layers"].items()) + f"; {rep['gnd_vias']} GND vias"
    return result("warn" if F else "pass", [finding(c, a, prj.rel(pcbs[0]), f) for c, a, f in F], reason=summary)


def _data_cmd(prj, board, script, key, path):
    board_only(board)
    cfg = prj.cfg.get("pcb", {})
    data = json.load(open(path))
    out, err = _run(script, {"board": os.path.abspath(board), "origin": cfg.get("origin_mm", [100, 100]), key: data,
                             "outline": cfg.get("outline_mm", [0, 0])})
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


def stackup_block(copper_oz, thickness, finish="None"):
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
          f'\t\t\t(copper_finish "{finish}")', '\t\t\t(dielectric_constraints no)', '\t\t)']
    return "\n".join(L) + "\n"


def write_stackup(board, copper_oz, thickness, finish="None"):
    t = open(board).read()
    t = re.sub(r"\n\t\t\(stackup\n.*?\n\t\t\)\n", "\n", t, count=1, flags=re.S)
    t = re.sub(r"(\n\t\(setup\n)", lambda m: m.group(1) + stackup_block(copper_oz, thickness, finish), t, count=1)
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


def setup(prj, board, replace_outline=False):
    cfg = dict(prj.cfg.get("pcb", {}), _replace_outline=replace_outline)
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
            "replace_outline": bool(cfg.get("_replace_outline")),
            # unset in [pcb] = keep the board's own value (an adopted board's stack-up is never reset to defaults)
            "layers": len(cfg["copper_oz"]) if "copper_oz" in cfg else None,
            "min_hole": cfg.get("min_hole_mm"), "thickness": cfg.get("thickness_mm"),
            "hole_dir": lib_dirs().get(lib), "hole_name": name, "inset": cfg.get("hole_inset_mm", 0),
            "keepout": cfg.get("hole_keepout_mm", 3.5)}
    out, err = _run(SETUP, spec)
    m = re.search(r"REPORT(\{.*\})", out)
    if m and json.loads(m.group(1)).get("error") == "layers":
        e = json.loads(m.group(1))
        raise SystemExit(f"[pcb] copper_oz asks for {e['want']} copper layers but the board has {e['have']} with copper "
                         "on them: reducing would delete copper. Nothing was changed. Fix copper_oz, or remove the "
                         "copper on the extra layers in KiCad first.")
    if m and json.loads(m.group(1)).get("error") == "outline":
        raise SystemExit(f"the board already has an outline pe-forge did not draw ({json.loads(m.group(1))['foreign']} "
                         "item(s) on Edge.Cuts). Nothing was changed. Keep it (remove outline_mm from [pcb] and set up "
                         "the rest by hand), or run `forge pcb setup --replace-outline` to replace it.")
    if "OK" not in out:
        raise SystemExit("pcbnew set-up failed: " + (err or out)[-600:])
    if "copper_oz" in cfg:                           # the stack-up only when the project defines it
        write_stackup(board, oz, spec["thickness"] if spec["thickness"] is not None else 1.6,
                      cfg.get("copper_finish", "None"))
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
    tool = {}
    from . import hp as _hp
    for rid, fn in (("R-12", as_check_copper), ("R-13", as_check_connectors), ("R-14", as_check_xtalk), ("R-15", _hp.as_check)):
        try:
            r = fn(prj, {})
            tool[rid] = f"tool: {r['status'].upper()} ({r.get('reason') or ''})"
        except SystemExit as e:
            tool[rid] = f"tool: did not run ({e})"
    prows = "\n".join(f"| {rid} | {txt} | | {tool.get(rid, '')} |" for rid, txt in PROJECT_RULES)
    from . import hp
    try:
        hrows = hp.review_rows(prj, board) if hp.profile(prj)["enabled"] else []
    except SystemExit as e:
        hrows = []
        tool["R-15"] = f"tool: did not run ({e})"
    cell = lambda s: s.replace("|", "/")
    hp_txt = "\n".join(f"| {r['id']} | ({r['strength']}) {cell(r['text'][:100])} | "
                       + ("WAIVED" if r["status"] == "WAIVED" else "") + f" | {r['status'].lower()}: {cell(r['evidence'][:160])} |"
                       for r in hrows) or "| — | high-power rules off for this board ([hp] enabled = false) | N/A | |"
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

## Project rules (enforced on every review; the tool result is evidence, the reviewer still checks by eye)
| Rule | Requirement | Verdict | Evidence |
|---|---|---|---|
{prows}

## High-power rule book (Rule 15; docs/High_Power_PCB_Rule_Book.md, applicable rules only)
Verdict per rule: PASS, FAIL, DEVIATION-OK (a Should with the reason written here; a Must only with the owner's
recorded OK, put in project.toml [hp.waive]) or N/A (why). "auto" results are tool evidence; open the board anyway.
| Rule | Requirement | Verdict | Evidence |
|---|---|---|---|
{hp_txt}

## Other findings (ranked Critical / Major / Minor)

## Verdict
Set `verdict:` above to READY, READY WITH FIXES or NOT READY.
""")
    return path


PROJECT_RULES = [
    ("R-11", "Critical parts follow the datasheet layout strictly (land pattern, placement order, loops, values), page cited"),
    ("R-12", "Best EMI / heat / shielding: outer GND fill, one-piece reference plane, stitching, minimal hot loop and SW node, thermal relief on pads (solid only by datasheet)"),
    ("R-13", "Edge connectors on the outline from the datasheet drawing; opening out; mating envelope and tool access clear; not swappable"),
    ("R-14", "Signal layers orthogonal: adjacent signal layers without a plane between run H / V; no broadside parallel runs"),
    ("R-15", "High-power rule book applied: every applicable STK/PL/GD/SW/GND/SNS/FLT/TH/TX/HC/HV row below graded; Must rules pass or carry the owner's waiver"),
]


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
    rs = rs + [i for i, _ in PROJECT_RULES]
    f = latest_review(prj)
    if not f:
        return False, ["no independent PCB review yet (forge pcb review-new <milestone>, then the pcb-reviewer agent)"]
    t = open(f).read()
    probs = []
    got = {}
    for line in t.splitlines():
        m = re.match(r"^\|\s*([LR]-\d+[A-Za-z]?|(?:STK|PL|GD|SW|GND|SNS|FLT|TH|TX|HC|HV)-\d+)\s*\|([^|]*)\|\s*([^|]*?)\s*\|", line)
        if m:
            got[m.group(1)] = m.group(3).upper()
            if re.match(r"(?:STK|PL|GD|SW|GND|SNS|FLT|TH|TX|HC|HV)-", m.group(1)):
                rs.append(m.group(1))       # every high-power row in the form needs a verdict too
    rs = list(dict.fromkeys(rs))
    missing = [i for i in rs if not got.get(i)]
    if missing:
        probs.append(f"rules without a verdict: {', '.join(missing)}")
    rs = list(dict.fromkeys(rs))
    bad = [i for i in rs if got.get(i) and not got[i].startswith(VERDICTS + ("WAIVED",))]
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
    if v != "OPEN":
        from . import learn
        learn.record_review(prj, f, v)
    if v != "READY":           # READY WITH FIXES needs the fixes and a fresh review before a gate
        probs.append(f"review verdict {v}" + (": fix the findings and review again" if v.startswith("READY") else ""))
    return not probs, probs, prj.rel(f)


def as_check_review(prj, opts):
    from .check import finding, result, roots, has_content
    pcbs = [b for b in roots(prj, "kicad_pcb") if has_content(b)]
    if not pcbs:
        return result("skip", reason="no board yet")
    F, locs = [], []
    for b in pcbs:                         # every board needs its own review, bound to its own hash
        ok, probs, *where = review_status(prj, b)
        loc = where[0] if where else prj.paths["reviews"]
        locs.append(loc)
        F += [finding(f"independent PCB review of {os.path.basename(b)} covers every layout rule, board unchanged", p, loc)
              for p in probs]
    return result("pass" if not F else "warn", F, reason="; ".join(locs))


def gate_items(prj):
    from .check import roots
    from .check import has_content
    pcbs = [b for b in roots(prj, "kicad_pcb") if has_content(b)]
    if not pcbs:
        return [(False, "PCB review: no board")]
    res = []
    for b in pcbs:
        ok, probs, *where = review_status(prj, b)
        res.append((ok, f"independent PCB review of {os.path.basename(b)} against the layout rules and R-11..R-14"
                    + (f" ({where[0]})" if where else "") + ("" if ok else ": " + "; ".join(probs))))
    from . import hp
    for name, fn in (("Rule 12 copper / thermal relief", as_check_copper), ("Rule 13 edge connectors", as_check_connectors),
                     ("Rule 14 orthogonal signal layers", as_check_xtalk), ("Rule 15 high-power rule book", hp.as_check)):
        try:
            r = fn(prj, {})
        except SystemExit as e:
            res.append((False, f"{name}: did not run ({e})")); continue
        good = r["status"] == "pass" or (r["status"] == "skip" and not r.get("missing_tool"))
        res.append((good, f"{name}: {r['status']}" + ("".join(f"; {f['claim']}: {f['actual']}" for f in r["findings"][:2]))))
    return res


def main(prj, argv):
    if argv[:1] == ["rules"]:
        rs = rules(prj)
        print(json.dumps(rs, indent=1) if "--json" in argv else
              "\n".join(f"{i}  {txt}" for i, txt in rs) or f"no L-rules in {prj.paths['layout_spec']}")
        return 0 if rs else 1
    if not argv or argv[0] not in ("sync", "setup", "place", "zones", "route", "copper", "connectors", "crosstalk", "review-new", "review-status"):
        print(__doc__)
        return 1
    from . import check
    if argv[0] in ("setup", "place", "zones", "route", "sync") and prj.level != "light" and not prj.process.get("sketch"):
        from . import state
        p6 = state.load(prj)["phases"]["P6"]["status"]
        if p6 not in ("passed", "skipped"):
            raise SystemExit(f"forge pcb {argv[0]}: the schematic gate G6 is {p6}, so the board would be built on an unreviewed "
                             "schematic. Pass G6 first; or, for a throwaway sketch, set [process] sketch = true in "
                             "project.toml (a sketch is never released: G8 refuses while it is set).")
    board = argv[argv.index("--board") + 1] if "--board" in argv else None
    if not board:
        pcbs = check.roots(prj, "kicad_pcb")
        if not pcbs:
            raise SystemExit("no board file under 08_PCB")
        board = pcbs[0]
    if argv[0] == "crosstalk":
        stats, pairs, F = xtalk_analyse(prj, xtalk(prj, board))
        for s in stats:
            kind = "plane" if s["plane"] else ("signal" if s["signal"] else "pours only")
            print(f"  {s['layer']:7s} {kind:10s} H {s['len']['H']:5.1f}  V {s['len']['V']:5.1f}  D {s['len']['D']:5.1f} mm"
                  + (f"  -> {s['dir']} {s['dom_pct']:.0f} %" if s["dom_pct"] is not None else "")
                  + (f" (declared {s['declared']})" if s["declared"] else ""))
        for p_ in pairs:
            print(f"  pair {p_['a']} / {p_['b']}: " + ("shielded by a plane" if p_["shielded"] else "adjacent, must be orthogonal"))
        for c, a_, f in F:
            print(f"  FAIL {c}: {a_} ({f})")
        print("PASS (Rule 14)" if not F else f"{len(F)} finding(s)")
        return 1 if F else 0
    if argv[0] == "connectors":
        rep = connectors(prj, board)
        for c in rep["connectors"]:
            if c["declared"]:
                print(f"  {c['ref']:4s} {c['side']:6s} face {c['inboard']:+.2f} mm inside the edge (want {-c['overhang']:+.2f}"
                      + (f"; footprint outline differs from the datasheet by {c['fab_vs_datasheet']:.2f} mm" if c.get("fab_vs_datasheet", 0) > 0.05 else "") + "), "
                      f"opening {'out' if c['outward'] else 'IN'}, corridor {'clear' if not c['blocked_by'] else c['blocked_by']}")
            else:
                print(f"  {c['ref']:4s} {c['near']} mm from the edge, not declared")
        F = connector_findings(prj, rep)
        for c, a_, f in F:
            print(f"  FAIL {c}: {a_} ({f})")
        print("PASS (Rule 13)" if not F else f"{len(F)} finding(s)")
        return 1 if F else 0
    if argv[0] == "copper":
        rep = copper(prj, board)
        for k, v in rep["layers"].items():
            print(f"  {k:7s} copper {v['copper_pct']:5.1f} %   GND {v['gnd_pct']:5.1f} % in {v['gnd_pieces']} piece(s)")
        print(f"  GND vias: {rep['gnd_vias']}")
        F = copper_findings(prj, rep)
        for c, a_, f in F:
            print(f"  WARN {c}: {a_} ({f})")
        print("PASS (Rule 12 metrics)" if not F else f"{len(F)} finding(s)")
        return 1 if F else 0
    if argv[0] in ("zones", "route"):
        if len(argv) < 2:
            raise SystemExit(f"forge pcb {argv[0]} <{argv[0]}.json>")
        rep = _data_cmd(prj, board, ZONES if argv[0] == "zones" else ROUTE, argv[0], argv[1])
        what = (f"{rep['zones']} pour(s)" + (f", {rep['stitch_vias']} stitching via(s)" if rep.get("stitch_vias") else "")) if argv[0] == "zones" else f"{rep['tracks']} track segment(s), {rep['vias']} via(s)"
        if rep.get("stitch_dropped"):
            print(f"  {rep['stitch_dropped']} stitching via(s) under the new tracks removed: run forge pcb zones again to re-stitch")
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
        if argv[1] == "placement" and prj.process.get("approval", "owner") != "hands-free":
            from . import decisions          # the placement milestone is the owner's to approve (human in the loop)
            have = any(d["status"] == "OPEN" and d["question"].startswith("Approve the placement") for d in decisions.load(prj))
            if not have:
                d = decisions.new(prj, f"Approve the placement milestone of {os.path.basename(board)} (connector positions, "
                                  "heatsink side, board size, HF loops) after the review", "G7",
                                  ["approved", "changes needed (say which)"], "approved", "placement is the last cheap point to change the board's shape")
                import sys
                print(f"  {d['id']} opened: the owner approves the placement (forge decision answer {d['id']} ...)", file=sys.stderr)
        return 0
    if argv[0] == "review-status":
        ok, probs, *where = review_status(prj, board)
        if "--json" in argv:
            print(json.dumps({"ok": ok, "problems": probs, "review": where[0] if where else None}))
        else:
            print(("PASS" if ok else "OPEN") + (f" ({where[0]})" if where else "") + "".join(f"\n  {p}" for p in probs))
        return 0 if ok else 1
    if argv[0] == "setup":
        s = setup(prj, board, replace_outline="--replace-outline" in argv)
        print(f"board set up: {s['outline'][0]:g} x {s['outline'][1]:g} mm"
              + (f", {s['layers']} copper layers ({'/'.join(str(x) for x in prj.cfg['pcb']['copper_oz'])} oz)"
                 if s["layers"] is not None else ", copper layers and stack-up unchanged (no copper_oz in [pcb])")
              + (f", min hole {s['min_hole']:g} mm" if s["min_hole"] is not None else "")
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
