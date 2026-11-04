"""Turn results into something a person (or a reviewer) can act on.

A report must carry the EVIDENCE, not just the number: what closed-form model
the result was judged against, which checks ran, and how much margin each had.
A table of numbers with no provenance is what this whole project exists to avoid.
"""
from __future__ import annotations

import datetime as _dt
import html
from pathlib import Path

import numpy as np
import pandas as pd

from .topologies.base import fmt_eng


_ERRLEN = len("_err_pct")


def _md_table(df, floatfmt="{:.6g}") -> str:
    """Markdown table without pandas.to_markdown, which needs `tabulate`.

    Adding an optional dependency for one table is not worth it, and the import
    error only surfaces on the reporting path -- long after a sweep has run.
    """
    cols = list(df.columns)
    def cell(v):
        if isinstance(v, float):
            return floatfmt.format(v)
        return "" if v is None else str(v)
    rows = [[cell(v) for v in rec] for rec in df.itertuples(index=False)]
    w = [max(len(str(c)), *(len(r[i]) for r in rows)) if rows else len(str(c))
         for i, c in enumerate(cols)]
    head = "| " + " | ".join(str(c).ljust(w[i]) for i, c in enumerate(cols)) + " |"
    sep = "|" + "|".join("-" * (x + 2) for x in w) + "|"
    body = ["| " + " | ".join(r[i].ljust(w[i]) for i in range(len(cols))) + " |"
            for r in rows]
    return "\n".join([head, sep] + body)


def _fmt(v):
    if v is None:
        return "n/a"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, (int, np.integer)):
        return str(v)
    if isinstance(v, float):
        return f"{v:.6g}"
    return str(v)


def result_markdown(sr, title=None) -> str:
    L = [f"# {title or sr.topology.name} result", ""]
    L += [f"*generated {_dt.datetime.now():%Y-%m-%d %H:%M}*", ""]
    L += ["## Design", "", "```", sr.topology.summary(), "```", ""]
    notes = sr.topology.design_notes()
    if notes:
        L += [f"- {n}" for n in notes] + [""]

    L += ["## Simulated vs closed form", "",
          "| quantity | simulated | closed form | error | tolerance |",
          "|---|---|---|---|---|"]
    for k in sorted(set(sr.extracted) | set(sr.analytical)):
        s, a = sr.extracted.get(k), sr.analytical.get(k)
        if isinstance(s, (int, float)) and isinstance(a, (int, float)) and a:
            L.append(f"| `{k}` | {s:.6g} | {a:.6g} | {(s-a)/a*100:+.4f} % | "
                     f"{sr.topology.tol_for(k):.3g} % |")
        elif s is not None:
            L.append(f"| `{k}` | {_fmt(s)} | — | — | — |")
    L.append("")

    L += ["## Checks", "",
          "| check | verdict | value | limit | detail |", "|---|---|---|---|---|"]
    for c in sr.checks:
        verdict = "pass" if c.passed else f"**{c.severity}**"
        L.append(f"| {c.name} | {verdict} | "
                 f"{'' if c.value is None else f'{c.value:.4g}'} | "
                 f"{'' if c.limit is None else f'{c.limit:.4g}'} | {c.detail} |")
    L += ["", f"**{sr.checks.summary()}**", ""]
    return "\n".join(L)


def sweep_markdown(df: pd.DataFrame, title="Sweep", notes=None) -> str:
    L = [f"# {title}", "", f"*generated {_dt.datetime.now():%Y-%m-%d %H:%M}*", ""]
    if notes:
        L += [notes, ""]
    n_bad = int((~df["ok"]).sum()) if "ok" in df else 0
    L += [f"- {len(df)} operating points, **{len(df)-n_bad} usable**, {n_bad} rejected"]
    errs = [c for c in df.columns if c.endswith("_err_pct")]
    for c in errs:
        L.append(f"- worst `{c[:-_ERRLEN]}` error vs closed form: "
                 f"**{df[c].abs().max():.4f} %**")
    L += ["", _md_table(df), ""]
    return "\n".join(L)


