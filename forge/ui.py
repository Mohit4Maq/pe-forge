"""pe-forge terminal theme: colours, glyphs and width-aware fitting, shared by the status line, hook messages and
`forge status`. Standard library only; layout spec in docs/ui-layout.md.

Colour: truecolor when $COLORTERM is truecolor / 24bit, else 256 colours; none with $NO_COLOR or [ui] color = false.
Accent: #4245f5 (blue) unless [ui] accent = "#RRGGBB" (project.toml, or ~/.config/pe-forge/ui.toml).
Glyphs: [ui] glyphs = "ascii" swaps every symbol for plain ASCII.
Marker: the bar starts with the MaqLabs mark "maq" (bold italic, accent colour); [ui] mark = "<text>" changes it,
mark = "" removes it. The banner (`forge status --banner`) shows the MaqLabs wordmark; [ui] logo = false hides it.
"""
import os
import re
import sys
import unicodedata

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None

PALETTE = {"accent": ("#4245f5", 63), "dim": ("#8a8a8a", 245), "ok": ("#3fb950", 71), "warn": ("#d29922", 178),
           "err": ("#ff6e9c", 204), "bright": (None, None)}
GLYPHS = {"mark": "◆", "hook": "▸", "ok": "✓", "fail": "✗", "active": "▶", "review": "?", "todo": "·", "skip": "–",
          "warn": "⚠", "dirty": "●", "sep": " · "}
ASCII = {"mark": "*", "hook": ">", "ok": "ok", "fail": "x", "active": ">", "review": "?", "todo": ".", "skip": "-",
         "warn": "!", "dirty": "*", "sep": " | "}
LOGO = [   # the MaqLabs wordmark ("maq Labs"), traced from the logo artwork into quadrant blocks (64 x 7)
    "                                                 ▟█",
    " ▄▄▄▄▄▄▄▄▄▖  ▗▄▄▄▄▄▖ ▗▄▄▄▄▄▄    ▗▄▖      ▗▄▄▄▄▄▄ ██ ▄▄▄   ▄▄▄▄▄▄",
    " █▛  ██  ██ ▟█▘  ▐█▌▗█▌  ▗██    ██▘     ▗█▌  ▗█▛▗█▌   █▙ ██   ▝▀",
    "▐█▌ ▐█▛  ██ █▛   ▟█▘▟█   ▐█▌   ▗██      ▟█   ▐█▌▐█▌  ▗█▌▐█▙▄▄▄▄▄",
    "▐█  ▐█▌ ▐█▛▗█▌   ██ ██   ██▌   ▐█▌      ██   ▟█▘██   ▐█▌     ▐█▀",
    "▀▀  ▀▀  ▝▀▘▝▀▀▀▘ ▀▀▝▀▀▀▀ ██    ▝▀▀▀▀▀▀▀▝▀▀▀▀ ▀▀ ▀▀▀▀▀▀▀ ▀▀▀▀▀▀▘",
    "                        ▐█▛",
]
ASCII_LOGO = ["maq Labs"]
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def user_cfg():
    p = os.path.expanduser("~/.config/pe-forge/ui.toml")
    if tomllib and os.path.exists(p):
        try:
            with open(p, "rb") as fh:
                return tomllib.load(fh).get("ui", {})
        except Exception:
            return {}
    return {}


class Theme:
    def __init__(self, cfg=None, color=None):
        c = dict(user_cfg(), **(cfg or {}))
        self.cfg = c
        if color is None:
            color = not os.environ.get("NO_COLOR") and c.get("color", True) is not False
        self.color = color
        self.truecolor = os.environ.get("COLORTERM", "").lower() in ("truecolor", "24bit")
        self.ascii = c.get("glyphs") == "ascii"
        self.accent = c.get("accent") if re.fullmatch(r"#[0-9a-fA-F]{6}", str(c.get("accent", ""))) else None
        self.mark = str(c.get("mark", "maq"))
        self.logo = c.get("logo", True) is not False

    def g(self, name):
        return (ASCII if self.ascii else GLYPHS)[name]

    def paint(self, style, text, bold=False, italic=False):
        if not self.color or not text:
            return text
        hexv, c256 = PALETTE.get(style, (None, None))
        if style == "accent" and self.accent:
            hexv, c256 = self.accent, PALETTE["accent"][1]
        codes = (["1"] if bold or style == "bright" else []) + (["3"] if italic else [])
        if hexv:
            if self.truecolor:
                r, gr, b = (int(hexv[i:i + 2], 16) for i in (1, 3, 5))
                codes.append(f"38;2;{r};{gr};{b}")
            else:
                codes.append(f"38;5;{c256}")
        return f"\x1b[{';'.join(codes)}m{text}\x1b[0m" if codes else text


def brand(theme):
    """The bar's first segment prefix: the mark (bold italic accent) and an arrow, or '' when mark is empty."""
    if not theme.mark:
        return ""
    return theme.paint("accent", theme.mark, bold=True, italic=True) + " " + theme.paint("accent", theme.g("hook")) + " "


def banner(theme):
    """The MaqLabs wordmark in the accent colour, then the tool name (ASCII: plain text)."""
    rows = []
    if theme.logo:
        rows = [theme.paint("accent", r, bold=True) for r in (ASCII_LOGO if theme.ascii else LOGO)]
    sep = "-" if theme.ascii else "·"
    return rows + [theme.paint("dim", f"pe-forge  {sep}  power-electronics design, idea to manufacturing files")]


def visible_len(s):
    """Printed width: ANSI codes count 0, wide (East Asian W / F) characters 2, combining marks 0."""
    n = 0
    for ch in ANSI.sub("", s):
        if unicodedata.combining(ch):
            continue
        n += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return n


def fit(segments, width, sep):
    """Join (priority, text) segments in priority order, dropping whole lowest-priority segments until it fits."""
    segs = sorted(segments, key=lambda s: s[0])
    while segs:
        line = sep.join(t for _, t in segs)
        if visible_len(line) <= width or len(segs) == 1:
            return line
        segs = segs[:-1]
    return ""


def hook_message(text, theme=None):
    """The JSON a hook prints to show the user one short branded line (systemMessage). Model context is separate."""
    t = theme or Theme(color=False)
    return {"systemMessage": f"pe-forge {t.g('hook')} {text}"}


def tty():
    return sys.stdout.isatty()
