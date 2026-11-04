"""MCP server exposing pwrlab to an agent.

DESIGN RULE, and the reason this file is careful: an agent driving these tools
cannot see intermediate state. It gets a JSON blob back and will report whatever
is in it. So NO TOOL HERE RETURNS A BARE NUMBER. Every result carries:

    usable    bool   -- did every ERROR-level check pass?
    verdict   str    -- one line an agent can quote honestly
    checks    list   -- what was verified, with margins
    warnings  list   -- anything that did not pass

If `usable` is false the numbers are still returned (they are diagnostic) but the
verdict says plainly that they must not be used. This matters more over MCP than
in the library: a +154 % wrong DAB result looks exactly like a right one, and an
agent with no other context will happily present it as fact.

Long simulations are the other hazard. A sweep of 50 points takes minutes, which
exceeds typical MCP client timeouts, so anything potentially long runs as a
BACKGROUND JOB: the tool returns a job id immediately and `job_status` polls it.

Run:  .venv/bin/python -m pwrlab.mcp_server
"""
from __future__ import annotations

import functools
import io
import math
import threading
import traceback
import uuid
from contextlib import redirect_stdout
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from . import provenance
from .sim import Runner, default_command, prune_work, dir_size
from .study import Study
from .checks import ERROR, WARN
from . import checks as C_

HERE = Path(__file__).resolve().parent.parent
WORK = HERE / "work" / "mcp"

mcp = MCPServer(
    name="pwrlab",
    instructions=(
        "Power-electronics design, simulation and verification via LTspice.\n\n"
        "Every result carries a `usable` flag and a `verdict`. A result with "
        "usable=false has FAILED its own validity checks -- report the verdict, "
        "never the numbers alone. The numbers are returned only for diagnosis.\n\n"
        "Each topology is checked against a closed-form analytical model, so "
        "`*_err_pct` fields are error against theory, not against a fit.\n\n"
        "Simulations take seconds to minutes. Sweeps, Monte Carlo, heatsink "
        "sizing and benchmarks return a job id; poll with job_status.\n\n"
        "Every payload carries a `provenance` block. If `evidence_eligible` is "
        "false the working tree was dirty and the run cannot be reproduced from "
        "a named revision -- discuss it, never cite it as validation.\n\n"
        "Two guarantees you can rely on: no payload contains Infinity or NaN (a "
        "value never reached comes back as null with a sibling `<name>__note` "
        "giving the reason), and no check is silently skipped (a property that "
        "could not be assessed appears as a FAILING check whose detail starts "
        "`NOT ASSESSED:` -- treat that as unknown, never as fine).\n\n"
        "`analyze` and friends compare against a CLOSED-FORM MODEL OF AN "
        "IDEALISED CIRCUIT. That is not the same claim as agreeing with "
        "hardware. For that, use `benchmark` -- and report the MODE each "
        "quantity earned. Never write 'validated against hardware' for one that "
        "passed only on TREND, BOUNDED_OPTIMISM or SPEC_BOUND.\n\n"
        "Some quantities are chaotic: the hysteresis PFC's current THD moves "
        "44 % for a 0.015 % change in load resistance, converged and "
        "bit-reproducible in both cases. Never quote single-point THD from "
        "`pfc` as a property of the converter; use an ensemble median and "
        "quote its spread. `assumptions` lists which quantities this applies "
        "to, per topology.\n\n"
        "Before a large sweep, call `validate_params`: a non-physical point "
        "does not fail fast in LTspice, it stalls the solver for minutes."
    ),
)


# --------------------------------------------------------------------------- #
# JSON boundary
# --------------------------------------------------------------------------- #
# `Infinity` and `NaN` are Python floats but they are NOT JSON (RFC 8259), and a
# strict client parser rejects the whole payload because one settling time never
# converged. Worse, a lenient parser accepts `Infinity` and an agent then reports
# it as a number. Both failures are silent. So non-finite values are replaced by
# null and the REASON is carried alongside, which is the thing a reader actually
# needed: "never settled within the measurement window" is information, and
# `Infinity` is not.
def _json_safe(obj):
    if isinstance(obj, float):
        if math.isnan(obj):
            return None
        if math.isinf(obj):
            return None
        return obj
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if isinstance(v, float) and not math.isfinite(v):
                out[k] = None
                out[f"{k}__note"] = ("not finite: no value was reached "
                                     "(did not settle, or diverged)")
            else:
                out[k] = _json_safe(v)
        return out
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    return obj


def _finalise(result):
    """Every tool return passes through here: sanitise, then stamp provenance."""
    result = _json_safe(result)
    if isinstance(result, dict) and "provenance" not in result:
        result["provenance"] = provenance.stamp()
    return result


