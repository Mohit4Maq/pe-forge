---
name: pwrlab
description: Simulate, verify and size power-electronics converters with LTspice - DAB, buck, H-bridge inverter, T-type/NPC three-level (1ph and 3ph), totem-pole PFC. Use for any question about converter performance, component sizing (DC-link and output capacitors, inductors, heatsinks), losses and junction temperature, efficiency, ZVS, THD and power factor, ripple current or voltage, tolerance and worst-case analysis, or fault and startup transients. Every result is cross-checked against a closed-form model before it is reported.
---

# pwrlab

Answers power-electronics design questions with simulations that are **validated
before they are reported**. Every result carries a `usable` flag; a result that
failed its checks must never be quoted as fact.

Project root: `$PE_FORGE/vendor/pwrlab`  ·  Python: `.venv/bin/python`

## The one rule

**Never report a number whose checks did not pass.** A wrong power-electronics
simulation looks exactly like a right one - during this project's development a
netlist returned +154 % power error with no error message, no warning and a
plausible waveform. That is why every result is compared against closed-form
theory. If `usable` is false, say so and diagnose; do not present the numbers.

## What the payload now guarantees

Every tool result carries a `provenance` block:

- `model_version` - the git SHA that produced it
- `dirty` - whether the working tree had uncommitted changes
- `toolchain_hash` - simulator + numpy/pandas/spicelib identity
- `evidence_eligible` - **false** if dirty; such a result may be discussed but
  must not be cited as validation evidence, and `why_not_evidence` says so

Two further rules the payload now enforces, so you can rely on them:

- **No `Infinity` or `NaN`.** A value that was never reached comes back as
  `null` with a sibling `<name>__note` explaining why. Report the reason, not a
  number.
- **No silently skipped checks.** A property that could not be assessed appears
  as a FAILING check whose detail starts `NOT ASSESSED:`. Treat that as "unknown",
  never as "fine".

Non-physical parameters are refused in microseconds before any simulation runs
(negative inductance, zero capacitance, a hysteresis band wider than the current
it shapes, an output below the AC peak). If you get a `ValueError`, the message
names the physical reason - pass it on rather than retrying.

## Validated against theory is not validated against hardware

`analyze`, `sweep`, `losses` and friends compare the simulation against a
**closed-form model of an idealised circuit**. That catches real bugs and it is
circular with respect to reality: a model can agree with its own theory to 0.01 %
and describe a converter nobody can build.

`benchmark` is the other question - does it agree with **measured hardware from a
published reference**. Results come back per quantity with the MODE of agreement
each one earned:

| Mode | What it means |
|---|---|
| `MATCH` | within tolerance of a measured value. Strongest claim available. |
| `BOUNDED_OPTIMISM` | model may be better by no more than a stated factor. Being WORSE than hardware always fails. Expected for loss and distortion. |
| `TREND` | reproduces how a quantity MOVES, even where the absolute value does not match. |
| `SPEC_BOUND` | satisfies a published LIMIT ("THD < 3 %"). **Weakest** - shows the model is not contradicted by the spec, not that it agrees with hardware. |
| `BRACKET` | the measurement falls inside a sweep of a missing-physics parameter. |

**Never write "validated against hardware" for a quantity that passed only on
TREND, BOUNDED_OPTIMISM or SPEC_BOUND.** Name the mode instead.

Current state: against TI TIDUEZ3B (4 kW GaN totem-pole PFC) the model's power
factor matches measured hardware to within 0.0023 across a 10:1 load range, and
its current THD is 9-19x optimistic. Both are true; only the first is a match.

## Some quantities are chaotic - never quote a single point

The hysteresis PFC's current THD moves **44 % for a 0.015 % change in load
resistance**. Both values are converged and the simulation is bit-reproducible,
so this is not noise in the solver - it is free-running hysteresis beating
against the line. A single THD number from `pfc` measures one beat pattern, not
the converter.

Use an ensemble median and quote its spread (measured: 19 % at full load, 60 % at
10 % load). `assumptions <topology>` lists which quantities this applies to.

## Before a big sweep

