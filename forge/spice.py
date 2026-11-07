"""ngspice assertion gate for the small analog circuits pwrlab does not cover (sense amplifiers, dividers into ADCs,
gate-drive RC, UVLO / OVP thresholds, feedback dividers, NTC bias). Grammar and semantics:
skills/pe-simulate/references/assertions.md.

  forge spice check [--module M-xx] [--strict] [--json]

What makes a result ACCEPTED (anything less is PROVISIONAL, REJECTED or NOT RUN, and the record says why):
  - the circuit is exactly the drawn one inside the scope: KiCad's own SPICE export, every part whose nets all lie in
    the scope. A part that touches the scope AND a net outside it stops the block (dropping it would silently change
    the circuit) unless the brief lists it under `ignore:`;
  - every source and probe names a net that exists and is in the simulated circuit; there is exactly one ground
    reference; every net gets its own node (no name collisions);
  - ngspice ran clean (exit code, log scanned for errors, singular matrix, timestep too small, aborted runs, data
    layout checked);
  - the value is inside the bound AND agrees with the `expect:` closed form (2 % or the stated tolerance); without
    `expect:` it is PROVISIONAL (Rule 1: no second source);
  - tran only: a re-run at half the time step moves the value by under 1 %.
Records: <module>/Simulations/<brief>_spice.md with the SVR. Its frontmatter carries `result:` and `inputs:` (a hash of
the Simulation block, the sheet and this tool), and gate G5 refuses a record that is REJECTED, NOT RUN or stale.
"""
import datetime as dt
import glob
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import tempfile

from . import kicad

TOOL_VERSION = "spice-2"            # in the record's input hash: a tool change makes old records stale
CF_TOL = 0.02                       # ngspice vs closed form
DT_TOL = 0.01                       # tran: half time step re-run
TIMEOUT = 120
SI = {"f": 1e-15, "p": 1e-12, "n": 1e-9, "u": 1e-6, "µ": 1e-6, "μ": 1e-6, "m": 1e-3, "k": 1e3, "K": 1e3, "meg": 1e6,
      "Meg": 1e6, "MEG": 1e6, "M": 1e6, "G": 1e9, "g": 1e9}
UNITS = r"(?:V|A|Hz|s|F|H|Ω|ohm|ohms|W|%)?"
NUM = re.compile(r"^([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)\s*(meg|Meg|MEG|[fpnuµμmkKMGg])?\s*" + UNITS + "$")
RKM = re.compile(r"^([-+]?\d+)(meg|Meg|MEG|[fpnuµμmkKMGg])(\d+)\s*" + UNITS + "$")
NUM_IN_TEXT = re.compile(r"(?<![\w.])[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?(?:meg|Meg|MEG|[fpnuµμmkKMGg])?\d*"
                         r"(?:V|A|Hz|s)?(?![\w.])")
GROUNDISH = re.compile(r"^(0|GND|[ADPS]GND|GND[ADP]|GNDPWR|GND_\w+|\w+_GND|0V|VSS)$", re.I)
LOG_BAD = re.compile(r"singular matrix|timestep too small|simulation\(s\) aborted|gmin stepping failed|"
                     r"source stepping failed|no such vector|fatal|\berror\b|doAnalyses", re.I)
MEASURES = {"v": 1, "i": 1, "gain": 2, "f3db": 1, "f3db_low": 1, "peak": 1, "settle": 1}


class BlockError(Exception):
    """The block cannot be simulated as written: the brief or the scope must change."""


def num(s):
    """'4.7k', '4k7', '1meg', '1M' (mega), '1m' (milli), '100n', '1e-3', '5.' -> float. A unit after the number is
    allowed and ignored (10uF, 3.3V, 1kHz); a bare F or H is the unit (femto is a lower-case f)."""
    s = s.strip()
    m = RKM.match(s)
    if m:
        return float(f"{m.group(1)}.{m.group(3)}") * SI[m.group(2)]
    m = NUM.match(s)
    if not m:
        raise ValueError(f"not a number: {s!r}")
    return float(m.group(1)) * SI.get(m.group(2) or "", 1.0)


def find_ngspice():
    env = os.environ.get("NGSPICE")
    if env is not None:
        return env if os.path.exists(env) else None
    return next((p for p in (shutil.which("ngspice"), "/opt/homebrew/bin/ngspice", "/usr/local/bin/ngspice",
                             "/usr/bin/ngspice") if p and os.path.exists(p)), None)


def ngspice_version(ng):
    try:
        m = re.search(r"ngspice-\S+", subprocess.run([ng, "-v"], capture_output=True, text=True, timeout=10).stdout)
        return m.group(0) if m else "ngspice"
    except Exception:
        return "ngspice"


