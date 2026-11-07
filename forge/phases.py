"""The pe-forge process: phases, their gates, and what each gate needs.

A gate passes only when (a) every automated check below is green and (b) the gate review in the QC register
for that gate (and module) has verdict READY or READY WITH MINORS and status CLOSED or ACCEPTED.

Phases P2..P6 run per module when the project is split into modules (see forge.modules); P0, P1, P7 and P8
run once at system level, and P2/P3 also run once at system level before any module starts.
"""

PHASES = [
    # id, gate, title, owner skill, artefact keys / globs that must exist and be filled in
    ("P0", "G0", "Intake and project set-up", "pe-new",
     ["project.toml", "CLAUDE.md", "{program}/Master_Plan.md", "{foundation}/Intake_Answers.md"]),
    ("P1", "G1", "Foundation: market survey, literature, reference designs, standards", "pe-foundation",
     ["{market}/Market_Survey.md", "{lit_notes}/*.md", "{ref_design}/Reference_Designs.md", "{standards}",
      "{background}"]),
    ("P2", "G2", "Requirements: system, broken down to hardware / software / control", "pe-requirements",
     ["{sys_req}", "{vmodel}/trace.csv", "{vmodel}/verification.csv", "{requirements}/Architecture/Module_Breakdown.md"]),
    ("P3", "G3", "Architecture and topology selection", "pe-architecture",
     ["{architecture}/Topology_Trade.md", "{decisions}/ADR-*.md"]),
    ("P4", "G4", "Tentative BoM, part selection and sizing", "pe-bom",
     ["{bom_csv}", "{bom_md}", "{hardware}/Design_Calculations.md"]),
    ("P5", "G5", "Simulation and verification (SVR per result)", "pe-simulate",
     ["{sim_results}/*.md"]),
    ("P6", "G6", "Schematic: booklet, sheet brief, parts, drawing, ERC, review", "pe-schematic",
     ["{booklets}/*.md", "{briefs}/*.md", "{pcb}/**/*.kicad_sch"]),
    ("P7", "G7", "PCB layout: stack-up, placement, routing, DRC, EMC / DFM review", "pe-pcb",
     ["{pcb}/**/*.kicad_pcb", "{layout_spec}"]),
    ("P8", "G8", "Manufacturing release: Gerbers, drill, BoM, CPL, fab notes, test plan", "pe-release",
     ["{release}/*/gerbers/*", "{release}/*/bom/*", "{release}/*/Release_Notes.md", "{dvp}"]),
]
MODULE_PHASES = ("P2", "P3", "P4", "P5", "P6")
PHASE = {p[0]: p for p in PHASES}
GATE_OF = {p[0]: p[1] for p in PHASES}

# Which independent reviewers each gate needs (the chair adds compliance when the work touches insulation,
# protection, stored HV energy, grid or EMC).
GATE_REVIEWERS = {
    "G0": [],
    "G1": ["pe-reviewer"],
    "G2": ["pe-reviewer", "pe-compliance", "pe-validation"],
    "G3": ["pe-reviewer"],
    "G4": ["pe-reviewer", "pe-reliability"],
    "G5": ["pe-reviewer"],
    "G6": ["pe-reviewer", "schematic-reviewer", "power-reviewer"],
    "G7": ["pcb-reviewer", "pe-reviewer"],
    "G8": ["pe-reviewer", "pe-validation", "pe-compliance"],
}

# "lite" profile: fewer reviewers, same automated checks. The chair can still call any reviewer.
LITE_REVIEWERS = {g: v[:1] for g, v in GATE_REVIEWERS.items()}

STATUS = ("todo", "active", "in-review", "passed", "skipped")
