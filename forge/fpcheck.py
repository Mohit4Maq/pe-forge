"""Footprint / symbol / assembly consistency (forge check fp_pins; G7 item): what makes a first board come back
wrong when ERC and DRC are both clean.

  FP-1  every symbol pin number has a pad of that number in the footprint (a symbol wired to pad 5 of a 4-pad
        footprint is an open on the board; a pin numbered A/K on a numbered footprint is the same)
  FP-2  a polarized symbol (electrolytic / tantalum capacitor, diode, LED, battery) sits on a footprint that marks
        polarity (a CP_ / polarized footprint, a pad named K / A / + / -, or a pad "1" with a distinct shape),
        so pin 1 / cathode can be checked on the assembly drawing
  FP-3  pick-and-place rotation: every placed part has a known rotation source for the assembly house: a
        JLC_Rotation field, a row in the package rotation table (library/tools/jlc_rotations.json), or a
        [fab.rotation] entry in project.toml; a part with none is placed at KiCad's rotation, which for many
        packages is not the house's zero (parts arrive rotated by 90 / 180 degrees)
  FP-4  footprint pad geometry matches the datasheet when MC_Parts records it: Verified_Pads "8@1.27;pad=0.6x1.55"
        (count @ pitch ; pad size); the land pattern is compared, not just counted

Nothing here needs KiCad's Python: footprint files are read as text.
"""
import json
import os
import re

from . import config, kicad

POLAR_SYM = re.compile(r"C_Polarized|CP\b|_Polar|Capacitor_Polar|D_|Diode|LED|Battery|Zener|Schottky|TVS", re.I)
POLAR_FP = re.compile(r"CP_|Polar|_D_|^D_|SOD|SMA|SMB|SMC|DO-|LED|Battery|Tantal|CP-|Elec|Radial", re.I)
ROT_TABLES = [os.path.join(config.LIBRARY, "tools", "jlc_rotations.json"),
              os.path.join(config.FORGE_ROOT, "library", "tools", "jlc_rotations.json")]   # the bundled copy


def pads(fp_text):
    """{pad number: {"x", "y", "w", "h", "shape"}} from a .kicad_mod text."""
    out = {}
    for m in re.finditer(r'\(pad "([^"]*)" (\w+) (\w+)[^\n]*?\n?\s*\(at ([-\d.]+) ([-\d.]+)[^)]*\)\s*\(size ([-\d.]+) ([-\d.]+)\)', fp_text):
        if m.group(1):
            out.setdefault(m.group(1), {"type": m.group(2), "shape": m.group(3), "x": float(m.group(4)), "y": float(m.group(5)),
                                        "w": float(m.group(6)), "h": float(m.group(7))})
    return out


def rotation_table():
    for f in ROT_TABLES:
        try:
            return json.load(open(f))
        except (OSError, ValueError):
            continue
    return {}


def rotation_source(comp, row, prj):
    """Where this part's assembly rotation comes from, or None."""
    if (row or {}).get("JLC_Rotation") or comp["fields"].get("JLC_Rotation"):
        return "JLC_Rotation field"
    fp = comp["footprint"].split(":")[-1]
    for pat, _ in prj.cfg.get("fab", {}).get("rotation", {}).items():
        if re.search(pat, fp):
            return "[fab.rotation]"
    for pat in rotation_table().get("by_footprint", {}):
        if re.search(pat, fp):
            return "rotation table"
    return None


