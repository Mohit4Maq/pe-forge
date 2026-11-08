# High-Power PCB Rule Book: EMI, Thermal Coupling & Routing

Nov 8, 2026 · @Veltara

## 1. Purpose and how to use this book

On a high-power board, layout sets overshoot, EMI margin and hot-spot temperature before any component choice does. This book turns vendor app notes, standards and bench experience into numbered, checkable rules for inverters, motor drives, PFC/DC-DC stages and battery converters using IGBT, Si MOSFET, SiC and GaN switches.

**Scope.** Boards switching roughly 50 V–1000 V bus, 5 A–300 A, 10 kHz–1 MHz, with dv/dt from 5 V/ns (IGBT) to 100+ V/ns (GaN). Rules marked *WBG* matter most for SiC/GaN; the rest apply to all.

**Rule IDs.** Each rule has a prefix and number so it can be quoted in design reviews and checklists:

| Prefix | Area |
| --- | --- |
| STK | Stackup and planes |
| PL | Power commutation loop |
| GD | Gate drive |
| SW | Switch node and common-mode EMI |
| GND | Grounding and return paths |
| SNS | Sensing and control signals |
| FLT | EMI filter and I/O |
| TH | Thermal design |
| TX | Thermal crosstalk |
| HC | High-current routing |
| HV | Creepage and clearance |

**Strength words.** *Must* = violating it reliably causes failure, failed compliance or a safety issue. *Should* = default; deviate only with a reason written in the review notes. *Prefer* = a tie-breaker.

**Priority order when rules conflict.** Safety spacing (HV) > power loop (PL) > gate loop (GD) > common-mode capacitance (SW) > thermal (TH/TX) > routing convenience. Thermal and loop rules conflict most often; section 4 ("Trading loop against thermal") says how to trade them.

## 2. The physics in one page

Every EMI and crosstalk rule in this book controls one of two equations: inductance turns di/dt into voltage, and capacitance turns dv/dt into current.

```latex
V_{L} = L \cdot \frac{di}{dt} \qquad I_{C} = C \cdot \frac{dv}{dt} \qquad f_{ring} = \frac{1}{2\pi\sqrt{L\,C}}
```

**Worked numbers to calibrate intuition** (all from the formulas above):

| Situation | Values | Result |
| --- | --- | --- |
| SiC turn-off overshoot | 15 nH loop, 100 A in 40 ns (2.5 A/ns) | 37.5 V extra on V\_DS |
| Same, optimised vertical loop | 4 nH | 10 V |
| Common-source inductance kick on gate | 2 nH shared, 2.5 A/ns | 5 V fighting the gate drive |
| Switch-node copper over a plane | 1 cm², 0.2 mm prepreg, εr 4.4 | ≈ 19 pF |
| CM current from that copper | 19 pF at 50 V/ns | ≈ 1 A peak into the plane |
| TO-247 tab on insulated heatsink | \~300 mm² tab, 0.2 mm pad, εr ≈ 5 | ≈ 65 pF to heatsink |
| CM current via heatsink | 65 pF at 50 V/ns | ≈ 3.3 A peak to chassis/PE |
| Gate-drive isolator coupling | 2 pF across barrier at 100 V/ns | 200 mA into logic ground |

**Three consequences drive the whole book:**

1. *Loops are the antenna and the overshoot source.* The commutation loop (DC-link cap → high side → low side → back) carries the fastest di/dt on the board. Its area sets both ringing energy and magnetic near-field. Loop inductance scales with area, so you shrink area and use opposing currents in adjacent layers to cancel flux.
2. *Nodes with dv/dt are capacitive noise sources.* The switch node, the high-side gate driver's floating ground and anything bolted to a heatsink inject current through every picofarad they see. Common-mode (CM) noise is mostly this current returning through earth.
3. *Heat moves slowly but shifts precision.* Copper conducts heat as well as current, so the same planes that lower inductance also carry heat from switches to shunts, references and op-amps. A 30 °C gradient across a shunt or a reference can cost more accuracy than its datasheet tolerance.

**Measure, don't guess.** Loop inductance can be backed out of the ringing frequency: with C\_ring ≈ C\_oss of the off device, L = 1 / (4π² f² C). TI uses this method in its GaN datasheets and quotes about 2.5 nH for its reference layout ([TI LMG3522R030-Q1](https://www.ti.com/document-viewer/lit/html/SNOSD97D/GUID-96BDA46B-3B08-428F-8CE2-8294C8E9702E)). Do this on every first article board.

## 3. Stackup and planes (STK)

Use at least 4 layers for any hard-switched stage above about 20 A or 20 V/ns, and put the power loop and its return on adjacent layers separated by thin dielectric. Wolfspeed's reference split for a 6-layer board is L1–L3 for the power loop, L4 as a ground shield, and L5–L6 for signals and SMD parts ([Wolfspeed PRD-06752](https://assets.wolfspeed.com/uploads/2024/10/Wolfspeed_PRD-06752_PCB_Layout_Techniques_for_Discrete_SiC_MOSFETs_Application_Note.pdf)).

**Recommended stackups**

| Board | L1 | L2 | L3 | L4 | L5 | L6 |
| --- | --- | --- | --- | --- | --- | --- |
| 4-layer, ≤ 400 V, ≤ 50 A | Power devices, DC+, SW | DC− / PGND (thin core to L1) | Signal GND shield | Control, gate drive, sensing | — | — |
| 6-layer, inverter / OBC | Power devices, DC+, SW | DC− return | DC+ / DC− duplicate (current) | GND shield | Signals | Signals, SMD |
| 8-layer, 100 A+ | Power | DC− | DC+ | DC− | GND shield | L6–L8: signals / GND / SMD |

**Rules**