def tool(**kw):
    """mcp.tool, with the JSON boundary and provenance stamp applied."""
    inner = mcp.tool(**kw)

    def deco(fn):
        @functools.wraps(fn)
        def wrapped(*a, **k):
            return _finalise(fn(*a, **k))
        return inner(wrapped)
    return deco


# --------------------------------------------------------------------------- #
def _registry():
    from .cli import REGISTRY, _load_registry
    if not REGISTRY:
        _load_registry()
    return REGISTRY


def _build(topology: str, params: dict | None):
    reg = _registry()
    if topology not in reg:
        raise ToolError(f"unknown topology '{topology}'. "
                        f"Available: {', '.join(sorted(reg))}")
    t = reg[topology]()
    for k, v in (params or {}).items():
        if not hasattr(t, k):
            raise ToolError(f"{topology} has no parameter '{k}'. "
                            f"Valid: {', '.join(t.params())}")
        try:
            setattr(t, k, v)
        except Exception as e:
            raise ToolError(f"cannot set {k}={v!r} on {topology}: {e}")
    return t


def _check_payload(checkset):
    return [{"name": c.name, "passed": bool(c.passed), "severity": c.severity,
             "detail": c.detail,
             "value": None if c.value is None else float(c.value),
             "limit": None if c.limit is None else float(c.limit)}
            for c in checkset]


def _verdict(sr) -> str:
    if sr.ok:
        errs = [f"{k} {(sr.extracted[k]-v)/v*100:+.3f}%"
                for k, v in sr.analytical.items()
                if isinstance(v, (int, float)) and v
                and isinstance(sr.extracted.get(k), (int, float))]
        return ("VALIDATED: all checks passed"
                + (f"; error vs closed-form theory: {', '.join(errs)}" if errs else ""))
    hard = [c for c in sr.checks if not c.passed and c.severity == ERROR]
    soft = [c for c in sr.checks if not c.passed and c.severity == WARN]
    lead = "NOT USABLE -- these numbers failed validation. "
    if hard:
        lead += "Failed: " + "; ".join(f"{c.name} ({c.detail})" for c in hard[:3])
    if soft and not hard:
        lead += "Warnings: " + "; ".join(c.name for c in soft[:3])
    return lead


def _result_payload(sr) -> dict:
    out = {
        "usable": bool(sr.ok),
        "verdict": _verdict(sr),
        "topology": sr.topology.summary(),
        "design_notes": sr.topology.design_notes(),
        "simulated": {k: (float(v) if isinstance(v, (int, float)) and
                          not isinstance(v, bool) else v)
                      for k, v in sr.extracted.items()},
        "closed_form": {k: float(v) for k, v in sr.analytical.items()
                        if isinstance(v, (int, float))},
        "error_vs_theory_pct": {},
        "checks": _check_payload(sr.checks),
        "warnings": [f"{c.severity}: {c.name} -- {c.detail}"
                     for c in sr.checks.failures],
        "wall_seconds": round(sr.wall_s, 2),
    }
    for k, a in sr.analytical.items():
        s = sr.extracted.get(k)
        if isinstance(a, (int, float)) and isinstance(s, (int, float)) and a:
            out["error_vs_theory_pct"][k] = round((s - a) / a * 100, 6)
    return out


# --------------------------------------------------------------------------- #
# background jobs
# --------------------------------------------------------------------------- #
@dataclass
class Job:
    id: str
    kind: str
    status: str = "running"
    progress: str = ""
    result: Any = None
    error: str | None = None
    log: list = field(default_factory=list)


JOBS: dict[str, Job] = {}
_LOCK = threading.Lock()


def _spawn(kind: str, fn) -> dict:
    job = Job(id=uuid.uuid4().hex[:8], kind=kind)
    with _LOCK:
        JOBS[job.id] = job

    def runner():
        buf = io.StringIO()
        try:
            with redirect_stdout(buf):
                job.result = fn(job)
            job.status = "done"
        except Exception as e:
            job.status = "failed"
            job.error = f"{type(e).__name__}: {e}"
            job.log.append(traceback.format_exc()[-1500:])
        finally:
            tail = buf.getvalue().strip().splitlines()[-12:]
            job.log.extend(tail)
            # Bound the scratch directory. An agent will not tidy up after
            # itself, and each .raw is megabytes.
            try:
                prune_work(WORK, keep_recent=5, max_bytes=500_000_000)
            except Exception:
                pass

    threading.Thread(target=runner, daemon=True).start()
    return {"job_id": job.id, "kind": kind, "status": "running",
            "note": "poll job_status(job_id); simulations take seconds to minutes"}


# --------------------------------------------------------------------------- #
# tools
# --------------------------------------------------------------------------- #
@tool(description="List available converter topologies with their parameters "
                      "and defaults. Start here.")
