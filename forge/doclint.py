"""forge docs lint: are the project's documents readable by a person (the owner, a reviewer, a customer)?

  forge docs lint [<file.md> ...] [--json]      every deliverable document (or the ones named), findings + score

Deterministic rules (template/90_Templates/Doc_Style.md says how to write; this measures it):
  D1 opening       the first content block after the title is a summary of <= 90 words (not a wall of sources)
  D2 paragraphs    no paragraph over 150 words (one idea per paragraph)
  D3 tables        no table row over 140 characters and no table over 7 columns (split it, or move detail to an appendix)
  D4 figures       a document that scans, sweeps or compares corners shows at least one figure (plot or circuit)
  D5 source keys   inline citation keys ("R5 p.6", "TT §4", "DC §8.1") at most 2 per 100 words: cite from a sources
                   table by number once per table or paragraph
  D6 budget        word budget per document type (booklet 2000, calculations 4000, trade 3000, result 3000, brief 1500,
                   other 5000; a `full` profile doubles it): derivations go to the script and an appendix
  D7 code prose    a paragraph that is mostly `inline code` (a netlist written as text) is drawn instead (forge fig)
  D8 sections      no section over 600 words without a subheading
Score 0 to 100 per document (advice; `forge check doc_style` warns, a strict project fails on warnings).
"""
import glob
import json
import os
import re

BUDGET = {"booklet": 2000, "calculations": 4000, "trade": 3000, "result": 3000, "brief": 1500, "other": 5000}
WEIGHT = {"D1": 10, "D2": 6, "D3": 4, "D4": 15, "D5": 8, "D6": 12, "D7": 10, "D8": 5}
SKIP_DIRS = ("90_Templates", "Registry", "V_Model/items", "/QC/", "/_superseded", "/.forge/", "/09_Release/", "/Datasheets/", "/Literature/")
KEY = re.compile(r"\b[A-Z][A-Za-z]?[A-Z0-9]{0,2}(?:-[a-z0-9])?\s?(?:p\.\s?\d|§\s?\d|rev\s?\d)", re.U)
SWEEP = re.compile(r"\bsweep|\bscan|\bcorner|vs\.? (?:temperature|voltage|load)|from \d[\d.]* (?:to|\.\.) \d", re.I)


def kind(path):
    b = os.path.basename(path).lower()
    if "booklet" in b:
        return "booklet"
    if "calculation" in b:
        return "calculations"
    if "trade" in b:
        return "trade"
    if "brief" in b:
        return "brief"
    if "/results/" in path.lower().replace(os.sep, "/") or b.startswith("sim-") or "result" in b:
        return "result"
    return "other"


def documents(prj):
    out = []
    for f in glob.glob(os.path.join(prj.root, "**", "*.md"), recursive=True):
        rel = prj.rel(f).replace(os.sep, "/")
        if rel.startswith((".", "modules/_")) or any(s.strip("/") in rel for s in SKIP_DIRS):
            continue
        if os.path.basename(f) in ("CLAUDE.md", "README.md", "Master_Plan.md", "Build_Board.md", "QC_Register.md", "Document_Control.md"):
            continue
        if len(open(f, errors="ignore").read().split()) < 300:      # short notes are not the problem
            continue
        out.append(f)
    return sorted(out)


def blocks(text):
    """Content blocks after the front matter: (kind, text) with kind heading | table | para | list | code."""
    t = re.sub(r"\A---\n.*?\n---\n", "", text, flags=re.S)
    out, buf, kind_, fence = [], [], None, False
    for line in t.splitlines() + [""]:
        s = line.rstrip()
        if s.startswith("```"):
            fence = not fence
            kind_ = "code"
            buf.append(s)
            continue
        if fence:
            buf.append(s)
            continue
        k = ("heading" if s.startswith("#") else "table" if s.startswith("|") else "list" if re.match(r"\s*([-*]|\d+\.)\s", s)
             else "code" if kind_ == "code" and s else "para" if s else None)
        if k is None or (kind_ and k != kind_) or k == "heading":
            if buf:
                out.append((kind_, "\n".join(buf)))
            buf = []
            kind_ = None
        if k:
            buf.append(s)
            kind_ = k
            if k == "heading":
                out.append(("heading", s))
                buf, kind_ = [], None
    return out


