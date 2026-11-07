"""forge protection: surge-protection sizing against the system and every protected pin. No model calls, no network.

  forge protection [--json]

For every clamp part on the schematic (a TVS: MC_Parts Clamp_V filled in), from KiCad's own netlist:
  PROT-1  the clamp does not conduct in normal operation: VRWM >= the system's maximum operating voltage
  PROT-2  at the design surge current the clamp voltage stays within margin x the absolute maximum of every pin on
          the protected net (MC_Parts Abs_Max_V, read from each part's datasheet)
  PROT-3  when PROT-1 and PROT-2 cannot both hold for any standard TVS (clamp ratio too tight), say so: the
          protected part needs a higher rating, or the design an active clamp / series element
  PROT-4  data missing (a clamp without Clamp_V, a protected pin without an Abs_Max_V entry): reported as not
          checked, never as a pass

Data (MC_Parts, from the datasheet, cite the page in Notes):
  Clamp_V    "VRWM=64; VBR_max=78.6; VC=103@5.9A; VC2=133.9@29.5A"   (unidirectional: cathode pin named K)
  Abs_Max_V  "VIN=85; SW=85; EN=VIN+0.3; FB=5.5; PGOOD=20"          (pin name = volts; "NET+x" relative pins are
                                                                       skipped: they follow their reference pin)
System sizing (project.toml):
  [protection]
  v_operating_max = 63      # default: [specs] vin_V maximum
  margin = 0.9              # peak allowed = margin x abs max (e.g. SYS-306: 90 %)
  surge_current_A = 5.9     # design surge current through the clamp; default: the clamp's rated Ipp (worst case)
  source = "SYS-306"
"""
import json
import os
import re

from . import insulation, kicad


def parse_kv(text):
    out = {}
    for part in re.split(r"[;,]", text or ""):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def clamp_data(text):
    """{vrwm, vbr_max, points: [(I, V), ...]} from a Clamp_V string, or None."""
    kv = parse_kv(text)
    if not kv:
        return None
    pts = []
    for k, v in kv.items():
        m = re.match(r"^\s*([\d.]+)\s*@\s*([\d.]+)\s*A\s*$", v)
        if k.upper().startswith("VC") and m:
            pts.append((float(m.group(2)), float(m.group(1))))
    num = lambda k: float(re.match(r"[\d.]+", kv[k]).group(0)) if k in kv and re.match(r"[\d.]+", kv[k]) else None
    return {"vrwm": num("VRWM"), "vbr_max": num("VBR_max"), "points": sorted(pts)}


def v_clamp(cd, current):
    """Clamp voltage at a current: linear between VBR_max at ~0 A and the datasheet VC points (conservative between
    points; beyond the last point extrapolated on the last segment)."""
    pts = ([(0.0, cd["vbr_max"])] if cd["vbr_max"] else []) + cd["points"]
    if not pts:
        return None
    if current is None:
        return cd["points"][0][1] if cd["points"] else None        # rated Ipp (10/1000 us) clamp: worst case
    for (i1, v1), (i2, v2) in zip(pts, pts[1:]):
        if current <= i2:
            return v1 + (v2 - v1) * (current - i1) / (i2 - i1) if i2 > i1 else v2
    (i1, v1), (i2, v2) = pts[-2:] if len(pts) > 1 else (pts[0], pts[0])
    return v2 + (v2 - v1) * (current - i2) / (i2 - i1) if i2 > i1 else v2


