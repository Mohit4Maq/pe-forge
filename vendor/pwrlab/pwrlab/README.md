# pwrlab

Power-electronics design, simulation and **verification**.

The premise: a simulation result is a *hypothesis* until it has been checked
against an independent closed-form model and shown to be insensitive to the
solver's numerical settings. This package exists because, during its own
development, a dual-active-bridge netlist returned **+154 % power error with no
error message, no convergence warning, a clean exit and a plausible waveform**.
Three separate bugs each produced confident, wrong numbers. Only cross-checking
against theory caught them.

## Quick start

```python
from pwrlab import Study
from pwrlab.topologies.dab import DAB

r = Study(DAB(phi_deg=45)).run()   # simulate + full check battery
print(r.report())
print(r.ok)                        # False if any ERROR-level check failed
```

```bash
.venv/bin/python -m pwrlab.cli doctor
.venv/bin/python -m pwrlab.cli run buck --Vin 48 --D 0.6 --Rload 4
.venv/bin/python -m pwrlab.cli sweep dab --phi_deg 10,20,30,45,60,90 --csv out.csv --html out.html
```

## What it checks, on every result

| check | catches |
|---|---|
| artefacts written | the macOS stub that exits 0 and writes nothing |
| closed-form cross-check | wrong topology behaviour, wrong startup state |
| timestep convergence (halve `tmax`, re-run) | a result that is a solver artefact |
| steady state (period-to-period drift) | measuring during a transient |
| power balance | sign errors; `Pout > Pin` is impossible |
| DC offset | startup transients biasing an AC quantity |
| conduction mode | an analytical model applied in the wrong regime |
| topology-specific (ZVS, ripple, ...) | physics the generic checks cannot see |

`tests/test_catches_regressions.py` re-injects each historical bug and asserts
the checks still catch it.

## Scenario analyses

```python
from pwrlab.montecarlo import ToleranceStudy, Tol
from pwrlab.losses import LIBRARY, analyse_losses, required_heatsink
from pwrlab.transients import TransientStudy, StartUp, LoadStep, ShortCircuit
```

**Tolerance.** Run them in cost order: `sensitivity()` costs 2N+1 simulations and
shows which tolerances matter at all; `corners()` costs 2^k for the few that do;
`monte_carlo()` costs hundreds and gives the distribution. A Monte Carlo sample
whose *checks* failed is excluded and counted, never averaged in silently.

**Losses.** The power stage is simulated with ideal switches and losses are
computed in post-processing from the resulting currents plus datasheet values.
Rds(on) rises with Tj and Tj depends on loss, so the two are solved together as a
fixed point — **ignoring that coupling understates conduction loss by 51 %** on
the DAB example. Turn-on energy is waived for switches the topology declares
soft-switched: on the DAB that is 14.6 W versus 40.7 W for the hard-switched
secondary. `required_heatsink()` bisects on Rth with loss re-solved at each step,
because dividing a temperature budget by a fixed loss undersizes the sink.

Devices in `LIBRARY` are **representative, not real part numbers** — substitute
datasheet values before trusting an absolute number.

**Faults and transients.** Events are netlist transformations, so they apply to
any topology declaring an output node: `StartUp` (strips the `.ic` seeding to
expose inrush), `LoadStep`, `ShortCircuit`. Metrics are peak stress, excursion,
and settling time — `.meas` averages are the wrong instrument when the point is
the excursion.

```bash
.venv/bin/python -m pwrlab.cli mc buck --outputs V_out,V_ripple --tol_L 0.2 --tol_Cout 0.2 -n 100
.venv/bin/python -m pwrlab.cli loss dab --device SiC_1200V_40m --rth 0.2 --size-heatsink 150
.venv/bin/python -m pwrlab.cli fault buck --event short --at 1.5e-3 --watch "I(L1)" --limit 100
```

## MCP server

Lets an agent drive all of the above. Register it with:

```json
{"mcpServers": {"pwrlab": {
  "command": "<pe-forge>/.venv/bin/python",
  "args": ["-m", "pwrlab.mcp_server"],
  "cwd": "<pe-forge>/vendor/pwrlab"}}}
```

(`bootstrap.sh` registers it for you). Nine tools: `list_topologies`, `doctor`,
`analyze`, `sweep`, `tolerance`, `losses`, `fault`, `list_devices`, `job_status`.

Two design rules, both load-bearing:

**No tool returns a bare number.** Every result carries `usable`, a one-line
`verdict`, the full `checks` list and any `warnings`. A driving agent cannot see
intermediate state — it gets JSON and will report what is in it — and a 154 %
wrong DAB result looks exactly like a right one. When `usable` is false the
numbers are still returned, for diagnosis, but the verdict says plainly that they
must not be used.

**Anything slow returns a job id.** A sweep takes minutes and would blow through
a client timeout, so `sweep`, `tolerance`, `losses` and `fault` return
immediately and are polled with `job_status`.

