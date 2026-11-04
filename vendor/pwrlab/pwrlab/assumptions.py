"""What each model actually represents, and what it does not.

This exists because the accuracy figures elsewhere answer a narrower question
than they appear to. "0.04 % error against closed-form theory" means the
SOLVER reproduces MY MODEL of an idealised circuit. It does not mean the
idealised circuit represents real hardware. Those are different claims, and
only the first is validated by the check battery.

The gap is measurable. With every device ideal and no dead time, the three-phase
T-type returns 0.14 % current THD. Published measurements of real T-type
hardware report ~1.36 % at 20 kHz. The model was not wrong about the circuit it
described; it was describing a circuit nobody builds.
"""
from __future__ import annotations

# --------------------------------------------------------------------------- #
COMMON_NOT_MODELLED = [
    "Device output capacitance (Coss) and gate-drive dynamics: switching "
    "transitions are instantaneous, so dv/dt, EMI and Coss-related soft "
    "switching are absent.",
    "Diode reverse recovery in the power stage. Switching loss is applied in "
    "post-processing from datasheet energies, not produced by the waveform.",
    "Magnetics: inductors are linear, lossless except for an explicit Rser. No "
    "core loss, no saturation, no proximity or skin effect.",
    "Closed-loop control. Everything is open loop except the PFC's hysteresis "
    "current loop, so no bandwidth, stability or disturbance-rejection result "
    "is available.",
    "Thermal transients: a single steady-state Rth chain, no Zth curve, so "
    "short overloads and cycling are not represented.",
    "Parasitics: no busbar or loop inductance, no PCB resistance, no ESL.",
]

