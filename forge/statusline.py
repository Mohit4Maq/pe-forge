"""forge statusline: the pe-forge bar at the bottom of Claude Code (layout: docs/ui-layout.md).

  forge statusline                            read Claude Code's JSON on stdin, print the bar (Claude Code runs this)
  forge statusline install [--project | --user] [--uninstall]
        --project (default): <project>/.claude/settings.local.json (this machine only; the command holds the path
        to this pe-forge install). --user: ~/.claude/settings.json, chaining to your previous status line outside
        pe-forge projects. Merges, never overwrites other keys; keeps a .bak; uninstall restores what was there.

Contract: under 100 ms; no subprocess inside a pe-forge project (git state is read from .git directly), no network,
no kicad-cli, no transcript scan; only small JSON files are read. Never a traceback in the bar: on any error the
minimal line is printed and the error goes to .forge/cache/statusline.log.
Options in project.toml [ui]: accent, color, glyphs = "ascii", statusline_lines = 1 | 2, statusline_width.
"""
import glob
import json
import os
import re
import subprocess
import sys
import time
import traceback

from . import ui

SHORT = {"P0": "Intake", "P1": "Foundation", "P2": "Requirements", "P3": "Architecture", "P4": "BoM",
         "P5": "Simulation", "P6": "Schematic", "P7": "PCB", "P8": "Release"}
GATE = {f"P{i}": f"G{i}" for i in range(9)}


def prev_file():
    """Where a replaced status line is remembered ($PE_FORGE_CONFIG_DIR or ~/.config/pe-forge)."""
    return os.path.join(os.environ.get("PE_FORGE_CONFIG_DIR") or os.path.expanduser("~/.config/pe-forge"),
                        "statusline_prev.json")


def _json(p, default=None):
    try:
        with open(p) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def find_root(*dirs):
    for d in dirs:
        d = os.path.abspath(d) if d else None
        while d:
            if os.path.isfile(os.path.join(d, "project.toml")):
                return d
            parent = os.path.dirname(d)
            d = None if parent == d else parent
    return None


def git_info(root, tools):
    gd = os.path.join(root, ".git")
    if os.path.isfile(gd):                       # a worktree: ".git" is a file pointing at the real dir
        m = re.match(r"gitdir: (.+)", open(gd).read().strip())
        gd = os.path.join(root, m.group(1)) if m else gd
    try:
        head = open(os.path.join(gd, "HEAD")).read().strip()
    except OSError:
        return None, False
    branch = head[16:] if head.startswith("ref: refs/heads/") else head[:8]
    w = _json(os.path.join(gd, "forge_watch.json"), {})
    dirty = bool(w.get("tree") and w.get("head_tree") and w["tree"] != w["head_tree"])
    return branch, dirty


def segments(root, data, T, cfg):
    """[(priority, text)] for the bar, plus the line-2 text."""
    from . import config, state
    prj = config.Project(root)
    tools = os.path.join(root, prj.paths["tools_data"])
    st = state.load(prj)
    scope, pid, nxt = state.next_action(st)
    S = [(1, ui.brand(T) + T.paint("bright", prj.name))]
    mod = None if scope == "system" else state.module(st, scope)
    if pid in SHORT:
        S.append((2, T.paint("bright", f"{pid} {SHORT[pid]}")))
    if mod:
        S.append((3, T.paint("bright", f"{mod['id']} {mod['name'][:18]}")))
    if pid in GATE:
        s = (mod["phases"].get(pid, {}) if mod else st["phases"].get(pid, {})).get("status", "todo")
        g = GATE[pid]
        S.append((4, T.paint("ok", f"{g} {T.g('ok')}") if s == "passed" else
                  T.paint("warn", f"{g} {T.g('review')} review") if s == "in-review" else T.paint("dim", f"{g} open")))
    led = _json(os.path.join(tools, "ledger.json"), {})
    opn = [o for o in led.get("open", []) if o.get("opened_by") != "stop"]
    if opn:
        kinds = list(dict.fromkeys(o["kind"] for o in opn))
        S.append((5, T.paint("warn", f"{T.g('warn')} {len(opn)} open: {', '.join(kinds[:3])}")))
    tx = _json(os.path.join(tools, "txn.json"))
    if tx:
        S.append((6, T.paint("warn", f"txn: {tx.get('what', '')[:16]}")))
    if mod and mod["phases"].get("P6", {}).get("status") == "passed":
        cr = None
        for f in glob.glob(os.path.join(root, prj.paths["program"], "Changes", "CR-*.md")):
            head = open(f, errors="ignore").read(800)
            if re.search(r"^status:\s*approved", head, re.M) and re.search(rf"^modules:.*\b{mod['id']}\b", head, re.M):
                cr = os.path.basename(f)[:-3]
        S.append((7, T.paint("dim", cr) if cr else T.paint("warn", "locked")))
    ck = _json(os.path.join(tools, "cache", "check.json"))
    if ck:
        at = (ck.get("at") or "")[11:16]
        S.append((8, T.paint("ok", f"check {T.g('ok')} {at}") if ck.get("ok") else
                  T.paint("err", f"check {T.g('fail')} {', '.join(ck.get('failed', [])[:3]) or 'strict'}")))
    mt = _json(os.path.join(tools, "cache", "meter.json"))
    if mt and mt.get("status"):
        lvl = mt["status"]
        S.append((9, T.paint({"GREEN": "ok", "AMBER": "warn"}.get(lvl, "err"), f"meter {lvl}")))
    pct = ((data.get("context_window") or {}).get("used_percentage"))
    if isinstance(pct, (int, float)):
        S.append((10, T.paint("err" if pct >= 90 else "warn" if pct >= 70 else "dim", f"ctx {pct:.0f}%")))
    name = (data.get("model") or {}).get("display_name")
    if name:
        S.append((11, T.paint("dim", name)))
    br, dirty = git_info(root, tools)
    if br:
        S.append((12, T.paint("dim", br + (f" {T.g('dirty')}" if dirty else ""))))
    return S, nxt


