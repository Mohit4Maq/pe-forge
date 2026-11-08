"""Obsidian as the project's document manager: every pe-forge project folder is also an Obsidian vault.

  forge vault init [dir]       write the pe-forge vault settings into <dir>/.obsidian/ (never overwrites a setting)
  forge vault open [dir] [--file REL] [--restart]
                               init, register the vault with Obsidian and open exactly that vault (by its id, never an
                               enclosing vault such as a home-folder vault); --file opens a note in it; --restart quits
                               a running Obsidian first so the folder can be registered

Settings: [[wikilinks]] (the ID links `forge link` writes), links updated on rename, attachments in
00_Inbox/attachments, and the tool folders hidden from search and the graph (.forge/, KiCad backups and release
outputs). Registration writes Obsidian's vault list only while Obsidian is closed (it rewrites that file on exit);
otherwise the vault is opened through Obsidian's own link handler, which asks to add it.
"""
import json
import os
import secrets
import subprocess
import sys
import time
import urllib.parse

APP = {
    "useMarkdownLinks": False,
    "newLinkFormat": "shortest",
    "alwaysUpdateLinks": True,
    "attachmentFolderPath": "00_Inbox/attachments",
    "showUnsupportedFiles": True,
    "userIgnoreFilters": [".forge/", "**/*-backups/", "**/release/", "**/.history/"],
}
CORE = ["file-explorer", "global-search", "switcher", "graph", "backlink", "outgoing-link", "tag-pane",
        "page-preview", "templates", "outline", "word-count", "file-recovery"]
TEMPLATES = {"folder": "90_Templates"}


def registry_path():
    if os.environ.get("PE_FORGE_OBSIDIAN_REGISTRY"):          # tests, or an Obsidian kept elsewhere
        return os.environ["PE_FORGE_OBSIDIAN_REGISTRY"]
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Application Support/obsidian/obsidian.json")
    if sys.platform.startswith("win"):
        return os.path.join(os.environ.get("APPDATA", ""), "obsidian", "obsidian.json")
    return os.path.expanduser("~/.config/obsidian/obsidian.json")


def init(d):
    """Create or complete <d>/.obsidian without changing anything the person set. Returns files written."""
    o = os.path.join(d, ".obsidian")
    os.makedirs(o, exist_ok=True)
    wrote = []
    for name, want in (("app.json", APP), ("core-plugins.json", CORE), ("templates.json", TEMPLATES)):
        p = os.path.join(o, name)
        try:
            have = json.load(open(p))
        except (OSError, ValueError):
            have = None
        if isinstance(want, dict):
            new = dict(want, **(have if isinstance(have, dict) else {}))      # the person's values win
        else:
            new = have if isinstance(have, list) else want
        if new != have:
            json.dump(new, open(p, "w"), indent=2)
            wrote.append(p)
    os.makedirs(os.path.join(d, "00_Inbox", "attachments"), exist_ok=True)
    gi = os.path.join(d, ".gitignore")
    lines = open(gi).read().split("\n") if os.path.exists(gi) else []
    add = [x for x in (".obsidian/workspace*.json", ".obsidian/cache") if x not in lines]
    if add:
        with open(gi, "a") as fh:
            fh.write(("\n" if lines and lines[-1] else "") + "# Obsidian: per-machine state\n" + "\n".join(add) + "\n")
    return wrote


def obsidian_running():
    try:
        return subprocess.run(["pgrep", "-x", "Obsidian"], capture_output=True).returncode == 0
    except OSError:
        return False


def _registry():
    try:
        return json.load(open(registry_path()))
    except (OSError, ValueError):
        return {}


def vault_id(d):
    """The id Obsidian knows this exact folder by, or None."""
    d = os.path.realpath(d)
    for k, v in _registry().get("vaults", {}).items():
        if os.path.realpath(v.get("path", "")) == d:
            return k
    return None


def enclosing_vaults(d):
    """Registered vaults that contain this folder (e.g. a vault on the whole home folder): opening by path would
    land in one of them, so pe-forge always opens by id."""
    d = os.path.realpath(d)
    out = []
    for v in _registry().get("vaults", {}).values():
        p = os.path.realpath(v.get("path", ""))
        if p and p != d and d.startswith(p.rstrip(os.sep) + os.sep):
            out.append(p)
    return out


def quit_obsidian(timeout=15):
    if sys.platform == "darwin":
        subprocess.run(["osascript", "-e", 'quit app "Obsidian"'], capture_output=True)
    else:
        subprocess.run(["pkill", "-x", "obsidian"], capture_output=True)
    for _ in range(timeout * 2):
        if not obsidian_running():
            return True
        time.sleep(0.5)
    return False


def register(d):
    """'registered', 'known' or 'running' (Obsidian open: not written)."""
    d = os.path.realpath(d)
    reg = registry_path()
    data = _registry()
    vaults = data.setdefault("vaults", {})
    if any(os.path.realpath(v.get("path", "")) == d for v in vaults.values()):
        return "known"
    if obsidian_running():
        return "running"
    vaults[secrets.token_hex(8)] = {"path": d, "ts": int(time.time() * 1000)}
    os.makedirs(os.path.dirname(reg), exist_ok=True)
    if os.path.exists(reg):
        open(reg + ".pe-forge.bak", "w").write(open(reg).read())
    tmp = reg + ".tmp"
    json.dump(data, open(tmp, "w"))
    os.replace(tmp, reg)
    return "registered"


def open_url(d, rel=None):
    """obsidian:// link to exactly this vault (by id) and optionally a note in it; None if not registered."""
    vid = vault_id(d)
    if not vid:
        return None
    url = "obsidian://open?vault=" + urllib.parse.quote(vid)
    if rel:
        url += "&file=" + urllib.parse.quote(rel)
    return url


def open_vault(d, rel=None):
    url = open_url(d, rel)
    if url is None:
        return False
    cmd = ["open", url] if sys.platform == "darwin" else ["xdg-open", url]
    try:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except OSError:
        return False


def main(argv):
    if not argv or argv[0] not in ("init", "open"):
        print(__doc__)
        return 1
    pos = [a for i, a in enumerate(argv[1:], 1) if not a.startswith("--") and argv[i - 1] != "--file"]
    d = os.path.abspath(pos[0] if pos else os.getcwd())
    if not os.path.isdir(d):
        raise SystemExit(f"{d}: no such folder")
    wrote = init(d)
    print(f"vault settings: {len(wrote)} file(s) written in {os.path.join(d, '.obsidian')}" if wrote else
          "vault settings: already in place")
    if argv[0] == "open":
        rel = argv[argv.index("--file") + 1] if "--file" in argv else None
        r = register(d)
        if r == "running" and "--restart" in argv:
            print("quitting Obsidian to register this folder as its own vault ...")
            if quit_obsidian():
                r = register(d)
        for p in enclosing_vaults(d):
            print(f"note: Obsidian also has a vault on {p}, which contains this folder; pe-forge opens this folder's "
                  "own vault by id. Remove that vault in Obsidian (Manage vaults) if it was not meant to be one.")
        if r == "running":
            print("Obsidian is open and this folder is not one of its vaults yet: run  forge vault open --restart  "
                  "(quits and reopens Obsidian), or add it in Obsidian with 'Open folder as vault'")
            return 1
        print({"registered": "registered with Obsidian", "known": "already in Obsidian's vault list"}[r])
        if not open_vault(d, rel):
            print("could not start Obsidian: open the folder with 'Open folder as vault'")
            return 1
    return 0
