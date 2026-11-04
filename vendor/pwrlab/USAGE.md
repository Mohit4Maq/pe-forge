# Using pwrlab — asking design questions in plain English

This document shows how to go from a question like

> *"What is the ripple current requirement of the DC-link capacitor in the
> T-type converter?"*

to a checked number, with output files, without knowing any of the internals.

---

## One-time setup

**Already done on this machine.** Both are registered globally, so any Claude
Code session in any directory can answer these questions. Recorded here for
reinstalling or moving the project.

### 1. The package

```bash
cd $PE_FORGE/vendor/pwrlab
uv pip install --python .venv/bin/python -e .
```

This puts `pwrlab` on the venv's path and creates two executables,
`.venv/bin/pwrlab` and `.venv/bin/pwrlab-mcp`, that work from any directory.
Without it, `python -m pwrlab.mcp_server` only runs from the project root.

### 2. The MCP server, at user scope

```bash
claude mcp add pwrlab --scope user -- $PE_FORGE/vendor/pwrlab/.venv/bin/pwrlab-mcp
```

`--scope user` is the part that matters: the default (`local`) registers it for
this project only. Verify with `claude mcp get pwrlab` — it should say
*"User config (available in all your projects)"* and *Connected*.

### 3. The skill, at user scope

```bash
ln -sfn $PE_FORGE/vendor/pwrlab/.claude/skills/pwrlab ~/.claude/skills/pwrlab
```

A symlink rather than a copy, so editing the skill in the project updates the
installed one. `~/.claude/skills/` is read by every session;
`<project>/.claude/skills/` only by sessions in that project.

### 4. Check it

Ask *"Is the pwrlab toolchain working?"*, or run
`$PE_FORGE/vendor/pwrlab/.venv/bin/pwrlab-mcp` — you should get
`toolchain OK`.

**The skill and the server do different jobs.** The server provides the tools;
the skill tells the model which tool a question maps to, what to ask you first,
and how to report the answer honestly. The skill alone still works — it falls
back to the CLI — but you lose the background-job handling.

### What would break it

- **Moving or renaming `$PE_FORGE/vendor/pwrlab`.** Both registrations use
  absolute paths. Re-run steps 1–3 after moving.
- **Deleting `.venv`.** Recreate it, then re-run step 1.
- **Removing LTspice.** `doctor` will say so plainly rather than failing oddly.

Scratch files accumulate under `$PE_FORGE/vendor/pwrlab/work/` regardless of
where you asked the question. Delete that directory any time.

## Where everything is stored

Three different kinds of file, in three different places.

| What | Where | Cleaned up? |
|---|---|---|
| **Your deliverables** — reports, CSVs, plots | the directory you are working in when you ask | never, they are yours |
| **Simulation scratch** — `.raw`, `.log`, `.net` | `$PE_FORGE/vendor/pwrlab/work/` **always** | yes, automatically |
| **Registration** — MCP server | `~/.claude.json` | n/a |
| **Registration** — skill | `~/.claude/skills/pwrlab` → symlink into the project | n/a |
| **The framework itself** | `$PE_FORGE/vendor/pwrlab/pwrlab/` | n/a |

### Deliverables follow you; scratch does not

The MCP tools return JSON — they do not write files. The skill instructs the
model to write reports and CSVs to **the directory you are working in**, so if
you ask a question from `~/projects/my-inverter`, that is where
`ttype_dclink_report.md` lands. Ask for a different path and it will use it.

Simulation scratch always goes to `$PE_FORGE/vendor/pwrlab/work/`, no matter
where you asked the question, because the path is baked in absolutely. This is
deliberate: `.raw` files are large, short-lived, and you do not want them
appearing in whatever repository you happen to be sitting in.

### Scratch is bounded — but it was not, and that was a bug

