# pe-forge terminal UI: layout spec (WP14)

Status: **approved by the owner on 2026-11-07 and implemented** (forge/ui.py, forge/statusline.py). Decisions:
accent #4245f5 (blue, owner 2026-11-07; was MaqLabs red); `ctx` segment included; installed per pe-forge project only (`.claude/settings.local.json`,
not the shared settings, because the command holds this machine's path); `bin/` kept.
Checked against the Claude Code docs on 2026-11-07 (status line: code.claude.com/docs/en/statusline; hooks:
code.claude.com/docs/en/hooks; plugin manifest: code.claude.com/docs/en/plugins/manifest-reference).

## 1. What the docs say (and where the plan changes)

| Plan assumption | Docs | Change |
|---|---|---|
| No TTY, so width comes from `[ui] statusline_width` (110) | Claude Code sets `COLUMNS` and `LINES` before running the script | Width = `$COLUMNS`, then `[ui] statusline_width`, then 110 |
| Multi-line output unconfirmed | Each printed line is its own row | Optional line 2 (`[ui] statusline_lines = 2`), default 1 |
| Refreshes after assistant messages, debounced, in-flight run cancelled | Same: session start, each assistant message, `/compact`, permission-mode change, 300 ms debounce, in-flight run cancelled. Optional `refreshInterval` (seconds) adds a timer | Leave `refreshInterval` unset (we only read small files; events are enough). Mention it as an option for idle sessions with background agents |
| A plugin cannot ship a status line | Confirmed: a plugin's `settings` honours only `agent` and `subagentStatusLine` | Install writes `statusLine` into user or project `settings.json`, as planned |
| stdin has model, workspace, maybe context and cost | Also `context_window.used_percentage`, `cost.total_cost_usd`, `effort.level`, `session_id`, `version`, `workspace.git_worktree`, `workspace.repo` | Optional `ctx 41 %` segment from stdin (free, no transcript scan) |
| `systemMessage` shown to the user | Shown on every event except Setup, InstructionsLoaded, Notification, MessageDisplay (those discard it) | SessionStart banner and Stop line via `systemMessage`; verify once in a live session |
| (not in plan) | A custom status line hides the footer's keyboard hints (`esc to interrupt`, `? for shortcuts`); a folder you have not trusted shows it blank | Say both in the README "Your terminal" section |

