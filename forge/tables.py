"""Shared table reading for BoMs, briefs and booklets (drift check, supplier export). Standard library only.

Columns are found by name through aliases, so a reordered or renamed column never breaks a reader:
  ref        Ref, Refs, Reference, References, Designator, Designators, RefDes
  value      Value, Val
  mpn        MPN, MPN (primary), Part Number, Mfr PN, Manufacturer Part Number
  footprint  Footprint, Land pattern
  mc_id      MC_ID, MC_Lib ID, MC Lib ID, Library ID
  dnp        DNP, Fitted (inverted), Populate (inverted)
  qty        Qty, Quantity
  lcsc       LCSC, LCSC_PN, LCSC Part #, LCSC Part Number
  net / pins Net, Net name / Pins, Pins (Ref.pin), Nodes
Values compare by meaning: 4u7 = 4.7u = 4.7 uF = 4.7µF, 10k = 10 kΩ = 10K = 10000, 2R2 = 2.2 Ω, 100n = 0.1u.
"""
import csv
import io
import re

ALIASES = {
    "ref": ["ref", "refs", "reference", "references", "designator", "designators", "refdes"],
    "value": ["value", "val"],
    "mpn": ["mpn", "mpn (primary)", "part number", "mfr pn", "manufacturer part number", "mfr part number"],
    "manufacturer": ["manufacturer", "mfr", "maker"],
    "footprint": ["footprint", "land pattern"],
    "mc_id": ["mc_id", "mc_lib id", "mc lib id", "library id", "mc id"],
    "dnp": ["dnp"],
    "fitted": ["fitted", "populate"],
    "qty": ["qty", "quantity"],
    "lcsc": ["lcsc", "lcsc_pn", "lcsc part #", "lcsc part number", "lcsc pn"],
    "net": ["net", "net name"],
    "pins": ["pins", "pins (ref.pin)", "nodes", "pins (ref.pin, ...)"],
}


def key_of(header):
    h = re.sub(r"\s+", " ", header.strip().lower())
    for k, names in ALIASES.items():
        if h in names:
            return k
    return None


def normalise_rows(headers, rows):
    """rows of raw dicts -> rows with canonical keys added (raw keys kept)."""
    keys = {h: key_of(h) for h in headers}
    out = []
    for r in rows:
        d = dict(r)
        for h, k in keys.items():
            if k and k not in d:
                d[k] = (r.get(h) or "").strip()
        out.append(d)
    return out


def read_csv(path):
    rows = list(csv.DictReader(open(path, newline="", encoding="utf-8-sig")))
    hdr = list(rows[0].keys()) if rows else (next(csv.reader(open(path, newline="", encoding="utf-8-sig")), []))
    return normalise_rows(hdr, rows)


def md_tables(text):
    """Every pipe table in a markdown text: [(line_no_of_header, headers, rows[{header: cell, '_line': n}])]."""
    out, lines, i = [], text.split("\n"), 0
    while i < len(lines):
        ln = lines[i]
        if ln.lstrip().startswith("|") and i + 1 < len(lines) and re.match(r"^\s*\|[\s:|-]+\|\s*$", lines[i + 1]):
            hdr = [c.strip() for c in ln.strip().strip("|").split("|")]
            rows, j = [], i + 2
            while j < len(lines) and lines[j].lstrip().startswith("|"):
                cells = [c.strip() for c in lines[j].strip().strip("|").split("|")]
                rows.append(dict(zip(hdr, cells), _line=j + 1))
                j += 1
            out.append((i + 1, hdr, normalise_rows(hdr, rows)))
            i = j
        else:
            i += 1
    return out


def placeholder(cell):
    return not cell or bool(re.fullmatch(r"_?\(fill in\)_?|tbd|todo|-|—|…|\.\.\.|x", cell.strip(), re.I))


