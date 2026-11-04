"""What a topology must provide.

The contract is deliberately demanding: a topology must supply a CLOSED-FORM
model alongside its netlist.  That is the whole point.  Without an independent
prediction there is nothing to check a simulation against, and an unchecked
power-electronics simulation is a plausible-looking number of unknown validity.

Where closed form is only approximate (FHA for resonant converters, say), the
topology says so via `tolerances`, and the report states the acceptance
criterion it was judged by rather than pretending to 0.1 % agreement.
"""
from __future__ import annotations

import math
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class Param:
    name: str
    value: float
    unit: str = ""
    desc: str = ""
    lo: float | None = None
    hi: float | None = None

    def check(self):
        if self.lo is not None and self.value < self.lo:
            raise ValueError(f"{self.name}={self.value}{self.unit} below minimum {self.lo}")
        if self.hi is not None and self.value > self.hi:
            raise ValueError(f"{self.name}={self.value}{self.unit} above maximum {self.hi}")


class Topology(ABC):
    """Base class. Subclasses are dataclasses holding their design parameters."""

    #: .meas names the netlist defines
    measurements: list = []
    #: acceptance tolerances, per analytical quantity, in percent
    tolerances: dict = {}
    #: default tolerance for anything not named above
    default_tolerance_pct: float = 2.0

    # -- identity ---------------------------------------------------------- #
    @property
    def name(self) -> str:
        return type(self).__name__

    @abstractmethod
    def params(self) -> dict:
        """Every parameter the netlist references, as {name: value}."""

    # -- the three things that make a topology checkable -------------------- #
    @abstractmethod
    def netlist(self) -> str:
        """SPICE netlist text, with .param lines the runner can override."""

    @abstractmethod
    def analytical(self) -> dict:
        """Closed-form predictions: {quantity: value}. Keys should match
        `extract()` keys so the two can be compared automatically."""

    @abstractmethod
    def extract(self, res, waves) -> dict:
        """Physical quantities from a SimResult (+ Waves). Keys must line up
        with `analytical()`."""

    # -- optional ----------------------------------------------------------- #
    def topology_checks(self, extracted, waves, res) -> list:
        """Checks that only make sense for this converter (ZVS, CCM/DCM, ...)."""
        return []

    def switch_blocking_voltages(self) -> dict:
        """{switch: peak voltage it must BLOCK} -- the device rating requirement.

        Distinct from the switched voltage below, and the distinction is the
        whole difference between T-type and NPC: a T-type rail switch blocks the
        FULL bus (so an 800 V link needs 1200 V parts there) while every NPC
        device is clamped to half the bus. Collapsing the two erases the
        topologies' defining trade-off.
        """
        return {}

    def switch_switching_voltages(self) -> dict:
        """{switch: voltage actually commutated at a transition}.

        This, not the blocking rating, is what scales E_on/E_off. In both
        three-level topologies each transition moves one level, so the commutated
        voltage is Vdc/2 even where the blocking requirement is Vdc.
        """
        return self.switch_blocking_voltages()

    def freewheel_diodes(self) -> list:
        """Antiparallel diode instances that carry load current.

        These conduct during every dead-time interval, at a forward drop far
        above the transistor's on-state voltage (3-4 V for a SiC body diode).
        With 0.3 us dead time at 20 kHz that is ~1.2 % duty at several volts and
        tens of amps -- a real, non-negligible loss that is simply absent if the
        diodes are not charged.
        """
        return []

    def clamping_diodes(self) -> list:
        """Diode instance names that carry load current (NPC's clamps, say).

        Their conduction loss is real and is charged to the topology; omitting
        it flatters whichever topology uses them.
        """
        return []

    def bidirectional_pairs(self) -> list:
        """Switch pairs forming one bidirectional device, e.g. a T-type's
        neutral-point pair [("S2a","S3a"), ...].

        Only ONE transistor of such a pair commutates at any transition -- the
        one whose forward direction matches the current -- while the other's
        antiparallel diode simply conducts. Charging both with full switching
        energy doubles the pair's contribution, which on a T-type is the
        difference between it losing to NPC at every frequency and showing the
        real crossover.
        """
        return []

    def recommend_devices(self, library, margin=1.35) -> dict:
        """Pick a device per switch position from `library` by blocking voltage.

        Chooses the lowest-voltage part that still clears the blocking
        requirement with `margin`. This matters because switching energy rises
        steeply with voltage class: putting 1200 V parts everywhere on an NPC
        throws away its advantage, and putting 650 V parts on a T-type rail
        switch is simply not a valid design.
        """
        out = {}
        for name, vblock in self.switch_blocking_voltages().items():
            need = vblock * margin
            cands = [d for d in library.values() if d.V_rating >= need]
            if not cands:
                raise ValueError(
                    f"no device in the library blocks {vblock:.0f} V with "
                    f"{margin:g}x margin (need {need:.0f} V) for {name}")
            out[name] = min(cands, key=lambda d: d.V_rating)
        return out

    def output_node(self) -> str | None:
        """Node a load step or fault is applied to. None = not applicable."""
        return None

    def load_resistance(self) -> float | None:
        return None

    # -- refuse nonsense before the solver sees it -------------------------- #
    # Quantities that cannot be zero or negative in any converter, ever. A
    # negative inductance or a zero capacitance is not a hard operating point,
    # it is not a circuit -- and LTspice does not say so. It grinds. Measured
    # on this machine: a negative hysteresis band ran for over three minutes
    # without converging or erroring, and `sim.DEFAULT_TIMEOUT` would have let
    # it run for ten. An agent sweeping a grid of such points hangs for hours
    # and then reports nothing.
    #
    # Deliberately NOT listed: phi_deg (negative reverses DAB power flow),
    # deadtime and Vf_diode (zero is the idealised default), M and D (bounded
    # per topology below, not merely positive).
    STRICTLY_POSITIVE = (
        "L", "Lb", "Lo", "Lload", "L_dc", "Lmag",
        "C", "Cout", "Cbus", "Cdc_used",
        "Rload", "Ron", "Roff", "Rser", "Rdamp",
        "fs", "f1", "fsw", "hyst", "Pout", "Tsamp",
        "Vac_rms", "Vdc", "Vin", "V1", "V2", "Vout_nom", "Vdc_ref",
        "n", "tmax_div",
    )

    #: {param: (lo, hi)} extra inclusive bounds a topology wants enforced.
    bounds: dict = {}

    def validate(self) -> None:
        """Raise ValueError if these parameters do not describe a circuit.

        Called before a netlist is written, so the cost of rejecting nonsense
        is microseconds rather than a solver timeout. This is the check the
        `Param` dataclass above was written for and never wired to.
        """
        p = self.params()
        for k in self.STRICTLY_POSITIVE:
            v = p.get(k)
            if v is None or isinstance(v, bool):
                continue
            if isinstance(v, (int, float)) and v <= 0:
                raise ValueError(
                    f"{self.name}: {k}={v:g} is not physical -- {k} must be "
                    f"greater than zero. A non-positive value here does not "
                    f"describe a circuit, and the solver will not tell you so.")
        for k, (lo, hi) in self.bounds.items():
            v = p.get(k)
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                continue
            if lo is not None and v < lo:
                raise ValueError(f"{self.name}: {k}={v:g} below minimum {lo:g}")
            if hi is not None and v > hi:
                raise ValueError(f"{self.name}: {k}={v:g} above maximum {hi:g}")
        self.validate_extra()

    def validate_extra(self) -> None:
        """Topology-specific feasibility a bounds table cannot express."""
        return None

    # -- structural lint on the netlist itself ------------------------------ #
    def netlist_structure_problems(self) -> list:
        """Wiring faults a SPICE run will not report as an error.

        A duplicate instance name LTspice rejects loudly. The dangerous faults
        are quiet, and two of them were found in the three-level legs on
        2026-09-13 -- in both cases the simulation completed and returned
        plausible numbers:

          * a component with a blank terminal (the NPC emitted `Dfw2  n1 DFW`
            because a name suffix and an output node were the same parameter)
          * a freewheel diode across the WRONG node pair (the T-type's
            neutral-switch diodes sat on `mida` while the switch they were meant
            to protect sat on `mid`, so the neutral freewheel path was simply
            absent)

        The second is the interesting one: that node had two components on it,
        so no degree-counting rule would flag it. What makes it checkable is
        INTENT -- an antiparallel diode must span the same two power nodes as
        some switch. So that is what is checked, rather than connectivity.
        """
        import re as _re
        problems, seen, dupes = [], set(), []
        switch_pairs, diodes = set(), []

        for raw in self.netlist().splitlines():
            line = raw.strip()
            if not line or line[0] in "*.+":
                continue
            parts = line.split()
            kind, name = parts[0][0].upper(), parts[0]
            if kind not in "RCLDSVIEFGHB":
                continue
            if name in seen:
                dupes.append(name)
            seen.add(name)

            n_term = 4 if kind == "S" else 2
            terms = parts[1:1 + n_term]
            if len(terms) < n_term or any(t in (",", "") for t in terms):
                problems.append(
                    f"{name}: expected {n_term} terminals, got {terms}")
                continue
            if kind == "S":
                switch_pairs.add(frozenset(terms[:2]))
            elif kind == "D":
                model = parts[3] if len(parts) > 3 else ""
                diodes.append((name, frozenset(terms[:2]), model))

        problems += [f"duplicate instance name {d!r}" for d in sorted(set(dupes))]

        # An antiparallel/freewheel diode must sit across a switch. Clamping
        # diodes deliberately do not, so they are identified by model name and
        # exempt -- the exemption is by MODEL, not by a name pattern, so a new
        # diode cannot slip past by being called something else.
        freewheel_models = {"DFW"}
        for name, pair, model in diodes:
            if model.upper() not in freewheel_models:
                continue
            if pair not in switch_pairs:
                problems.append(
                    f"{name} uses the freewheel model but does not span any "
                    f"switch: nodes {sorted(pair)}. An antiparallel diode that "
                    f"is not across its switch is electrically inert, and the "
                    f"run will still complete.")
        return problems

    # -- what a transient study should watch -------------------------------- #
    def saved_traces(self) -> list:
        """Trace names this topology's netlist actually writes to the .raw.

        Read out of the netlist's own `.save` line rather than kept in a
        parallel list, because a parallel list is a second source of truth that
        goes stale the first time someone renames a node. If the netlist saves
        it, this returns it; if it does not, no amount of declaring will help.
        """
        out = []
        for line in self.netlist().splitlines():
            if line.strip().lower().startswith(".save"):
                parts = line.split()
                out += parts[1:]
        return out

    def default_watch(self) -> list:
        """Signals a fault/transient study watches when the caller names none.

        Derived from this topology's own netlist. The previous behaviour --
        a hardcoded ["V(out)", "I(L1)"] in the MCP layer -- was right for the
        buck alone and silently produced an unusable result for every other
        converter, because the checker looked for traces that were never saved.
        """
        saved = self.saved_traces()
        watch = []

        node = self.output_node()
        if node and f"V({node})" in saved:
            watch.append(f"V({node})")
        else:
            volts = [s for s in saved if s.upper().startswith("V(")]
            if volts:
                watch.append(volts[0])

        # Prefer the inductor current over a source current: an output short is
        # visible in I(L) immediately, while the input source current can lag
        # behind it through the bus capacitor.
        ind = [s for s in saved if re.fullmatch(r"I\(L\w*\)", s, re.I)]
        if ind:
            watch.append(ind[0])
        else:
            amps = [s for s in saved if s.upper().startswith("I(")]
            if amps:
                watch.append(amps[0])
        return watch

    def design_notes(self) -> list:
        """Human-readable derivation lines for the report."""
        return []

    def period(self) -> float | None:
        p = self.params()
        fs = p.get("fs")
        return 1.0 / fs if fs else None

    def measure_window(self):
        """(t_start, t_stop) over which steady-state metrics are valid."""
        p = self.params()
        T = self.period()
        if T is None:
            return None
        ns, nm = p.get("Nsettle", 0), p.get("Nmeas", 1)
        return (ns * T, (ns + nm) * T)

    def tol_for(self, quantity) -> float:
        return self.tolerances.get(quantity, self.default_tolerance_pct)

    def summary(self) -> str:
        def fmt(v):
            # params are no longer all numeric -- `modulation` is a string, and
            # blanket ":g" formatting raised ValueError: Unknown format code 'g'
            return f"{v:g}" if isinstance(v, (int, float)) and not isinstance(
                v, bool) else str(v)
        ps = ", ".join(f"{k}={fmt(v)}" for k, v in self.params().items()
                       if not k.startswith("_"))
        return f"{self.name}({ps})"


def fmt_eng(x, unit=""):
    """Engineering notation, because 2.0e-05 H helps nobody read a report."""
    if x is None:
        return "n/a"
    if x == 0:
        return f"0 {unit}".strip()
    pref = {-12: "p", -9: "n", -6: "u", -3: "m", 0: "", 3: "k", 6: "M", 9: "G"}
    e = max(-12, min(9, int(math.floor(math.log10(abs(x)) / 3) * 3)))
    return f"{x/10**e:.4g} {pref[e]}{unit}".strip()
