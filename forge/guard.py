"""The write guard: pe-forge never overwrites a file it did not write.

Every file pe-forge generates goes through guard.write(). It records the file's content hash in
.forge/generated.json. On the next write:

  - the file does not exist                         -> written, recorded
  - it exists and still has the hash pe-forge wrote -> pe-forge's own, unchanged: overwritten, recorded
  - anything else (the user's file, or a generated
    file the user has since edited)                 -> left alone; the new content goes to a proposed copy beside
                                                       it (WIP_BOM.forge-proposed.csv) and the caller is told
  - replace=True (an explicit --replace by a person)-> the existing file is first copied to
                                                       .forge/backup/<time>/<path>, then overwritten

  forge guard status [--json]     generated files: ours and unchanged / edited since / missing; proposed copies
  forge guard accept <path>       take a proposed copy over the original (the original goes to .forge/backup/)

Outside a pe-forge project (no project.toml) guard.write is a plain write.
"""
import hashlib
import json
import os
import shutil
import time

STATE = "generated.json"
TAG = ".forge-proposed"


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _bytes(data):
    return data.encode("utf-8") if isinstance(data, str) else data


def _state_path(root):
    return os.path.join(root, ".forge", STATE)


def _load(root):
    try:
        return json.load(open(_state_path(root)))
    except (OSError, ValueError):
        return {}


def _save(root, d):
    os.makedirs(os.path.dirname(_state_path(root)), exist_ok=True)
    tmp = _state_path(root) + ".tmp"
    json.dump(d, open(tmp, "w"), indent=1, sort_keys=True)
    os.replace(tmp, _state_path(root))


def _rel(root, path):
    return os.path.relpath(os.path.abspath(path), os.path.abspath(root)).replace(os.sep, "/")


def proposed_path(path):
    """WIP_BOM.csv -> WIP_BOM.forge-proposed.csv (keeps the extension, so it opens in the same app)."""
    base, ext = os.path.splitext(path)
    return base + TAG + ext


def owned(root, path):
    """True when pe-forge wrote this file and nobody changed it since."""
    if not os.path.exists(path):
        return False
    h = _load(root).get(_rel(root, path))
    return bool(h) and h == _sha(open(path, "rb").read())


def record(root, path):
    """Mark an existing file as pe-forge's (after writing it by other means, e.g. a tool pe-forge ran)."""
    if root and os.path.exists(path):
        d = _load(root)
        d[_rel(root, path)] = _sha(open(path, "rb").read())
        _save(root, d)


def backup(root, path):
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dst = os.path.join(root, ".forge", "backup", stamp, _rel(root, path))
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(path, dst)
    return dst


def write(root, path, data, replace=False, out=None):
    """Write `data` (str or bytes) to `path` under the guard. Returns (status, written_path):
    'written' (new file), 'updated' (pe-forge's own file), 'replaced' (replace=True, original backed up),
    'unchanged' (same content already there), 'proposed' (someone else's file: data written beside it)."""
    raw = _bytes(data)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    if not root:                                    # outside a project: plain write
        open(path, "wb").write(raw)
        return "written", path
    if os.path.exists(path):
        cur = open(path, "rb").read()
        if cur == raw:
            record(root, path)
            return "unchanged", path
        if not owned(root, path):
            if replace:
                b = backup(root, path)
                open(path, "wb").write(raw)
                record(root, path)
                if out:
                    out(f"replaced {_rel(root, path)} (the previous version is in {_rel(root, b)})")
                return "replaced", path
            p = proposed_path(path)
            open(p, "wb").write(raw)
            if out:
                out(f"kept your {_rel(root, path)}: it was not written by pe-forge (or was edited since). "
                    f"The new version is in {_rel(root, p)}; review it, then `forge guard accept {_rel(root, path)}` "
                    "or merge by hand.")
            return "proposed", p
        open(path, "wb").write(raw)
        record(root, path)
        return "updated", path
    open(path, "wb").write(raw)
    record(root, path)
    return "written", path


def status(root):
    d = _load(root)
    rows = []
    for rel, h in sorted(d.items()):
        p = os.path.join(root, rel)
        if not os.path.exists(p):
            st = "missing"
        elif _sha(open(p, "rb").read()) == h:
            st = "ours"
        else:
            st = "edited"
        rows.append({"path": rel, "state": st, "proposed": os.path.exists(proposed_path(p))})
    extra = []
    for dp, dn, fn in os.walk(root):
        dn[:] = [x for x in dn if x not in (".git", ".forge", "node_modules")]
        for f in fn:
            if TAG in f:
                extra.append(_rel(root, os.path.join(dp, f)))
    return rows, sorted(extra)


def accept(root, rel):
    p = os.path.join(root, rel)
    prop = proposed_path(p)
    if not os.path.exists(prop):
        raise SystemExit(f"no proposed copy for {rel} ({_rel(root, prop)} does not exist)")
    b = backup(root, p) if os.path.exists(p) else None
    os.replace(prop, p)
    record(root, p)
    return b


def main(prj, argv):
    if argv[:1] == ["accept"] and len(argv) > 1:
        b = accept(prj.root, argv[1])
        print(f"{argv[1]}: the proposed version is now the file" + (f"; your previous version is in {_rel(prj.root, b)}" if b else ""))
        return 0
    rows, extra = status(prj.root)
    if "--json" in argv:
        print(json.dumps({"generated": rows, "proposed": extra}, indent=1))
        return 0
    for r in rows:
        print(f"  {r['state']:8s} {r['path']}" + ("   (a proposed copy is waiting)" if r["proposed"] else ""))
    if extra:
        print("proposed copies waiting for you (review, then forge guard accept <path>, or merge by hand):")
        for e in extra:
            print("  " + e)
    if not rows and not extra:
        print("no generated files recorded yet")
    return 0
