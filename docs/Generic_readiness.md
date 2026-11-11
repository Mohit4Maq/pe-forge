# Ready for any request: what applies to each project class (2026-11-12)

A customer may ask for any power-electronics product. The pilot is a 48 V to 12 V buck; the tool must not be
tuned to it. This is the matrix of what the tool does per class, from the words and `[specs]` of the request
(`forge new --types` lists the specs each class needs). Everything marked **check** is deterministic code with a
test; **sim** is a required simulation the gate demands; **owner** is a decision the tool asks rather than guesses.

| Class (`project.type`) | Topology inferred (`forge calc design`) | Closed forms (`forge calc`) | Required simulations (G5) | Ratings vs corners (`forge stress`) | Enclosure entries |
|---|---|---|---|---|---|
| dc-dc (non-isolated) | buck / boost / buck-boost from Vin vs Vout | D, ΔI_L, I_pk, I_L,rms, ΔV_out, I_C,rms, CCM | ripple, peak current, output ripple, efficiency, loop stability, start-up, short / overload | DC rails, switch node 0..Vin | glands / M12 by current and voltage |
| dc-dc-isolated | flyback (< 150 W) / forward; LLC and DAB by name | flyback / forward (turns ratio, V_ds off, reflected, secondary peak), DAB power vs phase shift | + transformer flux / B_max, switch stress (leakage spike), primary / secondary peaks | primary domain and secondary domain (GND_SEC / ISO_GND) as separate grounds; reflected voltages via the closed form | isolated secondary: SELV entries; primary at hazardous voltage: PE path required |
| ac-dc | pfc (+ isolated stage) | PFC: line current, inductor peak, 2f ripple; flyback / forward / LLC for the second stage | THD / PF, inrush, hold-up, ripple, efficiency, loop stability, short / overload, surge | mains nets swing ±Vac,max·√2 (L, N, LINE, AC_*); DC link from `vdc_link_V`; X caps judged at the peak | PE required on every mains entry (gland-PE, IEC inlet, Han), PE stud on a metal box |
| pfc | pfc / totem-pole | as above | THD / PF, efficiency, inductor ripple, inrush, fault | as above | as above |
| dc-ac (inverter) | inverter (H-bridge) / threelevel (T-type, NPC) | fundamental peak / rms, I_1, DPF | THD, efficiency, DC-link ripple, device losses / T_j, start-up / precharge, fault / short | DC link (`vdc_link_V`), AC output nets U / V / W / PH_A..C swing ±V_ac,out·√2 | AC output entries with PE; PV input MC4 at up to 1500 V |
| motor-drive | (owner: topology is the drive stage, inverter leg) | inverter closed forms at the phase voltage | phase ripple / THD, DC-link ripple, losses / T_j, stall / locked rotor, regeneration bus rise, short / desaturation | DC link; phase nets swing 0..V_dc | motor entries (M12-L power, Han), PE |
| charger | (owner: buck / boost / isolated by Vin vs V_bat) | the DC-DC closed form of the chosen stage | CC regulation, CV regulation, ripple, efficiency, loop stability, reverse / battery fault, pre-charge | DC rails, battery terminal at V_bat max | as DC-DC; reverse-polarity entry note |
| other | none (owner sets `[specs] topology`) | by topology when set | efficiency, start-up / inrush, fault / short / overload | DC rails | by voltage class |

Common to every class, independent of the type: intake with both corners, requirements and V-model, the
library rule (every part from MC_Parts with evidence), `forge drift`, `forge protection` (every clamp), `forge
insulation` (any net in the HV class), the high-power rule book (on at ≥ 50 V or ≥ 5 A), copper / connectors /
crosstalk, footprint pins / polarity / rotation, 3D models, the fab-house rules, test points, supply risk, and
the gates with the owner in the loop.

## What the tool asks rather than assumes

- the topology when the words and specs do not fix it (motor drive, charger, "other");
- the turns ratio and the transformer design for isolated stages (a magnetics design note, Rule 1 anchored);
- the grid standard and anti-islanding method for a grid-tied inverter (compliance intake);
- the IP code, material, mounting, connector family (enclosure requirements);
- any waiver of a required simulation or a Must rule.

## Known limits (named, not hidden)

- LLC: only the resonant frequency and gain at resonance are closed form; the design relies on pwrlab / LTspice.
- Three-level NP ripple and DC-link capacitor rms come from the simulation (pwrlab's model), not a closed form here.
- Motor drives: no motor model in the tool; stall and regeneration cases are required simulations with the
  owner's motor parameters.
- Three-phase grid filters (LCL) and EMI filters are designed in the booklets with closed forms per note; the tool
  checks the parts' ratings and the layout rules, not the filter transfer function (pwrlab `spectrum` does).
