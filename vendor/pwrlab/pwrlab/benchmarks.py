"""Validation against PUBLISHED HARDWARE, which is a different claim entirely.

Everything in `checks.py` answers one question: does the solver reproduce MY
MODEL of an idealised circuit? That question is worth answering and it catches
real bugs, but it is circular with respect to reality. A model can agree with
its own closed form to 0.01 % and describe a converter nobody can build.

This module answers the other question: **does the model agree with measured
hardware someone published?** It is the only check here that can fail for the
right reason -- because the physics is missing, not because the arithmetic is.

    The gap is not hypothetical. Measured 2026-09-13 against TI TIDUEZ3B
    (4 kW GaN totem-pole PFC): the model's power factor matches the hardware
    to within 0.25 % at all ten load points, and its current THD is between
    9x and 19x OPTIMISTIC, because the model has ideal switches, no dead time,
    no zero-crossing distortion and no EMI filter.

Which is why a benchmark cannot be a single number with a tolerance. An
idealised model SHOULD be optimistic about loss and distortion; a model that
were pessimistic about them would be wrong in a way no one had noticed. So each
criterion declares what KIND of agreement is being demanded:

    MATCH             the model must land within a tolerance of the measurement.
                      Use only for quantities set by topology and power balance
                      -- output voltage, RMS current, power factor -- where the
                      idealisation genuinely does not bite.

    BOUNDED_OPTIMISM  the model may be better than hardware, in a stated
                      direction, by no more than a stated factor. Being better
                      than the bound is a FAILURE, not a triumph: it means the
                      model omits more than it was declared to omit. Being WORSE
                      than hardware is always a failure.

    TREND             the model must reproduce how a quantity MOVES across the
                      operating range, even when its absolute value is off.
                      A model whose THD rises 6.1x from full to light load,
                      against 6.5x measured, has the mechanism right and the
                      magnitude wrong -- which is a useful model, honestly
                      described.

    SPEC_BOUND        the reference states a LIMIT rather than a measurement
                      ("THD < 3 % at full load"). The model must satisfy it in
                      the stated direction. This is a WEAKER claim than matching
                      a measured value and the report says so: satisfying a
                      bound shows only that the model is not contradicted by the
                      specification, not that it agrees with hardware.

    BRACKET           sweep a parameter representing physics the model usually
                      omits (dead time, an equivalent ripple band) and require
                      the measured value to fall INSIDE the swept range. This
                      proves the discrepancy is explained by the known-missing
                      term rather than by something unaccounted for. This is the
                      method already used for the three-level topologies.

A benchmark that passes on MATCH alone is a stronger claim than one that passes
on TREND, and the report says which, every time. Nothing here is allowed to
report "validated against hardware" without naming the mode it earned.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

from . import checks as C

BENCH_DIR = Path(__file__).resolve().parent.parent / "benchmarks"

MATCH = "MATCH"
BOUNDED_OPTIMISM = "BOUNDED_OPTIMISM"
TREND = "TREND"
BRACKET = "BRACKET"
SPEC_BOUND = "SPEC_BOUND"


@dataclass
class Criterion:
    """One demand made of the model, and the reason the demand is shaped that way."""
    quantity: str              # key in the model's extracted dict
    measured_key: str          # key in the benchmark's measured points
    mode: str
    rationale: str             # WHY this mode and this limit. Never optional.
    tol_pct: float | None = None        # MATCH
    tol_abs: float | None = None        # MATCH, absolute alternative
    max_optimism: float | None = None   # BOUNDED_OPTIMISM: model may be up to Nx better
    better_is: str = "lower"            # which direction counts as optimistic
    trend_tol_pct: float | None = None  # TREND
    bound: float | None = None          # SPEC_BOUND: the published limit
    bound_dir: str = "below"            # model must sit below/above the bound
    severity: str = C.ERROR

    def __post_init__(self):
        if not self.rationale:
            raise ValueError(
                f"criterion for {self.quantity} has no rationale. A tolerance "
                f"without a stated physical reason is a fudge factor.")


@dataclass
class BenchResult:
    benchmark_id: str
    criterion: Criterion
    passed: bool
    detail: str
    per_point: list = field(default_factory=list)

    @property
    def line(self) -> str:
        mark = "PASS" if self.passed else "FAIL"
        return (f"  [{mark}] {self.criterion.quantity:12s} "
                f"{self.criterion.mode:17s} {self.detail}")


def load(bench_id: str) -> dict:
    p = BENCH_DIR / f"{bench_id.replace('-', '_')}.json"
    if not p.exists():
        raise FileNotFoundError(f"no benchmark data at {p}")
    return json.loads(p.read_text())


def _rel(a, b):
    return (a - b) / b * 100.0 if b else float("nan")


def evaluate(crit: Criterion, measured: list, modelled: list,
             bench_id: str) -> BenchResult:
    """Compare one quantity across every operating point of a benchmark."""
    pairs = [(m, s) for m, s in zip(measured, modelled)
             if m is not None and s is not None and math.isfinite(s)]
    if not pairs:
        return BenchResult(bench_id, crit, False,
                           "no comparable points (model produced nothing usable)")

    if crit.mode == MATCH:
        errs = [(_rel(s, m), m, s) for m, s in pairs]
        worst = max(errs, key=lambda e: abs(e[0]))
        if crit.tol_abs is not None:
            bad = [e for e in errs if abs(e[2] - e[1]) > crit.tol_abs]
            ok = not bad
            d = (f"worst |model-measured| = {abs(worst[2]-worst[1]):.4g} "
                 f"(limit {crit.tol_abs:g}); {len(pairs)} points")
        else:
            ok = abs(worst[0]) <= crit.tol_pct
            d = (f"worst error {worst[0]:+.2f} % vs measured "
                 f"(limit {crit.tol_pct:g} %); {len(pairs)} points")
        return BenchResult(bench_id, crit, ok, d, errs)

    if crit.mode == BOUNDED_OPTIMISM:
        ratios = []
        for m, s in pairs:
            # ratio > 1 means the model is optimistic by that factor
            r = (m / s) if crit.better_is == "lower" else (s / m)
            ratios.append((r, m, s))
        worst_opt = max(ratios, key=lambda x: x[0])
        pessimistic = [x for x in ratios if x[0] < 1.0]
        over = [x for x in ratios if x[0] > crit.max_optimism]
        ok = (not pessimistic) and (not over)
        parts = [f"optimism {min(r[0] for r in ratios):.1f}x-"
                 f"{worst_opt[0]:.1f}x (limit {crit.max_optimism:g}x)"]
        if pessimistic:
            parts.append(f"{len(pessimistic)} point(s) PESSIMISTIC -- the model "
                         f"is worse than hardware, which is never expected")
        if over:
            parts.append(f"{len(over)} point(s) beyond the declared bound")
        return BenchResult(bench_id, crit, ok, "; ".join(parts), ratios)

    if crit.mode == TREND:
        # how much does the quantity move from the first point to the last?
        m_span = pairs[0][0] / pairs[-1][0] if pairs[-1][0] else float("nan")
        s_span = pairs[0][1] / pairs[-1][1] if pairs[-1][1] else float("nan")
        err = _rel(s_span, m_span)
        ok = abs(err) <= crit.trend_tol_pct
        d = (f"measured moves {m_span:.2f}x across the range, model {s_span:.2f}x "
             f"-> {err:+.1f} % (limit {crit.trend_tol_pct:g} %)")
        return BenchResult(bench_id, crit, ok, d, [(m_span, s_span)])

    if crit.mode == SPEC_BOUND:
        vals = [s for _, s in pairs]
        if crit.bound_dir == "below":
            bad = [v for v in vals if v > crit.bound]
            worst = max(vals)
        else:
            bad = [v for v in vals if v < crit.bound]
            worst = min(vals)
        ok = not bad
        d = (f"worst {worst:.4g} vs published limit {crit.bound:g} "
             f"({crit.bound_dir}); {len(vals)} point(s). "
             f"NOTE: a bound is not a measurement -- satisfying it shows the "
             f"model is not contradicted by the spec, not that it agrees "
             f"with hardware")
        return BenchResult(bench_id, crit, ok, d, [(crit.bound, v) for v in vals])

    raise ValueError(f"unknown criterion mode {crit.mode!r}")


def to_check(r: BenchResult) -> C.Check:
    """Express a benchmark result in the same currency as every other check."""
    return C.Check(f"benchmark {r.benchmark_id}: {r.criterion.quantity} "
                   f"[{r.criterion.mode}]",
                   r.passed, r.criterion.severity, r.detail)


def report(results: list, bench: dict) -> str:
    L = [f"BENCHMARK {bench['id']} -- {bench['title']}",
         f"  source   {bench['publisher']} {bench['document']}  {bench['url']}",
         f"  table    {bench['table']}",
         f"  points   {len(bench['measured'])}", ""]
    L += [r.line for r in results]
    L.append("")
    hard = [r for r in results if not r.passed and r.criterion.severity == C.ERROR]
    L.append("VERDICT: " + ("AGREES WITH PUBLISHED HARDWARE within the declared "
                            "modes" if not hard else
                            f"DOES NOT AGREE -- {len(hard)} criterion(s) failed"))
    return "\n".join(L)


# --------------------------------------------------------------------------- #
# Ensemble metrics, for quantities that are chaotic point-by-point
# --------------------------------------------------------------------------- #
def ensemble(run_one, base_value, n=5, spread_pct=0.05):
    """Evaluate a quantity over n neighbouring operating points.

    Some metrics are chaotic with respect to infinitesimal parameter changes
    even when the simulation itself is bit-reproducible. Measured 2026-09-13 on
    the hysteresis PFC: changing the load resistance by 0.015 % -- far below any
    physical tolerance -- moved reported current THD from 0.957 % to 1.382 %,
    a 44 % swing. The result was CONVERGED in both cases (identical to four
    decimals at 16x the FFT resolution), so this is not a post-processing
    artefact. It is the free-running hysteresis switching beating against the
    line frequency and depositing energy at low harmonic orders whose position
    depends on the exact trajectory.

    A single sample of such a quantity is not a measurement of the converter,
    it is a measurement of one beat pattern. So take several and report the
    MEDIAN and the SPREAD. A criterion may then be honestly stated about the
    central value, while the spread travels alongside it so nobody quotes the
    median as if it were repeatable.

    `run_one(scale)` must run the point with its swept parameter multiplied by
    `scale` and return the quantity, or None.
    """
    import statistics
    scales = [1.0 + spread_pct / 100.0 * (i - (n - 1) / 2) for i in range(n)]
    vals = [v for v in (run_one(s) for s in scales)
            if v is not None and math.isfinite(v)]
    if not vals:
        return None
    med = statistics.median(vals)
    return {"median": med, "min": min(vals), "max": max(vals), "n": len(vals),
            "spread_pct": (max(vals) - min(vals)) / med * 100.0 if med else None,
            "values": vals}
