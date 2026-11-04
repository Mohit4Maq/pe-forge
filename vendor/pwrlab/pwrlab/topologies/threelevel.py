"""Three-level inverter legs: T-type and NPC, phase-disposition SPWM.

Both topologies realise the SAME three-level leg -- output connectable to +Vdc/2,
the neutral point, or -Vdc/2 -- by different switch arrangements:

    NPC     four series switches with two diodes clamping the inner nodes to NP
    T-type  two switches across the bus plus a bidirectional pair to NP

So they share one analytical model, and a correctly built pair MUST agree with
each other as well as with theory.  That mutual agreement is a check neither
could pass alone if its leg were miswired, and it is asserted in
tests/test_threelevel.py.

Exactly known for a three-level leg driven by PD-PWM, referenced to NP:

    fundamental peak   V1   = M * Vdc/2
    RMS of v_aN             = (Vdc/2) * sqrt(2M/pi)
    fundamental current I1  = V1 / |R + j*w1*L|

The neutral point is formed by two bus capacitors, not by ideal sources, so the
NP can genuinely drift -- and `neutral_point` checks that it does not.  Using two
stiff sources would hide exactly the failure mode a three-level design has to be
verified against.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .. import checks as C
from ..harmonics import Spectrum, power_metrics, resample
from .base import Topology, fmt_eng


@dataclass
class ThreeLevelLeg(Topology):
    """Common model, modulation and checks for a three-level leg."""

    Vdc: float = 800.0         # total bus voltage (+/- Vdc/2 about the NP)
    M: float = 0.8
    f1: float = 50.0
    mf: int = 200
    Rload: float = 10.0
    Lload: float = 10e-3
    Cdc: float | None = None   # each half-bus cap; None -> sized for np_ripple_frac
    np_ripple_frac: float = 0.02   # target NP ripple as a fraction of Vdc/2
    Ron: float = 1e-3
    Roff: float = 1e6
    n_settle: int = 4          # NP imbalance decays ~0.74x per line cycle
    n_meas: int = 2
    tmax_div: int = 200
    stiff_np: bool = False     # True -> ideal sources, hides NP drift
    L_dc: float = 10e-3        # DC feed inductance; see the note in _preamble
    # --- realism switches. Defaults reproduce the idealised model that the
    # --- accuracy suite validates; turn them on for literature comparison.
    modulation: str = "spwm"   # spwm | thipwm | svpwm | dpwmmax | dpwmmin
    deadtime: float = 0.0      # s. Real IGBT legs use 1-3 us, SiC 0.2-0.5 us.
    Vf_diode: float = 0.0      # freewheel diode forward drop [V]
    Rd_diode: float = 1e-3     # diode slope resistance [ohm]
    R_balance: float = 100e3   # DC-link balancing resistors, as real hardware
    # has: with only capacitors the midpoint has no DC reference at all

    convergence_quantity = "V_1"
    convergence_tol_pct = 0.5

    measurements = []
    tolerances = {"V_1": 1.0, "V_rms": 1.0, "I_1": 1.5, "P": 2.0, "DPF": 1.0,
                  "I_cap_rms": 2.0,   # ideal-switch pulse-train model vs a
                  # simulation with finite Ron and cap ESR; agrees to <0.5 %

                  "np_ripple_V": 10.0}   # NP ripple model is good to ~1 %,
    # but the measured value still carries the tail of the startup transient

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

    def _dt_error(self) -> dict:
        """Cached dead-time waveform analysis; it builds ~50k-sample arrays."""
        key = (self.deadtime, self.M, self.mf, self.Vdc, self.f1,
               self.Rload, self.Lload)
        if getattr(self, "_dt_cache_key", None) != key:
            self._dt_cache_key = key
            self._dt_cache = self.deadtime_voltage_error()
        return self._dt_cache

    def V_1_peak(self):
        """IDEAL fundamental, before dead time. See V_1_effective()."""
        return self.M * self.Vdc / 2.0

    # -- what the converter actually delivers -------------------------------- #
    # These exist because `analytical()` used to predict the IDEAL fundamental
    # regardless of dead time, while the netlist simulated the real one. At
    # deadtime = 2 us the two disagreed by 9.2 % on V_1 and 17.5 % on P, so
    # every non-zero dead-time run came back usable=false -- including the runs
    # whose THD figures were quoted in the assumption sheet. The machinery to
    # predict the loss (deadtime_voltage_error) already existed and simply was
    # not connected to analytical(). thd_i_analytical() had been subtracting it
    # correctly all along, which is what showed the intent.
    def V_1_effective(self):
        return self.V_1_peak() - self._dt_error()["dV1"]

    def I_1_effective(self):
        return self.V_1_effective() / abs(self.Z())

    def V_rms_effective(self):
        d = self._dt_error()
        if self.deadtime <= 0:
            return self.V_rms_analytical()
        # scale the closed-form RMS by how much the constructed dead-time
        # waveform differs from the constructed ideal one, so the correction
        # rides on top of the validated ideal expression rather than replacing it
        ideal = d.get("v_rms_ideal") or 0.0
        return (self.V_rms_analytical() * d["v_rms"] / ideal) if ideal \
            else self.V_rms_analytical()

    def I_1_peak(self):
        return self.V_1_peak() / abs(self.Z())

    def phi(self):
        return math.atan2(self.w1 * self.Lload, self.Rload)

    def P_analytical(self):
        return 0.5 * self.V_1_peak() * self.I_1_peak() * math.cos(self.phi())

    def P_effective(self):
        return 0.5 * self.V_1_effective() * self.I_1_effective() * math.cos(self.phi())

    # ---- neutral point ---------------------------------------------------- #
    def _modulation_signals(self, t):
        """The actual per-phase references, INCLUDING zero-sequence injection.

        Every closed-form model here (RMS voltage, neutral-point charge, rail
        current) integrates a function of the reference. Hard-coding a pure sine
        makes them silently wrong the moment injection is used -- which is how
        the checks caught this: with SVPWM at M=1.1 they reported +3.2 % on
        V_rms and -58 % on NP ripple, because injection genuinely reshapes the
        duty distribution (and, in the NP case, genuinely improves it).
        """
        w = self.w1
        raw = [self.M * np.sin(w * t + th) for th in self._phases()]
        mode = self.modulation.lower()
        if mode == "spwm" or len(raw) == 1:
            z = np.zeros_like(t)
        elif mode == "thipwm":
            z = self.M / 6.0 * np.sin(3.0 * w * t)
        elif mode == "svpwm":
            stack = np.vstack(raw)
            z = -(stack.max(axis=0) + stack.min(axis=0)) / 2.0
        else:
            z = np.zeros_like(t)
        return [r + z for r in raw]

    def V_rms_analytical(self):
        """RMS of the leg-to-NP voltage.

        Within a carrier period the leg is at +/-Vdc/2 for a fraction |m| and at
        0 otherwise, so <v^2> = (Vdc/2)^2 * <|m|>. For a pure sine <|M sin|> =
        2M/pi, giving the familiar Vdc/2*sqrt(2M/pi); with injection the mean is
        integrated numerically from the real reference.
        """
        n = 200000
        t = np.linspace(0.0, self.T1, n, endpoint=False)
        m = self._modulation_signals(t)[0]
        return (self.Vdc / 2.0) * math.sqrt(float(np.mean(np.abs(m))))

    def _np_charge_ptp(self) -> float:
        """Peak-to-peak of the NP charge excursion, per farad.

        The bus capacitors supply the load only while the leg is in '+' or '-',
        whose combined duty is |m(t)|, so the neutral point sees

            i_np(t) = -i_load(t) * |m(t)|

        whose (DC-free) integral is the imbalance charge.  Integrating the ideal
        waveform rather than assuming a sinusoid keeps this independent of the
        simulator.  Verified against simulation at 20 mF and 50 mF to under 1 %.

        Single-phase three-level legs have a large low-frequency NP excursion --
        this is physics, not a modelling artefact, and is why they need either a
        big neutral capacitance or active balancing.  A three-phase leg pushes
        the same mechanism to 3*f1 with far smaller amplitude.
        """
        n = 200000
        t = np.linspace(0.0, self.T1, n, endpoint=False)
        w = self.w1
        i_load = self.I_1_effective() * np.sin(w * t - self.phi())
        i_np = -i_load * np.abs(self.M * np.sin(w * t))
        i_np -= i_np.mean()
        q = np.cumsum(i_np) * (self.T1 / n)
        q -= q.mean()
        return float(q.max() - q.min())

    def Cdc_for_np_ripple(self, frac=None) -> float:
        """Half-bus capacitance giving an NP ripple of `frac` * Vdc/2."""
        frac = self.np_ripple_frac if frac is None else frac
        return self._np_charge_ptp() / max(frac * self.Vdc / 2.0, 1e-12)

    def C_half(self) -> float:
        return self.Cdc if self.Cdc is not None else self.Cdc_for_np_ripple()

    def np_ripple_analytical(self) -> float:
        return self._np_charge_ptp() / self.C_half()

    # ---- DC-link capacitor ripple current --------------------------------- #
    def _phases(self):
        """(phase angle, ...) for the legs this topology drives."""
        return (0.0,)

    def _suffixes(self):
        """Instance-name suffixes for the legs ("" single phase, a/b/c three)."""
        return ("",)

    def max_linear_M(self) -> float:
        """Highest modulation index still in the linear range.

        Pure sinusoidal PWM clips above M = 1; any zero-sequence injection that
        centres the references buys 2/sqrt(3) = 1.155. Lives on the base class
        because the linear-range CHECK is shared -- putting it only on the
        three-phase subclass broke every single-phase run.

        A single-phase leg has no zero sequence to exploit, so it stays at 1.0.
        """
        if len(self._phases()) < 3:
            return 1.0
        return 1.0 if self.modulation.lower() == "spwm" else 2.0 / math.sqrt(3.0)

    def _rail_current_ideal(self, n_per_carrier=64):
        """Ideal switched current drawn from the POSITIVE rail, over one line
        cycle, built from the PD-PWM logic itself.

        This is the quantity a DC-link capacitor has to absorb: the rail current
        is a pulse train, and the capacitor carries all of its AC content. A
        local-average (duty x current) model captures only the low-frequency
        envelope and understates the true RMS, because the switching-frequency
        pulses are most of the mean square. So the pulse train is constructed
        explicitly -- still independent of SPICE, exact for ideal switches.

        Returns (t, i_rail_positive, i_rail_negative).
        """
        n = int(self.mf * n_per_carrier)
        t = np.linspace(0.0, self.T1, n, endpoint=False)
        w = self.w1
        # phase-disposition carriers: in phase, stacked 0..1 and -1..0
        frac = (t * self.fc) % 1.0
        tri = np.where(frac < 0.5, 4 * frac - 1, 3 - 4 * frac)   # -1..1 triangle
        carp = (tri + 1.0) / 2.0        # 0..1
        carn = (tri - 1.0) / 2.0        # -1..0
        ip = np.zeros(n)
        inn = np.zeros(n)
        # Dead time does TWO opposing things to the rail current and modelling
        # only one of them is worse than modelling neither. It lowers the load
        # current (via the lost fundamental), which reduces rail current; and it
        # forces conduction into a RAIL during the dead window -- because the
        # neutral path needs an active device -- which ADDS rail current where
        # the ideal pattern had none. Measured on ttype3ph at 2 us: using the
        # ideal current gave I_cap_rms -4.47 % against simulation, using only
        # the reduced current gave +4.71 %. The errors bracket zero because each
        # captures one half of the mechanism.
        nd = max(1, int(round(self.deadtime / (self.T1 / n)))) \
            if self.deadtime > 0 else 0

        def _gate(raw):
            """turn-on delayed by the dead time, turn-off immediate."""
            return raw if nd == 0 else (raw & np.roll(raw, nd))

        for th in self._phases():
            m = self.M * np.sin(w * t + th)
            i_k = self.I_1_effective() * np.sin(w * t + th - self.phi())
            if nd:
                raw_p = (m > carp).astype(np.int8)
                raw_n = (m < carn).astype(np.int8)
                raw_0 = ((~(m > carp)) & (~(m < carn))).astype(np.int8)
                g_p, g_n, g_0 = _gate(raw_p), _gate(raw_n), _gate(raw_0)
                # during dead time the current's own polarity picks the rail
                dead = (g_p + g_n + g_0) == 0
                on_p = (g_p == 1) | (dead & (i_k < 0))
                on_n = (g_n == 1) | (dead & (i_k > 0))
                ip += np.where(on_p, i_k, 0.0)
                inn += np.where(on_n, i_k, 0.0)
                continue
            ip += np.where(m > carp, i_k, 0.0)
            inn += np.where(m < carn, i_k, 0.0)
        return t, ip, inn

    def deadtime_voltage_error(self, n_per_carrier=256) -> dict:
        """Fundamental voltage lost to dead time, and the harmonics it creates.

        Built by constructing the ideal switched waveform WITH the dead-time
        logic, rather than from the textbook 2-level formula
        dV1 = (4/pi)*td*fc*Vdc, which under-predicts a three-level leg by ~1.46x.

        The reason: in a T-type the neutral-point path needs an ACTIVE device
        (the bidirectional pair). During dead time every device is off, so only
        the rail diodes D1/D4 can conduct and the output is pulled to a RAIL,
        not to the adjacent level. Measured directly: during the positive-current
        region the leg sits at 0 V for 87.5 % of dead-time samples and +400 V for
        12.5 %, never at the intermediate value the 2-level model assumes.
        """
        if self.deadtime <= 0:
            return {"dV1": 0.0, "harmonics": {}}
        # Resolve the dead time to at least 20 samples, or rounding it to the
        # nearest sample becomes the dominant error: at 256 samples/carrier a
        # 1 us dead time lands on 3 samples (1.17 us), a 17 % overestimate that
        # showed up directly in dV1.
        need = 20.0 / (self.fc * self.deadtime)
        n_per_carrier = max(n_per_carrier, need)
        n = int(self.mf * n_per_carrier)
        T1 = self.T1
        t = np.linspace(0.0, T1, n, endpoint=False)
        dt = T1 / n
        nd = max(1, int(round(self.deadtime / dt)))
        frac = (t * self.fc) % 1.0
        tri = np.where(frac < 0.5, 4 * frac - 1, 3 - 4 * frac)
        carp, carn = (tri + 1) / 2, (tri - 1) / 2
        m = self.M * np.sin(2 * np.pi * self.f1 * t)
        i_sign = np.sign(np.sin(2 * np.pi * self.f1 * t - self.phi()))
        raw_p = (m > carp).astype(np.int8)
        raw_n = (m < carn).astype(np.int8)
        raw_0 = ((~(m > carp)) & (~(m < carn))).astype(np.int8)

        def delayed_on(raw):
            """command AND the command delayed by nd samples -- turn-on delayed,
            turn-off immediate, exactly as the netlist does it."""
            return raw & np.roll(raw, nd)

        g_p, g_n, g_0 = delayed_on(raw_p), delayed_on(raw_n), delayed_on(raw_0)
        half = self.Vdc / 2.0
        ideal = np.where(raw_p == 1, half, np.where(raw_n == 1, -half, 0.0))
        # during dead time only the rail diodes conduct: the output goes to the
        # rail that the current polarity forces
        dead = (g_p + g_n + g_0) == 0
        actual = np.where(g_p == 1, half,
                          np.where(g_n == 1, -half,
                                   np.where(g_0 == 1, 0.0,
                                            np.where(i_sign > 0, -half, half))))
        err = actual - ideal
        spec = np.fft.rfft(err) / n
        amp = np.abs(spec) * 2.0
        return {"dV1": float(amp[1]),
                "harmonics": {k: float(amp[k]) for k in (1, 5, 7, 11, 13)
                              if k < len(amp)},
                "dead_fraction": float(dead.mean()),
                # RMS of the ACTUAL switched waveform. Dead time RAISES this
                # even as it lowers the fundamental, because the output is
                # pulled to a rail rather than to the adjacent level -- so the
                # waveform spends more time at +/-Vdc/2 and less at 0. Reporting
                # only the fundamental loss would miss that entirely.
                "v_rms": float(np.sqrt(np.mean(actual ** 2))),
                "v_rms_ideal": float(np.sqrt(np.mean(ideal ** 2)))}

    def thd_i_analytical(self) -> float:
        """Low-order current THD predicted from the dead-time voltage error."""
        d = self.deadtime_voltage_error()
        if not d["harmonics"]:
            return 0.0
        acc = 0.0
        for k, v in d["harmonics"].items():
            if k == 1 or k % 3 == 0:      # triplens cancel in a floating star
                continue
            acc += (v / abs(complex(self.Rload, k * self.w1 * self.Lload))) ** 2
        i1 = (self.V_1_peak() - d["dV1"]) / abs(self.Z())
        return math.sqrt(acc) / i1 if i1 else float("nan")

    def dc_link_irms_analytical(self, n_per_carrier=64) -> float:
        """RMS current the positive-rail DC-link capacitor must carry.

        Equal to the RMS of the AC component of the rail current: the DC source
        supplies the average, the capacitor absorbs everything else.
        """
        _, ip, _ = self._rail_current_ideal(n_per_carrier)
        return float(np.sqrt(np.mean((ip - ip.mean()) ** 2)))

    def dc_link_irms_split(self, n_per_carrier=64, f_split=None) -> dict:
        """Split the capacitor RMS current below/above a frequency.

        Capacitor ripple-current ratings are frequency dependent -- electrolytics
        are rated at 100/120 Hz and derated elsewhere, film caps the opposite --
        so a single RMS number is not enough to choose a part. Default split is
        half the carrier frequency.
        """
        f_split = f_split if f_split else self.fc / 2.0
        t, ip, _ = self._rail_current_ideal(n_per_carrier)
        ac = ip - ip.mean()
        n = len(ac)
        spec = np.fft.rfft(ac) / n
        freqs = np.fft.rfftfreq(n, d=self.T1 / n)
        amp2 = (np.abs(spec) * 2.0) ** 2 / 2.0          # per-bin mean square
        lo = float(np.sqrt(amp2[freqs < f_split].sum()))
        hi = float(np.sqrt(amp2[freqs >= f_split].sum()))
        return {"I_rms_total": float(np.sqrt(lo ** 2 + hi ** 2)),
                "I_rms_below_split": lo, "I_rms_above_split": hi,
                "f_split_Hz": f_split}

    def analytical(self) -> dict:
        d = {"V_1": self.V_1_effective(), "V_rms": self.V_rms_effective(),
             "I_1": self.I_1_effective(), "P": self.P_effective(),
             "DPF": math.cos(self.phi())}
        if not self.stiff_np:
            d["np_ripple_V"] = self.np_ripple_analytical()
            d["I_cap_rms"] = self.dc_link_irms_analytical()
        return d

    def design_notes(self):
        z = self.Z()
        return [
            f"{self.name}: three-level leg, PD-PWM, M = {self.M:g}, "
            f"mf = {self.mf} -> fc = {fmt_eng(self.fc,'Hz')}",
            f"bus {self.Vdc:g} V (+/-{self.Vdc/2:g} V about NP), "
            f"half-bus C = {fmt_eng(self.C_half(),'F')}"
            f"{' (auto-sized)' if self.Cdc is None else ''}"
            f"{' -- STIFF sources, NP dynamics suppressed' if self.stiff_np else ''}",
            f"predicted NP ripple = {self.np_ripple_analytical():.3f} V "
            f"({self.np_ripple_analytical()/(self.Vdc/2)*100:.2f} % of Vdc/2)",
            f"DC-link cap ripple current = "
            f"{self.dc_link_irms_analytical():.3f} A rms per capacitor"
            + ("" if self.L_dc > 0 else
               "  [L_dc=0: source shares the AC, so this is optimistic]"),
            f"load Z = {abs(z):.3f} ohm at {math.degrees(self.phi()):.2f} deg",
            f"V1 = M*Vdc/2 = {self.V_1_peak():.2f} V, "
            f"v_aN rms = {self.V_rms_analytical():.2f} V",
            f"I1 = {self.I_1_peak():.3f} A, P = {self.P_analytical():.2f} W",
        ]

    def params(self) -> dict:
        return {"Vdc": self.Vdc, "M": self.M, "f1": self.f1, "mf": self.mf,
                "Rload": self.Rload, "Lload": self.Lload,
                "Cdc_used": self.C_half(), "L_dc": self.L_dc,
                "deadtime": self.deadtime, "Vf_diode": self.Vf_diode,
                "modulation": self.modulation,
                "Ron": self.Ron, "Roff": self.Roff,
                "n_settle": self.n_settle,
                "n_meas": self.n_meas, "tmax_div": self.tmax_div,
                "stiff_np": int(self.stiff_np)}

    def measure_window(self):
        return (self.n_settle * self.T1,
                (self.n_settle + self.n_meas) * self.T1)

    # ---------------- shared netlist pieces ---------------- #
    def _gate_expr(self, raw: str) -> str:
        """Gate expression with dead time: turn-ON delayed, turn-OFF immediate.

        LTspice's delay() gives exactly this when ANDed with the raw command --
        the device may only conduct once the command has been true for td, but
        stops the instant it goes false. That is the textbook definition, and it
        is what creates the dead-time voltage error: during td the leg output is
        set by CURRENT POLARITY (it freewheels to a rail through D1 or D4), not
        by the modulator.
        """
        if self.deadtime <= 0:
            return raw
        return f"if(({raw})>0 & delay(({raw}),{self.deadtime:g})>0, 1, -1)"

    def _diode_models(self) -> str:
        if self.deadtime <= 0:
            return ""
        return (f".model DFW D(Ron={self.Rd_diode:g} Roff=1e9 "
                f"Vfwd={self.Vf_diode:g})\n")

    def _leg_diodes(self, p: str, mid: str | None = None) -> str:
        """Antiparallel diodes. Not optional once dead time exists: with every
        switch in the leg off, the load current has nowhere to go and LTspice
        forces it through Roff, producing a megavolt spike instead of the
        freewheeling that actually happens."""
        if self.deadtime <= 0:
            return ""
        # Orientation matters and is easy to get backwards. The NP switch is a
        # common-emitter pair with both emitters at mid:
        #   T2: a -> mid,  D2 antiparallel: mid -> a
        #   T3: 0 -> mid,  D3 antiparallel: mid -> 0
        # Writing D3 as 0 -> mid instead creates a free rectifier path
        # 0 -> mid -> a -> p_dc that drains the neutral point into the positive
        # rail: the bus collapses to 797 V / -2 V within one line cycle.
        # `p` names the OUTPUT node and suffixes the diode names; `mid` names
        # the neutral-switch midpoint. They are not always the same string: the
        # single-phase leg's output is "a" while its midpoint is "mid", not
        # "mida". Conflating them left D2a and D3a hanging on a node nothing
        # else touched -- an isolated diode pair, silently inert, so the
        # neutral-path freewheel was simply absent. The rail diodes carried the
        # dead-time behaviour and the results looked plausible.
        mid = f"mid{p}" if mid is None else mid
        return (f"D1{p} {p} p_dc DFW\n"          # antiparallel to S1
                f"D4{p} n_dc {p} DFW\n"          # antiparallel to S4
                f"D2{p} {mid} {p} DFW\n"         # antiparallel to S2
                f"D3{p} {mid} 0 DFW\n")          # antiparallel to S3

    def _leg_diodes_npc(self, p: str, out: str | None = None) -> str:
        """Antiparallel diodes for an NPC leg, whose nodes differ from a T-type.

        An NPC leg is four devices in SERIES between the rails
        (p_dc -> n1 -> a -> n2 -> n_dc), so each antiparallel diode spans a
        different pair of nodes than the T-type's. Reusing the T-type set here
        would connect diodes to n1/n2 nodes that do not exist by those names,
        which is how the NPC ended up with no freewheel path at all.
        """
        if self.deadtime <= 0:
            return ""
        # Same separation as _leg_diodes: `p` suffixes the NAMES, `out` is the
        # OUTPUT NODE. The single-phase leg's output is "a" with no suffix, so
        # defaulting one to the other emits a diode with a blank terminal.
        out = p if out is None else out
        return (f"Dfw1{p} n1{p} p_dc DFW\n"      # antiparallel to S1
                f"Dfw2{p} {out} n1{p} DFW\n"     # antiparallel to S2
                f"Dfw3{p} n2{p} {out} DFW\n"     # antiparallel to S3
                f"Dfw4{p} n_dc n2{p} DFW\n")     # antiparallel to S4

    def _preamble(self) -> str:
        """Bus, neutral point, PD carriers and the state comparators.

        Phase disposition: two in-phase triangular carriers, one spanning 0..+1
        and one -1..0.  ref above the upper carrier selects '+', below the lower
        selects '-', between them selects '0'.

            sp = sign(ref - carrier_upper)      sn = sign(ref - carrier_lower)
            state +  : sp=+1 sn=+1
            state 0  : sp=-1 sn=+1
            state -  : sp=-1 sn=-1
        """
        Tc = 1.0 / self.fc
        half = Tc / 2 - 1e-12
        v2 = self.Vdc / 2.0
        if self.stiff_np:
            bus = f"Vp p_dc 0 {v2:g}\nVn n_dc 0 {-v2:g}"
        else:
            c = self.C_half()
            # DC FEED INDUCTANCE. An ideal source placed straight across the link
            # is a short at every frequency, so it absorbs AC current that a real
            # DC link forces into its capacitors -- measured at 5.7 A RMS here,
            # which made the simulated capacitor current 10-20 % below the
            # sizing-relevant value. Real DC links are fed through a rectifier
            # and cabling, and the standard capacitor-sizing assumption is that
            # the source supplies DC only. L_dc enforces that: at 3*f1 its
            # impedance is already >10x the capacitors', so the capacitors carry
            # the AC as they do in hardware. Set L_dc=0 for a battery-stiff bus.
            idc = self.P_analytical() / self.Vdc if self.Vdc else 0.0
            if self.L_dc > 0:
                bus = (f"Vbus src n_dc {self.Vdc:g}\n"
                       f"Ldc src p_dc {self.L_dc:g} Rser=1m Rpar=1Meg\n"
                       f"Cp p_dc 0 {c:g} Rser=1m\n"
                       f"Cn 0 n_dc {c:g} Rser=1m\n"
                       f".ic I(Ldc)={idc:.12g}")
            else:
                bus = (f"Vbus p_dc n_dc {self.Vdc:g}\n"
                       f"Cp p_dc 0 {c:g} Rser=1m\n"
                       f"Cn 0 n_dc {c:g} Rser=1m\n"
                       f"Rbp p_dc 0 {self.R_balance:g}\n"
                       f"Rbn 0 n_dc {self.R_balance:g}")
        return f"""{bus}