def refs(cell):
    """'C1, C2' / 'C1 C2' / 'C1-C3' / 'C1–C3' -> ['C1', 'C2', 'C3'] (ranges only within one prefix)."""
    out = []
    for part in re.split(r"[,;\s]+", (cell or "").strip()):
        if not part:
            continue
        m = re.fullmatch(r"([A-Za-z_#]+)(\d+)\s*[-–]\s*(?:\1)?(\d+)", part)
        if m and int(m.group(3)) >= int(m.group(2)) and int(m.group(3)) - int(m.group(2)) < 500:
            out += [f"{m.group(1)}{n}" for n in range(int(m.group(2)), int(m.group(3)) + 1)]
        elif re.fullmatch(r"[A-Za-z_#]+\d+[A-Za-z]?", part):
            out.append(part)
    return out


def pins(cell):
    return [p for p in re.split(r"[,;\s]+", (cell or "").strip().strip("`")) if re.fullmatch(r"[A-Za-z_#]+\d+\.[\w+\-]+", p)]


def truthy(cell):
    return (cell or "").strip().lower() in ("yes", "y", "1", "true", "x", "dnp")


# ------------------------------------------------------------------ values
PREFIX = {"p": 1e-12, "n": 1e-9, "u": 1e-6, "µ": 1e-6, "μ": 1e-6, "m": 1e-3, "": 1.0, "k": 1e3, "K": 1e3,
          "M": 1e6, "G": 1e9, "R": 1.0, "r": 1.0}
UNIT = {"f": "F", "farad": "F", "h": "H", "henry": "H", "ω": "ohm", "Ω": "ohm", "ohm": "ohm", "ohms": "ohm",
        "r": "ohm", "v": "V", "a": "A", "w": "W", "hz": "Hz", "%": "%"}
NUM = re.compile(r"^([0-9]*\.?[0-9]+)\s*([pnuµμmkKMGRr]?)\s*([A-Za-zΩω%]*)$")
RKM = re.compile(r"^([0-9]+)([pnuµμmkKMGRr])([0-9]+)\s*([A-Za-zΩω]*)$")


def _unit(u):
    if not u:
        return ""
    return UNIT.get(u, UNIT.get(u.lower(), u.lower()))


def _token(tok):
    """'4u7' -> (4.7e-6, 'F' or ''), or None when it is not a number."""
    tok = tok.replace("±", "").strip()
    m = RKM.match(tok)
    if m:
        a, p, b, u = m.groups()
        return float(f"{a}.{b}") * PREFIX[p], _unit(u) or ("ohm" if p in "Rr" else "")
    m = NUM.match(tok)
    if m:
        num, p, u = m.groups()
        if p in ("R", "r") and not u:
            return float(num), "ohm"
        return float(num) * PREFIX.get(p, 1.0), _unit(u)
    return None


def norm_value(s):
    """A comparable key for a component value. Numbers become magnitudes; text is case- and space-folded."""
    s = (s or "").strip().replace("≥", ">=").replace("≤", "<=").replace(",", " ")
    s = re.sub(r"(\d)\s+([pnuµμmkKMG](?:F|H|Ω|ohms?|V|A|W|Hz)?|F|H|Ω|ohms?|V|A|W|Hz)(?=\s|$)", r"\1\2", s)
    out = []
    for tok in s.split():
        t = _token(tok)
        out.append(("n", float(f"{t[0]:.6g}"), t[1]) if t else ("s", tok.casefold().replace("µ", "u").replace("μ", "u")))
    return tuple(out)


def same_value(a, b):
    ka, kb = norm_value(a), norm_value(b)
    if len(ka) != len(kb):
        return False
    for x, y in zip(ka, kb):
        if x[0] != y[0]:
            return False
        if x[0] == "s" and x != y:
            return False
        if x[0] == "n" and (x[1] != y[1] or (x[2] and y[2] and x[2] != y[2])):
            return False
    return True


def same_footprint(a, b):
    """Case kept (library ids are case sensitive); a bare name matches the same name with a library prefix."""
    a, b = re.sub(r"\s+", "", a or ""), re.sub(r"\s+", "", b or "")
    if not a or not b:
        return True
    if ":" in a and ":" in b:
        return a == b
    return a.split(":")[-1] == b.split(":")[-1]


def same_mpn(a, b):
    f = lambda s: re.sub(r"[\s\-_/]", "", (s or "").upper())
    return not a or not b or f(a) == f(b)


def as_csv(rows, headers):
    buf = io.StringIO()
    w = csv.DictWriter(buf, headers, extrasaction="ignore")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()
