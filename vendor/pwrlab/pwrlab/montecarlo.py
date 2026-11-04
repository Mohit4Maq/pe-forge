"""Tolerance analysis: sensitivity, worst-case corners, Monte Carlo.

Use them in that order. Sensitivity costs 2N simulations and tells you which
tolerances matter at all; corners cost 2^k for the k that do; Monte Carlo costs
hundreds and gives the distribution. Running Monte Carlo first, over every
parameter, is the expensive way to discover that one component dominates -- and
on this host a simulation is seconds, not milliseconds, so that waste is real.

A caution this module enforces rather than assumes: a Monte Carlo sample whose
CHECKS failed is not a valid data point. Silently averaging it in reports a
distribution that includes unsettled or non-converged runs. Failed samples are
counted and excluded, and the count is part of the summary.
"""
from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .study import Study


@dataclass
class Tol:
    """A parameter tolerance.

    frac         fractional spread, e.g. 0.2 for +/-20 %
    dist         'normal' (frac = 3 sigma, so +/-frac is ~99.7 %),
                 'uniform' (hard bounds), 'worst' (only the two extremes)
    """
    frac: float
    dist: str = "normal"
    absolute: bool = False     # frac is an absolute delta, not a fraction

    def bounds(self, nominal):
        d = self.frac if self.absolute else abs(nominal) * self.frac
        return nominal - d, nominal + d

    def sample(self, nominal, rng, n):
        lo, hi = self.bounds(nominal)
        if self.dist == "uniform":
            return rng.uniform(lo, hi, n)
        if self.dist == "worst":
            return rng.choice([lo, hi], n)
        sigma = (hi - nominal) / 3.0
        return rng.normal(nominal, sigma, n)


@dataclass
class ToleranceResult:
    df: pd.DataFrame
    outputs: list
    nominal: dict = field(default_factory=dict)
    n_requested: int = 0

    @property
    def n_valid(self):
        return int(self.df["ok"].sum()) if "ok" in self.df else len(self.df)

    def valid(self):
        return self.df[self.df["ok"]] if "ok" in self.df else self.df

    def stats(self) -> pd.DataFrame:
        v = self.valid()
        rows = []
        for o in self.outputs:
            if o not in v:
                continue
            s = v[o].astype(float)
            nom = self.nominal.get(o)
            rows.append({
                "output": o, "nominal": nom,
                "mean": s.mean(), "std": s.std(),
                "min": s.min(), "p1": s.quantile(.01), "p5": s.quantile(.05),
                "p50": s.quantile(.50), "p95": s.quantile(.95),
                "p99": s.quantile(.99), "max": s.max(),
                "spread_pct": ((s.max() - s.min()) / abs(nom) * 100
                               if nom else float("nan")),
            })
        return pd.DataFrame(rows)

    def margin(self, output, lo=None, hi=None) -> dict:
        """How much of the distribution clears a specification limit.

        Reports the sigma distance to the nearer limit (a Cpk-style number) and
        the observed violation fraction, which is the honest figure when the
        distribution is not normal.
        """
        s = self.valid()[output].astype(float)
        mu, sd = s.mean(), s.std()
        out = {"output": output, "mean": mu, "std": sd, "n": len(s)}
        viol = 0
        if lo is not None:
            out["sigma_to_lo"] = (mu - lo) / sd if sd > 0 else math.inf
            viol += int((s < lo).sum())
        if hi is not None:
            out["sigma_to_hi"] = (hi - mu) / sd if sd > 0 else math.inf
            viol += int((s > hi).sum())
        out["violations"] = viol
        out["violation_pct"] = viol / len(s) * 100 if len(s) else float("nan")
        cands = [out[k] for k in ("sigma_to_lo", "sigma_to_hi") if k in out]
        out["cpk_like"] = min(cands) / 3.0 if cands else float("nan")
        return out

    def summary(self) -> str:
        L = [f"{self.n_valid}/{self.n_requested} samples usable"
             + ("" if self.n_valid == self.n_requested
                else f"  ({self.n_requested - self.n_valid} rejected by checks)")]
        L.append(self.stats().to_string(index=False, float_format=lambda v: f"{v:.6g}"))
        return "\n".join(L)


