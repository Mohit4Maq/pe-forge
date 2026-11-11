"""Simulation results as data, the side panel's feed, and the HTML report. Standard library only.

  forge results check                 validate every *.result.json in the project
  forge panel --json                  everything the pe-forge side panel shows, in one call
  forge report [--open]               .forge/report/index.html: results, charts, checks, gates (offline, no scripts
                                      from the network)

A result file sits next to its record (<module>/Simulations/<id>.result.json or 06_Simulations/Results/...):
{
  "schema": "pe-forge-result-1",
  "id": "buck_corners", "title": "Buck at the input corners", "module": "M-01", "tool": "pwrlab buck",
  "date": "2026-11-07T14:02:00", "record": "modules/M-01_Power/Simulations/buck_corners.md",
  "grade": "ACCEPTED | PROVISIONAL | REJECTED | NOT RUN",
  "corners": ["36 V", "60 V"],
  "metrics": [{"name": "Efficiency at 1 A", "unit": "%", "values": [93.1, 91.4],
               "closed_form": [92.5, 90.9], "err_pct": 0.6, "grade": "ACCEPTED", "note": ""}],
  "charts": [{"type": "line", "title": "Efficiency vs load", "x": {"label": "Load", "unit": "A", "values": [...]},
              "y": {"label": "Efficiency", "unit": "%"}, "series": [{"name": "36 V", "values": [...]}]},
             {"type": "bars", "title": "Losses at 60 V, 1 A", "unit": "W",   # "total": false for comparisons
              "items": [{"name": "Q1 switching", "value": 0.42}]}],
  "notes": ["Soft-start is PROVISIONAL: no hand calculation; bench test added to the DVP&R"]
}
The grade is the simulation's own (SIM-QC): ACCEPTED only with a benchmark agreeing; values in a metric follow
`corners`. `forge spice check` writes these files itself; pwrlab results are written by the pe-simulate skill.
"""
import datetime as dt
import glob
import html
import json
import os
import re
import subprocess
import sys

SCHEMA = "pe-forge-result-1"
GRADES = ("ACCEPTED", "PROVISIONAL", "REJECTED", "NOT RUN")

