---
name: council
description: Run a decision council in a pe-forge project - 2 to 5 role agents write independent position memos in parallel, cross-examine each other once, then the chair writes the record and recommendation for the owner. Use when the user says /council, "let the team decide", "cross-question this", or for any big decision (a D-xx, architecture, anything irreversible, a cost / NRE / date move, a sole-source commitment).
---

# Decision council

You (the main session) are the **chair**: you frame, run the rounds, check disputed facts and write the record.
You do not vote. **The owner decides.**

> **Agent names:** installed as a plugin, every agent is prefixed with the plugin name: spawn `pe-forge:pe-hw`, `pe-forge:pe-reviewer`, `pe-forge:schematic-reviewer` and so on (subagent_type). This file uses the short names.

## 0. Frame (before spawning)
≤ 10 lines: the question; the options including "defer"; constraints (decided architecture in CLAUDE.md,
requirements, dates); established facts with file paths. Pick 2–3 members (all only for a re-baseline):

| Decision touches | Members |
|---|---|
| Technical architecture / parts | `pe-hw` + `pe-supply` (+ `pe-sw` if control / sensing / protection) |
| Cost, make-vs-buy, NRE | `pe-finance` + `pe-supply` + `pe-hw` |
| Schedule, scope, gates | `pe-pm` + `pe-hw` + `pe-sw` |
| Certification, safety, insulation | `pe-compliance` + `pe-hw` |
| Interfaces, scope of a block | `pe-systems` + `pe-hw` |
| Margin vs cost, lifetime | `pe-reliability` + `pe-hw` + `pe-finance` |
| Gate readiness, test strategy | `pe-validation` + `pe-pm` + `pe-hw` |

Tell the user in one line who sits and why.

## 1. Round 1: independent memos, in parallel (one message, one Agent call per member)
Prompt starts with `COUNCIL ROUND 1`, includes the framing and file paths. No member sees another's view.

## 2. Round 2: cross-examination, in parallel (SendMessage to each member)
`COUNCIL ROUND 2` with the other memos verbatim. Each answers the questions put to it, challenges the weakest
claim of each other memo, and states a final position. Two rounds is the cap; a third only for one factual
dispute, put to the member who owns that fact.

## 3. Record
Check each disputed claim yourself (Rule 1). Write `01_Program/Decisions/Council/YYYY-MM-DD_<D-id>_<topic>.md`:
frontmatter (`type: council`, `decision`, `date`, `members`, `status: awaiting owner`); question and options;
positions table (member → final position → reason, who moved and why); agreed facts; open disagreements and
the evidence that would settle each; consequences of each option (schedule, cost, supply, margin, firmware);
recommendation with confidence and reversal triggers; **Owner decision: blank**. Link it from the Master Plan
item. Brief the owner in ≤ 15 lines.

## 4. After the owner decides
Fill in the decision, write an ADR if architectural, update the Master Plan decided log, CLAUDE.md decided
architecture and every affected doc (Rule 4).
