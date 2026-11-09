"""Transactional design edits with lossless rollback (git based). Standard library only.

  forge txn begin "<what>" [--allow-dirty]   start; refuses on a dirty tree unless --allow-dirty
  forge txn status                           the open transaction and the files it touched so far
  forge txn verify                           forge check on what the transaction touched (does not close)
  forge txn commit [-m MSG]                  needs a passing verify of the current tree; commits the touched files
                                             with a `Forge-Txn: <id>` trailer and closes
  forge txn close                            needs a passing verify; closes without committing (changes stay on disk)
  forge txn rollback [--reason TEXT]         save the failed work as a stash entry, then restore the tree exactly

What a rollback restores: HEAD, plus (with --allow-dirty) the tracked changes (a `git stash create` object) and the
untracked files (a tree object built through a private index; `git stash create` alone cannot see them, and the
clean step would delete them). The failed work is kept as `stash@{n}: forge failed txn <id>: <what>`.
Only changes made inside the transaction are undone: your edits from before `begin` come back byte for byte.
Each transaction leaves .forge/runs/<id>/summary.md (gitignored), which survives the rollback.
"""
import datetime as dt
import json
import os
import shutil
import subprocess
import tempfile

KEEP = [".forge/runs", ".forge/cache", ".forge/txn.json"]     # never removed by a rollback's clean step


class TxnError(SystemExit):
    pass


class Git:
    def __init__(self, root):
        self.root = root
        gd = self.run("rev-parse", "--absolute-git-dir").stdout.strip()
        if not gd:
            raise TxnError("not a git repository: transactions need git (git init, then a first commit)")
        self.gd = gd

    def run(self, *a, env=None, input=None):
        return subprocess.run(["git", "-C", self.root, *a], capture_output=True, text=True,
                              env=dict(os.environ, **(env or {})), input=input)

    def out(self, *a, **k):
        r = self.run(*a, **k)
        if r.returncode:
            raise TxnError(f"git {' '.join(a)} failed: {(r.stderr or r.stdout).strip()[:300]}")
        return r.stdout.strip()

    def scratch_index(self, seed=True):
        fd, p = tempfile.mkstemp(prefix="forge_txn_idx_", dir=self.gd)
        os.close(fd)
        os.remove(p)
        if seed and os.path.exists(os.path.join(self.gd, "index")):
            shutil.copy(os.path.join(self.gd, "index"), p)
        return p

    def tree_of_worktree(self):
        """Tree object of the whole working tree (tracked + untracked, .gitignore respected). Real index untouched."""
        idx = self.scratch_index()
        try:
            env = {"GIT_INDEX_FILE": idx}
            self.stage_all(env)
            return self.out("write-tree", env=env)
        finally:
            os.path.exists(idx) and os.remove(idx)

    def stage_all(self, env=None):
        """`git add -A`, then unstage the paths a rollback keeps. (An exclude pathspec would make git refuse
        when those paths are gitignored; stage-then-unstage works whether they are ignored or tracked.)"""
        self.out("add", "-A", env=env)
        self.run("rm", "-r", "-q", "--cached", "--ignore-unmatch", "--", *KEEP, env=env)

    def dirty(self):
        return [l for l in self.run("status", "--porcelain", "--untracked-files=all").stdout.splitlines()
                if l.strip() and not any(l[3:].startswith(k) for k in KEEP)]

    # Snapshots keep the exact bytes: no .gitattributes eol rules (attributes read from the empty tree) and no
    # autocrlf, or a CRLF file would come back as LF on Windows.
    RAW = {"GIT_ATTR_SOURCE": "4b825dc642cb6eb9a060e54bf8d69288fbee4904", "GIT_CONFIG_COUNT": "1",
           "GIT_CONFIG_KEY_0": "core.autocrlf", "GIT_CONFIG_VALUE_0": "false"}

    def untracked_tree(self):
        paths = [p for p in self.run("ls-files", "--others", "--exclude-standard", "-z").stdout.split("\0")
                 if p and not any(p.startswith(k) for k in KEEP)]
        usable = []
        for p in paths:
            full = os.path.join(self.root, p)
            if not os.path.lexists(full):
                continue
            if not os.access(full, os.R_OK):
                raise TxnError(f"cannot read untracked file {p}: a rollback would delete it with nothing to restore "
                               "it from. Make it readable (chmod +r), delete it, add it to .gitignore, or commit first.")
            usable.append(p)
        if not usable:
            return None
        idx = self.scratch_index(seed=False)
        try:
            env = {"GIT_INDEX_FILE": idx, **self.RAW}
            self.out("update-index", "-z", "--add", "--stdin", env=env, input="\0".join(usable) + "\0")
            return self.out("write-tree", env=env)
        finally:
            os.path.exists(idx) and os.remove(idx)

    def checkout_tree(self, tree):
        idx = self.scratch_index(seed=False)
        try:
            env = {"GIT_INDEX_FILE": idx, **self.RAW}
            self.out("read-tree", tree, env=env)
            self.out("checkout-index", "-a", "-f", env=env)
        finally:
            os.path.exists(idx) and os.remove(idx)