def list_topologies() -> dict:
    out = {}
    for name, cls in sorted(_registry().items()):
        t = cls()
        out[name] = {
            "description": (cls.__doc__ or "").strip().splitlines()[0],
            "parameters": {k: (float(v) if isinstance(v, (int, float)) and
                               not isinstance(v, bool) else v)
                           for k, v in t.params().items()},
            "measured_quantities": sorted(t.analytical().keys()),
        }
    return {"topologies": out,
            "note": "every topology is validated against a closed-form model; "
                    "error_vs_theory_pct in results is error against that model"}


@tool(description="Check that the LTspice toolchain works on this machine. "
                      "Run this first if anything behaves unexpectedly.")
def doctor() -> dict:
    from .topologies.buck import Buck
    try:
        cmd = default_command()
    except FileNotFoundError as e:
        return {"usable": False, "verdict": f"NO SIMULATOR: {e}"}
    sr = Study(Buck(Nsettle=150, Nmeas=10),
               runner=Runner(workroot=WORK / "doctor")).run(tag="doc",
                                                            converge=False)
    return {"usable": bool(sr.ok),
            "verdict": ("toolchain OK" if sr.ok else
                        f"toolchain PROBLEM: {_verdict(sr)}"),
            "simulator_command": cmd,
            "checks": _check_payload(sr.checks)}


@tool(description="Design and validate ONE operating point. Returns simulated "
                      "values, closed-form predictions, error against theory, and "
                      "the full check battery. Fast (seconds).")
def analyze(topology: str, params: dict | None = None,
            converge: bool = True) -> dict:
    t = _build(topology, params)
    sr = Study(t, runner=Runner(workroot=WORK / "analyze")).run(
        tag="a", converge=converge)
    p = _result_payload(sr)
    p["timestep_convergence_checked"] = converge
    if not converge:
        p["warnings"].append(
            "timestep convergence NOT checked (converge=false); the result has "
            "not been shown to be independent of the solver settings")
    return p


@tool(description="Sweep one or more parameters over a grid. Returns a job id; "
                      "poll with job_status. Each point is independently validated.")
def sweep(topology: str, grid: dict, params: dict | None = None) -> dict:
    t = _build(topology, params)
    for k in grid:
        if not hasattr(t, k):
            raise ToolError(f"{topology} has no parameter '{k}'. "
                            f"Valid: {', '.join(t.params())}")

    def work(job):
        job.progress = f"sweeping {list(grid)} over {len(grid)} parameter(s)"
        df = Study(t, runner=Runner(workroot=WORK / f"sweep_{job.id}")).sweep(
            grid, converge=False, progress=False)
        bad = int((~df["ok"]).sum())
        errs = {c[:-len("_err_pct")]: round(float(df[c].abs().max()), 5)
                for c in df.columns if c.endswith("_err_pct")}
        return {
            "usable": bad == 0,
            "verdict": (f"all {len(df)} points validated; worst error vs theory "
                        f"{errs}" if bad == 0 else
                        f"NOT USABLE: {bad} of {len(df)} points failed validation"),
            "n_points": len(df), "n_failed": bad,
            "worst_error_vs_theory_pct": errs,
            "rows": df.round(8).to_dict("records"),
        }

    return _spawn("sweep", work)


@tool(description="Tolerance analysis. mode='sensitivity' (cheap, 2N+1 runs, "
                      "shows which tolerances matter), 'corners' (2^k extremes), or "
                      "'monte_carlo' (n samples, distribution). Returns a job id.")
def tolerance(topology: str, tolerances: dict, outputs: list[str],
              mode: str = "sensitivity", n: int = 100, seed: int = 0,
              params: dict | None = None) -> dict:
    from .montecarlo import ToleranceStudy, Tol
    t = _build(topology, params)
    for k in tolerances:
        if not hasattr(t, k):
            raise ToolError(f"{topology} has no parameter '{k}'. "
                            f"Valid: {', '.join(t.params())}")
    if mode not in ("sensitivity", "corners", "monte_carlo"):
        raise ToolError(f"mode '{mode}' is not valid; use sensitivity, corners "
                        f"or monte_carlo")

    def work(job):
        ts = ToleranceStudy(t, {k: Tol(float(v)) for k, v in tolerances.items()},
                            runner=Runner(workroot=WORK / f"tol_{job.id}"))
        job.progress = f"{mode} over {list(tolerances)}"
        if mode == "sensitivity":
            df = ts.sensitivity(outputs, progress=False)
            rank = df[["output", "param", "S"]].round(5).to_dict("records")
            return {"usable": True, "mode": mode,
                    "verdict": ("normalised sensitivity dy/y per dx/x; |S| near 0 "
                                "means that tolerance does not matter"),
                    "sensitivities": rank}
        if mode == "corners":
            df = ts.corners(outputs, progress=False)
            bad = int((~df["ok"]).sum())
            return {"usable": bad == 0, "mode": mode,
                    "verdict": (f"{len(df)} corners, {bad} failed validation"),
                    "n_failed": bad, "rows": df.round(8).to_dict("records")}
        r = ts.monte_carlo(outputs, n=n, seed=seed, progress=False)
        return {"usable": r.n_valid > 0, "mode": mode,
                "verdict": (f"{r.n_valid}/{r.n_requested} samples usable"
                            + ("" if r.n_valid == r.n_requested else
                               f"; {r.n_requested - r.n_valid} EXCLUDED because "
                               f"their checks failed -- they are not averaged in")),
                "n_valid": r.n_valid, "n_requested": r.n_requested,
                "statistics": r.stats().round(8).to_dict("records")}

    return _spawn(f"tolerance:{mode}", work)


