# ngspice assertions (`forge spice check`)

For the small analog circuits pwrlab does not cover: current-sense gain and filter corner, a divider into an ADC,
gate-drive RC, UVLO / OVP comparator thresholds, aux-supply feedback, NTC bias. Power stages stay with pwrlab.

## The block (in the sheet brief)

```
## Simulation
scope: nets ISENSE_P, ISENSE_N, ADC_IOUT, P3V3, GND
analysis: ac 10 1e7
source: ISENSE_P = 0.05 ac 1
source: P3V3 = 3.3
assert: gain(ADC_IOUT, ISENSE_P) between 19.5 and 20.5 at 1k
expect: 20
assert: f3db(ADC_IOUT) between 180k and 220k
expect: 200k ± 5%
assert: V(ADC_IOUT) <= 3.3 at op
expect: 1.0
```

| Line | Meaning |
|---|---|
| `scope: A, B, ...` | the nets of the sub-circuit. A part is simulated when **all** its nets are in scope. A part that touches the scope **and** a net outside it **stops the block** with its name and the outside nets: leaving it out would silently change the circuit. Add those nets (an amplifier's supply, with a `source:`), or list the part under `ignore:`. So does a drawn part on a scope net that KiCad's export leaves out (excluded from simulation); DNP parts are listed as not simulated. NC pins never stop a block |
| `ground: NET` | the 0 V reference. Optional when exactly one ground-like net (GND, AGND, PGND, GND_ISO, 0V, VSS, ...) is in scope, or the design has only one. With several in scope the block asks you to name one |
| `ignore: J1, TP1` | parts deliberately left out (connectors, test points, a part that does not matter here); listed in the record |
| `analysis:` | `op` · `dc <source net> <start> <stop> <step>` · `ac <fstart> <fstop>` (50 points / decade) or `ac dec <n> <f1> <f2>` · `tran <step> <stop>` |
| `source: NET = <dc> [ac <mag>] [pulse(...) / sin(...) / pwl(...)]` | an ideal voltage source from NET to ground. NET must exist on the schematic; a block needs at least one source, a net takes one source, and a source drawn on the sheet on the same net stops the block |
| `assert: <measure> between A and B [at X]`, `< A`, `<= A`, `> A`, `>= A` | the bound (strict or not, as written) |
| `expect: <value> [± <tol> or ± <pct>%]` | the closed-form value of the assertion right above it (default tolerance 2 %; an expected 0 needs an absolute tolerance) |

Numbers take SI suffixes: `f p n u m k M G`, also `meg`. **`M` is mega and `m` is milli.** forge converts every
number before ngspice sees it, so ngspice's own "M = milli" never applies. `4k7`, `1m5`, `5.` and units (`10uF`,
`3.3V`) are accepted; a bare `F` or `H` is the unit, femto is a lower-case `f`.

## Measures

| Measure | Meaning |
|---|---|
| `V(NET)` | node voltage (ac: magnitude) |
| `I(NET)` | current the source on NET **delivers into the circuit** (positive when it supplies); ac: magnitude |
| `gain(OUT, IN)` | V(OUT) / V(IN) at the point (ac: magnitude ratio); fails when V(IN) is 0 there |
| `f3db(NET)` | upper -3 dB frequency, relative to the response at the sweep's low end (conventional, also for a peaking low-pass) |
| `f3db_low(NET)` | lower -3 dB frequency, relative to the response at the sweep's high end |
| `f3db(NET, peak)`, `f3db_low(NET, peak)` | relative to the passband peak, for band-pass responses |
| `peak(NET)` | maximum over the sweep or transient |
| `settle(NET, 2%)` | time after which NET stays within 2 % of its final value; the last 20 % of the run must stay within a quarter of the band (run at least twice the expected settling time) |

`at X` is a frequency (ac), a time (tran), a sweep value (dc), or `op` for the DC operating point, which is computed
on its own even in an ac or tran block. Without `at`: ac uses the lowest frequency, dc and tran the last point, op
the operating point. Net names may contain parentheses (`V(Net-(D1-A))`) and sub-sheet slashes (`sub/V+`). A measure may not read a net a
source drives (the input of `gain()` excepted). Sheet directives (`.include`, `.model`, `.ic`, `.subckt` ...) are kept
and recorded; analysis, `.save` and `.control` lines from the sheet are not used.

## Grades

| Grade | When |
|---|---|
| ACCEPTED | inside the bound, agrees with `expect:` within its tolerance, ran clean; tran: moves under 1 % at half the time step |
| PROVISIONAL | inside the bound, but no `expect:` (no second source), a closed-form disagreement, or time-step sensitivity |
| REJECTED | outside the bound, does not parse, the block cannot run as the drawn circuit, or ngspice failed |
| NOT RUN | a part in scope has no SPICE model, or KiCad's export failed |

Design values rest only on ACCEPTED results; PROVISIONAL ones carry their label and a bench-test item.

## Models

Resistors, capacitors and inductors get KiCad's ideal models from their Value. Any other part in scope needs a model:
KiCad's own `Sim.*` fields, or the MC_Parts fields `Sim_Model` (`<element letter> <model or subckt name> | <.model or
.subckt card> | ...`) and `Sim_Pins` (the symbol pin numbers in SPICE node order, e.g. `2 1` for anode, cathode).
Without one the block is NOT RUN, and `--strict` fails.

## Records and gate G5

`forge spice check [--module M-xx] [--strict]` writes `<module>/Simulations/<brief>_spice.md`: one row per assertion
(parse failures included), the SVR (ngspice version, log checks, time-step check, what it does not check) and the deck
that reproduces it. Its frontmatter carries `result:` and `inputs:`, a hash of the Simulation block, the sheet and the
tool version. Gate G5 refuses a record that is REJECTED or NOT RUN, and one that is **stale** (the block, any sheet of the
hierarchy, an included model file or the tool changed, or the brief points at another sheet). A PROVISIONAL record
passes with a note: its bench tests must be in the test plan. Without ngspice the check is skipped with an install hint, never passed.
