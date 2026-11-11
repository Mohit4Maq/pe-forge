"""forge calc: closed-form power-electronics calculations, and the recomputation of every simulation result's
closed form from the design itself (no model, no network).

  forge calc buck  --vin 36,63 --vout 12 --iout 1 --f 400k --L 33u --C 44u [--esr 5m] [--json]
  forge calc boost --vin 9,18  --vout 48 --iout 0.5 --f 200k --L 10u --C 100u [--json]
  forge calc design [--json]      the design parameters pe-forge reads from the project (specs, corners, the
                                  schematic's inductor and output capacitance)
  forge calc check [--json]       every *.result.json: its closed_form recomputed here from the design; a metric
                                  whose closed form this tool cannot reproduce within tolerance is a problem

Formulas (CCM, ideal switches; the same ones pwrlab validates against):
  buck   D = Vo/Vin   dI_L = (Vin - Vo) D / (f L)   I_pk = Io + dI_L/2   I_L,rms = sqrt(Io^2 + dI_L^2/12)
         dV_o = dI_L / (8 f C) + dI_L ESR          I_C,rms = dI_L / sqrt(12)     CCM while Io > dI_L/2
  boost  D = 1 - Vin/Vo   I_L = Io/(1-D)   dI_L = Vin D / (f L)   I_pk = I_L + dI_L/2   dV_o = Io D / (f C) + I_pk ESR

A result file may carry "params": {"f_sw_Hz": 400e3, "L_H": 33e-6, "C_out_F": 44e-6, "esr_ohm": 0.005} saying
what the simulation used; then the recomputation is exact (tolerance 5 %). Without it the design's own values
are used (the schematic's L, the output rail's capacitance, [corners] f_sw_kHz) and the nearest corner frequency is
tried (tolerance 10 %). Metrics are matched by name: inductor ripple, peak current, output ripple, duty, RMS.
"""
import json
import math
import os
import re

SI = {"p": 1e-12, "n": 1e-9, "u": 1e-6, "µ": 1e-6, "μ": 1e-6, "m": 1e-3, "k": 1e3, "M": 1e6, "G": 1e9}


def si(text):
    """'33uH' / '33µ' / '2.2uF' / '400k' / '4n7' -> float, or None."""
    s = str(text or "").strip().replace(" ", "")
    m = re.match(r"^(\d+(?:\.\d+)?)([pnuµμmkMG]?)(?:[HFAVWΩ]|Hz|ohm)?$", s, re.I)
    if m:
        pre = m.group(2)
        pre = pre if pre in SI else pre.lower() if pre.lower() in SI else pre
        return float(m.group(1)) * SI.get(pre, 1.0)
    m = re.match(r"^(\d+)([pnuµμmk])(\d+)$", s)          # 4n7, 2u2
    if m:
        return float(f"{m.group(1)}.{m.group(3)}") * SI[m.group(2)]
    return None


def buck(vin, vout, iout, f, L, C, esr=0.0):
    D = vout / vin
    di = (vin - vout) * D / (f * L)
    ipk = iout + di / 2
    return {"D": D, "dI_L": di, "I_pk": ipk, "I_L_rms": math.sqrt(iout ** 2 + di ** 2 / 12),
            "dV_out": di / (8 * f * C) + di * esr, "I_C_rms": di / math.sqrt(12), "ccm": iout > di / 2,
            "I_in_avg": vout * iout / vin}


def boost(vin, vout, iout, f, L, C, esr=0.0):
    D = 1 - vin / vout
    il = iout / (1 - D)
    di = vin * D / (f * L)
    ipk = il + di / 2
    return {"D": D, "I_L": il, "dI_L": di, "I_pk": ipk, "I_L_rms": math.sqrt(il ** 2 + di ** 2 / 12),
            "dV_out": iout * D / (f * C) + ipk * esr, "ccm": il > di / 2}


TOPO = {"buck": buck, "boost": boost}

# which metric name means which closed-form key
METRIC = [(re.compile(r"inductor.*ripple|ripple.*inductor|Δ\s*I_?L|dI_?L|current ripple", re.I), "dI_L", "A"),
          (re.compile(r"peak.*(inductor|current)|inductor.*peak|I_?pk|I_?L,?\s*pk", re.I), "I_pk", "A"),
          (re.compile(r"output.*ripple|ripple.*output|Δ\s*V_?out|dV_?out|voltage ripple", re.I), "dV_out", "V"),
          (re.compile(r"\bduty\b", re.I), "D", ""),
          (re.compile(r"inductor.*rms|rms.*inductor|I_?L,?\s*rms", re.I), "I_L_rms", "A")]
UNIT = {"mV": 1e-3, "V": 1.0, "mA": 1e-3, "A": 1.0, "%": 0.01, "": 1.0}


