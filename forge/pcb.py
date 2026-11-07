"""forge pcb: board-side helpers that KiCad 10's command line does not have. No model calls, no network.

  forge pcb sync [--board B.kicad_pcb] [--prune] [--dry-run]
      Update the board from the schematic, like KiCad's "Update PCB from Schematic" (F8), headless:
      footprints added or swapped (position kept), reference / value / fields / symbol link set, every pad's net
      set from KiCad's own netlist. New footprints land in a staging grid right of the board outline. Footprints
      no longer in the schematic are reported, and removed only with --prune. Board-only items (mounting
      holes, fiducials, logos: footprints with no symbol link) are never touched. Afterwards DRC with schematic
      parity should show no parity issue.

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
    stock = "/Applications/KiCad/KiCad.app/Contents/SharedSupport/footprints"
    if os.path.isdir(stock):
        for d in os.listdir(stock):
            if d.endswith(".pretty"):
                out[d[:-7]] = os.path.join(stock, d)
    env = dict(os.environ)
    for base in sorted(glob_kicad_prefs(), reverse=True):
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
    if f is None and c["ref"] in by_ref and not by_ref[c["ref"]].GetPath().AsString():
        f = by_ref[c["ref"]]                  # placed by hand from the library: adopt it by reference
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
            b.Remove(f); report["swapped"].append(c["ref"])
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
        report["removed"].append(f.GetReference()); b.Remove(f)
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
    r = subprocess.run([py, script, spec], capture_output=True, text=True)
    m = re.search(r"REPORT(\{.*\})", r.stdout)
    if not m:
        raise SystemExit("pcbnew sync failed: " + (r.stderr or r.stdout)[-600:])
    return json.loads(m.group(1)), len(comps)


def main(prj, argv):
    if not argv or argv[0] != "sync":
        print(__doc__)
        return 1
    from . import check
    board = argv[argv.index("--board") + 1] if "--board" in argv else None
    if not board:
        pcbs = check.roots(prj, "kicad_pcb")
        if not pcbs:
            raise SystemExit("no board file under 08_PCB")
        board = pcbs[0]
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
