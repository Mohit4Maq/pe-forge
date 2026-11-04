# CircuitJS save format

One element per line. Every line starts with a type character, then
`x1 y1 x2 y2 flags`, then per-element fields. Coordinates are integers on a
16 px grid. Verified against circuitjs1 source (`CircuitElm.dump()` and each
element's `(…, StringTokenizer st)` constructor).

## Header

```
$ 1 <timestep> 10.634267539816555 50 5 50 <timestep>
```

## Elements

| Type | Line | Notes |
|---|---|---|
| `w` | `w x1 y1 x2 y2 flags` | wire |
| `r` | `r x1 y1 x2 y2 flags resistance` | |
| `c` | `c x1 y1 x2 y2 flags capacitance voltdiff [v0] [Rseries]` | `Rseries` only read when the resistance flag is set |
| `l` | `l x1 y1 x2 y2 flags inductance current [i0] [Isat]` | |
| `d` | `d x1 y1 x2 y2 flags modelname` | current flows point1 → point2; `default` is a fine model |
| `v` | `v x1 y1 x2 y2 flags waveform freq maxV bias phase duty` | `waveform` 0 = DC, 1 = square. point1 is the **negative** terminal |
| `R` | `R x1 y1 x2 y2 flags waveform freq maxV bias phase duty` | rail — **one post**, at point1 |
| `g` | `g x1 y1 x2 y2 flags` | ground — **one post**, at point1 |
| `f` | `f x1 y1 x2 y2 flags vt beta` | MOSFET — see below |
| `o` | `o …` | scope; not an element, no posts |

## The MOSFET trap

`MosfetElm.getPost()` returns:

```java
(n == 0) ? point1 : (n == 1) ? src[0] : (n == 2) ? drn[0] : body[0];
```

So **post 0 is the gate, at `(x1,y1)`**. Drain and source are computed in
`setPoints()` as perpendicular offsets of `±hs` (`hs = 16`) about `(x2,y2)`:

```java
int hs2 = hs * dsign;                       // dsign from the point1→point2 direction
if ((flags & FLAG_FLIP) != 0) hs2 = -hs2;   // flags = 0 keeps the orientation
interpPoint2(point1, point2, src[0], drn[0], 1, -hs2);
```

Consequences:

- The gate lead runs from `(x1,y1)` to `(x2,y2)`, so **that line is the gate,
  not the channel**. For a drain-up/source-down device the line must be
  **horizontal**.
- Drain and source land on the perpendicular through `(x2,y2)`, 16 px either
  side. For a horizontal gate lead running left→right, **drain is 16 above and
  source is 16 below**.
- The gate lead must be at least 32 long or the device cannot be drawn.

`Circuit.nmos()` in `assets/falstad.py` computes all of this and returns
`.gate`, `.drain`, `.source`. Use them.

## Sharing

```
https://www.falstad.com/circuit/circuitjs.html?cct=<url-encoded file text>
```

URLs run to roughly 1 kB for a small circuit, which is fine for a description
or README. For anything large, ship the `.txt` and tell people to use
**File → Import From Text**.
