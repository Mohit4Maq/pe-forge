"""Device loss and electrothermal analysis.

Method: the power stage is simulated with IDEAL switches, then losses are
computed in post-processing from the resulting currents and the device
datasheet. This is deliberate and standard -- putting vendor SPICE models in the
loop makes runs an order of magnitude slower and convergence fragile, and buys
little, because switching loss in a real design is dominated by datasheet
E_on/E_off measured in a double-pulse test, not by whatever a behavioural model
extrapolates.

The part that is NOT a shortcut: Rds(on) rises with junction temperature, and
junction temperature depends on loss, so the two must be solved TOGETHER. A
single-pass calculation at 25 C understates conduction loss badly -- for SiC,
Rds(on) roughly doubles by 150 C, so a 25 C answer can be ~40 % optimistic. This
module iterates to a self-consistent fixed point and reports the temperature it
converged to.

Every device here is REPRESENTATIVE, not a specific part number. Replace with
real datasheet values before trusting an absolute number; the framework is the
deliverable, the library is a placeholder.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import checks as C


# --------------------------------------------------------------------------- #
@dataclass
class Device:
    name: str
    kind: str = "mosfet"            # 'mosfet' | 'igbt'
    V_rating: float = 1200.0
    I_rating: float = 40.0
    # conduction
    Rds_25: float = 40e-3           # MOSFET on-resistance at 25 C
    Rds_tc: float = 0.006           # per K, linear-ish: R(T)=R25*(1+tc*(T-25))
    Vce0: float = 0.9               # IGBT knee voltage
    Rce: float = 15e-3              # IGBT slope resistance
    # switching, from a double-pulse test at (V_ref, I_ref)
    Eon_ref: float = 250e-6         # J
    Eoff_ref: float = 150e-6        # J
    V_ref: float = 600.0
    I_ref: float = 20.0
    Esw_i_exp: float = 1.0          # E ~ I^exp
    Esw_v_exp: float = 1.3          # E ~ V^exp (hard switching, >1 in practice)
    # body diode / freewheel
    Vf: float = 1.4
    Rd: float = 20e-3
    Err_ref: float = 0.0            # reverse-recovery energy (0 for SiC/GaN)
    # thermal
    Rth_jc: float = 0.45            # K/W
    Rth_ch: float = 0.15            # K/W (case to heatsink, with TIM)
    Tj_max: float = 175.0

    def R_on(self, Tj: float) -> float:
        return self.Rds_25 * (1.0 + self.Rds_tc * (Tj - 25.0))

    def P_cond(self, I_rms: float, I_avg: float, Tj: float) -> float:
        if self.kind == "diode":
            return self.Vf * abs(I_avg) + self.Rd * I_rms ** 2
        if self.kind == "igbt":
            return self.Vce0 * abs(I_avg) + self.Rce * I_rms ** 2
        return self.R_on(Tj) * I_rms ** 2

    def _scale(self, e_ref, V, I, Tj):
        if I <= 0 or V <= 0 or e_ref <= 0:
            return 0.0
        e = e_ref * (I / self.I_ref) ** self.Esw_i_exp \
            * (V / self.V_ref) ** self.Esw_v_exp
        return e * (1.0 + 0.003 * (Tj - 25.0))

    def as_diode(self, name=None) -> "Device":
        """This device's co-packed/clamping diode as a standalone part.

        NPC's clamping diodes carry the whole zero-state current, so leaving them
        out understates NPC conduction loss and biases any T-type comparison.
        """
        return Device(name or (self.name + "_diode"), "diode",
                      V_rating=self.V_rating, I_rating=self.I_rating,
                      Vf=self.Vf, Rd=self.Rd, Eon_ref=0.0, Eoff_ref=0.0,
                      Err_ref=self.Err_ref, V_ref=self.V_ref, I_ref=self.I_ref,
                      Rth_jc=self.Rth_jc * 1.6, Rth_ch=self.Rth_ch,
                      Tj_max=self.Tj_max)

    def E_on(self, V, I, Tj=125.0, soft=False) -> float:
        """Turn-on energy. Under ZVS the device turns on with its drain already
        at zero, so the hard-switching E_on (and the diode's recovery energy,
        which is dissipated in the OPPOSITE device at its turn-on) does not
        apply. Charging it anyway is the single biggest way to make a
        soft-switched converter look bad on paper."""
        if soft:
            return 0.0
        return self._scale(self.Eon_ref + self.Err_ref, V, I, Tj)

    def E_off(self, V, I, Tj=125.0, soft=False) -> float:
        """Turn-off energy. ZVS does not remove it -- current is still being
        interrupted -- though a snubber or Coss reduces it. Kept in full."""
        return self._scale(self.Eoff_ref, V, I, Tj)


LIBRARY = {
    # representative 1200 V SiC MOSFET
    "SiC_1200V_40m": Device("SiC_1200V_40m", "mosfet", 1200, 40, Rds_25=40e-3,
                            Rds_tc=0.006, Eon_ref=250e-6, Eoff_ref=150e-6,
                            V_ref=600, I_ref=20, Err_ref=0.0, Vf=1.4, Rd=20e-3,
                            Rth_jc=0.45, Rth_ch=0.15, Tj_max=175),
    # representative 1200 V Si IGBT with co-packed diode
    "Si_IGBT_1200V_50A": Device("Si_IGBT_1200V_50A", "igbt", 1200, 50,
                                Vce0=0.9, Rce=15e-3, Eon_ref=4.5e-3,
                                Eoff_ref=3.2e-3, Err_ref=1.8e-3, V_ref=600,
                                I_ref=50, Esw_v_exp=1.0, Vf=1.6, Rd=12e-3,
                                Rth_jc=0.30, Rth_ch=0.10, Tj_max=150),
    # representative 650 V GaN HEMT
    "GaN_650V_50m": Device("GaN_650V_50m", "mosfet", 650, 30, Rds_25=50e-3,
                           Rds_tc=0.0085, Eon_ref=35e-6, Eoff_ref=15e-6,
                           V_ref=400, I_ref=15, Err_ref=0.0, Vf=2.0, Rd=50e-3,
                           Rth_jc=0.55, Rth_ch=0.20, Tj_max=150),
    # representative 650 V Si IGBT -- the NPC / T-type-NP-switch class.
    # Switching energy is roughly a third of the 1200 V part at the same
    # current, which is precisely why voltage class drives the T-type vs NPC
    # crossover with switching frequency.
    "Si_IGBT_650V_50A": Device("Si_IGBT_650V_50A", "igbt", 650, 50,
                               Vce0=0.85, Rce=12e-3, Eon_ref=1.5e-3,
                               Eoff_ref=1.1e-3, Err_ref=0.6e-3, V_ref=400,
                               I_ref=50, Esw_v_exp=1.0, Vf=1.5, Rd=10e-3,
                               Rth_jc=0.28, Rth_ch=0.10, Tj_max=150),
    # representative 650 V SiC MOSFET
    "SiC_650V_25m": Device("SiC_650V_25m", "mosfet", 650, 60, Rds_25=25e-3,
                           Rds_tc=0.006, Eon_ref=90e-6, Eoff_ref=45e-6,
                           V_ref=400, I_ref=30, Err_ref=0.0, Vf=1.3, Rd=15e-3,
                           Rth_jc=0.35, Rth_ch=0.12, Tj_max=175),
    # representative 300 V Si MOSFET -- the neutral-point switch class for a
    # 400 V link, which is where the T-type advantage is largest
    "Si_300V_15m": Device("Si_300V_15m", "mosfet", 300, 60, Rds_25=15e-3,
                          Rds_tc=0.008, Eon_ref=120e-6, Eoff_ref=90e-6,
                          V_ref=200, I_ref=30, Err_ref=60e-6, Vf=1.0, Rd=8e-3,
                          Rth_jc=0.45, Rth_ch=0.15, Tj_max=150),
    # representative 150 V Si MOSFET for low-voltage work
    "Si_150V_3m": Device("Si_150V_3m", "mosfet", 150, 100, Rds_25=3e-3,
                         Rds_tc=0.008, Eon_ref=40e-6, Eoff_ref=30e-6,
                         V_ref=60, I_ref=25, Err_ref=20e-6, Vf=0.9, Rd=4e-3,
                         Rth_jc=0.6, Rth_ch=0.2, Tj_max=150),
}


# --------------------------------------------------------------------------- #
def switch_metrics(waves, name, t0, t1, step_frac=0.15):
    """RMS/average current and the commutated currents for one switch.

    Commutations are found as STEPS in the switch current, not as threshold
    crossings of |i|.  That distinction matters: in a DAB (and any converter
    whose device current reverses while the device is on) the current passes
    through zero mid-conduction, and a magnitude threshold counts each of those
    as two extra switching events -- which doubled the event count and inflated
    switching loss by 2x here before this was fixed.  A real commutation moves
    the current abruptly between samples; a zero crossing does not.

    Each event is classified: current stepping UP into the device is a turn-on,
    stepping DOWN to zero is a turn-off.  They carry different energies, and a
    soft-switched turn-on carries almost none.
    """
    key = f"I({name})"
    if not waves.has(key):
        return None
    i = waves[key]
    m = waves.window(t0, t1)
    t, iw = waves.t[m], i[m]
    if t.size < 4:
        return None
    a = np.abs(iw)
    pk = float(a.max())
    dt = np.diff(t)
    dur = t[-1] - t[0]
    if pk <= 0 or dur <= 0:
        return {"I_rms": 0.0, "I_avg": 0.0, "n_on": 0, "n_off": 0,
                "I_on": [], "I_off": []}
    I_rms = float(np.sqrt(np.sum(0.5 * (iw[:-1] ** 2 + iw[1:] ** 2) * dt) / dur))
    I_avg = float(np.sum(0.5 * (np.abs(iw[:-1]) + np.abs(iw[1:])) * dt) / dur)

    jump = np.abs(np.diff(a))
    idx = np.flatnonzero(jump > step_frac * pk)
    # collapse runs of adjacent samples belonging to one commutation
    events = []
    for k in idx:
        if events and k - events[-1][-1] <= 2:
            events[-1].append(k)
        else:
            events.append([k])
    I_on, I_off = [], []
    for grp in events:
        lo, hi = grp[0], grp[-1] + 1
        before, after = a[lo], a[min(hi, len(a) - 1)]
        if after > before:
            I_on.append(float(after))      # current established at turn-on
        else:
            I_off.append(float(before))    # current interrupted at turn-off
    return {"I_rms": I_rms, "I_avg": I_avg, "I_peak": pk,
            "I_sw_max": max(I_on + I_off) if (I_on or I_off) else 0.0,
            "n_on": len(I_on), "n_off": len(I_off),
            "I_on": I_on, "I_off": I_off}


def voltage_margin(v_block, v_rating, L_loop_nH=None, di_dt_A_per_ns=None,
                   min_margin_pct=20.0) -> dict:
    """Device voltage margin INCLUDING commutation overshoot.

    An ideal-switching simulation cannot predict turn-off overshoot -- neither
    can PLECS -- because the overshoot comes from L_loop * di/dt in parasitics
    the netlist does not contain. Rather than report a blocking voltage that
    silently omits it, this applies an explicit rule and fails loudly when the
    margin is thin.

    Measured consequence of omitting it: at 800 V and 31 A a 1200 V part keeps
    27 % margin at 50 nH / 20 ns but only 7.5 % at 200 nH -- not a part you
    would ship, yet indistinguishable in the simulation.

    L_loop MUST come from layout extraction or a double-pulse measurement. The
    default band is for triage only and is flagged as such.
    """
    assumed = L_loop_nH is None or di_dt_A_per_ns is None
    L = 100.0 if L_loop_nH is None else float(L_loop_nH)
    didt = 1.0 if di_dt_A_per_ns is None else float(di_dt_A_per_ns)
    v_over = L * 1e-9 * didt * 1e9          # nH * A/ns = V
    v_peak = v_block + v_over
    margin = (v_rating - v_peak) / v_rating * 100.0 if v_rating else float("nan")
    return {
        "V_block": v_block, "V_overshoot": v_over, "V_peak": v_peak,
        "V_rating": v_rating, "margin_pct": margin,
        "L_loop_nH": L, "di_dt_A_per_ns": didt,
        "assumed_parasitics": assumed,
        "ok": margin >= min_margin_pct,
        "note": ("L_loop and di/dt were ASSUMED, not supplied -- this is a triage "
                 "figure and must not appear in a deliverable. Extract L_loop "
                 "from layout or a double-pulse test."
                 if assumed else
                 "based on supplied parasitics"),
    }


@dataclass
class LossResult:
    rows: list = field(default_factory=list)
    Ta: float = 40.0
    Rth_hs: float = 0.5
    iterations: int = 0
    converged: bool = True
    P_out: float | None = None

    def table(self) -> pd.DataFrame:
        return pd.DataFrame(self.rows)

    @property
    def P_total(self):
        return float(sum(r["P_total"] for r in self.rows))

    @property
    def Tj_max(self):
        return max((r["Tj"] for r in self.rows), default=float("nan"))

    def summary(self) -> str:
        df = self.table()
        L = [df.to_string(index=False, float_format=lambda v: f"{v:.4g}"), ""]
        L.append(f"total device loss {self.P_total:.2f} W, hottest junction "
                 f"{self.Tj_max:.1f} C (Ta {self.Ta:g} C, heatsink "
                 f"{self.Rth_hs:g} K/W)")
        if self.P_out:
            eta = self.P_out / (self.P_out + self.P_total) * 100
            L.append(f"efficiency implied by device loss alone: {eta:.3f} %")
        L.append(f"electrothermal fixed point: {self.iterations} iterations, "
                 f"{'converged' if self.converged else 'NOT CONVERGED'}")
        return "\n".join(L)


class LossAnalysis:
    """Electrothermal analysis of an already-simulated operating point."""

    def __init__(self, topology, waves, devices, Ta=40.0, Rth_hs=0.5,
                 shared_heatsink=True, soft_turn_on=None):
        self.topo = topology
        self.waves = waves
        self.devices = devices            # {switch_name: Device}
        self.Ta = Ta
        self.Rth_hs = Rth_hs
        self.shared = shared_heatsink
        # switches that achieve ZVS turn-on; the topology can declare this
        if soft_turn_on is None:
            soft_turn_on = set(getattr(topology, "soft_switched", lambda: set())())
        self.soft_on = set(soft_turn_on)

    def solve(self, tol=0.1, max_iter=40) -> LossResult:
        """Fixed-point iteration on junction temperature.

        Loss raises Tj, Tj raises Rds(on), which raises loss. Damped updates keep
        it stable; divergence here is physically meaningful -- it is thermal
        runaway, and the check below reports it rather than hiding it.
        """
        t0, t1 = self.topo.measure_window()
        blocking = self.topo.switch_blocking_voltages()
        # E_on/E_off scale with the voltage actually commutated at a transition,
        # NOT with the device's blocking rating. In a three-level leg each
        # transition moves one level, so this is Vdc/2 even on a T-type rail
        # switch that must be RATED for the full bus. Using the blocking voltage
        # here would double-count the topology's voltage advantage.
        switching = self.topo.switch_switching_voltages()
        stats = {n: switch_metrics(self.waves, n, t0, t1)
                 for n in self.devices}
        # A bidirectional pair shares one commutation: only the transistor whose
        # forward direction matches the current actually switches, while its
        # partner's antiparallel diode conducts. Split the events between them
        # rather than charging each the full amount.
        for a, b in self.topo.bidirectional_pairs():
            if a in stats and b in stats and stats[a] and stats[b]:
                for k in ("I_on", "I_off"):
                    ev = stats[a][k]
                    stats[a][k] = ev[0::2]
                    stats[b][k] = ev[1::2]
                stats[a]["n_on"] = len(stats[a]["I_on"])
                stats[a]["n_off"] = len(stats[a]["I_off"])
                stats[b]["n_on"] = len(stats[b]["I_on"])
                stats[b]["n_off"] = len(stats[b]["I_off"])
        missing = [n for n, s in stats.items() if s is None]
        if missing:
            raise RuntimeError(
                f"no current waveform for {missing}; re-run with switch currents "
                f"saved (LossAnalysis.simulate() does this automatically)")

        fs = self.topo.params().get("fs") or self.topo.params().get("f1") or 1.0
        window = t1 - t0
        Tj = {n: self.Ta + 25.0 for n in self.devices}
        conv, it = False, 0
        for it in range(1, max_iter + 1):
            P = {}
            for n, dev in self.devices.items():
                s, V = stats[n], switching.get(n, blocking.get(n, 0.0))
                p_c = dev.P_cond(s["I_rms"], s["I_avg"], Tj[n])
                soft = n in self.soft_on
                e_sw = sum(dev.E_on(V, i, Tj[n], soft) for i in s["I_on"]) \
                    + sum(dev.E_off(V, i, Tj[n]) for i in s["I_off"])
                p_s = e_sw / window if window > 0 else 0.0
                P[n] = (p_c, p_s, p_c + p_s)
            P_tot = sum(v[2] for v in P.values())
            newTj = {}
            for n, dev in self.devices.items():
                rise_hs = self.Rth_hs * (P_tot if self.shared else P[n][2])
                newTj[n] = self.Ta + rise_hs + P[n][2] * (dev.Rth_jc + dev.Rth_ch)
            delta = max(abs(newTj[n] - Tj[n]) for n in Tj)
            # damped update: undamped iteration oscillates once Rds_tc is large
            Tj = {n: 0.5 * Tj[n] + 0.5 * newTj[n] for n in Tj}
            if delta < tol:
                conv = True
                break

        # Recompute once at the CONVERGED temperature. Without this the table
        # reports Tj and Rds(Tj) from the final damped update but P from the
        # iteration before it, so P_cond != Rds(Tj)*I_rms^2 and the numbers do
        # not reconcile -- which is exactly what a reader would check first.
        P = {}
        for n, dev in self.devices.items():
            s, V = stats[n], switching.get(n, blocking.get(n, 0.0))
            p_c = dev.P_cond(s["I_rms"], s["I_avg"], Tj[n])
            soft = n in self.soft_on
            e_sw = sum(dev.E_on(V, i, Tj[n], soft) for i in s["I_on"]) \
                + sum(dev.E_off(V, i, Tj[n]) for i in s["I_off"])
            P[n] = (p_c, e_sw / window if window > 0 else 0.0,
                    p_c + (e_sw / window if window > 0 else 0.0))

        rows = []
        for n, dev in self.devices.items():
            s = stats[n]
            p_c, p_s, p_t = P[n]
            rows.append({"switch": n, "device": dev.name,
                         "I_rms": s["I_rms"], "I_avg": s["I_avg"],
                         "I_peak": s.get("I_peak", 0.0),
                         "I_sw_max": s.get("I_sw_max", 0.0),
                         "V_block": blocking.get(n, 0.0),
                         "V_switched": switching.get(n, 0.0),
                         "V_rating": dev.V_rating,
                         "n_on": s["n_on"], "n_off": s["n_off"],
                         "ZVS": n in self.soft_on,
                         "P_cond": p_c, "P_sw": p_s, "P_total": p_t,
                         "Rds_hot": dev.R_on(Tj[n]) if dev.kind == "mosfet"
                         else float("nan"),
                         "Tj": Tj[n], "Tj_max": dev.Tj_max})
        return LossResult(rows, self.Ta, self.Rth_hs, it, conv)

    # ------------------------------------------------------------------ #
    @staticmethod
    def voltage_checks(lr: LossResult, L_loop_nH=None, di_dt_A_per_ns=None,
                       min_margin_pct=20.0) -> list:
        """Blocking-voltage margin per device, including commutation overshoot."""
        cs = []
        for r in lr.rows:
            if not r.get("V_rating"):
                continue
            m = voltage_margin(r["V_block"], r["V_rating"], L_loop_nH,
                               di_dt_A_per_ns, min_margin_pct)
            sev = C.WARN if m["assumed_parasitics"] else C.ERROR
            cs.append(C.Check(
                f"voltage margin {r['switch']} ({r['device']})", m["ok"], sev,
                f"blocks {m['V_block']:.0f} V + {m['V_overshoot']:.0f} V "
                f"overshoot = {m['V_peak']:.0f} V on a {m['V_rating']:.0f} V "
                f"part -> {m['margin_pct']:.1f} % margin. {m['note']}",
                m["margin_pct"], min_margin_pct))
        return cs

    @staticmethod
    def checks(lr: LossResult) -> list:
        cs = [C.Check("electrothermal solution converged", lr.converged, C.ERROR,
                      f"{lr.iterations} iterations"
                      + ("" if lr.converged else " -- suggests thermal runaway"))]
        for r in lr.rows:
            cs.append(C.in_range(f"Tj({r['switch']}) below rating", r["Tj"],
                                 hi=r["Tj_max"], unit=" C"))
        return cs


# --------------------------------------------------------------------------- #
def add_switch_saves(netlist: str, names) -> str:
    """Append device currents to the netlist's .save so losses can be computed.

    Kept out of the topologies themselves: switch currents multiply the .raw size
    and are dead weight for an ordinary accuracy run.
    """
    extra = " ".join(f"I({n})" for n in names)
    out, done = [], False
    for line in netlist.splitlines():
        if line.lower().startswith(".save") and not done:
            line = line + " " + extra
            done = True
        out.append(line)
    if not done:
        out.insert(max(0, len(out) - 1), f".save {extra}")
    return "\n".join(out) + "\n"


def analyse_losses(topology, devices, runner, Ta=40.0, Rth_hs=0.5, tag="loss",
                   soft_turn_on=None, include_clamp_diodes=True):
    """Simulate with switch currents saved, then solve the electrothermal point.

    Returns (LossResult, StudyResult) so the loss numbers can be sanity-checked
    against the simulated power flow.
    """
    from .study import Study
    from .waveforms import Waves
    st = Study(topology, runner=runner)
    devices = dict(devices)
    extra = []
    if include_clamp_diodes:
        extra += topology.clamping_diodes()
        # Freewheel diodes conduct through every dead-time interval. Leaving
        # them out makes any converter with realistic dead time look better than
        # it is, and the error grows with switching frequency.
        extra += topology.freewheel_diodes()
    if extra:
        base = next(iter(devices.values()))
        for d in extra:
            devices.setdefault(d, base.as_diode(d))
    net = add_switch_saves(topology.netlist(), devices.keys())
    res = runner.run_text(net, dict(topology.params()), topology.measurements,
                          tag=tag)
    sr = st._post(topology, res)
    if not res.ok:
        raise RuntimeError(f"loss run failed: {res.why_not_ok}")
    waves = Waves(res.raw_path)
    lr = LossAnalysis(topology, waves, devices, Ta=Ta, Rth_hs=Rth_hs,
                      soft_turn_on=soft_turn_on).solve()
    lr.P_out = sr.extracted.get("P_out") or sr.extracted.get("P")
    return lr, sr


def required_heatsink(topology, devices, runner, Ta=40.0, Tj_target=None,
                      tag="hs", soft_turn_on=None, tol=0.5):
    """Largest heatsink Rth that keeps every junction at or below Tj_target.

    Solved by bisection on Rth because loss itself depends on temperature: a
    cooler sink lowers Rds(on), which lowers loss, which lowers temperature
    again. Dividing a temperature budget by a fixed loss ignores that and
    undersizes the heatsink.
    """
    lo, hi = 1e-4, 20.0
    best = None
    for _ in range(40):
        mid = math.sqrt(lo * hi)
        lr, sr = analyse_losses(topology, devices, runner, Ta=Ta, Rth_hs=mid,
                                tag=tag, soft_turn_on=soft_turn_on)
        limit = Tj_target if Tj_target is not None else \
            min(r["Tj_max"] for r in lr.rows)
        if lr.converged and lr.Tj_max <= limit:
            best, lo = (mid, lr, sr), mid
        else:
            hi = mid
        if hi / lo < 1.0 + tol / 100.0:
            break
    return best


# --------------------------------------------------------------------------- #
# instantaneous loss profile -> junction temperature ripple
# --------------------------------------------------------------------------- #
def loss_profile(waves, name, dev, V_sw, t0, t1, n_bins=400, Tj=125.0,
                 soft=False):
    """Instantaneous loss p(t) for one device, binned over the fundamental.

    Conduction is computed point-by-point from the device current; switching
    energy is dropped into the bin containing each commutation. Binning at
    roughly the carrier period is deliberate: the Foster network's fastest
    stage has tau ~ 20 us, so it cannot resolve an individual switching pulse
    anyway, and per-pulse resolution would cost a great deal for no change in
    the answer.
    """
    key = f"I({name})"
    if not waves.has(key):
        return None, None
    m = waves.window(t0, t1)
    t, i = waves.t[m], waves[key][m]
    if t.size < 8:
        return None, None
    edges = np.linspace(t0, t1, n_bins + 1)
    idx = np.clip(np.searchsorted(edges, t) - 1, 0, n_bins - 1)
    dt_bin = (t1 - t0) / n_bins

    # conduction: mean of i^2 within each bin
    sq = np.zeros(n_bins)
    cnt = np.zeros(n_bins)
    np.add.at(sq, idx, i ** 2)
    np.add.at(cnt, idx, 1)
    i2 = np.where(cnt > 0, sq / np.maximum(cnt, 1), 0.0)
    if getattr(dev, "kind", "mosfet") == "diode":
        absi = np.zeros(n_bins)
        np.add.at(absi, idx, np.abs(i))
        p = dev.P_cond(0.0, np.where(cnt > 0, absi / np.maximum(cnt, 1), 0.0), Tj)
    else:
        p = dev.R_on(Tj) * i2

    # switching: energy of each commutation into its bin
    s = switch_metrics(waves, name, t0, t1)
    if s:
        a = np.abs(i)
        pk = a.max() if a.size else 0.0
        if pk > 0:
            jump = np.abs(np.diff(a))
            ev = np.flatnonzero(jump > 0.15 * pk)
            for e in ev:
                b = idx[min(e + 1, len(idx) - 1)]
                cur = float(a[min(e + 1, len(a) - 1)] if a[min(e+1, len(a)-1)] > a[e]
                            else a[e])
                rising = a[min(e + 1, len(a) - 1)] > a[e]
                energy = (dev.E_on(V_sw, cur, Tj, soft) if rising
                          else dev.E_off(V_sw, cur, Tj))
                p[b] += float(energy) / dt_bin
    centres = 0.5 * (edges[:-1] + edges[1:])
    return centres, p


def junction_temperature_ripple(topology, waves, devices, lr, Ta=40.0,
                                Rth_hs=0.05, foster=None, n_bins=400):
    """Tj(t) per device, using each one's Foster network on its own loss profile."""
    from .thermal import GENERIC_SIC_FOSTER, junction_ripple
    t0, t1 = topology.measure_window()
    f1 = topology.params().get("f1")
    if f1:                                   # one fundamental period is enough
        t0 = max(t0, t1 - 1.0 / f1)
    switching = topology.switch_switching_voltages()
    P_tot = lr.P_total
    T_hs = Ta + Rth_hs * P_tot
    rows = {r["switch"]: r for r in lr.rows}
    out = {}
    for name, dev in devices.items():
        r = rows.get(name)
        if r is None:
            continue
        base = foster or GENERIC_SIC_FOSTER
        net = base.scaled_to(dev.Rth_jc)
        T_case = T_hs + r["P_total"] * dev.Rth_ch
        tt, pp = loss_profile(waves, name, dev, switching.get(name, 0.0),
                              t0, t1, n_bins, Tj=r["Tj"],
                              soft=name in getattr(lr, "soft_on", set()))
        if tt is None:
            continue
        out[name] = junction_ripple(tt, pp, net, T_case, dev.Tj_max, name)
    return out