def _path(prj):
    return prj.p("tools_data", "txn.json")


def load(prj):
    try:
        return json.load(open(_path(prj)))
    except (OSError, ValueError):
        return None


def _save(prj, t):
    os.makedirs(os.path.dirname(_path(prj)), exist_ok=True)
    json.dump(t, open(_path(prj), "w"), indent=1)


def _now():
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _rundir(prj, tid):
    d = prj.p("tools_data", "runs", tid)
    os.makedirs(d, exist_ok=True)
    return d


def _summary(prj, t, outcome=None):
    lines = [f"# Transaction {t['id']}", "", f"- what: {t['what']}", f"- started: {t['started']}",
             f"- head: {t['head']}", f"- dirty at start: {'yes' if t.get('stash') or t.get('untracked_tree') else 'no'}"]
    if t.get("ended"):
        lines.append(f"- ended: {t['ended']}")
    lines += ["", "## Files touched", ""] + [f"- {f}" for f in t.get("touched", [])] + ["", "## Verify runs", ""]
    for v in t.get("verifies", []):
        lines.append(f"- {v['at']}: {'PASS' if v['ok'] else 'FAIL'} ({', '.join(f'{k} {s}' for k, s in v['status'].items())})")
    if outcome:
        lines += ["", "## Outcome", "", outcome]
    open(os.path.join(_rundir(prj, t["id"]), "summary.md"), "w").write("\n".join(lines) + "\n")


def touched(prj, t, g=None):
    g = g or Git(prj.root)
    now = g.tree_of_worktree()
    names = g.out("diff", "--name-only", "--no-renames", t["begin_tree"], now).splitlines()
    return now, [n for n in names if n]


def begin(prj, what, allow_dirty=False):
    g = Git(prj.root)
    if load(prj):
        raise TxnError(f"a transaction is already open ({load(prj)['id']}): commit, close or roll it back first")
    head = g.run("rev-parse", "--verify", "HEAD").stdout.strip()
    if not head:
        raise TxnError("the repository has no commit yet: make a first commit, then begin")
    dirty = g.dirty()
    if dirty and not allow_dirty:
        raise TxnError("the working tree has uncommitted changes:\n" + "\n".join("  " + d for d in dirty[:20])
                       + "\nCommit them first, or pass --allow-dirty (they are snapshotted and restored on rollback).")
    stash = g.out("stash", "create") or None if dirty else None
    untracked = g.untracked_tree() if dirty else None
    tid = "T" + dt.datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + os.urandom(2).hex()
    t = {"id": tid, "what": what, "started": _now(), "head": head, "stash": stash, "untracked_tree": untracked,
         "begin_tree": g.tree_of_worktree(), "touched": [], "verifies": []}
    _save(prj, t)
    _summary(prj, t)
    return t


def need(prj):
    t = load(prj)
    if not t:
        raise TxnError("no open transaction (forge txn begin \"<what>\")")
    return t


SCOPE = [((".kicad_sch",), "erc"), ((".kicad_pcb",), "drc"),
         ((".kicad_sch", "BOM.csv", "WIP_BOM.csv", "Sheet_Brief.md", "Design_Booklet.md"), "drift")]


def verify(prj, quiet=False):
    from . import check
    t = need(prj)
    tree, files = touched(prj, t)
    only = ["consistency"] + [c for exts, c in SCOPE if any(f.endswith(exts) for f in files)]
    only = list(dict.fromkeys(only))
    res = check.run(prj, only)
    from . import changes
    bad = changes.violations(prj, files)
    if bad:
        res["ok"] = False
        res["checks"]["change_control"] = check.result("fail", [check.finding(
            "an approved CR covers this baselined file", why + " and no approved CR lists it", rel) for rel, why in bad])
    t["touched"] = files
    t["verifies"].append({"at": _now(), "tree": tree, "ok": res["ok"],
                          "status": {k: v["status"] for k, v in res["checks"].items()}})
    _save(prj, t)
    _summary(prj, t)
    if not quiet:
        print(check.render(res))
    return res["ok"], res


def _verified(prj, t):
    tree, files = touched(prj, t)
    last = t["verifies"][-1] if t.get("verifies") else None
    if not last or not last["ok"]:
        raise TxnError("no passing `forge txn verify` yet: run it (and fix what fails) first")
    if last["tree"] != tree:
        raise TxnError("the tree changed after the last passing verify: run `forge txn verify` again")
    return files


def finish(prj, outcome):
    """Close the open transaction with an outcome line, no checks (for tools that verified by their own means)."""
    _finish(prj, need(prj), outcome)


def _finish(prj, t, outcome):
    t["ended"] = _now()
    _summary(prj, t, outcome)
    os.path.exists(_path(prj)) and os.remove(_path(prj))
    from . import watch
    try:
        watch.main(prj, ["--mark"])      # the result is ours, not a user edit
    except Exception:
        pass


