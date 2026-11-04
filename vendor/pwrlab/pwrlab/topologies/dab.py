"""Dual active bridge, single phase shift, ideal switches.

Validated to 0.078 % worst case against closed-form theory over phi = 10..90 deg.

The netlist below encodes three findings that each cost real debugging time.
Changing any of them silently breaks accuracy by 40-150 %, so they are asserted
by the checks rather than trusted:

 1. NO `uic` on .tran.  `uic` makes LTspice IGNORE .ic and zero every inductor,
    injecting a ~750 A startup offset that decays with tau = L/(Rdamp+2Ron).
 2. `.ic` on ALL THREE inductors.  Without .ic on the windings the DC operating
    point treats them as shorts across a live bridge and seeds I(Ls) with ~2e5 A.
 3. `tr` must be very short.  Gates swing -1/+1 into Vt=0.5, so each leg has a
    both-off window of tr/2 during which the bridge node floats and the inductor
    current is forced through Roff.  tr=100p -> +19.5 A DC offset; tr=1p -> -0.3 A.
    (Vt=0 removes the both-off window and is NOT harmful -- it matches Vt=0.5 to
    0.003 % and is insensitive to tr. An earlier note claimed otherwise; that was
    measured with a broken startup state. Vt=0.5/tr=1p is kept only because it is
    what the 0.078 % sweep was validated with.)
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .. import checks as C
from .base import Topology, fmt_eng


@dataclass
class DAB(Topology):
    """Dual active bridge, single phase shift, 1:1 ideal-switch bridges."""

    V1: float = 400.0          # primary DC voltage [V]
    V2: float = 400.0          # secondary DC voltage [V] (sink)
    fs: float = 100e3          # switching frequency [Hz]
    L: float = 20e-6           # series (leakage) inductance [H]
    Lmag: float = 5e-3         # magnetizing inductance [H]
    n: float = 1.0             # turns ratio Np:Ns
    phi_deg: float = 30.0      # phase shift [deg]
    Ron: float = 1e-3          # switch on resistance [ohm]
    Roff: float = 1e6          # switch off resistance [ohm]
    Rdamp: float = 5e-3        # series damping in L, sets the settling time
    tr: float = 1e-12          # gate edge; both-off window is tr/2
    Nsettle: int = 120         # periods before measuring
    Nmeas: int = 10            # periods measured
    tmax_div: int = 1000       # max timestep = T / tmax_div

    convergence_quantity = "P_in"
    convergence_tol_pct = 1.0

    measurements = ["Iin", "Iout", "Pin", "Pout", "ILrms", "ILavg", "ILmax", "ILmin",
                    "Izvs_a1", "Izvs_b1", "Izvs_a2", "Izvs_b2", "Izvs_a3", "Izvs_b3"]
    tolerances = {"P_in": 2.0, "I_L_rms": 2.0}

    # ---------------- analytical model ---------------- #
    @property
    def phi(self) -> float:
        return math.radians(self.phi_deg)

    @property
    def T(self) -> float:
        return 1.0 / self.fs

    @property
    def td(self) -> float:
        return self.phi / (2 * math.pi) * self.T

    def P_analytical(self) -> float:
        """Single-phase-shift power transfer, referred through the turns ratio."""
        V2p = self.V2 * self.n
        return (self.V1 * V2p * self.phi * (math.pi - self.phi)
                / (2 * math.pi ** 2 * self.fs * self.L))

    def i_switching(self) -> float:
        """i_L at the primary switching instant (t=0), in steady state.

        Over 0 < t < t_d the inductor sees v_L = V1 + V2'; over t_d < t < T/2 it
        sees V1 - V2'.  Half-wave symmetry gives i(T/2) = -i(0), so

            2*i0 = -[ (V1+V2')*t_d + (V1-V2')*(T/2 - t_d) ] / L

        For the matched case V1 = n*V2 this collapses to the familiar
        i0 = -V1*t_d/L.
        """
        V2p = self.V2 * self.n
        half = self.T / 2
        return -((self.V1 + V2p) * self.td
                 + (self.V1 - V2p) * (half - self.td)) / (2 * self.L)

    def i_L_rms_analytical(self) -> float:
        """Exact RMS of the ideal piecewise-linear i_L over a half period.

        Only closed form for V1 == n*V2 (the trapezoid); otherwise fall back to
        numerically integrating the ideal piecewise-linear waveform, which is
        still independent of the simulator.
        """
        V2p = self.V2 * self.n
        L, T, td = self.L, self.T, self.td
        i0 = self.i_switching()
        i1 = i0 + (self.V1 + V2p) * td / L          # during 0 < t < td
        i2 = i1 + (self.V1 - V2p) * (T / 2 - td) / L
        seg = [(i0, i1, td), (i1, i2, T / 2 - td)]
        acc = sum((a * a + a * b + b * b) / 3.0 * dt for a, b, dt in seg if dt > 0)
        return math.sqrt(acc / (T / 2))

    def analytical(self) -> dict:
        return {"P_in": self.P_analytical(),
                "I_L_rms": self.i_L_rms_analytical()}

    def design_notes(self):
        return [
            f"phi = {self.phi_deg:g} deg = {self.phi:.4f} rad, "
            f"t_d = {fmt_eng(self.td,'s')} of T = {fmt_eng(self.T,'s')}",
            f"P = V1*V2'*phi*(pi-phi)/(2*pi^2*fs*L) = {self.P_analytical():.2f} W",
            f"i_L at switching instant = {self.i_switching():.3f} A",
            f"settle {self.Nsettle} periods (tau = L/(Rdamp+2Ron) = "
            f"{fmt_eng(self.L/(self.Rdamp+2*self.Ron),'s')}), "
            f"measure {self.Nmeas}",
            f"max timestep = T/{self.tmax_div} = {fmt_eng(self.T/self.tmax_div,'s')}",
        ]

    # ---------------- netlist ---------------- #
    def params(self) -> dict:
        return {"V1": self.V1, "V2": self.V2, "fs": self.fs, "L": self.L,
                "Lmag": self.Lmag, "n": self.n, "phi_deg": self.phi_deg,
                "Ron": self.Ron, "Roff": self.Roff, "Rdamp": self.Rdamp,
                "tr": self.tr, "Nsettle": self.Nsettle, "Nmeas": self.Nmeas,
                "tmax_div": self.tmax_div}

    def netlist(self) -> str:
        T, td = self.T, self.td
        tm = self.Nsettle * T
        tstop = (self.Nsettle + self.Nmeas) * T
        tmax = T / self.tmax_div
        tw = T / 2 - self.tr
        ic = self.i_switching() - self.V1 * (T / 4 - td) / self.Lmag
        Ls = self.Lmag / (self.n ** 2)
        probes = []
        for i, k in enumerate([1, 5, 9], start=1):
            probes.append(f".meas TRAN Izvs_a{i} FIND I(L1) AT={tm + k*T - 1e-9:.12g}")
            probes.append(f".meas TRAN Izvs_b{i} FIND I(L1) AT={tm + (k+0.5)*T - 1e-9:.12g}")
        return f"""* DAB single phase shift -- generated by pwrlab