# ------------------------------------------------------------------ the brief block
def block_text(text):
    m = re.search(r"^##\s+Simulation\s*$(.*?)(?=^##\s|\Z)", text, re.S | re.M)
    return m.group(1) if m else None


def split_args(s):
    """'Net-(D1-A), GND' -> ['Net-(D1-A)', 'GND']: commas inside parentheses stay."""
    out, depth, cur = [], 0, ""
    for ch in s:
        if ch == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
            continue
        depth += (ch == "(") - (ch == ")")
        cur += ch
    return out + ([cur.strip()] if cur.strip() else [])


def parse_block(text):
    body = block_text(text)
    if body is None:
        return None
    blk = {"scope": [], "analysis": "op", "sources": [], "asserts": [], "ground": None, "ignore": [], "text": body}
    for raw in body.split("\n"):
        line = raw.strip().strip("`").lstrip("-* ").strip()
        if ":" not in line:
            continue
        key, val = [x.strip() for x in line.split(":", 1)]
        key = key.lower()
        if key == "scope":
            blk["scope"] = split_args(re.sub(r"^nets?\s+", "", val))
        elif key == "analysis":
            blk["analysis"] = val
        elif key == "ground":
            blk["ground"] = val
        elif key == "ignore":
            blk["ignore"] = [x for x in re.split(r"[,\s]+", val) if x]
        elif key == "source":
            n, _, spec = val.partition("=")
            blk["sources"].append((n.strip(), spec.strip()))
        elif key == "assert":
            try:
                blk["asserts"].append(parse_assert(val))
            except ValueError as e:
                blk["asserts"].append({"text": val, "error": str(e)})
        elif key == "expect" and blk["asserts"]:
            a = blk["asserts"][-1]               # only the assertion right above, parsed or not
            if not a.get("error"):
                try:
                    a["expect"], a["expect_tol"] = parse_expect(val)
                except ValueError as e:
                    a["error"] = f"expect: {e}"
    return blk


def parse_expect(val):
    m = re.match(r"^\s*(\S+?)(?:\s*(?:±|\+-|\+/-|\btol\b)\s*([^\s%]+)(%?))?(?:\s+\S+)?\s*$", val)
    if not m:
        raise ValueError(f"cannot read {val!r}")
    v = num(m.group(1))
    if m.group(2):
        t = num(m.group(2))
        return v, (("rel", t / 100) if m.group(3) == "%" else ("abs", t))
    if v == 0:
        raise ValueError("an expected value of 0 needs an absolute tolerance, e.g. `expect: 0 ± 0.001`")
    return v, ("rel", CF_TOL)


def parse_assert(s):
    s = s.strip()
    m = re.match(r"^(\w+)\(", s)
    if not m:
        raise ValueError("expected `<measure>(...) between A and B [at X]` or `<measure>(...) < A [at X]`")
    fn, depth, end = m.group(1).lower(), 0, None
    for i in range(m.end() - 1, len(s)):
        depth += (s[i] == "(") - (s[i] == ")")
        if depth == 0:
            end = i
            break
    if end is None:
        raise ValueError("unbalanced parentheses")
    if fn not in MEASURES:
        raise ValueError(f"unknown measure {fn} ({', '.join(MEASURES)})")
    args, rest = split_args(s[m.end():end]), s[end + 1:].strip()
    if len(args) < MEASURES[fn]:
        raise ValueError(f"{fn}() needs {MEASURES[fn]} argument(s)")
    at = None
    mt = re.search(r"(?:^|\s)at\s+(\S+)\s*$", rest)
    if mt:
        at, rest = mt.group(1), rest[:mt.start()].strip()
        if at != "op":
            num(at)
    a = {"text": s, "fn": fn, "args": args, "at": at, "expect": None, "expect_tol": None}
    mb = re.fullmatch(r"between\s+(\S+)\s+and\s+(\S+)", rest, re.I)
    mc = re.fullmatch(r"(<=|>=|<|>)\s*(\S+)", rest)
    if mb:
        a.update(lo=num(mb.group(1)), hi=num(mb.group(2)), lo_strict=False, hi_strict=False)
        if a["lo"] > a["hi"]:
            raise ValueError(f"between {mb.group(1)} and {mb.group(2)}: the lower bound is above the upper one")
    elif mc:
        v = num(mc.group(2))
        if mc.group(1).startswith(">"):
            a.update(lo=v, hi=math.inf, lo_strict=mc.group(1) == ">", hi_strict=False)
        else:
            a.update(lo=-math.inf, hi=v, lo_strict=False, hi_strict=mc.group(1) == "<")
    else:
        raise ValueError(f"cannot read the bound {rest!r}: use `between A and B`, `< A`, `<= A`, `> A` or `>= A`")
    return a


