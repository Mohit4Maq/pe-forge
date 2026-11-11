"""forge level: how firmly pe-forge enforces its process on this project.

  forge level                       show the level and what it means
  forge level light|standard|strict set it (project.toml [process] level)

  light     help first. Every check still runs and is reported, as advice: nothing blocks the end of a turn,
            change requests only warn. A board is never released in light mode (G8 refuses). `forge adopt`
            starts here, so a customer gets quick answers on their own board before any process.
  standard  the default for new projects: open obligations (ERC, DRC, drift, BoM ...) block the end of a turn,
            gates as written; G8 needs the requirements and V-model in place (P2 passed, not skipped).
  strict    standard, plus: `forge check` fails on warnings, gates take no --override.

Raising the level is always allowed. Lowering it weakens the process, so only the owner can, at a terminal
(the same rule as `forge cr approve`): Claude cannot lower it on its own.
"""
import os
import re

from . import config, state

MEANING = {
    "light": "advice only: checks run and are reported, nothing blocks; no release (G8) in this level",
    "standard": "obligations block the end of a turn; gates as written; release needs requirements (P2 passed)",
    "strict": "standard + warnings fail `forge check`, gates take no override",
}
ORDER = {lv: i for i, lv in enumerate(config.LEVELS)}


def set_level(prj, new):
    """Rewrite [process] level in project.toml, keeping every other line as it is."""
    p = os.path.join(prj.root, "project.toml")
    t = open(p).read()
    m = re.search(r"(?ms)^\[process\][^\n]*\n(.*?)(?=^\[|\Z)", t)
    if not m:
        t = t.rstrip("\n") + f'\n\n[process]\nlevel = "{new}"\n'
    else:
        body = m.group(1)
        if re.search(r'(?m)^level\s*=', body):
            body2 = re.sub(r'(?m)^level\s*=.*$', f'level = "{new}"', body, count=1)
        else:
            body2 = f'level = "{new}"\n' + body
        t = t[:m.start(1)] + body2 + t[m.end(1):]
    open(p, "w").write(t)


def what_changes(prj, new):
    """Plain lines: what raising to `new` will enforce now, and what is still missing for a release."""
    L = []
    if new in ("standard", "strict"):
        L.append("  open obligations (ERC, DRC, drift, BoM, consistency) now block the end of a turn")
        try:
            st = state.load(prj)
            skipped = [p for p, v in st["phases"].items() if v["status"] == "skipped"]
            p2 = st["phases"]["P2"]["status"]
        except Exception:
            skipped, p2 = [], "unknown"
        if p2 != "passed":
            L.append(f"  release (G8) needs the requirements and V-model: P2 is {p2}. Draft them from the design: "
                     "forge req infer, confirm with the owner, then pass G2")
        if skipped:
            L.append("  phases skipped at adoption (done outside pe-forge): " + ", ".join(skipped)
                     + "; their gates are still open to pass for real")
    if new == "strict":
        L.append("  forge check fails on warnings; gates take no --override")
    return L


def main(prj, argv):
    cur = prj.level
    if not argv:
        print(f"process level: {cur} ({MEANING[cur]})")
        others = [lv for lv in config.LEVELS if lv != cur]
        print("  change: forge level " + " | ".join(others) + "   (lowering needs the owner at a terminal)")
        return 0
    new = argv[0]
    if new not in config.LEVELS:
        raise SystemExit(f"level must be one of {', '.join(config.LEVELS)}")
    if new == cur:
        print(f"process level is already {cur}")
        return 0
    if ORDER[new] < ORDER[cur] and not config.interactive():
        raise SystemExit(f"lowering the process level ({cur} -> {new}) is the owner's decision: run "
                         f"`forge level {new}` yourself in a terminal")
    set_level(prj, new)
    st = state.load(prj)
    state.log(st, f"process level {cur} -> {new}")
    state.save(prj, st)
    print(f"process level: {cur} -> {new} ({MEANING[new]})")
    for line in what_changes(config.Project(prj.root), new):
        print(line)
    return 0