TOPOLOGY = {
    "ttype3ph": {
        "what_is_simulated": (
            "Three-phase three-level T-type leg set driven by phase-disposition "
            "sinusoidal PWM, feeding a balanced star-connected R-L load with a "
            "floating neutral. Each leg: two rail switches plus a bidirectional "
            "neutral-point pair. DC link is two capacitors about a grounded "
            "midpoint, fed from a stiff source or through a DC feed inductance."
        ),
        "modelled": [
            "Three-level switching with the correct state sequence (+, 0, -).",
            "Modulation strategies: sinusoidal (spwm), third-harmonic injection "
            "(thipwm) and min-max / space-vector common mode (svpwm). Injection "
            "extends the linear range from M = 1.0 to 1.155 (verified to "
            "+0.00 % on line-to-line voltage at M = 1.15) and cuts the DC-link "
            "capacitance needed for a given neutral-point ripple by ~2.5x. "
            "Line-to-line voltage is identical across strategies, as it must be.",
            "Neutral-point current and its voltage excursion, with the "
            "capacitors as real components rather than ideal sources.",
            "DC-link capacitor ripple current, split by frequency band.",
            "Dead time and antiparallel freewheel diodes (deadtime > 0), "
            "including the T-type-specific behaviour that during dead time the "
            "output clamps to a RAIL, because the neutral-point path needs an "
            "active device.",
            "DC-link balancing resistors, giving the midpoint a DC reference.",
        ],
        "not_modelled": [
            "Three-level DISCONTINUOUS modulation (DPWM). The two-level form is "
            "explicitly rejected: applied to a three-level leg it shifts the "
            "reference into one carrier band and abandons the middle state "
            "(measured V_ll 554 -> 397 V, THD 0.32 -> 15.0 %). Real three-level "
            "DPWM clamps to the nearest LEVEL via redundant small-vector "
            "states, which is a different algorithm and is not implemented.",
            "Active neutral-point balancing that exploits redundant states.",
            "Active neutral-point balancing control (redundant vector "
            "selection). The NP here self-balances passively.",
            "An LCL or LC output filter and a grid connection. The load is a "
            "lumped R-L, so THD is not comparable with grid-code measurements "
            "made after a filter.",
            "Separate transistor and diode conduction paths. Switches are "
            "bidirectional resistances, so current does not commutate into a "
            "diode with a knee voltage. Loss DISTRIBUTION between transistor "
            "and diode is therefore not represented, even though total "
            "conduction loss is approximately right.",
            "Minimum pulse width suppression and carrier synchronisation.",
        ],
        "trustworthy": [
            "Fundamental voltage, RMS voltage, fundamental current, real power "
            "- all set by the switching function, validated to <0.1 %.",
            "Neutral-point voltage ripple: 1.4 % vs closed form under "
            "sinusoidal PWM. Under injection the closed-form model degrades to "
            "7-19 % and the tolerance is widened to 25 % to say so.",
            "DC-link capacitor RMS ripple current (0.45 % vs closed form).",
            "Dead-time voltage error dV1 (1.6 % vs closed form) when "
            "deadtime > 0.",
        ],
        "not_trustworthy": [
            "THD with deadtime = 0. The idealised model gives ~0.14 %, roughly "
            "10x better than real hardware. Set a realistic dead time.",
            "Efficiency and loss split from the power stage alone: use the "
            "`losses` tool, and substitute real datasheet devices first.",
            "Anything about control dynamics or grid interaction.",
        ],
        "loss_model": {
            "how_it_works": (
                "Power stage simulated with ideal switches; loss computed in "
                "post-processing from the resulting per-device currents plus "
                "datasheet energies. Rds(on)/Tj solved as a coupled fixed point."
            ),
            "device_rating_vs_switched_voltage": (
                "T-type RAIL switches (S1/S4) must BLOCK the full bus - with S4 "
                "on, S1 stands off the entire link - so an 800 V system needs "
                "1200 V parts there, while the neutral-point pair and every NPC "
                "device need only Vdc/2. But the voltage COMMUTATED at a "
                "transition is Vdc/2 everywhere, and that is what scales "
                "E_on/E_off. Conflating the two erases the topologies' "
                "trade-off entirely."
            ),
            "bidirectional_pair": (
                "The T-type neutral-point pair shares one commutation: only the "
                "transistor whose forward direction matches the current "
                "switches, while its partner's antiparallel diode conducts. "
                "Charging both doubles that contribution."
            ),
            "freewheel_diodes": (
                "With dead time modelled, the antiparallel diodes conduct "
                "through every dead-time interval at a forward drop several "
                "times the transistor's on-state voltage. Charging them added "
                "~13 W (21 %) to an 11 kW SiC T-type at 10-20 kHz -- loss that "
                "was previously missing entirely, and which grows with "
                "switching frequency."
            ),
            "clamping_diodes": (
                "NPC's clamping diodes carry the whole zero-state current and "
                "are counted. Omitting them understated NPC conduction loss by "
                "roughly 40 W in the reference case and hid the crossover."
            ),
            "still_approximate": [
                "Switches are bidirectional resistances, so current does not "
                "commutate into an antiparallel diode with a knee voltage. "
                "Total conduction loss is about right; the transistor/diode "
                "SPLIT within a position is not.",
                "Switching energy comes from datasheet points scaled by power "
                "laws (I^1, V^1.0-1.3), not from the simulated waveform.",
                "No reverse recovery in the power stage, no Coss, no gate-drive "
                "dynamics.",
            ],
            "validated_behaviour": (
                "Switching loss scales linearly with fs (8.8x measured over an "
                "8.3x frequency range); conduction loss is independent of it "
                "(<0.1 % drift). T-type wins below ~8 kHz and NPC above, "
                "against a published crossover near 10 kHz."
            ),
        },
        "benchmark": {
            "published": "T-type, 20 kHz, near full load: 99.29 % efficiency, "
                         "1.36 % THD; two-level baseline 98.43 % / 3.11 %.",
            "efficiency_crossover": (
                "T-type superior up to ~10 kHz, NPC superior above "
                "(Schweizer & Kolar). This model: T-type wins at 3 and 6 kHz, "
                "NPC from 10 kHz upward - crossover near 8 kHz."
            ),
            "source": "https://www.mdpi.com/1996-1073/19/17/4163 ; "
                      "Schweizer & Kolar, IEEE TPEL, T-type converter design",
            "this_model": {
                "_correction_2026_09_13": (
                    "The figures previously listed here (0.14 / 0.53 / 1.06 / "
                    "1.65 %) were WRONG and, worse, three of the four came from "
                    "runs that had FAILED their own ERROR-level checks. "
                    "analytical() predicted the ideal fundamental regardless of "
                    "dead time while the netlist simulated the real one, so at "
                    "2 us they disagreed by 9.2 % on V_1 and 17.5 % on P and "
                    "every comparison check failed. deadtime_voltage_error() "
                    "already computed the correction and analytical() simply "
                    "never called it. Now wired; all values below are from runs "
                    "with usable=true."),
                "deadtime=0": "0.303 % THD_i - idealised",
                "deadtime=1us": "0.596 % THD_i",
                "deadtime=2us": "1.113 % THD_i",
                "deadtime=3us": "1.683 % THD_i",
                "note": ("ttype3ph at mf=200 (10 kHz carrier), default load. "
                         "NPC agrees with T-type to within 0.01 % on all four, "
                         "as it must: the two produce the same output waveform "
                         "and differ in device stress, not in output."),
            },
            "hardware_references_2026_09_13": {
                "TI TIDA-01606 (TIDUE53J)": (
                    "11 kW, 3-phase, 400 V L-L, 800 V DC, SiC, 50-90 kHz. "
                    "Efficiency 98.6 %, THD < 3 % at 11 kW."),
                "ST UM3564": (
                    "50 kW T-type, 650-850 V DC, 380 V L-L. Peak efficiency "
                    "98.89 % at 800 V and 99.10 % at 650 V, THD < 3 % at full "
                    "load, PF 0.99."),
                "result": (
                    "THD: SPEC_BOUND pass -- 0.85 % at 20 kHz with 0.3 us dead "
                    "time, against a published < 3 % limit. A bound is not a "
                    "measurement: this shows the model is not contradicted by "
                    "the specification, NOT that it agrees with hardware. "
                    "Loss: BOUNDED_OPTIMISM pass -- model loss fraction is "
                    "1.3x-2.6x optimistic against TI's 1.4 %, which is expected "
                    "and required, since the model has no magnetics, gate-drive, "
                    "control, filter or busbar loss and uses a representative "
                    "device library. Loss rises monotonically with switching "
                    "frequency (0.542 % -> 0.705 % -> 1.056 % at 10/20/40 kHz)."),
            },
            "interpretation": (
                "With dead time in the normal IGBT range (2-3 us) the model "
                "brackets the published 1.36 %. The residual difference is "
                "expected: the benchmark is measured after an output filter, "
                "on real devices, with a control loop. Use dead time matched to "
                "your gate driver rather than the default of zero."
            ),
        },
        "to_make_it_more_realistic": [
            "Set `deadtime` to your actual gate-driver value (IGBT 1-3 us, "
            "SiC 0.2-0.5 us) and `Vf_diode` to the freewheel diode drop. This "
            "is the single largest fidelity gain and it is already supported.",
            "Use `losses` with datasheet device parameters rather than the "
            "representative library.",
            "For grid-code THD, add an LC or LCL output filter - not yet "
            "implemented; currently the R-L load does the filtering.",
            "For DC bus utilisation and NP current matching SVPWM papers, "
            "zero-sequence injection would need to be added to the modulator.",
            "Separate transistor and diode conduction paths would be needed "
            "before per-device loss distribution could be trusted.",
        ],
    },
    "pfc": {
        "what_is_simulated": (
            "Totem-pole bridgeless PFC: a fast leg switched at the rate the "
            "hysteresis comparator demands, and a line-frequency slow leg that "
            "selects the AC polarity. The boost inductor sits between the line "
            "and the fast-leg midpoint. The current reference is a sine in "
            "phase with the line, scaled for the requested throughput, so the "
            "displacement factor is unity by construction. Output is a bulk "
            "capacitor feeding a fixed resistor."
        ),
        "modelled": [
            "Current shaping by hysteresis control, with the band enforced as "
            "a real comparator hysteresis rather than assumed.",
            "The totem-pole property that matters: one control law works in "
            "both half cycles, because the inductor sits between the line and "
            "the fast-leg midpoint.",
            "Second-harmonic bus ripple, exactly, including the fact that it "
            "is the term that sizes the bulk capacitor in ANY single-phase PFC.",
            "Line-current spectrum, power factor and displacement factor from "
            "the actual waveform, not from an assumed shape.",
            "Boost feasibility: the model refuses an output below the AC peak.",
        ],
        "not_modelled": [
            "THE VOLTAGE LOOP. There is no outer regulator: the current "
            "reference is set from the REQUESTED power, not from a bus-voltage "
            "error. The bus therefore does not regulate -- on a load step it "
            "droops to a new level and stays there. No bandwidth, phase "
            "margin, overshoot or settling figure exists for this model, and "
            "any number resembling one would be meaningless.",
            "Fixed switching frequency. Hysteresis control gives a frequency "
            "that varies over the line cycle, so EMI filter design and "
            "frequency-dependent loss estimates do not transfer to a "
            "fixed-frequency average-current-mode design.",
            "Zero-crossing behaviour of a real controller: no minimum on-time, "
            "no blanking, no slow-leg dead time, and the slow leg commutates "
            "instantaneously at the zero crossing.",
            "Input EMI filter, and the line impedance it interacts with. THD "
            "here is at the converter terminals, not at the point of common "
            "coupling.",
            "Diode reverse recovery in the slow leg -- the reason real "
            "totem-pole PFCs need SiC or GaN, and the dominant loss term at "
            "hard-switched turn-on in CCM.",
            "Inductor current ripple raising the RMS current: the band is "
            "enforced but core and copper loss consequences are not computed "
            "here (use `losses`).",
        ],
        "trustworthy": [
            "V_out, P_out and I_ac_rms: set by power balance, validated to "
            "better than 0.25 % against closed form.",
            "V_ripple_2f: the P/(w1*Cout*Vout) term, validated to 0.08 %.",
            "Relative comparisons of PF and THD between two hysteresis bands "
            "at the same operating point.",
        ],
        "not_trustworthy": [
            "SINGLE-POINT CURRENT THD, AT ALL. Measured 2026-09-13: changing "
            "the load resistance by 0.015 % -- far below any physical tolerance "
            "-- moved reported THD from 0.957 % to 1.382 %, a 44 % swing. Both "
            "values were CONVERGED (identical to four decimals at 16x the FFT "
            "resolution) and the simulation is bit-reproducible, so this is not "
            "a numerical artefact. It is the free-running hysteresis switching "
            "beating against the line frequency and depositing energy at low "
            "harmonic orders whose position depends on the exact trajectory. "
            "A single THD number from this topology measures one beat pattern, "
            "not the converter. Use the median of an ensemble of neighbouring "
            "operating points, and quote the spread with it (measured spread: "
            "19 % at full load, 60 % at 10 % load). This is a real property of "
            "free-running hysteresis control and is one of the reasons "
            "production designs use fixed-frequency average-current-mode.",
            "Absolute THD as a prediction of hardware. Benchmarked against TI "
            "TIDUEZ3B (4 kW GaN totem-pole PFC, Table 3-1): the model is 9x-19x "
            "OPTIMISTIC on current THD across the full 10:1 load range. It "
            "omits the zero-crossing dead zone, dead time, reverse recovery, "
            "current-sense offset and the EMI filter.",
            "Anything dynamic: step response, regulation, stability. There is "
            "no voltage loop to have dynamics.",
            "Efficiency. The switches are resistances with no switching loss, "
            "so the model reports ~99.99 %, which is not a physical claim.",
            "Switching frequency: it is an OUTPUT of the band and the "
            "inductor, roughly Vout/(4*L*hyst) at the peak, and it varies "
            "across the line cycle.",
        ],
        "loss_model": {
            "how_it_works": (
                "Power stage simulated with ideal switches; loss computed in "
                "post-processing from the resulting per-device currents plus "
                "datasheet energies. Rds(on)/Tj solved as a coupled fixed point."
            ),
            "device_rating_vs_switched_voltage": (
                "T-type RAIL switches (S1/S4) must BLOCK the full bus - with S4 "
                "on, S1 stands off the entire link - so an 800 V system needs "
                "1200 V parts there, while the neutral-point pair and every NPC "
                "device need only Vdc/2. But the voltage COMMUTATED at a "
                "transition is Vdc/2 everywhere, and that is what scales "
                "E_on/E_off. Conflating the two erases the topologies' "
                "trade-off entirely."
            ),
            "bidirectional_pair": (
                "The T-type neutral-point pair shares one commutation: only the "
                "transistor whose forward direction matches the current "
                "switches, while its partner's antiparallel diode conducts. "
                "Charging both doubles that contribution."
            ),
            "freewheel_diodes": (
                "With dead time modelled, the antiparallel diodes conduct "
                "through every dead-time interval at a forward drop several "
                "times the transistor's on-state voltage. Charging them added "
                "~13 W (21 %) to an 11 kW SiC T-type at 10-20 kHz -- loss that "
                "was previously missing entirely, and which grows with "
                "switching frequency."
            ),
            "clamping_diodes": (
                "NPC's clamping diodes carry the whole zero-state current and "
                "are counted. Omitting them understated NPC conduction loss by "
                "roughly 40 W in the reference case and hid the crossover."
            ),
            "still_approximate": [
                "Switches are bidirectional resistances, so current does not "
                "commutate into an antiparallel diode with a knee voltage. "
                "Total conduction loss is about right; the transistor/diode "
                "SPLIT within a position is not.",
                "Switching energy comes from datasheet points scaled by power "
                "laws (I^1, V^1.0-1.3), not from the simulated waveform.",
                "No reverse recovery in the power stage, no Coss, no gate-drive "
                "dynamics.",
            ],
            "validated_behaviour": (
                "Switching loss scales linearly with fs (8.8x measured over an "
                "8.3x frequency range); conduction loss is independent of it "
                "(<0.1 % drift). T-type wins below ~8 kHz and NPC above, "
                "against a published crossover near 10 kHz."
            ),
        },
        "benchmark": {
            "published": (
                "TI TIDA-010203, 4 kW single-phase CCM totem-pole bridgeless "
                "PFC with GaN and C2000. Document TIDUEZ3B, Table 3-1, "
                "'Efficiency, iTHD and Power Factor Data With 230-V Input': "
                "ten measured load points from 0.395 kW to 4.001 kW. "
                "Efficiency 98.49-99.10 %, iTHD 13.65 % (10 % load) to 2.10 % "
                "(full load), PF 0.984 to 0.999. Hardware: Vout 400 V, "
                "L 480 uH, Cout 680 uF x 4."
            ),
            "source": "https://www.ti.com/lit/pdf/tiduez3",
            "benchmark_result_2026_09_13": {
                "V_out": "MATCH, worst error -0.01 % (limit 1 %)",
                "I_ac_rms": "MATCH vs Pout/Vin, worst error +1.39 % (limit 2 %)",
                "PF": "MATCH, worst |error| 0.0023 absolute (limit 0.01)",
                "THD": "BOUNDED_OPTIMISM, model 9x-19x optimistic (limit 30x), "
                       "on ensemble medians",
                "THD_trend": "TREND, measured rises 6.50x full->light load, "
                             "model 4.94x, -24 % (limit 40 %)",
                "caution": (
                    "The measured Iin column CANNOT be compared directly: "
                    "Pin/(Vin*Iin) disagrees with the table's own PF column by "
                    "-0.072 at light load and 0.000 at full load. The gap is "
                    "fully explained by a constant 0.67-0.89 A quadrature "
                    "current (~10 uF of EMI-filter X-capacitance) which carries "
                    "no power and which this model does not have.")
            },
            "this_model": {
                "230 Vac, 1000 W": "PF 0.9978, THD 0.584 %, Vout 399.98 V",
                "230 Vac, 500 W": "PF 0.9912, THD 1.472 %",
                "230 Vac, 200 W": "PF 0.9486, THD 4.117 %  <- NOT usable, PF check fails",
                "230 Vac, 100 W": "PF 0.8320, THD 8.175 %  <- NOT usable",
                "180 Vac, 1000 W": "PF 0.9986, THD 0.530 %",
                "265 Vac, 1000 W": "PF 0.9970, THD 0.522 %",
            },
            "interpretation": (
                "Measured 2026-09-13. The light-load collapse is NOT a defect "
                "and NOT a property of load: it is governed entirely by the "
                "RATIO of the hysteresis band to the peak line current. "
                "Measured: band/I_pk = 0.407 -> PF 0.9486; 0.163 -> PF 0.9912; "
                "0.081 -> PF 0.9978 -- and the 0.163 and 0.081 figures are "
                "IDENTICAL at 200 W and at 100 W, confirming the ratio, not "
                "the power, is the governing variable. A FIXED `hyst` "
                "therefore degrades as 1/P as load falls. To hold PF >= 0.99 "
                "the band must satisfy roughly hyst/I_pk <= 0.16, i.e. "
                "hyst <= 0.08 * Pout / Vac_rms. Scale `hyst` with load before "
                "reading anything into a light-load result."
            ),
        },
        "to_make_it_more_realistic": [
            "Scale `hyst` with `Pout` per the rule above. Leaving it at the "
            "default while sweeping load conflates a modelling artefact with "
            "converter behaviour, and is the single most likely way to draw a "
            "wrong conclusion from this topology.",
            "Use `losses` with a real SiC or GaN device: the ideal-switch "
            "efficiency here carries no information.",
            "For any question about regulation, transient response or loop "
            "stability, this model cannot answer it. A closed-loop, "
            "fixed-frequency average-current-mode model is required.",
            "For grid-code THD, an input EMI filter and a realistic "
            "zero-crossing treatment would both be needed.",
        ],
    },
}
TOPOLOGY["ttype"] = TOPOLOGY["npc"] = TOPOLOGY["npc3ph"] = TOPOLOGY["ttype3ph"]


def describe(topology: str) -> dict:
    d = TOPOLOGY.get(topology)
    if d is None:
        return {
            "topology": topology,
            "detailed_assumptions_available": False,
            "note": ("No per-topology assumption sheet has been written for "
                     "this converter yet. The common limitations below apply, "
                     "and the validated accuracy figures describe agreement "
                     "with an idealised closed-form model, not with hardware."),
            "common_not_modelled": COMMON_NOT_MODELLED,
        }
    out = {"topology": topology, "detailed_assumptions_available": True}
    out.update(d)
    out["common_not_modelled"] = COMMON_NOT_MODELLED
    out["headline"] = (
        "Validation compares the simulation against a closed-form model of an "
        "IDEALISED circuit. It does not establish that the idealised circuit "
        "matches hardware. Read not_modelled and not_trustworthy before "
        "quoting any number outside the trustworthy list."
    )
    return out
