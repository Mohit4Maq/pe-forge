"""Synchronous buck converter, open loop, ideal switches.

Included as the generality test for the framework: it shares no analytical
machinery with the DAB, and its closed-form model (CCM/DCM boundary, ripple,
RMS currents) is exact for ideal components, so a disagreement is the
simulation's fault and nothing else.

Unlike the DAB this converter has an output filter that must SETTLE, so the
steady-state check earns its keep here: the natural time constant is set by the
LC and the load, and a naive settle time silently biases the answer.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .. import checks as C
from .base import Topology, fmt_eng


@dataclass
class Buck(Topology):
    """Synchronous buck, open loop, ideal switches (set synchronous=False for a diode)."""

    Vin: float = 48.0
    D: float = 0.5             # duty cycle
    fs: float = 200e3
    L: float = 47e-6
    Cout: float = 100e-6
    Rload: float = 2.0
    synchronous: bool = True   # False -> diode rectifier, which CAN enter DCM
    Ron: float = 5e-3
    Roff: float = 1e6
    Resr: float = 2e-3         # output cap ESR
    RL: float = 5e-3           # inductor DCR
    tr: float = 1e-12
    Nsettle: int | None = None   # None -> derived from the filter time constant
    Nmeas: int = 20
    tmax_div: int = 500

    convergence_quantity = "V_out"
    convergence_tol_pct = 0.5

    measurements = ["Vout", "Vripple_max", "Vripple_min", "ILavg", "ILrms",
                    "ILmax", "ILmin", "Iin", "Pin", "Pout"]
    tolerances = {"V_out": 2.0, "I_L_rms": 2.0, "I_L_ripple": 5.0}

    # ---------------- analytical ---------------- #
    @property
    def T(self):
        return 1.0 / self.fs

    def di_L(self) -> float:
        """Peak-to-peak inductor ripple, CCM: (Vin-Vo)*D*T/L."""
        vo = self.Vin * self.D
        return (self.Vin - vo) * self.D * self.T / self.L

    def is_ccm(self) -> bool:
        """A SYNCHRONOUS buck is always continuous.

        The low-side switch conducts in both directions, so the inductor current
        simply goes negative at light load (forced continuous conduction) and the
        DCM criterion never applies.  Applying it anyway predicted 31.98 V where
        the converter delivers 24.00 V -- a 25 % error the framework caught via
        the conduction-mode check.  Only a diode rectifier can enter DCM.
        """
        if self.synchronous:
            return True
        return self.i_load_ideal() > self.di_L() / 2.0

    def current_reverses(self) -> bool:
        """True when a synchronous buck runs with reverse inductor current."""
        return self.synchronous and self.i_load_ideal() < self.di_L() / 2.0

    def lc_decay_tau(self) -> float:
        """Envelope decay time of the output LC, 1/(zeta*w0).

        This, not R*C, is what limits how soon the ripple can be measured: the
        filter is lightly damped (zeta ~ 0.18 at full load, 0.012 at Rload=100)
        so any startup mismatch rings for milliseconds.  Damping comes from the
        series path (RL+Ron+ESR) and from the load across the capacitor.
        """
        w0 = 1.0 / math.sqrt(self.L * self.Cout)
        rs = self.RL + self.Ron + self.Resr
        zeta = (rs / 2.0) * math.sqrt(self.Cout / self.L) \
            + (1.0 / (2.0 * self.Rload)) * math.sqrt(self.L / self.Cout)
        return 1.0 / max(zeta * w0, 1e-12)

    def settle_periods(self) -> int:
        """Periods to run before measuring: 5 envelope time constants.

        Measured consequence of getting this wrong: at Rload=2 with only 100
        periods the output ripple came out 3.6 % high (and 20 % high before the
        .ic was corrected), while every other quantity looked perfect.  Ripple is
        a small difference of large numbers and is the last thing to settle, so
        it sets the requirement.
        """
        if self.Nsettle is not None:
            return int(self.Nsettle)
        tau = max(self.lc_decay_tau(), 0.5 * self.Rload * self.Cout)
        return int(max(150, min(3000, math.ceil(5.0 * tau / self.T))))

    def i_load_ideal(self) -> float:
        return self.Vin * self.D / self.Rload

    def V_out_ideal(self) -> float:
        """CCM with conduction losses: the divider formed by RL and Ron against
        the load. Ignoring this gives a ~1 % error that looks like a bug."""
        if not self.is_ccm():
            return self._vout_dcm()
        r_series = self.RL + self.Ron
        return self.Vin * self.D * self.Rload / (self.Rload + r_series)

    def _vout_dcm(self) -> float:
        """DCM: M = 2/(1 + sqrt(1 + 4K/D^2)) with K = 2L/(R*T)."""
        K = 2 * self.L / (self.Rload * self.T)
        return self.Vin * 2.0 / (1.0 + math.sqrt(1.0 + 4.0 * K / self.D ** 2))

    def i_L_rms_analytical(self) -> float:
        """RMS of a DC level with a triangular ripple: sqrt(Idc^2 + dI^2/12).

        Valid whenever conduction is continuous, including the reverse-current
        (FCCM) case where Idc < dI/2.
        """
        if not self.is_ccm():
            return float("nan")
        idc = self.V_out_ideal() / self.Rload
        return math.sqrt(idc ** 2 + self.di_L() ** 2 / 12.0)

    def v_ripple_analytical(self) -> float:
        """Output ripple, computed exactly from the ideal triangular ripple current.

        The textbook shortcut  dV = dI/(8*fs*C) + dI*ESR  ADDS the two peak-to-peak
        terms, which is wrong: the capacitive term is the integral of the AC
        current (parabolic, peaking at its zero crossings) while the ESR term is
        in phase with the current (peaking at its extremes). They are a quarter
        cycle apart, so summing the peaks overestimates -- by 15.6 % for the
        default design, which the framework flagged as a 15.6 % "simulation
        error" until this was corrected.

        So construct v_C(t) + ESR*i_AC(t) on the ideal waveform and take its true
        peak-to-peak. Still fully independent of the simulator.
        """
        import numpy as np
        D, T, Cc, esr = self.D, self.T, self.Cout, self.Resr
        if D <= 0 or D >= 1:
            return float("nan")
        dI = self.di_L()
        N = 20000
        t = np.linspace(0.0, T, N, endpoint=False)
        rising = t < D * T
        i = np.empty_like(t)
        i[rising] = -dI / 2 + dI * (t[rising] / (D * T))
        i[~rising] = dI / 2 - dI * ((t[~rising] - D * T) / ((1 - D) * T))
        i -= i.mean()                       # AC component only
        v_c = np.cumsum(i) * (T / N) / Cc   # capacitor charge term
        v_c -= v_c.mean()
        v = v_c + esr * i
        return float(v.max() - v.min())

    def v_ripple_textbook(self) -> float:
        """The dI/(8 fs C) + dI*ESR shortcut, kept for comparison in reports."""
        di = self.di_L()
        return di / (8.0 * self.fs * self.Cout) + di * self.Resr

    def analytical(self) -> dict:
        d = {"V_out": self.V_out_ideal(), "I_L_ripple": self.di_L()}
        if self.is_ccm():
            d["I_L_rms"] = self.i_L_rms_analytical()
            d["V_ripple"] = self.v_ripple_analytical()
        return d

    def design_notes(self):
        return [
            f"D = {self.D:g}, fs = {fmt_eng(self.fs,'Hz')}, T = {fmt_eng(self.T,'s')}",
            f"mode = {'CCM' if self.is_ccm() else 'DCM'}"
            f"{' (forced, synchronous)' if self.synchronous else ' (diode)'}"
            f"{', inductor current REVERSES' if self.current_reverses() else ''} "
            f"-- I_load {self.i_load_ideal():.3f} A vs dI/2 {self.di_L()/2:.3f} A",
            f"settle {self.settle_periods()} periods (LC envelope tau = "
            f"{fmt_eng(self.lc_decay_tau(),'s')}, Rload*Cout = "
            f"{fmt_eng(self.Rload*self.Cout,'s')})",
            f"Vout = {self.V_out_ideal():.4f} V "
            f"(ideal D*Vin = {self.Vin*self.D:.4f} V, "
            f"less the RL+Ron divider)",
            f"dI_L = (Vin-Vo)*D*T/L = {self.di_L():.4f} A pk-pk",
            f"dV_out (exact, v_C + ESR*i in quadrature) = "
            f"{fmt_eng(self.v_ripple_analytical(),'V')}"
            f"  [textbook sum-of-peaks would say "
            f"{fmt_eng(self.v_ripple_textbook(),'V')}]",
        ]

    # ---------------- netlist ---------------- #
    def params(self) -> dict:
        return {"Vin": self.Vin, "D": self.D, "fs": self.fs, "L": self.L,
                "Cout": self.Cout, "Rload": self.Rload, "Ron": self.Ron,
                "Roff": self.Roff, "Resr": self.Resr, "RL": self.RL,
                "tr": self.tr, "Nsettle": self.settle_periods(),
                "Nmeas": self.Nmeas,
                "tmax_div": self.tmax_div}

    def netlist(self) -> str:
        T = self.T
        ton = self.D * T - self.tr
        ns = self.settle_periods()
        tm = ns * T
        tstop = (ns + self.Nmeas) * T
        tmax = T / self.tmax_div
        # Seed the state at the START OF THE ON-TIME, not at the cycle mean.
        # t=0 is a high-side turn-on, where the inductor current is at its
        # MINIMUM (Idc - dI/2).  Seeding the mean leaves a dI/2 mismatch that
        # kicks a high-Q LC (zeta ~ 0.18 here) and rings for milliseconds:
        # with the mean, 120 settle periods still gave a 20 % ripple error and
        # Pout > Pin; with this, 40 periods is already correct.
        vo = self.V_out_ideal()
        il = vo / self.Rload - self.di_L() / 2.0
        return f"""* Synchronous buck -- generated by pwrlab
