#!/usr/bin/env python3
"""The verification agent's mechanical half: try to make pwrlab lie.

Every other test here asks "does it get the right answer?". This one asks the
adversarial question instead: CAN I GET A WRONG ANSWER PRESENTED AS A RIGHT ONE?
That is the only failure mode that actually matters for Fermi, because an agent
consuming these payloads cannot see the circuit. It sees JSON. If a payload ever
says usable=true over a number that is physically impossible, the agent will
report it as fact and the whole evidence chain is worthless.

So each case below is a deliberate attempt to slip something past the checker.
The test PASSES when the attempt is CAUGHT. A case that sails through is a hole.

Two classes of case:

  REFUSE   physically invalid or out-of-contract input; the run must come back
           usable=false, or raise, and the verdict must say why
  ADMIT    physically fine but easy to misread; the run may be usable, but the
           payload must carry the caveat that stops it being misquoted

Run:  .venv/bin/python tests/test_adversarial.py
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pwrlab import Study
from pwrlab.topologies.pfc import TotemPolePFC
from pwrlab.topologies.buck import Buck

WORK = ROOT / "work" / "adv"
fail = 0
holes = []


def chk(label, caught, detail=""):
    global fail
    fail |= (not caught)
    if not caught:
        holes.append(label)
    print(f"  {'CAUGHT' if caught else 'HOLE  '}  {label:54s} {detail}")


def run(topo, tag, converge=False, budget_s=90):
    """Run, but refuse to wait forever.

    A pathological input must be REJECTED CHEAPLY, not simulated slowly. This
    harness measures that: anything that takes longer than `budget_s` to say no
    is itself a finding, because a sweep over such points hangs an agent.
    """
    t0 = time.time()
    try:
        r = Study(topo, workroot=WORK / tag).run(tag=tag, converge=converge)
        return r, None, time.time() - t0
    except Exception as e:
        return None, e, time.time() - t0


# --------------------------------------------------------------------------- #
print("REFUSE: physically impossible operating points must not return usable")

# A boost converter cannot buck. Asking for a bus below the AC peak is not a
# hard operating point, it is an impossible one, and a model that reports a
# tidy number here would be lying with confidence.
t = TotemPolePFC(Vac_rms=230, Pout=1000, Rload=100)   # -> Vout = 316 V < 325 V peak
r, err, dt = run(t, "sub_peak")
if err is not None:
    chk("output below AC peak is refused", True,
        f"{type(err).__name__} in {dt*1000:.1f} ms")
    chk("  ...and refused WITHOUT simulating", dt < 1.0, f"{dt*1000:.1f} ms")
else:
    named = any("peak" in c.name.lower() for c in r.checks.failures)
    chk("output below AC peak is refused", (not r.ok) and named,
        f"usable={r.ok}, Vout={r.extracted.get('V_out', 0):.1f} V")

# Negative and zero component values: SPICE will often happily integrate these.
for label, kw in (("negative inductance", {"L": -1e-3}),
                  ("zero output capacitance", {"Cout": 0.0}),
                  ("negative hysteresis band", {"hyst": -0.5})):
    t = TotemPolePFC(**kw)
    r, err, dt = run(t, label.replace(" ", "_"))
    caught = (err is not None) or (r is not None and not r.ok)
    chk(f"{label} is refused", caught,
        f"{type(err).__name__} in {dt*1000:.1f} ms" if err else f"usable={r.ok}")
    # Measured before the fix: a negative hysteresis band ran >3 minutes
    # without converging. Rejecting it must cost microseconds, not minutes.
    chk(f"{label}: refused before the solver ran", dt < 1.0, f"{dt*1000:.1f} ms")

# A hysteresis band wider than the current it is shaping is not a design.
t = TotemPolePFC(hyst=50.0)
r, err, dt = run(t, "band_too_wide")
chk("band wider than peak current is refused",
    err is not None or (r is not None and not r.ok),
    f"{type(err).__name__} in {dt*1000:.1f} ms" if err else f"usable={r.ok}")

# --------------------------------------------------------------------------- #
print("\nREFUSE: a broken measurement window must not pass as steady state")

# NOTE on the premise: the first version of this case assumed a short run could
# not have settled. That was WRONG for this topology -- the netlist seeds
# `.ic V(vo)` at the analytical output, so the bus starts in steady state and
# drift is ~0.006 % even with n_settle=0. Measuring that is what corrected the
# assumption.
#
# The real hazard is narrower and worse: with n_meas=1 there is only ONE cycle,
# so drift CANNOT be computed, and the check used to vanish from the list
# entirely. A check that silently does not run reads exactly like a check that
# passed.
t = TotemPolePFC(n_settle=0, n_meas=1)
r, err, dt = run(t, "no_settle")
if err is not None:
    chk("single-cycle run is refused", True, f"raised {type(err).__name__}")
else:
    named = [c for c in r.checks if "steady" in c.name.lower()]
    chk("steady state is never silently skipped", bool(named),
        f"{len(named)} steady-state check(s) present")
    chk("an unassessable property is reported as unassessed",
        bool(named) and (not named[0].passed) and "NOT ASSESSED" in named[0].detail,
        named[0].detail[:46] if named else "absent")

# And with enough cycles it must actually be measured, not merely declared.
t = TotemPolePFC(n_settle=0, n_meas=2)
r, err, dt = run(t, "two_cycle")
if err is None:
    named = [c for c in r.checks if "steady" in c.name.lower()]
    chk("with 2 cycles, drift is genuinely measured",
        bool(named) and named[0].value is not None,
        named[0].detail[:46] if named else "absent")

# --------------------------------------------------------------------------- #
print("\nREFUSE: the checker must catch a solver that silently under-resolves")

# tmax_div sets the maximum timestep. Coarsening it hard should either change
# the answer (caught by the convergence check) or not (fine) -- what must NEVER
# happen is a changed answer reported as converged.
t = TotemPolePFC(tmax_div=8)
r, err, dt = run(t, "coarse", converge=True)
if err is not None:
    chk("coarse timestep handled", True, f"raised {type(err).__name__}")
else:
    conv = [c for c in r.checks if "convergence" in c.name.lower()]
    ran = bool(conv)
    honest = (not ran) or all(c.passed for c in conv) or (not r.ok)
    chk("coarse timestep: answer moved => not usable", honest,
        f"usable={r.ok}, convergence checks={len(conv)}")

# --------------------------------------------------------------------------- #
print("\nADMIT: valid but misreadable results must carry their caveat")

# The light-load PF collapse. This is real behaviour of a FIXED hysteresis band,
# and the danger is an agent reporting "this PFC has PF 0.83" as a property of
# the converter rather than of the band it was simulated with.
t = TotemPolePFC(Pout=100, Rload=400**2 / 100)
r, err, dt = run(t, "light_load")
if err is None:
    pf = r.extracted.get("PF", 1.0)
    flagged = (not r.ok) or any("power factor" in c.name.lower()
                                for c in r.checks.failures)
    chk("light load with a fixed band is flagged, not reported clean",
        flagged, f"PF={pf:.4f}, usable={r.ok}")

    from pwrlab.assumptions import describe
    d = describe("pfc")
    text = repr(d)
    chk("the assumption sheet explains the band/I_pk scaling",
        "band/I_pk" in text or "hyst/I_pk" in text, "ratio rule documented")
    chk("the assumption sheet names the absent voltage loop",
        "VOLTAGE LOOP" in text, "")

# Efficiency from ideal switches is ~100 %, which is not a physical claim.
t = TotemPolePFC()
r, err, dt = run(t, "eff")
if err is None:
    eff = r.extracted.get("efficiency_pct")
    from pwrlab.assumptions import describe
    warned = "Efficiency" in repr(describe("pfc")["not_trustworthy"])
    chk("~100 % ideal-switch efficiency is declared untrustworthy",
        warned, f"model reports {eff:.2f} %" if eff else "")

# --------------------------------------------------------------------------- #
print("\nREFUSE: cross-topology contamination")

# default_watch must come from the topology, never from a neighbour. This is the
# A1 defect generalised: any topology whose watch list names another topology's
# traces will produce an empty, unusable transient result.
from pwrlab.cli import REGISTRY, _load_registry
_load_registry()
for name, cls in sorted(REGISTRY.items()):
    t = cls()
    saved = set(t.saved_traces())
    bad = [w for w in t.default_watch() if w not in saved]
    chk(f"{name}: watch list contains no foreign traces", not bad, str(bad))

# --------------------------------------------------------------------------- #
print("\nREFUSE: provenance cannot be faked clean")

from pwrlab import provenance
st = provenance.stamp()
chk("a dirty or unknown tree is never evidence-eligible",
    (not st["dirty"]) or (st["evidence_eligible"] is False),
    f"dirty={st['dirty']}, eligible={st['evidence_eligible']}")
chk("an ineligible result explains itself",
    st["evidence_eligible"] or bool(st.get("why_not_evidence")), "")

ok, msg = provenance.check_lockfile()
chk("toolchain drift is detectable", isinstance(ok, bool), msg[:58])

# --------------------------------------------------------------------------- #
print()
if fail:
    print(f"*** {len(holes)} HOLE(S) FOUND ***")
    for h in holes:
        print(f"      - {h}")
else:
    print("NO HOLES: every attempt to smuggle a bad number through was caught")
sys.exit(1 if fail else 0)