Errors are raised as `ToolError`, never bare exceptions: MCP 2.x masks an
unexpected exception as `"Error executing tool <name>"` with no detail, which
leaves an agent unable to correct itself. Every message names the valid options.

`tests/test_mcp.py` drives the server over real stdio — transport, schema
generation and JSON round trip included — not by calling the decorated functions,
which would skip exactly the parts that break.

## Adding a topology

Subclass `Topology` and supply four things. The closed-form model is not
optional — it is what makes the result checkable.

```python
@dataclass
class MyConverter(Topology):
    measurements = ["Vout", "ILrms"]
    tolerances   = {"V_out": 2.0}

    def params(self)     -> dict: ...   # everything the netlist references
    def netlist(self)    -> str:  ...   # SPICE text
    def analytical(self) -> dict: ...   # closed-form predictions
    def extract(self, res, waves) -> dict:   # same keys as analytical()

    def topology_checks(self, ex, waves, res):   # optional
        return [...]
```

Where closed form is only approximate (FHA for resonant tanks), widen
`tolerances` and say so — the report states the criterion it judged by rather
than implying precision it does not have.

## Accuracy achieved

| topology | quantity | worst error vs closed form |
|---|---|---|
| DAB (phi 10..90 deg) | average power | **0.078 %** |
| DAB | inductor RMS current | 0.53 % |
| Buck (D 0.25..0.75, Rload 0.5..100) | output voltage | **0.0002 %** |
| Buck | inductor ripple | 0.011 % |
| Buck | inductor RMS current | 0.019 % |
| Buck | output voltage ripple | 0.38 % |
| H-bridge SPWM (uni + bipolar) | V1, Vrms, I1, P, DPF | **0.075 %** |
| T-type / NPC, single leg | V1, Vrms, I1, P | 0.10 % |
| T-type / NPC, three phase | V1, V_ll, I1, P | **0.044 %** |
| T-type vs NPC agreement | V1, I1, P | 0.012 % |
| Three-level NP ripple | 2f (1ph) / 3f (3ph) excursion | 1.4 % / 5.0 % |
| Three-level DC-link capacitor | RMS ripple current | **0.45 %** |
| Totem-pole PFC | Vout, 2f ripple, P, I_rms | 0.22 % |

## Lessons the code encodes

Each of these cost real debugging time and is commented at the point it matters:

- **`uic` silently voids `.ic`** and zeroes every inductor (-41.9 % power).
- **The DC operating point shorts inductors** across a live bridge unless `.ic`
  covers *every* winding (+152 %).
- **Ripple must be measured within a cycle**, not as max-min over the whole
  window — that conflates ripple with drift (7.7 % error on the buck).
- **Seed state at the start of the on-time, not the cycle mean.** Seeding the
  mean leaves a dI/2 mismatch that rings a high-Q LC for milliseconds.
- **The textbook output-ripple formula** `dI/(8 fs C) + dI*ESR` overestimates by
  16 %: the two terms are a quarter cycle apart, not coincident.
- **A synchronous buck never enters DCM** — the low-side switch conducts both
  ways. Applying the DCM formula predicted 31.98 V where it delivers 24.00 V.
- **Never exceed 3 concurrent LTspice instances** on the macOS Wine stack: 4 and
  6 workers are both slower than sequential.

## Status

Implemented: DAB (single phase shift), synchronous buck, H-bridge SPWM inverter
(unipolar and bipolar), three-level T-type and NPC (single leg and three phase,
with a derived neutral-point ripple model and capacitor auto-sizing), and a
totem-pole bridgeless PFC with hysteresis current control.
Also implemented: sensitivity/corner/Monte-Carlo tolerance analysis,
electrothermal loss modelling with heatsink sizing, and fault/transient studies.

Also implemented: an MCP server (see above).

Held, pending your review of the above end to end: DAB variants (DPS/EPS/TPS),
boost/buck-boost/flyback, LLC/PSFB/CLLC, and Vienna rectifier. Held until that is checked
end to end: DAB variants (DPS/EPS/TPS), boost/buck-boost/flyback, LLC/PSFB/CLLC.

### Line-frequency notes

Two-timescale converters need machinery the switching-only ones do not:

- `harmonics.py` resamples onto a uniform grid before any FFT, because LTspice's
  variable timestep would otherwise weight the switching edges far more heavily
  than the flat intervals and corrupt every amplitude.
- The analysis window is an exact integer number of line periods, so harmonics
  land on FFT bins and no window function is needed. A window off by 0.1 % of a
  period smears the fundamental and inflates THD.
- True power factor is reported alongside displacement factor. Quoting only DPF
  is the classic way to make a distorted current look acceptable.
- Single-phase three-level legs have a LARGE low-frequency neutral-point
  excursion (14.7 mF needed here for 2 %, versus 3.52 mF three-phase). That is
  physics, not a modelling artefact.
