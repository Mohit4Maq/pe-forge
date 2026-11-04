#!/usr/bin/env python3
"""Three-level T-type / NPC against two independent hardware references.

Sources (public PDFs: ti.com/lit/pdf/tidue53, st.com UM3564):
  TI TIDA-01606 (TIDUE53J)  11 kW, 800 V, 400 V L-L, SiC, 50-90 kHz,
                            efficiency 98.6 %, THD < 3 % at 11 kW
  ST UM3564                 50 kW T-type, 650-850 V, peak efficiency 98.89 %
                            at 800 V and 99.10 % at 650 V, THD < 3 %, PF 0.99

What can honestly be asked of an ideal-switch model here is narrower than it
looks, and the criteria say so:

  * Both references quote efficiency as a PEAK or single figure, with the
    per-load curves living in figures that text extraction cannot recover. So
    there is no measured curve to match point by point.
  * pwrlab's device library is representative, not these designs' actual parts.
  * The loss model covers device conduction and switching only -- no magnetics,
    gate drive, control power, EMI filter or busbar. The model MUST therefore be
    optimistic on efficiency, and a model that were not would be wrong.

So efficiency is tested as BOUNDED_OPTIMISM on the LOSS FRACTION -- which
discriminates, where efficiency itself does not: 98.9 % against 98.6 % is a
ratio of 1.003 and tells you nothing, while 1.06 % loss against 1.40 % is a
ratio of 1.33 and tells you the loss model is the right size.

Run:  .venv/bin/python tests/test_benchmark_threelevel.py
      .venv/bin/python tests/test_benchmark_threelevel.py --quick
"""
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pwrlab import Study, benchmarks as B
from pwrlab.sim import Runner
from pwrlab.losses import analyse_losses, LIBRARY
from pwrlab.topologies.threelevel import TType3Phase, NPC3Phase

QUICK = "--quick" in sys.argv
WORK = ROOT / "work" / "bench3l"
fail = 0


def chk(label, ok, detail=""):
    global fail
    fail |= (not ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:46s} {detail}")