Call `validate_params`. A non-physical point does not fail fast in LTspice - it
**stalls the solver for minutes** (a negative hysteresis band ran over three
minutes before this check existed). `check_netlist` catches wiring faults that a
successful run will not report: blank terminals, duplicate names, and freewheel
diodes that do not actually span a switch.

## Step 1 - ask before simulating

Most questions arrive underspecified. A DC-link capacitor answer changes by an
order of magnitude with power and switching frequency, so **ask first**, using
AskUserQuestion with 2-4 concrete options. Offer a sensible default as the first
option so the user can just accept it.

What you almost always need:

| Question | Why it changes the answer |
|---|---|
| Bus/input voltage and output voltage | sets currents and device ratings |
| Power or load | ripple current scales with it, roughly linearly |
| Switching frequency | sets ripple, loss split, and the capacitor's frequency band |
| Line frequency, 1-phase or 3-phase | three-phase moves DC-link ripple to 3f/HF and needs far less capacitance |
| Load power factor | strongly affects DC-link and NP ripple |
| Modulation index | sets the fundamental and the duty distribution |

Do not ask about things with no bearing on the question. If the user says "just
use something typical", pick defaults, **state them explicitly in the answer**,
and continue.

## Step 2 - map the question to a tool

| The user asks about | Use |
|---|---|
| DC-link capacitor: ripple current, capacitance, voltage rating | `dc_link_capacitor` (three-level only) |
| one operating point: power, efficiency, ripple, ZVS, THD | `analyze` |
| how something varies with a parameter | `sweep` |
| which tolerances matter / worst case / spread | `tolerance` (sensitivity first, then monte_carlo) |
| device loss, junction temperature, heatsink size | `losses` |
| inrush, load step, short circuit, startup | `fault` |
| what can this do / what are the defaults | `list_topologies`, `list_devices` |
| "is the toolchain working?" | `doctor` |
| efficiency, THD, loss, or "why doesn't this match the paper?" | **`assumptions` first**, then the analysis tool |

If the MCP server is registered, call these as tools. If not, the same
capabilities are in the library and CLI - see `USAGE.md` in the project root for
the exact commands. **`sweep`, `tolerance`, `losses`, `fault` and a verified
`dc_link_capacitor` return a job id**: poll `job_status` until `done`. They take
seconds to minutes; say so rather than appearing to hang.

Topology names: `dab`, `buck`, `hbridge`, `ttype`, `npc`, `ttype3ph`, `npc3ph`,
`pfc`.

## When no tool fits the question

Most questions map to a tool. When one does not, escalate in this order and stop
at the first tier that answers it. **The check requirement applies at every tier**
- a new number still needs something independent to check it against, or it is
not an answer.

**Tier 1 - a tool exists.** Use it.

**Tier 2 - the measurement machinery exists but is not exposed.** Write a short
script against the library. `pwrlab.harmonics` (Spectrum, THD, power_metrics,
parseval_residual), `pwrlab.waveforms` (Waves: mean, rms, cycle_means,
cycle_ripple) and `pwrlab.losses` (switch_metrics) cover most waveform questions.
Run `Study(topo).run()` first and refuse to proceed if `.ok` is false. Say which
tier you used, and validate what you compute - for a spectrum that means
reporting the Parseval residual.

**Tier 3 - a new quantity on an existing topology.** Add a key to the topology's
`extract()`, a closed-form prediction to `analytical()`, and a tolerance. That is
how DC-link ripple current was added, and doing it properly caught a real
modelling error worth 20 %. Do not add a measurement without a prediction to
check it against.

**Tier 4 - a new topology.** Subclass `Topology` and supply `params`, `netlist`,
`analytical`, `extract`. See `pwrlab/topologies/buck.py` for the simplest
example.

For tiers 3 and 4, say plainly that you are extending the framework, and add a
test to `tests/` so the new capability cannot silently regress.

### Validating a quantity that has no closed form

Build the ideal waveform from the modulation or switching logic itself, in numpy,
and compare the simulation against that. It is independent of SPICE and
verifiable line by line. This is how neutral-point ripple, DC-link capacitor
current and the PWM spectrum are all checked - and it is preferable to quoting a
formula from memory, which cannot be verified in place.

## Step 3 - report with the evidence

