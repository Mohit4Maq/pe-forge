"""ngspice assertion gate for the small analog circuits pwrlab does not cover (sense amplifiers, dividers into ADCs,
gate-drive RC, UVLO / OVP thresholds, feedback dividers, NTC bias).

  forge spice check [--module M-xx] [--strict] [--json]

A sheet brief carries a `## Simulation` block (grammar in skills/pe-simulate/references/assertions.md):

  ## Simulation
  scope: nets ISENSE_P, ISENSE_N, ADC_IOUT, GND
  analysis: ac 10 1e7
  source: ISENSE_P = 0.05 ac 1
  assert: gain(ADC_IOUT, ISENSE_P) between 19.5 and 20.5 at 1k
  expect: 20
  assert: f3db(ADC_IOUT) between 180k and 220k
  assert: V(ADC_IOUT) < 3.3 at op

The circuit is KiCad's own SPICE export of the drawn schematic, cut to the scope: a part is kept only when every one
of its nets is in the scope (ground always is), so the sources define the boundary. Passives get KiCad's ideal
models; any other kept part needs a model (fields Sim_Model + Sim_Pins, or KiCad's own Sim.* fields), else its
assertions are SKIPPED with the part named (FAIL with --strict). Each block runs once in `ngspice -b`; the measures
are computed from the raw data, not from ngspice's .meas.
Second source (Rule 1): `expect:` after an assertion gives the closed-form value; ngspice and the closed form must
agree within 2 %, or the result is PROVISIONAL even when the bound holds.
Results go to <module>/Simulations/<brief>_spice.md as a Simulation Verification Record (ACCEPTED / PROVISIONAL /
REJECTED per assertion), which gate G5 counts.
"""
import datetime as dt
import glob
import json
import math
import os
import re
import shutil
import subprocess
import tempfile

from . import kicad

SI = {"f": 1e-15, "p": 1e-12, "n": 1e-9, "u": 1e-6, "µ": 1e-6, "m": 1e-3, "k": 1e3, "K": 1e3, "meg": 1e6, "M": 1e6,
      "g": 1e9, "G": 1e9}
CF_TOL = 0.02


def num(s):
    s = s.strip()
    m = re.fullmatch(r"([-+]?[0-9]*\.?[0-9]+(?:[eE][-+]?\d+)?)\s*(meg|[fpnuµmkKMgG])?\s*[A-Za-zΩ/%]*", s)
    if not m:
        raise ValueError(f"not a number: {s}")
    return float(m.group(1)) * SI.get(m.group(2) or "", 1.0)


def find_ngspice():
    env = os.environ.get("NGSPICE")
    if env is not None:
        return env if os.path.exists(env) else None
    return next((p for p in (shutil.which("ngspice"), "/opt/homebrew/bin/ngspice", "/usr/local/bin/ngspice",
                             "/usr/bin/ngspice") if p and os.path.exists(p)), None)


# ------------------------------------------------------------------ the brief block
def parse_block(text):
    """None, or {scope, analysis, sources, asserts[{text, expr, cmp, lo, hi, at, expect}]}."""
    m = re.search(r"^##\s+Simulation\s*$(.*?)(?=^##\s|\Z)", text, re.S | re.M)
    if not m:
        return None
    blk = {"scope": [], "analysis": "op", "sources": [], "asserts": [], "errors": []}
    for raw in m.group(1).split("\n"):
        line = raw.strip().strip("`").lstrip("-* ").strip()
        if ":" not in line:
            continue
        key, val = [x.strip() for x in line.split(":", 1)]
        key = key.lower()
        if key == "scope":
            blk["scope"] = [x for x in re.split(r"[,\s]+", re.sub(r"^nets?\s+", "", val)) if x]
        elif key == "analysis":
            blk["analysis"] = val
        elif key == "source":
            n, _, spec = val.partition("=")
            blk["sources"].append((n.strip(), spec.strip()))
        elif key == "assert":
            try:
                blk["asserts"].append(parse_assert(val))
            except ValueError as e:
                blk["errors"].append(f"{val}: {e}")
        elif key == "expect" and blk["asserts"]:
            try:
                blk["asserts"][-1]["expect"] = num(val.split()[0])
            except ValueError as e:
                blk["errors"].append(f"expect {val}: {e}")
    return blk


