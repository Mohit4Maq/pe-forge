"""Study: run a topology, then decide whether to believe the answer.

    st = Study(DAB(phi_deg=45))
    r  = st.run()                 # simulate + full check battery
    print(r.report())

    df = st.sweep({"phi_deg": [10, 20, 30, 45, 60, 90]})

`run(converge=True)` additionally re-runs at half the maximum timestep and
requires the answer not to move.  That costs a second simulation and is the
difference between "the solver said 7502 W" and "the circuit delivers 7502 W".
"""
from __future__ import annotations

import copy
import itertools
from dataclasses import dataclass, field

import pandas as pd

from . import checks as C
from .sim import Runner
from .waveforms import Waves


@dataclass
class StudyResult:
    topology: object
    params: dict
    extracted: dict = field(default_factory=dict)
    analytical: dict = field(default_factory=dict)
    checks: C.CheckSet = field(default_factory=C.CheckSet)
    sim: object = None
    wall_s: float = 0.0

    @property
    def ok(self) -> bool:
        return self.checks.ok

    def row(self) -> dict:
        r = dict(self.params)
        for k, v in self.extracted.items():
            r[k] = v
        for k, v in self.analytical.items():
            r[f"{k}_theory"] = v
            if isinstance(v, (int, float)) and v not in (0, None):
                s = self.extracted.get(k)
                if isinstance(s, (int, float)):
                    r[f"{k}_err_pct"] = (s - v) / v * 100.0
        r["ok"] = self.ok
        r["n_fail"] = len(self.checks.failures)
        r["wall_s"] = self.wall_s
        return r

    def report(self) -> str:
        L = [f"{self.topology.summary()}", ""]
        notes = self.topology.design_notes()
        if notes:
            L += ["Design:"] + [f"  {n}" for n in notes] + [""]
        L.append("Results:")
        for k in sorted(set(self.extracted) | set(self.analytical)):
            s, a = self.extracted.get(k), self.analytical.get(k)
            if isinstance(s, (int, float)) and isinstance(a, (int, float)) and a:
                L.append(f"  {k:22s} sim {s:>14.6g}   theory {a:>14.6g}   "
                         f"{(s-a)/a*100:+8.3f} %")
            elif s is not None:
                L.append(f"  {k:22s} sim {s!r:>14}")
        L += ["", "Checks:"] + [f"  {c}" for c in self.checks]
        L += ["", "  " + self.checks.summary()]
        return "\n".join(L)


