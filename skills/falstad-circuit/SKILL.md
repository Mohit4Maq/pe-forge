---
name: falstad-circuit
description: Build, verify and share interactive circuit simulations using Falstad CircuitJS. Generates the .txt circuit file and a shareable falstad.com link from Python, and checks the netlist for floating nodes before it ships. Use when someone wants an interactive circuit, a simulator link for a README, video description or lesson, a CircuitJS file, or asks to "show the circuit" in a way people can play with.
---

# Falstad circuits from Python

Falstad's CircuitJS is the fastest way to hand someone a circuit they can run
themselves. Its save format is undocumented and has traps. `assets/falstad.py`
encodes the format so you can build circuits declaratively and verify them.

## Use it

Copy `assets/falstad.py` next to your script (or add its directory to
`sys.path`), then build the circuit and **always verify before sharing**:

```python
from falstad import Circuit

c = Circuit()
c.voltage((112, 416), (112, 96), volts=48)     # minus, plus
c.capacitor((240, 96), (240, 416), 10e-6)
c.inductor((304, 96), (400, 96), 10e-9)        # parasitic loop inductance

q = c.nmos(gate=(432, 288), channel=(496, 288))
c.square_rail(q.gate, volts=12, freq=100e3)    # gate drive
c.wire(q.source, (496, 416))
c.wire(q.drain, (592, 272))

c.ground((240, 416))
c.verify()                                     # raises if anything floats
c.save("circuit.txt")
print(c.url())
```

## Rules

1. **Never hand-write element lines.** Use the builder. The format's traps are
   encoded there; a hand-written line reintroduces them.
2. **Never assume a MOSFET's drain and source.** `nmos()` returns them. In
   Falstad, `f x1 y1 x2 y2` puts the **gate** at `(x1,y1)` and drain/source
   perpendicular at `(x2,y2) ± 16`. Wiring it as if `(x1,y1)` were the drain
   leaves the device disconnected — and the file still loads without error, so
   nothing tells you.
3. **Always call `verify()` before sharing a link.** It re-implements Falstad's
   post geometry and fails on any floating post or a missing ground. A link
   that opens to a dead circuit is worse than no link.
4. **Coordinates snap to a 16 px grid.** The builder snaps for you; keep your
   own layout on multiples of 16 so it stays readable.
5. **Derive values from the source of truth.** If a simulation or datasheet set
   the values, read them from that file rather than retyping, so the
   interactive circuit cannot drift from whatever else you published.
6. **Attribution.** CircuitJS is by Paul Falstad, GPLv2
   (github.com/pfalstad/circuitjs1). Linking to falstad.com is free of any
   obligation; bundling or modifying the simulator is not — say so if the user
   plans to self-host.

## Verifying without a browser

`c.verify()` is a structural check, not a visual one. It proves the netlist is
connected and grounded; it does not prove the schematic is laid out prettily or
that the simulation converges. If a browser is available, open the URL and
confirm. If not, say plainly that the circuit was verified structurally but not
opened — do not imply more than you checked.

## Element reference

See `references/format.md` for the dump-line format of each element, the
argument order, and which fields are optional.