AS = re.compile(r"^(?P<expr>\w+\([^)]*\))\s+(?:(?P<op><|>|<=|>=)\s*(?P<v>\S+)|between\s+(?P<lo>\S+)\s+and\s+(?P<hi>\S+))"
                r"(?:\s+at\s+(?P<at>\S+))?\s*$", re.I)


def parse_assert(s):
    m = AS.match(s.strip())
    if not m:
        raise ValueError("expected `<measure>(...) between A and B [at X]` or `<measure>(...) < A [at X]`")
    e = m.group("expr")
    fn, args = re.match(r"(\w+)\((.*)\)", e).groups()
    fn = fn.lower()
    if fn not in ("v", "i", "gain", "f3db", "peak", "settle"):
        raise ValueError(f"unknown measure {fn} (V, I, gain, f3db, peak, settle)")
    a = {"text": s, "fn": fn, "args": [x.strip() for x in args.split(",") if x.strip()], "at": m.group("at"),
         "expect": None}
    if m.group("op"):
        v = num(m.group("v"))
        a["lo"], a["hi"] = (v, math.inf) if ">" in m.group("op") else (-math.inf, v)
    else:
        a["lo"], a["hi"] = num(m.group("lo")), num(m.group("hi"))
    return a


# ------------------------------------------------------------------ circuit
def spice_export(sch, workdir):
    cli = kicad.find_cli()
    out = os.path.join(workdir, "design.cir")
    subprocess.run([cli, "sch", "export", "netlist", "--format", "spice", "-o", out, sch], capture_output=True)
    return open(out).read() if os.path.exists(out) else None


GROUND_NET = re.compile(r"^/?(GND|AGND|DGND|GNDA|GNDD|GNDPWR|0)$", re.I)


def S(net):
    """A deck-safe node name: ground -> 0; KiCad's /NAME and NAME -> n_NAME (ngspice reads '/' as division)."""
    if GROUND_NET.match(net or ""):
        return "0"
    return "n_" + re.sub(r"[^A-Za-z0-9_]", "_", net.lstrip("/"))


def cut(cir, comps, scope_nodes):
    """(netlist lines kept, unmodelled refs inside the scope). A part is kept when all its nodes are in scope."""
    keep, missing = [], []
    inside = lambda nodes: all(n in scope_nodes or n == "0" for n in nodes)
    for line in cir.split("\n"):
        s = line.strip()
        if not s or s.startswith(("*", ".title", ".end")):
            continue
        if s.startswith("."):
            keep.append(s)            # models / options KiCad wrote
            continue
        parts = s.split()
        ref = parts[0]
        if ref.startswith("__") or (len(parts) > 1 and parts[1].startswith("__")):   # KiCad: "D1 __D1" = no model
            r = ref[2:] if ref.startswith("__") else ref
            c = comps.get(r, {})
            nodes = [S(p["net"]) for p in c.get("pins", {}).values()]
            if nodes and inside(nodes):
                extra = sim_fields(r, c)
                if extra:
                    keep += extra
                else:
                    missing.append(r)
            continue
        nodes = [S(n) for n in parts[1:3]]
        if inside(nodes):
            keep.append(" ".join([ref, *nodes, *parts[3:]]))
    return keep, missing


def sim_fields(ref, c):
    """Element line(s) from MC_Parts Sim_Model ('<element> <model> | <model card>...') and Sim_Pins ('2 1')."""
    f = c.get("fields", {})
    model, pins = (f.get("Sim_Model") or "").strip(), (f.get("Sim_Pins") or "").split()
    if not model or not pins:
        return None
    elem, *cards = [x.strip() for x in model.split("|")]
    nodes = []
    for p in pins:
        net = c.get("pins", {}).get(p, {}).get("net")
        if net is None:
            return None
        nodes.append(S(net))
    letter = elem.split()[0] if elem else "X"
    name = ref if ref.upper().startswith(letter.upper()) else letter + ref
    return [f"{name} {' '.join(nodes)} {' '.join(elem.split()[1:])}"] + cards


def analysis_line(a):
    w = a.split()
    k = w[0].lower()
    if k == "op":
        return "op", "op"
    if k == "ac":
        if len(w) == 3:                  # "ac 10 1e7" -> 50 points per decade
            return "ac", f"ac dec 50 {w[1]} {w[2]}"
        return "ac", "ac " + " ".join(w[1:])
    if k == "tran":
        return "tran", "tran " + " ".join(w[1:])
    if k == "dc":
        return "dc", "dc " + " ".join(w[1:])
    raise ValueError(f"analysis {a}: use op, dc, ac or tran")


