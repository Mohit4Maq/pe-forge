"""forge calc: closed-form power-electronics calculations, and the recomputation of every simulation result's
closed form from the design itself (no model, no network).

  forge calc buck|boost|buck-boost|flyback|forward --vin 36,63 --vout 12 --iout 1 --f 400k --L 33u --C 44u [--esr 5m] [--n 4]
  forge calc dab --v1 400 --v2 48 --f 100k --L 20u --phi 30 [--n 8]      forge calc pfc --vac 230 --pout 3300 --f1 50 --C 1m --vout 400
  forge calc inverter|threelevel --vdc 800 --m 0.9 --f1 50 --p 11000 [--pf 0.95]
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


def buck_boost(vin, vout, iout, f, L, C, esr=0.0):
    """Inverting / four-switch buck-boost in CCM: D = Vo / (Vin + Vo), I_L = Io / (1 - D)."""
    vo = abs(vout)
    D = vo / (vin + vo)
    il = iout / (1 - D)
    di = vin * D / (f * L)
    return {"D": D, "I_L": il, "dI_L": di, "I_pk": il + di / 2, "I_L_rms": math.sqrt(il ** 2 + di ** 2 / 12),
            "dV_out": iout * D / (f * C) + (il + di / 2) * esr, "ccm": il > di / 2}


def flyback(vin, vout, iout, f, L, C, esr=0.0, n=1.0, vf=0.5):
    """Flyback (n = Np / Ns) in CCM: D = n (Vo + Vf) / (Vin + n (Vo + Vf)); primary magnetising ripple and peak;
    secondary peak = primary peak x n; output ripple from the discontinuous secondary current."""
    vr = n * (vout + vf)
    D = vr / (vin + vr)
    pin = vout * iout / 0.85
    i_pri_avg = pin / vin
    il = i_pri_avg / D                      # primary current during D (plateau mean)
    di = vin * D / (f * L)                  # L = primary magnetising inductance
    ipk = il + di / 2
    return {"D": D, "I_pri_pk": ipk, "I_sec_pk": ipk * n, "dI_L": di, "I_pk": ipk,
            "V_ds_off": vin + vr, "V_d_rev": vout + vin / n, "dV_out": iout * D / (f * C) + ipk * n * esr,
            "ccm": il > di / 2, "P_in_est": pin}


def forward(vin, vout, iout, f, L, C, esr=0.0, n=1.0):
    """Single-switch forward (n = Np / Ns): D = n Vo / Vin (D <= 0.5 with a 1:1 reset), output choke like a buck."""
    vsec = vin / n
    D = vout / vsec
    di = (vsec - vout) * D / (f * L)
    return {"D": D, "dI_L": di, "I_pk": iout + di / 2, "I_L_rms": math.sqrt(iout ** 2 + di ** 2 / 12),
            "dV_out": di / (8 * f * C) + di * esr, "V_ds_off": 2 * vin, "ccm": iout > di / 2, "reset_ok": D <= 0.5}


def dab(v1, v2, f, L, phi_deg, n=1.0, **_):
    """Dual active bridge: P = V1 V2' phi (pi - phi) / (2 pi^2 f L), V2' = n V2 referred to the primary."""
    phi = math.radians(phi_deg)
    v2p = n * v2
    P = v1 * v2p * phi * (math.pi - phi) / (2 * math.pi ** 2 * f * L)
    return {"P": P, "phi_rad": phi, "I_L_pk_est": (v1 + v2p) * phi / (2 * math.pi * f * L) / 2 if L else None}


def inverter(vdc, m, f1, p, pf=1.0, **_):
    """H-bridge SPWM: fundamental peak M Vdc, rms V1/sqrt2, I1 peak from P and PF."""
    v1 = m * vdc
    vrms = v1 / math.sqrt(2)
    i1 = 2 * p / (v1 * pf) if v1 and pf else None
    return {"V_1": v1, "V_rms": vrms, "I_1": i1, "P": p, "DPF": pf}


def pfc(vac, pout, f1, C, vout, eff=0.97, **_):
    """Boost PFC: line current, 2f output ripple P / (w1 C Vout), inductor peak at the line peak."""
    iac = pout / (eff * vac)
    w1 = 2 * math.pi * f1
    return {"I_ac_rms": iac, "I_pk": iac * math.sqrt(2), "V_ripple_2f": pout / (w1 * C * vout), "V_out": vout,
            "V_pk_in": vac * math.sqrt(2), "D_min": 1 - vac * math.sqrt(2) / vout}


def threelevel(vdc, m, f1, p, pf=1.0, C_half=None, **_):
    """Three-level (NPC / T-type) leg, PD-PWM: fundamental like the inverter; NP ripple and DC-link cap rms are
    pwrlab's (the simulation) numbers; here only the first-harmonic quantities."""
    v1 = m * vdc / 2
    i1 = 2 * p / (v1 * pf) if v1 and pf else None
    return {"V_1": v1, "V_rms": v1 / math.sqrt(2), "I_1": i1, "P": p, "DPF": pf}


TOPO = {"buck": buck, "boost": boost, "buck-boost": buck_boost, "flyback": flyback, "forward": forward,
        "dab": dab, "inverter": inverter, "pfc": pfc, "threelevel": threelevel}
# which [specs] / params feed each topology's closed form (beyond vin, vout, iout, f, L, C)
TOPO_ARGS = {"dab": ("v1", "v2", "f", "L", "phi_deg", "n"), "inverter": ("vdc", "m", "f1", "p", "pf"),
             "pfc": ("vac", "pout", "f1", "C", "vout", "eff"), "threelevel": ("vdc", "m", "f1", "p", "pf")}