Side finding, outside WP14: a plugin's top-level `bin/` is put on the Bash tool's PATH while the plugin is enabled
(useful: `forge` works without bootstrap's symlink), but **claude.ai and Cowork refuse to install a plugin that
has a top-level `bin/`**. pe-forge has one. Decide before publishing whether that matters (fix: move the launchers to
`scripts/bin/` and point hooks there).

## 2. The bar (line 1)

```
maq ▸ Buck48to12 · P6 Schematic · M-02 Power stage · G6 ? review · ⚠ 2 open: erc, drift · txn: swap Q3 · CR-007 · check ✓ 14:02 · meter AMBER · ctx 41% · Opus · feat/wp03 ●
```

Segments, highest priority first. When the line is wider than the width, whole segments are dropped from the
bottom of this table (never cut in half). Missing source files drop their segment silently.

| # | Segment | Example | Source | Colour |
|---|---|---|---|---|
| 1 | mark + project | `maq ▸ Buck48to12` | `[ui] mark` (default `maq`, the MaqLabs mark) + `project.toml` name | mark accent bold italic, name bright |
| 2 | phase | `P6 Schematic` | `state.json` phase + short title from `phases.py` | bright |
| 3 | module | `M-02 Power stage` | `state.json` active_module (name cut to 18 chars) | bright |
| 4 | gate | `G6 ? review` / `G6 ✓` / `G6 open` | phase / module status (`cli_state.ICON` words) | ok when passed, warn when in review, dim otherwise |
| 5 | obligations | `⚠ 2 open: erc, drift` | `.forge/ledger.json` open list (kinds, max 3) | warn; omitted at 0 |
| 6 | transaction | `txn: swap Q3` | `.forge/txn.json` what (cut to 16) | warn; omitted when none |
| 7 | change control | `CR-007` / `locked` | approved CR covering the active module / module past G6 with none | dim / warn |
| 8 | last check | `check ✓ 14:02` / `check ✗ erc, drift` | `.forge/cache/check.json` (written by every `forge check`) | ok / err |
| 9 | budget | `meter AMBER` | `.forge/cache/meter.json` (written by the Stop hook) | ok GREEN, warn AMBER, err RED; omitted when absent |
| 10 | context | `ctx 41%` | stdin `context_window.used_percentage` | dim; warn ≥ 70 %, err ≥ 90 % |
| 11 | model | `Opus` | stdin `model.display_name` | dim |
| 12 | git | `feat/wp03 ●` | `.git/HEAD` read directly; `●` when the watch snapshot says dirty (no git call) | dim |

Separator ` · ` in dim. Outside a pe-forge project: the previously installed status line command runs with the same
stdin and its output is printed unchanged; with none saved, a minimal `Opus · folder` line.

## 3. Line 2 (optional, `[ui] statusline_lines = 2`)

```
  Next: work P6 for M-02 and pass G6 (skill pe-schematic)
```
`state.next_action` text in dim, indented two spaces, cut to the width.

## 4. Hook chrome (user-facing, `systemMessage`, prefixed `pe-forge ▸`)

| When | Line | Rule |
|---|---|---|
| SessionStart, inside a project | `pe-forge ▸ Buck48to12 · P6 Schematic · M-02 · next: pass G6` | one line |
| Stop, after the ledger ran | `pe-forge ▸ ERC ✓ · drift ✓ · consistency ✓` or the failures in err | only when the result changed since the last Stop (last line kept in `.forge/cache/`) |
| `forge gate pass`, `forge phase start` (CLI) | `✓ M-02 G6 passed` / `▶ M-02 P6 active` | coloured when stdout is a TTY |

Never more than two lines per hook event. Model-facing text (`additionalContext`, the ledger's exit-2 message)
stays unbranded, as today.

## 5. Theme

| Token | Truecolor | 256-colour fallback |
|---|---|---|
| accent | `#4245f5` (blue; `[ui] accent` in project.toml or `~/.config/pe-forge/ui.toml`) | 63 |
| bright | default foreground, bold | bold |
| dim | `#8a8a8a` | 245 |
| ok | `#3fb950` | 71 |
| warn | `#d29922` | 178 |
| err | `#ff6e9c` (pink-red, so an error never looks like the brand mark; always with `✗` or `⚠`) | 204 |

Truecolor when `$COLORTERM` is `truecolor` or `24bit`, else 256 colours; no colour with `$NO_COLOR` or
`[ui] color = false`. Glyphs `◆ ▸ ✓ ✗ ▶ ? · – ⚠ ●` with an ASCII set for `[ui] glyphs = "ascii"`:
`* > ok x > ? . - ! *`.

## 6. Contract

- Under 100 ms and no subprocesses (git state comes from `.git/HEAD` and the watch snapshot), no network,
  no kicad-cli, no transcript scan. A test times it.
- Never a traceback in the bar: on any error print the minimal line and log to `.forge/cache/statusline.log`.
- `forge statusline install [--user | --project] [--uninstall]` merges into `settings.json` (atomic write, `.bak`),
  saves a foreign `statusLine` to `~/.config/pe-forge/statusline_prev.json` and chains to it outside pe-forge
  projects; uninstall restores it byte for byte.
- `forge status` keeps its exact text when stdout is not a TTY; colour only on a TTY; `--banner` adds a three-row
  block mark.

## 7. Results panel (pe-forge-panel 0.2, owner 2026-11-08)

Layout, top to bottom, with one blank row between blocks and one column of side padding:

```
 ▄▄▄▄▄▄▄▄▄▖  ▗▄▄▄▄▄▖ ▗▄▄▄▄▄▄
 █▛  ██  ██ ▟█▘  ▐█▌▗█▌  ▗██   Buck48to12
▐█▌ ▐█▛  ██ █▛   ▟█▘▟█   ▐█▌   pe-forge · power electronics
▐█  ▐█▌ ▐█▛▗█▌   ██ ██   ██▌
▀▀  ▀▀  ▝▀▘▝▀▀▀▘ ▀▀▝▀▀▀▀ ██    P3  system
                        ▐█▛
next  <next action, wrapped>
────────────────────────────────────────────
[ ▌1 V-cycle ]  [ 2 Simulations (1) ]  [ 3 Checks ]  [ 4 Gates ]  [ 5 Parts ]
────────────────────────────────────────────
<tab body: accent section titles; cards with a rounded accent border; findings show the result first and stop at
the edge (truncate-end); button rows spaced two columns>
```

- The logo is `forge.ui.MAQ_LOGO`, the "maq" letters cut from the wordmark (28 × 6). `forge panel --json` carries it
  as `logo`; empty with `[ui] logo = false` or `glyphs = "ascii"`, then the header is `maq ▸ <project>`.
- Accent `#4245f5` for the logo, section titles, the active tab bar and card borders; dim for secondary text.
- Columns: simulation result names 26, values 8 per corner, units 9; series labels 12.

## 8. Owner decisions

1. Decided: accent `#4245f5` (blue), changed from MaqLabs red on 2026-11-07.
2. Decided: yes.
3. Decided: pe-forge projects only (`forge new`); `--user` stays available but bootstrap does not offer it.
4. Decided: keep `bin/` (Claude Code CLI).
5. Decided (2026-11-08): the results panel shows the maq mark as a big block logo; layout as in section 7.