def minimal(data):
    name = (data.get("model") or {}).get("display_name") or "Claude"
    d = (data.get("workspace") or {}).get("current_dir") or data.get("cwd") or os.getcwd()
    return f"{name} · {os.path.basename(d.rstrip('/')) or d}"


def render(data, cwd=None):
    """The bar's text (one or two lines), or None when outside a pe-forge project."""
    ws = data.get("workspace") or {}
    root = find_root(ws.get("project_dir"), ws.get("current_dir"), data.get("cwd"), cwd or os.getcwd())
    if not root:
        return None
    from . import config
    cfg = config.Project(root).cfg.get("ui", {})
    T = ui.Theme(cfg)
    try:
        width = int(os.environ.get("COLUMNS") or cfg.get("statusline_width") or 110)
    except ValueError:
        width = 110
    S, nxt = segments(root, data, T, cfg)
    line = ui.fit(S, max(20, width - 2), T.paint("dim", T.g("sep")))
    if int(cfg.get("statusline_lines", 1)) >= 2:
        line += "\n" + T.paint("dim", ("  Next: " + nxt)[:max(20, width - 2)])
    return line


def run(stdin_text):
    try:
        data = json.loads(stdin_text) if stdin_text.strip() else {}
        if not isinstance(data, dict):
            raise ValueError("status line input is not a JSON object")
    except ValueError as e:
        log(None, e)
        return minimal({})
    try:
        out = render(data)
    except Exception as e:  # never a traceback in the bar
        ws = data.get("workspace") or {}
        log(find_root(ws.get("project_dir"), ws.get("current_dir"), os.getcwd()), e)
        return minimal(data)
    if out is not None:
        return out
    prev = (_json(prev_file(), {}) or {}).get("command")
    if prev:
        try:
            r = subprocess.run(prev, shell=True, input=stdin_text, capture_output=True, text=True, timeout=5)
            return r.stdout.rstrip("\n")
        except Exception as e:
            log(None, e)
    return minimal(data)


def log(root, err):
    try:
        d = os.path.join(root, ".forge", "cache") if root else os.path.dirname(prev_file())
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "statusline.log"), "a") as fh:
            fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {type(err).__name__}: {err}\n"
                     + "".join(traceback.format_exception(err))[-1500:] + "\n")
    except Exception:
        pass


# ------------------------------------------------------------------ install / uninstall
def _forge_cmd():
    from . import config
    return f'"{os.path.join(config.FORGE_ROOT, "bin", "forge")}" statusline'


def install(path, uninstall=False, chain=False):
    """Merge statusLine into the settings file at `path`. Returns a message."""
    prevs = _json(prev_file(), {}) or {}
    exists = os.path.exists(path)
    raw = open(path, "rb").read() if exists else None
    d = json.loads(raw or b"{}") if raw is not None else {}
    ours = {"type": "command", "command": _forge_cmd(), "padding": 0}
    key = os.path.realpath(path)
    if uninstall:
        rec = (prevs.get("installs") or {}).pop(key, None)
        if not rec:
            return f"no pe-forge status line recorded for {path}"
        if rec.get("created"):
            os.remove(path)
        else:
            orig = rec.get("original")
            cur = {k: v for k, v in d.items() if k != "statusLine"}
            if rec.get("previous") is not None:
                cur["statusLine"] = rec["previous"]
            if orig is not None and json.loads(orig) == cur:
                open(path, "w").write(orig)          # byte for byte, when nothing else changed meanwhile
            else:
                _write(path, cur)
        if rec.get("chained"):
            prevs.pop("command", None)
        _save_prev(prevs)
        return f"pe-forge status line removed from {path}" + (" (previous one restored)" if rec.get("previous") else "")
    if (d.get("statusLine") or {}).get("command", "").endswith(" statusline") and "forge" in d["statusLine"]["command"]:
        d["statusLine"] = ours
        _write(path, d)
        return f"pe-forge status line already installed in {path} (command refreshed)"
    prev = d.get("statusLine")
    rec = {"created": not exists, "original": raw.decode() if raw is not None else None, "previous": prev,
           "chained": bool(chain and prev and prev.get("command"))}
    if exists:
        open(path + ".bak", "wb").write(raw)
    if rec["chained"]:
        prevs["command"] = prev["command"]
    prevs.setdefault("installs", {})[key] = rec
    _save_prev(prevs)
    d["statusLine"] = ours
    _write(path, d)
    return (f"pe-forge status line installed in {path}" + (" (your previous one runs outside pe-forge projects)"
                                                         if rec["chained"] else "")
            + ". Restart Claude Code (or open /statusline) if it does not appear.")


def _write(path, d):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(d, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def _save_prev(prevs):
    os.makedirs(os.path.dirname(prev_file()), exist_ok=True)
    _write(prev_file(), prevs)


def main(argv):
    if argv[:1] == ["install"]:
        if "--user" in argv:
            path = os.path.expanduser("~/.claude/settings.json")
        else:
            root = find_root(os.environ.get("CLAUDE_PROJECT_DIR"), os.getcwd())
            if not root:
                print("not inside a pe-forge project: run it in one, or pass --user")
                return 1
            path = os.path.join(root, ".claude", "settings.local.json")
        print(install(path, "--uninstall" in argv, chain="--user" in argv))
        return 0
    print(run(sys.stdin.read()))
    return 0
