"""Obsidian as the project's document manager: every pe-forge project folder is also an Obsidian vault.

  forge vault init [dir]       write the pe-forge vault settings into <dir>/.obsidian/ (never overwrites a setting)
  forge vault open [dir]       init, register the vault with Obsidian and open it

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


def register(d):
    """'registered', 'known' or 'running' (Obsidian open: not written)."""
    d = os.path.realpath(d)
    reg = registry_path()
    try:
        data = json.load(open(reg))
    except (OSError, ValueError):
        data = {}
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


def open_vault(d):
    url = "obsidian://open?path=" + urllib.parse.quote(os.path.realpath(d))
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
    d = os.path.abspath(argv[1] if len(argv) > 1 else os.getcwd())
    if not os.path.isdir(d):
        raise SystemExit(f"{d}: no such folder")
    wrote = init(d)
    print(f"vault settings: {len(wrote)} file(s) written in {os.path.join(d, '.obsidian')}" if wrote else
          "vault settings: already in place")
    if argv[0] == "open":
        r = register(d)
        print({"registered": "registered with Obsidian", "known": "already in Obsidian's vault list",
               "running": "Obsidian is open: it will ask to add this folder as a vault"}[r])
        if not open_vault(d):
            print("could not start Obsidian: open the folder with 'Open folder as vault'")
    return 0
