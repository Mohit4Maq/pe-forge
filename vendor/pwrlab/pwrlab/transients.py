"""Fault and dynamic-transient studies.

Steady-state work asks "what does this converter deliver?"; a transient study
asks "what does it survive, and how fast does it recover?".  Different questions
need different measurements: peak stress, overshoot, settling time and recovery,
not cycle averages.  Nothing here uses .meas -- LTspice's AVG/RMS over a window
is exactly the wrong instrument when the point is the excursion.

Events are applied as netlist transformations so they work for any topology that
declares its output node.  Each event is a deliberate perturbation:

    StartUp        strip the .ic seeding and measure inrush from a cold start
    LoadStep       switch a parallel resistor in (or out) at a chosen instant
    ShortCircuit   clamp the output through a small resistance
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np

from . import checks as C
from .waveforms import Waves


# --------------------------------------------------------------------------- #
# events
# --------------------------------------------------------------------------- #
@dataclass
class Event:
    t: float = 0.0
    name: str = "event"

    def apply(self, netlist: str, topo) -> str:
        return netlist

    def extend_to(self, topo) -> float | None:
        """Required stop time, if the event needs a longer run."""
        return None


@dataclass
class StartUp(Event):
    """Cold start: every .ic removed, so the converter charges from zero.

    This is where inrush lives.  The steady-state netlists seed their state
    deliberately (see the DAB and buck notes); removing that is the point here.
    """
    name: str = "startup"

    def apply(self, netlist, topo):
        return "\n".join(l for l in netlist.splitlines()
                         if not l.lower().startswith(".ic")) + "\n"


@dataclass
class LoadStep(Event):
    """Step the load by switching a parallel resistor in at time t.

    factor < 1 means a heavier load: the parallel resistor is chosen so the
    combination equals factor * R_nominal.
    """
    factor: float = 0.5
    name: str = "load_step"

    def apply(self, netlist, topo):
        node = topo.output_node()
        R = topo.load_resistance()
        if node is None or R is None:
            raise ValueError(f"{topo.name} does not declare an output node/load")
        if not 0 < self.factor < 1:
            raise ValueError("LoadStep factor must be between 0 and 1")
        r_par = R * self.factor / (1.0 - self.factor)
        add = (f"Vstep_ vstep_ 0 PWL(0 -1 {self.t:.12g} -1 "
               f"{self.t + 1e-9:.12g} 1)\n"
               f"Sstep_ {node} nstep_ vstep_ 0 STEPSW\n"
               f"Rstep_ nstep_ 0 {r_par:.12g}\n"
               f".model STEPSW SW(Ron=1m Roff=1e9 Vt=0 Vh=0)")
        return _insert_before_end(netlist, add)


@dataclass
class ShortCircuit(Event):
    """Hard fault: clamp the output through R_fault at time t."""
    R_fault: float = 50e-3
    name: str = "short_circuit"

    def apply(self, netlist, topo):
        node = topo.output_node()
        if node is None:
            raise ValueError(f"{topo.name} does not declare an output node")
        add = (f"Vsc_ vsc_ 0 PWL(0 -1 {self.t:.12g} -1 "
               f"{self.t + 1e-9:.12g} 1)\n"
               f"Ssc_ {node} nsc_ vsc_ 0 SCSW\n"
               f"Rsc_ nsc_ 0 {self.R_fault:.12g}\n"
               f".model SCSW SW(Ron=1m Roff=1e9 Vt=0 Vh=0)")
        return _insert_before_end(netlist, add)


def _insert_before_end(netlist: str, block: str) -> str:
    lines = netlist.splitlines()
    for i, l in enumerate(lines):
        if l.strip().lower().startswith(".end"):
            lines[i:i] = block.splitlines()
            return "\n".join(lines) + "\n"
    return netlist + block + "\n"


def _set_tstop(netlist: str, tstop: float) -> str:
    def sub(m):
        return f".tran {m.group(1)} {tstop:.12g} {m.group(3)}"
    return re.sub(r"^\.tran\s+(\S+)\s+(\S+)\s+(.*)$", sub, netlist,
                  flags=re.M | re.I)


# --------------------------------------------------------------------------- #
# metrics
# --------------------------------------------------------------------------- #
def settling_time(t, y, final, band_frac=0.02, t_from=0.0):
    """First instant after t_from beyond which |y-final| stays inside the band."""
    band = abs(final) * band_frac if final else band_frac
    m = t >= t_from
    tt, yy = t[m], y[m]
    outside = np.flatnonzero(np.abs(yy - final) > band)
    if outside.size == 0:
        return 0.0
    last = outside[-1]
    if last >= len(tt) - 1:
        return float("inf")          # never settles within the run
    return float(tt[last + 1] - t_from)


@dataclass
class TransientResult:
    topology: object
    event: Event
    metrics: dict = field(default_factory=dict)
    checks: C.CheckSet = field(default_factory=C.CheckSet)
    sim: object = None
    waves: object = None

    @property
    def ok(self):
        return self.checks.ok

    def summary(self) -> str:
        L = [f"{self.topology.name} -- {self.event.name} at t = "
             f"{self.event.t*1e3:.3f} ms", ""]
        for k, v in self.metrics.items():
            L.append(f"  {k:34s} {v:12.6g}" if isinstance(v, float)
                     else f"  {k:34s} {v}")
        L += ["", "Checks:"] + [f"  {c}" for c in self.checks]
        L += ["", "  " + self.checks.summary()]
        return "\n".join(L)


class TransientStudy:
    def __init__(self, topology, runner):
        self.topo = topology
        self.runner = runner

    def run(self, event: Event, watch: dict, tstop=None, tag=None) -> TransientResult:
        """Run one event.

        watch: {signal: {'limit_hi':..., 'limit_lo':..., 'settle_band':...}}
        Signals are raw trace names, e.g. 'V(out)' or 'I(L1)'.
        """
        net = event.apply(self.topo.netlist(), self.topo)
        if tstop:
            net = _set_tstop(net, tstop)
        need = " ".join(w for w in watch if f"I({w})" != w)
        res = self.runner.run_text(net, dict(self.topo.params()),
                                   self.topo.measurements,
                                   tag=tag or event.name)
        tr = TransientResult(self.topo, event, sim=res)
        tr.checks.add(C.sim_completed(res))
        if not res.ok:
            return tr
        w = Waves(res.raw_path)
        tr.waves = w
        t = w.t
        for sig, spec in watch.items():
            if not w.has(sig):
                tr.checks.add(C.Check(f"{sig} recorded", False, C.ERROR,
                                      f"not in .save; traces: {w.names[:8]}"))
                continue
            y = w[sig]
            post = t >= event.t
            pre = t < event.t
            tr.metrics[f"{sig} peak"] = float(np.max(y))
            tr.metrics[f"{sig} min"] = float(np.min(y))
            if post.sum() > 4:
                tr.metrics[f"{sig} peak after event"] = float(np.max(np.abs(y[post])))
                final = float(np.mean(y[t >= t[-1] - 0.05 * (t[-1] - event.t)]))
                tr.metrics[f"{sig} final"] = final
                if pre.sum() > 4:
                    before = float(np.mean(y[pre][-max(4, pre.sum() // 10):]))
                    tr.metrics[f"{sig} before event"] = before
                    if abs(final) > 1e-12:
                        tr.metrics[f"{sig} excursion %"] = \
                            (float(np.max(np.abs(y[post] - final))) / abs(final)) * 100
                st = settling_time(t, y, final,
                                   spec.get("settle_band", 0.02), event.t)
                tr.metrics[f"{sig} settling s"] = st
                if spec.get("settle_within"):
                    tr.checks.add(C.in_range(
                        f"{sig} settles within {spec['settle_within']*1e3:g} ms",
                        st, hi=spec["settle_within"], unit=" s"))
            if spec.get("limit_hi") is not None:
                tr.checks.add(C.in_range(f"{sig} stays under limit",
                                         float(np.max(y)), hi=spec["limit_hi"]))
            if spec.get("limit_lo") is not None:
                tr.checks.add(C.in_range(f"{sig} stays above limit",
                                         float(np.min(y)), lo=spec["limit_lo"]))
            tr.checks.add(C.finite(f"{sig} remains finite",
                                   float(np.max(np.abs(y)))))
        return tr
