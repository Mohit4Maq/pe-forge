"""
Build and verify Falstad CircuitJS circuits from Python.

CircuitJS (github.com/pfalstad/circuitjs1, GPLv2) is the easiest way to hand
someone a circuit they can actually poke at.  Its file format is undocumented
and has sharp edges, the worst of which is the MOSFET: `f x1 y1 x2 y2` puts the
GATE at (x1,y1), with drain and source perpendicular at (x2,y2) +/- 16.  Wiring
one as if (x1,y1) were the drain leaves the device silently disconnected and
the circuit still "loads fine".

This module encodes the geometry so you cannot make that mistake, and
`verify()` re-implements Falstad's post rules to catch floating nodes before
you publish a link.

    c = Circuit()
    bat = c.voltage((112, 416), (112, 96), volts=48)
    q = c.nmos(gate=(432, 288), channel=(496, 288))
    c.wire((496, 96), q.drain)
    c.ground((240, 416))
    c.verify()                      # raises on a floating post
    c.save("circuit.txt"); print(c.url())
"""

import math
import urllib.parse
from dataclasses import dataclass, field

BASE = "https://www.falstad.com/circuit/circuitjs.html"
HS = 16                      # MosfetElm.hs — the drain/source offset
GRID = 16


def sign(x):
    return (x > 0) - (x < 0)


def snap(p):
    """Falstad works on a 16 px grid; off-grid posts look wrong and misconnect."""
    return (int(round(p[0] / GRID)) * GRID, int(round(p[1] / GRID)) * GRID)


@dataclass
class Mosfet:
    gate: tuple
    drain: tuple
    source: tuple


@dataclass
class Circuit:
    """A CircuitJS circuit. Coordinates are Falstad's integer screen grid."""
    timestep: float = 5e-11
    lines: list = field(default_factory=list)
    _posts: list = field(default_factory=list)   # (kind, (x, y))

    # ------------------------------------------------------------ internals
    def _add(self, text, posts):
        self.lines.append(text)
        for p in posts:
            self._posts.append((text.split()[0], p))

    def _two(self, code, a, b, tail):
        a, b = snap(a), snap(b)
        self._add(f"{code} {a[0]} {a[1]} {b[0]} {b[1]} 0 {tail}".strip(),
                  [a, b])
        return (a, b)

    # -------------------------------------------------------------- sources
    def voltage(self, minus, plus, volts=5.0, waveform=0, freq=40.0,
                duty=0.5):
        """DC source by default; `minus` is the negative terminal."""
        return self._two("v", minus, plus,
                         f"{waveform} {freq:g} {volts:g} 0 0 {duty:g}")

    def square_rail(self, at, tail_dir=(-64, 0), volts=12.0, freq=1e5,
                    duty=0.5):
        """
        A one-post square-wave rail — the usual way to drive a gate.
        Its single post is at `at`, so pass the gate coordinate directly.
        """
        at = snap(at)
        end = snap((at[0] + tail_dir[0], at[1] + tail_dir[1]))
        self._add(f"R {at[0]} {at[1]} {end[0]} {end[1]} 0 1 {freq:g} "
                  f"{volts:g} 0 0 {duty:g}", [at])
        return at

    # ------------------------------------------------------------- passives
    def resistor(self, a, b, ohms):
        return self._two("r", a, b, f"{ohms:g}")

    def capacitor(self, a, b, farads, v0=0.0):
        return self._two("c", a, b, f"{farads:g} {v0:g}")

    def inductor(self, a, b, henries, i0=0.0):
        return self._two("l", a, b, f"{henries:g} {i0:g}")

    def diode(self, anode, cathode, model="default"):
        a, k = snap(anode), snap(cathode)
        self._add(f"d {a[0]} {a[1]} {k[0]} {k[1]} 2 {model}", [a, k])
        return (a, k)

    def wire(self, a, b):
        return self._two("w", a, b, "")

    def ground(self, at, drop=48):
        at = snap(at)
        end = (at[0], at[1] + drop)
        self._add(f"g {at[0]} {at[1]} {end[0]} {end[1]} 0", [at])
        return at

    # --------------------------------------------------------------- MOSFET
    def nmos(self, gate, channel, vt=4.0, beta=0.02, pchannel=False):
        """
        N-channel MOSFET.

        `gate` is the gate post; `channel` is the centre of the channel.  The
        line gate->channel must be horizontal or vertical and at least 32 long.
        Drain and source come back on the returned object — never assume them.
        """
        g, c = snap(gate), snap(channel)
        dx, dy = c[0] - g[0], c[1] - g[1]
        if dx and dy:
            raise ValueError("mosfet gate lead must be axis-aligned")
        dn = math.hypot(dx, dy)
        if dn < 32:
            raise ValueError(f"mosfet gate lead too short ({dn}); use >= 32")
        drain, source = self._mosfet_ds(g, c)
        flags = 1 if pchannel else 0
        self._add(f"f {g[0]} {g[1]} {c[0]} {c[1]} {flags} {vt:g} {beta:g}",
                  [g, source, drain])
        return Mosfet(gate=g, drain=drain, source=source)

    @staticmethod
    def _mosfet_ds(g, c):
        """Falstad's own geometry: src[0] and drn[0] from interpPoint2()."""
        dsign = sign(c[0] - g[0]) if c[1] == g[1] else sign(c[1] - g[1])
        hs2 = HS * dsign
        gx, gy = (c[1] - g[1]), (g[0] - c[0])
        k = -hs2 / math.sqrt(gx * gx + gy * gy)
        src = (math.floor(c[0] + k * gx + 0.48), math.floor(c[1] + k * gy + 0.48))
        drn = (math.floor(c[0] - k * gx + 0.48), math.floor(c[1] - k * gy + 0.48))
        return drn, src

    # ---------------------------------------------------------------- scope
    def scope(self, spec="5 64 0 4099 100 0.05 0 2 5 3"):
        self.lines.append(f"o {spec}")

    # ------------------------------------------------------------- checking
    def verify(self, strict=True):
        """
        Every post must share its coordinate with at least one other post, and
        the circuit must be grounded.  Returns a list of problems; raises when
        strict and any are found.
        """
        counts = {}
        for _, p in self._posts:
            counts[p] = counts.get(p, 0) + 1
        problems = [f"floating {kind} post at {p}"
                    for kind, p in self._posts if counts[p] < 2]
        if not any(l.startswith("g ") for l in self.lines):
            problems.append("no ground element")
        if problems and strict:
            raise ValueError("circuit is not connected:\n  "
                             + "\n  ".join(problems))
        return problems

    # ---------------------------------------------------------------- output
    def text(self):
        head = (f"$ 1 {self.timestep:g} 10.634267539816555 50 5 50 "
                f"{self.timestep:g}")
        return "\n".join([head] + self.lines) + "\n"

    def url(self):
        return BASE + "?cct=" + urllib.parse.quote(self.text(), safe="")

    def save(self, path):
        with open(path, "w") as fh:
            fh.write(self.text())
        return path
