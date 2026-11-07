# pe-forge-panel

A results panel for pe-forge projects, beside the Claude Code chat. It shows:

- **Simulations**: each result's SIM-QC grade, its key numbers per corner against the hand calculation, charts (lines
  as sparklines, loss breakdowns as bars) and notes.
- **Checks**: the last full `forge check`, with first findings and the open re-checks.
- **Gates**: phases and modules, and a button that checks the next gate.
- **Parts**: the datasheet work list for the board.

It opens by itself when a new simulation result or a new full check lands. Open it at any time with `/results`, or
`/results <project folder>` to look at another pe-forge project. Keys 1 to 4 switch tabs; the letter on a button
presses it.

All data comes from `forge panel --json` (the project's own files); every button runs a forge command in the project
folder. It needs pe-forge installed (`PE_FORGE` set, or `forge` in `~/.local/bin`).

License: MIT.