Vin in 0 {self.Vin:g}
Vg g 0 PULSE(-1 1 0 {self.tr:g} {self.tr:g} {ton:.12g} {T:.12g})
S_H in sw g 0 SWM
S_L sw 0 0 g SWM
L1 sw out {self.L:g} Rser={self.RL:g} Rpar=1G
C1 out 0 {self.Cout:g} Rser={self.Resr:g}
Rload out 0 {self.Rload:g}
.model SWM SW(Ron={self.Ron:g} Roff={self.Roff:g} Vt=0.5 Vh=0)
.ic V(out)={vo:.12g}
.ic I(L1)={il:.12g}
.tran 0 {tstop:.12g} 0 {tmax:.12g}
.save V(out) V(sw) I(L1) I(Vin)
.meas TRAN Vout AVG V(out) FROM={tm:.12g} TO={tstop:.12g}
.meas TRAN Vripple_max MAX V(out) FROM={tm:.12g} TO={tstop:.12g}
.meas TRAN Vripple_min MIN V(out) FROM={tm:.12g} TO={tstop:.12g}
.meas TRAN ILavg AVG I(L1) FROM={tm:.12g} TO={tstop:.12g}
.meas TRAN ILrms RMS I(L1) FROM={tm:.12g} TO={tstop:.12g}
.meas TRAN ILmax MAX I(L1) FROM={tm:.12g} TO={tstop:.12g}
.meas TRAN ILmin MIN I(L1) FROM={tm:.12g} TO={tstop:.12g}
.meas TRAN Iin AVG I(Vin) FROM={tm:.12g} TO={tstop:.12g}
.meas TRAN Pin AVG {-self.Vin:g}*I(Vin) FROM={tm:.12g} TO={tstop:.12g}
.meas TRAN Pout AVG V(out)*V(out)/{self.Rload:g} FROM={tm:.12g} TO={tstop:.12g}
.end
"""

    def switch_blocking_voltages(self) -> dict:
        return {"S_H": self.Vin, "S_L": self.Vin}

    def output_node(self):
        return "out"

    def load_resistance(self):
        return self.Rload

    # ---------------- extraction ---------------- #
    def extract(self, res, waves) -> dict:
        m = res.meas
        out = {"V_out": m.get("Vout"), "I_L_dc": m.get("ILavg"),
               "I_L_rms": m.get("ILrms"), "P_in": m.get("Pin"),
               "P_out": m.get("Pout")}
        if m.get("ILmin") is not None:
            out["I_L_min"] = m["ILmin"]
        # Ripple must be measured WITHIN a cycle. (max-min) over the whole
        # measurement window also picks up settling drift -- see cycle_ripple.
        t_end = self.measure_window()[1]
        if waves is not None and waves.has("V(out)"):
            out["V_ripple"] = waves.cycle_ripple(waves["V(out)"], t_end, self.T)
        if waves is not None and waves.has("I(L1)"):
            out["I_L_ripple"] = waves.cycle_ripple(waves["I(L1)"], t_end, self.T)
        if out.get("I_L_ripple") is None and m.get("ILmax") is not None \
                and m.get("ILmin") is not None:
            out["I_L_ripple"] = m["ILmax"] - m["ILmin"]
        if out["P_in"]:
            out["efficiency_pct"] = out["P_out"] / out["P_in"] * 100.0
        return out

    def topology_checks(self, ex, waves, res):
        cs = [C.power_balance(ex.get("P_in"), ex.get("P_out"), tol_pct=10.0)]
        if waves is not None and waves.has("V(out)"):
            t0, t1 = self.measure_window()
            cm = waves.cycle_means(waves["V(out)"], t1, self.T,
                                   min(self.Nmeas, 10))
            cs.append(C.steady_state(cm, tol_frac=0.002, ref=ex.get("V_out")))
        # the analytical model branches on CCM/DCM, so verify the simulation
        # actually ran in the mode the model assumed
        imin = ex.get("I_L_min")
        if imin is not None:
            # continuous conduction means the current never sits at zero; for a
            # synchronous stage it may legitimately go negative
            sim_ccm = (imin > 1e-6) or (self.synchronous and imin < -1e-6)
            cs.append(C.Check(
                f"conduction mode is {'CCM' if self.is_ccm() else 'DCM'} as modelled",
                sim_ccm == self.is_ccm(), C.ERROR,
                f"min I(L1) = {imin:.4g} A -> simulation is "
                f"{'CCM' if sim_ccm else 'DCM'}"
                f"{' with reverse current' if imin < -1e-6 else ''}"))
            if self.current_reverses():
                cs.append(C.Check("reverse inductor current (expected, FCCM)",
                                  imin < 0, C.INFO,
                                  f"min I(L1) = {imin:.4g} A"))
        return cs
