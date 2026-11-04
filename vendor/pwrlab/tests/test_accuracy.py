#!/usr/bin/env python3
"""Assert the closed-form agreement actually achieved, so it cannot regress."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pwrlab import Study
from pwrlab.topologies.dab import DAB
from pwrlab.topologies.buck import Buck

WORK = Path(__file__).resolve().parent.parent / "work" / "acc"
LIMITS = {"dab_P": 0.2, "dab_Irms": 1.0, "buck_V": 0.05,
          "buck_dI": 0.1, "buck_Irms": 0.1, "buck_dV": 1.0}
fail = 0


def chk(label, got, limit):
    global fail
    ok = abs(got) <= limit
    fail |= (not ok)
    print(f"  {'PASS' if ok else 'FAIL'}  {label:34s} {got:+9.4f} %  (limit {limit} %)")


print("DAB, phi = 10..90 deg")
wP = wI = 0.0
for phi in (10, 20, 30, 45, 60, 90):
    r = Study(DAB(phi_deg=phi), workroot=WORK / f"dab{phi}").run(tag="d", converge=False)
    e = lambda k: (r.extracted[k] - r.analytical[k]) / r.analytical[k] * 100
    wP, wI = max(wP, abs(e("P_in"))), max(wI, abs(e("I_L_rms")))
    if not r.ok:
        print(f"  FAIL  phi={phi} not usable: {r.checks.summary()}"); fail = 1
chk("worst power error", wP, LIMITS["dab_P"])
chk("worst inductor RMS error", wI, LIMITS["dab_Irms"])

print("\nBuck, D = 0.25..0.75, Rload = 0.5..100")
w = {"V_out": 0.0, "I_L_ripple": 0.0, "I_L_rms": 0.0, "V_ripple": 0.0}
for R, D in [(0.5, .5), (2., .25), (2., .5), (2., .75), (10., .5), (100., .5)]:
    r = Study(Buck(Rload=R, D=D), workroot=WORK / f"bk{R:g}_{D}").run(tag="b", converge=False)
    for k in w:
        w[k] = max(w[k], abs((r.extracted[k] - r.analytical[k]) / r.analytical[k] * 100))
    if not r.ok:
        print(f"  FAIL  Rload={R} D={D} not usable: {r.checks.summary()}"); fail = 1
chk("worst Vout error", w["V_out"], LIMITS["buck_V"])
chk("worst inductor ripple error", w["I_L_ripple"], LIMITS["buck_dI"])
chk("worst inductor RMS error", w["I_L_rms"], LIMITS["buck_Irms"])
chk("worst output ripple error", w["V_ripple"], LIMITS["buck_dV"])

print("\n" + ("ACCURACY TARGETS MET" if not fail else "*** ACCURACY REGRESSION ***"))
sys.exit(1 if fail else 0)