@tool(description="Device loss and junction temperature, solved as a coupled "
                      "electrothermal fixed point. Optionally size the heatsink. "
                      "Returns a job id. NOTE: the device library is representative, "
                      "not real part numbers.")
def losses(topology: str, device: str = "SiC_1200V_40m", ambient_C: float = 40.0,
           rth_heatsink: float = 0.3, size_heatsink_for_Tj: float | None = None,
           params: dict | None = None) -> dict:
    from .losses import LIBRARY, analyse_losses, LossAnalysis, required_heatsink
    if device not in LIBRARY:
        raise ToolError(f"unknown device '{device}'. Available: "
                        f"{', '.join(LIBRARY)}")
    t = _build(topology, params)
    sw = t.switch_blocking_voltages()
    if not sw:
        raise ToolError(f"{topology} does not declare switch blocking voltages, "
                        f"so loss analysis is not available for it")

    def work(job):
        dev = LIBRARY[device]
        devs = {n: dev for n in sw}
        runner = Runner(workroot=WORK / f"loss_{job.id}")
        job.progress = "solving electrothermal fixed point"
        lr, sr = analyse_losses(t, devs, runner, Ta=ambient_C,
                                Rth_hs=rth_heatsink)
        cs = LossAnalysis.checks(lr)
        ok = all(c.passed for c in cs)
        out = {
            "usable": ok,
            "verdict": ("loss solution converged and every junction is within "
                        f"rating (hottest {lr.Tj_max:.1f} C)" if ok else
                        "NOT USABLE: " + "; ".join(c.name for c in cs
                                                   if not c.passed)),
            "device": device,
            "device_library_caveat": ("representative parameters, NOT a real part "
                                      "number -- substitute datasheet values "
                                      "before trusting absolute numbers"),
            "total_device_loss_W": round(lr.P_total, 3),
            "hottest_junction_C": round(lr.Tj_max, 2),
            "converged": lr.converged,
            "per_switch": lr.table().round(6).to_dict("records"),
            "checks": _check_payload(cs),
        }
        if lr.P_out:
            out["efficiency_pct"] = round(lr.P_out / (lr.P_out + lr.P_total) * 100, 4)
        if size_heatsink_for_Tj:
            job.progress = "bisecting on heatsink thermal resistance"
            best = required_heatsink(t, devs, runner, Ta=ambient_C,
                                     Tj_target=size_heatsink_for_Tj)
            out["heatsink_sizing"] = (
                {"max_rth_K_per_W": round(best[0], 5),
                 "at_that_point_loss_W": round(best[1].P_total, 2),
                 "hottest_junction_C": round(best[1].Tj_max, 2)}
                if best else
                {"error": f"no heatsink keeps Tj <= {size_heatsink_for_Tj} C"})
        return out

    return _spawn("losses", work)


@tool(description="Fault or dynamic transient: 'startup' (inrush from a cold "
                      "start), 'loadstep', or 'short' (output short circuit). "
                      "Reports peak stress, excursion and settling time.")
def fault(topology: str, event: str = "startup", at_s: float = 1.5e-3,
          factor: float = 0.5, tstop_s: float | None = None,
          watch: list[str] | None = None, limit_hi: float | None = None,
          params: dict | None = None) -> dict:
    from .transients import TransientStudy, StartUp, LoadStep, ShortCircuit
    t = _build(topology, params)
    events = {"startup": lambda: StartUp(),
              "loadstep": lambda: LoadStep(t=at_s, factor=factor),
              "short": lambda: ShortCircuit(t=at_s)}
    if event not in events:
        raise ToolError(f"event '{event}' is not valid; use "
                        f"{', '.join(events)}")
    if event in ("loadstep", "short") and t.output_node() is None:
        raise ToolError(f"{topology} does not declare an output node, so "
                        f"'{event}' cannot be applied to it; 'startup' works "
                        f"for any topology")
    sigs = watch or t.default_watch()
    if not sigs:
        raise ToolError(
            f"{topology} saves no traces a transient study could watch; "
            f"its netlist has no .save line")
    saved = set(t.saved_traces())
    missing = [x for x in sigs if x not in saved]
    if missing:
        raise ToolError(
            f"{topology} does not save {missing}. It saves: {sorted(saved)}. "
            f"Omit `watch` to use this topology's own defaults "
            f"({t.default_watch()}).")

    def work(job):
        spec = {s: {"settle_band": 0.02} for s in sigs}
        if limit_hi is not None:
            spec[sigs[0]]["limit_hi"] = limit_hi
        job.progress = f"running {event}"
        r = TransientStudy(t, Runner(workroot=WORK / f"tr_{job.id}")).run(
            events[event](), spec, tstop=tstop_s, tag=event)
        return {
            "usable": bool(r.ok),
            "verdict": ("transient completed within all declared limits" if r.ok
                        else "LIMIT EXCEEDED or check failed: "
                             + "; ".join(c.name for c in r.checks.failures)),
            "event": event,
            "metrics": {k: (round(float(v), 8) if isinstance(v, float) else v)
                        for k, v in r.metrics.items()},
            "checks": _check_payload(r.checks),
        }

    return _spawn(f"fault:{event}", work)