def source_lines(srcs):
    out = []
    for net, spec in srcs:
        dc = re.match(r"^\s*([-+0-9.eE]+\s*(?:meg|[fpnuµmkKMgG])?)(?=\s|$)", spec)
        rest = spec[dc.end():].strip() if dc else spec
        val = f"dc {num(dc.group(1))}" if dc else "dc 0"
        out.append(f"V_{re.sub(r'[^A-Za-z0-9_]', '_', net)} {S(net)} 0 {val} {rest}".strip())
    return out


def run_block(blk, cir, comps, ng, workdir):
    """(per-assert results, notes)."""
    scope = blk["scope"] or [n for n, _ in blk["sources"]]
    scope_nodes = {S(n) for n in scope + [n for n, _ in blk["sources"]]} - {"0"}
    lines, missing = cut(cir, comps, scope_nodes)
    kind, an = analysis_line(blk["analysis"])
    srcs = source_lines(blk["sources"])
    probes = []
    for a in blk["asserts"]:
        for x in a["args"]:
            if not re.fullmatch(r"[\d.]+%?", x):
                probes.append(x)
    probes = list(dict.fromkeys(probes))
    vecs = []
    for p in probes:
        node = S(p)
        vecs.append(("v", p, "0" if node == "0" else (f"vm({node})" if kind == "ac" else f"v({node})")))
    for a in blk["asserts"]:
        if a["fn"] == "i":
            vecs.append(("i", a["args"][0], f"i(v_{re.sub(r'[^A-Za-z0-9_]', '_', a['args'][0]).lower()})"))
    data = os.path.join(workdir, "out.txt")
    ctl = [".control", "set wr_singlescale", "set wr_vecnames", an,
           "wrdata " + data + " " + " ".join(v for _, _, v in vecs if v != "0"), ".endc"]
    deck = "\n".join(["* pe-forge spice assertion deck"] + lines + srcs + ctl + [".end"]) + "\n"
    open(os.path.join(workdir, "deck.cir"), "w").write(deck)
    r = subprocess.run([ng, "-b", os.path.join(workdir, "deck.cir")], capture_output=True, text=True, timeout=300)
    log = r.stdout + r.stderr
    open(os.path.join(workdir, "ngspice.log"), "w").write(log)
    if missing:
        return [dict(a, status="skip", detail=f"no SPICE model for {', '.join(sorted(missing))} (set Sim_Model / Sim_Pins)")
                for a in blk["asserts"]], deck, log
    if not os.path.exists(data) or re.search(r"\b(singular matrix|Error|error:|no such vector)\b", log):
        err = next((l for l in log.split("\n") if re.search(r"error|singular", l, re.I)), "ngspice wrote no data")
        return [dict(a, status="fail", detail=f"ngspice: {err.strip()[:160]}") for a in blk["asserts"]], deck, log
    rows = [l.split() for l in open(data) if l.strip()]
    head, rows = rows[0], [[float(x) for x in r] for r in rows[1:]]
    col = {h: i for i, h in enumerate(head)}
    xs = [r[0] for r in rows] if kind != "op" else [0.0]
    series = {}
    for typ, name, vec in vecs:
        if vec == "0":
            series[(typ, name)] = [0.0] * len(rows)
        else:
            i = col.get(vec, col.get(vec.lower()))
            series[(typ, name)] = [r[i] for r in rows] if i is not None else None
    res = []
    for a in blk["asserts"]:
        try:
            val = measure(a, kind, xs, series)
        except ValueError as e:
            res.append(dict(a, status="fail", detail=str(e)))
            continue
        ok = a["lo"] <= val <= a["hi"]
        cf = None
        if a["expect"] is not None:
            cf = (val - a["expect"]) / a["expect"] if a["expect"] else val
        st = "pass" if ok else "fail"
        detail = f"measured {fmt(val)}; bound {bound(a)}"
        if cf is not None:
            detail += f"; closed form {fmt(a['expect'])} ({cf * 100:+.1f} %)"
            if ok and abs(cf) > CF_TOL:
                st = "provisional"
                detail += f": ngspice and the closed form disagree by more than {CF_TOL * 100:g} %"
        res.append(dict(a, status=st, value=val, detail=detail))
    return res, deck, log