Give the number, the check verdict, and what it was checked against. A good
answer looks like:

> **13.15 A RMS per capacitor**, verified against the analytical pulse-train
> model to +0.45 %.
> Split by frequency: 4.65 A below 5 kHz, 12.24 A above - so check the part's
> derating curve at switching frequency, not its 120 Hz rating.
> Assumptions: 800 V bus, M = 0.8, 10 kHz carrier, 10 ohm + 10 mH load
> (PF 0.954), 4.66 kW.

Always state the assumptions you used. Always give the frequency split for
ripple current - a single RMS figure cannot select a capacitor.

## Step 4 - write the results to files

Simulation results belong on disk, not only in chat. Write to the current working
directory unless told otherwise:

Write them to **the directory the user is working in**, not to the pwrlab
project — simulation scratch goes to `$PE_FORGE/vendor/pwrlab/work/`
automatically and is pruned, but deliverables belong with the user's work.

- `<topic>_results.csv` for any sweep or multi-point study
- `<topic>_report.md` for the summary, via `pwrlab.report.result_markdown` or
  `sweep_markdown`
- `<topic>.html` via `pwrlab.report.html_report` when the user wants something
  shareable, with the full check table

Tell the user the file paths. For a sweep, a plot usually earns its place -
`plot_dab.py` is an example to copy for other topologies.

## Idealised by default - say so

**Validation compares the simulation against a closed-form model of an IDEALISED
circuit. It does not establish that the circuit matches hardware.** The check
battery cannot see the difference, so you must.

Before quoting **efficiency, THD, loss, or anything a paper would report**, call
`assumptions` and pass on what it says. In particular:

- Three-level topologies default to `deadtime = 0`, which gives roughly **10x
  better THD than real hardware** (0.14 % against a published 1.36 %). Ask the
  user for their gate driver's dead time (IGBT 1-3 us, SiC 0.2-0.5 us) and set
  it. At 2-3 us the model brackets the published figure.
- THD here is measured on an R-L load, not after an LCL filter into a grid, so it
  is not directly comparable with grid-code numbers.
- Modulation is sinusoidal PD-PWM, not SVPWM, so the linear limit is M = 1.0 and
  not 1.155.
- Switches are bidirectional resistances with no separate diode path, so total
  conduction loss is about right but the transistor/diode SPLIT is not.

If a user says results disagree with a paper, that is the first thing to check -
not the last.

## Things that are true and worth saying

Report these when they apply; they are what turns a number into a decision.

- **Three-phase needs far less DC-link capacitance than single-phase.** The NP
  excursion moves from f1 to 3*f1 and largely cancels: for the same 2 % ripple,
  3.5 mF three-phase against 14.7 mF single-phase.
- **Capacitor ripple current is frequency dependent.** Electrolytics are rated at
  100/120 Hz and derated above; film is the reverse. Always give the split.
- **Each three-level capacitor sees half the bus.** That reduced blocking voltage
  is the point of the topology and it cuts switching loss (E_sw scales as
  V^1.0-1.3).
- **Ignoring the electrothermal coupling understates conduction loss by ~50 %.**
  Rds(on) roughly doubles by 150 C; `losses` solves loss and temperature
  together.
- **ZVS is worth roughly 3x on switching loss** - 14.6 W against 40.7 W on the
  DAB example.
- **The device library is representative, not real part numbers.** Say this
  whenever you quote a loss or temperature. Ask for a datasheet if the absolute
  number matters.
- **T-type and NPC are the same three-level leg.** They agree to 0.012 %, so if
  the user is choosing between them, the difference is conduction path and device
  count, not waveform quality.

## When a check fails

Do not tune parameters until the numbers look nice. Diagnose:

1. Read the failing check's `detail` - it names the quantity and the margin.
2. `steady state reached` failing means the run was too short; raise `Nsettle`
   or `n_settle`.
3. `power balance` with Pout > Pin means a sign error or an unsettled run.
4. `conduction mode ... as modelled` means the analytical model is being applied
   in the wrong regime.
5. A large error on one quantity with everything else correct usually means the
   analytical model is wrong, not the simulation. Say so.

Report what failed and why. A diagnosed failure is a better answer than a
plausible number.
