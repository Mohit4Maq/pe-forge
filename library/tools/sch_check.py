#!/usr/bin/env python3
"""sch_check - schematic design checks beyond KiCad's ERC (library discipline, net naming, decoupling).

  sch_check.py <project dir | .kicad_pro | .kicad_sch>  [--json] [--no-erc] [--strict]

Exit code 1 if any ERROR (or any WARN with --strict). Codes:
  LIB-*  part is not from MC_Parts / unknown ID / stale fields vs database / not reviewed / no footprint
  NET-*  unnamed IC nets (with name suggestions), label typos, near-duplicate names, power nets that
         miss their net class, mixed active-low styles, bad characters
  DEC-*  IC power pins with no capacitor on the net
  ERC-*  KiCad ERC summary
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kinet  # noqa: E402
import mclib  # noqa: E402

SYNC_FIELDS = ["Value", "MPN", "Manufacturer", "DigiKey_PN", "Mouser_PN"]
IC_PREFIX = ("U", "IC", "J", "P", "Q", "T", "K", "X", "Y", "D")
GROUND = re.compile(r"^(?:[ADPS]?GND|GND_?\w*|VSS\w*|0V|COM)$", re.I)
# names that look like supplies; the MC_Standard Power class catches "+*", "VBAT*", "VIN*", "PGND*"
POWERISH = re.compile(r"^(?:\+?-?\d+V\d*|\+?\d+V\d+|\d+V\d*_\w+|V(?:CC|DD|BAT|IN|BUS|SYS|DRV|IO|AA|A)\w*|P?VCC\w*|VREF\w*)$", re.I)
ACTIVE_LOW = [("~{X}", re.compile(r"^~\{.+\}$")), ("X_N", re.compile(r".+_[Nn]$")),
              ("nX", re.compile(r"^n[A-Z][A-Z0-9_]+$")), ("X#", re.compile(r".+#$")), ("/X", re.compile(r"^/.+"))]


class Report:
    def __init__(self):
        self.items = []

    def add(self, sev, code, msg, fix=""):
        self.items.append({"severity": sev, "code": code, "message": msg, "fix": fix})

    def count(self, sev):
        return sum(i["severity"] == sev for i in self.items)


def db_rows() -> dict[str, dict]:
    con = sqlite3.connect(mclib.DB_PATH)
    con.row_factory = sqlite3.Row
    rows = {}
    for t in mclib.TABLES:
        for r in con.execute(f"SELECT * FROM {t}"):
            rows[r["ID"]] = dict(r)
    return rows


def check_parts(comps, rep: Report):
    db = db_rows()
    for ref, c in sorted(comps.items()):
        if is_rail(c):
            continue
        if ref.endswith("?"):
            rep.add("ERROR", "LIB-ANNOT", f"{ref}: not annotated", "Tools → Annotate Schematic")
        pid = c.fields.get("MC_ID", "")
        if not pid:
            rep.add("ERROR", "LIB-NOTDB", f"{ref} ({c.lib}:{c.part} {c.value}) was not placed from MC_Parts"
                    + ("" if c.footprint else ", and has no footprint"),
                    "Replace it with an MC_Parts part (right-click → Change Symbol…), or add the part to MC_Lib first")
            continue
        row = db.get(pid)
        if not row:
            rep.add("ERROR", "LIB-UNKNOWN", f"{ref}: MC_ID {pid} is not in the database")
            continue
        stale = [f for f in SYNC_FIELDS if (c.value if f == "Value" else c.fields.get(f, "")) != row[f]]
        allowed = [f for f in row["Footprints"].split(";") if f]
        if c.footprint not in allowed and row["Exclude_Board"] != "1":
            stale.append("Footprint")
        if stale:
            rep.add("WARN", "LIB-STALE", f"{ref} ({pid}): {', '.join(stale)} differ from database",
                    "Tools → Update Symbols from Library (keep 'Update/reset fields' ticked)")
        if row["Status"] in ("Seed", "Legacy", "NRND", "Obsolete"):
            sev = "ERROR" if row["Status"] == "Obsolete" else "WARN"
            rep.add(sev, "LIB-STATUS", f"{ref} ({pid} {row['MPN']}): status {row['Status']}",
                    "Review footprint vs datasheet, then `mclib.py set <ID> Status=Active`"
                    if row["Status"] in ("Seed", "Legacy") else "Choose an active replacement part")
        if not c.footprint and row["Exclude_Board"] != "1":
            rep.add("ERROR", "LIB-NOFP", f"{ref}: no footprint assigned")


def is_rail(c) -> bool:
    """Power/rail symbols: KiCad's own (#PWR, lib 'power') or custom rail symbols with a single pin."""
    return c.ref.startswith("#") or c.lib.lower() == "power" or c.part.lower().startswith(("rail", "pwr", "gnd", "vcc"))