def fmt(v):
    return f"{v:.4g}"


def bound(a):
    if a["lo"] == -math.inf:
        return f"< {fmt(a['hi'])}"
    if a["hi"] == math.inf:
        return f"> {fmt(a['lo'])}"
    return f"{fmt(a['lo'])} to {fmt(a['hi'])}"


def _at(xs, ys, x):
    if len(xs) == 1:
        return ys[0]
    for (x0, y0), (x1, y1) in zip(zip(xs, ys), zip(xs[1:], ys[1:])):
        if x0 <= x <= x1 or x1 <= x <= x0:
            return y0 + (y1 - y0) * ((x - x0) / (x1 - x0) if x1 != x0 else 0)
    raise ValueError(f"point {x:g} outside the simulated range {xs[0]:g} to {xs[-1]:g}")


def measure(a, kind, xs, series):
    fn, args = a["fn"], a["args"]
    get = lambda typ, n: series.get((typ, n)) or (_ for _ in ()).throw(ValueError(f"no data for {n}"))
    at = None if a["at"] in (None, "op") else num(a["at"])
    if fn == "v":
        ys = get("v", args[0])
        return ys[-1] if at is None else _at(xs, ys, at)
    if fn == "i":
        ys = get("i", args[0])
        return ys[-1] if at is None else _at(xs, ys, at)
    if fn == "gain":
        out, inp = get("v", args[0]), get("v", args[1])
        g = [o / i if i else math.inf for o, i in zip(out, inp)]
        return g[0] if at is None and kind in ("op", "ac") else (g[-1] if at is None else _at(xs, g, at))
    if fn == "f3db":
        if kind != "ac":
            raise ValueError("f3db needs an ac analysis")
        ys = get("v", args[0])
        ref = ys[0]
        target = ref / math.sqrt(2)
        for (f0, y0), (f1, y1) in zip(zip(xs, ys), zip(xs[1:], ys[1:])):
            if y0 >= target > y1:
                lf0, lf1 = math.log10(f0), math.log10(f1)
                return 10 ** (lf0 + (lf1 - lf0) * (y0 - target) / (y0 - y1))
        raise ValueError("no -3 dB point inside the swept range")
    if fn == "peak":
        return max(get("v", args[0]))
    if fn == "settle":
        ys = get("v", args[0])
        tol = float(args[1].rstrip("%")) / 100 if len(args) > 1 else 0.02
        fin = ys[-1]
        for i in range(len(ys) - 1, -1, -1):
            if abs(ys[i] - fin) > tol * abs(fin):
                return xs[min(i + 1, len(xs) - 1)]
        return xs[0]
    raise ValueError(fn)


# ------------------------------------------------------------------ the run
def briefs(prj, module=None):
    out = []
    from .drift import frontmatter
    try:
        from . import state
        mods = state.load(prj)["modules"]
    except Exception:
        mods = []
    for m in mods:
        if module and m["id"] != module:
            continue
        b = os.path.join(prj.root, m["dir"], "Sheet_Brief.md")
        if os.path.exists(b):
            out.append((m, b))
    if not module:
        out += [(None, b) for b in glob.glob(prj.p("briefs", "*.md"))]
    res = []
    for m, b in out:
        text = open(b, errors="ignore").read()
        blk = parse_block(text)
        if blk:
            sheet = frontmatter(text).get("kicad_sheet")
            res.append((m, b, blk, os.path.join(prj.root, sheet) if sheet and not sheet.startswith("08_PCB/<") else None))
    return res


