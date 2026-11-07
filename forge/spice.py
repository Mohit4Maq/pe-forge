"""ngspice assertion gate for the small analog circuits pwrlab does not cover (sense amplifiers, dividers into ADCs,
gate-drive RC, UVLO / OVP thresholds, feedback dividers, NTC bias). Grammar and semantics:
skills/pe-simulate/references/assertions.md.

  forge spice check [--module M-xx] [--strict] [--json]

What makes a result ACCEPTED (anything less is PROVISIONAL, REJECTED or NOT RUN, and the record says why):
  - the circuit is exactly the drawn one inside the scope: KiCad's own SPICE export, every part whose nets all lie in
    the scope. A part that touches the scope and a net outside it, or a drawn part on a scope net that the export
    left out (excluded from simulation), stops the block unless the brief lists it under `ignore:`;
  - every source and probe names a net that exists and is in the circuit; one ground reference; one source per net;
    every net its own node; no probe reads a net a source drives (that would read the source, not the circuit);
  - ngspice ran clean: exit code; the log scanned for errors, singular matrix, timestep too small, aborted runs and
    model-parameter problems; the data layout and, for tran, the stop time checked;
  - the value is inside the bound AND agrees with the `expect:` closed form (2 % or the stated tolerance); without
    `expect:` it is PROVISIONAL (Rule 1: no second source);
  - tran only: a re-run at half the time step moves the value by under 1 %.
Records: <module>/Simulations/<brief>_spice.md with the SVR. Its frontmatter carries `result:`, `inputs:` (a hash of
the Simulation block, every sheet of the hierarchy, every included model file and this tool) and `files:`; gate G5
refuses a record that is REJECTED, NOT RUN or stale.
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

TOOL_VERSION = "spice-3"            # in the record's input hash: a tool change makes old records stale
CF_TOL = 0.02                       # ngspice vs closed form
DT_TOL = 0.01                       # tran: half time step re-run
TIMEOUT = 120
SI = {"f": 1e-15, "p": 1e-12, "n": 1e-9, "u": 1e-6, "µ": 1e-6, "μ": 1e-6, "m": 1e-3, "k": 1e3, "K": 1e3, "meg": 1e6,
      "Meg": 1e6, "MEG": 1e6, "M": 1e6, "G": 1e9, "g": 1e9}
PFX = r"(meg|Meg|MEG|[fpnuµμmkKMGg])"
UNITS = r"(?i:v|a|hz|s|f|h|Ω|ohm|ohms|w|%)?"
NUM = re.compile(r"^([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)\s*" + PFX + r"?\s*" + UNITS + "$")
RKM = re.compile(r"^([-+]?\d+)" + PFX + r"(\d+)\s*" + UNITS + "$")
NUM_IN_TEXT = re.compile(r"(?<![\w.])[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?" + PFX + r"?\d*" + UNITS + r"(?![\w.])")
GROUNDISH = re.compile(r"^(0|GND|[ADPS]GND|GND[ADP]|GNDPWR|GND_\w+|\w+_GND|0V|VSS)$", re.I)
LOG_BAD = re.compile(r"singular matrix|timestep too small|simulation\(s\) aborted|gmin stepping failed|"
                     r"source stepping failed|no such vector|fatal|\berror\b|doAnalyses|unrecognized parameter|"
                     r"unknown parameter|model issue|unknown model|could not find|unable to find|can't find", re.I)
LOG_CHECKS = ("errors, singular matrix, timestep too small, aborted runs, gmin / source stepping failures, unknown or "
              "unrecognized model parameters, missing models or vectors")
MEASURES = {"v": 1, "i": 1, "gain": 2, "f3db": 1, "f3db_low": 1, "peak": 1, "settle": 1}
NODES = {"R": 2, "C": 2, "L": 2, "V": 2, "I": 2, "B": 2, "D": 2, "F": 2, "H": 2, "E": 4, "G": 4, "Q": 3, "J": 3,
         "M": 4, "K": 0}


class BlockError(Exception):
    """The block cannot be simulated as written: the brief or the scope must change."""


def num(s):
    """'4.7k', '4k7', '1meg', '1M' (mega), '1m' (milli), '100n', '1e-3', '5.' -> float. A unit after the number is
    allowed in any case and ignored (10uF, 3.3V, 1kHz, 1Mhz); a bare F or H is the unit (femto: lower-case f)."""
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
    if fn in ("f3db", "f3db_low") and len(args) > 1 and args[1].lower() != "peak":
        raise ValueError(f"{fn}(NET) or {fn}(NET, peak)")
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


# ------------------------------------------------------------------ the export
def spice_export(sch, workdir):
    out = os.path.join(workdir, "design.cir")
    subprocess.run([kicad.find_cli(), "sch", "export", "netlist", "--format", "spice", "-o", out, sch], capture_output=True)
    return open(out).read() if os.path.exists(out) else None


def kicad_token(name):
    """A guess at KiCad's export spelling, used only for nets no exported part shows (see Nets)."""
    return re.sub(r"[\s()\[\]{},;=<>~]", "_", name)


