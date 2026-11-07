"""Shared helpers: export a KiCad netlist with kicad-cli and parse it into plain Python objects."""
from __future__ import annotations

import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kicad_paths  # noqa: E402

CLI = kicad_paths.cli()


def parse_sexpr(text: str):
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
    return cur[0]


def _get(node, key):
    return next((c for c in node if isinstance(c, list) and c and c[0] == key), None)


def _val(node, key, default=""):
    n = _get(node, key)
    return n[1] if n and len(n) > 1 else default


@dataclass
class Comp:
    ref: str
    value: str
    footprint: str
    fields: dict[str, str]
    lib: str
    part: str
    dnp: bool
    in_bom: bool
    sheet: str
    sheetfile: str = ""


@dataclass
class Node:
    ref: str
    pin: str
    name: str    # pin name ("pinfunction")
    type: str    # passive, input, power_in, ...


@dataclass
class Net:
    name: str
    cls: str
    nodes: list[Node] = field(default_factory=list)

    @property
    def unnamed(self) -> bool:
        return self.name.startswith(("Net-(", "unconnected-("))


def load(sch: Path) -> tuple[dict[str, Comp], list[Net]]:
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "n.net"
        r = subprocess.run([CLI, "sch", "export", "netlist", "--format", "kicadsexpr", "-o", str(out), str(sch)],
                           capture_output=True, text=True)
        if r.returncode or not out.exists():
            raise SystemExit(f"netlist export failed: {r.stdout}{r.stderr}")
        tree = parse_sexpr(out.read_text())
    comps = {}
    for c in _get(tree, "components")[1:]:
        fields = {}
        for f in (_get(c, "fields") or [])[1:]:
            name = _val(f, "name")
            fields[name] = next((x for x in f[1:] if isinstance(x, str)), "")
        props = {_val(p, "name"): _val(p, "value") for p in c if isinstance(p, list) and p[0] == "property"}
        src = _get(c, "libsource") or []
        sheet = _val(_get(c, "sheetpath") or [], "names", "/")
        ref = _val(c, "ref")
        comps[ref] = Comp(ref, _val(c, "value"), fields.get("Footprint", "") or _val(c, "footprint"), fields,
                          _val(src, "lib"), _val(src, "part"), "dnp" in props,
                          "exclude_from_bom" not in props, sheet, props.get("Sheetfile", ""))
    nets = []
    for n in _get(tree, "nets")[1:]:
        net = Net(_val(n, "name"), _val(n, "class", "Default"))
        for nd in n:
            if isinstance(nd, list) and nd[0] == "node":
                net.nodes.append(Node(_val(nd, "ref"), _val(nd, "pin"), _val(nd, "pinfunction"), _val(nd, "pintype")))
        nets.append(net)
    return comps, nets


def find_sch(arg: str) -> Path:
    p = Path(arg).expanduser().resolve()
    if p.is_dir():
        pros = list(p.glob("*.kicad_pro"))
        if len(pros) != 1:
            raise SystemExit(f"expected one .kicad_pro in {p}")
        p = pros[0].with_suffix(".kicad_sch")
    elif p.suffix == ".kicad_pro":
        p = p.with_suffix(".kicad_sch")
    if not p.exists():
        raise SystemExit(f"no schematic {p}")
    return p
