---
name: pe-adopt
description: Help with a KiCad board that is not a pe-forge project yet, and bring it under pe-forge when the owner wants. Quick, evidence-backed answers first (forge quick, read-only), then adoption in light mode (no file of theirs changed), then the full process before boards are ordered. Use when the user says /pe-adopt, "help me with this board", "check my design", "review this schematic / layout", "use pe-forge on my existing project", or when the session-start note says the folder is a KiCad design that is not a pe-forge project.
---

# pe-adopt: help first, process later

The customer opened Claude Code in their own KiCad folder. They want help with *their* board now, not a set-up
interview. pe-forge earns its process by being useful first. `forge` is `$PE_FORGE/bin/forge`.

## Stage 0: help (no project, nothing written)
1. Answer the question they asked. Do not start phases, ask intake questions or run `forge new`.
2. For evidence run `forge quick` (or `forge quick --vin <max input V>` when you know a high-voltage input). It
   runs ERC, DRC, the library and layout checks, protection, insulation and the High-Power PCB Rule Book **on a
   copy**: their folder is never changed (it verifies that by hash).
3. Lead with what bears on their question, then the rest, in this order: **SAFETY** (protection, insulation, a
   Must rule) > errors > warnings. Give the location and the fix. Say which checks did not run and why: a check
   that did not run is not a pass.
4. Their schematic and board are theirs: give changes as precise instructions ("move D2 ..."), never edit their
   `.kicad_sch` / `.kicad_pcb` unless they ask for that specific edit.

## Stage 1: offer to keep track (once, after you have helped)
In one line: "Want me to keep track of this board? pe-forge adds its files beside yours, changes none of them,
and `forge adopt --undo` reverses it." On yes:
1. `forge adopt` (the survey: show them the plan), then `forge adopt --apply` on their OK.
2. It starts in **light** mode (`forge level`): every check runs and is reported as advice; nothing blocks; a
   release is refused until the level is raised. The phase starts where the work is (P6 / P7); earlier phases are
   *skipped*, not passed.
3. Carry the quick-check findings over: open rows in the QC register for the SAFETY and error items.
If they say no, keep helping in stage 0 and do not ask again this session.

## Stage 2: the full process, before anything is made
Boards are not released in light mode, and G8 needs the requirements and V-model in place (P2 passed). When they
head for an order or a release (Gerbers, "send it to fab"), or ask for the process:
1. `forge req infer`: drafts system requirements from the design (rails and their voltage limits, output
   current, protection, isolation) with the evidence for each, plus rows the design cannot tell (efficiency,
   ambient, standards, life). **Show the draft; the owner confirms, fills the _(…)_ blanks, deletes what is wrong.**
   Never fill a blank with your own guess. Then `forge req infer --apply`.
2. `forge level standard` (raising is always allowed): obligations now block; then /pe-requirements derives the
   HWR / SWR rows and verification, and G2 is passed with its reviewers as usual (/pe-run takes over).
3. Lowering the level is the owner's decision, typed by them in a terminal: never ask them to let you lower it,
   never work around it.
