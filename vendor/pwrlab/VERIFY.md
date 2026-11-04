# How to verify this yourself

Three independent routes, cheapest first. Route 3 (the GUI) is the one that needs
nothing from me — you read the numbers off your own screen.

## Route 1 — one command, checks everything

```bash
cd $PE_FORGE/vendor/pwrlab && ./verify.sh
```

Re-runs every simulation from scratch (nothing cached) and exits non-zero on any
failure. Six checks, ~30 s:

1. LTspice present; the working invocation reachable; session type reported.
2. **Proves the trap:** calls `Contents/MacOS/LTspice -b …` and asserts it exits 0
   *and writes no `.raw`*. If this check ever flips to "behaviour changed", ADI gave
   the Mac build a real CLI and `run_sweep.WINE_CMD` should be revisited.
3. Stage 1: `tau` vs `R*C`.
4. **Netlists both `.asc` files with LTspice and diffs them against the validated
   `.net` files**, component by component.
5. Re-runs the Stage 3 sweep and checks all six φ against closed-form theory.
6. Re-runs the *schematics* and checks they give the same answer as the netlists.

## Route 2 — spot-check the physics by hand

The analytical power is `P = V1·V2·φ(π−φ)/(2π²·fs·L)` with φ in radians,
V1=V2=400 V, fs=100 kHz, L=20 µH. At φ=45° (π/4 rad):

```
P = 400·400·(π/4)·(π − π/4) / (2·π²·1e5·20e-6) = 160000·0.785398·2.356194 / 39.478
  = 7500.0 W
```

`results.csv` says 7502.51 W → **+0.034 %**. You can redo any row with a calculator.

A second, independent check that does *not* use the power formula: the RMS of the
ideal trapezoidal inductor current. At φ=45° the current ramps −25 A → +25 A over
t_d = 1.25 µs then holds flat for the rest of the half period, giving
`I_rms = 22.8218 A`. The simulation reports **22.8233 A**. This is the stronger test —
it constrains the *waveform*, not just its average, and it is computed in
`sweep_stage3.py::analytic_rms` from geometry alone.

## Route 3 — open the schematic and run it in the GUI

```bash
open /Applications/LTspice.app                       # then File > Open
# or directly:
open -a LTspice $PE_FORGE/vendor/pwrlab/stage3/dab.asc
```

Files: `stage3/dab.asc` (the DAB) and `stage1/rc_lowpass.asc` (the smoke test).

**How these schematics are drawn — read this first.** Inside each bridge leg the
midpoint is a real wire, but the rails, gate nets and transformer connections use
**net-name labels** (`a b c d x p_dc s_dc g_a…g_d`) rather than drawn wires. Two pins
with the same label are the same node. This is deliberate: a label cannot be
mis-routed, so the schematic is *provably* the circuit that was validated — check 4
of `verify.sh` confirms it by netlisting the `.asc` and diffing against the `.net`.
It is standard practice for gate and rail nets in power schematics, though a
hand-drawn version would usually wire the power path too.

Press **Run**. The sweep parameter is the `.param phi=30` directive on the canvas —
edit it (right-click the text) to try other angles.

### Numbers to expect in the GUI

The `.meas` results appear in the SPICE Error Log (**View > SPICE Error Log**, or
`⌘L`). At the **default φ = 30°**:

| `.meas` | expected | meaning |
|---|---|---|
| `pin` | **5556.78 W** | avg power drawn from V1 (analytical: 5555.56 W) |
| `pout` | 5553.98 W | avg power into V2 |
| `ilrms` | **15.7183 A** | RMS current in L1 (closed form: 15.7135 A) |
| `ilavg` | −0.409 A | residual DC offset — should be small |
| `ilmax` / `ilmin` | +16.2 / −16.6 A | near-symmetric about zero |
| `izvs_a1…a3` | all **negative** | I(L1) before each `n·T` turn-on → ZVS |
| `izvs_b1…b3` | all **positive** | I(L1) before each `n·T+T/2` turn-on → ZVS |

To see the waveforms, plot `I(L1)` and look at the last 10 periods (1.2–1.3 ms).
You should see a **trapezoid centred on zero** — flat-topped, ~32 A peak-to-peak at
φ=30°, with no visible DC pedestal. A large downward offset means the startup
transient was not handled; that is the failure mode described in REPORT.md.