- **STK-01 (Must)** The layer directly under the commutation loop is its return (DC− or DC+), not signal ground and not a split. Target 0.1–0.2 mm dielectric between L1 and L2. TI's vertical-loop reference uses 0.15 mm and reaches a 2 mm² loop area ([TI SNVA803](https://www.ti.com/lit/ab/snva803/snva803.pdf)).
- **STK-02 (Must)** Check dielectric breakdown and partial discharge for that thin layer. Above about 400 V bus, a 0.1 mm prepreg between DC+ and DC− is a reliability risk; use 0.2–0.3 mm, a high-CTI / high-voltage prepreg, and ask the fab for a PD or hipot test coupon. Never route the loop return through a layer pair whose dielectric has not been rated for the bus plus overshoot.
- **STK-03 (Should)** Use heavier copper for current, not for the whole board. Common split: 2 oz outer, 2–3 oz inner power layers, 1 oz signal layers. Over 3 oz outer copper limits fine-pitch gate-driver footprints and min spacing; specify it only where the current map demands it.
- **STK-04 (Must)** Signal layers sit on the far side of a solid ground shield from the power layers. No control trace runs on a layer adjacent to a switch-node pour.
- **STK-05 (Should)** Keep the stackup symmetric in copper weight to avoid bow and twist, which crack large ceramic caps and SMD power packages under the heatsink.
- **STK-06 (Must)** Inner ground pours must be pulled back from high-voltage plated holes. Wolfspeed warns of breakdowns between plated through-holes and inner ground layers, and advises extra distance or keeping power parts off inner mass planes ([Wolfspeed PRD-06752](https://assets.wolfspeed.com/uploads/2024/10/Wolfspeed_PRD-06752_PCB_Layout_Techniques_for_Discrete_SiC_MOSFETs_Application_Note.pdf)). Apply the same creepage numbers internally (section 13).
- **STK-07 (Should)** Duplicate DC+ and DC− on multiple layer pairs to raise current capacity and lower inductance. Each extra overlapping pair adds a parallel low-L path.
- **STK-08 (Prefer)** Choose high-Tg (≥ 170 °C) FR-4 or better for boards whose hot spots exceed 105 °C, and a CTI ≥ 400 V (material group II) or ≥ 600 V (group I) laminate to shrink creepage.
- **STK-09 (Should)** Write the stackup on the fab drawing with dielectric thickness tolerances, copper weights per layer, laminate part number and the hipot voltage. Fabs substitute materials when the drawing is vague.

## 4. Power commutation loop (PL)

The commutation loop is the single most important geometry on the board: make it a vertical loop with its return on the next layer, and budget its inductance in nanohenries before placement starts. TI's vertical-loop buck reached under 500 pH and cut switch-node overshoot by about 4 V versus a lateral loop ([TI SNVA803](https://www.ti.com/lit/ab/snva803/snva803.pdf)); EPC measured a one-point efficiency gain at 1 MHz going from 1.7 nH to 0.4 nH ([EPC webinar](https://epc-co.com/epc/Portals/0/epc/documents/presentations/EPC%20Design%20Tips%20Mandarin%20Webinar.pdf)).

**Inductance budget by technology** (achievable layout targets, package inductance included; rule-of-thumb ranges drawn from the sources and bench practice)

| Switch / package | Typical dv/dt | Target loop L | Above this, expect trouble |
| --- | --- | --- | --- |
| GaN, chip-scale / LGA | 50–150 V/ns | 0.3–1 nH | 2 nH |
| GaN / SiC in QFN, TOLL, D2PAK-7 | 30–100 V/ns | 2–6 nH | 10 nH |
| SiC / Si in TO-247-4 | 20–60 V/ns | 8–15 nH | 25 nH |
| IGBT discrete / small modules | 3–15 V/ns | 15–30 nH | 50 nH |
| Power modules with busbar | 5–30 V/ns | 10–20 nH (module + busbar) | per module datasheet |

**Quick estimators**

```latex
L_{trace} \approx 0.2\,l\left(\ln\frac{2l}{w} + 0.2235\frac{w}{l} + 0.5\right)\ \text{nH (l, w in mm)} \qquad L_{via} \approx 0.2\,h\left(\ln\frac{4h}{d} + 1\right)\ \text{nH}
```

Wolfspeed uses the first form: a 50 mm × 10 mm trace is 28.5 nH, giving a 71 V spike at 50 A in 20 ns ([Wolfspeed PRD-06752](https://assets.wolfspeed.com/uploads/2024/10/Wolfspeed_PRD-06752_PCB_Layout_Techniques_for_Discrete_SiC_MOSFETs_Application_Note.pdf)). From the second, a 0.3 mm via through 1.6 mm board is about 1.3 nH, while the same via spanning only a 0.15 mm L1–L2 gap is a few tens of pH. That is the whole argument for putting the return on L2.

**Rules**

- **PL-01 (Must)** Draw the commutation loop on the schematic as a named net group (DC+ cap → HS drain → SW → LS source → DC− cap) and place those parts first, before anything else on the board.
- **PL-02 (Must)** Put high-frequency ceramic decoupling directly across each half-bridge, within 3–5 mm of the device pads. Use several small caps in parallel (0603/1206/1210 C0G or X7R/X7T rated for the bus) rather than one large one; parallel ESL drops and self-resonance rises. TI's example uses four 10 nF 0402/0603 caps right at the high-side drain ([TI SNVA803](https://www.ti.com/lit/ab/snva803/snva803.pdf)).
- **PL-03 (Must)** Use a vertical loop: devices and HF caps on L1, return on L2 directly underneath, many vias at the innermost pad edges. EPC's rule is to place vias as close as possible to the innermost electrical connection ([EPC/Würth 2024](https://we-online.com/files/pdf1/epc_wurth-day_boston_part1_layout_2024-10-22-boston-2024.pdf)).
- **PL-04 (Should)** Use at least 4–8 vias per HF cap terminal and per device source/drain transition; several 0.3 mm vias beat one large via for inductance and for current.
- **PL-05 (Must)** Overlap DC+ and DC− planes across the whole switching cell, on more than one layer pair if possible. Wolfspeed lists overlapping and distributing DC+/DC− on multiple layers as core switching-cell rules ([Wolfspeed PRD-06752](https://assets.wolfspeed.com/uploads/2024/10/Wolfspeed_PRD-06752_PCB_Layout_Techniques_for_Discrete_SiC_MOSFETs_Application_Note.pdf)).
- **PL-06 (Must)** Check the overshoot budget: V\_bus,max + L\_loop · di/dt,max ≤ 80% of V\_DS(BR) (≤ 70% for long-life or high-altitude designs). If it fails, fix layout first, then slow di/dt (gate resistor), then add a snubber.
- **PL-07 (Should)** Use a two-tier DC-link: HF ceramics at the cell (sets the fast loop), film or electrolytic bulk within 20–50 mm on the same overlapped planes. Watch for resonance between the two tiers; add a damped element (an electrolytic's ESR or an RC) if a sweep shows a sharp peak.
- **PL-08 (Should)** For TO-247 parts, minimise lead length above the board: mount devices flush, and place HF caps on the bottom side directly between the drain and source pins if the heatsink side is taken.
- **PL-09 (Should)** Put an RC snubber or a small "decoupling" ceramic directly across each switch when L\_loop is near the budget. The snubber loop must itself be tiny; a snubber with 10 nH of leads does little.
- **PL-10 (Must)** Measure L\_loop on the first article with the ringing-frequency method (section 2), with a low-inductance probe tip at the device pins. Record it in the design file; it is the number every later revision is compared against.
- **PL-11 (Should, WBG)** Prefer packages with a Kelvin source and bottom- or top-side cooling over 3-lead through-hole parts. Wolfspeed shows lower switching loss for the same die in a 4-lead package ([Wolfspeed PRD-06752](https://assets.wolfspeed.com/uploads/2024/10/Wolfspeed_PRD-06752_PCB_Layout_Techniques_for_Discrete_SiC_MOSFETs_Application_Note.pdf)).

**Trading loop against thermal.** Big copper and spacing for cooling lengthen the loop; Infineon notes the loop suffers when extra PCB space is spent on thermal performance ([Infineon SiC package evaluation](https://www.infineon.com/assets/row/public/documents/10/42/infineon-comprehensive-electric-and-thermal-evaluation-of-sic-discrete-packages-applicationnotes-en.pdf)). Resolve it by spreading heat on the far side (heatsink through TIM, inner planes) and keeping the loop compact on L1–L2, not by spreading the devices apart.

## 5. Gate drive (GD)

The gate loop must be small, referenced only to the Kelvin source, and kept off the power loop's copper; most false turn-on, shoot-through and gate-oxide failures trace back to one of those three. A shared 15 mm × 3 mm source trace is about 8 nH and drops 2 V at 250 A/µs, which subtracts directly from gate drive ([Wolfspeed PRD-06752](https://assets.wolfspeed.com/uploads/2024/10/Wolfspeed_PRD-06752_PCB_Layout_Techniques_for_Discrete_SiC_MOSFETs_Application_Note.pdf)). At SiC speeds (2–5 A/ns) the same 8 nH is 16–40 V, enough to oscillate or destroy the gate.

**Gate loop**

- **GD-01 (Must)** Place the driver output stage within 10–20 mm of the gate pin (≤ 10 mm for GaN). Target gate-loop inductance under 5 nH for SiC/GaN and under 15 nH for IGBT. Wolfspeed's simulation with a 30 nH loop (about 2 inches of trace) could only be damped by raising Rg and losing speed ([Wolfspeed PRD-06752](https://assets.wolfspeed.com/uploads/2024/10/Wolfspeed_PRD-06752_PCB_Layout_Techniques_for_Discrete_SiC_MOSFETs_Application_Note.pdf)).
- **GD-02 (Must)** Route gate and gate-return as a stacked pair (gate on L1, return directly below on L2) or as a tight side-by-side pair. Never let them separate to go around a part.
- **GD-03 (Must)** Return the driver to the Kelvin source pin only. On 3-lead parts, split the gate return from the power source right at the package body, before any shared copper. Driver ground must not touch the power source pour anywhere else.
- **GD-04 (Must)** Put Rg(on)/Rg(off) and the driver's local supply decoupling at the driver; put the gate-source pull-down (typically 10 kΩ) at the MOSFET so the gate stays off if the driver is disconnected. Wolfspeed advises an external Cgs only on Kelvin-source packages, not on TO-247-3 ([Wolfspeed PRD-06752](https://assets.wolfspeed.com/uploads/2024/10/Wolfspeed_PRD-06752_PCB_Layout_Techniques_for_Discrete_SiC_MOSFETs_Application_Note.pdf)).
- **GD-05 (Should)** Route the active Miller clamp pin to the gate with the shortest trace on the board; a clamp with 20 mm of trace cannot hold the gate during a 50 V/ns edge.
- **GD-06 (Should)** Decouple bipolar driver rails (+15/+18 V and −3/−5 V) to the Kelvin source with ceramic caps (1–10 µF plus 100 nF) at the driver pins. These caps supply the peak gate current; their loop is part of the gate loop.
- **GD-07 (Should)** Place gate-clamp TVS or Zener (if used) at the MOSFET, not at the driver. ROHM ranks gate surge-protection placement as a priority item ([ROHM 67AN006E](https://fscdn.rohm.com/en/products/databook/applinote/discrete/sic/mosfet/sic_mosfet_layout_design_considerations_an-e.pdf)).

**Separating gate loop from power loop**

- **GD-08 (Must)** No overlap between gate-loop copper and switch-node or drain copper on any layer. Overlap adds external C\_GD, raising crosstalk and switching loss ([Wolfspeed PRD-06752](https://assets.wolfspeed.com/uploads/2024/10/Wolfspeed_PRD-06752_PCB_Layout_Techniques_for_Discrete_SiC_MOSFETs_Application_Note.pdf)). Scale: 1 cm² of overlap through 0.2 mm FR-4 is about 19 pF; at 800 V and 100 kHz, C·V²·f is about 1.2 W wasted, plus a Miller kick on every edge.
- **GD-09 (Should)** Run the gate loop orthogonal to, or on the opposite side of the device from, the power loop. EPC separates the two loops by having their currents flow in opposite or orthogonal directions ([EPC, Bodo's Power 2021](https://epc-co.com/epc/Portals/0/epc/documents/articles/Layout%20Considerations%20for%20GaN%20-%20Bodo%20January%202021.pdf)).
- **GD-10 (Should)** Under the gate-drive area, use a shield plane tied to that device's source (its local reference), not to logic ground ([Wolfspeed PRD-06752](https://assets.wolfspeed.com/uploads/2024/10/Wolfspeed_PRD-06752_PCB_Layout_Techniques_for_Discrete_SiC_MOSFETs_Application_Note.pdf)).
- **GD-11 (Should)** Keep gate loops away from magnetics (PFC chokes, transformers, motor-current busbars): at least 10–20 mm from a gapped core edge. A compact loop is also a small pickup antenna.

**Isolation barrier and floating domains**

- **GD-12 (Must)** Keep the isolation gap clean on every layer: no plane, pour or trace crosses under the isolator or the isolated DC-DC. Gap width meets the creepage number in section 13 for the working voltage.
- **GD-13 (Must, WBG)** Choose isolators and isolated DC-DCs with CMTI at least 2× the worst measured dv/dt (≥ 100–150 V/ns for SiC, ≥ 200 V/ns for GaN), and DC-DC barrier capacitance as low as practical (under 5–10 pF for SiC high-side supplies). At 100 V/ns every pF injects 100 mA into the logic side.
- **GD-14 (Must)** Treat the high-side driver's secondary ground pour as part of the switch node: keep it small, keep it away from other domains, and never run it over another domain's plane.
- **GD-15 (Should)** Place bootstrap diode and capacitor right at the driver; route the bootstrap path as switch-node-referenced copper with the same small-area rule.
- **GD-16 (Should)** Reference desaturation and overcurrent sense lines to the driver's local Kelvin ground, with the blanking/filter cap at the driver pin.

**Paralleled devices**

- **GD-17 (Must)** Place the driver at the electrical centre of the gate terminals, with equal-length gate and Kelvin traces to every device, an individual gate resistor per device, and a small resistor in each Kelvin return to damp loops between sources ([Wolfspeed PRD-06752](https://assets.wolfspeed.com/uploads/2024/10/Wolfspeed_PRD-06752_PCB_Layout_Techniques_for_Discrete_SiC_MOSFETs_Application_Note.pdf)). Typical Kelvin-return resistor: 0.5–2 Ω.

## 6. Switch node and common-mode EMI (SW)

Common-mode noise is displacement current from every dv/dt node into earth, so the rules are: shrink the capacitance, shield what remains back to the DC bus, and give the current a short, intended return path. In most inverters the heatsink is the largest single path; the device-to-heatsink capacitance is identified as the critical CM parameter in both patent and academic work ([Grobler, Univ. Pretoria](https://repository.up.ac.za/bitstream/2263/61590/1/Grobler_Analysis_2017.pdf); [US 7602159](https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/7602159)).

**Switch-node copper**

- **SW-01 (Must)** Size switch-node copper for current only. Wolfspeed: keep high-dv/dt traces small and only as wide as the current needs ([Wolfspeed PRD-06752](https://assets.wolfspeed.com/uploads/2024/10/Wolfspeed_PRD-06752_PCB_Layout_Techniques_for_Discrete_SiC_MOSFETs_Application_Note.pdf)). Use section 2's 19 pF/cm² figure: every square centimetre is about an amp of CM current at 50 V/ns.
- **SW-02 (Must)** Keep switch-node copper on one layer. TI routes SW and BST on a single layer so no high-dv/dt vias appear on the bottom side near the reference ground plane ([TI SNVA803](https://www.ti.com/lit/ab/snva803/snva803.pdf)). If current forces a second layer, put a quiet (DC bus) plane between it and the outside world.
- **SW-03 (Should)** Where the switch node runs to an inductor, transformer or output connector, surround it with DC− or DC+ pour on the same layer and a quiet plane below; route it the shortest way to the magnetic and put the hot terminal of the magnetic toward the switches.
- **SW-04 (Should)** Orient inductors and transformers so the winding start (the dv/dt end) is buried inside and connects to the switch node; the outer layer, which faces the board and enclosure, should be the quiet end.
- **SW-05 (Must)** Keep switch-node copper, its vias and its magnetics at least 10 mm from the board edge, from connectors and from the EMI filter. Edge copper with dv/dt radiates and couples to cables.

**Heatsink, chassis and shields**

- **SW-06 (Must)** Decide in the schematic what the heatsink connects to, and draw it: chassis/PE, DC−, or a capacitive midpoint. A floating heatsink is a resonant antenna; never leave it undefined.
- **SW-07 (Should)** Minimise device-to-heatsink capacitance: thicker or lower-εr TIM where thermal budget allows, the smallest drain tab area, and bottom-cooled packages whose exposed pad is the source (quiet) rather than drain where available. Every halving of C halves heatsink CM current.
- **SW-08 (Should)** For high-dv/dt designs, insert a shield between the switch node and the earthed heatsink and return it to the DC bus: a copper layer between two insulating layers tied to source or DC− keeps the CM loop local ([US 12648448](https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/12648448)). Module-level CM screens tied to the DC bus have shown up to 26 dB less CM current ([CPES, Virginia Tech](https://cpes.vt.edu/library/viewnugget/1125/)). On a PCB, the equivalent is a DC− or DC+ plane between switch-node copper and the bottom (heatsink-facing) layer.
- **SW-09 (Should)** When the heatsink is tied to PE, add a Y-capacitor (or a few hundred pF to a few nF, safety rated) from heatsink or chassis back to DC−/DC+ right at the power stage, within leakage-current limits. This returns the CM current to its source instead of through the LISN or the cable.
- **SW-10 (Prefer, WBG)** Shield the high-side gate-driver supply and the device cooling pads too; local shielding of the mid-point track, gate supplies and cooling pads progressively cut CM noise in a GaN leg ([Derkacz et al., IEEE TPEL](https://pub.pg.edu.pl/en/download/157957-emi-mitigation-of-gan-power-inverter-leg-by-local-shielding-techniques)).
- **SW-11 (Should)** Use a symmetrical layout for H-bridges and three-phase legs so CM currents from complementary nodes partly cancel; mirror the phase legs rather than copying them.

**dv/dt control**

- **SW-12 (Should)** Treat dv/dt as a design parameter: set it with separate Rg(on) and Rg(off). Halving dv/dt above the corner frequency of the switching edge drops CM noise by about 6 dB there. Do this after the layout is right, because it costs switching loss.
- **SW-13 (Should)** Use a fixed, spread-spectrum or randomised switching frequency where the controller allows, to spread conducted peaks; check that it does not interfere with current-sampling timing.

## 7. Grounding and return paths (GND)

Design returns, not grounds: every high di/dt current must have a defined return on the layer directly beside it, and power and control grounds meet at exactly one deliberate point. Most "ground noise" on power boards is power return current wandering through control copper because no one drew where it should go.

- **GND-01 (Must)** Name ground domains in the schematic: PGND (power return / DC−), AGND or SGND (control and sensing), each gate driver's floating reference (one per switch), PE/chassis. Separate net names force the layout tool to keep them apart until you join them on purpose.
- **GND-02 (Must)** Join PGND and SGND at one point, at the low-di/dt end of the power return: usually the DC− terminal of the bulk capacitor or the low-side shunt's ground-side Kelvin pad. Never at a switch source or under the half-bridge. ROHM's EMC guide shows the failure of making power ground common with other grounds ([ROHM 67AN064E](https://atta.szlcsc.com/upload/public/pdf/source/20250515/B6ED126B6A3159D21E35172D097BF573.pdf)).
- **GND-03 (Must)** Under the control section, use one solid SGND plane with no slots. Do not split a ground plane under any trace that crosses the split; if two domains must meet, the crossing signal goes through an isolator, a differential pair, or a bridge at the star point.
- **GND-04 (Must)** No power return current flows through SGND copper. Check it: for each high-current loop, trace the return path on the layout and confirm it never enters the control area.
- **GND-05 (Should)** Stitch reference planes with vias every λ/20 at the highest frequency of concern; for 1 GHz radiated limits that is about 7–10 mm. Stitch plane edges with a row of vias to stop edge radiation.
- **GND-06 (Should)** Keep the control section's ground plane at least 3–5 mm from any switch-node copper on any layer, and never overlapping it in Z.
- **GND-07 (Must)** Every signal entering the control area from the power area (PWM, fault, sense) needs an adjacent return conductor along its whole length. A single trace with its return elsewhere is a loop antenna picking up the power stage's field.
- **GND-08 (Should)** Put PE/chassis connections (screw pads to enclosure) at the I/O edge, beside the filter, not in the middle of the board. Use multiple screws with plated pads and vias for low inductance.
- **GND-09 (Should)** Use a 360° bond for cable shields to chassis at the entry point; pigtails above a few cm make shields ineffective above \~10 MHz.
- **GND-10 (Prefer)** For multiphase inverters, give each phase leg its own local DC− return island on L2, joined to the main DC− plane at the DC-link capacitor, so phase return currents don't share copper with each other's sensing.

## 8. Sensing and control signals (SNS)

Sensing is where EMI and heat both turn into measurement error: route every sense signal as a Kelvin pair, filter at the receiver, and keep sense parts out of the hot zone. A 2 nH shunt inductance at 100 A/µs gives a 200 mV spike, larger than a 100 mV full-scale signal ([PCBSync shunt guide](https://pcbsync.com/current-sense-resistor-guide/)).

**Shunts and current sensing**

- **SNS-01 (Must)** Use 4-wire Kelvin connections, sensing at a point outside the direct high-current path ([TI LMP8640 layout](https://www.ti.com/document-viewer/LMP8640/datasheet/layout)). For 2-terminal shunts, take sense traces from the inner edge of each pad.
- **SNS-02 (Must)** Route the two sense lines as a tight differential pair over SGND, away from the shunt's own current path, with the RC filter at the amplifier inputs. Match copper mass on both input pins; the copper-solder junction forms a thermocouple ([TI LMP8640 layout](https://ti.com/document-viewer/LMP8640-Q1/datasheet/layout)).
- **SNS-03 (Should)** Prefer low-inductance shunts (wide-terminal, reverse-geometry or 4-terminal metal strip) for switched currents, and place the shunt where di/dt is lowest (in the DC− return of the leg, not the switch node) unless the topology demands otherwise.
- **SNS-04 (Should)** For paralleled shunts, sense from the symmetry point of the array; sensing at one end skews the reading ([Wevolver shunt article](https://www.wevolver.com/article/what-is-a-shunt-resistor-sizing-sensing-and-layout)).
- **SNS-05 (Should)** Sample current at the centre of the PWM on-time or off-time and keep a blanking window after each edge. Layout sets how long the ringing lasts and therefore how long the blanking must be.
- **SNS-06 (Should)** For Hall/flux-gate or core-less magnetic sensors, keep busbars carrying other phases at the distance the sensor's datasheet requires (often 5–10 mm minimum) and route the measured conductor straight through the sensing area.

**Control and feedback**

- **SNS-07 (Must)** Keep high-impedance nodes (voltage-feedback dividers, comparator inputs, op-amp summing nodes, MCU ADC inputs) physically short, close to their receiver, and on the side of the board away from switch nodes. Put the divider at the receiver, not at the measured node, and run the low-impedance net the long way.
- **SNS-08 (Must)** Feed high-voltage dividers from the measured node through several series resistors (each within its voltage rating), and leave creepage between the top resistor and the low-voltage end.
- **SNS-09 (Should)** Route PWM and fault lines between the controller and gate drivers as differential pairs or with an adjacent ground, along the board edge away from magnetics, as Wolfspeed does in its OBC reference ([Wolfspeed PRD-06752](https://assets.wolfspeed.com/uploads/2024/10/Wolfspeed_PRD-06752_PCB_Layout_Techniques_for_Discrete_SiC_MOSFETs_Application_Note.pdf)). Add a series resistor (22–100 Ω) at the source and a small cap at the receiver if the line exceeds \~50 mm.
- **SNS-10 (Should)** Place temperature sensors (NTC, die sensors) where the protection decision needs them: at the hottest device's pad, with the NTC's return run as a pair to the ADC. An NTC on a quiet corner protects nothing.
- **SNS-11 (Prefer)** Use a ground-referenced shield trace or guard pour around precision analog nets that must pass near the power stage, tied to SGND at one end.

## 9. EMI filter and I/O stages (FLT)

A filter only works if its input and output can't see each other: put it at the board edge, in a straight line from connector to power stage, with Y-caps on the noisy side of the choke and the CM return short. Würth's 3-phase filter tests show more than 60 dB damping at 200 kHz with correct Y-cap placement, and far less with wrong placement ([Würth WE Days 2025](https://www.we-online.com/files/pdf1/design-calculation--simulaion-of-3-phase-acdc-filters_digital_we_days2025-v1.pdf)).

- **FLT-01 (Must)** Place the filter at the point where power enters or leaves, with the connector on one side and the power stage on the other. Input and output connectors go on opposite sides of the board ([Wolfspeed PRD-06752](https://assets.wolfspeed.com/uploads/2024/10/Wolfspeed_PRD-06752_PCB_Layout_Techniques_for_Discrete_SiC_MOSFETs_Application_Note.pdf)).
- **FLT-02 (Must)** Keep the filter's "dirty" and "clean" sides physically separated: no trace from the converter side runs past the connector side, and no plane spans both sides on any layer. A shared plane couples the noise around the filter.
- **FLT-03 (Must)** Remove ground and power planes under the common-mode choke on all layers ([ROHM 67AN064E](https://atta.szlcsc.com/upload/public/pdf/source/20250515/B6ED126B6A3159D21E35172D097BF573.pdf)). Plane copper under it adds winding-to-plane capacitance that bypasses the choke at high frequency.
- **FLT-04 (Must)** Put Y-caps on the converter side of the CM choke; a Y-cap on the line side gives CM current a path around the choke ([PCBSync CM choke guide](https://pcbsync.com/?p=31221)). When a Y-cap returns to DC−, route it straight to the bulk capacitor's negative node without sharing trace with other currents ([Power Integrations PI Expert](https://piexpertonline.power.com/help/piexpert/en/topics/emi_filter_components.htm)).
- **FLT-05 (Should)** Connect Y-caps to PE/chassis with the shortest, widest path to a plated chassis screw pad (under 10 mm, multiple vias). A 30 mm trace adds about 20–30 nH and kills Y-cap performance above \~10 MHz.
- **FLT-06 (Should)** Use two-stage filtering for DM: an X-cap / ceramic at the converter end and another at the connector end, with the DM inductance between. Orient the two caps so their loops don't face each other.
- **FLT-07 (Should)** Keep magnetic components of the filter at least 20–30 mm from power-stage magnetics, and rotate their axes 90° to each other to cut near-field coupling.
- **FLT-08 (Should)** Run the input lines through the filter as a tight pair (DC+ over DC−, or L next to N), never as a wide loop. Loop area between filter stages is an antenna.
- **FLT-09 (Must)** Respect leakage-current limits when sizing Y-caps (equipment and medical standards cap touch current); total line-to-PE capacitance is a safety number, not just an EMI number.
- **FLT-10 (Should)** Leave footprints for an extra Y-cap, a second X-cap and a ferrite on the first spin. Pre-compliance always finds something; spare pads cost nothing.
- **FLT-11 (Should)** Filter every cable that leaves the enclosure, including low-power signal and communication lines (CAN, RS-485, encoder). A clean power port with a noisy sensor cable still fails radiated emissions.

## 10. Thermal design (TH)

On FR-4, heat only leaves a device through copper: vias carry it vertically, planes spread it sideways, and both are far weaker than designers assume. FR-4 is close to an insulator through its thickness, so a hot SMD package with no vias under it is effectively thermally isolated from the bottom side.

**Calibration numbers** (worked from k\_Cu ≈ 390 W/m·K unless sourced)

| Path | Value | Note |
| --- | --- | --- |
| One hollow via, 0.3 mm drill, 25 µm plating, 1.6 mm board | ≈ 190 °C/W | [Atlas PCB](https://atlaspcb.com/blog/pcb-thermal-via-design) |
| Same via, copper-filled | ≈ 58 °C/W | about 3.3× better, same source |
| 16 hollow vias of that kind in parallel | ≈ 12 °C/W | array = single / N |
| Lateral spreading, 1 oz (35 µm) copper | ≈ 73 °C/W per square | R = 1 / (k·t), any square size |
| Lateral spreading, 2 oz (70 µm) | ≈ 37 °C/W per square |  |
| Lateral spreading, 4 layers of 2 oz in parallel | ≈ 9 °C/W per square | needs vias tying the layers |
| 2512 shunt self-heating | 40–60 °C/W above its pads | [PCBSync shunt guide](https://pcbsync.com/current-sense-resistor-guide/) |

The "per square" figure is the key insight: copper's sideways thermal resistance depends on the number of squares, not the size. A 10 mm × 30 mm strip of 1 oz copper is three squares, about 220 °C/W end to end. Planes spread heat well over the first 10–20 mm from a device and poorly beyond that, which is why remote copper does not cool a hot part.

**Rules**

- **TH-01 (Must)** Build a thermal budget per hot device before layout: P\_loss, T\_j,max, R\_th(j-c), TIM, PCB/vias, heatsink, ambient. Write the allowed PCB contribution as a number in °C/W and design the via array to meet it.
- **TH-02 (Must)** Fill the exposed thermal pad of every SMD power package with a via array. Wolfspeed's starting point is 0.4 mm vias at 0.8 mm pitch with 60 µm plating ([Wolfspeed PRD-06752](https://assets.wolfspeed.com/uploads/2024/10/Wolfspeed_PRD-06752_PCB_Layout_Techniques_for_Discrete_SiC_MOSFETs_Application_Note.pdf)); a common alternative is 0.3 mm at 0.8–1.0 mm pitch. Pitch beyond \~1.2 mm leaves dead zones.
- **TH-03 (Must)** Under SMD pads, specify filled and capped vias (IPC-4761 Type VII) or accept solder wicking and voids. Note it on the fab drawing; open vias in a pad pull solder away from the joint and raise R\_th unpredictably.
- **TH-04 (Should)** Specify heavier via plating (35–60 µm instead of 20–25 µm) for thermal arrays; more barrel copper lowers R\_th per via in proportion to its cross-section.
- **TH-05 (Must)** Tie every copper layer under a hot device into the via array with solid (no thermal relief) connections, and keep each layer's copper unbroken for at least 10–20 mm around the device. Clearance slots and anti-pads near the pad cut the spreading path.
- **TH-06 (Should)** Size traces with IPC-2152, not IPC-2221. IPC-2221's internal-layer chart was derived by halving the external chart rather than from measurements ([PCBSync IPC-2152 guide](https://pcbsync.com/?p=11006)); IPC-2152 shows a plane within about 0.5 mm of a trace raises its ampacity by around 40% at the same rise ([Salitronic calculator](https://salitronic.com/pcb-trace-width-calculator/)).
- **TH-07 (Must)** Add the temperature rise of neighbouring hot copper and parts to each trace's own rise. Final copper temperature must stay below the laminate's MOT/RTI and below each part's rated ambient.
- **TH-08 (Should)** For bottom-side cooling through the board, use an electrically isolating TIM between board and heatsink, and keep switch-node copper off the bottom layer (it raises CM capacitance, rule SW-02). For high loss density, step up to copper coins/inlays, IMS or AlN inserts; Wolfspeed compares these options on cost, isolation and thermal performance ([Wolfspeed PRD-06752](https://assets.wolfspeed.com/uploads/2024/10/Wolfspeed_PRD-06752_PCB_Layout_Techniques_for_Discrete_SiC_MOSFETs_Application_Note.pdf)).
- **TH-09 (Must)** Keep the board flat where it meets a heatsink: no tall bottom-side parts in the contact area, controlled TIM thickness, and screw positions on all sides of the device group. Board bow under the heatsink is the most common cause of one device in a parallel group running hot.
- **TH-10 (Should)** Derate passives for heat: electrolytic life roughly halves per 10 °C, so keep bulk capacitors out of the airflow behind heatsinks; use X7R/X7T or C0G rated for 125 °C+ in the hot zone; check resistor power derating curves at local board temperature, not 25 °C.
- **TH-11 (Should)** Place hot devices where cooling air exits or along the heatsink's coldest edge, spread across the heatsink rather than clustered, and orient fins along the airflow.
- **TH-12 (Must)** Verify with a thermal camera at full load on the first article (emissivity tape on shiny copper), and compare hot-spot temperatures to the budget. Log the hot-spot map with the inductance measurement from PL-10.

## 11. Thermal crosstalk (TX)

Thermal crosstalk is heat from the power stage shifting precision parts: references, shunts, op-amp inputs, matched resistors, isolators and electrolytics. Zone the board by temperature, break the copper paths that carry heat into the quiet zone, and put matched parts on the same isotherm. Every copper-to-different-metal junction is a thermocouple, so a gradient across a sense path appears as offset voltage ([Microchip AN1258 summary](https://developerhelp.microchip.com/xwiki/bin/view/products/amplifiers-linear/operational-amplifier-ics/precision-design/summary-and-references/)).

**Zoning**

| Zone | Typical board temperature | Belongs here | Keep out |
| --- | --- | --- | --- |
| Hot | 90–125 °C | Switches, magnetics, power shunts, snubber resistors | Electrolytics, references, isolators, MCU, crystals |
| Warm | 60–90 °C | Gate drivers, film/ceramic DC-link, bootstrap parts | Precision analog, references |
| Cool | ambient + 10–20 °C | MCU, references, sense amplifiers, ADCs, comms, bulk electrolytics | Anything dissipating > 0.5 W |

**Rules**

- **TX-01 (Must)** Draw the three zones on the placement drawing before placing parts, oriented to the airflow and heatsink. Place cool-zone parts upstream of airflow and away from board edges that touch hot metal.
- **TX-02 (Should)** Keep voltage references, precision op-amps and ADC front-ends at least 20–30 mm from any part dissipating more than 1 W, and not over a plane that is thermally tied to the heatsink area.
- **TX-03 (Should)** Break thermal paths between zones. Where a pour or plane runs from the hot zone into the cool zone only for electrical reasons, neck it down to the width the current needs, or slot it. Do not slot a reference plane under signals that cross the slot (GND-03); slot only spreading copper.
- **TX-04 (Must)** Give both terminals of a shunt equal copper area and equal heat paths, so both ends sit at the same temperature; the copper-solder-lead junctions form thermocouples and the copper mass should be equal on both inputs ([TI LMP8640 layout](https://ti.com/document-viewer/LMP8640-Q1/datasheet/layout)). Run the shunt's Kelvin pair along an isotherm, not across the gradient.
- **TX-05 (Should)** Choose shunt alloys with low thermal EMF against copper (manganin-type, around 1 µV/K) for low-mV signals ([Wevolver shunt article](https://www.wevolver.com/article/what-is-a-shunt-resistor-sizing-sensing-and-layout)). Expect copper-terminal shunts to show component TCR far above element TCR; budget for it or use 4-terminal parts.
- **TX-06 (Should)** Place matched resistors and op-amp input pairs side by side on the same isotherm, same orientation, so a gradient shifts both equally. Microchip recommends orienting critical parts along constant-temperature contours to cancel thermoelectric voltages ([Microchip AN1258 summary](https://developerhelp.microchip.com/xwiki/bin/view/products/amplifiers-linear/operational-amplifier-ics/precision-design/summary-and-references/)).
- **TX-07 (Should)** Keep isolators and optocouplers in the warm zone at most; optocoupler CTR and LED life fall with temperature, and digital isolator timing shifts.
- **TX-08 (Should)** Place NTCs for protection on the hot device's copper, and NTCs for compensation (shunt, reference) next to the part they compensate. Account for the sensor's thermal lag in protection limits.
- **TX-09 (Should)** Put paralleled switches on one isotherm of the heatsink and give them equal copper. MOSFETs (positive R\_DS(on) tempco) self-balance; IGBTs and diodes with negative tempco at low current can thermally run away if one sits hotter.
- **TX-10 (Prefer)** Shield precision analog parts from fan air and convection currents; moving air over input pins creates fluctuating gradients that show up as low-frequency noise.
- **TX-11 (Should)** Check mechanical crosstalk too: large MLCCs and BGAs next to hot zones see the most thermal-cycle stress. Keep them away from screw points and orient large MLCCs parallel to the likely bend line.

## 12. High-current routing (HC)

Count squares, not millimetres: copper resistance scales with length ÷ width, and at 100 A every milliohm is 10 W. 1 oz copper is about 0.5 mΩ per square at 25 °C (2 oz about 0.25 mΩ). A 10 mm × 50 mm path in 2 oz is 5 squares, 1.25 mΩ, and dissipates 12.5 W at 100 A, more than many of the switches. Above roughly 50–80 A, etched copper alone usually stops being the right conductor.

**Choosing the conductor**

| Option | Practical range | Strengths | Watch out for |
| --- | --- | --- | --- |
| Standard copper, 1–2 oz, multiple layers | up to \~30–50 A | Cheap, fine pitch OK | Squares add up fast; necks at vias and pads |
| Heavy copper, 3–10 oz | \~50–150 A | No assembly step | Coarse spacing everywhere on that layer; cost; etch tolerances |
| Solderable copper strips / SMD busbars on the surface | \~50–300 A | Add copper only where needed, keep 1–2 oz board; good heat spreading | Reflow thermal mass, joint fatigue, loop inductance, creepage of bare metal |
| Embedded wires (Wirelaid) | \~20–100 A per track | Thick-copper tracks on a thin-copper board | Fab-specific process, design rules from the fab |
| Embedded busbar / copper inlay laminated in | \~100–300 A+ | Lowest inductance and best thermal coupling | Specialist fab, long lead time, cost |
| Bolted busbar, press-fit or screw terminals | 100 A+ | Serviceable, high current | Mechanical design, contact resistance, torque control |

**Solderable copper strips (surface busbars)**

These are tinned copper bars or SMD high-current elements soldered onto exposed pads to add copper cross-section along a power path. Typical bar thickness is 1–3 mm, used for roughly 50–300 A, with tin coatings usually 1–10 µm for solderability ([Best Technology bus bar guide](https://www.bestpcbs.com/blog/2026/02/pcb-bus-bar/)). Parts that run on standard SMT lines also exist, such as Würth's PowerOne SMD high-current elements processed in reflow or vapour-phase ovens ([Electronic Specifier](https://www.electronicspecifier.com/products/power/smd-offers-alternative-to-press-in-through-hole-technology)). The embedded alternative, Wirelaid, welds copper wire under the foil: Würth's example cuts a 20 A track at 20 K rise from 8.9 mm to 1.9 mm wide on 35 µm base copper ([Würth Wirelaid presentation](https://dtsheet.com/doc/1484538/wirelaid---bypasses-for-high-currents--pdf-)).

- **HC-01 (Should)** Use strips where the current path is long and straight (DC bus runs, battery/phase connections), not inside the commutation loop. A strip lifts current 1–3 mm off the board, away from its L2 return, which raises loop inductance and radiated field.
- **HC-02 (Must)** If a strip carries DC+ (or DC−), keep its partner directly underneath: mirror strip on the bottom side, or a solid opposite-polarity plane on L2. The pair must stay overlapped like any DC bus (PL-05).
- **HC-03 (Must)** Solder the strip along its whole length to an exposed pad of the same shape (solder mask opened), with the PCB copper beneath sized to carry the current alone if the joint cracks at one end. Design for graceful degradation, not a fuse.
- **HC-04 (Should)** Segment long pads into several sections with mask dams to control solder volume and voids, and add through-hole tabs, pins or screws every 30–50 mm as mechanical anchors. Solder alone should not hold a 50 g bar under vibration.
- **HC-05 (Should)** Limit the length of rigid bars on FR-4 (roughly under 60–80 mm per piece) or add a stress-relief bend; copper and FR-4 expand differently in Z and slightly in X/Y, and long rigid joints fatigue under thermal cycling.
- **HC-06 (Must)** Plan assembly: a 2 mm copper bar is a big heat sink during reflow. Agree the profile with the assembler, use selective soldering or a second pass if needed, and inspect joints by X-ray on the first build.
- **HC-07 (Must)** Treat the bar as bare high-voltage metal: measure creepage and clearance from its top and edges to neighbouring nets and to the enclosure, which are usually closer than to PCB copper.
- **HC-08 (Should not)** Do not rely on "solder-filled" exposed traces (mask opening plus extra solder) as a current rating. Solder conductivity is roughly 1/8 of copper's and the thickness is uncontrolled; treat it as margin only.

**General high-current rules**

- **HC-09 (Must)** Check every neck: via fields, pad entries, gaps between mounting holes and plane cut-outs. Current crowds at corners and narrow points; the neck sets the hot spot, not the average width.
- **HC-10 (Should)** Use many small vias rather than few large ones for layer transitions. A 0.3 mm via with 25 µm plating carries about 1 A at 10 °C rise on the conservative IPC-2221 internal model ([MiniWebtool via calculator](https://miniwebtool.com/ko/pcb-비아-전류-계산기/)); size arrays at 1–2 A per via and spread them across the full width of the path.
- **HC-11 (Must)** No thermal reliefs on high-current pads and via fields; use solid connections, and tell the assembler so they can adjust preheat.
- **HC-12 (Must)** Feed paralleled devices symmetrically: equal path length and squares from the DC-link to every device, and a balanced return. Wolfspeed stresses similar power-path lengths and balanced drain/source stray inductance for current sharing ([Wolfspeed PRD-06752](https://assets.wolfspeed.com/uploads/2024/10/Wolfspeed_PRD-06752_PCB_Layout_Techniques_for_Discrete_SiC_MOSFETs_Application_Note.pdf)). A star or "distributed block" arrangement beats a daisy chain.
- **HC-13 (Should)** Rate connectors and terminals at the board's hot-spot temperature, not 25 °C, and put several pins or a press-fit/screw terminal rather than one large pin. Connector contact resistance often dominates the loss budget.
- **HC-14 (Should)** Use multiple layers in parallel for each high-current net, tied with via fields every 10–20 mm; the layers share current only where vias force them to.
- **HC-15 (Should)** Measure milliohm drops across the main power path (4-wire) on the first article at rated current and compare with the squares calculation. A gap of 30% or more usually means a neck or a bad joint.

## 13. Creepage, clearance and insulation (HV)

The end-product safety standard sets the spacing (IEC 61800-5-1 for drives, IEC 62109-1 for PV inverters, IEC 62368-1 for ICT/AV, IEC 60335-1 for appliances, ISO 6469/ISO 21498 for EV); IEC 60664-1 is the basis most of them build on. Look up the product standard first, then use the tables below for planning.

**Creepage, basic insulation, IEC 60664-1 basis (mm)** — from [UL 840 / IEC 60664 summary](https://www.scribd.com/document/530957488/Creepage-Distances-EN-1) and [HARTING](https://b2b.harting.com/files/livebooks/en/PRD0200000100181/downloads/livebook.pdf#page=1)

| Working voltage (V rms or DC) | PD1, printed board | PD2, group I (CTI ≥ 600) | PD2, group II (CTI 400–600) | PD2, group III (CTI 100–400) | PD3, group III |
| --- | --- | --- | --- | --- | --- |
| 250 | 0.56 | 1.25 | 1.8 | 2.5 | 4.0 |
| 400 | 1.0 | 2.0 | 2.8 | 4.0 | 6.3 |
| 630 | 1.8 | 3.2 | 4.5 | 6.3 | 10.0 |
| 800 | 2.4 | 4.0 | 5.6 | 8.0 | 12.5 |
| 1000 | 3.2 | 5.0 | 7.1 | 10.0 | 16.0 |

**Clearance (PD2, inhomogeneous field, ≤ 2000 m)** follows the rated impulse voltage: 2.5 kV → 1.5 mm, 4 kV → 3.0 mm, 6 kV → 5.5 mm, 8 kV → 8.0 mm ([UL 840 / IEC 60664 summary](https://www.scribd.com/document/530957488/Creepage-Distances-EN-1)). An 800 V DC bus in overvoltage category II needs a 6 kV impulse rating, so 5.5 mm, rising to about 6.8 mm at 3000 m ([batterydesign.net](https://www.batterydesign.net/creepage-and-clearance-calculator/)).

**Rules**

- **HV-01 (Must)** Identify the product standard, pollution degree, overvoltage category, altitude, insulation type (functional, basic, reinforced) and the laminate CTI before layout. Write them on the schematic title block.
- **HV-02 (Must)** Reinforced insulation is about double basic creepage, with clearance from the next impulse step up. Mains-to-SELV and isolated-gate-driver barriers usually need reinforced.
- **HV-03 (Must)** Creepage is set by RMS/DC working voltage; clearance by peak and impulse. Use the real peak including switching overshoot (PL-06) for functional spacing between switch node and DC rails.
- **HV-04 (Should)** For functional spacing on internal power nets, IPC-2221 and IPC-9592 give planning numbers. At an 850 V bus, Wolfspeed quotes 2.6 mm from IPC-2221 (B4) and 4.85 mm from IPC-9592's 0.6 + 0.005 × V\_peak rule ([Wolfspeed PRD-06752](https://assets.wolfspeed.com/uploads/2024/10/Wolfspeed_PRD-06752_PCB_Layout_Techniques_for_Discrete_SiC_MOSFETs_Application_Note.pdf)). Use the larger unless a standard says otherwise.
- **HV-05 (Should)** Use slots to gain creepage under parts and between pads. Minimum slot width is 0.25 mm for PD1 and 1 mm for PD2 per IEC 60664-1 ([Wolfspeed PRD-06752](https://assets.wolfspeed.com/uploads/2024/10/Wolfspeed_PRD-06752_PCB_Layout_Techniques_for_Discrete_SiC_MOSFETs_Application_Note.pdf)). Slots under isolators and between TO-247 legs are common.
- **HV-06 (Should)** Prefer oval pads to round or rectangular ones on high-voltage legs; Wolfspeed recommends elliptical pads to increase creepage and avoid corner field concentration ([Wolfspeed PRD-06752](https://assets.wolfspeed.com/uploads/2024/10/Wolfspeed_PRD-06752_PCB_Layout_Techniques_for_Discrete_SiC_MOSFETs_Application_Note.pdf)).
- **HV-07 (Must)** Apply spacing to everything tied to a high-voltage pad: snubbers, divider resistors, test points, Y-caps, strips (HC-07), heatsink edges and screw heads. Through-hole devices need spacing from their bent legs to the heatsink; extend the insulating pad past the bend where needed.
- **HV-08 (Must)** Inner layers: keep pull-back from plated holes of the other potential to the solid-insulation requirement, and confirm the prepreg thickness meets the distance-through-insulation rule of the standard for that insulation type.
- **HV-09 (Should)** Conformal coating can lower the pollution degree for the coated area (allowing PD1 spacing) only if the standard and process qualify it; plan for PD2 spacing and treat coating as margin unless the safety engineer signs off.
- **HV-10 (Should)** Above about 600 V, check partial discharge: sharp copper corners, voids in thick prepreg and air gaps under large parts can start PD long before breakdown. Round high-voltage copper corners and specify PD testing for reinforced barriers.

## 14. Checklists

Run these at three gates: before placement, at layout review, and on the first article. Each item points to the rule it checks.

**Before placement**

- [ ] Product safety standard, PD, OVC, altitude, insulation types and laminate CTI recorded (HV-01)
- [ ] Stackup drawn with dielectric thicknesses, copper weights and hipot voltage (STK-01, STK-02, STK-09)
- [ ] Loop inductance budget and overshoot check written for each half-bridge (PL-06)
- [ ] Ground domains named in the schematic; star point chosen (GND-01, GND-02)
- [ ] Heatsink connection decided and drawn (SW-06)
- [ ] Thermal budget per hot device, with allowed PCB °C/W (TH-01)
- [ ] Hot / warm / cool zones drawn on the placement plan (TX-01)
- [ ] Conductor type chosen for each > 30 A path: layers, heavy copper, strips or busbar (HC table)

**Layout review**

- [ ] Commutation loops vertical, return on L2, HF caps within 3–5 mm, via fields at inner pad edges (PL-02, PL-03, PL-04)
- [ ] Gate loops < 20 mm, stacked pairs, Kelvin return only, no overlap with switch node on any layer (GD-01–GD-03, GD-08)
- [ ] Isolation gaps clean on every layer; CMTI and barrier capacitance checked (GD-12, GD-13)
- [ ] Switch-node copper minimal, single layer, ≥ 10 mm from edges and filter (SW-01, SW-02, SW-05)
- [ ] No power return current crosses SGND; no signal crosses a plane split (GND-03, GND-04)
- [ ] Shunts 4-wire, sense pairs over SGND, equal copper both ends (SNS-01, SNS-02, TX-04)
- [ ] Filter at the edge, planes removed under CM choke, Y-caps on converter side (FLT-01–FLT-04)
- [ ] Thermal via arrays filled/capped under pads, solid connects, spreading copper unbroken (TH-02, TH-03, TH-05)
- [ ] Every neck in high-current paths checked; via counts sized; paralleled devices fed symmetrically (HC-09, HC-10, HC-12)
- [ ] Copper strips paired with their return and anchored; reflow plan agreed (HC-02, HC-04, HC-06)
- [ ] Creepage/clearance DRC run with net classes, including inner layers, strips and heatsink (HV-07, HV-08)

**First article bring-up**

- [ ] Loop inductance from ringing frequency at each device; compared with budget (PL-10)
- [ ] V\_DS overshoot and gate waveform at worst-case current and temperature, with a low-inductance probe tip
- [ ] Pre-compliance conducted (LISN) and near-field probe scan of switch nodes, heatsink and cables
- [ ] Thermal camera map at full load, hot spots vs budget (TH-12)
- [ ] 4-wire mΩ drop along main power paths vs calculation (HC-15)
- [ ] Current-sense offset and gain checked cold and after thermal soak (TX-04, TX-05)
- [ ] Hipot and, above \~600 V, partial-discharge test on isolation barriers (HV-10)

## 15. Sources

Vendor application notes

- [Wolfspeed PRD-06752: PCB Layout Techniques for Discrete SiC MOSFETs (Rev. 01, 2024)](https://assets.wolfspeed.com/uploads/2024/10/Wolfspeed_PRD-06752_PCB_Layout_Techniques_for_Discrete_SiC_MOSFETs_Application_Note.pdf)
- [TI SNVA803: Improve High-Current DC/DC Regulator EMI Performance with Optimized Power Stage Layout](https://www.ti.com/lit/ab/snva803/snva803.pdf)
- [TI LMG3522R030-Q1 datasheet, layout section (loop inductance from ringing)](https://www.ti.com/document-viewer/lit/html/SNOSD97D/GUID-96BDA46B-3B08-428F-8CE2-8294C8E9702E)
- [TI LMP8640 datasheet, layout (Kelvin sensing, thermocouple junctions)](https://www.ti.com/document-viewer/LMP8640/datasheet/layout)
- [ROHM 67AN006E: SiC MOSFET Layout Design Considerations](https://fscdn.rohm.com/en/products/databook/applinote/discrete/sic/mosfet/sic_mosfet_layout_design_considerations_an-e.pdf)
- [ROHM 67AN064E: Step-down DC-DC PCB layout EMC design guide](https://atta.szlcsc.com/upload/public/pdf/source/20250515/B6ED126B6A3159D21E35172D097BF573.pdf)
- [Infineon: Electric and thermal evaluation of SiC discrete packages](https://www.infineon.com/assets/row/public/documents/10/42/infineon-comprehensive-electric-and-thermal-evaluation-of-sic-discrete-packages-applicationnotes-en.pdf)
- [EPC: Layout Considerations for GaN Transistor Circuits (Bodo's Power, 2021)](https://epc-co.com/epc/Portals/0/epc/documents/articles/Layout%20Considerations%20for%20GaN%20-%20Bodo%20January%202021.pdf)
- [EPC / Würth Power Day 2024: GaN FET Layout](https://we-online.com/files/pdf1/epc_wurth-day_boston_part1_layout_2024-10-22-boston-2024.pdf)
- [EPC Design Tips webinar (loop inductance vs efficiency)](https://epc-co.com/epc/Portals/0/epc/documents/presentations/EPC%20Design%20Tips%20Mandarin%20Webinar.pdf)
- [Würth: Design, Layout and Simulation of 3-Phase AC/DC Filters (2025)](https://www.we-online.com/files/pdf1/design-calculation--simulaion-of-3-phase-acdc-filters_digital_we_days2025-v1.pdf)
- [Würth: Wirelaid, bypasses for high currents](https://dtsheet.com/doc/1484538/wirelaid---bypasses-for-high-currents--pdf-)
- [Microchip AN1258 summary: op-amp precision PCB layout](https://developerhelp.microchip.com/xwiki/bin/view/products/amplifiers-linear/operational-amplifier-ics/precision-design/summary-and-references/)
- [Power Integrations PI Expert: EMI filter components](https://piexpertonline.power.com/help/piexpert/en/topics/emi_filter_components.htm)

Research and patents

- [Grobler & Gitau: heatsink effects on EMI in converters (Univ. Pretoria, 2017)](https://repository.up.ac.za/bitstream/2263/61590/1/Grobler_Analysis_2017.pdf)
- [Derkacz et al.: EMI mitigation of GaN inverter leg by local shielding (IEEE TPEL)](https://pub.pg.edu.pl/en/download/157957-emi-mitigation-of-gan-power-inverter-leg-by-local-shielding-techniques)
- [CPES Virginia Tech: integrated common-mode screen for SiC modules](https://cpes.vt.edu/library/viewnugget/1125/)
- [US 7602159: CM noise and switch-to-heatsink capacitance](https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/7602159)
- [US 12648448: shielding layer between switch and grounded radiator](https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/12648448)

Standards references and calculators

- [UL 840 / IEC 60664 creepage and clearance tables](https://www.scribd.com/document/530957488/Creepage-Distances-EN-1)
- [HARTING: IEC 60664-1 creepage and clearance tables](https://b2b.harting.com/files/livebooks/en/PRD0200000100181/downloads/livebook.pdf#page=1)
- [batterydesign.net: creepage and clearance calculator (altitude correction)](https://www.batterydesign.net/creepage-and-clearance-calculator/)
- [PCBSync: IPC-2152 explained](https://pcbsync.com/?p=11006) and [Salitronic trace-width calculator](https://salitronic.com/pcb-trace-width-calculator/)
- [Atlas PCB: thermal via design](https://atlaspcb.com/blog/pcb-thermal-via-design)
- [PCBSync: current sense resistor guide](https://pcbsync.com/current-sense-resistor-guide/) and [Wevolver: shunt sizing and layout](https://www.wevolver.com/article/what-is-a-shunt-resistor-sizing-sensing-and-layout)
- [Best Technology: PCB bus bars](https://www.bestpcbs.com/blog/2026/02/pcb-bus-bar/) and [Electronic Specifier: Würth PowerOne SMD](https://www.electronicspecifier.com/products/power/smd-offers-alternative-to-press-in-through-hole-technology)

Numbers marked as worked examples come from the formulas in section 2 and section 10; inductance budget ranges are engineering rules of thumb, not vendor specifications. Safety spacing must be confirmed against the end-product standard.
