"""Single-phase H-bridge inverter, open-loop SPWM, ideal switches.

Chosen as the first line-frequency topology because several quantities are known
EXACTLY, which makes it a hard test rather than a plausibility check:

    fundamental peak of v_ab : M * Vdc                       (linear modulation)
    RMS of v_ab, bipolar     : Vdc                           (it is always +/-Vdc)
    RMS of v_ab, unipolar    : Vdc * sqrt(2*M/pi)
    fundamental current      : M * Vdc / |R + j*w1*L|
    displacement factor      : cos(atan(w1*L/R))
    real power               : 0.5 * V1 * I1 * cos(phi)

The unipolar RMS falls out of the local duty: within a carrier period v_ab is
+/-Vdc for a fraction |m(t)| and zero otherwise, so <v^2> = Vdc^2*|m|, and
averaging |M sin| over a line cycle gives 2M/pi.

Two timescales: the line period is 20 ms while the carrier is tens of kHz, so
these runs are long.  Metrics come from an FFT over an exact integer number of
line periods (see harmonics.py), not from .meas.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .. import checks as C
from ..harmonics import Spectrum, power_metrics
from .base import Topology, fmt_eng


@dataclass
class HBridgeInverter(Topology):
    """Single-phase H-bridge, sinusoidal PWM, R-L load."""

    Vdc: float = 400.0
    M: float = 0.8             # modulation index, <= 1 for linear range
    f1: float = 50.0           # fundamental (line) frequency
    mf: int = 200              # carrier ratio: fc = mf * f1
    unipolar: bool = True      # False -> bipolar (2-level) switching
    Rload: float = 10.0
    Lload: float = 10e-3
    Ron: float = 1e-3
    Roff: float = 1e6
    tr: float = 1e-12
    n_settle: int = 2          # line cycles before measuring
    n_meas: int = 2            # line cycles measured (integer -> no FFT leakage)
    tmax_div: int = 200        # max timestep = Tc / tmax_div

    convergence_quantity = "V_1"
    convergence_tol_pct = 0.5

    measurements = ["Idc"]
    tolerances = {"V_1": 1.0, "V_rms": 1.0, "I_1": 1.5, "P": 2.0, "DPF": 1.0}

    # ---------------- analytical ---------------- #
    @property
    def fc(self):
        return self.mf * self.f1

    @property
    def T1(self):
        return 1.0 / self.f1

    @property
    def w1(self):
        return 2 * math.pi * self.f1

    def Z(self):
        return complex(self.Rload, self.w1 * self.Lload)

    def V_1_peak(self):
        return self.M * self.Vdc

    def V_rms_analytical(self):
        if self.unipolar:
            return self.Vdc * math.sqrt(2.0 * self.M / math.pi)
        return float(self.Vdc)

    def I_1_peak(self):
        return self.V_1_peak() / abs(self.Z())

    def phi(self):
        return math.atan2(self.w1 * self.Lload, self.Rload)

    def P_analytical(self):
        return 0.5 * self.V_1_peak() * self.I_1_peak() * math.cos(self.phi())

    def analytical(self) -> dict:
        return {"V_1": self.V_1_peak(),
                "V_rms": self.V_rms_analytical(),
                "I_1": self.I_1_peak(),
                "P": self.P_analytical(),
                "DPF": math.cos(self.phi())}

    def design_notes(self):
        z = self.Z()
        return [
            f"M = {self.M:g}, {'unipolar (3-level)' if self.unipolar else 'bipolar (2-level)'} "
            f"SPWM, mf = {self.mf} -> fc = {fmt_eng(self.fc,'Hz')}",
            f"load Z({self.f1:g} Hz) = {z.real:.3f} + j{z.imag:.3f} = "
            f"{abs(z):.3f} ohm at {math.degrees(self.phi()):.2f} deg",
            f"V1 peak = M*Vdc = {self.V_1_peak():.2f} V "
            f"({self.V_1_peak()/math.sqrt(2):.2f} V rms)",
            f"V_rms of v_ab = {self.V_rms_analytical():.2f} V "
            + ("(Vdc*sqrt(2M/pi))" if self.unipolar else "(= Vdc exactly)"),
            f"I1 peak = {self.I_1_peak():.3f} A, P = {self.P_analytical():.2f} W",
            f"run {self.n_settle}+{self.n_meas} line cycles = "
            f"{fmt_eng((self.n_settle+self.n_meas)*self.T1,'s')}, "
            f"max step {fmt_eng(1/self.fc/self.tmax_div,'s')}",
        ]

    def ideal_spectrum(self, n_per_carrier=256):
        """Harmonic amplitudes of v_ab from the ideal PWM logic alone.

        Constructed by building the switched waveform from the comparator rule
        and transforming it -- no SPICE involved -- so it is an independent
        prediction to check the simulated spectrum against. Preferred over
        quoting a Bessel-function series because it is verifiable line by line
        and stays correct for unipolar and bipolar without separate formulae.
        """
        import numpy as np
        n = int(self.mf * n_per_carrier)
        t = np.linspace(0.0, 1.0 / self.f1, n, endpoint=False)
        frac = (t * self.fc) % 1.0
        tri = np.where(frac < 0.5, 4 * frac - 1, 3 - 4 * frac)
        ref = self.M * np.sin(2 * np.pi * self.f1 * t)
        ga = np.where(ref > tri, 1.0, -1.0)
        gb = np.where(-ref > tri, 1.0, -1.0) if self.unipolar else -ga
        vab = (ga - gb) / 2.0 * self.Vdc
        spec = np.fft.rfft(vab) / n
        amp = np.abs(spec) * 2.0
        amp[0] = np.abs(spec[0])
        return amp

    def ideal_harmonic(self, k: int) -> float:
        a = self.ideal_spectrum()
        return float(a[k]) if k < len(a) else 0.0

    # ---------------- netlist ---------------- #
    def params(self) -> dict:
        return {"Vdc": self.Vdc, "M": self.M, "f1": self.f1, "mf": self.mf,
                "unipolar": int(self.unipolar), "Rload": self.Rload,
                "Lload": self.Lload, "Ron": self.Ron, "Roff": self.Roff,
                "tr": self.tr, "n_settle": self.n_settle, "n_meas": self.n_meas,
                "tmax_div": self.tmax_div}

    def measure_window(self):
        return (self.n_settle * self.T1,
                (self.n_settle + self.n_meas) * self.T1)

    def netlist(self) -> str:
        Tc = 1.0 / self.fc
        tstop = (self.n_settle + self.n_meas) * self.T1
        tmax = Tc / self.tmax_div
        # Triangular carrier: a PULSE whose rise and fall each take half the
        # period. Ton is a token 1 ps because Tr+Ton+Tf must not exceed Period.
        half = Tc / 2 - 1e-12
        # leg B compares against -m for unipolar, or is the complement of leg A
        # for bipolar
        legb = ("Bgb g_b 0 V=if(-V(ref)>V(tri),1,-1)" if self.unipolar
                else "Bgb g_b 0 V=-V(g_a)")
        return f"""* Single-phase H-bridge SPWM inverter -- generated by pwrlab
