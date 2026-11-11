"""forge stress: every part's rating against what the design puts on it, with derating. No model, no network.

  forge stress [--json]

From KiCad's own netlist, the parts' ratings (MC_Parts, or the value text "22uF 25V") and the project's operating
corners ([specs], [corners]), for every part whose nets have a known voltage:

  STR-1  capacitor:    V across <= cap_v x Voltage_Rating            (default 0.8: MLCC DC-bias, electrolytic life)
  STR-2  IC pin:       V on the pin <= Voltage_Rating (recommended operating max), or, if only the absolute
                       maximum is known, <= ic_abs x Abs_Max_V (default 0.8)
  STR-3  transistor:   V(D) - V(S) (or C - E) <= semi_v x Voltage_Rating   (default 0.8)
  STR-4  diode:        reverse V(K) - V(A) <= semi_v x Voltage_Rating       (TVS: see forge protection)
  STR-5  inductor:     I_sat >= i_pk (the peak inductor current) and I_rms >= the output current (default: the
                       [specs] output current; a simulation result's "peak current" metric when there is one)
  STR-6  temperature:  every part's Temp_Range max >= the ambient corner max + rise_C (default 20 K)
  STR-7  not checked:  a part on a known-voltage net with no rating anywhere, or a net with no known voltage,
                       is listed. That is a gap in the data, never a pass.

Net voltages come from the design: the input path (connector, fuse, filter: VIN*, VBAT, DC_IN ...) and the
switch node (SW, LX, PH) carry [specs] vin_V maximum; a rail named for its voltage (+12V, 3V3) carries that;
GND-like nets 0 V. Anything else is unknown unless [stress.nets] names it: `"BST" = 70`.

  [stress]
  cap_v = 0.8      ic_abs = 0.8      semi_v = 0.8      rise_C = 20
  i_pk_A = 3.8     # the peak inductor current to size against (default: 1.2 x the output current)
  i_out_A = 1.0    # the output current (default: [specs] iout_A maximum, or power_W / vout_V)
  [stress.nets]
  "BST" = 75
"""
import json
import os
import re

from . import insulation, reqinfer

NUM_V = re.compile(r"(\d+(?:\.\d+)?)\s*k?V(?!A)(?:rms|dc|DC)?\b", re.I)
NUM_A = re.compile(r"(\d+(?:\.\d+)?)\s*A\b")
TEMP_MAX = re.compile(r"(-?\d+)\s*°?\s*C?\s*(?:~|to|-|–|/)\s*\+?(\d+)\s*°?\s*C", re.I)
SW = re.compile(r"^(SW|LX|PH|SWITCH|PHASE)(\d|_|$)", re.I)
DEFAULTS = {"cap_v": 0.8, "ic_abs": 0.8, "semi_v": 0.8, "rise_C": 20.0}


def _kind(ref):
    m = re.match(r"^[A-Z]+", ref)
    return m.group(0) if m else ""


def _num(rx, *texts):
    for t in texts:
        m = rx.search(str(t or ""))
        if m:
            v = float(m.group(1))
            if "k" in m.group(0).lower() and rx is NUM_V:
                v *= 1000
            return v
    return None


def _temp_max(text):
    m = TEMP_MAX.search(str(text or ""))
    return float(m.group(2)) if m else None


def net_voltages(parts, nets, specs, overrides=None):
    """{net: (lo, hi)} from the design and [specs]; nets not known are absent. A rail is fixed (lo = hi); a switch
    node swings between 0 and the input, so a part across it sees the whole swing. Uses reqinfer's rail analysis."""
    a = reqinfer.analyse(parts, nets, None)
    vin = specs.get("vin_V")
    vmax = max(vin) if isinstance(vin, list) and vin else vin
    V = {}
    for n in nets:
        if reqinfer.GND.match(reqinfer.short(n)):
            V[n] = (0.0, 0.0)
    for n, r in a["rails"].items():
        if r["role"] in ("input", "input path") and vmax:
            V[n] = (float(vmax), float(vmax))
        elif r["nominal_V"] is not None:
            V[n] = (float(r["nominal_V"]), float(r["nominal_V"]))
    for n in nets:
        if SW.match(reqinfer.short(n)) and vmax:
            V[n] = (0.0, float(vmax))
    for k, v in (overrides or {}).items():
        for n in nets:
            if reqinfer.short(n) == k or n == k:
                V[n] = (float(v), float(v)) if not isinstance(v, list) else (float(min(v)), float(max(v)))
    return V, a


def across(V, a, b):
    """The largest voltage a part between nets a and b can see."""
    return max(V[a][1] - V[b][0], V[b][1] - V[a][0], 0.0)