def commit(prj, msg=None):
    t = need(prj)
    files = _verified(prj, t)
    g = Git(prj.root)
    if files:
        g.out("add", "-A", "--", *files)
        r = g.run("commit", "-q", "-m", f"{msg or t['what']}\n\nForge-Txn: {t['id']}", "--", *files)
        if r.returncode:
            raise TxnError("git commit failed (the transaction stays open):\n" + (r.stdout + r.stderr).strip()[-1500:])
    sha = g.out("rev-parse", "HEAD")
    _finish(prj, t, f"committed {sha[:10]}: {len(files)} file(s)")
    return sha, files


def close(prj):
    t = need(prj)
    files = _verified(prj, t)
    _finish(prj, t, f"verified and closed without a commit: {len(files)} file(s) left on disk")
    return files


def rollback(prj, reason=""):
    t = need(prj)
    g = Git(prj.root)
    now, files = touched(prj, t, g)
    t["touched"] = files
    saved = None
    if files:      # keep the failed work: everything staged, stash object stored under a findable name
        g.stage_all()
        sha = g.run("stash", "create").stdout.strip()
        if sha:
            g.run("stash", "store", "-m", f"forge failed txn {t['id']}: {t['what']}", sha)
            saved = sha
    keep = tempfile.mkdtemp(prefix="forge-txn-keep-")
    try:
        for k in KEEP:
            src = os.path.join(prj.root, k)
            if os.path.isdir(src):
                shutil.copytree(src, os.path.join(keep, k))
            elif os.path.exists(src):
                os.makedirs(os.path.dirname(os.path.join(keep, k)), exist_ok=True)
                shutil.copy2(src, os.path.join(keep, k))
        g.out("reset", "-q", "--hard", t["head"])
        g.out("clean", "-fdq", *sum((["-e", k] for k in KEEP), []))
        warn = []
        if t.get("stash"):
            r = g.run("stash", "apply", "--index", t["stash"])
            if r.returncode:
                g.run("reset", "-q", "--hard", t["head"])
                r = g.run("stash", "apply", t["stash"])
                if r.returncode:
                    warn.append("could not re-apply your earlier tracked changes: " + r.stderr.strip()[:200]
                                + f" (they are in stash object {t['stash']})")
        if t.get("untracked_tree"):
            try:
                g.checkout_tree(t["untracked_tree"])
            except TxnError as e:
                warn.append(f"could not restore earlier untracked files ({e}); tree object {t['untracked_tree']}")
    finally:
        for k in KEEP:
            src = os.path.join(keep, k)
            dst = os.path.join(prj.root, k)
            if os.path.isdir(src):
                shutil.copytree(src, dst, dirs_exist_ok=True)
            elif os.path.exists(src):
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst)
        shutil.rmtree(keep, True)
    stash_ref = None
    if saved:
        for line in g.run("stash", "list", "--format=%gd %H").stdout.splitlines():
            ref, h = line.split()
            if h == saved:
                stash_ref = ref
    outcome = (f"rolled back{': ' + reason if reason else ''}. {len(files)} file(s) restored."
               + (f" Failed work kept as {stash_ref} ({saved[:10]})." if saved else "")
               + ("".join("\nWARNING: " + w for w in warn)))
    _finish(prj, t, outcome)
    return stash_ref, files, warn, os.path.join(prj.paths["tools_data"], "runs", t["id"])


def main(prj, argv):
    if not argv or argv[0] not in ("begin", "status", "verify", "commit", "close", "rollback"):
        print(__doc__)
        return 1
    opt = lambda k: argv[argv.index(k) + 1] if k in argv else None
    try:
        if argv[0] == "begin":
            what = next((a for a in argv[1:] if not a.startswith("--")), None)
            if not what:
                print('forge txn begin "<what>" [--allow-dirty]')
                return 1
            t = begin(prj, what, "--allow-dirty" in argv)
            print(f"transaction {t['id']} open: {what}"
                  + (" (your uncommitted work is snapshotted)" if t["stash"] or t["untracked_tree"] else ""))
            return 0
        if argv[0] == "status":
            t = load(prj)
            if not t:
                print("no open transaction")
                return 0
            _, files = touched(prj, t)
            last = t["verifies"][-1] if t["verifies"] else None
            print(f"{t['id']}: {t['what']} (since {t['started']})\n  touched: {', '.join(files) or 'nothing yet'}\n"
                  f"  last verify: {('PASS' if last['ok'] else 'FAIL') + ' at ' + last['at'] if last else 'none'}")
            return 0
        if argv[0] == "verify":
            ok, _ = verify(prj)
            return 0 if ok else 1
        if argv[0] == "commit":
            sha, files = commit(prj, opt("-m"))
            print(f"committed {sha[:10]} ({len(files)} file(s)); transaction closed")
            return 0
        if argv[0] == "close":
            files = close(prj)
            print(f"verified; transaction closed, {len(files)} file(s) left uncommitted on disk")
            return 0
        ref, files, warn, run = rollback(prj, opt("--reason") or "")
        print(f"rolled back {len(files)} file(s)" + (f"; failed work kept as {ref}" if ref else "")
              + f"\nrun record: {run}/summary.md" + "".join("\nWARNING: " + w for w in warn))
        return 0
    except TxnError as e:
        print(str(e))
        return 1
