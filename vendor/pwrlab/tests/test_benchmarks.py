#!/usr/bin/env python3
"""Does the model agree with measured hardware? Gate on the answer.

This suite is the one that is allowed to fail because the PHYSICS is missing
rather than because the code is broken. Its failures are a specification for
what to model next, not a bug report.

Each criterion below names the mode of agreement it demands and why. A model
that passes only on TREND has not earned the phrase "validated against
hardware" for that quantity, and the report says so.

Run:  .venv/bin/python tests/test_benchmarks.py
      .venv/bin/python tests/test_benchmarks.py --quick   (3 points, for the gate)
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pwrlab import Study, benchmarks as B
from pwrlab.topologies.pfc import TotemPolePFC

QUICK = "--quick" in sys.argv
WORK = ROOT / "work" / "bench"
fail = 0


# --------------------------------------------------------------------------- #
# TIDA-010203 -- 4 kW GaN totem-pole PFC, TI TIDUEZ3B Table 3-1
# --------------------------------------------------------------------------- #
CRITERIA = [
    B.Criterion(
        quantity="V_out", measured_key="Vout_V", mode=B.MATCH, tol_pct=1.0,
        rationale=(
            "Output voltage is set by the regulator's reference and by power "
            "balance, neither of which the idealisation touches. If this does "
            "not match, the model is not describing the same converter.")),

    B.Criterion(
        quantity="I_ac_rms", measured_key="I_ideal_for_Pout_A", mode=B.MATCH,
        tol_pct=2.0,
        rationale=(
            "Compared against Pout/Vin, NOT against the measured Iin. The raw "
            "Iin cannot be used: Pin/(Vin*Iin) disagrees with the table's own "
            "PF column by -0.072 at light load and 0.000 at full load, and the "
            "gap is fully explained by a constant 0.67-0.89 A quadrature "
            "current (~10 uF of EMI-filter X-capacitance) that carries no "
            "power. Holding a filterless model to a filtered measurement would "
            "be scoring it on physics it never claimed. Against the "
            "power-carrying component, 2 % is tight: this is pure power "
            "balance and nothing idealised affects it.")),

    B.Criterion(
        quantity="PF", measured_key="PF", mode=B.MATCH, tol_abs=0.01,
        rationale=(
            "Power factor is dominated by current-ripple-to-fundamental ratio, "
            "a mechanism the model DOES represent: a fixed hysteresis band "
            "behaves like the fixed CCM ripple of a fixed-frequency design, "
            "because both are roughly load-independent in amplitude. So a real "
            "match is demanded here, not merely a trend. 0.01 absolute is the "
            "resolution the source table is quoted to (3 decimal places).")),

    B.Criterion(
        quantity="thd_median", measured_key="iTHD_pct", mode=B.BOUNDED_OPTIMISM,
        max_optimism=30.0, better_is="lower",
        rationale=(
            "Evaluated on the MEDIAN of a 5-point ensemble, never on a single "
            "run. Single-point THD from this model is chaotic: a 0.015 % change "
            "in load resistance moved it from 0.957 % to 1.382 %, and both "
            "values were converged (identical at 16x FFT resolution), so it is "
            "the free-running hysteresis beating against the line, not a "
            "numerical artefact. A single sample measures one beat pattern, not "
            "the converter. "
            "The model must be optimistic -- it omits the zero-crossing dead "
            "zone, dead time, reverse recovery, current-sense offset and EMI "
            "filter -- and must not be unboundedly so. 30x sits above the "
            "observed 9x-19x with room for the ensemble spread, and far below "
            "the point where the mechanism would have stopped being represented "
            "at all. A PESSIMISTIC point fails outright: an idealised model "
            "producing MORE distortion than hardware would mean distortion is "
            "coming from somewhere unaccounted for.")),

    B.Criterion(
        quantity="thd_median", measured_key="iTHD_pct", mode=B.TREND,
        trend_tol_pct=40.0,
        rationale=(
            "Absolute THD is out of reach, but the MECHANISM is testable: as "
            "load falls, ripple stays roughly constant while the fundamental "
            "shrinks, so THD must rise. Measured, THD rises 6.5x from full to "
            "light load. Reproducing that ratio means the physics is right and "
            "only the magnitude is wrong, which is a model worth having. "
            "40 % on a ratio-of-ratios, evaluated on ensemble medians. This was "
            "25 % and passed at -24.0 %, i.e. it was passing by 1 point on a "
            "quantity later shown to be chaotic -- a limit that tight was "
            "measuring luck. Widened deliberately, with the ensemble median "
            "doing the work the tight limit was pretending to do.")),
]


def run_benchmark():
    global fail
    bench = B.load("tida-010203")
    pts = bench["measured"]
    if QUICK:
        pts = [pts[0], pts[len(pts) // 2], pts[-1]]

    print(f"BENCHMARK {bench['id']} -- {bench['title']}")
    print(f"  source   {bench['publisher']} {bench['document']}")
    print(f"  url      {bench['url']}")
    print(f"  table    {bench['table']}")
    print(f"  points   {len(pts)}" + ("  (quick subset)" if QUICK else ""))
    print()

    hw = bench["hardware"]
    modelled, usable_all = [], True
    n_ens = 3 if QUICK else 5
    print(f"  THD is evaluated as the median of a {n_ens}-point ensemble "
          f"(+/-0.05 % on Rload); see the criterion rationale for why.")
    print()
    print(f"  {'Pout kW':>8} {'meas PF':>8} {'mdl PF':>8} {'meas THD':>9} "
          f"{'mdl THD~':>9} {'spread':>8} {'usable':>7}")
    for pt in pts:
        P = pt["Pout_kW"] * 1000.0
        R0 = pt["Vout_V"] ** 2 / P
        base = {}

        def one(scale, _pt=pt, _P=P, _R0=R0, _base=base):
            t = TotemPolePFC(
                Vac_rms=_pt["Vin_Vac"], Pout=_P, Rload=_R0 * scale,
                L=hw["L_H"], Cout=hw["Cout_F"])
            r = Study(t, workroot=WORK / f"{_pt['Pout_kW']:.3f}_{scale:.6f}").run(
                tag="bench", converge=False)
            if scale == 1.0:
                _base["r"] = r
            return r.extracted.get("thd_i_pct") if r.ok else None

        ens = B.ensemble(one, None, n=n_ens, spread_pct=0.05)
        r = base.get("r")
        ok = bool(r and r.ok)
        usable_all &= ok
        e = r.extracted if r else {}
        rec = dict(e)
        rec["thd_median"] = ens["median"] if ens else None
        modelled.append(rec if ok else {})
        print(f"  {pt['Pout_kW']:8.3f} {pt['PF']:8.3f} {e.get('PF', 0):8.4f} "
              f"{pt['iTHD_pct']:9.2f} "
              f"{(ens['median'] if ens else 0):9.3f} "
              f"{(ens['spread_pct'] if ens else 0):7.1f}% {str(ok):>7}")
    print()

    if not usable_all:
        print("  NOTE: some points were not usable; they are excluded from the "
              "comparison below rather than counted as agreement.")
        print()

    results = []
    for crit in CRITERIA:
        meas = [pt.get(crit.measured_key) for pt in pts]
        mdl = [m.get(crit.quantity) for m in modelled]
        res = B.evaluate(crit, meas, mdl, bench["id"])
        results.append(res)
        print(res.line)
        if not res.passed:
            fail |= 1

    print()
    print("  Mode meanings:")
    print("    MATCH            model must land within tolerance of hardware")
    print("    BOUNDED_OPTIMISM model may be better, by no more than the bound;")
    print("                     being WORSE than hardware always fails")
    print("    TREND            model must reproduce how the quantity MOVES")
    return results


if __name__ == "__main__":
    res = run_benchmark()
    print()
    hard = [r for r in res if not r.passed]
    if hard:
        print(f"*** {len(hard)} BENCHMARK CRITERION(S) FAILED ***")
        for r in hard:
            print(f"      {r.criterion.quantity} [{r.criterion.mode}]: {r.detail}")
            print(f"        why this criterion exists: "
                  f"{r.criterion.rationale[:150]}...")
    else:
        print("AGREES WITH PUBLISHED HARDWARE within every declared mode")
    sys.exit(1 if fail else 0)
