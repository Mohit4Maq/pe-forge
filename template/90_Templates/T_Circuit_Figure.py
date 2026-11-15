"""{{NAME}}: a circuit figure for the design booklet, drawn with schemdraw (forge fig render {{NAME}}).

Flow left to right, supplies up, ground down. Label every element with the booklet's reference (Q1, L1, C_out)
and every node with its net name. Replace the example buck stage below with the module's circuit.
Docs: https://schemdraw.readthedocs.io/en/stable/elements/electrical.html
"""
import schemdraw.elements as elm

from forge.schemfig import ACCENT, house, save

d = house()
d += elm.SourceV().up().label("V_IN")
d += elm.Line().right(0.6)
d += (q1 := elm.Switch().right().label("Q1"))
d += (n_sw := elm.Dot(open=True).label("SW", "top", color=ACCENT))
d += elm.Inductor2().right().label("L1")
d += (n_out := elm.Dot().label("V_OUT", "top"))
d += elm.Capacitor().down().label("C_OUT", loc="bottom")
d += elm.Ground()
d += elm.Line().at(n_sw.center).down()
d += elm.Diode().down().reverse().label("D1", loc="bottom")
d += elm.Ground()
d += elm.Line().at(n_out.center).right()
d += elm.Resistor().down().label("R_LOAD", loc="bottom")
d += elm.Ground()

save(d, __file__)