def analyse(parts, nets, cfg, db, specs):
    out = {"PROT-1": [], "PROT-2": [], "PROT-3": [], "PROT-4": []}
    v_op = cfg.get("v_operating_max")
    if v_op is None:
        vin = specs.get("vin_V")
        v_op = max(vin) if isinstance(vin, list) and vin else vin
    margin = float(cfg.get("margin", 1.0))
    i_surge = cfg.get("surge_current_A")
    src = cfg.get("source", "project.toml [protection]")
    pin_nets = {}
    for n, v in nets.items():
        for ref, pin, fn in v["pins"]:
            pin_nets[(ref, pin)] = (n, fn)
    checked = []
    for ref, p in sorted(parts.items()):
        row = (db or {}).get(p["mc_id"]) or {}
        if not row.get("Clamp_V"):
            continue
        cd = clamp_data(row["Clamp_V"])
        name = f"{ref} ({p['mpn'] or p['value']})"
        prot = [n for (r, pin), (n, fn) in pin_nets.items() if r == ref and (fn or "").upper() in ("K", "A1", "A2")]
        if not prot:
            prot = [n for (r, pin), (n, fn) in pin_nets.items() if r == ref and pin == "1"]
        vc = v_clamp(cd, i_surge)
        if cd["vrwm"] is not None and v_op is not None and cd["vrwm"] < v_op:
            out["PROT-1"].append({"ref": name, "claim": f"{name} does not conduct at the maximum operating {v_op:g} V",
                                  "actual": f"VRWM {cd['vrwm']:g} V < {v_op:g} V: it conducts (and heats) in normal use",
                                  "fix": f"Choose a clamp with VRWM >= {v_op:g} V"})
        for net in prot:
            for (r, pin), (n, fn) in sorted(pin_nets.items()):
                if n != net or r == ref or r.startswith("#"):
                    continue
                prow = (db or {}).get(parts.get(r, {}).get("mc_id", "")) or {}
                am = parse_kv(prow.get("Abs_Max_V", ""))
                if not am:
                    if r[0] in "UQ":       # active parts: their rating matters; passives are rated by Voltage_Rating
                        out["PROT-4"].append({"ref": r, "claim": f"{r}.{fn or pin} on {insulation.short(net)} has an "
                                                                 "absolute-maximum rating to check",
                                              "actual": f"no Abs_Max_V in MC_Parts {parts[r]['mc_id'] or '?'}: not checked",
                                              "fix": "Copy the pin's absolute maximum from the datasheet into Abs_Max_V"})
                    continue
                lim = am.get(fn) or am.get(pin)
                if lim is None or not re.match(r"^[\d.]+$", lim):
                    continue                                    # relative rating (EN=VIN+0.3) or pin not listed
                limit = margin * float(lim)
                checked.append((name, r, fn or pin))
                if vc is None:
                    out["PROT-4"].append({"ref": name, "claim": f"{name} clamp voltage is known",
                                          "actual": "Clamp_V has no VC point", "fix": "Add VC=<V>@<A> from the datasheet"})
                elif vc > limit:
                    at = f"{i_surge:g} A" if i_surge is not None else "its rated Ipp"
                    out["PROT-2"].append({"ref": name, "claim": f"{name} clamps {insulation.short(net)} within "
                                                                f"{margin:g} x the {float(lim):g} V absolute maximum of "
                                                                f"{r}.{fn or pin} ({limit:g} V, {src})",
                                          "actual": f"clamps at {vc:.1f} V at {at}: {vc - limit:.1f} V over",
                                          "fix": "Lower-clamp TVS, a protected part with a higher rating, or an active "
                                                 "clamp / series element in front of it"})
                    ratio = limit / v_op if v_op else None
                    if ratio and ratio < 1.35:
                        out["PROT-3"].append({"ref": name, "claim": "a standard TVS can meet both limits",
                                              "actual": f"needs VC / VRWM <= {limit:.1f} / {v_op:g} = {ratio:.2f}; standard "
                                                        "avalanche TVS run about 1.6 (e.g. this part "
                                                        f"{(cd['points'][0][1] / cd['vrwm']) if cd['points'] and cd['vrwm'] else 0:.2f})",
                                              "fix": f"Use a protected part rated >= {v_op * 1.6 / margin:.0f} V, or an "
                                                     "active clamp / surge stopper; a TVS alone cannot do it"})
    for k in out:                       # one PROT-3 per clamp
        if k == "PROT-3":
            seen, uniq = set(), []
            for x in out[k]:
                if x["ref"] not in seen:
                    seen.add(x["ref"]); uniq.append(x)
            out[k] = uniq
    return out, {"v_operating_max": v_op, "margin": margin, "surge_current_A": i_surge, "checked": checked}


def as_check(prj, opts):
    from .check import finding, result
    sch = insulation.root_sheet(prj)
    if not sch:
        return result("skip", reason="no schematic yet")
    text = insulation.export(sch)
    if text is None:
        return result("skip", reason="kicad-cli not found or the netlist export failed", missing_tool=True)
    parts, nets = insulation.parse(text)
    db = insulation.db_rows()
    if not any(((db or {}).get(p["mc_id"]) or {}).get("Clamp_V") for p in parts.values()):
        return result("skip", reason="no clamp part with Clamp_V on the schematic")
    found, info = analyse(parts, nets, prj.cfg.get("protection", {}), db, prj.cfg.get("specs", {}))
    loc = prj.rel(sch)
    F = [finding(f"{k}: {x['claim']}", x["actual"], loc, x["fix"]) for k in ("PROT-1", "PROT-2", "PROT-3")
         for x in found[k]]
    W = [finding(f"PROT-4: {x['claim']}", x["actual"], loc, x["fix"]) for x in found["PROT-4"]]
    reason = (f"{len(info['checked'])} protected pin(s), max operating {info['v_operating_max']} V, "
              f"margin {info['margin']:g}")
    return result("fail" if F else "warn" if W else "pass", F + W, reason=reason)


def main(prj, argv):
    r = as_check(prj, {})
    if "--json" in argv:
        print(json.dumps(r, indent=1))
    else:
        print(f"{r['status'].upper()}" + (f" ({r['reason']})" if r["reason"] else ""))
        for f in r["findings"]:
            print(f"  {f['claim']}\n    {f['actual']}" + (f"\n    fix: {f['fix']}" if f.get("fix") else ""))
    return 1 if r["status"] == "fail" else 0