def rating_of(P, ref):
    """(volts, source) the part is rated for in normal operation, or (None, '')."""
    r = P.row(ref)
    v = _num(NUM_V, r.get("Voltage_Rating"))
    if v is not None:
        return v, "Voltage_Rating"
    v = _num(NUM_V, P.parts[ref]["value"], r.get("Description"))
    if v is not None:
        return v, "value text"
    return None, ""


def net_limit(P, nets, net):
    """The lowest operating-voltage rating of any rated part on `net` (ICs by their pin's Abs_Max_V x ic_abs when
    they have no Voltage_Rating), with who limits it, and the parts with no rating at all."""
    from .protection import parse_kv
    best, by, unrated = None, "", []
    for ref, pin, fn in nets[net]["pins"]:
        if ref not in P.parts:
            continue
        k = _kind(ref)
        if k in ("J", "P", "TP", "H", "MH", "R", "F", "FB", "L", "T"):
            continue                     # a connector, resistor or inductor does not set the rail's limit
        if k in ("C", "D", "Z") and len(P.parts[ref]["nets"]) == 2:      # a two-terminal part limits the rail only
            other = [n for n in P.parts[ref]["nets"] if n != net]        # when it sits between the rail and GND
            if other and not reqinfer.GND.match(reqinfer.short(other[0])):   # (a gate Zener, a boot cap: no)
                continue
        row = P.row(ref)
        v, src = rating_of(P, ref)
        am = parse_kv(row.get("Abs_Max_V", ""))
        if k in ("U", "IC") and fn and am.get(fn) and re.match(r"^\d", am[fn]):
            va = float(re.match(r"^\d+(?:\.\d+)?", am[fn]).group(0)) * DEFAULTS["ic_abs"]
            if v is None or va < v:
                v, src = va, f"Abs_Max_V {fn}={am[fn]} x {DEFAULTS['ic_abs']}"
        if v is None:
            unrated.append(ref)
            continue
        if best is None or v < best:
            best, by = v, f"{P.label(ref)} ({src})"
    return best, by, unrated