@tool(description="DC-link capacitor requirement for a three-level topology "
                      "(ttype, npc, ttype3ph, npc3ph): RMS ripple current with a "
                      "low/high frequency split, capacitance for a voltage-ripple "
                      "target, and voltage rating. Set verify=true to confirm the "
                      "analytical numbers against a simulation (slower, returns a "
                      "job id).")
def dc_link_capacitor(topology: str, v_ripple_frac: float = 0.02,
                      f_split_Hz: float | None = None, verify: bool = True,
                      params: dict | None = None) -> dict:
    from .topologies.threelevel import ThreeLevelLeg, dc_link_requirements
    t = _build(topology, params)
    if not isinstance(t, ThreeLevelLeg):
        raise ToolError(
            f"'{topology}' is not a three-level topology; DC-link capacitor "
            f"analysis is available for: ttype, npc, ttype3ph, npc3ph")
    req = dc_link_requirements(t, v_ripple_frac=v_ripple_frac,
                               f_split=f_split_Hz)
    if not verify:
        req["usable"] = True
        req["verdict"] = ("analytical only (verify=false) -- NOT confirmed "
                          "against simulation")
        return req

    def work(job):
        job.progress = "verifying the analytical model against a simulation"
        sr = Study(t, runner=Runner(workroot=WORK / f"cap_{job.id}")).run(
            tag="cap", converge=False)
        sim = sr.extracted.get("I_cap_rms")
        th = req["ripple_current"]["I_rms_A"]
        err = (sim - th) / th * 100 if (sim and th) else None
        req["verification"] = {
            "simulated_I_cap_rms_A": sim,
            "analytical_I_cap_rms_A": th,
            "error_pct": None if err is None else round(err, 4),
            "simulated_I_cap_peak_A": sr.extracted.get("I_cap_peak"),
            "simulated_np_ripple_V": sr.extracted.get("np_ripple_V"),
            "checks": _check_payload(sr.checks),
        }
        req["usable"] = bool(sr.ok)
        req["verdict"] = (f"VERIFIED: simulation agrees with the analytical "
                          f"model to {err:+.2f} %" if sr.ok and err is not None
                          else _verdict(sr))
        return req

    return _spawn("dc_link_capacitor", work)


@tool(description="Harmonic spectrum / FFT of any saved signal: per-harmonic "
                      "amplitudes, THD, and the frequency breakdown. Validated by "
                      "Parseval (the harmonics must account for the signal's "
                      "energy) and, where available, against an ideal-modulation "
                      "model. Returns a job id.")