def lint(path, profile="lite"):
    text = open(path, errors="ignore").read()
    words = len(text.split())
    bl = blocks(text)
    F = []
    # D1 opening
    first = next(((k, b) for k, b in bl if k in ("para", "table", "list")), None)
    if first and len(first[1].split()) > 90:
        F.append(("D1", f"the document opens with a {len(first[1].split())}-word {first[0]}: start with a <= 90-word summary (what, key numbers, status)"))
    # D2 paragraphs, D7 code prose
    for k, b in bl:
        if k != "para":
            continue
        n = len(b.split())
        if n > 150:
            F.append(("D2", f"a {n}-word paragraph ('{b[:50]}…'): one idea per paragraph, <= 150 words"))
        code = sum(len(m) for m in re.findall(r"`[^`]+`", b))
        if n >= 25 and code > 0.3 * len(b):
            F.append(("D7", f"a paragraph that is mostly inline code ('{b[:50]}…'): draw the circuit (forge fig new) and describe it in words"))
    # D3 tables
    for k, b in bl:
        if k != "table":
            continue
        rows = b.splitlines()
        if re.match(r"\|\s*Rev\b", rows[0]):          # the revision history is a log, not a table a reader scans
            continue
        wide = [r for r in rows if len(r) > 140]
        cols = max(len(r.strip().strip("|").split("|")) for r in rows)
        if wide:
            F.append(("D3", f"table '{rows[0][:40]}…': {len(wide)} row(s) over 140 characters: shorter cells, detail to an appendix or a note"))
        if cols > 7:
            F.append(("D3", f"table '{rows[0][:40]}…': {cols} columns: split it"))
    # D4 figures
    if not re.search(r"!\[|<img", text) and (SWEEP.search(text) or kind(path) in ("booklet", "calculations", "trade", "result")):
        F.append(("D4", "no figure: a circuit (forge fig new) or a plot of what the document scans or compares"))
    # D5 source keys
    keys = len(KEY.findall(text))
    if words and keys / words * 100 > 2:
        F.append(("D5", f"{keys} inline source keys in {words} words: a sources table once, then cite by number per table or paragraph"))
    # D6 budget
    budget = BUDGET[kind(path)] * (2 if profile == "full" else 1)
    if words > budget:
        F.append(("D6", f"{words} words against a {budget}-word budget for a {kind(path)}: derivations to the script and an appendix, history to the revision table"))
    # D8 sections without a subheading
    sec, name = 0, ""
    for k, b in bl + [("heading", "# end")]:
        if k == "heading":
            if sec > 600 and "revision" not in name.lower():
                F.append(("D8", f"section '{name[:40]}' runs {sec} words without a subheading"))
            sec, name = 0, b.lstrip("# ")
        else:
            sec += len(b.split())
    score = max(0, 100 - sum(WEIGHT[c] for c, _ in F))
    return {"file": path, "kind": kind(path), "words": words, "score": score, "findings": [f"{c}: {m}" for c, m in F]}


def as_check(prj, opts):
    from .check import finding, result
    docs = documents(prj)
    if not docs:
        return result("skip", reason="no document over 300 words yet")
    profile = prj.process.get("profile", "full")
    F, scores = [], []
    for d in docs:
        r = lint(d, profile)
        scores.append(f"{os.path.basename(d)} {r['score']}")
        F += [finding("readable document (Doc_Style.md)", x, prj.rel(d), "forge docs lint") for x in r["findings"]]
    return result("warn" if F else "pass", F, reason="; ".join(scores[:6]) + (" …" if len(scores) > 6 else ""))


def main(prj, argv):
    if argv[:1] != ["lint"]:
        print(__doc__)
        return 1
    files = [a for a in argv[1:] if not a.startswith("--")]
    files = [f if os.path.isabs(f) else os.path.join(prj.root, f) for f in files] or documents(prj)
    profile = prj.process.get("profile", "full")
    rows = [lint(f, profile) for f in files]
    if "--json" in argv:
        print(json.dumps(rows, indent=1))
    else:
        for r in rows:
            print(f"{r['score']:3d}/100  {prj.rel(r['file'])}  ({r['kind']}, {r['words']} words)")
            for f in r["findings"]:
                print(f"         {f}")
        if not rows:
            print("no document over 300 words yet")
    return 0 if all(r["score"] >= 70 for r in rows) else 1