class ToleranceStudy:
    def __init__(self, topology, tolerances: dict, runner=None, workroot=None):
        self.topology = topology
        self.tolerances = {k: (v if isinstance(v, Tol) else Tol(v))
                           for k, v in tolerances.items()}
        self.study = Study(topology, runner=runner, workroot=workroot)
        self.runner = self.study.runner

    def _variant(self, values: dict):
        t = copy.deepcopy(self.topology)
        for k, v in values.items():
            setattr(t, k, v)
        return t

    def _nominal(self):
        return {k: getattr(self.topology, k) for k in self.tolerances}

    # ------------------------------------------------------------------ #
    def sensitivity(self, outputs, delta=0.05, progress=True) -> pd.DataFrame:
        """One-at-a-time normalised sensitivity: (dy/y) / (dx/x).

        A coefficient of 1 means a 1 % part shift moves the output 1 %. Costs
        2N+1 simulations, and usually shows that most tolerances are irrelevant.
        """
        nom = self._nominal()
        topos = [self.topology]
        index = [("nominal", 0)]
        for k, v0 in nom.items():
            for sign in (-1, +1):
                topos.append(self._variant({k: v0 * (1 + sign * delta)}))
                index.append((k, sign))
        if progress:
            print(f"sensitivity: {len(topos)} runs "
                  f"({len(nom)} parameters at +/-{delta*100:g} %)")
        res = self.study.run_batch(topos)
        base = res[0]
        if not base.ok:
            raise RuntimeError(f"nominal case failed: {base.checks.summary()}")
        rows = []
        for k in nom:
            lo = res[index.index((k, -1))]
            hi = res[index.index((k, +1))]
            for o in outputs:
                y0 = base.extracted.get(o)
                yl, yh = lo.extracted.get(o), hi.extracted.get(o)
                if None in (y0, yl, yh) or not y0:
                    continue
                rows.append({"param": k, "output": o,
                             "S": ((yh - yl) / y0) / (2 * delta),
                             "y_nominal": y0,
                             "pct_per_pct": ((yh - yl) / y0) / (2 * delta)})
        df = pd.DataFrame(rows)
        if not df.empty:
            df["abs_S"] = df["S"].abs()
            df = df.sort_values(["output", "abs_S"], ascending=[True, False])
        return df.reset_index(drop=True)

    def corners(self, outputs, params=None, progress=True) -> pd.DataFrame:
        """Every combination of extremes for the chosen parameters (2^k runs)."""
        keys = list(params or self.tolerances)
        nom = self._nominal()
        combos = []
        for mask in range(1 << len(keys)):
            vals = {}
            for i, k in enumerate(keys):
                lo, hi = self.tolerances[k].bounds(nom[k])
                vals[k] = hi if (mask >> i) & 1 else lo
            combos.append(vals)
        if progress:
            print(f"corners: {len(combos)} runs over {keys}")
        res = self.study.run_batch([self._variant(c) for c in combos])
        rows = [{**c, **{o: r.extracted.get(o) for o in outputs},
                 "ok": r.ok, "n_fail": len(r.checks.failures)}
                for c, r in zip(combos, res)]
        return pd.DataFrame(rows)

    def monte_carlo(self, outputs, n=100, seed=0, progress=True) -> ToleranceResult:
        rng = np.random.default_rng(seed)
        nom = self._nominal()
        samples = {k: t.sample(nom[k], rng, n) for k, t in self.tolerances.items()}
        combos = [{k: float(samples[k][i]) for k in samples} for i in range(n)]
        if progress:
            print(f"monte carlo: {n} samples over {list(nom)}")
        cb = None
        if progress:
            def cb(done, total, r):
                if done % max(1, total // 10) == 0 or done == total:
                    print(f"  {done}/{total}", flush=True)
        res = self.study.run_batch([self._variant(c) for c in combos], progress=cb)
        rows = []
        for c, r in zip(combos, res):
            row = dict(c)
            row.update({o: r.extracted.get(o) for o in outputs})
            row["ok"] = r.ok
            row["n_fail"] = len(r.checks.failures)
            rows.append(row)
        base = self.study.run(tag="nominal", converge=False)
        return ToleranceResult(pd.DataFrame(rows), list(outputs),
                               {o: base.extracted.get(o) for o in outputs}, n)