def spectrum(topology: str, signal: str | None = None, n_max: int = 50,
             params: dict | None = None) -> dict:
    """signal: a raw trace name such as 'I(Lo)' or 'V(a)'; omitted = the
    topology's natural output current."""
    t = _build(topology, params)

    def work(job):
        from .harmonics import Spectrum, parseval_residual, spectrum_table
        from .waveforms import Waves
        job.progress = "simulating"
        sr = Study(t, runner=Runner(workroot=WORK / f"fft_{job.id}")).run(
            tag="fft", converge=False)
        if not sr.sim.ok:
            return {"usable": False, "verdict": _verdict(sr),
                    "checks": _check_payload(sr.checks)}
        w = Waves(sr.sim.raw_path)
        sig = signal or next((c for c in ("I(Lo)", "I(Loa)", "I(L1)", "I(Lb)")
                              if w.has(c)), None)
        if sig is None or not w.has(sig):
            raise ToolError(
                f"signal '{signal or 'auto'}' is not saved for {topology}. "
                f"Available traces: {', '.join(w.names)}")
        f1 = t.params().get("f1") or t.params().get("fs")
        n_cyc = getattr(t, "n_meas", None) or 2
        sp = Spectrum(w.t, w[sig], f1, t.measure_window()[1], n_cyc)
        resid = parseval_residual(sp, n_max)
        checks = list(sr.checks)
        checks.append(C_.Check(
            "Parseval: harmonics account for the signal energy",
            resid < 0.02, C_.ERROR,
            f"unaccounted mean square {resid*100:.4f} % "
            f"(a large residual means the analysis window is not an exact "
            f"integer number of periods, which smears every amplitude)",
            resid, 0.02))
        out = {
            "signal": sig, "fundamental_Hz": f1,
            "fundamental_amplitude": sp.h(1),
            "rms": sp.rms(), "dc": sp.dc,
            "thd_pct": sp.thd(n_max) * 100,
            "thd_including_switching_pct": sp.thd_total() * 100,
            "harmonics": spectrum_table(sp, f1, n_max),
            "parseval_residual_pct": resid * 100,
        }
        if hasattr(t, "ideal_spectrum") and sig.startswith("V("):
            ideal = t.ideal_spectrum()
            out["vs_ideal_modulation"] = [
                {"order": k, "simulated": sp.h(k),
                 "ideal_pwm": float(ideal[k]) if k < len(ideal) else 0.0}
                for k in (1, t.mf - 2, t.mf, t.mf + 2, 2 * t.mf - 1, 2 * t.mf + 1)
                if k < len(ideal)]
        ok = all(c.passed or c.severity != C_.ERROR for c in checks)
        out["usable"] = ok
        out["verdict"] = (
            f"spectrum validated (Parseval residual {resid*100:.4f} %); "
            f"THD {out['thd_pct']:.3f} % over harmonics 2..{n_max}"
            if ok else "NOT USABLE: " + "; ".join(
                c.name for c in checks if not c.passed))
        out["checks"] = _check_payload(checks)
        return out

    return _spawn("spectrum", work)


@tool(description="What a topology's model actually represents and what it "
                      "omits: modelled physics, unmodelled physics, which "
                      "quantities are trustworthy, comparison against published "
                      "benchmarks, and how to make it more realistic. Call this "
                      "before quoting efficiency, THD or loss figures, or when a "
                      "result disagrees with a paper.")
def assumptions(topology: str) -> dict:
    from .assumptions import describe
    reg = _registry()
    if topology not in reg:
        raise ToolError(f"unknown topology '{topology}'. "
                        f"Available: {', '.join(sorted(reg))}")
    d = describe(topology)
    t = reg[topology]()
    if hasattr(t, "deadtime"):
        d["current_defaults"] = {
            "deadtime_s": t.deadtime,
            "Vf_diode_V": getattr(t, "Vf_diode", None),
            "warning": ("deadtime = 0 is IDEALISED and gives roughly 10x better "
                        "THD than real hardware; set it to your gate driver's "
                        "value before comparing with any measurement or paper")
            if t.deadtime == 0 else None,
        }
    return d


@tool(description="List the published hardware references a topology can be "
                  "checked against: source document, URL, what was measured, and "
                  "the caveats that limit what the comparison can prove. Use "
                  "this before claiming any model agrees with reality.")
def list_benchmarks() -> dict:
    from . import benchmarks as B
    out = {}
    for f in sorted(B.BENCH_DIR.glob("*.json")):
        try:
            d = B.load(f.stem.replace("_", "-"))
        except Exception as e:
            out[f.stem] = {"error": str(e)}
            continue
        out[d["id"]] = {
            "title": d.get("title"),
            "publisher": d.get("publisher"),
            "document": d.get("document"),
            "url": d.get("url"),
            "table": d.get("table"),
            "retrieved_utc": d.get("retrieved_utc"),
            "n_measured_points": len(d.get("measured", [])) or None,
            "references": list(d.get("references", {})) or None,
            "caveats": d.get("caveats"),
            "derived_fields": d.get("derived_fields"),
        }
    return {
        "benchmarks": out,
        "how_to_read_a_result": {
            "MATCH": "model lands within tolerance of a MEASURED value. The "
                     "strongest claim available here.",
            "BOUNDED_OPTIMISM": "model may be better than hardware by no more "
                                "than a stated factor. Being WORSE than "
                                "hardware always fails. Expected for loss and "
                                "distortion, which the idealisation omits.",
            "TREND": "model reproduces how a quantity MOVES across the range, "
                     "even where its absolute value does not match.",
            "SPEC_BOUND": "model satisfies a published LIMIT (e.g. 'THD < 3 %'). "
                          "WEAKEST mode: it shows the model is not contradicted "
                          "by the specification, NOT that it agrees with "
                          "hardware.",
            "BRACKET": "sweep a missing-physics parameter until the measurement "
                       "falls inside the swept range.",
        },
        "rule": ("Never write 'validated against hardware' for a quantity that "
                 "passed only on TREND, BOUNDED_OPTIMISM or SPEC_BOUND. Name "
                 "the mode instead."),
    }