def in_bound(a, v):
    return (v > a["lo"] if a["lo_strict"] else v >= a["lo"]) and (v < a["hi"] if a["hi_strict"] else v <= a["hi"])


# ------------------------------------------------------------------ circuit
def spice_export(sch, workdir):
    out = os.path.join(workdir, "design.cir")
    subprocess.run([kicad.find_cli(), "sch", "export", "netlist", "--format", "spice", "-o", out, sch], capture_output=True)
    return open(out).read() if os.path.exists(out) else None


def kicad_token(name):
    """How KiCad's SPICE export spells a net name: spaces, parentheses and other separators become '_'."""
    return re.sub(r"[\s()\[\]{},;=]", "_", name)


class Nets:
    """Brief / design net names -> the export's node tokens -> unique deck node names (ngspice is case-insensitive)."""

    def __init__(self, tokens):
        self.tokens, self.deck, used = set(tokens), {}, set()
        for t in sorted(self.tokens):
            base = "n_" + re.sub(r"[^A-Za-z0-9_]", "_", t.lstrip("/"))
            name, k = base, 2
            while name.lower() in used:
                name, k = f"{base}_{k}", k + 1
            used.add(name.lower())
            self.deck[t] = name

    def token(self, name):
        n = (name or "").replace("{slash}", "/").strip()
        for cand in (n, "/" + n, kicad_token(n), "/" + kicad_token(n)):
            if cand in self.tokens:
                return cand
        low = [t for t in self.tokens if t.lstrip("/").lower() == kicad_token(n).lstrip("/").lower()]
        return low[0] if len(low) == 1 else None


def elements(cir, comps):
    """[(ref_token, design_ref, node_tokens, rest_tokens, unmodelled)] from KiCad's SPICE export."""
    out = []
    for line in cir.split("\n"):
        s = line.strip()
        if not s or s.startswith(("*", ".")):
            continue
        tok = s.split()
        rt = tok[0]
        ref = rt if rt in comps else (rt[1:] if rt[1:] in comps else rt)
        c = comps.get(ref, {})
        if len(tok) > 1 and tok[1].startswith("__"):                  # "D1 __D1": KiCad has no model for it
            out.append((rt, ref, sorted({p["token"] for p in c.get("pins", {}).values()}), [], True))
            continue
        simpins = c.get("fields", {}).get("Sim.Pins", "")
        n = len(re.findall(r"\S+=\S+", simpins)) or len(c.get("pins", {})) or 2
        if rt[0].upper() in "RCLVI" and not simpins:
            n = 2
        out.append((rt, ref, tok[1:1 + n], tok[1 + n:], False))
    return out


def sim_fields(c, nets):
    """From MC_Parts Sim_Model ('<letter> <model or subckt> | <card> ...') + Sim_Pins ('2 1')."""
    f = c.get("fields", {})
    model, pins = (f.get("Sim_Model") or "").strip(), (f.get("Sim_Pins") or "").split()
    if not model or not pins:
        return None
    elem, *cards = [x.strip() for x in model.split("|")]
    toks = [c.get("pins", {}).get(p, {}).get("token") for p in pins]
    if None in toks:
        return None
    return toks, (elem.split() or ["X"])[0], " ".join(elem.split()[1:]), cards