def analyse(parts, nets, db, cfg, specs, corners, i_pk=None):
    P = reqinfer.Parts(parts, db)
    st = dict(DEFAULTS, **{k: v for k, v in cfg.items() if k in DEFAULTS})
    V, a = net_voltages(parts, nets, specs, cfg.get("nets"))
    F, W, unchecked, checked = [], [], [], 0
    seen_unknown = set()

    def v_of(net):
        if net in V:
            return V[net]
        seen_unknown.add(net)
        return None

    for ref, p in parts.items():
        k = _kind(ref)
        row = P.row(ref)
        pins = [(n, pin, fn) for n in nets for (r_, pin, fn) in nets[n]["pins"] if r_ == ref]
        pnet = {fn or pin: n for n, pin, fn in pins}
        label = P.label(ref)
        if k == "C" and len(pins) >= 2:
            vs = [v_of(n) for n, _, _ in pins[:2]]
            if None in vs:
                continue
            across_v = across(V, pins[0][0], pins[1][0])
            rated, src = rating_of(P, ref)
            if rated is None:
                unchecked.append(f"{label}: no voltage rating (MC_Parts Voltage_Rating or the value text)")
                continue
            checked += 1
            lim = st["cap_v"] * rated
            if across_v > lim:
                F.append({"rule": "STR-1", "claim": f"{label}: {across_v:g} V across <= {st['cap_v']:g} x {rated:g} V rating",
                          "actual": f"{across_v:g} V > {lim:g} V ({src})", "fix": f"a capacitor rated >= {across_v / st['cap_v']:.0f} V"})
        elif k in ("U", "IC"):
            from .protection import parse_kv
            am = parse_kv(row.get("Abs_Max_V", ""))
            vr, src = rating_of(P, ref)
            any_pin = False
            for n, pin, fn in pins:
                v = V[n][1] if n in V else None
                if v is None or v <= 0:
                    continue
                any_pin = True
                lim, why = None, ""
                if vr is not None:
                    lim, why = vr, f"{src} {vr:g} V"
                if fn and am.get(fn) and re.match(r"^\d", am[fn]):
                    va = float(re.match(r"^\d+(?:\.\d+)?", am[fn]).group(0)) * st["ic_abs"]
                    if lim is None or va < lim:
                        lim, why = va, f"{st['ic_abs']:g} x Abs_Max_V {fn}={am[fn]}"
                if lim is None:
                    unchecked.append(f"{label}.{fn or pin} on {reqinfer.short(n)} ({v:g} V): no Voltage_Rating or Abs_Max_V")
                    continue
                checked += 1
                if v > lim:
                    F.append({"rule": "STR-2", "claim": f"{label}.{fn or pin} on {reqinfer.short(n)}: {v:g} V <= {why}",
                              "actual": f"{v:g} V > {lim:g} V", "fix": "a part rated for the rail, or a lower rail"})
            if not any_pin and pins:
                pass
        elif k in ("Q", "M", "T") and k != "T":
            d = pnet.get("D") or pnet.get("C")
            s = pnet.get("S") or pnet.get("E")
            if d and s and v_of(d) is not None and v_of(s) is not None:
                vds = across(V, d, s)
                rated, src = rating_of(P, ref)
                if rated is None:
                    unchecked.append(f"{label}: no Vds / Vce rating")
                    continue
                checked += 1
                lim = st["semi_v"] * rated
                if vds > lim:
                    F.append({"rule": "STR-3", "claim": f"{label}: {vds:g} V D-S <= {st['semi_v']:g} x {rated:g} V",
                              "actual": f"{vds:g} V > {lim:g} V ({src})", "fix": f"a transistor rated >= {vds / st['semi_v']:.0f} V"})
        elif k == "D" and not row.get("Clamp_V"):
            kk, aa = pnet.get("K"), pnet.get("A")
            if kk and aa and v_of(kk) is not None and v_of(aa) is not None:
                vr_ = V[kk][1] - V[aa][0]
                rated, src = rating_of(P, ref)
                if rated is None:
                    unchecked.append(f"{label}: no reverse voltage rating")
                    continue
                checked += 1
                lim = st["semi_v"] * rated
                if vr_ > lim:
                    F.append({"rule": "STR-4", "claim": f"{label}: reverse {vr_:g} V <= {st['semi_v']:g} x {rated:g} V",
                              "actual": f"{vr_:g} V > {lim:g} V ({src})", "fix": f"a diode rated >= {vr_ / st['semi_v']:.0f} V"})
        elif k == "L":
            cr = str(row.get("Current_Rating") or "")
            amps = [float(x) for x in NUM_A.findall(cr + " " + str(row.get("Description") or ""))]
            if not amps:
                amps = [float(x) for x in NUM_A.findall(p["value"])]
            if not amps:
                unchecked.append(f"{label}: no current rating (Isat / Irms)")
                continue
            isat, irms = max(amps), min(amps)
            ipk = i_pk if i_pk is not None else cfg.get("i_pk_A")
            iout = cfg.get("i_out_A") or _iout(specs)
            if ipk is None and iout:
                ipk = 1.2 * iout
            if ipk is None:
                unchecked.append(f"{label}: no peak current to size against ([specs] iout_A or [stress] i_pk_A)")
                continue
            checked += 1
            if isat < ipk:
                F.append({"rule": "STR-5", "claim": f"{label}: I_sat {isat:g} A >= peak {ipk:g} A",
                          "actual": f"saturates: {isat:g} A < {ipk:g} A", "fix": "an inductor with I_sat above the peak (overload included)"})
            if iout and irms < iout:
                F.append({"rule": "STR-5", "claim": f"{label}: I_rms {irms:g} A >= output {iout:g} A",
                          "actual": f"{irms:g} A < {iout:g} A: overheats", "fix": "an inductor with I_rms above the output current"})
        # temperature: every part with a range
        amb = corners.get("ambient_C")
        amax = max(amb) if isinstance(amb, list) and amb else amb
        tmax = _temp_max(row.get("Temp_Range"))
        if amax is not None and tmax is not None and tmax < amax + st["rise_C"]:
            W.append({"rule": "STR-6", "claim": f"{label}: rated to {tmax:g} °C >= ambient {amax:g} + {st['rise_C']:g} K rise",
                      "actual": f"{tmax:g} °C < {amax + st['rise_C']:g} °C", "fix": "a wider temperature grade, or a measured rise below the margin"})
    # every net with a part on it (not just a connector or test point) and no known voltage: listed, so the
    # owner can name it in [stress.nets] or see which parts went unchecked
    unknown = sorted(reqinfer.short(n) for n in nets if n not in V and not n.startswith("unconnected-")
                     and any(_kind(r_) not in ("J", "P", "TP", "H", "MH") for r_, _, _ in nets[n]["pins"]))
    ipk_used = i_pk if i_pk is not None else cfg.get("i_pk_A")
    return {"fail": F, "warn": W, "unchecked": unchecked, "unknown_nets": unknown, "checked": checked,
            "voltages": {reqinfer.short(n): (v[1] if v[0] == v[1] else list(v)) for n, v in V.items() if v[1]},
            "i_pk_A": ipk_used}