def unit_scale(unit, kind):
    u = str(unit or "").split()[0] if unit else ""
    if kind == "V":
        return UNIT.get(u, 1.0) if u in ("mV", "V") else 1.0
    if kind == "A":
        return UNIT.get(u, 1.0) if u in ("mA", "A") else 1.0
    return 0.01 if u == "%" else 1.0


# ------------------------------------------------------------------ the design's own parameters
def design(prj):
    """{topology, vin: [lo, hi], vout, iout, f_sw: [lo, hi] Hz, L_H, C_out_F, sources: {...}}, from [specs],
    [corners] and the schematic (the inductor on the output rail, the capacitors on it)."""
    specs, corners = prj.cfg.get("specs", {}), prj.cfg.get("corners", {})
    typ = (prj.cfg.get("project", {}).get("type") or "").lower()
    topo = specs.get("topology") or ("buck" if "buck" in (typ + " " + prj.name.lower()) else
                                     "boost" if "boost" in (typ + " " + prj.name.lower()) else None)
    vin = corners.get("vin_V") or specs.get("vin_V")
    vin = [float(min(vin)), float(max(vin))] if isinstance(vin, list) else ([float(vin)] * 2 if vin else None)
    vo = specs.get("vout_V")
    vo = float(vo if not isinstance(vo, list) else vo[0]) if vo else None
    io = specs.get("iout_A")
    io = float(max(io) if isinstance(io, list) else io) if io else (float(specs["power_W"]) / vo if specs.get("power_W") and vo else None)
    f = corners.get("f_sw_kHz") or specs.get("f_sw_kHz")
    f = [float(min(f)) * 1e3, float(max(f)) * 1e3] if isinstance(f, list) else ([float(f) * 1e3] * 2 if f else None)
    d = {"topology": topo, "vin": vin, "vout": vo, "iout": io, "f_sw": f, "L_H": None, "C_out_F": None, "sources": {}}
    try:
        from . import insulation, reqinfer
        sch = insulation.root_sheet(prj)
        text = insulation.export(sch) if sch else None
        if text:
            parts, nets = insulation.parse(text)
            a = reqinfer.analyse(parts, nets, None)
            outs = set(a["outputs"])
            if vo is not None:
                outs |= {n for n, r in a["rails"].items() if r["nominal_V"] == vo}
            Ls = [(r, si(p["value"])) for r, p in parts.items() if r.startswith("L") and set(p["nets"]) & outs and si(p["value"])]
            if Ls:
                d["L_H"], d["sources"]["L"] = Ls[0][1], Ls[0][0]
            Cs = [(r, si(p["value"])) for r, p in parts.items() if r.startswith("C") and si(p["value"])
                  and any(n in outs for n in p["nets"]) and any(reqinfer.GND.match(reqinfer.short(n)) for n in p["nets"])]
            if Cs:
                d["C_out_F"], d["sources"]["C_out"] = sum(c for _, c in Cs), "+".join(r for r, _ in Cs)
    except Exception as e:                                   # the schematic is optional here
        d["sources"]["error"] = f"{type(e).__name__}: {e}"
    return d


def topo_params(d, params=None):
    """(f list, L, C, esr) to recompute with: the result's own params when given, else the design's."""
    p = params or {}
    f = [float(p["f_sw_Hz"])] if p.get("f_sw_Hz") else d["f_sw"]
    return f, float(p["L_H"]) if p.get("L_H") else d["L_H"], float(p["C_out_F"]) if p.get("C_out_F") else d["C_out_F"], \
        float(p.get("esr_ohm") or 0.0)


def corner_vin(label, d):
    m = re.search(r"(\d+(?:\.\d+)?)\s*V", str(label))
    if m:
        return float(m.group(1))
    return None


def recompute(result, d):
    """For one result document: [(metric, key, corner, stated_closed_form, recomputed, ok, note)]."""
    out = []
    topo = TOPO.get((result.get("topology") or d.get("topology") or "").lower())
    if not topo or not d.get("vout") or not d.get("iout"):
        return out, "no topology / vout / iout to recompute with ([specs] topology, vout_V, iout_A)"
    fs, L, C, esr = topo_params(d, result.get("params"))
    if not fs or not L or not C:
        return out, "no f_sw / L / C to recompute with (result params, [corners] f_sw_kHz, the schematic)"
    strict = bool(result.get("params"))
    tol = 0.05 if strict else 0.10
    corners = result.get("corners") or []
    for m in result.get("metrics") or []:
        key, kind = next(((k, u) for rx, k, u in METRIC if rx.search(m.get("name", ""))), (None, None))
        cf = m.get("closed_form") or []
        if not key or not cf:
            continue
        scale = unit_scale(m.get("unit"), kind)
        for i, stated in enumerate(cf):
            if not isinstance(stated, (int, float)):
                continue
            label = corners[i] if i < len(corners) else ""
            vin = corner_vin(label, d) or (d["vin"][1] if d.get("vin") else None)
            if vin is None:
                continue
            cands = []
            for f in (fs if len(fs) == 1 else sorted({fs[0], fs[1], (fs[0] + fs[1]) / 2})):
                r = topo(vin, d["vout"], d["iout"], f, L, C, esr)
                if key in r:
                    cands.append((f, r[key]))
            if not cands:
                continue
            f, val = min(cands, key=lambda x: abs(x[1] - stated * scale))
            err = abs(val - stated * scale) / max(abs(stated * scale), 1e-12)
            out.append({"metric": m["name"], "key": key, "corner": label, "stated": stated,
                        "recomputed": round(val / scale, 4), "f_Hz": f, "ok": err <= tol, "err_pct": round(err * 100, 2),
                        "tol_pct": tol * 100})
    return out, ""