def build(blk, cir, comps):
    """Deck lines and maps. Raises BlockError when the block cannot run as the drawn circuit."""
    els = elements(cir, comps)
    tokens = {t for _, _, nodes, _, _ in els for t in nodes}
    tokens |= {p["token"] for c in comps.values() for p in c.get("pins", {}).values()}
    nets = Nets(tokens)

    def tok(n, what):
        t = nets.token(n)
        if t is None:
            raise BlockError(f"{what} {n!r} is not a net on the drawn schematic")
        return t

    if not blk["sources"]:
        raise BlockError("no source: the circuit would be unpowered (add `source: NET = <value>`)")
    src = {}
    for n, spec in blk["sources"]:
        src[tok(n, "source net")] = (n, spec)
    scope = {tok(n, "scope net") for n in blk["scope"]} | set(src)
    if blk["ground"]:
        gnd = tok(blk["ground"], "ground")
    else:
        cands = sorted(t for t in scope if GROUNDISH.match(t.lstrip("/")))
        if len(cands) > 1:
            raise BlockError(f"several ground nets in scope ({', '.join(c.lstrip('/') for c in cands)}): "
                             "name the reference with `ground: NET`")
        if not cands:
            cands = sorted(t for t in nets.tokens if GROUNDISH.match(t.lstrip("/")))
            if len(cands) != 1:
                raise BlockError("no ground reference in scope: add the return net to the scope or name it with "
                                 "`ground: NET`" + (f" (candidates {', '.join(g.lstrip('/') for g in cands)})" if cands else ""))
        gnd = cands[0]
    if gnd in src:
        raise BlockError(f"a source drives the ground reference {gnd.lstrip('/')}")
    scope.add(gnd)
    node = lambda t: "0" if t == gnd else nets.deck[t]
    deck, missing, crossing, ignored = [], [], [], []
    for rt, ref, nodes, rest, unm in els:
        if not [n for n in nodes if n in scope and n != gnd]:
            continue                                     # wholly outside the scope, or only on ground
        if ref in blk["ignore"] or rt in blk["ignore"]:
            ignored.append(ref)
            continue
        outside = sorted({n.lstrip("/") for n in nodes if n not in scope})
        if outside:
            crossing.append(f"{ref} (reaches {', '.join(outside)})")
            continue
        if unm:
            sf = sim_fields(comps.get(ref, {}), nets)
            if not sf:
                missing.append(ref)
                continue
            toks, letter, model, cards = sf
            name = ref if ref.upper().startswith(letter.upper()) else letter + ref
            deck += [" ".join([name, *[node(t) for t in toks], model])] + cards
            continue
        deck.append(" ".join([rt, *[node(n) for n in nodes], *rest]))
    if crossing:
        raise BlockError("parts touch the scope and nets outside it, so the circuit would not be the drawn one: "
                         + "; ".join(crossing) + ". Add those nets to the scope (with a source if they are inputs), "
                         "or list the part under `ignore:` if it does not matter here")
    used = {x for l in deck for x in l.split()[1:]}
    for t in scope - {gnd}:
        if t not in src and node(t) not in used:
            raise BlockError(f"scope net {t.lstrip('/')} has no part in the simulated circuit")
    head = [l.strip() for l in cir.split("\n") if l.strip().startswith(".")
            and not l.strip().lower().startswith((".title", ".end"))]
    return {"deck": head + deck, "nets": nets, "gnd": gnd, "node": node, "src": src, "missing": sorted(missing),
            "ignored": ignored}


def source_line(name, node, spec):
    """V_<name> <node> 0 dc <v> [ac <mag>] [pulse(...) / sin(...) / pwl(...)]: every number converted to a float, so
    our suffixes (M = mega) never reach ngspice (where M means milli)."""
    def conv(m):
        try:
            return repr(num(m.group(0)))
        except ValueError:
            return m.group(0)
    words = NUM_IN_TEXT.sub(conv, spec.strip())
    first = re.match(r"^\s*([-+0-9.eE]+)(?=\s|$)", words)
    dcv, rest = (first.group(1), words[first.end():].strip()) if first else ("0", words)
    return f"V_{re.sub(r'[^A-Za-z0-9_]', '_', name)} {node} 0 dc {dcv} {rest}".strip()


def analysis(a, half=False):
    w = a.split()
    k = w[0].lower() if w else ""
    vals = lambda xs: " ".join(repr(num(x)) for x in xs)
    if k == "op":
        return "op", "op"
    if k == "ac":
        if len(w) == 3:
            return "ac", "ac dec 50 " + vals(w[1:])
        if len(w) == 5 and w[1].lower() in ("dec", "oct", "lin"):
            return "ac", f"ac {w[1].lower()} {int(num(w[2]))} " + vals(w[3:])
        raise ValueError(f"analysis {a!r}: use `ac <fstart> <fstop>` or `ac dec <n> <fstart> <fstop>`")
    if k == "tran":
        if len(w) != 3:
            raise ValueError(f"analysis {a!r}: use `tran <step> <stop>`")
        step, stop = num(w[1]) / (2 if half else 1), num(w[2])
        return "tran", f"tran {step!r} {stop!r} 0 {step!r}"
    if k == "dc":
        if len(w) != 5:
            raise ValueError(f"analysis {a!r}: use `dc <source net> <start> <stop> <step>`")
        return "dc", "dc V_" + re.sub(r"[^A-Za-z0-9_]", "_", w[1]) + " " + vals(w[2:])
    raise ValueError(f"analysis {a!r}: use op, dc, ac or tran")