# What every converter's simulation set must show before G5 (metric names matched by these patterns, across all
# result files). project.toml [sims] required = [...] replaces the list; [sims.waive] "name" = "reason (owner OK)".
REQUIRED = {
    "dc-dc": [("inductor current ripple", r"inductor.*ripple|ripple.*inductor|dI_?L|ΔI"),
              ("peak inductor current", r"peak.*current|current.*peak|I_?pk"),
              ("output voltage ripple", r"output.*ripple|ripple.*output|dV_?out|ΔV"),
              ("efficiency", r"efficien"),
              ("loop stability (phase / gain margin or crossover)", r"phase margin|gain margin|crossover|loop"),
              ("start-up / soft-start / inrush", r"start-?up|soft-?start|inrush"),
              ("output short / overload / current limit", r"short|overload|current limit|fault")],
    "inverter": [("THD", r"\bthd\b"), ("efficiency", r"efficien"), ("DC-link ripple", r"dc.?link.*ripple|bus.*ripple"),
                 ("device losses / junction temperature", r"loss|T_?j|junction"),
                 ("start-up / precharge", r"start-?up|precharge|inrush"), ("fault / short", r"short|fault|overload")],
    "pfc": [("THD / power factor", r"\bthd\b|power factor|\bpf\b"), ("efficiency", r"efficien"),
            ("inductor ripple", r"ripple"), ("start-up / inrush", r"start-?up|inrush"), ("fault / short", r"short|fault|overload")],
    "dc-dc isolated": [("magnetising / inductor current ripple", r"ripple|dI_?L|magneti"), ("primary / secondary peak current", r"peak.*current|I_?pk"),
                       ("transformer flux / Bmax", r"flux|B_?max|saturat"), ("switch voltage stress (leakage spike / clamp)", r"V_?ds|stress|spike|clamp"),
                       ("output voltage ripple", r"output.*ripple|ΔV"), ("efficiency", r"efficien"),
                       ("loop stability", r"phase margin|gain margin|crossover|loop"), ("start-up / soft-start", r"start-?up|soft-?start|inrush"),
                       ("output short / overload", r"short|overload|current limit|fault")],
    "ac-dc": [("THD / power factor", r"\bthd\b|power factor|\bpf\b"), ("inrush", r"inrush|start-?up"), ("hold-up", r"hold-?up"),
              ("bus / output ripple", r"ripple"), ("efficiency", r"efficien"), ("loop stability", r"phase margin|gain margin|crossover|loop"),
              ("output short / overload", r"short|overload|fault|current limit"), ("surge / line transient", r"surge|transient")],
    "motor": [("phase current ripple / THD", r"ripple|thd"), ("DC-link ripple", r"dc.?link|bus"), ("device losses / junction temperature", r"loss|T_?j|junction"),
              ("start / stall / locked rotor current", r"stall|locked|start"), ("braking / regeneration bus rise", r"brak|regen"),
              ("short / fault / desaturation", r"short|fault|desat")],
    "charger": [("charge current regulation", r"current.*regul|CC\b|constant current"), ("voltage regulation / CV", r"CV\b|constant voltage|regulat"),
                ("ripple", r"ripple"), ("efficiency", r"efficien"), ("loop stability", r"phase margin|gain margin|crossover|loop"),
                ("reverse / battery fault", r"reverse|fault|short"), ("start-up / pre-charge", r"start-?up|pre-?charge|inrush")],
    "other": [("efficiency", r"efficien"), ("start-up / inrush", r"start-?up|inrush"), ("fault / short / overload", r"short|fault|overload")],
}
TYPE_KEYS = [("dc-dc isolated", ("isolated", "flyback", "forward", "llc", "dab", "phase-shift")), ("ac-dc", ("ac-dc", "ac/dc", "psu", "pfc")),
             ("inverter", ("inverter", "dc-ac", "dc/ac", "three-level", "t-type", "npc", "grid")), ("pfc", ("pfc", "totem")),
             ("motor", ("motor", "drive", "bldc", "pmsm", "foc")), ("charger", ("charger", "bms", "battery")), ("dc-dc", ("dc-dc", "dc/dc", "buck", "boost", "converter"))]
PROVENANCE_TOOLS = ("pwrlab", "ltspice", "ngspice", "spice", "plecs", "simetrix", "psim")


# ------------------------------------------------------------------ result files
def provenance_problems(prov, root=None):
    """A simulated number is citable only when what produced it is named: pwrlab's provenance block
    (model_version, toolchain_hash, run_utc, evidence_eligible = true), or the netlist file with its hash."""
    if not isinstance(prov, dict):
        return ["provenance missing: the pwrlab payload's provenance block, or {tool, netlist, sha256}"]
    P = []
    if prov.get("netlist"):
        p = os.path.join(root, prov["netlist"]) if root and not os.path.isabs(prov["netlist"]) else prov["netlist"]
        if root is not None and not os.path.exists(p):
            P.append(f"provenance netlist {prov['netlist']} does not exist")
        elif root is not None and prov.get("sha256"):
            import hashlib
            h = hashlib.sha256(open(p, "rb").read()).hexdigest()
            if not h.startswith(str(prov["sha256"])[:16]):
                P.append(f"provenance netlist {prov['netlist']} has changed since the run (hash {h[:16]})")
        elif not prov.get("sha256"):
            P.append("provenance netlist without sha256")
        return P
    for k in ("model_version", "toolchain_hash", "run_utc"):
        if not prov.get(k):
            P.append(f"provenance {k} missing")
    if prov.get("evidence_eligible") is not True:
        P.append("provenance evidence_eligible is not true (a dirty or unversioned run cannot be cited)")
    return P