def parse_export(cir):
    """{'lines': element lines, 'subckts': verbatim .subckt blocks, 'directives': kept dot lines,
    'dropped': dot lines and .control blocks not used (forge sets the analysis and the saved vectors)}."""
    out = {"lines": [], "subckts": [], "directives": [], "dropped": []}
    rows, i = [l.rstrip() for l in cir.split("\n")], 0
    while i < len(rows):
        s = rows[i].strip()
        low = s.lower()
        i += 1
        if not s or s.startswith("*") or low.startswith(".title") or low == ".end":
            continue
        if low.startswith(".subckt"):
            blk = [s]
            while i < len(rows) and not rows[i].strip().lower().startswith(".ends"):
                blk.append(rows[i].strip())
                i += 1
            blk.append(rows[i].strip() if i < len(rows) else ".ends")
            i += 1
            out["subckts"].append(blk)
            continue
        if low.startswith(".control"):
            while i < len(rows) and not rows[i].strip().lower().startswith(".endc"):
                i += 1
            i += 1
            out["dropped"].append(".control block")
            continue
        if s.startswith("+") and out["lines"]:
            out["lines"][-1] += " " + s[1:].strip()
            continue
        if s.startswith("."):
            if re.match(r"\.(save|probe|print|plot|op|ac|dc|tran|noise|tf|sens|pz|four|meas|measure)\b", low):
                out["dropped"].append(s)
            else:
                out["directives"].append(s)
            continue
        out["lines"].append(s)
    return out


def elements(lines, comps):
    """[(ref_token, design_ref, node_tokens, rest_tokens, unmodelled)] for the export's element lines."""
    out = []
    for s in lines:
        tok = s.split()
        rt = tok[0]
        ref = rt if rt in comps else (rt[1:] if rt[1:] in comps else rt)
        c = comps.get(ref, {})
        if len(tok) > 1 and tok[1].startswith("__"):                  # "D1 __D1": KiCad has no model for it
            out.append((rt, ref, [], [], True))
            continue
        simpins = c.get("fields", {}).get("Sim.Pins", "")
        n = len(re.findall(r"\S+=\S+", simpins))
        if not n:
            n = NODES.get(rt[0].upper())
            if n is None or (rt[0].upper() in "QJMD" and c.get("pins")):
                n = len(c.get("pins", {})) or n or 2
        out.append((rt, ref, tok[1:1 + n], tok[1 + n:], False))
    return out


