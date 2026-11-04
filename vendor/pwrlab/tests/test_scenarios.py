#!/usr/bin/env python3
"""Item 5: tolerance analysis, electrothermal losses, fault transients.

Each assertion is tied to a physical expectation, not to a previously observed
number, so a genuine regression fails rather than a re-baselined constant.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pwrlab.sim import Runner
from pwrlab.montecarlo import ToleranceStudy, Tol
from pwrlab.losses import LIBRARY, analyse_losses, LossAnalysis
from pwrlab.transients import TransientStudy, StartUp, LoadStep, ShortCircuit
from pwrlab.topologies.buck import Buck
from pwrlab.topologies.dab import DAB

W = Path(__file__).resolve().parent.parent / "work" / "scen"
fail = 0


def chk(label, cond, detail=""):
    global fail
    fail |= (not cond)
    print(f"  {'PASS' if cond else 'FAIL'}  {label:52s} {detail}")


print("Sensitivity coefficients must match closed-form exponents")
ts = ToleranceStudy(Buck(), {"L": Tol(.2), "Cout": Tol(.2), "Rload": Tol(.05)},
                   workroot=W / "sens")
s = ts.sensitivity(["V_out", "I_L_ripple", "I_L_rms"], progress=False)
g = lambda p, o: float(s[(s["param"] == p) & (s["output"] == o)]["S"].iloc[0])
chk("dI_L ~ 1/L      -> S = -1", abs(g("L", "I_L_ripple") + 1) < 0.05,
    f"S = {g('L','I_L_ripple'):+.4f}")
chk("I_rms ~ 1/Rload -> S = -1", abs(g("Rload", "I_L_rms") + 1) < 0.05,
    f"S = {g('Rload','I_L_rms'):+.4f}")
chk("V_out independent of L and Cout", abs(g("L", "V_out")) < 0.02
    and abs(g("Cout", "V_out")) < 0.02,
    f"S_L = {g('L','V_out'):+.5f}, S_C = {g('Cout','V_out'):+.5f}")

print("\nMonte Carlo")
mc = ts.monte_carlo(["V_out", "I_L_ripple"], n=24, seed=3, progress=False)
chk("all samples usable", mc.n_valid == mc.n_requested,
    f"{mc.n_valid}/{mc.n_requested}")
st = mc.stats().set_index("output")
chk("V_out spread < 0.1 % (insensitive, per sensitivity)",
    st.loc["V_out", "spread_pct"] < 0.1,
    f"{st.loc['V_out','spread_pct']:.4f} %")
chk("dI_L spread tracks the +/-20 % on L",
    20 < st.loc["I_L_ripple", "spread_pct"] < 90,
    f"{st.loc['I_L_ripple','spread_pct']:.1f} %")

print("\nElectrothermal losses")
d = DAB(phi_deg=45)
dev = LIBRARY["SiC_1200V_40m"]
devs = {n: dev for n in d.switch_blocking_voltages()}
lr, sr = analyse_losses(d, devs, Runner(workroot=W / "loss"), Ta=40, Rth_hs=0.2)
chk("electrothermal fixed point converged", lr.converged, f"{lr.iterations} iter")
rows = {r["switch"]: r for r in lr.rows}
one = rows["S_AH"]
chk("conduction loss = Rds(Tj) * Irms^2 (self-consistent)",
    abs(one["P_cond"] - one["Rds_hot"] * one["I_rms"] ** 2) < 1e-6,
    f"{one['P_cond']:.3f} W")
chk("Rds rises with Tj (coupling is active)", one["Rds_hot"] > dev.Rds_25 * 1.3,
    f"{one['Rds_hot']*1e3:.1f} mohm vs {dev.Rds_25*1e3:.0f} at 25 C")
zvs = [r for r in lr.rows if r["ZVS"]]
hard = [r for r in lr.rows if not r["ZVS"]]
chk("ZVS switches show lower switching loss than hard-switched",
    zvs and hard and (sum(r["P_sw"] for r in zvs) / len(zvs)
                      < 0.6 * sum(r["P_sw"] for r in hard) / len(hard)),
    f"{sum(r['P_sw'] for r in zvs)/len(zvs):.1f} W vs "
    f"{sum(r['P_sw'] for r in hard)/len(hard):.1f} W")
chk("one turn-on and one turn-off per switching period",
    all(r["n_on"] == d.Nmeas and r["n_off"] == d.Nmeas for r in lr.rows),
    f"n_on = {one['n_on']} over {d.Nmeas} periods")
chk("device loss exceeds the ideal-switch simulation loss",
    lr.P_total > (sr.extracted["P_in"] - sr.extracted["P_out"]),
    f"{lr.P_total:.1f} W vs {sr.extracted['P_in']-sr.extracted['P_out']:.2f} W ideal")

print("\nFault and dynamic transients")
b = Buck(Nsettle=200)
R = Runner(workroot=W / "tr")
tstudy = TransientStudy(b, R)
r1 = tstudy.run(StartUp(), {"I(L1)": {}}, tstop=3e-3, tag="su")
i_ss = b.V_out_ideal() / b.Rload
chk("cold-start inrush exceeds the steady-state current",
    r1.metrics["I(L1) peak"] > 1.5 * i_ss,
    f"{r1.metrics['I(L1) peak']:.1f} A peak vs {i_ss:.1f} A steady")
r2 = tstudy.run(LoadStep(t=1.5e-3, factor=0.5), {"V(out)": {"settle_band": 0.02}},
                tstop=4e-3, tag="ls")
chk("load step settles within the run", r2.metrics["V(out) settling s"] < 2e-3,
    f"{r2.metrics['V(out) settling s']*1e6:.0f} us")
chk("heavier load lowers the output (finite source impedance)",
    r2.metrics["V(out) final"] < r2.metrics["V(out) before event"],
    f"{r2.metrics['V(out) before event']:.3f} -> {r2.metrics['V(out) final']:.3f} V")
r3 = tstudy.run(ShortCircuit(t=1.5e-3), {"I(L1)": {"limit_hi": 100.0}},
                tstop=2.5e-3, tag="sc")
chk("short circuit drives current far above rating (and is caught)",
    r3.metrics["I(L1) peak"] > 5 * i_ss and not r3.ok,
    f"{r3.metrics['I(L1) peak']:.0f} A, checks flagged it")

print("\n" + ("SCENARIO ANALYSES OK" if not fail else "*** SCENARIO REGRESSION ***"))
sys.exit(1 if fail else 0)