@tool(description="Run a topology against a published hardware benchmark and "
                  "report, per quantity, WHICH MODE of agreement it earned. "
                  "This is the only check here that can fail because the "
                  "physics is missing rather than the arithmetic. Long: returns "
                  "a job id, poll with job_status.")
def benchmark(benchmark_id: str = "tida-010203") -> dict:
    from . import benchmarks as B
    try:
        bench = B.load(benchmark_id)
    except FileNotFoundError as e:
        avail = sorted(f.stem.replace("_", "-")
                       for f in B.BENCH_DIR.glob("*.json"))
        raise ToolError(f"{e}. Available: {', '.join(avail)}")

    def work(job):
        import subprocess, sys as _s
        script = {"tida-010203": "tests/test_benchmarks.py",
                  "three-level-ttype": "tests/test_benchmark_threelevel.py"}.get(
                      benchmark_id)
        if script is None:
            raise ToolError(
                f"benchmark '{benchmark_id}' has reference data but no runner "
                f"script wired to it yet")
        job.progress = f"running {script}"
        r = subprocess.run([_s.executable, str(HERE / script), "--quick"],
                           capture_output=True, text=True, timeout=1800)
        lines = r.stdout.splitlines()
        crit = [l.strip() for l in lines if l.strip().startswith("[")]
        return {
            "usable": r.returncode == 0,
            "verdict": ("agrees with published hardware within every declared "
                        "mode" if r.returncode == 0 else
                        "DOES NOT AGREE -- at least one criterion failed"),
            "benchmark_id": benchmark_id,
            "source": {"publisher": bench.get("publisher"),
                       "document": bench.get("document"),
                       "url": bench.get("url"),
                       "table": bench.get("table")},
            "criteria": crit,
            "report": "\n".join(lines[-60:]),
            "caveats": bench.get("caveats"),
            "rule": ("Quote the MODE with every result. A quantity that passed "
                     "only on TREND, BOUNDED_OPTIMISM or SPEC_BOUND has NOT "
                     "been validated against hardware."),
        }

    return _spawn(f"benchmark:{benchmark_id}", work)


@tool(description="Check a topology's netlist for wiring faults that a "
                  "SUCCESSFUL simulation will not report: duplicate instance "
                  "names, components with a blank terminal, and freewheel "
                  "diodes that do not actually span a switch. Cheap, no "
                  "simulation. Run it after changing a netlist.")
def check_netlist(topology: str, params: dict | None = None) -> dict:
    t = _build(topology, params)
    problems = t.netlist_structure_problems()
    return {
        "usable": not problems,
        "verdict": ("netlist structure is sound" if not problems else
                    f"{len(problems)} structural problem(s) -- a simulation "
                    f"may still COMPLETE and return plausible numbers"),
        "topology": t.summary(),
        "problems": problems,
        "saved_traces": t.saved_traces(),
        "default_watch": t.default_watch(),
        "note": ("Found by this check on 2026-09-13: T-type freewheel diodes "
                 "sitting on a node nothing else touched (electrically inert, "
                 "results looked plausible) and an NPC diode emitted with a "
                 "blank terminal."),
    }


@tool(description="Check whether a parameter set describes a physically "
                  "realisable circuit, WITHOUT simulating. Returns the reason "
                  "if not. Use before a large sweep: a non-physical point does "
                  "not fail fast in LTspice, it stalls the solver for minutes.")
def validate_params(topology: str, params: dict | None = None) -> dict:
    try:
        t = _build(topology, params)
    except ToolError as e:
        return {"usable": False, "verdict": str(e)}
    try:
        t.validate()
    except ValueError as e:
        return {"usable": False, "verdict": f"NOT PHYSICAL: {e}",
                "topology": t.summary()}
    return {"usable": True, "verdict": "parameters describe a circuit",
            "topology": t.summary(), "params": dict(t.params()),
            "design_notes": t.design_notes()}


@tool(description="Datasheet-backed devices available for selection, with their "
                  "ratings and how complete each model's data is.")
def list_datasheet_devices() -> dict:
    from .devices.infineon import LIBRARY
    return {
        "devices": {n: {
            "part": d.part_number, "manufacturer": d.manufacturer,
            "V_rating": d.V_rating, "I_dc_25C": d.I_dc_25,
            "I_dc_100C": d.I_dc_100, "I_pulse": d.I_pulse,
            "Rds_on_25C_mohm": round(d.R_on(25) * 1e3, 1),
            "Rds_on_175C_mohm": round(d.R_on(175) * 1e3, 1),
            "Rth_jc_K_per_W": d.Rth_jc, "Tj_max_C": d.Tj_max,
            "data_coverage": d.coverage(),
        } for n, d in LIBRARY.items()},
        "note": ("Digitised from the datasheets in the project folder. Values "
                 "read from diagrams are marked; queries outside a curve's "
                 "measured range are flagged, never silently extrapolated."),
    }