# ------------------------------------------------------------------ running and measuring
def run_deck(ng, lines, srcs, kind, an, probes, need_op, wd, tag):
    """One ngspice run. probes: [(key, expr)] with expr a real-valued ngspice expression of the probe.
    Returns (series {key: [..]}, xs, op {key: value}, deck). Raises RuntimeError on any bad run."""
    data = os.path.join(wd, f"{tag}.txt")
    ctl = [".control", "set wr_singlescale", "set wr_vecnames"]
    if need_op or kind == "op":
        ctl += ["op"] + [f"let o{k} = {e['op']}" for k, (_, e) in enumerate(probes)]
        ctl += ["print " + " ".join(f"o{k}" for k in range(len(probes)))]
    if kind != "op":
        ctl += [an] + [f"let p{k} = {e[kind if kind == 'ac' else 'real']}" for k, (_, e) in enumerate(probes)]
        ctl += [f"wrdata {data} " + " ".join(f"p{k}" for k in range(len(probes)))]
    deck = "\n".join(["* pe-forge spice assertion deck"] + lines + srcs + ctl + [".endc", ".end"]) + "\n"
    path = os.path.join(wd, f"{tag}.cir")
    open(path, "w").write(deck)
    try:
        r = subprocess.run([ng, "-b", path], capture_output=True, text=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"ngspice did not finish within {TIMEOUT} s")
    log = r.stdout + r.stderr
    bad = [l.strip() for l in log.split("\n") if LOG_BAD.search(l)]
    if r.returncode or bad:
        raise RuntimeError("ngspice: " + (bad[0][:160] if bad else f"exit code {r.returncode}"))
    opv = {}
    if need_op or kind == "op":
        for k, (key, _) in enumerate(probes):
            m = re.search(rf"^\s*o{k}\s*=\s*([-+0-9.eE]+)\s*$", log, re.M)
            if not m:
                raise RuntimeError(f"ngspice printed no operating-point value for {key[1]}")
            opv[key] = float(m.group(1))
    series, xs = {}, []
    if kind != "op":
        if not os.path.exists(data):
            raise RuntimeError("ngspice wrote no data")
        rows = [l.split() for l in open(data) if l.strip()]
        if len(rows) < 3:
            raise RuntimeError("ngspice wrote fewer than 2 data points")
        head, rows = rows[0], [[float(x) for x in row] for row in rows[1:]]
        if len(head) != len(probes) + 1 or any(len(row) != len(head) for row in rows):
            raise RuntimeError(f"unexpected wrdata layout ({len(head)} columns for {len(probes)} probe(s))")
        xs = [row[0] for row in rows]
        for k, (key, _) in enumerate(probes):
            series[key] = [row[k + 1] for row in rows]
    return series, xs, opv, deck


def _at(xs, ys, x, log_x=False):
    if not min(xs[0], xs[-1]) <= x <= max(xs[0], xs[-1]):
        raise ValueError(f"point {x:g} is outside the simulated range {xs[0]:g} to {xs[-1]:g}")
    for i in range(len(xs) - 1):
        x0, x1 = xs[i], xs[i + 1]
        if min(x0, x1) <= x <= max(x0, x1):
            if x1 == x0:
                return ys[i]
            f = (math.log10(x / x0) / math.log10(x1 / x0)) if log_x else (x - x0) / (x1 - x0)
            return ys[i] + (ys[i + 1] - ys[i]) * f
    return ys[-1]


def measure(a, kind, xs, series, opv):
    fn, args, at = a["fn"], a["args"], a["at"]

    def pick(typ, net):
        key = (typ, net)
        if at == "op" or kind == "op":
            return opv[key]
        ys = series[key]
        if at is None:                       # ac: the lowest frequency; dc / tran: the last point
            return ys[0] if kind == "ac" else ys[-1]
        return _at(xs, ys, num(at), log_x=kind == "ac")

    if fn in ("v", "i"):
        return pick(fn, args[0])
    if fn == "gain":
        o, i = pick("v", args[0]), pick("v", args[1])
        if i == 0:
            raise ValueError(f"gain: the input {args[1]} is 0 at that point")
        return o / i
    if kind == "op" or at == "op":
        raise ValueError(f"{fn} needs a sweep or a transient, not an operating point")
    ys = series[("v", args[0])]
    if fn == "peak":
        return max(ys)
    if fn in ("f3db", "f3db_low"):
        if kind != "ac":
            raise ValueError(f"{fn} needs an ac analysis")
        k = max(range(len(ys)), key=lambda j: ys[j])
        target = ys[k] / math.sqrt(2)
        step = 1 if fn == "f3db" else -1
        j = k
        while 0 <= j + step < len(ys):
            if ys[j] >= target > ys[j + step] > 0:
                db0, db1, dbt = (20 * math.log10(y) for y in (ys[j], ys[j + step], target))
                lf0, lf1 = math.log10(xs[j]), math.log10(xs[j + step])
                return 10 ** (lf0 + (lf1 - lf0) * (db0 - dbt) / (db0 - db1))
            j += step
        raise ValueError(f"no {'upper' if step > 0 else 'lower'} -3 dB point inside the swept range")
    if fn == "settle":
        if kind != "tran":
            raise ValueError("settle needs a tran analysis")
        tol = num(args[1].rstrip("%")) / 100 if len(args) > 1 else 0.02
        fin = ys[-1]
        band = tol * abs(fin) if fin else tol
        if any(abs(y - fin) > band for y in ys[int(len(ys) * 0.9):]):
            raise ValueError("has not settled by the end of the run: extend the tran stop time")
        for j in range(len(ys) - 1, -1, -1):
            if abs(ys[j] - fin) > band:
                return xs[min(j + 1, len(xs) - 1)]
        return xs[0]
    raise ValueError(fn)