class Nets:
    """Net names -> the export's node tokens -> unique deck node names.

    The export's spelling of each net is learned from the parts themselves: for an exported part whose node count
    equals its pin count, the k-th node is the net on its k-th pin. Nets no exported part shows fall back to a guess.
    Deck names are unique case-insensitively (ngspice folds case)."""

    def __init__(self, els, comps):
        self.by_net, tokens = {}, set()
        for rt, ref, nodes, _, unm in els:
            tokens |= set(nodes)
            c = comps.get(ref)
            if unm or not c or c.get("fields", {}).get("Sim.Pins") or len(nodes) != len(c.get("pins", {})):
                continue
            order = sorted(c["pins"], key=lambda p: (len(p), p) if p.isdigit() else (99, p))
            for p, t in zip(order, nodes):
                self.by_net.setdefault(c["pins"][p]["net"], t)
        for c in comps.values():
            for p in c.get("pins", {}).values():
                if p["net"] not in self.by_net:
                    self.by_net[p["net"]] = p.get("token") or kicad_token(p["net"])
                tokens.add(self.by_net[p["net"]])
        self.tokens, self.deck, used = tokens, {}, set()
        for t in sorted(tokens):
            base = "n_" + re.sub(r"[^A-Za-z0-9_]", "_", t.lstrip("/"))
            name, k = base, 2
            while name.lower() in used:
                name, k = f"{base}_{k}", k + 1
            used.add(name.lower())
            self.deck[t] = name

    def token(self, name):
        n = (name or "").replace("{slash}", "/").strip()
        if n in self.by_net:
            return self.by_net[n]
        low = [k for k in self.by_net if k.lower() == n.lower()]
        if len(low) == 1:
            return self.by_net[low[0]]
        for cand in (n, "/" + n, kicad_token(n), "/" + kicad_token(n)):
            if cand in self.tokens:
                return cand
        return None

    def pin_token(self, c, pin):
        p = c.get("pins", {}).get(pin)
        return self.by_net.get(p["net"]) if p else None


def sim_fields(c, nets):
    """From MC_Parts Sim_Model ('<letter> <model or subckt> | <card> ...') + Sim_Pins ('2 1')."""
    f = c.get("fields", {})
    model, pins = (f.get("Sim_Model") or "").strip(), (f.get("Sim_Pins") or "").split()
    if not model or not pins:
        return None
    elem, *cards = [x.strip() for x in model.split("|")]
    toks = [nets.pin_token(c, p) for p in pins]
    if None in toks:
        return None
    return toks, (elem.split() or ["X"])[0], " ".join(elem.split()[1:]), cards


def safe(name):
    return re.sub(r"[^A-Za-z0-9_]", "_", name)