def required_metrics(prj):
    """[(name, regex)] this project's simulation set must cover, from [sims] required or the type's default."""
    sims = prj.cfg.get("sims", {})
    if sims.get("required"):
        return [(x, re.escape(x)) if isinstance(x, str) else tuple(x) for x in sims["required"]]
    typ = (prj.cfg.get("project", {}).get("type") or "").lower()
    specs = prj.cfg.get("specs", {})
    wide = (typ + " " + str(specs.get("isolation", "")) + " " + str(specs.get("topology", ""))).lower()
    # the declared type first (an isolated AC-DC is an AC-DC); the isolation / topology words only when it is vague
    key = next((k for k, words in TYPE_KEYS if any(w in typ for w in words)), None)
    if key == "dc-dc" and "iso" in wide:
        key = "dc-dc isolated"
    if key is None:
        key = next((k for k, words in TYPE_KEYS if any(w in wide for w in words)), "other")
    return REQUIRED[key]


def coverage(prj, docs=None):
    """(covered, missing, waived): which required metrics the project's results show (REJECTED / NOT RUN do not count)."""
    docs = load_results(prj) if docs is None else docs
    names = [m.get("name", "") for d in docs if d.get("grade") in ("ACCEPTED", "PROVISIONAL") for m in d.get("metrics") or []]
    notes = " ".join(str(n) for d in docs for n in d.get("notes") or [])
    waive = prj.cfg.get("sims", {}).get("waive", {})
    cov, miss, wv = [], [], []
    for name, rx in required_metrics(prj):
        if any(re.search(rx, n, re.I) for n in names):
            cov.append(name)
        elif any(re.search(re.escape(k), name, re.I) or re.search(rx, k, re.I) for k in waive):
            wv.append(f"{name} (waived: {next(v for k, v in waive.items() if re.search(re.escape(k), name, re.I) or re.search(rx, k, re.I))})")
        else:
            miss.append(name)
    return cov, miss, wv


def gate_items(prj):
    """G5: every result file valid and citable; the required metrics covered; the closed forms reproduce."""
    docs = load_results(prj)
    bad = [(d["file"], d["problems"]) for d in docs if d.get("problems")]
    out = [(not bad, f"simulation results: {len(docs)} file(s), {len(bad)} with problems"
            + "".join(f"; {f}: {p[0]}" for f, p in bad[:3]))]
    cov, miss, wv = coverage(prj, docs)
    out.append((not miss, f"required simulations ({len(cov)}/{len(cov) + len(miss) + len(wv)} shown"
                + (f", {len(wv)} waived" if wv else "") + ")" + (": missing " + ", ".join(miss) if miss else "")))
    from . import calc
    r = calc.as_check(prj, {})
    out.append((r["status"] != "fail", f"closed forms recomputed by pe-forge: {r['status']} ({r['reason']})"
                + "".join(f"; {f['actual']}" for f in r["findings"][:2] if r["status"] == "fail")))
    return out


def validate(d, root=None):
    """List of problems with one result document (empty = valid)."""
    P = []
    if not isinstance(d, dict):
        return ["not a JSON object"]
    if d.get("schema") != SCHEMA:
        P.append(f"schema must be {SCHEMA!r}")
    for k in ("id", "title", "tool", "grade"):
        if not isinstance(d.get(k), str) or not d.get(k):
            P.append(f"{k} missing")
    if d.get("grade") not in GRADES:
        P.append(f"grade must be one of {', '.join(GRADES)}")
    corners = d.get("corners") or []
    for i, m in enumerate(d.get("metrics") or []):
        if not isinstance(m, dict) or not m.get("name"):
            P.append(f"metrics[{i}] needs a name")
            continue
        vals = m.get("values")
        if not isinstance(vals, list) or not all(isinstance(v, (int, float)) or v is None for v in vals):
            P.append(f"metric {m['name']}: values must be a list of numbers")
        elif corners and len(vals) != len(corners):
            P.append(f"metric {m['name']}: {len(vals)} value(s) for {len(corners)} corner(s)")
        if m.get("grade") and m["grade"] not in GRADES:
            P.append(f"metric {m['name']}: grade {m['grade']!r}")
        cf = m.get("closed_form") or []
        if cf and isinstance(vals, list) and len(cf) == len(vals) and isinstance(m.get("err_pct"), (int, float)):
            errs = [abs(v - c) / abs(c) * 100 for v, c in zip(vals, cf)
                    if isinstance(v, (int, float)) and isinstance(c, (int, float)) and c]
            if errs and abs(max(errs) - m["err_pct"]) > 0.5:       # the stated error must be the computed one
                P.append(f"metric {m['name']}: err_pct {m['err_pct']} but values vs closed_form give {max(errs):.2f} %")
        if m.get("grade") == "ACCEPTED" and not cf and not m.get("anchor"):
            P.append(f"metric {m['name']}: ACCEPTED needs a closed_form or an \"anchor\" (hardware / datasheet value)")
    if d.get("grade") in ("ACCEPTED", "PROVISIONAL") and any(k in str(d.get("tool", "")).lower() for k in PROVENANCE_TOOLS) \
            and "forge spice check" not in str(d.get("tool", "")):
        P += provenance_problems(d.get("provenance"), root)
    for i, c in enumerate(d.get("charts") or []):
        t = (c or {}).get("type")
        if t == "line":
            xs = (c.get("x") or {}).get("values") or []
            for s in c.get("series") or []:
                if len(s.get("values") or []) != len(xs):
                    P.append(f"chart {c.get('title', i)}: series {s.get('name')} has {len(s.get('values') or [])} "
                             f"points for {len(xs)} x values")
        elif t == "bars":
            if not all(isinstance(it.get("value"), (int, float)) for it in c.get("items") or []):
                P.append(f"chart {c.get('title', i)}: every bar needs a numeric value")
        else:
            P.append(f"chart {i}: type must be line or bars")
    return P