def html_report(sr_list, path, title="pwrlab report", images=None):
    """Standalone HTML with the full evidence trail, no external assets."""
    if not isinstance(sr_list, (list, tuple)):
        sr_list = [sr_list]
    css = """
    body{font:14px/1.55 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
         margin:0;padding:2rem;max-width:1100px;background:#fbfbfc;color:#16171a}
    h1{font-size:1.5rem;margin:.2rem 0 1rem} h2{font-size:1.1rem;margin:1.6rem 0 .5rem}
    table{border-collapse:collapse;width:100%;margin:.5rem 0 1rem;font-size:13px}
    th,td{border:1px solid #e3e5e8;padding:5px 9px;text-align:left}
    th{background:#f2f3f5;font-weight:600}
    .pass{color:#127a3d;font-weight:600}.ERROR{color:#c0281c;font-weight:700}
    .WARN{color:#a8690b;font-weight:600}.INFO{color:#555}
    code{background:#f0f1f3;padding:1px 4px;border-radius:3px}
    .ok{background:#e8f6ec;border-left:4px solid #127a3d;padding:.6rem .9rem;margin:.6rem 0}
    .bad{background:#fdeceb;border-left:4px solid #c0281c;padding:.6rem .9rem;margin:.6rem 0}
    img{max-width:100%;border:1px solid #e3e5e8;border-radius:4px;margin:.6rem 0}
    .mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px}
    """
    P = [f"<!doctype html><meta charset='utf-8'><title>{html.escape(title)}</title>",
         f"<style>{css}</style>", f"<h1>{html.escape(title)}</h1>",
         f"<p class='mono'>generated {_dt.datetime.now():%Y-%m-%d %H:%M}</p>"]
    for sr in sr_list:
        P.append(f"<h2>{html.escape(sr.topology.summary())}</h2>")
        cls = "ok" if sr.ok else "bad"
        P.append(f"<div class='{cls}'>{html.escape(sr.checks.summary())}</div>")
        if sr.topology.design_notes():
            P.append("<ul>" + "".join(
                f"<li class='mono'>{html.escape(n)}</li>"
                for n in sr.topology.design_notes()) + "</ul>")
        P.append("<table><tr><th>quantity</th><th>simulated</th><th>closed form</th>"
                 "<th>error</th><th>tol</th></tr>")
        for k in sorted(set(sr.extracted) | set(sr.analytical)):
            s, a = sr.extracted.get(k), sr.analytical.get(k)
            if isinstance(s, (int, float)) and isinstance(a, (int, float)) and a:
                P.append(f"<tr><td><code>{k}</code></td><td>{s:.6g}</td>"
                         f"<td>{a:.6g}</td><td>{(s-a)/a*100:+.4f} %</td>"
                         f"<td>{sr.topology.tol_for(k):.3g} %</td></tr>")
            elif s is not None:
                P.append(f"<tr><td><code>{k}</code></td><td>{_fmt(s)}</td>"
                         "<td>—</td><td>—</td><td>—</td></tr>")
        P.append("</table>")
        P.append("<table><tr><th>check</th><th>verdict</th><th>value</th>"
                 "<th>limit</th><th>detail</th></tr>")
        for c in sr.checks:
            v = "pass" if c.passed else c.severity
            P.append(f"<tr><td>{html.escape(c.name)}</td>"
                     f"<td class='{v}'>{v}</td>"
                     f"<td>{'' if c.value is None else f'{c.value:.4g}'}</td>"
                     f"<td>{'' if c.limit is None else f'{c.limit:.4g}'}</td>"
                     f"<td>{html.escape(c.detail)}</td></tr>")
        P.append("</table>")
    for img in (images or []):
        P.append(f"<img src='{html.escape(str(Path(img).name))}'>")
    Path(path).write_text("\n".join(P))
    return Path(path)