def build(blk, cir, comps):
    """Deck and maps for the block. Raises BlockError when the block cannot run as the drawn circuit."""
    exp = parse_export(cir)
    els = elements(exp["lines"], comps)
    nets = Nets(els, comps)

    def tok(n, what):
        t = nets.token(n)
        if t is None:
            raise BlockError(f"{what} {n!r} is not a net on the drawn schematic")
        return t

    if not blk["sources"]:
        raise BlockError("no source: the circuit would be unpowered (add `source: NET = <value>`)")
    src = {}
    for k, (n, spec) in enumerate(blk["sources"], 1):
        t = tok(n, "source net")
        if t in src:
            raise BlockError(f"two sources on net {t.lstrip('/')} ({src[t][0]} and {n})")
        src[t] = (n, spec, f"V_S{k}_{safe(n)}"[:40])
    scope = {tok(n, "scope net") for n in blk["scope"]} | set(src)
    scope |= {t for t in nets.tokens if t.lstrip("/").lower().startswith("unconnected-")}   # NC pins: nothing else on them
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
    deck, missing, crossing, ignored, drawn_src = [], [], [], [], []
    exported = set()
    for rt, ref, nodes, rest, unm in els:
        exported.add(ref)
        c = comps.get(ref, {})
        if unm:
            sf = sim_fields(c, nets)
            nodes = sf[0] if sf else [nets.pin_token(c, p) for p in c.get("pins", {})]
            nodes = [n for n in nodes if n]
        if not [n for n in nodes if n in scope and n != gnd]:
            continue                                     # wholly outside the scope, or only on ground
        if ref in blk["ignore"] or rt in blk["ignore"]:
            ignored.append(ref)
            continue
        outside = sorted({n.lstrip("/") for n in nodes if n not in scope})
        if outside:
            crossing.append(f"{ref} (reaches {', '.join(outside)})")
            continue
        if not unm and rt[0].upper() in "VI" and set(nodes) & set(src):
            drawn_src.append(f"{ref} on {', '.join(n.lstrip('/') for n in nodes if n in src)}")
            continue
        if unm:
            if not sf:
                missing.append(ref)
                continue
            toks, letter, model, cards = sf
            name = ref if ref.upper().startswith(letter.upper()) else letter + ref
            deck += [" ".join([name, *[node(t) for t in toks], model])] + cards
            continue
        deck.append(" ".join([rt, *[node(n) for n in nodes], *rest]))
    # drawn parts on a scope net that KiCad's export left out (excluded from simulation, or DNP)
    left_out, dnp = [], []
    for ref, c in sorted(comps.items()):
        if ref.startswith("#") or ref in exported or ref in blk["ignore"]:
            continue
        on = [nets.pin_token(c, p) for p in c.get("pins", {})]
        if any(t in scope and t != gnd for t in on if t):
            (dnp if c.get("dnp") else left_out).append(ref)
    if crossing:
        raise BlockError("parts touch the scope and nets outside it, so the circuit would not be the drawn one: "
                         + "; ".join(crossing) + ". Add those nets to the scope (with a source if they are inputs), "
                         "or list the part under `ignore:` if it does not matter here")
    if left_out:
        raise BlockError(f"{', '.join(left_out)} sit on scope nets but are not in KiCad's SPICE export (excluded from "
                         "simulation?), so the circuit would not be the drawn one: include them, or list them under "
                         "`ignore:`")
    if drawn_src:
        raise BlockError(f"the sheet draws a source ({'; '.join(drawn_src)}) on a net the brief also drives: remove the "
                         "brief's source, or list the drawn one under `ignore:`")
    used = {x for l in deck for x in l.split()[1:]}
    for t in scope - {gnd}:
        if t not in src and node(t) not in used and not t.lstrip("/").lower().startswith("unconnected-"):
            raise BlockError(f"scope net {t.lstrip('/')} has no part in the simulated circuit")

    def remap(d):                                      # .ic / .nodeset name nets the way KiCad spells them
        return re.sub(r"\b([vV])\(([^)]+)\)", lambda m: f"{m.group(1)}({node(m.group(2)) if m.group(2) in nets.deck or m.group(2) == gnd else m.group(2)})", d)
    directives = [remap(d) for d in exp["directives"]]
    head = directives + [l for blk_ in exp["subckts"] for l in blk_]
    return {"deck": head + deck, "nets": nets, "gnd": gnd, "node": node, "src": src, "missing": sorted(missing),
            "ignored": ignored, "dnp": dnp, "directives": directives, "dropped": exp["dropped"]}


def source_line(vname, node, spec):
    """<vname> <node> 0 dc <v> [ac <mag>] [pulse(...) ...]: every number converted to a float, so our suffixes
    (M = mega) never reach ngspice (where M means milli); '100 mV' with a space is joined first."""
    def conv(m):
        try:
            return repr(num(m.group(0)))
        except ValueError:
            return m.group(0)
    spec = re.sub(r"(\d)\s+(" + PFX[1:-1] + r"?(?i:v|a|hz|s))(?![\w.])", r"\1\2", spec.strip())
    words = NUM_IN_TEXT.sub(conv, spec)
    first = re.match(r"^\s*([-+0-9.eE]+)(?=\s|$)", words)
    dcv, rest = (first.group(1), words[first.end():].strip()) if first else ("0", words)
    return f"{vname} {node} 0 dc {dcv} {rest}".strip()