Vcp carp 0 PULSE(0 1 0 {half:.12g} {half:.12g} 1p {Tc:.12g})
Vcn carn 0 PULSE(-1 0 0 {half:.12g} {half:.12g} 1p {Tc:.12g})
Vref ref 0 SINE(0 {self.M:g} {self.f1:g})
Bsp sp 0 V=if(V(ref)>V(carp),1,-1)
Bsn sn 0 V=if(V(ref)>V(carn),1,-1)
Bg1 g1 0 V={self._gate_expr("V(ref)-V(carp)")}
Bg4 g4 0 V={self._gate_expr("V(carn)-V(ref)")}
Bg0 g0 0 V={self._gate_expr("min(V(carp)-V(ref),V(ref)-V(carn))")}
"""

    def _tail(self) -> str:
        tstop = (self.n_settle + self.n_meas) * self.T1
        tmax = 1.0 / self.fc / self.tmax_div
        v2 = self.Vdc / 2.0
        ic = "" if self.stiff_np else \
            f".ic V(p_dc)={v2:.12g}\n.ic V(n_dc)={-v2:.12g}\n"
        return f"""Lo a m {self.Lload:g} Rser=0 Rpar=1G
Ro m 0 {self.Rload:g}
.model SWM SW(Ron={self.Ron:g} Roff={self.Roff:g} Vt=0 Vh=0)
.model DCL D(Ron=1m Roff=1e9 Vfwd=0)
{self._diode_models()}{ic}.ic I(Lo)=0
.tran 0 {tstop:.12g} 0 {tmax:.12g}
.save V(a) V(p_dc) V(n_dc) I(Lo) I(Vbus) I(Vp) I(Vn) I(Cp) I(Cn)
.end
"""

    def switch_blocking_voltages(self) -> dict:
        """Overridden per topology: T-type rails block the full bus, NPC does not."""
        v = self.Vdc / 2.0
        return {n: v for n in ("S1", "S2", "S3", "S4")}

    def switch_switching_voltages(self) -> dict:
        """One level per transition, so Vdc/2 everywhere in both topologies."""
        return {n: self.Vdc / 2.0 for n in self.switch_blocking_voltages()}

    def bidirectional_pairs(self) -> list:
        return [(f"S2{p}", f"S3{p}") for p in self._suffixes()]

    def tol_for(self, quantity) -> float:
        """Loosen the neutral-point and DC-link tolerances under injection.

        Both models integrate a local-duty approximation of the reference. That
        is accurate to well under 1 % for sinusoidal PWM, but injection reshapes
        the duty distribution and the approximation degrades -- measured 7-19 %
        on NP ripple and up to 18 % on capacitor current at M = 1.15. Reporting
        those against a 2 % band would be claiming an accuracy that is not
        there; the wider band is the honest statement of what is known.
        """
        base = super().tol_for(quantity)
        if self.modulation.lower() != "spwm" and quantity in (
                "np_ripple_V", "I_cap_rms"):
            return max(base, 25.0)
        return base

    def freewheel_diodes(self) -> list:
        """Present only when dead time is modelled -- _leg_diodes emits them."""
        if self.deadtime <= 0:
            return []
        return [f"D{i}{p}" for i in (1, 2, 3, 4) for p in self._suffixes()]

    # ---------------- extraction ---------------- #
    def extract(self, res, waves) -> dict:
        if waves is None:
            return {}
        t_end = self.measure_window()[1]
        va = waves["V(a)"]
        io = waves["I(Lo)"]
        sv = Spectrum(waves.t, va, self.f1, t_end, self.n_meas)
        si = Spectrum(waves.t, io, self.f1, t_end, self.n_meas)
        pm = power_metrics(waves.t, va, io, self.f1, t_end, self.n_meas)
        out = {"V_1": sv.h(1), "V_rms": sv.rms(), "V_dc_offset": sv.dc,
               "I_1": si.h(1), "I_rms": si.rms(), "P": pm["P"],
               "PF": pm["PF"], "DPF": pm["DPF"],
               "thd_i_pct": si.thd() * 100.0,
               "thd_v_total_pct": sv.thd_total() * 100.0}
        # neutral point: the imbalance is V(p_dc) + V(n_dc), zero when centred
        if waves.has("V(p_dc)") and waves.has("V(n_dc)"):
            t0 = t_end - self.n_meas * self.T1
            _, vp = resample(waves.t, waves["V(p_dc)"], t0, t_end, 1 << 15)
            _, vn = resample(waves.t, waves["V(n_dc)"], t0, t_end, 1 << 15)
            imb = vp + vn
            out["np_offset_V"] = float(np.mean(imb))
            out["np_ripple_V"] = float(imb.max() - imb.min())
        out.update(self._cap_metrics(waves, t_end))
        return out

    def _cap_metrics(self, waves, t_end) -> dict:
        """RMS ripple current in each DC-link capacitor, measured from the raw."""
        out = {}
        t0 = t_end - self.n_meas * self.T1
        for key, label in (("I(Cp)", "I_cap_rms"), ("I(Cn)", "I_cap_n_rms")):
            if waves.has(key):
                _, ic = resample(waves.t, waves[key], t0, t_end, 1 << 17)
                out[label] = float(np.sqrt(np.mean((ic - ic.mean()) ** 2)))
                out[label.replace("rms", "peak")] = float(np.max(np.abs(ic)))
        return out

    def topology_checks(self, ex, waves, res):
        cs = []
        if ex.get("V_dc_offset") is not None:
            cs.append(C.dc_offset(ex["V_dc_offset"], self.Vdc / 2,
                                  tol_frac=0.01))
        cs.append(C.in_range(
            f"modulation index within the linear range for {self.modulation}",
            self.M, hi=self.max_linear_M() + 1e-9, severity=C.WARN))
        # three-level output must actually use three levels: if the middle state
        # is never selected the leg has silently degenerated to two-level
        if waves is not None:
            va = waves["V(a)"]
            t0, t1 = self.measure_window()
            m = waves.window(t0, t1)
            near_zero = np.mean(np.abs(va[m]) < 0.05 * self.Vdc / 2)
            cs.append(C.Check("leg uses the neutral-point (zero) state",
                              near_zero > 0.05, C.ERROR,
                              f"{near_zero*100:.1f} % of samples at the NP level "
                              f"(a two-level leg would show ~0 %)"))
            peak = float(np.max(np.abs(va[m])))
            cs.append(C.in_range("output never exceeds half the bus", peak,
                                 hi=self.Vdc / 2 * 1.05, unit=" V"))
        if ex.get("I_cap_rms") is not None and self.L_dc <= 0:
            cs.append(C.Check(
                "DC-link capacitor current is sizing-relevant", False, C.WARN,
                "L_dc=0, so the ideal source absorbs part of the AC current and "
                "I_cap_rms understates what a real capacitor carries"))
        if ex.get("np_offset_V") is not None:
            cs.append(C.Check(
                "neutral point stays centred",
                abs(ex["np_offset_V"]) < 0.02 * self.Vdc / 2, C.WARN,
                f"mean imbalance {ex['np_offset_V']:+.4g} V, ripple "
                f"{ex.get('np_ripple_V', float('nan')):.4g} V "
                f"(limit {0.02*self.Vdc/2:.3g} V)"))
        return cs


@dataclass
class TTypeInverter(ThreeLevelLeg):
    """T-type three-level leg: two bus switches plus a bidirectional NP pair."""

    def _suffixes(self):
        return ("",)

    def switch_blocking_voltages(self) -> dict:
        """T-type: the RAIL switches block the FULL bus.

        With S4 conducting, the output sits at -Vdc/2 while S1's other terminal
        is at +Vdc/2, so S1 stands off the entire link -- and vice versa. Only
        the neutral-point pair is limited to Vdc/2. This is why a T-type needs
        full-voltage parts on the rails, which carry more switching energy, and
        it is the reason NPC overtakes T-type above roughly 10 kHz.
        """
        full, half = self.Vdc, self.Vdc / 2.0
        return {**{f"S1{p}": full for p in self._suffixes()},
                **{f"S4{p}": full for p in self._suffixes()},
                **{f"S2{p}": half for p in self._suffixes()},
                **{f"S3{p}": half for p in self._suffixes()}}

    def netlist(self) -> str:
        return f"""* T-type three-level leg -- generated by pwrlab
{self._preamble()}
* S1 conducts in state '+', S4 in state '-'
S1 p_dc a g1 0 SWM
S4 a n_dc g4 0 SWM
* bidirectional pair to the neutral point, on only in state '0'
* (sn > 0 and sp < 0); two devices in series as in the real converter
S2 a mid g0 0 SWM
S3 mid 0 g0 0 SWM
{self._leg_diodes("a", mid="mid")}
{self._tail()}"""


@dataclass
class NPCInverter(ThreeLevelLeg):
    """Neutral-point-clamped leg: four series switches, two clamping diodes."""

    def _suffixes(self):
        return ("",)

    def bidirectional_pairs(self):
        """None. NPC's inner devices sit in separate conduction paths clamped by
        D1/D2; they are not one bidirectional switch."""
        return []

    def clamping_diodes(self) -> list:
        return ["Dcl1", "Dcl2"]

    def netlist(self) -> str:
        return f"""* NPC three-level leg -- generated by pwrlab
{self._preamble()}
* S1 on in '+', S2 in '+'/'0', S3 in '0'/'-', S4 in '-'
S1 p_dc n1 g1 0 SWM
S2 n1 a Bnpc_s2 0 SWM
S3 a n2 Bnpc_s3 0 SWM
S4 n2 n_dc g4 0 SWM
Bnpc_s2 Bnpc_s2 0 V=max(V(g1),V(g0))
Bnpc_s3 Bnpc_s3 0 V=max(V(g4),V(g0))
* clamping diodes tie the inner nodes to the neutral point during state '0'
* NOTE the Dcl prefix: _leg_diodes() emits the antiparallel freewheel diodes as
* D1..D4, so naming the clamps D1/D2 collided with them the moment dead time
* turned the freewheel diodes on. LTspice rejects the duplicate outright
* ("Instance with that name already defined"), which meant the NPC topology had
* never once run with dead time -- and every T-type-vs-NPC comparison was
* therefore only valid at deadtime = 0.
Dcl1 0 n1 DCL
Dcl2 n2 0 DCL
{self._leg_diodes_npc("", out="a")}
{self._tail()}"""


# =========================================================================== #
# three phase
# =========================================================================== #
@dataclass
class ThreeLevelThreePhase(ThreeLevelLeg):
    """Three-phase three-level inverter, PD-PWM, star R-L load with a FLOATING
    neutral (the usual arrangement, and the one that lets a common-mode/triplen
    component exist without driving current).

    Exactly known, in the linear range:

        phase (leg-to-NP) fundamental peak   V1    = M * Vdc/2
        line-to-line fundamental peak              = sqrt(3) * M * Vdc/2
        phase current fundamental            I1    = V1 / |Z|
        total real power                     P     = 3 * 0.5 * V1 * I1 * cos(phi)

    The neutral point is the reason to prefer three phase: the NP excursion moves
    from f1 (large, single phase) to 3*f1 with much smaller amplitude, because
    the three legs' NP currents largely cancel.
    """

    def V_ll_peak(self):
        return math.sqrt(3.0) * self.V_1_peak()

    def V_ll_effective(self):
        return math.sqrt(3.0) * self.V_1_effective()

    def P_analytical(self):
        return 3.0 * 0.5 * self.V_1_peak() * self.I_1_peak() * math.cos(self.phi())

    def P_effective(self):
        return (3.0 * 0.5 * self.V_1_effective() * self.I_1_effective()
                * math.cos(self.phi()))

    def _modulation_signals(self, t):
        """The actual per-phase references, INCLUDING zero-sequence injection.

        Every closed-form model here (RMS voltage, neutral-point charge, rail
        current) integrates a function of the reference. Hard-coding a pure sine
        makes them silently wrong the moment injection is used -- which is how
        the checks caught this: with SVPWM at M=1.1 they reported +3.2 % on
        V_rms and -58 % on NP ripple, because injection genuinely reshapes the
        duty distribution (and, in the NP case, genuinely improves it).
        """
        w = self.w1
        raw = [self.M * np.sin(w * t + th) for th in self._phases()]
        mode = self.modulation.lower()
        if mode == "spwm" or len(raw) == 1:
            z = np.zeros_like(t)
        elif mode == "thipwm":
            z = self.M / 6.0 * np.sin(3.0 * w * t)
        elif mode == "svpwm":
            stack = np.vstack(raw)
            z = -(stack.max(axis=0) + stack.min(axis=0)) / 2.0
        else:
            z = np.zeros_like(t)
        return [r + z for r in raw]

    def V_rms_analytical(self):
        """RMS of the leg-to-NP voltage.

        Within a carrier period the leg is at +/-Vdc/2 for a fraction |m| and at
        0 otherwise, so <v^2> = (Vdc/2)^2 * <|m|>. For a pure sine <|M sin|> =
        2M/pi, giving the familiar Vdc/2*sqrt(2M/pi); with injection the mean is
        integrated numerically from the real reference.
        """
        n = 200000
        t = np.linspace(0.0, self.T1, n, endpoint=False)
        m = self._modulation_signals(t)[0]
        return (self.Vdc / 2.0) * math.sqrt(float(np.mean(np.abs(m))))

    def _np_charge_ptp(self) -> float:
        """NP charge excursion with all three legs summed.

        i_np = -sum_k i_k(t) * |m_k(t)| over the three phases.  The triplen
        content survives while the fundamental cancels, so the residual sits at
        3*f1 and is far smaller than the single-phase case.
        """
        n = 200000
        t = np.linspace(0.0, self.T1, n, endpoint=False)
        w = self.w1
        i_np = np.zeros(n)
        for th, m_k in zip(self._phases(), self._modulation_signals(t)):
            i_k = self.I_1_effective() * np.sin(w * t + th - self.phi())
            i_np -= i_k * np.abs(m_k)
        i_np -= i_np.mean()
        q = np.cumsum(i_np) * (self.T1 / n)
        q -= q.mean()
        return float(q.max() - q.min())

    #: zero-sequence injection per strategy. A common-mode term added to all
    #: three references cancels in every line-to-line voltage and in a floating
    #: star, so it changes NOTHING the load sees -- but it reshapes each leg's
    #: duty, which is what buys DC-bus utilisation (1.155x) or removes switching
    #: events (DPWM). LTspice B-sources evaluate these directly.
    ZERO_SEQ = {
        "spwm":    "0",
        # Classic 1/6 third-harmonic injection. The sign matters and is easy to
        # invert: at theta=90 deg sin(3*theta) = -1, so +M/6*sin(3*theta)
        # SUBTRACTS M/6 from the peak (reference peak 0.953 at M=1.1), while the
        # opposite sign ADDS it (peak 1.283 -> clipping, measured as -9.9 %
        # line-to-line error and 3.7 % THD).
        "thipwm":  "{M}/6*sin(3*2*pi*{f1}*time)",
        # min-max (equivalent to symmetric SVPWM's common-mode term)
        "svpwm":   "-(max(V(ra),max(V(rb),V(rc)))+min(V(ra),min(V(rb),V(rc))))/2",
    }

    #: Deliberately NOT offered. DPWMMAX/MIN as written for a two-level bridge
    #: (m0 = 1 - max, clamping a phase to the RAIL) shifts the whole reference
    #: into one carrier band, so a three-level leg stops using its middle state
    #: and degenerates to two-level. Measured: line-to-line fundamental fell
    #: from 554 V to 397 V and current THD rose from 0.32 % to 15.0 %, while the
    #: switching count halved -- cheap loss bought by wrecking the output.
    #: Genuine three-level discontinuous modulation clamps to the nearest LEVEL
    #: using the redundant small-vector states, which is a different algorithm.
    _TWO_LEVEL_ONLY = {"dpwmmax", "dpwmmin", "dpwm0", "dpwm1", "dpwm2", "dpwm3"}

    def _zero_seq_expr(self) -> str:
        m = self.modulation.lower()
        if m in self._TWO_LEVEL_ONLY:
            raise ValueError(
                f"'{self.modulation}' is a TWO-LEVEL discontinuous strategy and "
                f"is not valid here: applied to a three-level leg it shifts the "
                f"reference into one carrier band, abandoning the middle state "
                f"(measured: V_ll 554 -> 397 V, THD 0.32 -> 15.0 %). Real "
                f"three-level DPWM clamps to the nearest LEVEL via the "
                f"redundant small-vector states and is not implemented. "
                f"Available: {', '.join(self.ZERO_SEQ)}")
        if m not in self.ZERO_SEQ:
            raise ValueError(
                f"unknown modulation '{self.modulation}'. "
                f"Available: {', '.join(self.ZERO_SEQ)}")
        return self.ZERO_SEQ[m].format(M=self.M, f1=self.f1)

    def _phases(self):
        return (0.0, -2.0 * math.pi / 3.0, 2.0 * math.pi / 3.0)

    def switch_blocking_voltages(self) -> dict:
        v = self.Vdc / 2.0
        return {f"{n}{p}": v for n in ("S1", "S2", "S3", "S4") for p in "abc"}

    def switch_switching_voltages(self) -> dict:
        return {n: self.Vdc / 2.0 for n in self.switch_blocking_voltages()}

    def analytical(self) -> dict:
        d = super().analytical()
        d["V_ll"] = self.V_ll_effective()
        return d

    def design_notes(self):
        return super().design_notes() + [
            f"line-to-line fundamental = sqrt(3)*M*Vdc/2 = {self.V_ll_peak():.2f} V "
            f"({self.V_ll_peak()/math.sqrt(2):.2f} V rms)",
            f"three-phase total P = {self.P_analytical():.2f} W",
        ]

    def _preamble(self) -> str:
        base = super()._preamble()
        # three references 120 deg apart; SINE's phase argument is in degrees
        # raw sinusoidal references, then a single common-mode term added to all
        refs = "\n".join(
            f"Vr{p} r{p} 0 SINE(0 {self.M:g} {self.f1:g} 0 0 {-120*k:g})"
            for k, p in enumerate("abc"))
        refs += f"\nBz z 0 V={self._zero_seq_expr()}\n"
        refs += "\n".join(f"Bref{p} ref{p} 0 V=V(r{p})+V(z)" for p in "abc")
        # Explicit positive-logic gate per device. Reusing sp/sn with inverted
        # control terminals breaks under dead time: delaying the RISE of sn
        # lengthens S4's conduction instead of shortening it, which is
        # shoot-through rather than dead time.
        comps = "\n".join(
            f"Bsp{p} sp{p} 0 V=if(V(ref{p})>V(carp),1,-1)\n"
            f"Bsn{p} sn{p} 0 V=if(V(ref{p})>V(carn),1,-1)\n"
            f"Bg1{p} g1{p} 0 V={self._gate_expr(f'V(ref{p})-V(carp)')}\n"
            f"Bg4{p} g4{p} 0 V={self._gate_expr(f'V(carn)-V(ref{p})')}\n"
            f"Bg0{p} g0{p} 0 V="
            f"{self._gate_expr(f'min(V(carp)-V(ref{p}),V(ref{p})-V(carn))')}"
            for p in "abc")
        # Drop every single-phase-specific line, keeping only the bus and the
        # carriers. The prefixes must cover each per-device gate too: missing one
        # leaves a line referencing V(ref), which does not exist here (the
        # phases are refa/refb/refc) and LTspice rejects the netlist.
        _SINGLE = ("Vref ", "Bsp ", "Bsn ", "Bg1 ", "Bg4 ", "Bg0 ")
        keep = [ln for ln in base.splitlines() if not ln.startswith(_SINGLE)]
        return "\n".join(keep) + "\n" + refs + "\n" + comps + "\n"

    def _tail(self) -> str:
        tstop = (self.n_settle + self.n_meas) * self.T1
        tmax = 1.0 / self.fc / self.tmax_div
        v2 = self.Vdc / 2.0
        ic = "" if self.stiff_np else \
            f".ic V(p_dc)={v2:.12g}\n.ic V(n_dc)={-v2:.12g}\n"
        load = "\n".join(
            f"Lo{p} {p} m{p} {self.Lload:g} Rser=0 Rpar=1G\n"
            f"Ro{p} m{p} sn_star {self.Rload:g}" for p in "abc")
        return f"""{load}