For `rc_lowpass.asc`: the log should show `tau: time = 1.00000051e-03`, i.e. 1.000 ms
= R·C, and `vfinal = 0.993262` = 1−e⁻⁵.

### The waveforms should now appear by themselves

An empty black plot pane on Run is normal for a schematic with a `.save`
directive: LTspice stores only the listed signals and adds no traces to the plot
by itself. The fix is a **plot-settings file**, `stage3/dab.plt`, which now sits
next to the schematic. It opens two panes:

* **v_ab and v_cd** — the two bridge squares, so you can see the phase shift
* **I(L1)** — the inductor current

and it opens the X axis on **1.2–1.3 ms**, the measurement window, rather than
the full 0–1.3 ms (which crams 130 periods together and shows no detail).

The schematic's `.save` was also widened to keep `V(a) V(b) V(c) V(d)`, so those
bridge voltages exist to plot. That makes its `.raw` 5.4 MB instead of 3.2 MB.
The sweep netlist `stage3/dab.net` deliberately stays lean — `.save` changes only
what is *written*, never what is computed, and `verify.sh` confirms both give
identical `.meas` results.

**Caveat — the one thing I could not verify headlessly.** I can run LTspice in
batch but cannot see its GUI, so I could not watch the `.plt` render. What I did
check: its structure uses only keys that appear in the shipped example `.plt`
files (and every key that appears in 100 % of them), the multi-trace syntax
matches, and trace ids follow the convention those files use — small ids for
computed expressions like `V(a)-V(b)`, `524290+` for plain traces. If a pane still
comes up empty, the traces are one click away: **right-click the plot > Add
Trace**, or drag over the schematic. Tell me and I will fix the file.

### Plots without the GUI

`plot_dab.py` renders the same three views straight from a `.raw`, which is also
how an autonomous sweep should produce figures:

```bash
.venv/bin/python plot_dab.py --phi 45          # runs it and writes dab_phi45.png
.venv/bin/python plot_dab.py --raw work/.../dab.raw --phi 45 --periods 5
```

It marks each primary turn-on instant with a circle — **green means I(L1) has the
polarity that gives ZVS** — so the ZVS claim in `results.csv` is visible rather
than just tabulated. Pre-rendered: `dab_phi10.png`, `dab_phi30.png`,
`dab_phi90.png`.

What to look for, and what would indicate a problem:

| you should see | a problem would look like |
|---|---|
| trapezoid **centred on zero** | a large DC pedestal (the startup transient, see REPORT.md) |
| flat top shrinking as φ grows; at φ=90° a **pure triangle** | flat top at φ=90° |
| all turn-on circles **green** | orange circles = no ZVS at that instant |
| instantaneous power symmetric about the analytical dashed line | average visibly off the line |

### If the GUI asks to update libraries or shows a dialog

The batch path never needed one, but the GUI may offer an update. Declining is safe;
the bottle's library at
`~/Library/Application Support/LTspice/Bottles/ltspice/drive_c/users/crossover/AppData/Local/LTspice/lib`
is already unpacked and complete (68 symbols, 3084 subcircuits).

## Route 4 — cross-check against a second solver

`ngspice 45.2` is installed natively (`/opt/homebrew/bin/ngspice`) and is a
genuinely independent engine — no Wine, no Rosetta. Porting `dab.net` to ngspice
syntax (its `.meas` and switch model differ) would be the strongest possible check
that a number is physics and not an LTspice artefact. I have not done it; say the
word if you want it.

## What I would distrust

- **Any result where you did not confirm `.raw` and `.log` exist.** The native stub
  returns success and writes nothing; this is the single most dangerous behaviour here.
- **Sweeps with more than 3 workers.** Not wrong, but slower than sequential.
- **A headless SSH session with no console login.** Everything here was verified with
  `launchctl managername` = `Aqua`. Untested otherwise, and the Wine launcher does
  request a display driver.
- **`run_sweep.WINE_CMD` after any LTspice update.** It pins the bottle name
  (`ltspice`) and the Windows exe path. It fails loudly, not silently, but it is the
  one brittle line.