@tool(description="Evaluate real devices in a topology and judge them: voltage, "
                  "current and thermal margins with industry derating, junction "
                  "temperature RIPPLE (not just the mean), pass/fail per device "
                  "with the reason. Returns a job id.")
def select_devices(topology: str, rail_device: str | None = None,
                   np_device: str | None = None, ambient_C: float = 40.0,
                   rth_heatsink: float = 0.05, L_loop_nH: float | None = None,
                   di_dt_A_per_ns: float | None = None,
                   params: dict | None = None) -> dict:
    from .devices import as_loss_device
    from .devices.infineon import LIBRARY as DS
    from .selection import evaluate_candidates
    t = _build(topology, params)
    if not t.switch_blocking_voltages():
        raise ToolError(f"{topology} does not declare switch blocking voltages")
    for n in (rail_device, np_device):
        if n and n not in DS:
            raise ToolError(f"unknown device '{n}'. Available: {', '.join(DS)}")

    def work(job):
        blocking = t.switch_blocking_voltages()
        vmax = max(blocking.values())
        rail = as_loss_device(DS[rail_device]) if rail_device else None
        npd = as_loss_device(DS[np_device]) if np_device else None
        dmap = {}
        for n, v in blocking.items():
            d = rail if v > vmax * 0.6 else npd
            if d is None:
                c = [x for x in DS.values() if x.V_rating >= v * 1.35]
                if not c:
                    raise ToolError(f"no device blocks {v:.0f} V with margin")
                d = as_loss_device(min(c, key=lambda x: x.V_rating))
            dmap[n] = d
        job.progress = "simulating and judging devices"
        cands, lr, sr = evaluate_candidates(
            t, dmap, Runner(workroot=WORK / f"sel_{job.id}"), Ta=ambient_C,
            Rth_hs=rth_heatsink, L_loop_nH=L_loop_nH,
            di_dt_A_per_ns=di_dt_A_per_ns)
        ok = all(c.ok for c in cands)
        pout = sr.extracted.get("P") or 0.0
        return {
            "usable": ok,
            "verdict": ("every device clears its derated limits" if ok else
                        "AT LEAST ONE DEVICE FAILS its derated limits -- see "
                        "the per-device reasons"),
            "total_device_loss_W": round(lr.P_total, 2),
            "output_power_W": round(pout, 1),
            "efficiency_pct": (round(pout / (pout + lr.P_total) * 100, 4)
                               if pout else None),
            "parasitics_assumed": L_loop_nH is None,
            "devices": [{
                "switch": c.switch, "device": c.device.name,
                "V_block": c.V_block,
                "V_peak_with_overshoot": round(c.V_peak, 1),
                "I_rms": round(c.I_rms, 2), "I_peak": round(c.I_peak, 2),
                "P_cond_W": round(c.P_cond, 2), "P_sw_W": round(c.P_sw, 2),
                "Tj_mean_C": round(c.Tj_mean, 1),
                "Tj_peak_C": round(c.Tj_peak, 1),
                "delta_Tj_K": round(c.delta_Tj, 1),
                "passes": c.ok, "failures": c.reasons,
            } for c in cands],
            "note": ("Tj_peak includes ripple over a fundamental cycle. "
                     "Selecting on Tj_mean hides roughly half of it."),
        }

    return _spawn("select_devices", work)


@tool(description="Poll a background job started by sweep, tolerance, losses "
                      "or fault.")
def job_status(job_id: str) -> dict:
    with _LOCK:
        job = JOBS.get(job_id)
    if job is None:
        return {"error": f"no such job '{job_id}'",
                "known_jobs": sorted(JOBS)}
    out = {"job_id": job.id, "kind": job.kind, "status": job.status,
           "progress": job.progress}
    if job.status == "done":
        out["result"] = job.result
    elif job.status == "failed":
        out["error"] = job.error
        out["log"] = job.log[-6:]
    return out


@tool(description="List the representative device models available for loss "
                      "analysis.")
def list_devices() -> dict:
    from .losses import LIBRARY
    return {
        "caveat": ("these are REPRESENTATIVE parameters, not real part numbers; "
                   "substitute datasheet values before trusting absolute loss or "
                   "temperature numbers"),
        "devices": {n: {"kind": d.kind, "V_rating": d.V_rating,
                        "I_rating": d.I_rating,
                        "Rds_on_25C_mohm": (round(d.Rds_25 * 1e3, 2)
                                            if d.kind == "mosfet" else None),
                        "Eon_uJ": round(d.Eon_ref * 1e6, 1),
                        "Eoff_uJ": round(d.Eoff_ref * 1e6, 1),
                        "Rth_jc_K_per_W": d.Rth_jc, "Tj_max_C": d.Tj_max}
                    for n, d in LIBRARY.items()},
    }


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