def fmt(v):
    return f"{v:.4g}"


def bound(a):
    if a["lo"] == -math.inf:
        return f"{'<' if a['hi_strict'] else '<='} {fmt(a['hi'])}"
    if a["hi"] == math.inf:
        return f"{'>' if a['lo_strict'] else '>='} {fmt(a['lo'])}"
    return f"{fmt(a['lo'])} to {fmt(a['hi'])}"


def grade(a, val, val_half):
    """(status, detail): pass (ACCEPTED), provisional or fail (REJECTED)."""
    detail, notes = f"measured {fmt(val)}; bound {bound(a)}", []
    if a["expect"] is None:
        notes.append("no closed-form `expect:` (Rule 1: no second source)")
    else:
        mode, tol = a["expect_tol"]
        err = abs(val - a["expect"])
        lim = tol * abs(a["expect"]) if mode == "rel" else tol
        detail += f"; closed form {fmt(a['expect'])}" + (f" ({(val - a['expect']) / a['expect'] * 100:+.1f} %)"
                                                       if a["expect"] else f" (off by {fmt(err)})")
        if err > lim:
            notes.append("ngspice and the closed form disagree beyond " + (f"{tol * 100:g} %" if mode == "rel" else fmt(tol)))
    if val_half is not None:
        d = abs(val_half - val) / abs(val) if val else abs(val_half)
        detail += f"; half time step {fmt(val_half)} ({d * 100:.2f} %)"
        if d > DT_TOL:
            notes.append(f"the result moves {d * 100:.1f} % at half the time step")
    st = ("provisional" if notes else "pass") if in_bound(a, val) else "fail"
    return st, detail + (": " + "; ".join(notes) if notes else "")


def run_block(blk, cir, comps, ng, wd):
    """([result per assertion, in the brief's order], deck, info)."""
    good = [a for a in blk["asserts"] if not a.get("error")]
    out = lambda per: [dict(a, status="fail", detail=f"does not parse: {a['error']}") if a.get("error") else per(a)
                       for a in blk["asserts"]]
    if not good:
        return out(None), "", {}
    try:
        C = build(blk, cir, comps)
        kind, an = analysis(blk["analysis"])
        if C["missing"]:
            msg = f"no SPICE model for {', '.join(C['missing'])} (set Sim_Model / Sim_Pins, or list it under `ignore:`)"
            return out(lambda a: dict(a, status="skip", detail=msg)), "", C
        node, src = C["node"], C["src"]
        srcs = [source_line(n, node(t), spec) for t, (n, spec) in src.items()]
        present = {x for l in C["deck"] + srcs for x in l.split()[1:]}
        probes = {}
        for a in good:
            for typ, net in ([("i", a["args"][0])] if a["fn"] == "i" else
                             [("v", x) for x in a["args"][:2 if a["fn"] == "gain" else 1]]):
                if (typ, net) in probes:
                    continue
                t = C["nets"].token(net)
                if typ == "i":
                    if t not in src:
                        raise BlockError(f"I({net}): currents are measured in sources, and {net} has none")
                    v = "v_" + re.sub(r"[^A-Za-z0-9_]", "_", src[t][0]).lower()
                    # delivered current: out of the source's + terminal into the circuit (SPICE reports the opposite)
                    probes[(typ, net)] = {"op": f"-i({v})", "real": f"-i({v})", "ac": f"mag(i({v}))"}
                    continue
                if t is None:
                    raise BlockError(f"V({net}): {net} is not a net on the drawn schematic")
                if t == C["gnd"]:
                    raise BlockError(f"V({net}) probes the ground reference itself")
                if node(t) not in present:
                    raise BlockError(f"V({net}): {net} is not in the simulated circuit (add it to the scope)")
                probes[(typ, net)] = {"op": f"v({node(t)})", "real": f"v({node(t)})", "ac": f"mag(v({node(t)}))"}
        plist = list(probes.items())
        need_op = any(a["at"] == "op" for a in good)
        series, xs, opv, deck = run_deck(ng, C["deck"], srcs, kind, an, plist, need_op, wd, "run")
        half = None
        if kind == "tran":
            hs, hx, _, _ = run_deck(ng, C["deck"], srcs, kind, analysis(blk["analysis"], half=True)[1], plist,
                                    False, wd, "half")
            half = (hx, hs)
    except (BlockError, ValueError) as e:
        return out(lambda a: dict(a, status="fail", detail=f"block cannot run: {e}")), "", {}
    except RuntimeError as e:
        return out(lambda a: dict(a, status="fail", detail=str(e))), "", {}

    def one(a):
        try:
            val = measure(a, kind, xs, series, opv)
            vh = measure(a, kind, half[0], half[1], opv) if half and a["at"] != "op" else None
        except (ValueError, KeyError) as e:
            return dict(a, status="fail", detail=str(e))
        if math.isnan(val) or math.isinf(val):
            return dict(a, status="fail", detail=f"measured {val}: not a finite number")
        st, detail = grade(a, val, vh)
        return dict(a, status=st, value=val, detail=detail)
    return out(one), deck, C