# which metric name means which closed-form key
METRIC = [(re.compile(r"inductor.*ripple|ripple.*inductor|Δ\s*I_?L|dI_?L|current ripple", re.I), "dI_L", "A"),
          (re.compile(r"peak.*(inductor|current)|inductor.*peak|I_?pk|I_?L,?\s*pk", re.I), "I_pk", "A"),
          (re.compile(r"output.*ripple|ripple.*output|Δ\s*V_?out|dV_?out|voltage ripple", re.I), "dV_out", "V"),
          (re.compile(r"\bduty\b", re.I), "D", ""),
          (re.compile(r"inductor.*rms|rms.*inductor|I_?L,?\s*rms", re.I), "I_L_rms", "A"),
          (re.compile(r"2\s*f.*ripple|ripple.*2\s*f|bus.*ripple|dc.?link.*ripple", re.I), "V_ripple_2f", "V"),
          (re.compile(r"line current|I_?ac|input current rms", re.I), "I_ac_rms", "A"),
          (re.compile(r"fundamental.*peak|V_?1\b", re.I), "V_1", "V"),
          (re.compile(r"output.*rms|V_?rms", re.I), "V_rms", "V"),
          (re.compile(r"\bpower\b|P_?in|P_?out", re.I), "P", "W")]
UNIT = {"mV": 1e-3, "V": 1.0, "mA": 1e-3, "A": 1.0, "%": 0.01, "": 1.0}


def unit_scale(unit, kind):
    u = str(unit or "").split()[0] if unit else ""
    if kind == "V":
        return UNIT.get(u, 1.0) if u in ("mV", "V") else 1.0
    if kind == "A":
        return UNIT.get(u, 1.0) if u in ("mA", "A") else 1.0
    return 0.01 if u == "%" else 1.0


# ------------------------------------------------------------------ the design's own parameters
def infer_topology(text, specs):
    """The topology from the project's words and specs; None when it cannot tell (the owner sets [specs] topology)."""
    s = text.lower()
    for key in ("threelevel", "three-level", "t-type", "npc", "dab", "flyback", "forward", "buck-boost", "llc", "pfc", "totem", "inverter", "boost", "buck"):
        if key in s:
            return {"three-level": "threelevel", "t-type": "threelevel", "npc": "threelevel", "totem": "pfc", "llc": "llc"}.get(key, key)
    if specs.get("vac_V") and specs.get("vout_V"):
        return "pfc"
    if specs.get("vdc_link_V") or (specs.get("vin_V") and specs.get("vac_out_V")):
        return "inverter"
    vin, vo = specs.get("vin_V"), specs.get("vout_V")
    if vin and vo:
        vmin = min(vin) if isinstance(vin, list) else vin
        vmax = max(vin) if isinstance(vin, list) else vin
        vo = max(vo) if isinstance(vo, list) else vo
        if specs.get("isolation", "").startswith("iso"):
            return "flyback" if (specs.get("power_W") or 0) < 150 else "forward"
        if vo < vmin:
            return "buck"
        if vo > vmax:
            return "boost"
        return "buck-boost"
    return None


def design(prj):
    """{topology, vin: [lo, hi], vout, iout, f_sw: [lo, hi] Hz, L_H, C_out_F, sources: {...}}, from [specs],
    [corners] and the schematic (the inductor on the output rail, the capacitors on it)."""
    specs, corners = prj.cfg.get("specs", {}), prj.cfg.get("corners", {})
    typ = (prj.cfg.get("project", {}).get("type") or "").lower()
    topo = specs.get("topology") or infer_topology(typ + " " + prj.name.lower() + " " + str(specs.get("summary", "")), specs)
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
    if argv[:1] and argv[0] in TOPO_ARGS:                       # the non-DC-DC closed forms: named arguments
        fn, keys = TOPO[argv[0]], TOPO_ARGS[argv[0]]
        kw = {k: si(_arg(argv, "--" + k)) for k in keys if _arg(argv, "--" + k) is not None}
        if "phi_deg" in keys and _arg(argv, "--phi") is not None:
            kw["phi_deg"] = float(_arg(argv, "--phi"))
        missing = [k for k in keys if k not in kw and k not in ("n", "pf", "eff")]
        if missing:
            raise SystemExit(f"forge calc {argv[0]} " + " ".join(f"--{k} <v>" for k in keys))
        r = fn(**kw)
        print(json.dumps(r, indent=1) if "--json" in argv else "\n".join(f"  {k}: {v:.4g}" if isinstance(v, float) else f"  {k}: {v}" for k, v in r.items()))
        return 0
    if argv[:1] and argv[0] in TOPO:
        fn = TOPO[argv[0]]
        try:
            vins = _vals(_arg(argv, "--vin"))
            vo, io, f = si(_arg(argv, "--vout")), si(_arg(argv, "--iout")), si(_arg(argv, "--f"))
            L, C, esr = si(_arg(argv, "--L")), si(_arg(argv, "--C")), si(_arg(argv, "--esr", "0"))
            assert None not in (vo, io, f, L, C) and all(v is not None for v in vins)
        except (TypeError, AssertionError):
            raise SystemExit(f"forge calc {argv[0]} --vin 36,63 --vout 12 --iout 1 --f 400k --L 33u --C 44u [--esr 5m] [--n 4 for flyback / forward]")
        extra = {"n": float(_arg(argv, "--n", 1.0))} if argv[0] in ("flyback", "forward") else {}
        rows = {f"{v:g} V": fn(v, vo, io, f, L, C, esr, **extra) for v in vins}
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
