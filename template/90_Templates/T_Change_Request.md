---
type: change-request
id:
title:
status: draft
modules:
files_allowed:
requirements:
reason:
approved_by:
approved_on:
commit:
---
# {{ID}}: {{TITLE}}

## Why
What is wrong or must change, with the evidence (review finding, bench result, owner request, supply problem).

## What changes
Parts, values, nets, sheets and documents, one line each (old → new).

## Impact
Requirements, other modules, ICDs, BoM cost, schedule, re-verification needed (simulations, gates to re-run).

## Plan
1. `forge txn begin "{{ID}} ..."`
2. Edit the files listed in `files_allowed`.
3. `forge txn verify`, then `forge txn commit -m "{{ID}}: ..."`.
4. Re-run the module gate checks that the change touches, then `forge cr close {{ID}}`.
