"""Command line interface.

    pwrlab list
    pwrlab run dab --phi_deg 45
    pwrlab run buck --Vin 48 --D 0.6 --Rload 4
    pwrlab sweep dab --phi_deg 10,20,30,45,60,90 --csv out.csv --html out.html
    pwrlab doctor
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .sim import Runner, default_command
from .study import Study
from .report import result_markdown, sweep_markdown, html_report

REGISTRY = {}


def _load_registry():
    from .topologies.dab import DAB
    from .topologies.buck import Buck
    from .topologies.inverter import HBridgeInverter
    from .topologies.threelevel import (TTypeInverter, NPCInverter,
                                        TType3Phase, NPC3Phase)
    from .topologies.pfc import TotemPolePFC
    REGISTRY.update({"dab": DAB, "buck": Buck, "hbridge": HBridgeInverter,
                     "ttype": TTypeInverter, "npc": NPCInverter,
                     "ttype3ph": TType3Phase, "npc3ph": NPC3Phase,
                     "pfc": TotemPolePFC})


def _coerce(v: str):
    low = v.strip().lower()
    if low in ("true", "yes"):
        return True
    if low in ("false", "no"):
        return False
    try:
        return int(v) if v.strip().lstrip("+-").isdigit() else float(v)
    except ValueError:
        return v


def _kwargs_from(unknown):
    """--name value / --name=value  ->  {name: coerced}"""
    out, i = {}, 0
    while i < len(unknown):
        tok = unknown[i]
        if not tok.startswith("--"):
            i += 1
            continue
        if "=" in tok:
            k, v = tok[2:].split("=", 1)
            i += 1
        else:
            k = tok[2:]
            v = unknown[i + 1] if i + 1 < len(unknown) else ""
            i += 2
        out[k] = v
    return out


def cmd_list(a, unknown):
    for name, cls in sorted(REGISTRY.items()):
        t = cls()
        print(f"{name:8s} {cls.__doc__.splitlines()[0] if cls.__doc__ else ''}")
        print(f"         params: {', '.join(t.params())}")
    return 0


def cmd_doctor(a, unknown):
    print("simulator command:")
    try:
        for c in default_command():
            print(f"  {c}")
    except FileNotFoundError as e:
        print(f"  NOT FOUND: {e}")
        return 1
    from .topologies.buck import Buck
    print("\nrunning a quick buck to prove the toolchain works end to end...")
    r = Study(Buck(Nsettle=150, Nmeas=10)).run(tag="doctor", converge=False)
    print(f"  {r.checks.summary()}")
    for c in r.checks.failures:
        print(f"  {c}")
    return 0 if r.ok else 1


def _build(name, kw):
    if name not in REGISTRY:
        sys.exit(f"unknown topology '{name}'. Known: {', '.join(sorted(REGISTRY))}")
    cls = REGISTRY[name]
    typed = {k: _coerce(v) for k, v in kw.items()}
    bad = [k for k in typed if k not in cls().params() and not hasattr(cls(), k)]
    if bad:
        sys.exit(f"unknown parameter(s) for {name}: {', '.join(bad)}")
    return cls(**typed)


def cmd_run(a, unknown):
    topo = _build(a.topology, _kwargs_from(unknown))
    r = Study(topo, runner=Runner(keep=a.keep, workroot=a.workdir)).run(
        converge=not a.no_converge)
    print(r.report())
    if a.md:
        Path(a.md).write_text(result_markdown(r))
        print(f"\nwrote {a.md}")
    if a.html:
        html_report(r, a.html, title=topo.summary())
        print(f"wrote {a.html}")
    return 0 if r.ok else 2


def cmd_sweep(a, unknown):
    kw = _kwargs_from(unknown)
    grid = {k: [_coerce(x) for x in v.split(",")] for k, v in kw.items() if "," in v}
    fixed = {k: v for k, v in kw.items() if "," not in v}
    if not grid:
        sys.exit("sweep needs at least one comma-separated parameter, "
                 "e.g. --phi_deg 10,20,30")
    topo = _build(a.topology, fixed)
    st = Study(topo, runner=Runner(keep=a.keep, workroot=a.workdir))
    df = st.sweep(grid, converge=not a.no_converge)
    import pandas as pd
    pd.set_option("display.width", 220)
    print("\n" + df.to_string(index=False, float_format=lambda v: f"{v:.6g}"))
    if a.csv:
        df.to_csv(a.csv, index=False)
        print(f"wrote {a.csv}")
    if a.md:
        Path(a.md).write_text(sweep_markdown(df, title=f"{topo.name} sweep"))
        print(f"wrote {a.md}")
    if a.html:
        html_report(df.attrs["results"], a.html, title=f"{topo.name} sweep")
        print(f"wrote {a.html}")
    return 0 if df["ok"].all() else 2


def cmd_loss(a, unknown):
    from .losses import LIBRARY, analyse_losses, LossAnalysis, required_heatsink
    topo = _build(a.topology, _kwargs_from(unknown))
    if a.device not in LIBRARY:
        sys.exit(f"unknown device '{a.device}'. Known: {', '.join(LIBRARY)}")
    dev = LIBRARY[a.device]
    sw = topo.switch_blocking_voltages()
    if not sw:
        sys.exit(f"{topo.name} does not declare switch blocking voltages")
    runner = Runner(keep=a.keep, workroot=a.workdir)
    lr, sr = analyse_losses(topo, {n: dev for n in sw}, runner,
                            Ta=a.ambient, Rth_hs=a.rth)
    lr.P_out = sr.extracted.get("P_out") or sr.extracted.get("P")
    print(lr.summary())
    print()
    for c in LossAnalysis.checks(lr):
        print(" ", c)
    if a.size_heatsink:
        best = required_heatsink(topo, {n: dev for n in sw}, runner,
                                 Ta=a.ambient, Tj_target=a.size_heatsink)
        if best:
            print(f"\nlargest heatsink for Tj <= {a.size_heatsink:g} C: "
                  f"Rth <= {best[0]:.4f} K/W  "
                  f"({best[1].P_total:.1f} W, Tj {best[1].Tj_max:.1f} C)")
        else:
            print(f"\nno heatsink keeps Tj <= {a.size_heatsink:g} C")
    return 0 if all(c.passed for c in LossAnalysis.checks(lr)) else 2


def cmd_mc(a, unknown):
    from .montecarlo import ToleranceStudy, Tol
    kw = _kwargs_from(unknown)
    tols, fixed = {}, {}
    for k, v in kw.items():
        if k.startswith("tol_"):
            tols[k[4:]] = Tol(float(v))
        else:
            fixed[k] = v
    if not tols:
        sys.exit("give at least one tolerance, e.g. --tol_L 0.2")
    topo = _build(a.topology, fixed)
    ts = ToleranceStudy(topo, tols, runner=Runner(keep=a.keep,
                                                  workroot=a.workdir))
    outs = [o.strip() for o in a.outputs.split(",")]
    print(ts.sensitivity(outs).to_string(index=False,
                                         float_format=lambda v: f"{v:+.4f}"))
    if a.n:
        r = ts.monte_carlo(outs, n=a.n, seed=a.seed)
        print("\n" + r.summary())
        if a.csv:
            r.df.to_csv(a.csv, index=False)
            print(f"wrote {a.csv}")
    return 0


def cmd_fault(a, unknown):
    from .transients import (TransientStudy, StartUp, LoadStep, ShortCircuit)
    topo = _build(a.topology, _kwargs_from(unknown))
    ev = {"startup": lambda: StartUp(),
          "loadstep": lambda: LoadStep(t=a.at, factor=a.factor),
          "short": lambda: ShortCircuit(t=a.at)}[a.event]()
    watch = {}
    for w in a.watch.split(","):
        watch[w.strip()] = {"settle_band": 0.02}
    if a.limit:
        first = a.watch.split(",")[0].strip()
        watch[first]["limit_hi"] = a.limit
    r = TransientStudy(topo, Runner(keep=a.keep, workroot=a.workdir)).run(
        ev, watch, tstop=a.tstop)
    print(r.summary())
    return 0 if r.ok else 2


def cmd_clean(a, unknown):
    from .sim import prune_work, dir_size
    from pathlib import Path as _P
    root = _P(a.dir) if a.dir else _P(__file__).resolve().parent.parent / "work"
    before = dir_size(root)
    r = prune_work(root, keep_recent=a.keep_recent, max_bytes=a.max_mb * 1_000_000,
                   dry_run=a.dry_run)
    print(f"scratch root : {root}")
    print(f"before       : {before/1e6:.1f} MB")
    print(f"{'would remove' if a.dry_run else 'removed'}      : "
          f"{len(r['removed'])} run dirs, {r['freed_bytes']/1e6:.1f} MB")
    print(f"remaining    : {r['remaining_bytes']/1e6:.1f} MB")
    return 0


def cmd_coverage(a, unknown):
    from .coverage import report, summary
    print(report())
    return 0 if not summary()["missing_critical"] else 0


def main(argv=None):
    _load_registry()
    ap = argparse.ArgumentParser(prog="pwrlab", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list").set_defaults(fn=cmd_list)
    sub.add_parser("doctor").set_defaults(fn=cmd_doctor)
    for nm, fn in (("run", cmd_run), ("sweep", cmd_sweep)):
        p = sub.add_parser(nm)
        p.add_argument("topology")
        p.add_argument("--csv"); p.add_argument("--md"); p.add_argument("--html")
        p.add_argument("--workdir"); p.add_argument("--keep", action="store_true")
        p.add_argument("--no-converge", action="store_true",
                       help="skip the halved-timestep convergence run")
        p.set_defaults(fn=fn)
    pl = sub.add_parser("loss")
    pl.add_argument("topology")
    pl.add_argument("--device", default="SiC_1200V_40m")
    pl.add_argument("--ambient", type=float, default=40.0)
    pl.add_argument("--rth", type=float, default=0.3)
    pl.add_argument("--size-heatsink", type=float, default=None,
                    metavar="TJ_TARGET")
    pl.add_argument("--workdir"); pl.add_argument("--keep", action="store_true")
    pl.set_defaults(fn=cmd_loss)

    pm = sub.add_parser("mc")
    pm.add_argument("topology")
    pm.add_argument("--outputs", required=True)
    pm.add_argument("-n", type=int, default=0, help="Monte Carlo samples")
    pm.add_argument("--seed", type=int, default=0)
    pm.add_argument("--csv")
    pm.add_argument("--workdir"); pm.add_argument("--keep", action="store_true")
    pm.set_defaults(fn=cmd_mc)

    pf = sub.add_parser("fault")
    pf.add_argument("topology")
    pf.add_argument("--event", choices=["startup", "loadstep", "short"],
                    default="startup")
    pf.add_argument("--at", type=float, default=1.5e-3)
    pf.add_argument("--factor", type=float, default=0.5)
    pf.add_argument("--tstop", type=float, default=None)
    pf.add_argument("--watch", default="V(out),I(L1)")
    pf.add_argument("--limit", type=float, default=None)
    pf.add_argument("--workdir"); pf.add_argument("--keep", action="store_true")
    pf.set_defaults(fn=cmd_fault)

    sub.add_parser("coverage", help="validation coverage and gaps"
                   ).set_defaults(fn=cmd_coverage)

    pc = sub.add_parser("clean", help="prune the scratch directory")
    pc.add_argument("--dir", default=None)
    pc.add_argument("--keep-recent", type=int, default=5)
    pc.add_argument("--max-mb", type=int, default=500)
    pc.add_argument("--dry-run", action="store_true")
    pc.set_defaults(fn=cmd_clean)

    a, unknown = ap.parse_known_args(argv)
    return a.fn(a, unknown)


if __name__ == "__main__":
    sys.exit(main())
