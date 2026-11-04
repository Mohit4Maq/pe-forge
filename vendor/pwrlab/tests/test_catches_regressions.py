#!/usr/bin/env python3
"""Fault injection: re-introduce each historical bug, require the checks to catch it.

Each of these produced a confident, clean-exiting, plausible-looking WRONG answer
during development. If the checks layer cannot catch them, it is decoration.

    .venv/bin/python tests/test_catches_regressions.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pwrlab import Study
from pwrlab.topologies.dab import DAB

WORK = Path(__file__).resolve().parent.parent / "work" / "regr"


class BugUic(DAB):
    """`uic` on .tran makes LTspice IGNORE .ic and zero every inductor current.
    Historically: -41.9 % power and a -6986 A DC offset."""
    def netlist(self):
        return "\n".join((l + " uic") if l.startswith(".tran ") else l
                         for l in super().netlist().splitlines()) + "\n"


class BugNoWindingIC(DAB):
    """No .ic on the transformer windings: the DC operating point shorts them
    across a live bridge and seeds I(Ls) with ~2e5 A. Historically +152 %."""
    def netlist(self):
        return "\n".join(l for l in super().netlist().splitlines()
                         if not l.startswith((".ic I(Lp)", ".ic I(Ls)"))) + "\n"


class BugSlowGates(DAB):
    """tr=100p. Each leg is both-off for tr/2; the bridge node floats and the
    inductor current is forced through Roff, injecting DC. Historically +19.5 A
    offset on a +/-25 A ripple."""
    def __init__(self, **kw):
        super().__init__(**kw)
        self.tr = 100e-12


class Vt0(DAB):
    """NOT a bug -- kept as a regression guard on a CORRECTION.

    REPORT.md originally claimed Vt=0 causes shoot-through and is 'far worse
    (-43 %)'. That was measured while the startup state was still broken and is
    not true on its own: with correct .ic, Vt=0 matches Vt=0.5 to within 0.003 %
    and is INSENSITIVE to tr (Vt=0.5 drifts to +19.5 A offset at tr=100p).
    This case asserts it stays clean, so the claim cannot silently creep back."""
    def netlist(self):
        return super().netlist().replace("Vt=0.5", "Vt=0")


def run_case(cls, label, expect_catch, **kw):
    r = Study(cls(phi_deg=45, **kw), workroot=WORK / cls.__name__).run(
        tag=cls.__name__, converge=False)
    fails = r.checks.failures
    caught = len(fails) > 0
    ok = caught == expect_catch
    p, dc = r.extracted.get("P_in"), r.extracted.get("I_L_dc")
    print(f"\n--- {label}")
    print(f"    P_in = {p if p is None else f'{p:.2f}'} W "
          f"(theory {r.analytical.get('P_in', float('nan')):.2f})   "
          f"I_L_dc = {dc if dc is None else f'{dc:+.3f}'} A")
    for c in fails[:4]:
        print(f"    {c}")
    if len(fails) > 4:
        print(f"    ... and {len(fails)-4} more")
    verdict = ("CAUGHT" if caught else "MISSED") if expect_catch else \
              ("clean" if not caught else "UNEXPECTED FAILURE")
    print(f"    => {verdict}  {'OK' if ok else '*** TEST FAILED ***'}")
    return ok


if __name__ == "__main__":
    cases = [
        (DAB,             "baseline: correct netlist must pass cleanly",   False),
        (BugUic,          "bug 1: uic silently voids .ic",                 True),
        (BugNoWindingIC,  "bug 2: no .ic on transformer windings",         True),
        (BugSlowGates,    "bug 3: tr=100p both-off window injects DC",     True),
        (Vt0,             "correction: Vt=0 is fine, must stay clean",     False),
    ]
    results = [run_case(c, lbl, exp) for c, lbl, exp in cases]
    print("\n" + "=" * 64)
    print("ALL REGRESSION CASES BEHAVED AS EXPECTED" if all(results)
          else "*** SOME CASES DID NOT BEHAVE AS EXPECTED ***")
    sys.exit(0 if all(results) else 1)