def _iout(specs):
    io = specs.get("iout_A")
    if isinstance(io, list) and io:
        return max(io)
    if io:
        return float(io)
    if specs.get("power_W") and specs.get("vout_V"):
        vo = specs["vout_V"]
        vo = max(vo) if isinstance(vo, list) else vo
        try:
            return float(specs["power_W"]) / float(vo)
        except (TypeError, ZeroDivisionError):
            return None
    return None


def peak_from_results(prj):
    """The largest 'peak current' value in the project's simulation results, if any (the designed-in overload)."""
    try:
        from . import results
        best = None
        for f in results.result_files(prj):
            d = json.load(open(f))
            for m in d.get("metrics") or []:
                if re.search(r"peak.*current|current.*peak|i_?pk|i_?l,?\s*pk", m.get("name", ""), re.I) and \
                        str(m.get("unit", "")).startswith("A"):
                    vals = [v for v in (m.get("values") or []) if isinstance(v, (int, float))]
                    if vals:
                        best = max(best or 0, max(vals))
        return best
    except Exception:
        return None


def as_check(prj, opts):
    from .check import finding, result
    sch = insulation.root_sheet(prj)
    if not sch:
        return result("skip", reason="no schematic yet")
    text = insulation.export(sch)
    if text is None:
        return result("skip", reason="kicad-cli not found or the netlist export failed", missing_tool=True)
    parts, nets = insulation.parse(text)
    specs, corners = prj.cfg.get("specs", {}), prj.cfg.get("corners", {})
    cfg = prj.cfg.get("stress", {})
    r = analyse(parts, nets, insulation.db_rows(), cfg, specs, corners, i_pk=cfg.get("i_pk_A") or peak_from_results(prj))
    loc = prj.rel(sch)
    F = [finding(f"{x['rule']}: {x['claim']}", x["actual"], loc, x["fix"]) for x in r["fail"]]
    W = [finding(f"{x['rule']}: {x['claim']}", x["actual"], loc, x["fix"]) for x in r["warn"]]
    W += [finding("STR-7: every part on a known-voltage net has a rating", u, loc,
                  "fill Voltage_Rating / Abs_Max_V / Current_Rating in MC_Parts from the datasheet") for u in r["unchecked"]]
    if not specs.get("vin_V"):
        W.append(finding("STR-7: the input voltage is known", "no [specs] vin_V: input-path parts not checked", "project.toml",
                         "set [specs] vin_V = [min, max]"))
    reason = f"{r['checked']} rating(s) checked, {len(r['unchecked'])} not checked"
    if r.get("i_pk_A"):
        reason += (f"; inductors sized against {r['i_pk_A']:g} A peak ("
                   + ("[stress] i_pk_A" if cfg.get("i_pk_A") else "the simulation results: set [stress] i_pk_A to the overload / current-limit peak if higher") + ")")
    if r["unknown_nets"]:
        reason += f"; nets with unknown voltage: {', '.join(r['unknown_nets'][:6])}" + ("…" if len(r["unknown_nets"]) > 6 else "")
    if not r["checked"] and not F:
        return result("warn" if W else "skip", W, reason="nothing could be checked: " + reason)
    return result("fail" if F else "warn" if W else "pass", F + W, reason=reason)


def gate_items(prj):
    r = as_check(prj, {})
    if r["status"] == "skip":
        return [(not r["missing_tool"] and "no schematic" in r["reason"], f"stress / derating: skipped ({r['reason']})")]
    bad = [f for f in r["findings"] if f["claim"].startswith(("STR-1", "STR-2", "STR-3", "STR-4", "STR-5"))]
    gaps = [f for f in r["findings"] if f["claim"].startswith("STR-7")]
    return [(r["status"] != "fail", f"stress / derating ({r['reason']}): {len(bad)} over rating"
             + "".join(f"; {f['claim']}: {f['actual']}" for f in bad[:3])),
            (not gaps, f"stress / derating data: {len(gaps)} part(s) or rail(s) with no rating to check against"
             + "".join(f"; {f['actual']}" for f in gaps[:3]) + (" (fill MC_Parts, or [stress.nets])" if gaps else ""))]


def main(prj, argv):
    r = as_check(prj, {})
    if "--json" in argv:
        print(json.dumps(r, indent=1))
    else:
        print(f"{r['status'].upper()}" + (f" ({r['reason']})" if r["reason"] else ""))
        for f in r["findings"]:
            print(f"  {f['claim']}\n    {f['actual']}" + (f"\n    fix: {f['fix']}" if f.get("fix") else ""))
    return 1 if r["status"] == "fail" else 0