class Study:
    def __init__(self, topology, runner: Runner | None = None, workroot=None,
                 keep=False):
        self.topology = topology
        self.runner = runner or Runner(workroot=workroot, keep=keep)

    # ---------------------------------------------------------------- #
    def _simulate(self, topo, tag, tmax_scale=1.0):
        params = dict(topo.params())
        if tmax_scale != 1.0 and "tmax" in params:
            params["tmax"] = params["tmax"] * tmax_scale
        net = topo.netlist()
        # apply any overridden params by regenerating from the topology itself:
        # topologies render their own .param lines, so nothing to substitute here
        return self.runner.run_text(net, params, topo.measurements, tag=tag)

    def run(self, tag="run", converge=True, tmax_ratio=0.5) -> StudyResult:
        topo = self.topology
        # Reject a non-circuit before the solver is asked to integrate it: a
        # negative inductance does not error in LTspice, it stalls.
        topo.validate()
        res = self._simulate(topo, tag)
        sr = StudyResult(topology=topo, params=dict(topo.params()), sim=res,
                         wall_s=res.wall_s)

        sr.checks.add(C.sim_completed(res))
        if not res.ok:
            return sr
        sr.checks.add(C.measurements_present(res, topo.measurements))

        waves = Waves(res.raw_path) if res.raw_path else None
        sr.extracted = topo.extract(res, waves)
        sr.analytical = topo.analytical()

        for k, a in sr.analytical.items():
            s = sr.extracted.get(k)
            if isinstance(a, (int, float)) and isinstance(s, (int, float)):
                sr.checks.add(C.finite(f"{k} is finite", s))
                sr.checks.add(C.relative_error(k, s, a, topo.tol_for(k)))

        sr.checks.extend(topo.topology_checks(sr.extracted, waves, res))

        if converge:
            fine = copy.deepcopy(topo)
            if hasattr(fine, "tmax_div"):
                fine.tmax_div = topo.tmax_div * int(1 / tmax_ratio)
                res2 = self._simulate(fine, tag + "_fine")
                sr.wall_s += res2.wall_s
                if res2.ok:
                    w2 = Waves(res2.raw_path)
                    e2 = fine.extract(res2, w2)
                    key = getattr(topo, "convergence_quantity", None)
                    if key and key in sr.extracted:
                        sr.checks.add(C.convergence(
                            [sr.extracted[key], e2.get(key)],
                            tol_pct=getattr(topo, "convergence_tol_pct", 1.0),
                            label=f"timestep ({key})"))
                else:
                    sr.checks.add(C.Check("timestep convergence", False, C.WARN,
                                          f"refinement run failed: {res2.why_not_ok}"))
        return sr

    # ---------------------------------------------------------------- #
    def _post(self, topo, res, converge_val=None) -> StudyResult:
        """Turn a finished SimResult into a checked StudyResult."""
        sr = StudyResult(topology=topo, params=dict(topo.params()), sim=res,
                         wall_s=res.wall_s)
        sr.checks.add(C.sim_completed(res))
        if not res.ok:
            return sr
        sr.checks.add(C.measurements_present(res, topo.measurements))
        waves = Waves(res.raw_path) if res.raw_path else None
        sr.extracted = topo.extract(res, waves)
        sr.analytical = topo.analytical()
        for k, a in sr.analytical.items():
            v = sr.extracted.get(k)
            if isinstance(a, (int, float)) and isinstance(v, (int, float)):
                sr.checks.add(C.finite(f"{k} is finite", v))
                sr.checks.add(C.relative_error(k, v, a, topo.tol_for(k)))
        sr.checks.extend(topo.topology_checks(sr.extracted, waves, res))
        return sr

    def run_batch(self, topologies, progress=None) -> list:
        """Run many topology instances IN PARALLEL, then check each.

        Study.run() is one-at-a-time; anything with more than a handful of points
        (a sweep, and especially Monte Carlo) must go through here or it wastes
        the 3 concurrent simulations the host can actually sustain.
        """
        # Validate per point, never as a batch precondition. A Monte Carlo run
        # of 500 samples must not lose 499 good results because one perturbed
        # sample wandered non-physical: that point is REJECTED, with a failing
        # check saying why, and the rest of the batch proceeds. Positions are
        # preserved because callers zip these results against their inputs.
        invalid = {}
        for i, t in enumerate(topologies):
            try:
                t.validate()
            except ValueError as e:
                invalid[i] = e

        runnable = [(i, t) for i, t in enumerate(topologies) if i not in invalid]
        jobs = [(f"pt{i:04d}", t.netlist(), dict(t.params()), t.measurements,
                 ".net") for i, t in runnable]
        results = self.runner.map(jobs, progress=progress) if jobs else []

        out = [None] * len(topologies)
        for (i, t), r in zip(runnable, results):
            out[i] = self._post(t, r)
        for i, e in invalid.items():
            t = topologies[i]
            sr = StudyResult(topology=t, params=dict(t.params()))
            sr.checks.add(C.Check("parameters describe a circuit", False,
                                  C.ERROR, str(e)))
            out[i] = sr
        return out

    def sweep(self, grid: dict, converge=False, progress=True) -> pd.DataFrame:
        keys = list(grid)
        combos = [dict(zip(keys, c)) for c in
                  itertools.product(*(grid[k] if isinstance(grid[k], (list, tuple))
                                      else [grid[k]] for k in keys))]
        topos = []
        for combo in combos:
            t = copy.deepcopy(self.topology)
            for k, v in combo.items():
                setattr(t, k, v)
            topos.append(t)

        if converge:
            # the convergence re-run doubles the work, so keep that path serial
            results = []
            for i, t in enumerate(topos):
                results.append(Study(t, runner=self.runner).run(
                    tag=f"pt{i:03d}", converge=True))
        else:
            cb = None
            if progress:
                def cb(done, total, r):
                    print(f"  [{done}/{total}] {'ok' if r.ok else 'sim FAILED'}",
                          flush=True)
            results = self.run_batch(topos, progress=cb)
        rows = [{**c, **r.row()} for c, r in zip(combos, results)]
        df = pd.DataFrame(rows)
        df.attrs["results"] = results
        df.attrs["topology"] = self.topology.name
        return df
