"""The rigor layer: decide whether a simulation result may be believed.

Motivation, from a real failure in this project's own history.  A DAB netlist
returned +154 % power error with NO simulator error, NO convergence warning, a
clean exit and a plausible-looking waveform.  Three separate bugs (an operating
point that shorted the windings, `uic` silently voiding `.ic`, and a both-off
window injecting DC) each produced confident, wrong numbers.

So: a number is not a result until it has survived these.  Every check returns a
Check with an explicit numeric margin, never a bare bool, so a report can show
HOW close to the limit a design sits.

Severity:
  ERROR -- do not use this number
  WARN  -- usable but something is off; say so in the report
  INFO  -- recorded as evidence
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

ERROR, WARN, INFO = "ERROR", "WARN", "INFO"


@dataclass
class Check:
    name: str
    passed: bool
    severity: str = ERROR
    detail: str = ""
    value: float | None = None
    limit: float | None = None

    @property
    def failed_hard(self) -> bool:
        return (not self.passed) and self.severity == ERROR

    def __str__(self):
        mark = "pass" if self.passed else self.severity
        v = "" if self.value is None else f"  ({self.value:.6g}"
        v += "" if (self.value is None or self.limit is None) else f" vs limit {self.limit:.6g}"
        v += ")" if self.value is not None else ""
        return f"[{mark:5s}] {self.name}{v}  {self.detail}"


@dataclass
class CheckSet:
    checks: list = field(default_factory=list)

    def add(self, c: Check):
        self.checks.append(c)
        return c

    def extend(self, cs):
        self.checks.extend(cs)

    @property
    def ok(self) -> bool:
        return not any(c.failed_hard for c in self.checks)

    @property
    def failures(self):
        return [c for c in self.checks if not c.passed]

    def summary(self) -> str:
        n_err = sum(1 for c in self.checks if c.failed_hard)
        n_warn = sum(1 for c in self.checks if not c.passed and c.severity == WARN)
        return (f"{len(self.checks)} checks, {n_err} error(s), {n_warn} warning(s)"
                f" -> {'USABLE' if self.ok else 'NOT USABLE'}")

    def __iter__(self):
        return iter(self.checks)


# --------------------------------------------------------------------------- #
# generic checks
# --------------------------------------------------------------------------- #
def sim_completed(res) -> Check:
    return Check("simulation produced artefacts", res.ok, ERROR,
                 res.why_not_ok or "raw + log written, no solver complaints")


def measurements_present(res, required) -> Check:
    missing = [m for m in required if res.meas.get(m) is None]
    return Check("all .meas values parsed", not missing, ERROR,
                 f"missing: {missing}" if missing else "all present")


def relative_error(name, sim, ref, tol_pct, severity=ERROR) -> Check:
    """Cross-check a simulated quantity against closed-form theory."""
    if sim is None or ref is None:
        return Check(f"{name} vs analytical", False, severity, "value unavailable")
    if abs(ref) < 1e-15:
        err = abs(sim - ref)
        return Check(f"{name} vs analytical (abs)", err <= tol_pct, severity,
                     f"sim {sim:.6g}, theory {ref:.6g}", err, tol_pct)
    err = (sim - ref) / ref * 100.0
    return Check(f"{name} vs analytical", abs(err) <= tol_pct, severity,
                 f"sim {sim:.6g}, theory {ref:.6g}, error {err:+.3f} %",
                 abs(err), tol_pct)


def steady_state(cycle_means, tol_frac=0.02, ref=None, severity=WARN) -> Check:
    """Is the waveform actually periodic yet?

    Compares the mean of the last cycle with the first in the window.  Expressed
    as a fraction of `ref` (typically the RMS) so it is meaningful for a quantity
    whose own mean is legitimately ~0 -- an inductor current, say.
    """
    if not cycle_means or len(cycle_means) < 2:
        return Check("steady state reached", False, severity, "no cycle data")
    drift = abs(cycle_means[-1] - cycle_means[0])
    scale = abs(ref) if ref else max(abs(v) for v in cycle_means) or 1.0
    frac = drift / scale
    return Check("steady state reached", frac <= tol_frac, severity,
                 f"drift over window {drift:.4g} = {frac*100:.2f} % of {scale:.4g}",
                 frac, tol_frac)


def dc_offset(offset, scale, tol_frac=0.05, severity=WARN) -> Check:
    """Residual DC in a quantity that should be AC-symmetric."""
    if offset is None or not scale:
        return Check("DC offset negligible", False, severity, "value unavailable")
    frac = abs(offset) / abs(scale)
    return Check("DC offset negligible", frac <= tol_frac, severity,
                 f"offset {offset:+.4g} = {frac*100:.2f} % of {scale:.4g}",
                 frac, tol_frac)


def power_balance(p_in, p_out, tol_pct=5.0, noise_pct=0.15, severity=WARN) -> Check:
    """Energy must go somewhere. Output above input means a sign or model error.

    `noise_pct` is the floor below which a negative loss is measurement noise
    rather than physics: Pin and Pout are separate integrals of switched
    waveforms and will not agree to the last digit.  Above the floor a negative
    loss is a real defect -- it is how under-settling shows up, since a
    still-discharging output capacitor genuinely delivers more than it takes.
    """
    if p_in is None or p_out is None:
        return Check("power balance", False, severity, "value unavailable")
    if abs(p_in) < 1e-12:
        return Check("power balance", True, INFO, "no power flow")
    loss = p_in - p_out
    frac = loss / abs(p_in) * 100.0
    if loss < 0:
        if abs(frac) <= noise_pct:
            return Check("power balance", True, INFO,
                         f"Pout exceeds Pin by {abs(frac):.4f} %, within the "
                         f"{noise_pct:g} % measurement-noise floor", frac, tol_pct)
        return Check("power balance", False, ERROR,
                     f"Pout {p_out:.4g} EXCEEDS Pin {p_in:.4g} by {abs(frac):.3f} % "
                     f"-- sign error, or the run has not settled",
                     frac, tol_pct)
    return Check("power balance", frac <= tol_pct, severity,
                 f"Pin {p_in:.6g} W, Pout {p_out:.6g} W, loss {loss:.4g} W "
                 f"({frac:.3f} %)", frac, tol_pct)


def convergence(values, tol_pct=1.0, label="timestep", severity=ERROR) -> Check:
    """Result must be insensitive to the numerical parameter being refined.

    `values` are the same physical quantity computed at successively finer
    settings.  A result that moves when you halve the timestep is a statement
    about the solver, not about the circuit.
    """
    vals = [v for v in values if v is not None]
    if len(vals) < 2:
        return Check(f"{label} convergence", False, severity, "insufficient runs")
    a, b = vals[-2], vals[-1]
    if abs(b) < 1e-15:
        d = abs(a - b)
        return Check(f"{label} convergence", d <= tol_pct, severity,
                     f"{a:.6g} -> {b:.6g}", d, tol_pct)
    d = abs((b - a) / b) * 100.0
    return Check(f"{label} convergence", d <= tol_pct, severity,
                 f"{a:.6g} -> {b:.6g} on refinement, change {d:.4f} %", d, tol_pct)


def in_range(name, value, lo=None, hi=None, severity=ERROR, unit="") -> Check:
    if value is None:
        return Check(name, False, severity, "value unavailable")
    ok = (lo is None or value >= lo) and (hi is None or value <= hi)
    bounds = f"[{'-inf' if lo is None else f'{lo:.6g}'}, " \
             f"{'inf' if hi is None else f'{hi:.6g}'}]"
    return Check(name, ok, severity, f"{value:.6g}{unit} in {bounds}", value,
                 hi if hi is not None else lo)


def finite(name, value, severity=ERROR) -> Check:
    ok = value is not None and math.isfinite(value)
    return Check(name, ok, severity,
                 "finite" if ok else f"non-finite value: {value}")