def result_files(prj):
    pats = [os.path.join(prj.p("modules"), "*", "Simulations", "*.result.json"),
            os.path.join(prj.p("sim_results"), "*.result.json")]
    return sorted({f for p in pats for f in glob.glob(p)})


def load_results(prj):
    out = []
    from .spice import record_problem
    for f in result_files(prj):
        try:
            d = json.load(open(f))
        except (OSError, ValueError) as e:
            out.append({"id": os.path.basename(f), "title": os.path.basename(f), "grade": "REJECTED", "tool": "?",
                        "problems": [f"unreadable: {e}"], "file": prj.rel(f)})
            continue
        d = dict(d, file=prj.rel(f), problems=validate(d, prj.root))
        rec = d.get("record")
        if rec and os.path.exists(os.path.join(prj.root, rec)):
            why = record_problem(prj, os.path.join(prj.root, rec))
            if why and why.startswith("stale"):
                d["stale"] = why
        out.append(d)
    out.sort(key=lambda d: d.get("date") or "", reverse=True)
    return out


def write(prj, path, doc):
    """Write a result file after validating it (raises ValueError with the problems)."""
    doc = dict({"schema": SCHEMA, "date": dt.datetime.now().isoformat(timespec="seconds")}, **doc)
    problems = validate(doc)
    if problems:
        raise ValueError("; ".join(problems))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    json.dump(doc, open(tmp, "w"), indent=1)
    os.replace(tmp, path)
    return path


# ------------------------------------------------------------------ the panel feed
def _json(p, default=None):
    try:
        return json.load(open(p))
    except (OSError, ValueError):
        return default


