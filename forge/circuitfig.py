"""forge fig: the booklet's circuit figures are drawn with schemdraw, from a script kept beside the booklet.

  forge fig new <name> [--booklet PATH]      figures/<name>.py from the template, beside the booklet (system or module)
  forge fig render [<name>|--all] [--booklet PATH]
                                             run the script(s) -> figures/<name>.svg (+ .png); records the source hash
  forge fig check [--json]                   every booklet embeds >= 1 schemdraw figure (a .svg/.png in figures/ with its
                                             .py source that imports schemdraw, rendered from the current source);
                                             a pasted image, a hand sketch or ASCII art is not a circuit figure

Why: a booklet is read by the owner, the reviewer and the drafter; its circuit must be seen, not inferred from a
table, and the figure must be regenerable (the script is the source, the picture is an output). G6 checks it.
"""
import glob
import hashlib
import json
import os
import re
import subprocess
import sys

from . import config

INDEX = ".fig_index.json"
FIG_REF = re.compile(r"!\[[^\]]*\]\(([^)\s]+)\)|<img[^>]+src=\"([^\"]+)\"", re.I)


def booklets(prj):
    out = sorted(glob.glob(prj.p("booklets", "*.md")))
    out += sorted(glob.glob(os.path.join(prj.p("modules"), "*", "Design_Booklet.md")))
    return [b for b in out if "/_superseded" not in b and os.path.basename(b).startswith(("T_", "_")) is False]


def fig_dir(booklet):
    return os.path.join(os.path.dirname(booklet), "figures")


def _sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()[:16]


def _index(d):
    try:
        return json.load(open(os.path.join(d, INDEX)))
    except (OSError, ValueError):
        return {}


def new(prj, name, booklet):
    d = fig_dir(booklet)
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, f"{name}.py")
    if os.path.exists(p):
        return p, False
    tpl = os.path.join(config.FORGE_ROOT, "template", "90_Templates", "T_Circuit_Figure.py")
    t = open(tpl).read().replace("{{NAME}}", name)
    open(p, "w").write(t)
    return p, True


def render(prj, booklet, names=None):
    """Run figures/<name>.py under this Python with pe-forge importable; return [(name, ok, message)]."""
    d = fig_dir(booklet)
    scripts = sorted(glob.glob(os.path.join(d, "*.py")))
    if names:
        scripts = [s for s in scripts if os.path.splitext(os.path.basename(s))[0] in names]
    idx = _index(d)
    out = []
    env = dict(os.environ, PYTHONPATH=config.FORGE_ROOT + os.pathsep + os.environ.get("PYTHONPATH", ""), MPLBACKEND="Agg")
    for s in scripts:
        name = os.path.splitext(os.path.basename(s))[0]
        r = subprocess.run([sys.executable, s], cwd=d, capture_output=True, text=True, env=env, timeout=300)
        svg = os.path.join(d, name + ".svg")
        ok = r.returncode == 0 and os.path.exists(svg)
        if ok:
            idx[name] = {"src_sha": _sha(s), "svg_sha": _sha(svg), "png": os.path.exists(os.path.join(d, name + ".png"))}
        out.append((name, ok, "rendered " + prj.rel(svg) if ok else (r.stderr or r.stdout).strip()[-400:] or "no svg written"))
    if scripts:
        json.dump(idx, open(os.path.join(d, INDEX), "w"), indent=1)
    return out


