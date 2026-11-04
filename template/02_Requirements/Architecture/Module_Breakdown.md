---
type: architecture
rev: 0.1
status: draft
---
# {{NAME}}: module breakdown

> Written at the end of system-level P2 / P3 by `pe-systems`. If the project is small (one sheet, < ~40 parts,
> one power stage), say so and use a single module. Otherwise split it so that **each module can be designed,
> simulated and drawn on its own sheet and verified alone**, with interfaces fixed first.

## Rule for the split
- One power conversion stage, or one control / sensing / auxiliary function, per module.
- Each module has a measurable interface (voltage, current, signal levels, timing) written in the ICD below.
- Order: modules others depend on first (auxiliary supply, sensing reference), then power stages, then control.

## Modules
| ID | Module | Purpose | Interfaces | Depends on | Sheet |
|---|---|---|---|---|---|
| M-01 | _(fill in)_ | | | | |

## Interface control (ICD)
| ICD | From | To | Quantity | Nominal | Limits | Notes |
|---|---|---|---|---|---|---|