LTspice writes a `.raw` next to every netlist, several megabytes each. `Runner`
had a `cleanup()` method that **nothing ever called**, so scratch accumulated
forever: 1.0 GB across 263 `.raw` files during development, and unbounded in real
use. Now every background job prunes on completion, keeping the 5 newest runs
under a 500 MB cap — recent runs stay because a follow-up question ("now plot
that") needs them.

To prune by hand:

```bash
pwrlab clean --dry-run          # see what would go
pwrlab clean                    # keep 5 newest, cap 500 MB
pwrlab clean --keep-recent 1 --max-mb 100
```

Deleting `work/` entirely is always safe. Nothing there is a deliverable.

### The older files in the project root

From the original Stage 0–4 validation, kept because `verify.sh` still checks
them: `REPORT.md`, `VERIFY.md`, `results.csv`, `dab_phi*.png`, `run_sweep.py`,
`sweep_stage3.py`, `plot_dab.py`, `gen_asc.py`, `verify.sh`, and the `stage1/`,
`stage2/`, `stage3/` directories. The framework itself does not depend on them.

## The worked example

**You ask:** *"What is the ripple current requirement of the DC-link capacitor
in the T-type converter?"*

### What should happen — step 1: it asks you first

That question is underspecified by an order of magnitude. Ripple current scales
with power, and the frequency content depends on whether the converter is single-
or three-phase. So you should get something like:

> **Single-phase or three-phase?** → *Three-phase (recommended)* — moves the
> ripple to 3×f₁ and switching frequency, needing roughly 4× less capacitance
> than single-phase for the same voltage ripple.
>
> **DC bus voltage and power?** → *800 V, ~5 kW (default)* / *Specify my own*
>
> **Switching frequency?** → *10 kHz* / *20 kHz* / *Specify*
>
> **Load power factor?** → *0.95 (typical motor)* / *Unity* / *Specify*

If you answer "just use typical values", it should pick defaults and **state them
in the answer**. If it simulates without asking anything and without stating
assumptions, that is a bug in the skill — tell it so.

### Step 2: it runs the right tool

```
dc_link_capacitor(topology="ttype3ph", v_ripple_frac=0.02, verify=true)
→ {"job_id": "a1b2c3d4", ...}          # returns immediately
job_status("a1b2c3d4")                  # poll until "done"
```

`verify=true` runs a real LTspice simulation and compares it against the
analytical model. It takes ~30 s. `verify=false` returns the analytical answer
instantly, clearly labelled as unverified.

### Step 3: what a good answer looks like

> **13.15 A RMS per capacitor**, verified against the analytical pulse-train
> model to **+0.45 %**.
>
> | | |
> |---|---|
> | Total RMS ripple current | **13.15 A** |
> | Below 5 kHz | 4.65 A |
> | Above 5 kHz | 12.24 A |
> | Peak current | 19.3 A |
> | Capacitance for 2 % NP ripple | 3.52 mF per capacitor |
> | Voltage rating | 400 V per capacitor (half the bus), before derating |
>
> Most of the ripple is at switching frequency, so check the part's derating
> curve at 10 kHz — not its 120 Hz rating. An electrolytic rated 13 A at 120 Hz
> may be fine or may not be; a film capacitor derates the other way.
>
> Assumptions: 800 V bus, M = 0.8, 10 kHz carrier, 10 Ω + 10 mH load
> (PF 0.954), 4.66 kW. Files: `ttype_dclink_report.md`, `ttype_dclink.csv`.

### Step 4: files on disk

Ask for them if they are not offered: *"write that to a CSV and a report"*.

---

## Other questions this answers, and what it will ask you

| You ask | It will ask about | Tool |
|---|---|---|
| "How much DC-link capacitance for 1 % ripple?" | phases, power, fs | `dc_link_capacitor` |
| "What's the efficiency of my DAB at 45°?" | V1, V2, L, fs, device | `analyze` + `losses` |
| "How does DAB power vary with phase shift?" | range, V1/V2, L | `sweep` |
| "Which tolerance hurts my buck's ripple most?" | which parts, spread | `tolerance` (sensitivity) |
| "What heatsink do I need?" | device, ambient, Tj target | `losses` + `size_heatsink_for_Tj` |
| "What's the inrush at startup?" | topology, limits | `fault` |
| "Does my DAB achieve ZVS at light load?" | phase-shift range | `sweep` |
| "T-type or NPC?" | the operating point | `analyze` on both |
| "What's the THD of my inverter?" | M, mf, load | `analyze` |
| "FFT of the output current?" | topology, which signal | `spectrum` |
| "How much harmonic content at the carrier?" | M, mf | `spectrum` |

---

## A question with no matching tool

Not every question has a tool. The model escalates in four tiers and tells you
which one it used:

1. **A tool exists** - it calls it.
2. **The machinery exists but is not exposed** - it writes a short script against
   the library (`pwrlab.harmonics`, `pwrlab.waveforms`, `pwrlab.losses`). Still
   validated: a spectrum comes with its Parseval residual.
3. **A new quantity on an existing topology** - it adds the measurement *and* a
   closed-form prediction to check it against, plus a test.
4. **A new topology** - it implements the four required methods.

Tiers 3 and 4 change the codebase, so it will say so. The rule that does not bend
is that a new number needs something independent to check it against; a
measurement with no prediction is not an answer.

Where no closed form exists, the pattern is to construct the ideal waveform from
the switching logic in numpy and compare against that - independent of SPICE and
verifiable line by line. Neutral-point ripple, DC-link capacitor current and the
PWM spectrum are all validated this way.

## Using it directly, without an agent

### CLI

`pwrlab` works from any directory after the install above. Add it to PATH with
`export PATH="$PE_FORGE/.venv/bin:$PATH"`, or call it in full.

```bash
pwrlab list
pwrlab doctor
pwrlab run ttype3ph --M 0.9
pwrlab sweep dab --phi_deg 10,20,30,45,60,90 --csv out.csv
pwrlab loss dab --device SiC_1200V_40m --size-heatsink 150
pwrlab mc buck --outputs V_ripple --tol_L 0.2 -n 100
pwrlab fault buck --event short --watch "I(L1)" --limit 100
```

### Python

```python
from pwrlab import Study
from pwrlab.topologies.threelevel import TType3Phase, dc_link_requirements

t = TType3Phase(Vdc=800, M=0.8, Rload=10, Lload=10e-3, mf=200)

req = dc_link_requirements(t, v_ripple_frac=0.02)   # analytical, instant
print(req["ripple_current"])

r = Study(t).run()                                   # simulate + verify
print(r.report())
print(r.ok, r.extracted["I_cap_rms"])
```

Every `Study` result has `.ok`, `.checks`, `.extracted`, `.analytical` and
`.report()`.

---

## Reading the output

**`usable: true`** — every ERROR-level check passed. The number is defensible.

**`usable: false`** — at least one check failed. **Do not use the numbers.** The
`verdict` and `warnings` say what failed. Common causes:

| Check that failed | Meaning | Fix |
|---|---|---|
| `steady state reached` | measured during a transient | raise `Nsettle` / `n_settle` |
| `power balance` (Pout > Pin) | sign error, or not settled | as above |
| `X vs analytical` | simulation disagrees with theory | genuine — diagnose, do not tune |
| `conduction mode ... as modelled` | theory applied in the wrong regime | change the operating point or the model |
| `Tj below rating` | device too hot | bigger heatsink, better device, less power |

**`error_vs_theory_pct`** is error against a closed-form model, not against a
fit. Sub-0.1 % is normal for the switching topologies; the line-frequency ones
sit under ~0.5 %.

---

## Limits — read before trusting a number

- **Device parameters are representative, not real part numbers.** Loss and
  junction temperature are directionally right and absolutely unverified.
  Substitute datasheet values (`pwrlab/losses.py`, `LIBRARY`) before committing.
- **Ideal switches.** No Coss, no reverse recovery in the power stage, no gate
  drive dynamics. Switching loss comes from datasheet energies in
  post-processing, not from the waveform.
- **Open loop.** No controllers, except the PFC's hysteresis current loop. No
  stability or bandwidth results.
- **The DC feed matters.** Three-level topologies default to `L_dc = 10 mH` so
  the capacitors carry all the AC current — the standard sizing assumption. With
  `L_dc = 0` an ideal source absorbs part of it and the capacitor current comes
  out ~15 % low; the checks warn about this.
- **Thermal is steady-state** — a single Rth chain, no transient Zth, so short
  overloads are not modelled.
- **Runtime**: one point is seconds; a sweep or Monte Carlo is minutes. Never
  more than 3 concurrent LTspice instances — 4 is slower than sequential on this
  Wine stack.

---

## If something looks wrong

1. `pwrlab doctor` — is the toolchain alive?
2. `./tests/run_all.sh` — seven suites, including fault injection that
   re-introduces historical bugs and asserts the checks still catch them.
3. `pwrlab/README.md` — how the framework works and why.
4. `REPORT.md` — the macOS/LTspice findings, including the silent-failure trap.