def run(prj, module=None, strict=False):
    from .check import roots
    ng = find_ngspice()
    found = briefs(prj, module)
    if not found:
        return {"status": "skip", "reason": "no `## Simulation` block in any sheet brief", "results": []}
    if not ng:
        return {"status": "skip", "reason": "ngspice not found (brew install ngspice / apt install ngspice, or set NGSPICE)",
                "missing_tool": True, "results": []}
    results, records = [], []
    for m, brief, blk, sheet in found:
        sch = sheet or next(iter(roots(prj, "kicad_sch")), None)
        rel = prj.rel(brief)
        if blk["errors"]:
            results += [{"brief": rel, "text": e, "status": "fail", "detail": "assertion does not parse"} for e in blk["errors"]]
        if not sch or not os.path.exists(sch):
            results += [{"brief": rel, "text": a["text"], "status": "skip", "detail": "no schematic"} for a in blk["asserts"]]
            continue
        wd = tempfile.mkdtemp(prefix="forge-spice-")
        cir = spice_export(sch, wd)
        d = kicad.design(sch)
        if cir is None or d is None:
            results += [{"brief": rel, "text": a["text"], "status": "fail", "detail": "KiCad SPICE export failed"}
                        for a in blk["asserts"]]
            continue
        try:
            res, deck, log = run_block(blk, cir, d[0], ng, wd)
        except ValueError as e:
            res, deck, log = [dict(a, status="fail", detail=str(e)) for a in blk["asserts"]], "", ""
        for r in res:
            r["brief"] = rel
        results += res
        records.append(write_svr(prj, m, brief, blk, res, deck, prj.rel(sch)))
    st = [r["status"] for r in results]
    if "fail" in st or (strict and "skip" in st):
        status = "fail"
    elif "provisional" in st or "skip" in st:
        status = "warn"
    else:
        status = "pass"
    return {"status": status, "reason": f"{len(results)} assertion(s)", "results": results, "records": records}


def write_svr(prj, m, brief, blk, res, deck, sch_rel):
    d = os.path.join(prj.root, m["dir"], "Simulations") if m else prj.p("sim_results")
    os.makedirs(d, exist_ok=True)
    stem = os.path.splitext(os.path.basename(brief))[0]
    p = os.path.join(d, f"{stem}_spice.md")
    grade = {"pass": "ACCEPTED", "provisional": "PROVISIONAL", "fail": "REJECTED", "skip": "NOT RUN"}
    lines = [f"---\ntype: simulation\ntool: ngspice (forge spice check)\ndate: {dt.date.today().isoformat()}\n---",
             f"# {stem}: ngspice assertions", "", f"Brief: `{prj.rel(brief)}` · schematic: `{sch_rel}` · analysis: "
             f"`{blk['analysis']}` · scope: {', '.join(blk['scope']) or '(sources)'}", "",
             "## Results", "", "| Assertion | Result | Detail |", "|---|---|---|"]
    lines += [f"| `{r['text']}` | {grade[r['status']]} | {r['detail']} |" for r in res]
    lines += ["", "## Simulation Verification Record (SVR)", "", "| Item | Record |", "|---|---|",
              "| Model provenance | KiCad SPICE export of the drawn schematic, cut to the scope; ideal passives |",
              "| Anchor | the `expect` closed form per assertion (agreement in Detail); none where no expect is given |",
              "| Convergence | ngspice batch run, log checked for errors and singular matrix |",
              "| Corners | as written in the brief's Simulation block (add one block per corner) |",
              "| Reproduce | `forge spice check" + (f" --module {m['id']}" if m else "") + "` |", "",
              "## Deck", "", "```", re.sub(r"wrdata \S+", "wrdata out.txt", deck.strip()), "```", ""]
    open(p, "w").write("\n".join(lines))
    return prj.rel(p)


def as_check(prj, opts):
    from .check import result, finding
    r = run(prj, opts.get("module"), opts.get("strict"))
    if r["status"] == "skip":
        return result("skip", reason=r["reason"], missing_tool=r.get("missing_tool", False))
    F = [finding(a["text"], f"{a['status'].upper()}: {a['detail']}", a["brief"]) for a in r["results"] if a["status"] != "pass"]
    return result(r["status"], F, reason=r["reason"])


def main(prj, argv):
    if not argv or argv[0] != "check":
        print(__doc__)
        return 1
    mod = argv[argv.index("--module") + 1] if "--module" in argv else None
    r = run(prj, mod, "--strict" in argv)
    if "--json" in argv:
        print(json.dumps(r, indent=1, default=str))
    else:
        if r["status"] == "skip":
            print(f"spice: skipped ({r['reason']})")
        for a in r["results"]:
            print(f"{a['status'].upper():<11} {a['brief']}: {a['text']}  ({a['detail']})")
        for rec in r.get("records", []):
            print(f"record: {rec}")
    strict_skip = "--strict" in argv and r["status"] == "skip"
    return 1 if r["status"] == "fail" or strict_skip else 0