* star point floats; a large resistor only gives the solver a DC reference
Rstar sn_star 0 10Meg
.model SWM SW(Ron={self.Ron:g} Roff={self.Roff:g} Vt=0 Vh=0)
.model DCL D(Ron=1m Roff=1e9 Vfwd=0)
{self._diode_models()}{ic}.ic I(Loa)=0
.ic I(Lob)=0
.ic I(Loc)=0
.tran 0 {tstop:.12g} 0 {tmax:.12g}
.save V(a) V(b) V(c) V(sn_star) V(p_dc) V(n_dc) I(Loa) I(Lob) I(Loc) I(Vbus) I(Cp) I(Cn)
.end
"""

    def extract(self, res, waves) -> dict:
        if waves is None:
            return {}
        t_end = self.measure_window()[1]
        va, vs = waves["V(a)"], waves["V(sn_star)"]
        ia = waves["I(Loa)"]
        # leg-to-NP voltage carries the common mode; the load sees va - v_star
        sv = Spectrum(waves.t, va, self.f1, t_end, self.n_meas)
        svl = Spectrum(waves.t, va - waves["V(b)"], self.f1, t_end, self.n_meas)
        si = Spectrum(waves.t, ia, self.f1, t_end, self.n_meas)
        out = {"V_1": sv.h(1), "V_rms": sv.rms(), "V_dc_offset": sv.dc,
               "V_ll": svl.h(1), "I_1": si.h(1), "I_rms": si.rms(),
               "thd_i_pct": si.thd() * 100.0,
               "thd_v_total_pct": sv.thd_total() * 100.0}
        # total power: sum of the three phases, same window and method
        t0 = t_end - self.n_meas * self.T1
        P = 0.0
        for p in "abc":
            _, vp = resample(waves.t, waves[f"V({p})"] - vs, t0, t_end, 1 << 16)
            _, ip = resample(waves.t, waves[f"I(Lo{p})"], t0, t_end, 1 << 16)
            P += float(np.mean(vp * ip))
        out["P"] = P
        pmA = power_metrics(waves.t, va - vs, ia, self.f1, t_end, self.n_meas)
        out["DPF"] = pmA["DPF"]
        out["PF"] = pmA["PF"]
        if waves.has("V(p_dc)") and waves.has("V(n_dc)"):
            _, vp2 = resample(waves.t, waves["V(p_dc)"], t0, t_end, 1 << 15)
            _, vn2 = resample(waves.t, waves["V(n_dc)"], t0, t_end, 1 << 15)
            imb = vp2 + vn2
            out["np_offset_V"] = float(np.mean(imb))
            out["np_ripple_V"] = float(imb.max() - imb.min())
        out.update(self._cap_metrics(waves, t_end))
        return out


@dataclass
class TType3Phase(ThreeLevelThreePhase):
    """Three-phase T-type three-level inverter."""

    def _suffixes(self):
        return tuple("abc")

    def switch_blocking_voltages(self) -> dict:
        """T-type: the RAIL switches block the FULL bus.

        With S4 conducting, the output sits at -Vdc/2 while S1's other terminal
        is at +Vdc/2, so S1 stands off the entire link -- and vice versa. Only
        the neutral-point pair is limited to Vdc/2. This is why a T-type needs
        full-voltage parts on the rails, which carry more switching energy, and
        it is the reason NPC overtakes T-type above roughly 10 kHz.
        """
        full, half = self.Vdc, self.Vdc / 2.0
        return {**{f"S1{p}": full for p in self._suffixes()},
                **{f"S4{p}": full for p in self._suffixes()},
                **{f"S2{p}": half for p in self._suffixes()},
                **{f"S3{p}": half for p in self._suffixes()}}

    def netlist(self) -> str:
        legs = "\n".join(f"""S1{p} p_dc {p} g1{p} 0 SWM