def analysis(a, src_names, half=False):
    """(kind, ngspice line, stop time for tran or None)."""
    w = a.split()
    k = w[0].lower() if w else ""
    vals = lambda xs: " ".join(repr(num(x)) for x in xs)
    if k == "op":
        return "op", "op", None
    if k == "ac":
        if len(w) == 3:
            return "ac", "ac dec 50 " + vals(w[1:]), None
        if len(w) == 5 and w[1].lower() in ("dec", "oct", "lin"):
            return "ac", f"ac {w[1].lower()} {int(num(w[2]))} " + vals(w[3:]), None
        raise ValueError(f"analysis {a!r}: use `ac <fstart> <fstop>` or `ac dec <n> <fstart> <fstop>`")
    if k == "tran":
        if len(w) != 3:
            raise ValueError(f"analysis {a!r}: use `tran <step> <stop>`")
        step, stop = num(w[1]) / (2 if half else 1), num(w[2])
        return "tran", f"tran {step!r} {stop!r} 0 {step!r}", stop
    if k == "dc":
        if len(w) != 5:
            raise ValueError(f"analysis {a!r}: use `dc <source net> <start> <stop> <step>`")
        if w[1] not in src_names:
            raise ValueError(f"analysis {a!r}: {w[1]} has no `source:` line to sweep")
        return "dc", f"dc {src_names[w[1]]} " + vals(w[2:]), None
    raise ValueError(f"analysis {a!r}: use op, dc, ac or tran")


# ------------------------------------------------------------------ running and measuring
def run_deck(ng, lines, srcs, kind, an, probes, need_op, wd, tag, stop=None):
    """One ngspice run. probes: [(key, {op, real, ac} expressions)]. Returns (series, xs, op values, deck)."""
    data = os.path.join(wd, f"{tag}.txt")
    ctl = [".control", "set wr_singlescale", "set wr_vecnames"]
    if need_op or kind == "op":
        ctl += ["op"] + [f"let o{k} = {e['op']}" for k, (_, e) in enumerate(probes)]
        ctl += ["print " + " ".join(f"o{k}" for k in range(len(probes)))]
    if kind != "op":
        ctl += [an] + [f"let p{k} = {e['ac' if kind == 'ac' else 'real']}" for k, (_, e) in enumerate(probes)]
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
        if stop is not None and xs[-1] < stop * (1 - 1e-6):
            raise RuntimeError(f"the transient stopped at {xs[-1]:.4g} s, before {stop:.4g} s")
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