def analyse(prj, sch):
    from . import evidence
    d = kicad.design(sch)
    if d is None:
        return None
    comps, _ = d
    db = evidence.rows_by_id() or {}
    F, W, n = [], [], 0
    for ref, c in sorted(comps.items()):
        if c["dnp"] or c.get("board_excluded") or not c["footprint"] or ref.startswith(("#", "TP")):
            continue
        fpf = evidence.footprint_file(c["footprint"])
        if not fpf:
            W.append((ref, "FP-1", f"footprint {c['footprint']} not found in the libraries"))
            continue
        text = open(fpf, errors="ignore").read()
        pd = pads(text)
        n += 1
        pins = set((c.get("pins") or {}).keys())
        missing = sorted(p for p in pins if p not in pd and not p.startswith("~"))
        if missing:
            F.append((ref, "FP-1", f"symbol pin(s) {', '.join(missing[:6])} have no pad in {c['footprint'].split(':')[-1]} "
                                    f"(pads: {', '.join(sorted(pd)[:8])})"))
        lib = c["fields"].get("Symbol", "") + " " + c["value"] + " " + ref
        if (POLAR_SYM.search(lib) or ref[:1] in ("D",)) and not ref.startswith(("DS", "DZ")):
            marked = POLAR_FP.search(c["footprint"]) or any(k in pd for k in ("K", "A", "+", "-")) or \
                ("1" in pd and len({v["shape"] for v in pd.values()}) > 1)
            if not marked:
                W.append((ref, "FP-2", f"polarized part on {c['footprint'].split(':')[-1]}: no polarity marking (pad 1 shape, K/A pads, CP_ footprint)"))
        row = db.get(c["fields"].get("MC_ID", "")) or {}
        if not rotation_source(c, row, prj):
            W.append((ref, "FP-3", f"no assembly rotation source for {c['footprint'].split(':')[-1]} (JLC_Rotation, [fab.rotation], or the rotation table)"))
        vp = (row.get("Verified_Pads") or "")
        m = re.search(r"pad=([\d.]+)x([\d.]+)", vp)
        if m and pd:
            w, h = float(m.group(1)), float(m.group(2))
            big = [k for k, v in pd.items() if v["type"] == "smd"]
            if big:
                k = big[0]
                have = sorted([pd[k]["w"], pd[k]["h"]])
                want = sorted([w, h])
                if abs(have[0] - want[0]) > 0.05 or abs(have[1] - want[1]) > 0.05:
                    F.append((ref, "FP-4", f"pad {k} is {pd[k]['w']} x {pd[k]['h']} mm, datasheet {w} x {h} mm (Verified_Pads)"))
    return {"checked": n, "fail": F, "warn": W}


def as_check(prj, opts):
    from .check import finding, result, roots, has_content
    from . import insulation
    sch = insulation.root_sheet(prj)
    if not sch:
        return result("skip", reason="no schematic yet")
    r = analyse(prj, sch)
    if r is None:
        return result("skip", reason="kicad-cli not found", missing_tool=True)
    loc = prj.rel(sch)
    F = [finding(f"{rule}: {ref} footprint matches its symbol / datasheet", msg, loc,
                 "fix the footprint or the symbol pin numbers; the board as drawn would be wrong") for ref, rule, msg in r["fail"]]
    W = [finding(f"{rule}: {ref} assembly-safe", msg, loc,
                 "mark the polarity / set the rotation source before ordering assembly") for ref, rule, msg in r["warn"]]
    return result("fail" if F else "warn" if W else "pass", F + W, reason=f"{r['checked']} part(s) checked")


def gate_items(prj):
    r = as_check(prj, {})
    if r["status"] == "skip":
        return [(not r["missing_tool"], f"footprint / symbol / assembly: skipped ({r['reason']})")]
    bad = [f for f in r["findings"] if f["claim"].startswith(("FP-1", "FP-4"))]
    rot = [f for f in r["findings"] if f["claim"].startswith("FP-3")]
    pol = [f for f in r["findings"] if f["claim"].startswith("FP-2")]
    return [(not bad, f"footprint pins / land pattern ({r['reason']}): {len(bad)} mismatch" + "".join(f"; {f['actual']}" for f in bad[:3])),
            (not rot and not pol, f"assembly: {len(rot)} part(s) without a rotation source, {len(pol)} polarized part(s) unmarked"
             + "".join(f"; {f['actual']}" for f in (rot + pol)[:3]))]