Vdc p_dc 0 {self.Vdc:g}
Vtri tri 0 PULSE(-1 1 0 {half:.12g} {half:.12g} 1p {Tc:.12g})
Vref ref 0 SINE(0 {self.M:g} {self.f1:g})
Bga g_a 0 V=if(V(ref)>V(tri),1,-1)
{legb}
S_AH p_dc a g_a 0 SWM
S_AL a 0 0 g_a SWM
S_BH p_dc b g_b 0 SWM
S_BL b 0 0 g_b SWM
Lo a m {self.Lload:g} Rser=0 Rpar=1G
Ro m b {self.Rload:g}
.model SWM SW(Ron={self.Ron:g} Roff={self.Roff:g} Vt=0 Vh=0)
.ic I(Lo)=0
.tran 0 {tstop:.12g} 0 {tmax:.12g}
.save V(a) V(b) I(Lo) I(Vdc)
.meas TRAN Idc AVG I(Vdc) FROM={self.n_settle*self.T1:.12g} TO={tstop:.12g}
.end
"""

    def switch_blocking_voltages(self) -> dict:
        return {n: self.Vdc for n in ("S_AH", "S_AL", "S_BH", "S_BL")}

    # ---------------- extraction ---------------- #
    def extract(self, res, waves) -> dict:
        if waves is None:
            return {}
        t_end = self.measure_window()[1]
        vab = waves["V(a)"] - waves["V(b)"]
        iout = waves["I(Lo)"]
        sv = Spectrum(waves.t, vab, self.f1, t_end, self.n_meas)
        si = Spectrum(waves.t, iout, self.f1, t_end, self.n_meas)
        pm = power_metrics(waves.t, vab, iout, self.f1, t_end, self.n_meas)
        out = {"V_1": sv.h(1), "V_rms": sv.rms(), "V_dc_offset": sv.dc,
               "I_1": si.h(1), "I_rms": si.rms(),
               "P": pm["P"], "S": pm["S"], "PF": pm["PF"], "DPF": pm["DPF"],
               "thd_i_pct": si.thd() * 100.0,
               "thd_v_total_pct": sv.thd_total() * 100.0}
        # P_dc must be integrated over the SAME window by the SAME method as P,
        # or the two disagree by ~0.05 % and the power-balance check reads that
        # methodological difference as a physical impossibility.
        if waves.has("I(Vdc)"):
            from ..harmonics import resample
            import numpy as np
            t0 = t_end - self.n_meas * self.T1
            _, idc = resample(waves.t, waves["I(Vdc)"], t0, t_end, 1 << 16)
            out["P_dc"] = float(-self.Vdc * np.mean(idc))
        return out

    # ---------------- checks ---------------- #
    def topology_checks(self, ex, waves, res):
        cs = []
        if ex.get("P_dc") is not None:
            cs.append(C.power_balance(ex["P_dc"], ex.get("P"), tol_pct=5.0))
        # v_ab must have no DC component: any offset means the two legs are not
        # switching symmetrically, which would saturate a real transformer
        if ex.get("V_dc_offset") is not None:
            cs.append(C.dc_offset(ex["V_dc_offset"], self.Vdc, tol_frac=0.005))
        # linear modulation only: M > 1 is overmodulation and the V1 = M*Vdc
        # model stops being valid
        cs.append(C.in_range("modulation index in linear range", self.M,
                             hi=1.0, severity=C.WARN))
        # even harmonics indicate a broken carrier/reference relationship
        if waves is not None:
            t_end = self.measure_window()[1]
            sv = Spectrum(waves.t, waves["V(a)"] - waves["V(b)"], self.f1,
                          t_end, self.n_meas)
            even = max(sv.h(k) for k in (2, 4, 6))
            cs.append(C.Check("no significant even harmonics",
                              even < 0.02 * sv.h(1), C.WARN,
                              f"largest even harmonic {even:.4g} V = "
                              f"{even/max(sv.h(1),1e-12)*100:.3f} % of fundamental"))
        return cs
