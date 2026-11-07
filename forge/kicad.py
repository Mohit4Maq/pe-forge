"""One KiCad command-line layer for every pe-forge tool (standard library only).

  find_cli()                 kicad-cli: $KICAD_CLI, PATH, the macOS app, common Linux paths (single source of truth)
  find_python()              KiCad's bundled Python (has pcbnew): $KICAD_PYTHON, macOS app, system python3
  stock_symbols()            KiCad's stock symbol folder: $KICAD_SYMBOL_DIR / $KICAD10_SYMBOL_DIR, app, /usr/share
  erc(sch)    -> Report      `kicad-cli sch erc --format json`, violations normalised
  drc(pcb)    -> Report      `kicad-cli pcb drc --format json --schematic-parity --all-track-errors`
  netlist(sch) -> {net: {"REF.PIN", ...}} or None

Honest results: a Report with tool_ok False means kicad-cli did not produce a readable report. Callers must treat
that as a failure ("kicad-cli produced no report"), never as a pass, whatever the exit code was.

KiCad 10 JSON quirks (read from captured reports in tests/fixtures/kicad/, not guessed):
  - ERC nests violations per sheet (sheets[].violations) and gives item positions in units of 100 mm
    (a pin at 55.88 mm is reported as 0.5588); DRC gives positions in mm. Report items are always in mm.
  - DRC has top-level violations, unconnected_items and schematic_parity.
  - Without <same stem>.kicad_pro next to the file, title-block text variables are unresolved: ERC/DRC report
    `unresolved_variable`. Those are dropped for a file checked on its own, kept for a project's top file.

Other copies of the search order (documented fallbacks, used only when this module cannot be imported because the
library lives outside the pe-forge repo): library/tools/kicad_paths.py.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field

MAC_APP = "/Applications/KiCad/KiCad.app"
CLI_PATHS = [MAC_APP + "/Contents/MacOS/kicad-cli", "/usr/bin/kicad-cli", "/usr/local/bin/kicad-cli",
             "/snap/bin/kicad-cli", "/var/lib/flatpak/exports/bin/kicad-cli"]
PY_PATHS = [MAC_APP + "/Contents/Frameworks/Python.framework/Versions/Current/bin/python3"]
SYMBOL_PATHS = [MAC_APP + "/Contents/SharedSupport/symbols", "/usr/share/kicad/symbols",
                "/usr/local/share/kicad/symbols"]
ERC_POS_SCALE = 100.0     # KiCad 10 ERC JSON positions are in 100 mm units


def _first(cands):
    return next((c for c in cands if c and os.path.exists(c)), None)


def find_cli():
    """Path to kicad-cli, or None."""
    return _first([os.environ.get("KICAD_CLI"), shutil.which("kicad-cli"), *CLI_PATHS])


def find_python():
    """A Python that can `import pcbnew` (KiCad's bundled one on macOS), or None."""
    return _first([os.environ.get("KICAD_PYTHON"), *PY_PATHS, shutil.which("python3")])


def stock_symbols():
    return _first([os.environ.get("KICAD_SYMBOL_DIR"), os.environ.get("KICAD10_SYMBOL_DIR"), *SYMBOL_PATHS])


@dataclass
class Report:
    source: str                       # "erc" | "drc"
    path: str                         # the checked .kicad_sch / .kicad_pcb
    tool_ok: bool = False             # kicad-cli ran and wrote a report that parsed
    error: str = ""                   # why tool_ok is False
    violations: list = field(default_factory=list)   # severity-filtered: what decides `ok`
    warnings: list = field(default_factory=list)     # violations below the requested severity
    unconnected: list = field(default_factory=list)  # DRC: unrouted connections
    parity: list = field(default_factory=list)       # DRC: schematic / board mismatches
    raw_path: str = ""

    @property
    def ok(self):
        return self.tool_ok and not self.violations

    def summary(self):
        name = os.path.basename(self.path)
        if not self.tool_ok:
            return f"{self.source.upper()} {name}: {self.error}"
        s = f"{self.source.upper()} {name}: {len(self.violations)} error(s)"
        if self.source == "drc":
            s += f", {len(self.unconnected)} unconnected, {len(self.parity)} parity issue(s)"
        return s

    def lines(self, limit=20):
        """One line per finding: `location: type, description`."""
        out = []
        for v in self.violations[:limit]:
            where = [f"sheet {v['sheet']}"] if v["sheet"] else []
            it = next((i for i in v["items"] if i.get("pos")), None)
            if it:
                where.append(f"@ ({it['pos'][0]:g}, {it['pos'][1]:g}) mm")
            refs = ", ".join(i["ref"] for i in v["items"] if i.get("ref"))
            out.append(" ".join([os.path.basename(self.path), *where]) + f": {v['type']}, {v['description']}"
                       + (f" [{refs}]" if refs else ""))
        return out

    def to_dict(self):
        return {"source": self.source, "path": self.path, "ok": self.ok, "tool_ok": self.tool_ok, "error": self.error,
                "violations": self.violations, "warnings": self.warnings, "unconnected": self.unconnected,
                "parity": self.parity, "raw_path": self.raw_path}


REF_RE = re.compile(r"^(?:Symbol|Footprint) (\S+)|\bof (\S+)")


def _item(i, scale, sheet):
    d = i.get("description", "")
    m = REF_RE.search(d)
    ref = (m.group(1) or m.group(2)) if m else ""
    pos = i.get("pos")
    return {"description": d, "ref": ref, "sheet": sheet,
            "pos": (round(pos.get("x", 0) * scale, 4), round(pos.get("y", 0) * scale, 4)) if pos else None}


def _viol(v, scale, sheet=""):
    return {"severity": v.get("severity", "error"), "type": v.get("type", "unknown"),
            "description": v.get("description", ""), "sheet": sheet,
            "items": [_item(i, scale, sheet) for i in v.get("items", [])]}


def parse(raw, source, path="", severity=("error",), has_project=True):
    """Normalise a kicad-cli JSON report (already loaded) into a Report."""
    rep = Report(source, path, tool_ok=True)
    keep = lambda v: has_project or v["type"] != "unresolved_variable"
    allv = []
    if source == "erc":
        if "sheets" not in raw:
            return Report(source, path, error="kicad-cli report has no 'sheets' (not an ERC report?)")
        for sh in raw.get("sheets", []):
            allv += [_viol(v, ERC_POS_SCALE, sh.get("path", "")) for v in sh.get("violations", [])]
    else:
        if "violations" not in raw:
            return Report(source, path, error="kicad-cli report has no 'violations' (not a DRC report?)")
        allv = [_viol(v, 1.0) for v in raw.get("violations", [])]
        rep.unconnected = [_viol(v, 1.0) for v in raw.get("unconnected_items", [])]
        rep.parity = [_viol(v, 1.0) for v in raw.get("schematic_parity", [])]
    allv = [v for v in allv if keep(v)]
    rep.violations = [v for v in allv if v["severity"] in severity]
    rep.warnings = [v for v in allv if v["severity"] not in severity]
    return rep


def _run(args, out, source, path, severity, has_project):
    cli = find_cli()
    if not cli:
        return Report(source, path, error="kicad-cli not found (install KiCad 10 or set KICAD_CLI; forge doctor)")
    if os.path.exists(out):
        os.remove(out)
    try:
        r = subprocess.run([cli, *args], capture_output=True, text=True, timeout=600)
    except Exception as e:
        return Report(source, path, error=f"kicad-cli could not run: {e}")
    if not os.path.exists(out) or os.path.getsize(out) == 0:
        tail = (r.stderr or r.stdout or "").strip().splitlines()[-1:] or [""]
        return Report(source, path, error="kicad-cli produced no report" + (f" ({tail[0][:120]})" if tail[0] else ""))
    try:
        raw = json.load(open(out))
    except Exception as e:
        return Report(source, path, error=f"kicad-cli report did not parse: {e}")
    rep = parse(raw, source, path, severity, has_project)
    rep.raw_path = out
    return rep


def _has_project(path):
    """kicad-cli resolves text variables only from <same stem>.kicad_pro: a sheet checked on its own without one
    reports every title-block variable as unresolved, which says nothing about the sheet."""
    return os.path.exists(os.path.splitext(os.path.abspath(path))[0] + ".kicad_pro")


def _out(path, out, suffix):
    """The report / export file: `out` if given, else a unique temp file (two runs never share one)."""
    if out:
        return out
    d = os.path.join(tempfile.gettempdir(), "pe-forge-kicad")
    os.makedirs(d, exist_ok=True)
    fd, p = tempfile.mkstemp(prefix=os.path.basename(path) + ".", suffix=suffix, dir=d)
    os.close(fd)
    return p


def erc(sch, severity=("error",), out=None):
    """ERC of a schematic. `severity`: which levels count as violations (the rest go to `warnings`)."""
    out = _out(sch, out, ".erc.json")
    return _run(["sch", "erc", "--format", "json", "--severity-all", "-o", out, sch], out, "erc", sch,
                tuple(severity), _has_project(sch))


def drc(pcb, parity=True, severity=("error",), out=None):
    """DRC of a board, with unconnected items and (if a schematic sits next to it) schematic parity."""
    out = _out(pcb, out, ".drc.json")
    args = ["pcb", "drc", "--format", "json", "--severity-all", "--all-track-errors"]
    if parity and os.path.exists(os.path.splitext(pcb)[0] + ".kicad_sch"):
        args.append("--schematic-parity")
    return _run(args + ["-o", out, pcb], out, "drc", pcb, tuple(severity), _has_project(pcb))


def block_at(text, start):
    """Balanced s-expression starting at text[start] == '('."""
    d, i = 0, start
    while True:
        c = text[i]
        if c == '"':
            i += 1
            while text[i] != '"':
                if text[i] == "\\":
                    i += 1
                i += 1
        elif c == "(":
            d += 1
        elif c == ")":
            d -= 1
            if d == 0:
                return text[start:i + 1]
        i += 1


def parse_netlist(t):
    nets = {}
    for m in re.finditer(r"\(net\s*\(code", t):
        blk = block_at(t, m.start())
        name = re.search(r'\(name "([^"]*)"\)', blk).group(1).lstrip("/")
        nodes = re.findall(r'\(node\s*\(ref "([^"]+)"\)\s*\(pin "([^"]+)"\)', blk)
        nets[name] = {f"{r}.{p}" for r, p in nodes if not r.startswith("#")}
    return nets


def netlist(sch, out=None):
    """{net name: {"REF.PIN"}} from KiCad's own netlist export, or None if kicad-cli gave nothing."""
    cli = find_cli()
    if not cli:
        return None
    mine = out is None
    out = out or _out(sch, None, ".net")
    if os.path.exists(out):
        os.remove(out)
    subprocess.run([cli, "sch", "export", "netlist", "--format", "kicadsexpr", "-o", out, sch], capture_output=True)
    if not os.path.exists(out):
        return None
    text = open(out).read()
    if mine:
        os.remove(out)
    return parse_netlist(text)


# ------------------------------------------------------------------ components + nets in one export
def parse_sexpr(text):
    tokens = re.findall(r'"(?:[^"\\]|\\.)*"|[()]|[^\s()"]+', text)
    stack, cur = [], []
    for t in tokens:
        if t == "(":
            stack.append(cur)
            cur = []
        elif t == ")":
            done, cur = cur, stack.pop()
            cur.append(done)
        else:
            cur.append(t[1:-1].replace('\\"', '"').replace("\\\\", "\\") if t.startswith('"') else t)
    return cur[0] if cur else []


def _get(node, key):
    return next((c for c in node if isinstance(c, list) and c and c[0] == key), None)


def _val(node, key, default=""):
    n = _get(node or [], key)
    return n[1] if n and len(n) > 1 and isinstance(n[1], str) else default


def parse_design(text):
    """(components, nets) from a kicadsexpr netlist.
    components: {ref: {ref, value, footprint, fields{}, dnp, in_bom, sheetfile, sheet}}; nets: {name: {"REF.PIN"}}"""
    tree = parse_sexpr(text)
    comps = {}
    for c in (_get(tree, "components") or [])[1:]:
        fields = {}
        for f in (_get(c, "fields") or [])[1:]:
            fields[_val(f, "name")] = next((x for x in f[1:] if isinstance(x, str)), "")
        props = {_val(p, "name"): _val(p, "value") for p in c if isinstance(p, list) and p and p[0] == "property"}
        ref = _val(c, "ref")
        comps[ref] = {"ref": ref, "value": _val(c, "value"), "footprint": _val(c, "footprint") or fields.get("Footprint", ""),
                      "fields": fields, "dnp": "dnp" in props, "in_bom": "exclude_from_bom" not in props,
                      "board_excluded": "exclude_from_board" in props,
                      "sheetfile": props.get("Sheetfile", ""), "sheet": _val(_get(c, "sheetpath"), "names", "/")}
    nets = {}
    for n in (_get(tree, "nets") or [])[1:]:
        raw = _val(n, "name").replace("{slash}", "/")
        name = raw.lstrip("/")
        token = re.sub(r"[\s()\[\]{},;=]", "_", raw)       # how KiCad's SPICE export spells this net
        pins = set()
        for nd in n:
            if isinstance(nd, list) and nd and nd[0] == "node":
                ref, pin = _val(nd, "ref"), _val(nd, "pin")
                if ref.startswith("#"):
                    continue
                pins.add(f"{ref}.{pin}")
                if ref in comps:     # pin name, type and net, for lookups (forge explain)
                    fn = _val(nd, "pinfunction")
                    if fn.endswith("_" + pin):        # KiCad 10 writes pin K of pin 1 as "K_1"
                        fn = fn[:-len(pin) - 1]
                    comps[ref].setdefault("pins", {})[pin] = {"name": fn, "type": _val(nd, "pintype"),
                                                               "net": name, "class": _val(n, "class", "Default"),
                                                               "token": token}
        nets[name] = pins
    return comps, nets


def design(sch, out=None):
    """(components, nets) of a schematic (its whole hierarchy) from KiCad's netlist export, or None."""
    cli = find_cli()
    if not cli:
        return None
    mine = out is None
    out = out or _out(sch, None, ".design.net")
    if os.path.exists(out):
        os.remove(out)
    subprocess.run([cli, "sch", "export", "netlist", "--format", "kicadsexpr", "-o", out, sch], capture_output=True)
    if not os.path.exists(out):
        return None
    text = open(out).read()
    if mine:
        os.remove(out)
    return parse_design(text)