# ------------------------------------------------------------------ records and the run
def inputs_sha(block_body, sheet):
    h = hashlib.sha256(TOOL_VERSION.encode())
    h.update(block_body.encode())
    if sheet and os.path.exists(sheet):
        h.update(open(sheet, "rb").read())
    return h.hexdigest()[:16]


GRADE = {"pass": "ACCEPTED", "provisional": "PROVISIONAL", "fail": "REJECTED", "skip": "NOT RUN"}


def overall(results):
    st = {r["status"] for r in results}
    return "REJECTED" if "fail" in st else "NOT RUN" if ("skip" in st or not st) else \
        "PROVISIONAL" if "provisional" in st else "ACCEPTED"


def briefs(prj, module=None):
    from .drift import frontmatter
    try:
        from . import state
        mods = state.load(prj)["modules"]
    except Exception:
        mods = []
    cands = [(m, os.path.join(prj.root, m["dir"], "Sheet_Brief.md")) for m in mods if not module or m["id"] == module]
    if not module:
        cands += [(None, b) for b in sorted(glob.glob(prj.p("briefs", "*.md")))]
    res = []
    for m, b in cands:
        if os.path.exists(b):
            text = open(b, errors="ignore").read()
            blk = parse_block(text)
            if blk:
                sheet = frontmatter(text).get("kicad_sheet")
                res.append((m, b, blk, os.path.join(prj.root, sheet) if sheet and not sheet.startswith("08_PCB/<") else None))
    return res


def record_path(prj, m, brief):
    if m:
        return os.path.join(prj.root, m["dir"], "Simulations", os.path.splitext(os.path.basename(brief))[0] + "_spice.md")
    return prj.p("sim_results", "brief_" + os.path.splitext(os.path.basename(brief))[0] + "_spice.md")


def run(prj, module=None, strict=False):
    from .check import roots
    ng = find_ngspice()
    found = briefs(prj, module)
    if not found:
        return {"status": "skip", "reason": "no `## Simulation` block in any sheet brief", "results": []}
    if not ng:
        return {"status": "skip", "reason": "ngspice not found (brew install ngspice / apt install ngspice, or set NGSPICE)",
                "missing_tool": True, "results": []}
    tops = roots(prj, "kicad_sch")
    results, records = [], []
    for m, brief, blk, sheet in found:
        sch, deck, info = sheet or (tops[0] if len(tops) == 1 else None), "", {}
        with tempfile.TemporaryDirectory(prefix="forge-spice-") as wd:
            if not sch:
                rs = [dict(a, status="fail", detail=f"name the sheet in the brief's frontmatter (`kicad_sheet:`): "
                                                    f"{len(tops)} top sheets in 08_PCB/") for a in blk["asserts"]]
            else:
                cir = spice_export(sch, wd) if os.path.exists(sch) else None
                d = kicad.design(sch) if cir else None
                if cir is None or d is None:
                    rs = [dict(a, status="skip", detail="KiCad SPICE export failed (sheet missing, or kicad-cli)")
                          for a in blk["asserts"]]
                else:
                    rs, deck, info = run_block(blk, cir, d[0], ng, wd)
        for r in rs:
            r["brief"] = prj.rel(brief)
        results += rs
        records.append(write_svr(prj, m, brief, blk, rs, deck, sch, ng, info))
    st = {r["status"] for r in results}
    status = "fail" if "fail" in st or (strict and st & {"skip", "provisional"}) else \
        "warn" if st & {"skip", "provisional"} else "pass"
    return {"status": status, "reason": f"{len(results)} assertion(s)", "results": results, "records": records}