def panel(prj):
    from . import state
    from .gates import qc_rows
    from .phases import PHASES
    st = state.load(prj)
    scope, pid, nxt = state.next_action(st)
    tools = prj.p("tools_data")
    reviews = {}
    for r in qc_rows(prj):
        reviews[(r.get("gate"), r.get("module", "system"))] = {k: r.get(k, "") for k in ("date", "verdict", "status", "tier")}
    phases = [{"id": p[0], "gate": p[1], "title": p[2], "status": st["phases"][p[0]]["status"],
               "date": st["phases"][p[0]].get("date", ""), "review": reviews.get((p[1], "system"))} for p in PHASES]
    modules = [{"id": m["id"], "name": m["name"], "status": m["status"], "depends": m["depends"],
                "phases": {k: {"status": v["status"], "date": v.get("date", ""),
                               "review": reviews.get((f"G{k[1:]}", m["id"]))} for k, v in m["phases"].items()}}
               for m in st["modules"]]
    led = _json(os.path.join(tools, "ledger.json"), {}) or {}
    results = load_results(prj)
    stamp = "|".join(f"{r.get('id')}@{r.get('date')}" for r in results[:20])
    full = _json(os.path.join(tools, "cache", "check_full.json"))
    parts = _json(os.path.join(tools, "cache", "parts.json"))
    try:
        from . import vcycle
        v = vcycle.build(prj)
    except Exception as e:      # the V view must never take the panel down
        v = {"error": f"{type(e).__name__}: {e}"}
    from . import ui
    try:
        logo = ui.panel_logo(prj.cfg.get("ui", {}))
    except Exception:
        logo = []
    return {"ok": True, "vcycle": v, "project": prj.name, "root": prj.root, "profile": prj.process.get("profile", "full"),
            "logo": logo, "accent": prj.cfg.get("ui", {}).get("accent", ""),
            "phase": pid, "scope": scope, "next": nxt, "phases": phases, "modules": modules,
            "results": results, "results_stamp": stamp,
            "check": full, "check_stamp": (full or {}).get("at", ""),
            "obligations": [o for o in led.get("open", []) if o.get("opened_by") != "stop"],
            "txn": _json(os.path.join(tools, "txn.json")), "parts": parts,
            "report": os.path.join(tools, "report", "index.html")}


# ------------------------------------------------------------------ the HTML report
COLORS = ["#4245f5", "#e8590c", "#3fb950", "#d29922", "#8c7bf6", "#5aa9ff"]
GRADE_CSS = {"ACCEPTED": "ok", "PROVISIONAL": "warn", "REJECTED": "err", "NOT RUN": "dim"}


def _svg_line(c, w=560, h=240):
    xs = [float(x) for x in (c.get("x") or {}).get("values") or []]
    series = [s for s in c.get("series") or [] if s.get("values")]
    if len(xs) < 2 or not series:
        return "<p class=dim>no data</p>"
    ys = [float(v) for s in series for v in s["values"] if v is not None]
    y0, y1 = min(ys), max(ys)
    if y1 == y0:
        y0, y1 = y0 - 1, y1 + 1
    pad = (y1 - y0) * 0.08
    y0, y1 = y0 - pad, y1 + pad
    L, R, T, B = 52, 12, 14, 34
    X = lambda x: L + (x - xs[0]) / (xs[-1] - xs[0]) * (w - L - R)
    Y = lambda y: T + (y1 - y) / (y1 - y0) * (h - T - B)
    out = [f'<svg viewBox="0 0 {w} {h}" class=chart role=img aria-label="{html.escape(c.get("title", ""))}">']
    for k in range(5):
        yv = y0 + (y1 - y0) * k / 4
        out.append(f'<line x1={L} x2={w - R} y1={Y(yv):.1f} y2={Y(yv):.1f} class=grid />'
                   f'<text x={L - 6} y={Y(yv) + 4:.1f} class=tick text-anchor=end>{yv:.3g}</text>')
    for k in range(5):
        xv = xs[0] + (xs[-1] - xs[0]) * k / 4
        out.append(f'<text x={X(xv):.1f} y={h - B + 16} class=tick text-anchor=middle>{xv:.3g}</text>')
    xl = c.get("x") or {}
    out.append(f'<text x={(L + w - R) / 2} y={h - 4} class=axis text-anchor=middle>'
               f'{html.escape(xl.get("label", ""))} {("(" + html.escape(xl.get("unit", "")) + ")") if xl.get("unit") else ""}</text>')
    for i, s in enumerate(series):
        col = COLORS[i % len(COLORS)]
        pts = [(X(x), Y(float(v)), x, v) for x, v in zip(xs, s["values"]) if v is not None]
        out.append(f'<polyline fill=none stroke="{col}" stroke-width=2 points="' +
                   " ".join(f"{a:.1f},{b:.1f}" for a, b, _, _ in pts) + '" />')
        for a, b, x, v in pts:
            out.append(f'<circle cx={a:.1f} cy={b:.1f} r=3 fill="{col}"><title>{html.escape(s.get("name", ""))}: '
                       f'{v:g} at {x:g}</title></circle>')
    out.append("</svg>")
    legend = "".join(f'<span><i style="background:{COLORS[i % len(COLORS)]}"></i>{html.escape(s.get("name", ""))}</span>'
                     for i, s in enumerate(series))
    return "".join(out) + f"<div class=legend>{legend}</div>"


