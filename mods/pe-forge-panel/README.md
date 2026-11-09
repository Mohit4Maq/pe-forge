# pe-forge-panel

A results panel for pe-forge projects, beside the Claude Code chat. Its header shows the **maq** mark as a large block
logo with the project, phase and next step. Five tabs:

- **V-cycle**: every stage of the V, where the project is now and what needs attention; the chosen stage's facts,
  items and documents in a card, and buttons to ask Claude about it.
- **Simulations**: each result's SIM-QC grade, its key numbers per corner against the hand calculation, charts (lines
  as sparklines, loss breakdowns as bars) and notes.
- **Checks**: the last full `forge check`, with first findings and the open re-checks.
- **Gates**: phases and modules, and a button that checks the next gate.
- **Parts**: the datasheet work list for the board.

It opens by itself when a new simulation result or a new full check lands. Open it at any time with `/results`, or
`/results <project folder>` to look at another pe-forge project. Press ctrl+x then Tab to move the keyboard into
the panel; keys 1 to 5 switch tabs, the letter on a button presses it, Esc returns to the prompt.

Install: `/plugin install pe-forge-panel --marketplace Mohit4Maq/pe-forge` (or `/plugin install pe-forge-panel@pe-forge`
once the pe-forge marketplace is added). `[ui] logo = false` in project.toml gives a one-line header.

All data comes from `forge panel --json` (the project's own files); every button runs a forge command in the project
folder. It needs pe-forge installed (`PE_FORGE` set, or `forge` in `~/.local/bin`).

License: MIT.
