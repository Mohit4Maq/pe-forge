---
name: pe-issue
description: Report a pe-forge problem or idea to the pe-forge team as a GitHub issue in one step - a tool error, a rule that does not fit the board, a check that missed something or a feature idea. Use when the user says "report this", "file an issue", "this rule is wrong for my board", "send feedback", or right after `forge` printed "Report it in one step: forge issue --last".
---

# pe-issue: one-step feedback to the pe-forge team

The user's design stays private: issues carry the environment, the error trace inside pe-forge, rule IDs and
the user's note. Paths, net names and the project name are redacted unless the user asks for `--keep-names`.

1. **Pick the kind** from what just happened:
   - `bug`: a forge command crashed or gave a wrong result (after a crash, use `--last`);
   - `rule`: a rule-book or project rule does not fit this board (`--rule SW-05`), often after a waiver;
   - `check`: a review found something no tool check caught (an escaped finding), or a check is wrong;
   - `idea`: anything else.
2. **Write the note with the user**, two to five lines: what they did, what they expected, what happened, and
   for a rule, why it does not fit (the waiver reason is a good start). No confidential design details.
3. **Show it first:** run `forge issue --kind <kind> [--last] [--rule ID] --note "<note>" --dry-run` and show
   the user the title and body exactly as printed.
4. **File it only after the user says yes:** run the same command with `--yes` instead of `--dry-run`. With
   `gh` installed and logged in it is created directly; otherwise the browser opens the pre-filled page and the
   user presses Submit. Report the issue link (or say the page is open).
5. For a broader report ("what has the tool seen on my project?"), run `forge feedback` and show it; it is never
   sent by itself. `forge issue --kind idea --with-summary` attaches it to an issue.

Never file an issue without the user's yes. Never add design files, netlists or part lists to an issue.
`forge learn off` stops local recording; `forge learn` shows what is recorded.