def suggest_name(net, used):
    """Suggest a net name from the most meaningful IC/connector pin name on an unnamed net."""
    cands = []
    for n in net.nodes:
        name = n.name[:-len(n.pin) - 1] if n.pin and n.name.endswith("_" + n.pin) else n.name
        if not name or re.fullmatch(r"[\d~]+|Pad\d*|\d+_\d+|[A-Z]?\d+", name) or n.ref.startswith(("R", "C", "L", "FB")):
            continue
        name = re.sub(r"[^A-Za-z0-9_+~{}/-]", "_", name).strip("_")
        cands.append((0 if n.ref.startswith(("U", "IC")) else 1, n.ref, name))
    if not cands:
        return ""
    _, ref, name = sorted(cands)[0]
    return name if name.upper() not in used else f"{ref}_{name}"


def check_nets(comps, nets, rep: Report):
    names = [n.name for n in nets if not n.unnamed]
    used = {n.upper() for n in names}
    for net in nets:
        refs = {n.ref for n in net.nodes}
        # unnamed nets that reach an IC / connector pin: name them so layout, DRC classes and review read well
        if net.name.startswith("Net-(") and any(r.startswith(IC_PREFIX) for r in refs):
            s = suggest_name(net, used)
            pins = ", ".join(f"{n.ref}.{n.name or n.pin}" for n in net.nodes[:4]) + ("…" if len(net.nodes) > 4 else "")
            rep.add("WARN", "NET-UNNAMED", f"{net.name} ({pins}) is unnamed",
                    f"Add a net label '{s}'" if s else "Add a net label")
        # a named net with a single pin: usually a label typo or a missing label on the other end
        if not net.unnamed and len(net.nodes) == 1:
            n = net.nodes[0]
            close = [o for o in names if o != net.name and _norm(o) == _norm(net.name)]
            rep.add("ERROR", "NET-SINGLE", f"'{net.name}' connects to one pin only ({n.ref}.{n.name or n.pin})",
                    f"Probably meant '{close[0]}'" if close else "Typo in a label, or the other end is missing a label")
        if not net.unnamed and re.search(r"[^A-Za-z0-9_+\-./~{}]", net.name.split("/")[-1]):
            rep.add("WARN", "NET-CHARS", f"'{net.name}' contains spaces or unusual characters",
                    "Use A-Z 0-9 _ + - only (Gerber/CAM tools and scripts choke on others)")
        base = net.name.split("/")[-1]
        if (not net.unnamed and POWERISH.match(base) and not GROUND.match(base)
                and net.cls == "Default" and any(n.type in ("power_in", "power_out") for n in net.nodes)):
            fix = f"Rename to '+{base.lstrip('+')}'" if re.match(r"^\d", base) else \
                "Name supplies '+3V3', '+5V', 'VBAT…' so the Power net class applies, or add a pattern in Schematic Setup → Net Classes"
            rep.add("WARN", "NET-CLASS", f"'{net.name}' looks like a supply but is in net class Default", fix)
    # near-duplicates: same name ignoring case/separators, e.g. VCC_3V3 vs VCC3V3 vs vcc_3v3
    groups = defaultdict(set)
    for n in names:
        groups[_norm(n.split("/")[-1])].add(n)
    for g in groups.values():
        if len(g) > 1:
            rep.add("WARN", "NET-DUP", f"Nets that look identical but are separate: {', '.join(sorted(g))}",
                    "If they should be one net, use one spelling")
    # active-low naming styles
    styles = Counter()
    for n in names:
        for style, rx in ACTIVE_LOW:
            if rx.match(n.split("/")[-1]):
                styles[style] += 1
                break
    if len(styles) > 1:
        rep.add("WARN", "NET-STYLE", "Mixed active-low styles: " + ", ".join(f"{k}×{v}" for k, v in styles.items()),
                "Pick one; KiCad renders ~{RESET} with an overbar")


def _norm(s):
    return re.sub(r"[_\-.\s]", "", s).upper()


def check_decoupling(comps, nets, rep: Report):
    for net in nets:
        if net.unnamed and not net.name.startswith("Net-("):
            continue
        if GROUND.match(net.name.split("/")[-1]):
            continue
        ics = sorted({f"{n.ref}.{n.name or n.pin}" for n in net.nodes
                      if n.type == "power_in" and n.ref.startswith(("U", "IC"))})
        caps = [n.ref for n in net.nodes if n.ref.startswith("C")]
        if ics and not caps:
            rep.add("WARN", "DEC-NOCAP", f"{net.name}: IC power pins {', '.join(ics)} have no capacitor on this net",
                    "Add local decoupling, or check the pin is really a supply")


