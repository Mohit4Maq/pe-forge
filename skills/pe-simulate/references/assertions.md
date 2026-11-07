# ngspice assertions (`forge spice check`)

For the small analog circuits pwrlab does not cover: current-sense gain and filter corner, a divider into an ADC,
gate-drive RC, UVLO / OVP comparator thresholds, aux-supply feedback, NTC bias. Power stages stay with pwrlab.

## The block (in the sheet brief)

```
## Simulation
scope: nets ISENSE_P, ISENSE_N, ADC_IOUT, GND
analysis: ac 10 1e7
source: ISENSE_P = 0.05 ac 1
assert: gain(ADC_IOUT, ISENSE_P) between 19.5 and 20.5 at 1k
expect: 20
assert: f3db(ADC_IOUT) between 180k and 220k
assert: V(ADC_IOUT) < 3.3 at op
```

| Line | Meaning |
|---|---|
| `scope: nets A, B, ...` | the nets of the sub-circuit. A part is simulated only when **every** net it touches is in scope (ground always is). Parts that reach outside are left out: the sources define the boundary. |
| `analysis:` | `op` · `dc <src> <start> <stop> <step>` · `ac <fstart> <fstop>` (50 points / decade) or `ac dec <n> <f1> <f2>` · `tran <step> <stop>` |
| `source: NET = <dc> [ac <mag>] [pulse(...) / sin(...)]` | an ideal voltage source from NET to ground (named `V_NET`) |
| `assert: <measure> between A and B [at X]` or `<measure> < A` / `> A` | the bound. Numbers take SI suffixes (`1k`, `4u7` is not accepted here: write `4.7u`) |
| `expect: <value>` | the closed-form value of the assertion above it. ngspice must agree within 2 %, else the result is PROVISIONAL even inside the bound (Rule 1: no number without a second source) |

Measures: `V(NET)`, `I(NET)` (current of the source on NET), `gain(OUT, IN)` (ratio; magnitude in ac),
`f3db(NET)` (frequency where the magnitude falls 3 dB below its value at the lowest swept frequency: low-pass and
band-limited responses), `peak(NET)` (maximum), `settle(NET, 2%)` (time after which NET stays within 2 % of its
final value). `at X` is a frequency (ac), time (tran) or `op`; without it the last point (tran / dc) or the first
point (ac, op) is used.

## Models

Resistors, capacitors and inductors get KiCad's ideal models from their Value. Any other part in scope needs a model:
KiCad's own `Sim.*` fields, or the MC_Parts fields `Sim_Model` (`<element letter> <model or subckt name> | <.model or
.subckt card> | ...`) and `Sim_Pins` (the symbol pin numbers in SPICE node order, e.g. `2 1` for anode, cathode). A
part without a model makes the block's assertions **SKIPPED** with the part named, and **FAIL** under `--strict`.

## Results

`forge spice check [--module M-xx] [--strict]` writes `<module>/Simulations/<brief>_spice.md`: one row per
assertion (ACCEPTED / PROVISIONAL / REJECTED / NOT RUN), the SVR table and the deck that reproduces it. Gate G5
counts the record; a REJECTED row keeps G5 closed. Without ngspice the check is skipped with an install hint,
never passed.