def _crossing(xs, ys, j0, step, target):
    j = j0
    while 0 <= j + step < len(ys):
        if ys[j] >= target > ys[j + step] > 0:
            db0, db1, dbt = (20 * math.log10(y) for y in (ys[j], ys[j + step], target))
            lf0, lf1 = math.log10(xs[j]), math.log10(xs[j + step])
            return 10 ** (lf0 + (lf1 - lf0) * (db0 - dbt) / (db0 - db1))
        j += step
    return None


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
        up = fn == "f3db"
        kpk = max(range(len(ys)), key=lambda j: ys[j])
        if len(args) > 1:                    # relative to the passband peak (band-pass)
            ref, j0 = ys[kpk], kpk
        else:                                # classic: relative to the response at the sweep's low (high) end
            j0 = 0 if up else len(ys) - 1
            ref = ys[j0]
            if ref <= 0 or 20 * math.log10(ys[kpk] / ref) > 20:
                raise ValueError(f"the response at the sweep's {'low' if up else 'high'} end is over 20 dB below its "
                                 f"peak (a band-pass?): use {fn}({args[0]}, peak)")
        f = _crossing(xs, ys, j0, 1 if up else -1, ref / math.sqrt(2))
        if f is None:
            raise ValueError(f"no {'upper' if up else 'lower'} -3 dB point inside the swept range")
        return f
    if fn == "settle":
        if kind != "tran":
            raise ValueError("settle needs a tran analysis")
        tol = num(args[1].rstrip("%")) / 100 if len(args) > 1 else 0.02
        fin = ys[-1]
        band = tol * abs(fin) if fin else tol
        # settled means flat at the end: the last 20 % of the run stays within a quarter of the band
        if any(abs(y - fin) > band / 4 for y in ys[int(len(ys) * 0.8):]):
            raise ValueError("has not settled by the end of the run (still moving in the last 20 %): extend the tran "
                             "stop time to at least twice the expected settling time")
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
        names = {n: vn for _, (n, _, vn) in C["src"].items()}
        kind, an, stop = analysis(blk["analysis"], names)
        if C["missing"]:
            msg = f"no SPICE model for {', '.join(C['missing'])} (set Sim_Model / Sim_Pins, or list it under `ignore:`)"
            return out(lambda a: dict(a, status="skip", detail=msg)), "", C
        node, src = C["node"], C["src"]
        srcs = [source_line(vn, node(t), spec) for t, (n, spec, vn) in src.items()]
        present = {x for l in C["deck"] + srcs for x in l.split()[1:]}
        probes = {}
        for a in good:
            reads = [("i", a["args"][0])] if a["fn"] == "i" else \
                [("v", x) for x in a["args"][:2 if a["fn"] == "gain" else 1]]
            for k, (typ, net) in enumerate(reads):
                t = C["nets"].token(net)
                if typ == "v" and t in src and not (a["fn"] == "gain" and k == 1):
                    raise BlockError(f"{a['fn']}({net}) reads a net a source drives: the result would be the source "
                                     "value, not the circuit's")
                if (typ, net) in probes:
                    continue
                if typ == "i":
                    if t not in src:
                        raise BlockError(f"I({net}): currents are measured in sources, and {net} has none")
                    v = src[t][2].lower()
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
        series, xs, opv, deck = run_deck(ng, C["deck"], srcs, kind, an, plist, need_op, wd, "run", stop)
        half = None
        if kind == "tran":
            hs, hx, _, _ = run_deck(ng, C["deck"], srcs, kind, analysis(blk["analysis"], names, half=True)[1], plist,
                                    False, wd, "half", stop)
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
def hierarchy(sheet):
    """Every sheet file of the hierarchy under `sheet` (itself first)."""
    seen, todo = [], [os.path.abspath(sheet)]
    while todo:
        f = todo.pop(0)
        if f in seen or not os.path.exists(f):
            continue
        seen.append(f)
        for m in re.finditer(r'\(property "Sheetfile" "([^"]+)"', open(f, errors="ignore").read()):
            todo.append(os.path.join(os.path.dirname(f), m.group(1)))
    return seen


def model_files(directives, sheet):
    out = []
    for d in directives:
        m = re.match(r'\.(?:include|inc|lib)\s+"?([^"\s]+)"?', d, re.I)
        if m:
            p = os.path.expandvars(os.path.expanduser(m.group(1)))
            out.append(p if os.path.isabs(p) else os.path.join(os.path.dirname(sheet), p))
    return out


def inputs_sha(block_body, files):
    """Contents of the block and of every file, with each file named relative to the first (the sheet), so the hash
    does not depend on where the project lives."""
    h = hashlib.sha256(TOOL_VERSION.encode())
    h.update(block_body.encode())
    base = os.path.dirname(os.path.realpath(files[0])) if files else ""
    for f in files:
        h.update(os.path.relpath(os.path.realpath(f), base).encode())
        h.update(open(f, "rb").read() if os.path.exists(f) else b"<missing>")
    return h.hexdigest()[:16]


GRADE = {"pass": "ACCEPTED", "provisional": "PROVISIONAL", "fail": "REJECTED", "skip": "NOT RUN"}


def overall(results):
    st = {r["status"] for r in results}
    return "REJECTED" if "fail" in st else "NOT RUN" if ("skip" in st or not st) else \
        "PROVISIONAL" if "provisional" in st else "ACCEPTED"


def strict_roots(prj):
    """Top sheets named after their folder (08_PCB/x/x.kicad_sch): no arbitrary fallback."""
    return [f for f in glob.glob(os.path.join(prj.p("pcb"), "**", "*.kicad_sch"), recursive=True)
            if "-backups" not in f and "/release/" not in f
            and os.path.splitext(os.path.basename(f))[0] == os.path.basename(os.path.dirname(f))]


