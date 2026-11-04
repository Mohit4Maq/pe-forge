"""Totem-pole bridgeless PFC with hysteresis current control.

Current-shaping needs a controller, so this is the first closed-loop topology
here.  Hysteresis control is used deliberately: the inductor current is forced to
track its reference within a known band, which keeps the converter's behaviour
ANALYTICALLY PREDICTABLE.  An average-current-mode loop would add controller
dynamics that are not separable from the power stage when checking accuracy.

The control law is uniform across both line half-cycles, which is what makes
totem-pole attractive: with the inductor between the AC line and the fast leg
midpoint, closing the LOW-side switch always drives the current more positive and
closing the HIGH-side one always drives it more negative -- in both polarities.

Exactly known:

    output voltage        Vout = sqrt(P * Rload),  P = Vac_rms * Iac_rms
    2nd-harmonic ripple   dVpp = P / (w1 * Cout * Vout)
        because p(t) = P(1 - cos 2wt); the AC half must flow through Cout, so
        v_ripple = -P sin(2wt) / (2 w C Vout), i.e. P/(w C Vout) peak to peak.
        This is the term that forces the bulk capacitor in ANY single-phase PFC.
    power factor          -> 1, limited only by the hysteresis band and the
                             inductor's inability to track near the zero crossing
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .. import checks as C
from ..harmonics import Spectrum, power_metrics, resample
from .base import Topology, fmt_eng


@dataclass
class TotemPolePFC(Topology):
    """Totem-pole bridgeless PFC, hysteresis current control, resistive load."""

    Vac_rms: float = 230.0
    f1: float = 50.0
    Pout: float = 1000.0       # target throughput, sets the current reference
    Vout_nom: float = 400.0    # used to pick Rload when Rload is None
    Rload: float | None = None
    L: float = 1e-3
    Cout: float = 1e-3
    hyst: float = 0.5          # +/- band on the inductor current [A]
    Ron: float = 1e-3
    Roff: float = 1e6
    n_settle: int = 2
    n_meas: int = 2
    tmax_div: int = 400        # max step = T1 / (mf_equiv * tmax_div) proxy

    convergence_quantity = "V_out"
    convergence_tol_pct = 0.5

    measurements = []
    tolerances = {"V_out": 2.0, "V_ripple_2f": 8.0, "P_out": 3.0,
                  "I_ac_rms": 2.0}

    # A hysteresis band wider than the peak current means the comparator never
    # commutates in a controlled way; n_settle/n_meas below 1 cannot contain a
    # line cycle, so no line-frequency metric is defined.
    bounds = {"n_settle": (0, None), "n_meas": (1, None), "f1": (1.0, 1000.0)}

    def validate_extra(self):
        if self.hyst >= self.I_pk():
            raise ValueError(
                f"hysteresis band {self.hyst:g} A exceeds the peak line "
                f"current {self.I_pk():.3f} A: the comparator cannot shape a "
                f"current it never crosses. Scale hyst with Pout -- "
                f"hyst <= 0.08 * Pout / Vac_rms holds PF >= 0.99.")
        if self.V_out_analytical() <= self.Vpk:
            raise ValueError(
                f"Vout = sqrt(P*R) = {self.V_out_analytical():.1f} V does not "
                f"exceed the AC peak {self.Vpk:.1f} V. A boost stage cannot "
                f"buck: raise Rload or Pout, or lower Vac_rms.")

    # ---------------- analytical ---------------- #
    @property
    def T1(self):
        return 1.0 / self.f1

    @property
    def w1(self):
        return 2 * math.pi * self.f1

    @property
    def Vpk(self):
        return self.Vac_rms * math.sqrt(2.0)

    def R(self):
        return self.Rload if self.Rload is not None \
            else self.Vout_nom ** 2 / self.Pout

    def I_pk(self):
        """Peak line current for the requested throughput (unity PF)."""
        return 2.0 * self.Pout / self.Vpk

    def I_ac_rms(self):
        return self.I_pk() / math.sqrt(2.0)

    def V_out_analytical(self):
        return math.sqrt(self.Pout * self.R())

    def V_ripple_analytical(self):
        v = self.V_out_analytical()
        return self.Pout / (self.w1 * self.Cout * v)

    def analytical(self) -> dict:
        return {"V_out": self.V_out_analytical(),
                "V_ripple_2f": self.V_ripple_analytical(),
                "P_out": self.Pout,
                "I_ac_rms": self.I_ac_rms()}

    def design_notes(self):
        return [
            f"Vac = {self.Vac_rms:g} V rms ({self.Vpk:.1f} V peak), "
            f"f1 = {self.f1:g} Hz, P = {self.Pout:g} W",
            f"line current: {self.I_pk():.3f} A peak, {self.I_ac_rms():.3f} A rms",
            f"Rload = {self.R():.2f} ohm -> Vout = sqrt(P*R) = "
            f"{self.V_out_analytical():.2f} V  (must exceed {self.Vpk:.1f} V to boost)",
            f"2f ripple = P/(w1*Cout*Vout) = {self.V_ripple_analytical():.3f} V pk-pk",
            f"hysteresis band +/-{self.hyst:g} A on L = {fmt_eng(self.L,'H')}"
            f"  -> ~{self.V_out_analytical()/(4*self.L*self.hyst)/1e3:.0f} kHz "
            f"peak switching",
        ]

    def params(self) -> dict:
        return {"Vac_rms": self.Vac_rms, "f1": self.f1, "Pout": self.Pout,
                "Rload": self.R(), "L": self.L, "Cout": self.Cout,
                "hyst": self.hyst, "Ron": self.Ron, "Roff": self.Roff,
                "n_settle": self.n_settle, "n_meas": self.n_meas,
                "tmax_div": self.tmax_div}

    def measure_window(self):
        return (self.n_settle * self.T1,
                (self.n_settle + self.n_meas) * self.T1)

    def netlist(self) -> str:
        tstop = (self.n_settle + self.n_meas) * self.T1
        tmax = self.T1 / self.tmax_div / 20.0
        vo = self.V_out_analytical()
        return f"""* Totem-pole bridgeless PFC, hysteresis current control -- pwrlab