def status(prj, booklet):
    """(ok, problems, figures) for one booklet: the figures it embeds and whether each is a current schemdraw one."""
    t = open(booklet, errors="ignore").read()
    bdir = os.path.dirname(booklet)
    refs = [a or b for a, b in FIG_REF.findall(t)]
    probs, figs = [], []
    if not refs:
        return False, ["no figure embedded (forge fig new <name>; render; then ![..](figures/<name>.svg) in section 2)"], []
    idx = _index(fig_dir(booklet))
    for ref in refs:
        if ref.startswith(("http:", "https:")):
            probs.append(f"{ref}: a linked image is not a circuit figure (draw it with schemdraw)")
            continue
        p = os.path.normpath(os.path.join(bdir, ref))
        name, ext = os.path.splitext(os.path.basename(p))
        if ext.lower() not in (".svg", ".png"):
            continue                       # a photo or a plot in another format is not judged here
        if os.path.dirname(p) != os.path.normpath(fig_dir(booklet)):
            probs.append(f"{ref}: not in figures/ beside the booklet (pasted image?): draw it with schemdraw")
            continue
        src = os.path.join(fig_dir(booklet), name + ".py")
        if not os.path.exists(p):
            probs.append(f"{ref}: file missing (forge fig render {name})")
            continue
        if not os.path.exists(src):
            probs.append(f"{ref}: no {name}.py source beside it: not a schemdraw figure")
            continue
        if "schemdraw" not in open(src, errors="ignore").read():
            probs.append(f"{name}.py does not use schemdraw")
            continue
        rec = idx.get(name)
        if not rec or rec.get("src_sha") != _sha(src):
            probs.append(f"{ref}: stale, {name}.py changed since it was rendered (forge fig render {name})")
            continue
        figs.append(name)
    if not figs and not probs:
        probs.append("no schemdraw figure embedded (forge fig new <name>)")
    return not probs and bool(figs), probs, figs


def as_check(prj, opts):
    from .check import finding, result
    F, n = [], 0
    for b in booklets(prj):
        n += 1
        ok, probs, figs = status(prj, b)
        for p in probs:
            F.append(finding("booklet circuit figures are drawn with schemdraw and current", p, prj.rel(b), "forge fig new | render"))
    if not n:
        return result("skip", reason="no design booklet yet")
    return result("fail" if F else "pass", F, reason=f"{n} booklet(s)")


def gate_items(prj, mod=None):
    bs = [os.path.join(prj.root, mod["dir"], "Design_Booklet.md")] if mod else booklets(prj)
    bs = [b for b in bs if os.path.exists(b)]
    if not bs:
        return []
    bad = []
    for b in bs:
        ok, probs, _ = status(prj, b)
        if not ok:
            bad.append(f"{prj.rel(b)}: {probs[0]}")
    return [(not bad, f"booklet circuit figures (schemdraw): {len(bs) - len(bad)}/{len(bs)} booklet(s)" + ("; " + "; ".join(bad[:2]) if bad else ""))]


def _booklet_arg(prj, argv):
    if "--booklet" in argv:
        p = argv[argv.index("--booklet") + 1]
        return p if os.path.isabs(p) else os.path.join(prj.root, p)
    bs = booklets(prj)
    if len(bs) == 1:
        return bs[0]
    if not bs:
        d = prj.p("booklets")
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, "Design_Booklet.md")      # figures/ goes beside the booklet to come
    raise SystemExit("several booklets: say which with --booklet <path>\n  " + "\n  ".join(prj.rel(b) for b in bs))


def main(prj, argv):
    if argv[:1] == ["new"] and len(argv) > 1:
        p, made = new(prj, re.sub(r"[^A-Za-z0-9_]", "_", argv[1]), _booklet_arg(prj, argv))
        print(f"{'written' if made else 'exists'}: {prj.rel(p)}  (edit, then forge fig render {argv[1]}; embed ![..](figures/{argv[1]}.svg))")
        return 0
    if argv[:1] == ["render"]:
        names = [a for a in argv[1:] if not a.startswith("--") and a != _booklet_arg(prj, argv)] or None
        rc = 0
        for name, ok, msg in render(prj, _booklet_arg(prj, argv), names):
            print(("OK   " if ok else "FAIL ") + f"{name}: {msg}")
            rc |= 0 if ok else 1
        return rc
    if argv[:1] == ["check"]:
        rows = [(prj.rel(b),) + status(prj, b) for b in booklets(prj)]
        if "--json" in argv:
            print(json.dumps([{"booklet": b, "ok": ok, "problems": pr, "figures": f} for b, ok, pr, f in rows], indent=1))
        else:
            for b, ok, pr, f in rows:
                print(("OK   " if ok else "FAIL ") + f"{b}: " + (", ".join(f) if ok else pr[0]))
            if not rows:
                print("no design booklet yet")
        return 0 if all(r[1] for r in rows) else 1
    print(__doc__)
    return 1