def sheet_of(prj, brief_text):
    from .drift import frontmatter
    s = frontmatter(brief_text).get("kicad_sheet")
    if s and not s.startswith("08_PCB/<"):
        return os.path.join(prj.root, s)
    tops = strict_roots(prj)
    return tops[0] if len(tops) == 1 else None


def briefs(prj, module=None):
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
                res.append((m, b, blk, sheet_of(prj, text)))
    return res


def record_path(prj, m, brief):
    if m:
        return os.path.join(prj.root, m["dir"], "Simulations", os.path.splitext(os.path.basename(brief))[0] + "_spice.md")
    return prj.p("sim_results", "brief_" + os.path.splitext(os.path.basename(brief))[0] + "_spice.md")


def run(prj, module=None, strict=False):
    ng = find_ngspice()
    found = briefs(prj, module)
    if not found:
        return {"status": "skip", "reason": "no `## Simulation` block in any sheet brief", "results": []}
    if not ng:
        return {"status": "skip", "reason": "ngspice not found (brew install ngspice / apt install ngspice, or set NGSPICE)",
                "missing_tool": True, "results": []}
    results, records = [], []
    for m, brief, blk, sch in found:
        deck, info, files = "", {}, []
        with tempfile.TemporaryDirectory(prefix="forge-spice-") as wd:
            if not sch:
                rs = [dict(a, status="fail", detail="name the sheet in the brief's frontmatter (`kicad_sheet:`): no single "
                                                    "top sheet named after its folder in 08_PCB/") for a in blk["asserts"]]
            else:
                files = hierarchy(sch)
                cir = spice_export(sch, wd) if os.path.exists(sch) else None
                d = kicad.design(sch) if cir else None
                if cir is None or d is None:
                    rs = [dict(a, status="skip", detail="KiCad SPICE export failed (sheet missing, or kicad-cli)")
                          for a in blk["asserts"]]
                else:
                    rs, deck, info = run_block(blk, cir, d[0], ng, wd)
                    files += model_files(parse_export(cir)["directives"], sch)
        for r in rs:
            r["brief"] = prj.rel(brief)
        results += rs
        records.append(write_svr(prj, m, brief, blk, rs, deck, sch, files, ng, info))
    st = {r["status"] for r in results}
    status = "fail" if "fail" in st or (strict and st & {"skip", "provisional"}) else \
        "warn" if st & {"skip", "provisional"} else "pass"
    return {"status": status, "reason": f"{len(results)} assertion(s)", "results": results, "records": records}


def cell(s):
    return str(s).replace("|", "\\|").replace("\n", " ")


