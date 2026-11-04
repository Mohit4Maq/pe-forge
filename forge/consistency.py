"""Propagation check (Rule 5): list every live mention of a superseded part, value or decision.

  .forge/superseded_terms.csv   term,superseded_by,since,note   (append a row whenever something is superseded)
  .forge/consistency_allow.csv  path,term,reason                (intended mentions: history, rationale)
Not flagged: _superseded*/ folders, .claude/, docs with frontmatter `status: superseded`, "## Revision history"
sections, callouts / sections whose heading says superseded / history, and lines that mark the term as replaced.
"""
import csv
import json
import os
import re
import sys

OK = re.compile(r"supersed|fallback|withdrawn|replac|retired|instead of|rejected|removed|not used|history|archiv|"
                r"→|->|\bwas\b", re.I)
SKIP_DIRS = {".git", ".claude", ".obsidian", ".forge", "node_modules"}


def _csv(path):
    return list(csv.DictReader(open(path, newline=""))) if os.path.exists(path) else []


def scan(prj):
    terms = _csv(prj.p("tools_data", "superseded_terms.csv"))
    if not terms:
        return []
    allow = {(r["path"], r["term"].lower()) for r in _csv(prj.p("tools_data", "consistency_allow.csv"))}
    frozen = prj.paths["reviews"]
    hits = []
    for dp, dn, fn in os.walk(prj.root):
        dn[:] = [d for d in dn if d not in SKIP_DIRS and not d.startswith("_superseded")]
        for f in fn:
            if not f.endswith(".md"):
                continue
            p = os.path.join(dp, f)
            rel = os.path.relpath(p, prj.root)
            if rel.startswith(frozen) or "/Reviews/" in rel:
                continue
            lines = open(p, errors="ignore").read().split("\n")
            if lines and lines[0] == "---" and "---" in lines[1:]:
                end = lines.index("---", 1)
                if any(re.match(r"status:\s*superseded", l, re.I) for l in lines[1:end]):
                    continue
            sect_ok = block_ok = False
            for i, line in enumerate(lines, 1):
                if line.startswith("#"):
                    sect_ok = bool(OK.search(line)) or line.lower().startswith("## revision history")
                if line.startswith(">"):
                    if i == 1 or not lines[i - 2].startswith(">"):
                        block_ok = bool(OK.search(line))
                else:
                    block_ok = False
                if sect_ok or block_ok or OK.search(line):
                    continue
                low = line.lower()
                for t in terms:
                    k = t["term"].lower()
                    if k and k in low and (rel, k) not in allow:
                        hits.append((rel, i, t["term"], t["superseded_by"], line.strip()[:120]))
    return hits


def main(prj, argv):
    hits = scan(prj)
    msg = "\n".join(f"{r}:{i}  [{t} -> {by}]  {ln}" for r, i, t, by, ln in hits)
    if "--hook" in argv:
        try:
            active = json.load(sys.stdin).get("stop_hook_active", False)
        except Exception:
            active = False
        if not hits:
            return 0
        if active:
            print(json.dumps({"systemMessage": "pe-forge consistency check still failing:\n" + msg[:1500]}))
            return 0
        print(f"Rule 5: {len(hits)} live mention(s) of superseded parts / decisions remain. Update or remove them "
              "(or mark them as history, or add them to .forge/consistency_allow.csv with a reason):\n" + msg[:4000],
              file=sys.stderr)
        return 2
    print(msg + ("\n\n" if hits else "") + f"{len(hits)} live mention(s) of superseded terms"
          + ("" if hits else ": project consistent"))
    return 1 if hits else 0