BUILTIN_VARS = {"PROJECTNAME", "CURRENT_DATE", "SHEETNAME", "SHEETPATH", "FILENAME", "FILEPATH", "TITLE",
                "REVISION", "ISSUE_DATE", "COMPANY", "PAPER", "KICAD_VERSION", "LAYER", "#", "##", "REFERENCE",
                "VALUE", "FOOTPRINT", "NET_NAME", "NET_CLASS", "SHORT_NET_NAME", "PIN_NAME", "CONNECTION_TYPE",
                "OP", "DNP", "EXCLUDE_FROM_BOM", "EXCLUDE_FROM_BOARD", "EXCLUDE_FROM_SIM", "DATASHEET",
                "DESCRIPTION", "PART", "UNIT", "SYMBOL_NAME", "SYMBOL_LIBRARY", "INTERSHEET_REFS", "VIA_COUNT",
                "DRC_ERROR", "DRC_WARNING", "ERC_ERROR", "ERC_WARNING", "CURRENT_TIME"} | {f"COMMENT{i}" for i in range(1, 10)}


def check_text_vars(sch: Path, rep: Report):
    """Name the variable behind KiCad's anonymous 'unresolved text variable' error."""
    pro = sch.with_suffix(".kicad_pro")
    try:
        defined = json.loads(pro.read_text()).get("text_variables", {})
    except (OSError, json.JSONDecodeError):
        return
    for f in [*sch.parent.glob("*.kicad_sch"), sch.with_suffix(".kicad_pcb")]:
        if not f.exists():
            continue
        for var in sorted(set(re.findall(r"\$\{([A-Za-z0-9_#]+)\}", f.read_text()))):
            if var in defined:
                if not defined[var]:
                    rep.add("WARN", "PRJ-VAR-EMPTY", f"text variable ${{{var}}} used in {f.name} is empty",
                            "Give it a value in Schematic Setup → Text Variables (e.g. TBD)")
            elif var.upper() not in BUILTIN_VARS and not var.startswith(("MC_", "KIPRJMOD", "KICAD")):
                close = [d for d in [*defined, *BUILTIN_VARS] if d.replace("_", "") == var.replace("_", "")]
                rep.add("ERROR", "PRJ-VAR", f"${{{var}}} in {f.name} is not defined",
                        f"Did you mean ${{{close[0]}}}? Fix it in File → Page Settings" if close
                        else "Define it in Schematic Setup → Text Variables, or fix the spelling")


def check_coverage(sch: Path, comps, rep: Report):
    """Sheets in the project folder whose parts never reach the netlist: orphan files, or independent
    top-level sheets (KiCad 10) that this check did not load. Say so instead of silently passing."""
    seen = {Path(c.sheetfile).name for c in comps.values() if c.sheetfile}
    for f in sorted(sch.parent.glob("*.kicad_sch")):
        if f.name in seen or f == sch:
            continue
        if "(lib_id" in f.read_text():
            rep.add("WARN", "SCH-NOTCHECKED", f"{f.name} has parts but is not part of the checked design ({sch.name})",
                    "Place it as a sheet in the hierarchy, or it is a separate top-level sheet: run the check on it too "
                    f"(sch-check {f.name}) and tell Claude, so tools can be extended to multi-root projects")


def run_erc(sch: Path, rep: Report):
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "erc.json"
        subprocess.run([kinet.CLI, "sch", "erc", "--format", "json", "--severity-error", "--severity-warning",
                        "-o", str(out), str(sch)], capture_output=True)
        if not out.exists():
            rep.add("ERROR", "ERC-FAIL", "ERC did not run")
            return
        d = json.loads(out.read_text())
    by = defaultdict(list)
    for sheet in d.get("sheets", []):
        for v in sheet.get("violations", []):
            where = "; ".join(i["description"] for i in v.get("items", [])[:2])
            by[(v["severity"], v["type"])].append(where)
    for (sev, typ), where in sorted(by.items()):
        rep.add("ERROR" if sev == "error" else "WARN", f"ERC-{typ}", f"{len(where)}× {typ}: " + " | ".join(where[:3])
                + (" …" if len(where) > 3 else ""), "Inspect → Electrical Rules Checker in the schematic editor")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("target")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-erc", action="store_true")
    ap.add_argument("--strict", action="store_true", help="warnings also fail")
    a = ap.parse_args()
    sch = kinet.find_sch(a.target)
    comps, nets = kinet.load(sch)
    rep = Report()
    check_parts(comps, rep)
    check_nets(comps, nets, rep)
    check_decoupling(comps, nets, rep)
    check_text_vars(sch, rep)
    check_coverage(sch, comps, rep)
    if not a.no_erc:
        run_erc(sch, rep)
    if a.json:
        print(json.dumps({"schematic": str(sch), "parts": len(comps), "nets": len(nets), "items": rep.items}, indent=1))
    else:
        print(f"{sch.name}: {len(comps)} parts, {len(nets)} nets")
        for sev in ("ERROR", "WARN"):
            for i in rep.items:
                if i["severity"] == sev:
                    print(f"{sev:5} {i['code']:14} {i['message']}")
                    if i["fix"]:
                        print(f"{'':20} → {i['fix']}")
        print(f"{rep.count('ERROR')} errors, {rep.count('WARN')} warnings")
    return 1 if rep.count("ERROR") or (a.strict and rep.count("WARN")) else 0


if __name__ == "__main__":
    sys.exit(main())