def cell(s):
    return str(s).replace("|", "\\|").replace("\n", " ")


def write_svr(prj, m, brief, blk, res, deck, sch, ng, info):
    p = record_path(prj, m, brief)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    result = overall(res)
    kind = (blk["analysis"].split() or ["op"])[0].lower()
    lines = ["---", "type: simulation", "tool: ngspice (forge spice check)", f"result: {result}",
             f"brief: {prj.rel(brief)}", f"sheet: {prj.rel(sch) if sch else '(none)'}",
             f"inputs: {inputs_sha(blk['text'], sch)}", f"date: {dt.date.today().isoformat()}", "---",
             f"# {os.path.basename(brief)}: ngspice assertions ({result})", "",
             f"Analysis `{cell(blk['analysis'])}` · scope {cell(', '.join(blk['scope']) or '(sources)')} · ground "
             f"{cell(blk['ground'] or '(auto)')}" + (f" · ignored {', '.join(info.get('ignored', []))}" if info.get("ignored") else ""),
             "", "## Results", "", "| Assertion | Result | Detail |", "|---|---|---|"]
    lines += [f"| `{cell(r['text'])}` | {GRADE[r['status']]} | {cell(r['detail'])} |" for r in res]
    lines += ["", "## Simulation Verification Record (SVR)", "", "| Item | Record |", "|---|---|",
              "| Model provenance | KiCad's SPICE export of the drawn sheet, cut to the scope (a part reaching outside it stops "
              "the block); KiCad's ideal passives; other parts from Sim_Model / Sim_Pins or KiCad Sim.* fields |",
              "| Anchor | the `expect:` closed form of each assertion (agreement in Detail); without one the result is PROVISIONAL |",
              f"| Convergence | {ngspice_version(ng)} batch run; exit code and log scanned for errors, singular matrix, timestep "
              "too small and aborted runs; data layout checked |",
              "| Time step | " + ("re-run at half the time step; a move over 1 % makes the result PROVISIONAL |" if kind == "tran"
                                 else "not applicable (op / dc / ac) |"),
              "| Energy / KCL closure | not checked by this tool; check by hand where it matters |",
              "| Corners | only what this block states; write one block per corner |",
              "| Reproduce | `forge spice check" + (f" --module {m['id']}" if m else "") + "` |", "",
              "## Deck", "", "```", re.sub(r"wrdata \S+", "wrdata out.txt", deck.strip()) or "(not run)", "```", ""]
    open(p, "w").write("\n".join(lines))
    return prj.rel(p)


def record_problem(prj, path):
    """Why gate G5 must refuse a forge spice record (REJECTED, NOT RUN, stale), or None (and None for other files)."""
    text = open(path, errors="ignore").read()
    if "tool: ngspice (forge spice check)" not in text[:400]:
        return None
    from .drift import frontmatter
    fm = frontmatter(text)
    if fm.get("result") in ("REJECTED", "NOT RUN"):
        return f"result {fm['result']}"
    brief = os.path.join(prj.root, fm.get("brief", ""))
    if not fm.get("brief") or not os.path.exists(brief):
        return "its brief is gone"
    body = block_text(open(brief, errors="ignore").read())
    sheet = fm.get("sheet")
    sheet = os.path.join(prj.root, sheet) if sheet and sheet != "(none)" else None
    if body is None or inputs_sha(body, sheet) != fm.get("inputs"):
        return "stale: the Simulation block, the sheet or the tool changed since the run (re-run forge spice check)"
    return None


def as_check(prj, opts):
    from .check import finding, result
    r = run(prj, opts.get("module"), opts.get("strict"))
    if r["status"] == "skip":
        return result("skip", reason=r["reason"], missing_tool=r.get("missing_tool", False))
    F = [finding(a["text"], f"{GRADE[a['status']]}: {a['detail']}", a["brief"]) for a in r["results"] if a["status"] != "pass"]
    return result(r["status"], F, reason=r["reason"])


def main(prj, argv):
    if not argv or argv[0] != "check":
        print(__doc__)
        return 1
    r = run(prj, argv[argv.index("--module") + 1] if "--module" in argv else None, "--strict" in argv)
    if "--json" in argv:
        print(json.dumps(r, indent=1, default=str))
    else:
        if r["status"] == "skip":
            print(f"spice: skipped ({r['reason']})")
        for a in r["results"]:
            print(f"{GRADE[a['status']]:<11} {a['brief']}: {a['text']}  ({a['detail']})")
        for rec in r.get("records", []):
            print(f"record: {rec}")
    return 1 if r["status"] == "fail" or ("--strict" in argv and r["status"] == "skip") else 0
