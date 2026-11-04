"""Waveform loading and steady-state metrics computed from the .raw directly.

Why not rely on .meas alone: .meas can only report what you thought to ask for,
and it reports it without telling you whether the circuit had actually reached
steady state.  Recomputing from the raw lets the checks layer ask questions the
netlist author did not anticipate -- above all "is this periodic yet?".
"""
from __future__ import annotations

import numpy as np
from spicelib.raw.raw_read import RawRead


class Waves:
    """Thin wrapper over a .raw with a monotonic, positive time base.

    LTspice stores the time vector with sign flags on compressed points, so the
    raw values can come back negative; abs() is the documented fix.
    """

    def __init__(self, path):
        self._raw = RawRead(str(path))
        self.names = list(self._raw.get_trace_names())
        self.t = np.abs(np.asarray(self._raw.get_trace("time").get_wave(), dtype=float))

    def has(self, name) -> bool:
        return name in self.names

    def __getitem__(self, name) -> np.ndarray:
        return np.asarray(self._raw.get_trace(name).get_wave(), dtype=float)

    def get(self, name, default=None):
        return self[name] if self.has(name) else default

    def window(self, t0, t1):
        m = (self.t >= t0) & (self.t <= t1)
        return m

    # ---- integral metrics ------------------------------------------------- #
    def _trapz(self, y, m):
        t = self.t[m]
        if t.size < 2:
            return None, None
        return np.trapezoid(y[m], t), t[-1] - t[0]

    def mean(self, y, t0, t1):
        m = self.window(t0, t1)
        i, dt = self._trapz(y, m)
        return None if i is None or dt <= 0 else i / dt

    def rms(self, y, t0, t1):
        m = self.window(t0, t1)
        i, dt = self._trapz(y ** 2, m)
        return None if i is None or dt <= 0 else float(np.sqrt(i / dt))

    def cycle_means(self, y, t_end, period, n):
        """Mean of y over each of the last n periods, oldest first.

        Drift across these is the honest test for steady state -- far more
        reliable than eyeballing a plot or trusting a fixed settle time.
        """
        out = []
        for k in range(n, 0, -1):
            v = self.mean(y, t_end - k * period, t_end - (k - 1) * period)
            if v is None:
                return []
            out.append(v)
        return out


    def cycle_ripple(self, y, t_end, period, n=5):
        """Median peak-to-peak WITHIN a cycle, over the last n cycles.

        Not (max - min) across the whole measurement window: that conflates the
        per-cycle ripple with any residual settling drift across the window, and
        silently overstates ripple.  For the default buck it reported 8.89 mV
        where the true cycle ripple is 8.26 mV -- a 7.7 % error that looked like
        a simulation problem.  The median over several cycles also rejects a
        single outlier cycle.
        """
        pp = []
        for k in range(n, 0, -1):
            m = self.window(t_end - k * period, t_end - (k - 1) * period)
            if m.sum() < 4:
                continue
            w = y[m]
            pp.append(float(w.max() - w.min()))
        return float(np.median(pp)) if pp else None


def ripple(y) -> float:
    return float(np.max(y) - np.min(y))