def _svg_bars(c, w=560):
    items = c.get("items") or []
    if not items:
        return "<p class=dim>no data</p>"
    top = max(float(it["value"]) for it in items) or 1
    row, L = 26, 150
    h = row * len(items) + 8
    out = [f'<svg viewBox="0 0 {w} {h}" class=chart role=img aria-label="{html.escape(c.get("title", ""))}">']
    for i, it in enumerate(items):
        y = 4 + i * row
        bw = (w - L - 80) * float(it["value"]) / top
        out.append(f'<text x={L - 8} y={y + 16} class=tick text-anchor=end>{html.escape(it["name"])}</text>'
                   f'<rect x={L} y={y + 3} width={bw:.1f} height={row - 8} rx=3 fill="{COLORS[0]}"><title>'
                   f'{html.escape(it["name"])}: {float(it["value"]):g} {html.escape(c.get("unit", ""))}</title></rect>'
                   f'<text x={L + bw + 6:.1f} y={y + 16} class=tick>{float(it["value"]):g} {html.escape(c.get("unit", ""))}</text>')
    total = sum(float(it["value"]) for it in items)
    out.append("</svg>")
    if c.get("total") is False:          # comparisons (values against a limit) have no meaningful total
        return "".join(out)
    return "".join(out) + f'<div class=legend>total {total:.3g} {html.escape(c.get("unit", ""))}</div>'


def _metrics_table(r):
    corners = r.get("corners") or [""]
    head = "".join(f"<th>{html.escape(c)}</th>" for c in corners)
    rows = []
    for m in r.get("metrics") or []:
        vals = "".join(f"<td>{'' if v is None else f'{v:g}'}</td>" for v in (m.get("values") or []))
        cf = m.get("closed_form")
        cfs = ", ".join(f"{v:g}" for v in cf) if isinstance(cf, list) else ("" if cf is None else f"{cf:g}")
        err = m.get("err_pct")
        g = m.get("grade") or ""
        rows.append(f"<tr><td>{html.escape(m['name'])}</td>{vals}<td>{html.escape(m.get('unit', ''))}</td>"
                    f"<td>{cfs}</td><td>{'' if err is None else f'{err:+.1f} %'}</td>"
                    f"<td class={GRADE_CSS.get(g, '')}>{html.escape(g)}</td>"
                    f"<td class=dim>{html.escape(m.get('note', ''))}</td></tr>")
    return (f"<table><tr><th>Result</th>{head}<th>Unit</th><th>Hand calc</th><th>vs calc</th><th>Grade</th><th></th></tr>"
            + "".join(rows) + "</table>")


CSS = """
:root{--bg:#fff;--fg:#1d1d1f;--dim:#6b6b70;--line:#e5e5ea;--card:#fafafa;--accent:#4245f5;--ok:#1a7f37;--warn:#9a6700;--err:#cf222e}
@media (prefers-color-scheme:dark){:root{--bg:#111214;--fg:#e8e8ea;--dim:#9a9aa0;--line:#2a2b2f;--card:#18191c;--ok:#3fb950;--warn:#d29922;--err:#ff6e9c}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.45 -apple-system,system-ui,Segoe UI,sans-serif}
header{padding:18px 24px;border-bottom:1px solid var(--line);display:flex;gap:16px;align-items:baseline;flex-wrap:wrap}
header b.mark{color:var(--accent);font-style:italic;font-size:22px}header h1{font-size:20px;margin:0}
nav{display:flex;gap:4px;padding:0 24px;border-bottom:1px solid var(--line)}
nav button{background:none;border:0;padding:10px 14px;color:var(--dim);font:inherit;cursor:pointer;border-bottom:2px solid transparent}
nav button.on{color:var(--fg);border-color:var(--accent)}main{padding:20px 24px;max-width:1100px}
section{display:none}section.on{display:block}.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:14px 16px;margin:0 0 16px}
.card h3{margin:0 0 6px;font-size:16px}.meta{color:var(--dim);font-size:13px;margin-bottom:10px}
table{border-collapse:collapse;width:100%;font-size:14px;margin:8px 0}th,td{text-align:left;padding:5px 8px;border-bottom:1px solid var(--line)}
th{color:var(--dim);font-weight:500}.ok{color:var(--ok)}.warn{color:var(--warn)}.err{color:var(--err)}.dim{color:var(--dim)}
.badge{display:inline-block;padding:1px 8px;border-radius:10px;font-size:12px;border:1px solid currentColor}
.charts{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:16px}
svg.chart{width:100%;height:auto}svg .grid{stroke:var(--line)}svg .tick,svg .axis{fill:var(--dim);font-size:11px}
.legend{font-size:12px;color:var(--dim);display:flex;gap:12px;flex-wrap:wrap}.legend i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:4px}
select{font:inherit;padding:4px 8px;background:var(--card);color:var(--fg);border:1px solid var(--line);border-radius:6px}
@media (max-width:600px){header,nav,main{padding-left:16px;padding-right:16px}}
"""