def check(prj):
    from . import results
    d = design(prj)
    rows, notes = [], []
    for f in results.result_files(prj):
        try:
            doc = json.load(open(f))
        except (OSError, ValueError):
            continue
        rs, why = recompute(doc, d)
        if why:
            notes.append(f"{prj.rel(f)}: {why}")
        for r in rs:
            r["file"] = prj.rel(f)
            rows.append(r)
    return d, rows, notes


def as_check(prj, opts):
    from .check import finding, result
    d, rows, notes = check(prj)
    if not rows and not notes:
        return result("skip", reason="no simulation results with a closed form")
    F = [finding(f"{r['file']}: {r['metric']} at {r['corner']}: closed form reproduces ({r['key']})",
                 f"stated {r['stated']}, pe-forge computes {r['recomputed']} at f = {r['f_Hz']:g} Hz ({r['err_pct']} % > {r['tol_pct']:g} %)",
                 r["file"], "check the formula, the parameters the simulation used (write them in \"params\"), or the units")
         for r in rows if not r["ok"]]
    W = [finding("every result can be recomputed", n, "", "set [specs] topology / vout_V / iout_A, [corners] f_sw_kHz, "
                 "or \"params\" in the result file") for n in notes]
    src = ", ".join(f"{k} {v}" for k, v in d["sources"].items() if k != "error")
    reason = f"{len(rows)} closed form(s) recomputed, {len(F)} off" + (f"; design: L {d['L_H']}, C_out {d['C_out_F']} ({src})" if d["L_H"] else "")
    return result("fail" if F else "warn" if W else "pass", F + W, reason=reason)


def _arg(argv, k, default=None):
    return argv[argv.index(k) + 1] if k in argv else default


def _vals(s):
    return [si(x) for x in str(s).split(",")]


def main(prj, argv):
    if argv[:1] in (["buck"], ["boost"]):
        fn = TOPO[argv[0]]
        try:
            vins = _vals(_arg(argv, "--vin"))
            vo, io, f = si(_arg(argv, "--vout")), si(_arg(argv, "--iout")), si(_arg(argv, "--f"))
            L, C, esr = si(_arg(argv, "--L")), si(_arg(argv, "--C")), si(_arg(argv, "--esr", "0"))
            assert None not in (vo, io, f, L, C) and all(v is not None for v in vins)
        except (TypeError, AssertionError):
            raise SystemExit(f"forge calc {argv[0]} --vin 36,63 --vout 12 --iout 1 --f 400k --L 33u --C 44u [--esr 5m]")
        rows = {f"{v:g} V": fn(v, vo, io, f, L, C, esr) for v in vins}
        if "--json" in argv:
            print(json.dumps(rows, indent=1))
            return 0
        keys = list(next(iter(rows.values())))
        print(f"{argv[0]}: Vout {vo:g} V, Iout {io:g} A, f {f:g} Hz, L {L:g} H, C {C:g} F, ESR {esr:g} Ω")
        print("  " + "corner".ljust(10) + "".join(k.rjust(11) for k in keys))
        for c, r in rows.items():
            print("  " + c.ljust(10) + "".join((f"{r[k]:.4g}" if isinstance(r[k], float) else str(r[k])).rjust(11) for k in keys))
        return 0
    if argv[:1] == ["design"]:
        if prj is None:
            raise SystemExit("forge calc design needs a project")
        d = design(prj)
        print(json.dumps(d, indent=1) if "--json" in argv else
              "\n".join(f"  {k}: {v}" for k, v in d.items()))
        return 0
    if argv[:1] == ["check"]:
        if prj is None:
            raise SystemExit("forge calc check needs a project")
        r = as_check(prj, {})
        if "--json" in argv:
            print(json.dumps(r, indent=1))
        else:
            print(f"{r['status'].upper()} ({r['reason']})")
            for x in r["findings"]:
                print(f"  {x['claim']}\n    {x['actual']}")
        return 1 if r["status"] == "fail" else 0
    print(__doc__)
    return 1
