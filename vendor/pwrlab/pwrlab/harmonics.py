"""Harmonic analysis for line-frequency converters.

Two details matter more than the FFT itself, and both are easy to get silently
wrong:

1. LTspice uses a VARIABLE timestep.  Feeding its raw samples to an FFT weights
   the densely-sampled switching edges far more than the flat intervals, which
   corrupts every amplitude.  Everything here resamples onto a uniform grid
   first.

2. The analysis window must be an EXACT integer number of fundamental periods.
   Then every harmonic lands precisely on an FFT bin, there is no spectral
   leakage, and no window function is needed (a Hann window would itself distort
   the amplitudes we are trying to measure).  A window that is off by 0.1 % of a
   period smears the fundamental into its neighbours and inflates THD.
"""
from __future__ import annotations

import numpy as np


def resample(t, y, t0, t1, n):
    """Linear resample of (t, y) onto n uniform points over [t0, t1)."""
    grid = np.linspace(t0, t1, int(n), endpoint=False)
    order = np.argsort(t)
    return grid, np.interp(grid, t[order], y[order])


def _next_pow2(n):
    return 1 << (int(n) - 1).bit_length()


class Spectrum:
    """Harmonic amplitudes of y over an integer number of periods of f1.

    `amp[k]` is the PEAK amplitude of the k-th harmonic (k=1 is the fundamental);
    `phase[k]` is its phase in radians.
    """

    def __init__(self, t, y, f1, t_end, n_cycles=2, points_per_cycle=32768):
        self.f1 = f1
        self.n_cycles = int(n_cycles)
        T1 = 1.0 / f1
        t0 = t_end - self.n_cycles * T1
        n = _next_pow2(points_per_cycle * self.n_cycles)
        self.t, self.y = resample(np.asarray(t), np.asarray(y), t0, t_end, n)
        self.n = n
        self.fs = n / (self.n_cycles * T1)
        spec = np.fft.rfft(self.y) / n
        self._c = spec
        self.amp = np.abs(spec) * 2.0
        self.amp[0] = np.abs(spec[0])          # DC is not doubled
        self.phase = np.angle(spec)
        self.dc = float(self.amp[0])

    def h(self, k: int) -> float:
        """Peak amplitude of harmonic k (bin k*n_cycles)."""
        idx = k * self.n_cycles
        return float(self.amp[idx]) if idx < len(self.amp) else 0.0

    def h_phase(self, k: int) -> float:
        idx = k * self.n_cycles
        return float(self.phase[idx]) if idx < len(self.phase) else 0.0

    def fundamental(self) -> float:
        return self.h(1)

    def rms(self) -> float:
        return float(np.sqrt(np.mean(self.y ** 2)))

    def thd(self, n_max: int = 50) -> float:
        """THD relative to the fundamental, harmonics 2..n_max (IEC style)."""
        f = self.h(1)
        if f <= 0:
            return float("nan")
        s = sum(self.h(k) ** 2 for k in range(2, n_max + 1))
        return float(np.sqrt(s) / f)

    def thd_total(self) -> float:
        """THD against total RMS, i.e. including everything above the
        fundamental (switching sidebands too), not just low-order harmonics."""
        f = self.h(1) / np.sqrt(2.0)
        r = self.rms()
        if f <= 0 or r < f:
            return float("nan")
        return float(np.sqrt(max(r * r - f * f, 0.0)) / f)

    def harmonics(self, n_max=25) -> dict:
        return {k: self.h(k) for k in range(1, n_max + 1)}


def power_metrics(t, v, i, f1, t_end, n_cycles=2, points_per_cycle=32768):
    """Real/apparent power, power factor and displacement factor.

    True PF = P / (Vrms*Irms) includes distortion; displacement PF is cos of the
    angle between the two FUNDAMENTALS.  Reporting only the displacement factor
    is the classic way to make a distorted waveform look good.
    """
    T1 = 1.0 / f1
    t0 = t_end - n_cycles * T1
    n = _next_pow2(points_per_cycle * n_cycles)
    _, vv = resample(np.asarray(t), np.asarray(v), t0, t_end, n)
    _, ii = resample(np.asarray(t), np.asarray(i), t0, t_end, n)
    P = float(np.mean(vv * ii))
    Vrms = float(np.sqrt(np.mean(vv ** 2)))
    Irms = float(np.sqrt(np.mean(ii ** 2)))
    S = Vrms * Irms
    sv = Spectrum(t, v, f1, t_end, n_cycles, points_per_cycle)
    si = Spectrum(t, i, f1, t_end, n_cycles, points_per_cycle)
    dphi = sv.h_phase(1) - si.h_phase(1)
    return {"P": P, "S": S, "Q": float(np.sqrt(max(S * S - P * P, 0.0))),
            "V_rms": Vrms, "I_rms": Irms,
            "PF": (P / S if S > 0 else float("nan")),
            "DPF": float(np.cos(dphi)),
            "V_1": sv.h(1), "I_1": si.h(1),
            "thd_v": sv.thd(), "thd_i": si.thd()}


# --------------------------------------------------------------------------- #
# spectrum as a checked deliverable
# --------------------------------------------------------------------------- #
def parseval_residual(spec: "Spectrum", n_max=None) -> float:
    """Fraction of the signal's mean square NOT accounted for by harmonics 0..n_max.

    Parseval's theorem says the two must agree. A large residual means the FFT is
    missing energy -- almost always because the analysis window is not an exact
    integer number of periods, which smears every amplitude. This is the check
    that makes a reported spectrum trustworthy rather than merely plausible.
    """
    total = float(np.mean(spec.y ** 2))
    if total <= 0:
        return 0.0
    n_max = n_max or (len(spec.amp) // spec.n_cycles - 1)
    acc = spec.dc ** 2
    for k in range(1, int(n_max) + 1):
        acc += spec.h(k) ** 2 / 2.0
    return abs(total - acc) / total


def spectrum_table(spec: "Spectrum", f1: float, n_max=50, floor_frac=1e-4):
    """Harmonics worth reporting: (order, frequency, amplitude, % of fundamental)."""
    h1 = spec.h(1)
    rows = []
    for k in range(1, int(n_max) + 1):
        a = spec.h(k)
        if h1 > 0 and a / h1 < floor_frac and k > 1:
            continue
        rows.append({"order": k, "freq_Hz": k * f1, "amplitude": a,
                     "pct_of_fundamental": (a / h1 * 100) if h1 else float("nan")})
    return rows