V1 p_dc 0 {self.V1:g}
V2 s_dc 0 {self.V2:g}
Vga g_a 0 PULSE(-1 1 0 {self.tr:g} {self.tr:g} {tw:.12g} {T:.12g})
Vgb g_b 0 PULSE(1 -1 0 {self.tr:g} {self.tr:g} {tw:.12g} {T:.12g})
Vgc g_c 0 PULSE(-1 1 {td:.12g} {self.tr:g} {self.tr:g} {tw:.12g} {T:.12g})
Vgd g_d 0 PULSE(1 -1 {td:.12g} {self.tr:g} {self.tr:g} {tw:.12g} {T:.12g})
S_AH p_dc a g_a 0 SWM
S_AL a 0 0 g_a SWM
S_BH p_dc b g_b 0 SWM
S_BL b 0 0 g_b SWM
S_CH s_dc c g_c 0 SWM
S_CL c 0 0 g_c SWM
S_DH s_dc d g_d 0 SWM
S_DL d 0 0 g_d SWM
L1 a x {self.L:g} Rser={self.Rdamp:g} Rpar=1G
Lp x b {self.Lmag:g} Rser=0 Rpar=1G
Ls c d {Ls:g} Rser=0 Rpar=1G
K1 Lp Ls 1
.model SWM SW(Ron={self.Ron:g} Roff={self.Roff:g} Vt=0.5 Vh=0)
.ic I(L1)={ic:.12g}
.ic I(Lp)={ic:.12g}
.ic I(Ls)={-ic*self.n:.12g}
.tran 0 {tstop:.12g} 0 {tmax:.12g}
.save I(V1) I(V2) I(L1)
.meas TRAN Iin AVG I(V1) FROM={tm:.12g} TO={tstop:.12g}
.meas TRAN Iout AVG I(V2) FROM={tm:.12g} TO={tstop:.12g}
.meas TRAN Pin AVG {-self.V1:g}*I(V1) FROM={tm:.12g} TO={tstop:.12g}
.meas TRAN Pout AVG {self.V2:g}*I(V2) FROM={tm:.12g} TO={tstop:.12g}
.meas TRAN ILrms RMS I(L1) FROM={tm:.12g} TO={tstop:.12g}
.meas TRAN ILavg AVG I(L1) FROM={tm:.12g} TO={tstop:.12g}
.meas TRAN ILmax MAX I(L1) FROM={tm:.12g} TO={tstop:.12g}
.meas TRAN ILmin MIN I(L1) FROM={tm:.12g} TO={tstop:.12g}
{chr(10).join(probes)}
.end
"""

    def soft_switched(self) -> set:
        """Primary switches achieve ZVS turn-on for phi > 0 with V1 = n*V2.

        The secondary bridge is soft too in the matched case; kept to the primary
        here because that is what the netlist's Izvs probes actually verify.
        """
        return {"S_AH", "S_AL", "S_BH", "S_BL"} if self.phi_deg > 0 else set()

    def switch_blocking_voltages(self) -> dict:
        return {**{n: self.V1 for n in ("S_AH", "S_AL", "S_BH", "S_BL")},
                **{n: self.V2 for n in ("S_CH", "S_CL", "S_DH", "S_DL")}}

    # ---------------- extraction ---------------- #
    def extract(self, res, waves) -> dict:
        m = res.meas
        out = {"P_in": m.get("Pin"), "P_out": m.get("Pout"),
               "I_L_rms": m.get("ILrms"), "I_L_dc": m.get("ILavg"),
               "I_L_max": m.get("ILmax"), "I_L_min": m.get("ILmin")}
        if out["P_in"] and out["P_out"] is not None:
            out["efficiency_pct"] = out["P_out"] / out["P_in"] * 100.0
        za = [m.get(f"Izvs_a{i}") for i in (1, 2, 3)]
        zb = [m.get(f"Izvs_b{i}") for i in (1, 2, 3)]
        if all(v is not None for v in za + zb):
            out["zvs"] = all(v < 0 for v in za) and all(v > 0 for v in zb)
            out["zvs_margin_A"] = min([-v for v in za] + [v for v in zb])
        return out

    # ---------------- topology-specific checks ---------------- #
    def topology_checks(self, ex, waves, res):
        cs = []
        cs.append(C.power_balance(ex.get("P_in"), ex.get("P_out"), tol_pct=5.0))
        rms = ex.get("I_L_rms")
        cs.append(C.dc_offset(ex.get("I_L_dc"), rms, tol_frac=0.05))
        if waves is not None and waves.has("I(L1)"):
            t0, t1 = self.measure_window()
            cm = waves.cycle_means(waves["I(L1)"], t1, self.T, min(self.Nmeas, 10))
            cs.append(C.steady_state(cm, tol_frac=0.02, ref=rms))
        if "zvs" in ex:
            cs.append(C.Check("ZVS on all primary turn-ons", bool(ex["zvs"]), C.WARN,
                              f"worst-case |i_L| at turn-on "
                              f"{ex.get('zvs_margin_A', float('nan')):.3f} A"))
        # ripple sanity: an ideal SPS DAB with V1 == n*V2 ramps 2*V1*td/L
        if abs(self.V1 - self.V2 * self.n) < 1e-9 and ex.get("I_L_max") is not None:
            pred = 2 * self.V1 * self.td / self.L
            got = ex["I_L_max"] - ex["I_L_min"]
            cs.append(C.relative_error("i_L ripple", got, pred, 5.0, C.WARN))
        return cs
