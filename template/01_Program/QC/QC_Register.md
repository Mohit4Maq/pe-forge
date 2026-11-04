---
type: register
status: live
---
# {{NAME}}: QC register

One row per independent review. `forge gate check` reads the latest row for each gate and module: the gate passes
only with verdict READY / READY WITH MINORS and status CLOSED (or ACCEPTED). A Critical or a REJECTED result is
closed only by a fresh reviewer run on the fixed work.

| Date | Gate | Module | Deliverable | Tier | Verdict | C / M / m | SIM A / P / R | Review file | Status |
|---|---|---|---|---|---|---|---|---|---|
