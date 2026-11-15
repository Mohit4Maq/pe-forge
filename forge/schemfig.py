"""House style for the booklet's circuit figures, drawn with schemdraw (https://schemdraw.readthedocs.io).

A figure script (figures/<name>.py beside the booklet, from `forge fig new <name>`) does:

    import schemdraw.elements as elm
    from forge.schemfig import house, save
    d = house()                       # the pe-forge drawing style
    d += elm.SourceV().up().label('V_in')
    ...
    save(d, __file__)                 # figures/<name>.svg (+ .png when matplotlib is there)

Rules the style encodes: signal flow left to right, supplies up, ground down, one font, thin uniform lines,
labels as the booklet names them (refs and net names), no colour except the accent for the measured node.
"""
import os
import sys

ACCENT = "#4245f5"


def house(unit=2.5, fontsize=11, lw=1.6):
    import schemdraw
    schemdraw.config(unit=unit, fontsize=fontsize, lw=lw, font="sans-serif", color="#1d1d1f")
    d = schemdraw.Drawing(show=False)
    return d


def save(d, script, formats=("svg", "png")):
    """Write the figure next to its script: <name>.svg and <name>.png (PNG only when matplotlib is installed).
    Returns the written paths."""
    base = os.path.splitext(os.path.abspath(script))[0]
    out = []
    for ext in formats:
        p = f"{base}.{ext}"
        try:
            d.save(p, transparent=False)
        except Exception as e:  # PNG needs matplotlib; SVG always works
            if ext == "svg":
                raise
            print(f"({ext} skipped: {e})", file=sys.stderr)
            continue
        out.append(p)
    return out