Vac ac1 ac2 SINE(0 {self.Vpk:g} {self.f1:g})
* polarity reference for the line-frequency (slow) leg
Vsref sref 0 SINE(0 1 {self.f1:g})
* current reference, in phase with the line -> unity displacement factor
Viref iref 0 SINE(0 {self.I_pk():g} {self.f1:g})
Lb ac1 sw {self.L:g} Rser=0 Rpar=1G
* hysteretic comparator on the current error, built from a switch with Vh:
* closing the LOW side always drives i_L positive, in BOTH half cycles
Berr err 0 V=I(Lb)-V(iref)
Vpull pull 0 1
Rpull pull gate 1k
S_cmp gate 0 err 0 CMP
Bgl gl 0 V=if(V(gate)>0.5,1,-1)
* fast leg
S_FL sw 0 gl 0 SWM
S_FH vo sw 0 gl SWM
* slow leg: ac2 to ground on the positive half, to the rail on the negative
S_SN ac2 0 sref 0 SWM
S_SP vo ac2 0 sref SWM
Cbus vo 0 {self.Cout:g} Rser=1m
Rload vo 0 {self.R():g}
.model SWM SW(Ron={self.Ron:g} Roff={self.Roff:g} Vt=0 Vh=0)
.model CMP SW(Ron=1 Roff=1meg Vt=0 Vh={self.hyst:g})
.ic V(vo)={vo:.12g}
.ic I(Lb)=0
.tran 0 {tstop:.12g} 0 {tmax:.12g}
.save V(vo) V(ac1) V(ac2) I(Lb) I(Vac) I(Rload)
.end
"""

    def output_node(self):
        return "vo"

    def load_resistance(self):
        return self.R()

    def switch_blocking_voltages(self) -> dict:
        v = self.V_out_analytical()
        return {"S_FL": v, "S_FH": v, "S_SN": v, "S_SP": v}

    # ---------------- extraction ---------------- #
    def extract(self, res, waves) -> dict:
        if waves is None:
            return {}
        t_end = self.measure_window()[1]
        t0 = t_end - self.n_meas * self.T1
        vac = waves["V(ac1)"] - waves["V(ac2)"]
        iac = waves["I(Lb)"]
        _, vo = resample(waves.t, waves["V(vo)"], t0, t_end, 1 << 16)
        pm = power_metrics(waves.t, vac, iac, self.f1, t_end, self.n_meas)
        si = Spectrum(waves.t, iac, self.f1, t_end, self.n_meas)
        sv = Spectrum(waves.t, waves["V(vo)"], self.f1, t_end, self.n_meas)
        out = {"V_out": float(np.mean(vo)),
               "V_ripple_2f": 2.0 * sv.h(2),   # peak-to-peak of the 2f component
               "P_in": pm["P"], "I_ac_rms": pm["I_rms"],
               "PF": pm["PF"], "DPF": pm["DPF"],
               "thd_i_pct": si.thd() * 100.0,
               "I_1": si.h(1)}
        out["P_out"] = float(np.mean(vo ** 2)) / self.R()
        if out["P_in"]:
            out["efficiency_pct"] = out["P_out"] / out["P_in"] * 100.0
        return out

    def cycle_means(self, waves, signal="V(vo)"):
        """Mean of `signal` over each line cycle in the measurement window."""
        if waves is None or self.n_meas < 2:
            return []
        t_end = self.measure_window()[1]
        out = []
        for k in range(self.n_meas):
            a = t_end - (self.n_meas - k) * self.T1
            _, seg = resample(waves.t, waves[signal], a, a + self.T1, 1 << 12)
            out.append(float(np.mean(seg)))
        return out

    def topology_checks(self, ex, waves, res):
        cs = [C.power_balance(ex.get("P_in"), ex.get("P_out"), tol_pct=8.0)]
        # The netlist seeds .ic V(vo) at the ANALYTICAL output voltage, so the
        # bus starts where theory says it ends. That is a deliberate shortcut to
        # avoid simulating a long charge-up -- and it means a wrong closed form
        # would place the bus at a wrong value that nothing else would notice,
        # because every comparison here is against that same closed form. This
        # check is the independent one: whatever the seed, the bus must not
        # still be MOVING across the measurement window.
        means = self.cycle_means(waves)
        if means:
            cs.append(C.steady_state(means, tol_frac=0.002,
                                     ref=ex.get("V_out")))
        else:
            # Never let a check SILENTLY not run. A single measured cycle gives
            # nothing to compare against, so steady state is unassessed -- and
            # an unassessed property must be reported as unassessed, not left
            # out of the list where its absence reads as "fine".
            cs.append(C.Check(
                "steady state reached", False, C.WARN,
                f"NOT ASSESSED: n_meas={self.n_meas} gives one cycle; at "
                f"least 2 are needed to measure drift. Raise n_meas."))
        cs.append(C.in_range("power factor", ex.get("PF"), lo=0.98,
                             severity=C.WARN))
        # IEC 61000-3-2 territory: a PFC that does not get under ~10 % THD is not
        # doing its job, whatever its power factor looks like
        cs.append(C.in_range("line current THD [%]", ex.get("thd_i_pct"),
                             hi=10.0, severity=C.WARN, unit=" %"))
        # boost converters cannot buck: the output must stay above the AC peak,
        # or the shaping stage loses control near the crest
        cs.append(C.in_range("output above AC peak (boost feasible)",
                             ex.get("V_out"), lo=self.Vpk, unit=" V"))
        return cs