S4{p} {p} n_dc g4{p} 0 SWM
S2{p} {p} mid{p} g0{p} 0 SWM
S3{p} mid{p} 0 g0{p} 0 SWM
{self._leg_diodes(p)}""" for p in "abc")
        return f"""* Three-phase T-type three-level inverter -- generated by pwrlab
{self._preamble()}
{legs}
{self._tail()}"""


@dataclass
class NPC3Phase(ThreeLevelThreePhase):
    """Three-phase neutral-point-clamped three-level inverter."""

    def _suffixes(self):
        return tuple("abc")

    def bidirectional_pairs(self):
        return []

    def clamping_diodes(self) -> list:
        return [f"Dcl{i}{p}" for i in (1, 2) for p in "abc"]

    def netlist(self) -> str:
        legs = "\n".join(f"""S1{p} p_dc n1{p} g1{p} 0 SWM
S2{p} n1{p} {p} q2{p} 0 SWM
S3{p} {p} n2{p} q3{p} 0 SWM
S4{p} n2{p} n_dc g4{p} 0 SWM
Bq2{p} q2{p} 0 V=max(V(g1{p}),V(g0{p}))
Bq3{p} q3{p} 0 V=max(V(g4{p}),V(g0{p}))
Dcl1{p} 0 n1{p} DCL
Dcl2{p} n2{p} 0 DCL
{self._leg_diodes_npc(p)}""" for p in "abc")
        return f"""* Three-phase NPC three-level inverter -- generated by pwrlab
{self._preamble()}
{legs}
{self._tail()}"""


# =========================================================================== #
# capacitor selection
# =========================================================================== #
def dc_link_requirements(topo, v_ripple_frac=0.02, f_split=None) -> dict:
    """Everything needed to choose a DC-link capacitor for a three-level leg.

    Capacitors are chosen against THREE independent limits, and the binding one
    varies with the design:

      1. RMS ripple current -- drives self-heating and therefore lifetime. This
         is usually what selects an electrolytic bank, and it is frequency
         dependent, so the low/high split is reported separately.
      2. Voltage ripple    -- the capacitance needed to hold the neutral point
         within tolerance.
      3. Peak current      -- not a rating limit, but it sets the busbar and
         terminal design.

    Returns the requirement, not a part number.
    """
    split = topo.dc_link_irms_split(f_split=f_split)
    c_needed = topo.Cdc_for_np_ripple(v_ripple_frac)
    return {
        "topology": topo.name,
        "operating_point": {
            "Vdc": topo.Vdc, "M": topo.M, "f1": topo.f1,
            "fc": topo.fc, "P": topo.P_analytical(),
            "I_phase_rms": topo.I_1_peak() / math.sqrt(2.0),
            "power_factor": math.cos(topo.phi()),
        },
        "ripple_current": {
            "I_rms_A": split["I_rms_total"],
            "I_rms_below_split_A": split["I_rms_below_split"],
            "I_rms_above_split_A": split["I_rms_above_split"],
            "f_split_Hz": split["f_split_Hz"],
            "note": ("capacitor ripple-current ratings are frequency dependent: "
                     "electrolytics are rated at 100/120 Hz and derated above, "
                     "film caps the reverse -- check the part's derating curve "
                     "against the split, not against the total alone"),
        },
        "capacitance": {
            "C_for_ripple_F": c_needed,
            "v_ripple_target_frac": v_ripple_frac,
            "v_ripple_target_V": v_ripple_frac * topo.Vdc / 2.0,
            "C_currently_set_F": topo.C_half(),
            "np_ripple_at_current_C_V": topo.np_ripple_analytical(),
        },
        "voltage_rating": {
            "V_per_capacitor_V": topo.Vdc / 2.0,
            "note": ("each capacitor sees half the bus; derate for the NP ripple "
                     "and for any overvoltage transient"),
        },
    }
