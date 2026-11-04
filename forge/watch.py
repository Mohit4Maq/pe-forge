"""Detect the user's manual edits between Claude turns (Rule 6).

Snapshots the working tree (untracked included, .gitignore respected) as a git tree object through a private
index in .git/; nothing is committed and the real index is untouched.
  --mark                 snapshot now (Stop hook)
  --report [--event E]   diff against the last snapshot, print it as hook context, re-snapshot
  --show                 print the report without moving the snapshot
"""
import json
import os
import subprocess

TEXT = (".md", ".csv", ".py", ".txt", ".json", ".toml", ".yaml", ".yml", ".asc", ".cir")
NOISE_EXT = (".raw", ".log", ".net", ".db", ".plt", ".rpt", ".lck")
NOISE = ("/runs/", "/work/", "token_ledger.csv", "/.forge/state.json")
MAXDIFF, MAXNAMES = 1500, 20


class Watch:
    def __init__(self, prj):
        self.prj, self.root = prj, prj.root
        gd = subprocess.run(["git", "-C", self.root, "rev-parse", "--absolute-git-dir"], capture_output=True,
                            text=True).stdout.strip()
        self.ok = bool(gd)
        self.idx, self.st = os.path.join(gd, "forge_watch.idx"), os.path.join(gd, "forge_watch.json")
        self.gd = gd

    def git(self, *a, env=None):
        return subprocess.run(["git", "-C", self.root, *a], capture_output=True, text=True, env=env).stdout

    def snapshot(self):
        env = dict(os.environ, GIT_INDEX_FILE=self.idx)
        if not os.path.exists(self.idx) and os.path.exists(os.path.join(self.gd, "index")):
            open(self.idx, "wb").write(open(os.path.join(self.gd, "index"), "rb").read())
        self.git("add", "-A", ".", env=env)
        return self.git("write-tree", env=env).strip()

    def load(self):
        try:
            return json.load(open(self.st))
        except Exception:
            return {}

    def save(self, tree):
        json.dump({"tree": tree, "head": self.git("rev-parse", "HEAD").strip()}, open(self.st, "w"))

    def report(self):
        st, new = self.load(), self.snapshot()
        old = st.get("tree") or self.git("rev-parse", "HEAD^{tree}").strip()
        if not old or old == new:
            return new, ""
        noise = lambda f: any(k in "/" + f for k in NOISE) or f.lower().endswith(NOISE_EXT)
        names = [l.split("\t") for l in self.git("diff", "--name-status", "-M", old, new).splitlines() if l.strip()]
        nz = [n for n in names if noise(n[-1])]
        names = [n for n in names if not noise(n[-1])]
        names = [n for n in names if n[0] != "M" or self.git("diff", "--ignore-cr-at-eol", "--stat", old, new, "--", n[-1]).strip()]
        if not names:
            return new, ""
        out = [f"{len(names)} file(s) changed outside Claude since the last turn"
               + (f" ({len(nz)} tool artefacts not listed)" if nz else "") + ":"]
        out += [f"  {n[0]:<4} {' -> '.join(n[1:])}" for n in names[:MAXNAMES]]
        if len(names) > MAXNAMES:
            out.append(f"  ... +{len(names) - MAXNAMES} more (forge watch --show)")
        txt = [n[-1] for n in names if n[-1].lower().endswith(TEXT)]
        if txt:
            d = self.git("diff", "-M", "--ignore-cr-at-eol", "--unified=1", old, new, "--", *txt)
            out += ["", "Diff of text files:", d[:MAXDIFF] + ("\n... (truncated: read the files)" if len(d) > MAXDIFF else "")]
        bins = [n[-1] for n in names if not n[-1].lower().endswith(TEXT)]
        if bins:
            out += ["", "Binary / CAD files changed (open them to see what changed): " + ", ".join(bins)]
        return new, "\n".join(out)


def main(prj, argv):
    w = Watch(prj)
    if not w.ok:
        return 0
    if "--mark" in argv:
        w.save(w.snapshot())
        return 0
    ev = argv[argv.index("--event") + 1] if "--event" in argv else "UserPromptSubmit"
    new, text = w.report()
    if "--show" in argv:
        print(text or "no changes since last snapshot")
        return 0
    w.save(new)
    if text:
        ctx = ("[pe-forge change watch, Rule 6] Files changed since your last turn (the user, or a background agent). "
               "User edits are design intent: understand them, propagate them (Rule 5), ask if unclear, mention them "
               "briefly, never revert them.\n\n" + text)
        print(json.dumps({"hookSpecificOutput": {"hookEventName": ev, "additionalContext": ctx}}))
    return 0
