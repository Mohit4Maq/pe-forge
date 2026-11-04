#!/usr/bin/env python3
"""Accuracy + cross-topology agreement for the line-frequency converters."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pwrlab import Study
from pwrlab.topologies.inverter import HBridgeInverter
from pwrlab.topologies.threelevel import (TTypeInverter, NPCInverter,
                                          TType3Phase, NPC3Phase)
from pwrlab.topologies.pfc import TotemPolePFC

WORK = Path(__file__).resolve().parent.parent / "work" / "line"
fail = 0


def chk(label, got, limit, unit="%"):
    global fail
    ok = abs(got) <= limit
    fail |= (not ok)
    print(f"  {'PASS' if ok else 'FAIL'}  {label:42s} {got:+10.5f} {unit}  (limit {limit})")


def worst(topo, keys, tag, **kw):
    r = Study(topo, workroot=WORK / tag).run(tag=tag, converge=False, **kw)
    global fail
    if not r.ok:
        print(f"  FAIL  {tag} not usable: {r.checks.summary()}")
        for c in r.checks.failures:
            print(f"        {c}")
        fail = 1
        return r, {}
    return r, {k: (r.extracted[k] - r.analytical[k]) / r.analytical[k] * 100
               for k in keys if r.analytical.get(k)}


print("H-bridge SPWM inverter (V1=M*Vdc, Vrms exact, I1, P, DPF)")
for uni in (True, False):
    _, e = worst(HBridgeInverter(unipolar=uni),
                 ["V_1", "V_rms", "I_1", "P", "DPF"],
                 f"hb_{'uni' if uni else 'bi'}")
    for k, v in e.items():
        chk(f"{'unipolar' if uni else 'bipolar'} {k}", v, 1.0)

print("\nThree-level single leg: T-type and NPC")
single = {}
for cls in (TTypeInverter, NPCInverter):
    r, e = worst(cls(), ["V_1", "V_rms", "I_1", "P", "np_ripple_V"], cls.__name__)
    single[cls.__name__] = r.extracted
    for k, v in e.items():
        chk(f"{cls.__name__} {k}", v, 10.0 if k == "np_ripple_V" else 1.0)

print("\nThree-level three-phase: T-type and NPC")
tri = {}
for cls in (TType3Phase, NPC3Phase):
    r, e = worst(cls(), ["V_1", "V_ll", "I_1", "P", "np_ripple_V"], cls.__name__)
    tri[cls.__name__] = r.extracted
    for k, v in e.items():
        chk(f"{cls.__name__} {k}", v, 10.0 if k == "np_ripple_V" else 1.0)

print("\nT-type vs NPC must agree: same three-level leg, different switch arrangement")
for label, d in (("1-phase", single), ("3-phase", tri)):
    a, b = list(d.values())
    if a and b:
        for k in ("V_1", "I_1", "P"):
            chk(f"{label} T-type vs NPC {k}", (a[k] - b[k]) / b[k] * 100, 0.1)

print("\nTotem-pole PFC")
r, e = worst(TotemPolePFC(), ["V_out", "V_ripple_2f", "P_out", "I_ac_rms"], "pfc")
for k, v in e.items():
    chk(f"PFC {k}", v, 8.0 if k == "V_ripple_2f" else 3.0)
if r.extracted:
    chk("PFC power factor shortfall", (1 - r.extracted["PF"]) * 100, 2.0)
    chk("PFC line current THD", r.extracted["thd_i_pct"], 10.0)

print("\n" + ("LINE-FREQUENCY TOPOLOGIES OK" if not fail
             else "*** LINE-FREQUENCY REGRESSION ***"))
sys.exit(1 if fail else 0)