def report(prj):
    data = panel(prj)
    esc = html.escape
    sims = []
    for i, r in enumerate(data["results"]):
        g = r.get("grade", "")
        charts = "".join(f'<div><h4>{esc(c.get("title", ""))}</h4>'
                         + (_svg_line(c) if c.get("type") == "line" else _svg_bars(c)) + "</div>"
                         for c in r.get("charts") or [])
        notes = "".join(f"<li>{esc(n)}</li>" for n in r.get("notes") or [])
        warn = "".join(f"<li class=err>{esc(p)}</li>" for p in r.get("problems") or []) + \
            (f"<li class=warn>{esc(r['stale'])}</li>" if r.get("stale") else "")
        sims.append(f'<div class="card sim" data-i={i}><h3>{esc(r.get("title", r.get("id", "")))} '
                    f'<span class="badge {GRADE_CSS.get(g, "")}">{esc(g)}</span></h3>'
                    f'<div class=meta>{esc(r.get("module") or "system")} · {esc(r.get("tool", ""))} · '
                    f'{esc((r.get("date") or "")[:16].replace("T", " "))} · {esc(r.get("record") or r.get("file", ""))}</div>'
                    + (f"<ul>{warn}</ul>" if warn else "") + _metrics_table(r)
                    + (f'<div class=charts>{charts}</div>' if charts else "") + (f"<ul>{notes}</ul>" if notes else "") + "</div>")
    picker = ""
    if len(data["results"]) > 1:
        picker = ("<p><select id=pick><option value=all>All results</option>" +
                  "".join(f"<option value={i}>{esc(r.get('title', r.get('id', '')))}</option>" for i, r in enumerate(data["results"]))
                  + "</select></p>")
    ck = data.get("check")
    checks = "<p class=dim>No full `forge check` yet.</p>"
    if ck:
        rows = []
        for n, c in ck["checks"].items():
            cls = {"pass": "ok", "warn": "warn", "fail": "err", "skip": "dim"}[c["status"]]
            first = "; ".join(f"{f.get('location', '')}: {f.get('claim', '')}, {f.get('actual', '')}" for f in c["findings"][:3])
            rows.append(f"<tr><td>{esc(n)}</td><td class={cls}>{c['status'].upper()}</td>"
                        f"<td class=dim>{esc(c.get('reason', ''))}</td><td>{esc(first)}</td></tr>")
        checks = (f"<p class=meta>forge check · {esc(ck.get('at', ''))} · "
                  f"<span class={'ok' if ck['ok'] else 'err'}>{'OK' if ck['ok'] else 'FAILED'}</span></p>"
                  "<table><tr><th>Check</th><th>Status</th><th>Scope</th><th>Findings</th></tr>" + "".join(rows) + "</table>")
    ob = "".join(f"<li>{esc(o['kind'])}:{esc(o['target'])} <span class=dim>({esc(o.get('opened_by', ''))})</span></li>"
                 for o in data["obligations"])
    gate_rows = []
    for p in data["phases"]:
        rv = p.get("review") or {}
        gate_rows.append(f"<tr><td>{p['id']}</td><td>{esc(p['title'])}</td><td>{esc(p['status'])}</td><td>{esc(p.get('date', ''))}</td>"
                         f"<td>{esc(rv.get('verdict', ''))} {esc(rv.get('status', ''))}</td></tr>")
    mod_rows = "".join(f"<tr><td>{esc(m['id'])}</td><td>{esc(m['name'])}</td><td>{esc(m['status'])}</td><td>"
                       + " ".join(f"{k}:{v['status']}" for k, v in m["phases"].items()) + "</td></tr>" for m in data["modules"])
    parts = data.get("parts") or {}
    fam = "".join(f"<tr><td>{esc(g['family'])}</td><td>{g['count']}</td><td>{esc(', '.join(g['refs']))}</td>"
                  f"<td class=dim>{esc(g['need'])}</td></tr>" for g in parts.get("groups", []))
    body = f"""<header><b class=mark>maq</b><h1>{esc(data['project'])}</h1>
<span class=dim>{esc(data['phase'])} · next: {esc(data['next'])} · report {dt.datetime.now():%Y-%m-%d %H:%M}</span></header>
<nav><button data-t=sims class=on>Simulations ({len(data['results'])})</button><button data-t=checks>Checks</button>
<button data-t=gates>Gates</button><button data-t=parts>Parts</button></nav>
<main><section id=sims class=on>{picker}{''.join(sims) or '<p class=dim>No simulation results yet.</p>'}</section>
<section id=checks>{checks}{('<h3>Open re-checks</h3><ul>' + ob + '</ul>') if ob else ''}</section>
<section id=gates><table><tr><th>Phase</th><th>Title</th><th>Status</th><th>Passed</th><th>Review</th></tr>{''.join(gate_rows)}</table>
{('<h3>Modules</h3><table><tr><th>Module</th><th>Name</th><th>Status</th><th>Phases</th></tr>' + mod_rows + '</table>') if mod_rows else ''}</section>
<section id=parts>{('<table><tr><th>Family</th><th>Parts</th><th>Refs</th><th>Needs</th></tr>' + fam + '</table>') if fam
                   else '<p class=dim>No datasheet work list yet (forge evidence todo).</p>'}</section></main>
<script>
document.querySelectorAll('nav button').forEach(b=>b.onclick=()=>{{document.querySelectorAll('nav button,section').forEach(x=>x.classList.remove('on'));b.classList.add('on');document.getElementById(b.dataset.t).classList.add('on')}});
const pick=document.getElementById('pick');if(pick)pick.onchange=()=>document.querySelectorAll('.sim').forEach(s=>s.style.display=(pick.value==='all'||s.dataset.i===pick.value)?'':'none');
</script>"""
    page = (f"<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>{esc(data['project'])} results</title><style>{CSS}</style></head><body>{body}</body></html>")
    out = data["report"]
    os.makedirs(os.path.dirname(out), exist_ok=True)
    open(out, "w").write(page)
    return out


def open_file(path):
    cmd = ["open", path] if sys.platform == "darwin" else ["xdg-open", path]
    try:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except OSError:
        return False


# ------------------------------------------------------------------ commands
def main_results(prj, argv):
    if argv[:1] != ["check"]:
        print(__doc__)
        return 1
    bad = 0
    for f in result_files(prj):
        try:
            P = validate(json.load(open(f)), prj.root)
        except (OSError, ValueError) as e:
            P = [str(e)]
        print(("FAIL " if P else "OK   ") + prj.rel(f) + ("".join(f"\n     {p}" for p in P)))
        bad += bool(P)
    cov, miss, wv = coverage(prj)
    print(f"{len(result_files(prj))} result file(s), {bad} with problems; required simulations shown: {', '.join(cov) or 'none'}"
          + (f"; MISSING: {', '.join(miss)}" if miss else "") + (f"; waived: {', '.join(wv)}" if wv else ""))
    return 1 if bad or miss else 0


def main_panel(prj, argv):
    print(json.dumps(panel(prj), default=str))
    return 0


def main_report(prj, argv):
    out = report(prj)
    print(out)
    if "--open" in argv:
        open_file(out)
    return 0