def main():
    global fail
    bench = B.load("three-level-ttype")
    ti = bench["references"]["tida_01606"]

    Vdc = float(ti["dc_nominal_V"])
    Vll = float(ti["output_voltage_Vll_rms"])
    P = float(ti["output_power_W"])
    M = Vll * math.sqrt(2) / (math.sqrt(3) * Vdc / 2)
    V1 = M * Vdc / 2
    I1 = 2 * P / (3 * V1)
    Z = V1 / I1

    print(f"BENCHMARK {bench['id']}")
    print(f"  TI  {ti['document']}: {P/1000:g} kW, {Vll:g} V L-L, {Vdc:g} V DC, "
          f"{ti['switching_frequency_Hz'][0]/1000:g}-"
          f"{ti['switching_frequency_Hz'][1]/1000:g} kHz, "
          f"eff {ti['efficiency_pct']}%, THD < {ti['thd_pct_limit']}%")
    st = bench["references"]["st_um3564"]
    print(f"  ST  {st['document']}: {st['output_power_W']/1000:g} kW, peak eff "
          f"{st['efficiency_pct_at_800V']}% @800V / "
          f"{st['efficiency_pct_at_650V']}% @650V, THD < {st['thd_pct_limit']}%")
    print(f"  model point: M={M:.4f}, Z={Z:.3f} ohm, deadtime 0.3 us (SiC)")
    print()

    # ------------------------------------------------------------------ #
    # 1. THD against the published specification limit
    # ------------------------------------------------------------------ #
    thd_crit = B.Criterion(
        quantity="thd_i_pct", measured_key="thd_pct_limit", mode=B.SPEC_BOUND,
        bound=float(ti["thd_pct_limit"]), bound_dir="below",
        rationale=(
            "Both references specify THD as an upper bound at full load rather "
            "than publishing a measured value, so this is the strongest claim "
            "the sources support. Evaluated with a SiC-realistic 0.3 us dead "
            "time, since dead time is the dominant low-order distortion "
            "mechanism in a three-level leg and testing at deadtime=0 would "
            "flatter the model."))

    mfs = (200, 400) if QUICK else (200, 400, 800)
    print(f"  {'topo':>9} {'fsw kHz':>8} {'THD_i %':>9} {'usable':>7}")
    thd_vals, thd_topo = [], []
    for cls, nm in ((TType3Phase, "ttype3ph"), (NPC3Phase, "npc3ph")):
        t = cls(Vdc=Vdc, M=M, Rload=Z * 0.9995, Lload=1e-3, mf=400,
                deadtime=0.3e-6)
        r = Study(t, workroot=WORK / f"thd_{nm}").run(tag="thd", converge=False)
        v = r.extracted.get("thd_i_pct")
        print(f"  {nm:>9} {400*50/1000:8.0f} {v if v else 0:9.3f} {str(r.ok):>7}")
        if r.ok and v is not None:
            thd_vals.append(v)
            thd_topo.append(nm)
        else:
            chk(f"{nm} THD run is usable", False, r.checks.summary())
    print()

    res = B.evaluate(thd_crit, [float(ti["thd_pct_limit"])] * len(thd_vals),
                     thd_vals, bench["id"])
    print(res.line)
    if not res.passed:
        fail |= 1

    # T-type and NPC produce the SAME output waveform. They differ in device
    # stress and loss distribution, not in what reaches the load. This could not
    # be checked at all before the NPC's dead-time netlist collision was fixed.
    if len(thd_vals) == 2:
        d = abs(thd_vals[0] - thd_vals[1]) / max(thd_vals) * 100
        chk("T-type and NPC agree on output THD", d < 1.0,
            f"{thd_vals[0]:.4f}% vs {thd_vals[1]:.4f}%  ({d:.3f}% apart)")

    # ------------------------------------------------------------------ #
    # 2. Loss fraction against the published efficiency
    # ------------------------------------------------------------------ #
    print()
    print(f"  {'fsw kHz':>8} {'Pout W':>8} {'Ploss W':>8} {'loss %':>8} "
          f"{'eff %':>8} {'Tj C':>7}")
    loss_pct, effs = [], []
    for mf in mfs:
        t = TType3Phase(Vdc=Vdc, M=M, Rload=Z * 0.9995, Lload=1e-3, mf=mf,
                        deadtime=0.3e-6)
        dev = t.recommend_devices(LIBRARY, margin=1.35)
        lr, sr = analyse_losses(t, dev, Runner(workroot=WORK / f"loss{mf}"),
                                Ta=40.0, Rth_hs=0.3, tag=f"L{mf}")
        pout = lr.P_out or 0.0
        lp = lr.P_total / (pout + lr.P_total) * 100.0 if pout else float("nan")
        eff = 100.0 - lp
        loss_pct.append(lp)
        effs.append(eff)
        print(f"  {mf*50/1000:8.0f} {pout:8.0f} {lr.P_total:8.1f} {lp:8.3f} "
              f"{eff:8.3f} {lr.Tj_max:7.1f}")
    print()

    ref_loss = 100.0 - float(ti["efficiency_pct"])
    loss_crit = B.Criterion(
        quantity="loss_pct", measured_key="loss_pct", mode=B.BOUNDED_OPTIMISM,
        max_optimism=3.0, better_is="lower",
        rationale=(
            "Compared as LOSS FRACTION, not efficiency: 98.9 % against 98.6 % "
            "is a ratio of 1.003 and discriminates nothing, while 1.06 % loss "
            "against 1.40 % is 1.33 and says the loss model is the right size. "
            "The model must be optimistic -- it has no magnetics, gate-drive, "
            "control, filter or busbar loss, and a representative device "
            "library rather than these designs' actual parts -- and must not be "
            "optimistic by more than 3x, which would mean device loss had "
            "stopped dominating the model's budget. It is also evaluated below "
            "the reference's 50-90 kHz because a three-phase leg at 50 kHz "
            "costs mf=1000; that handicaps the MODEL, since loss rises with "
            "frequency, so the test is conservative in the safe direction."))
    res2 = B.evaluate(loss_crit, [ref_loss] * len(loss_pct), loss_pct,
                      bench["id"])
    print(res2.line)
    if not res2.passed:
        fail |= 1

    # The trend is the part that is genuinely predictive: switching loss must
    # rise with frequency, or the loss model is not doing its job.
    mono = all(b > a for a, b in zip(loss_pct, loss_pct[1:]))
    chk("loss rises monotonically with switching frequency", mono,
        " -> ".join(f"{v:.3f}%" for v in loss_pct))

    print()
    print("  Modes: SPEC_BOUND is weaker than MATCH -- it shows the model is not")
    print("  contradicted by the published limit, not that it matches hardware.")
    return fail


if __name__ == "__main__":
    rc = main()
    print()
    print("THREE-LEVEL BENCHMARK OK" if not rc
          else "*** THREE-LEVEL BENCHMARK FAILED ***")
    sys.exit(1 if rc else 0)
