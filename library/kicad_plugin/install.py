#!/usr/bin/env python3
"""Install the MC Tools toolbar into KiCad 10 as an IPC API plugin (runtime "exec").

  python3 kicad_plugin/install.py        (re-run after editing ACTIONS; restart KiCad)

Creates ~/Documents/KiCad/10.0/plugins/com.mc.tools/ with plugin.json, one wrapper script per
button and generated icons. The wrappers call kicad_plugin/mc_action.sh in this repo, so edits
to mc_action.sh take effect without reinstalling.
"""
import json
import shutil
import stat
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
DEST = Path.home() / "Documents/KiCad/10.0/plugins/com.mc.tools"
FONT = "/System/Library/Fonts/SFNS.ttf"

S, P, F = "schematic", "pcb", "project_manager"
ACTIONS = [  # id, name, description, glyph, colour, scopes
    ("check",   "MC Check",       "Design check: library/DB sync, net naming, decoupling, ERC",  "✓",  "#2e7d32", [S, P, F]),
    ("ercdrc",  "MC ERC+DRC",     "ERC and DRC summary (DRC includes schematic↔PCB parity)",   "!",  "#c62828", [S, P, F]),
    ("bom",     "MC BOM",         "BOM from the MC_Parts database + DigiKey/Mouser upload files", "B", "#1565c0", [S, P, F]),
    ("pdf",     "MC PDF",         "Export the schematic PDF and open it",                        "P",  "#6a1b9a", [S, F]),
    ("render3d", "MC 3D",          "Render the board in 3D and open the image",                   "3D", "#00838f", [P, F]),
    ("find",    "MC Find part",   "Search MC_Parts in a terminal",                               "?",  "#ef6c00", [S, P, F]),
    ("import",  "MC Import part", "Import a vendor download (UL/SnapEDA/Altium) into MC_Lib",     "+",  "#4e342e", [S, P, F]),
    ("release", "MC Release",     "Release gate (ERC/DRC/check/BOM) then Gerbers, PDF, STEP, P&P", "R", "#37474f", [S, P, F]),
    ("copilot", "MC Co-pilot",    "Start the Claude co-pilot: re-checks on every save and suggests fixes", "AI", "#5e35b1", [S, P, F]),
    ("term",    "MC Terminal",    "Terminal in the project folder with MC commands (khelp)",     ">_", "#212121", [S, P, F]),
]


def icon(glyph, colour, size, dark):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pad = max(1, size // 12)
    d.rounded_rectangle([pad, pad, size - pad - 1, size - pad - 1], radius=size // 5,
                        fill=colour if not dark else _lighten(colour))
    fs = int(size * (0.62 if len(glyph) == 1 else 0.44))
    font = ImageFont.truetype(FONT, fs)
    font.set_variation_by_name("Bold") if hasattr(font, "set_variation_by_name") else None
    box = d.textbbox((0, 0), glyph, font=font)
    x = (size - (box[2] - box[0])) / 2 - box[0]
    y = (size - (box[3] - box[1])) / 2 - box[1]
    d.text((x, y), glyph, font=font, fill="white" if not dark else "#111111")
    return img


def _lighten(hexcol, f=0.45):
    r, g, b = (int(hexcol[i:i + 2], 16) for i in (1, 3, 5))
    return "#%02x%02x%02x" % tuple(int(c + (255 - c) * f) for c in (r, g, b))


def main():
    if DEST.exists():
        shutil.rmtree(DEST)
    (DEST / "icons").mkdir(parents=True)
    actions = []
    for aid, name, desc, glyph, colour, scopes in ACTIONS:
        light, dark = [], []
        for size in (24, 48):
            for is_dark, bucket in ((False, light), (True, dark)):
                fn = f"icons/{aid}-{'dark' if is_dark else 'light'}-{size}.png"
                icon(glyph, colour, size, is_dark).save(DEST / fn)
                bucket.append(fn)
        wrapper = DEST / f"{aid}.sh"
        wrapper.write_text(f'#!/bin/zsh\nexec "{HERE}/mc_action.sh" {aid}\n')
        wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        actions.append({"identifier": aid, "name": name, "description": desc, "show-button": True,
                        "scopes": scopes, "entrypoint": f"{aid}.sh", "icons-light": light, "icons-dark": dark})
    plugin = {"$schema": "https://go.kicad.org/api/schemas/v1", "identifier": "com.mc.tools", "name": "MC Tools",
              "description": "MC_Lib checks, BOM, release and library tools", "runtime": {"type": "exec"},
              "actions": actions}
    (DEST / "plugin.json").write_text(json.dumps(plugin, indent=2) + "\n")
    (HERE / "mc_action.sh").chmod(0o755)
    print(f"installed {len(actions)} actions → {DEST}")


if __name__ == "__main__":
    main()