def write_svr(prj, m, brief, blk, res, deck, sch, files, ng, info):
    p = record_path(prj, m, brief)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    result = overall(res)
    kind = (blk["analysis"].split() or ["op"])[0].lower()
    rel = lambda f: prj.rel(f) if os.path.abspath(f).startswith(prj.root) else f
    lines = ["---", "type: simulation", "tool: ngspice (forge spice check)", f"result: {result}",
             f"brief: {prj.rel(brief)}", f"sheet: {prj.rel(sch) if sch else '(none)'}",
             f"files: {'; '.join(rel(f) for f in files)}", f"inputs: {inputs_sha(blk['text'], files)}",
             f"date: {dt.date.today().isoformat()}", "---",
             f"# {os.path.basename(brief)}: ngspice assertions ({result})", "",
             f"Analysis `{cell(blk['analysis'])}` · scope {cell(', '.join(blk['scope']) or '(sources)')} · ground "
             f"{cell(blk['ground'] or '(auto)')}", ""]
    if info.get("ignored") or info.get("dnp"):
        lines += [f"Not simulated: {', '.join(info.get('ignored', []))} (ignore:)" if info.get("ignored") else "",
                  f"Not simulated: {', '.join(info['dnp'])} (DNP)" if info.get("dnp") else "", ""]
    lines += ["## Results", "", "| Assertion | Result | Detail |", "|---|---|---|"]
    lines += [f"| `{cell(r['text'])}` | {GRADE[r['status']]} | {cell(r['detail'])} |" for r in res]
    prov = [r for r in res if r["status"] == "provisional"]
    if prov:
        lines += ["", "## Bench tests needed (PROVISIONAL results)", ""] + [f"- [ ] `{cell(r['text'])}`" for r in prov]
    lines += ["", "## Simulation Verification Record (SVR)", "", "| Item | Record |", "|---|---|",
              "| Model provenance | KiCad's SPICE export of the drawn sheet, cut to the scope (a part reaching outside it, or a "
              "drawn part missing from the export, stops the block); KiCad's ideal passives; other parts from Sim_Model / "
              "Sim_Pins or KiCad Sim.* fields |",
              "| Anchor | the `expect:` closed form of each assertion (agreement in Detail); without one the result is PROVISIONAL |",
              f"| Convergence | {ngspice_version(ng)} batch run: exit code, and the log scanned for {LOG_CHECKS}; data "
              "layout checked" + ("; transient stop time reached" if kind == "tran" else "") + " |",
              "| Time step | " + ("re-run at half the time step; a move over 1 % makes the result PROVISIONAL |" if kind == "tran"
                                 else "not applicable (op / dc / ac) |"),
              "| Directives from the sheet | " + cell("; ".join(info.get("directives", [])) or "none")
              + (f" (not used: {cell('; '.join(info['dropped']))})" if info.get("dropped") else "") + " |",
              "| Energy / KCL closure | not checked by this tool; check by hand where it matters |",
              "| Corners | only what this block states; write one block per corner |",
              "| Reproduce | `forge spice check" + (f" --module {m['id']}" if m else "") + "` |", "",
              "## Deck", "", "```", re.sub(r"wrdata \S+", "wrdata out.txt", deck.strip()) or "(not run)", "```", ""]
    open(p, "w").write("\n".join(l for l in lines if l is not None))
    write_result(prj, m, brief, blk, res, p, result)
    return prj.rel(p)


def write_result(prj, m, brief, blk, res, record, result):
    """The record as data (<stem>.result.json) for the side panel and the report."""
    from . import results
    metrics = []
    for r in res:
        cf = r.get("expect")
        err = ((r["value"] - cf) / cf * 100) if cf and r.get("value") is not None else None
        metrics.append({"name": r["text"], "unit": "", "values": [r.get("value")], "closed_form": [cf] if cf is not None else None,
                        "err_pct": err, "grade": GRADE[r["status"]], "note": r["detail"]})
    doc = {"id": os.path.splitext(os.path.basename(record))[0], "title": f"{os.path.basename(brief)}: ngspice assertions",
           "module": m["id"] if m else None, "tool": "ngspice (forge spice check)", "record": prj.rel(record),
           "grade": result, "corners": [blk["analysis"]], "metrics": metrics, "charts": [],
           "notes": [f"{GRADE[r['status']]}: {r['text']}: {r['detail']}" for r in res if r["status"] != "pass"]}
    try:
        results.write(prj, os.path.splitext(record)[0] + ".result.json", doc)
    except (ValueError, OSError):
        pass


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
    btext = open(brief, errors="ignore").read()
    body = block_text(btext)
    sheet = sheet_of(prj, btext)
    if body is None or not sheet or prj.rel(sheet) != fm.get("sheet"):
        return "stale: the brief no longer points at the sheet this record was made from (re-run forge spice check)"
    recorded = [f for f in (fm.get("files") or "").split("; ") if f]
    models = [f if os.path.isabs(f) else os.path.join(prj.root, f) for f in recorded
              if not f.endswith(".kicad_sch")]
    files = hierarchy(sheet) + models
    if inputs_sha(body, files) != fm.get("inputs"):
        return "stale: the Simulation block, a sheet of the hierarchy, a model file or the tool changed since the run " \
               "(re-run forge spice check)"
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
