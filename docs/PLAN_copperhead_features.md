# pe-forge upgrade plan: features adapted from copperhead

**Audience:** Claude Code, working in a clone of `Mohit4Maq/pe-forge`.
**Owner:** Mohit Chand (Veltara Innovations / MaqLabs).
**Date:** 2026-11-06. **Reference versions:** pe-forge `5839ddd` (v0.1, 2026-11-04); copperhead `v0.13.0` (main, 2026-11-06).

---

## 0. How to use this document (read first)

You are implementing a set of work packages (WPs) in pe-forge. Each WP borrows a proven idea from
[copperheadhq/copperhead](https://github.com/copperheadhq/copperhead), adapted to pe-forge's architecture
(Python, Claude Code plugin, gated phases, power-electronics focus).

Rules for this work:

1. **Work in the order given in section 4.** Later WPs depend on WP1 and WP2.
2. **One WP per branch and per commit series.** Branch name `feat/wpNN-short-name`. Do not mix WPs.
3. **Before writing code for a WP, read the pe-forge files listed under "Current state" for that WP**, and
   (optional but useful) the copperhead files listed under "Reference". Clone copperhead read-only into a
   scratch folder outside the pe-forge repo: `git clone --depth 1 https://github.com/copperheadhq/copperhead /tmp/copperhead`.
4. **Re-implement, do not copy.** pe-forge is Python; copperhead is TypeScript. Port the idea and the
   acceptance behaviour. If you do copy a non-trivial piece of logic or text, add an entry to `THIRD_PARTY.md`
   naming copperhead, its Apache-2.0 licence and the file, and keep any Apache NOTICE text it carries.
   Either way, add one line to `THIRD_PARTY.md`: "Design ideas adapted from copperhead (Apache-2.0)".
5. **Engine code in `forge/` stays standard-library only** (as today). Tools under `library/tools/` may keep
   their current imports.
6. **Never break the existing CLI.** Every current `forge` command keeps its arguments and exit codes. New
   behaviour arrives as new commands or new flags.
7. **Tests:** add unit tests to `tests/test_forge.py` (stdlib `unittest`, same style as the existing ones).
   KiCad-dependent tests must be skipped cleanly when `kicad-cli` is absent, like the current schematic tests.
   Run `./tests/run.sh` before every commit. If a WP touches `vendor/pwrlab` or any simulation check, also run
   `./tests/run.sh --pwrlab` and ask the `roast` reviewer agent to attack the change before committing.
8. **Docs:** update the `forge/__main__.py` docstring, `README.md` (Command line and Status sections) and the
   relevant `skills/*/SKILL.md` in the same commit as the feature.
9. **Rule 10 of pe-forge applies to you:** no `git push`, no tags, no network purchases or messages without
   asking Mohit first. Commit locally, then report.
10. **Writing style in docs and messages:** no em-dashes; use commas, colons, parentheses or new sentences.

When a WP's acceptance criteria all pass, mark it done in section 6 (the tracker) and move on.

---

## 1. Background

### 1.1 pe-forge today (what you are changing)

- Claude Code plugin (`.claude-plugin/`), skills per phase (`skills/pe-*`), role and reviewer agents (`agents/`).
- `forge` CLI (`forge/__main__.py`), stdlib Python: scaffold, phase / module / gate state (`state.py`,
  `phases.py`, `gates.py`), V-model traceability (`vmodel.py`), ID linking (`linkids.py`), superseded-term
  consistency (`consistency.py`), manual-edit watch (`watch.py`), token meter (`meter.py`), schematic drawing
  engine (`schdraw.py`, 16 style rules, double connectivity check, ERC on a dry copy).
- Hooks (`hooks/hooks.json`): `watch --report` on SessionStart / UserPromptSubmit; `consistency --hook` and
  `watch --mark` on Stop (Stop exits 2 to block while superseded terms remain).
- `library/`: KiCad 10 database library (`database/mc_parts.sqlite`, ~570 parts), template project with
  `release.sh` and a jobset, tools `bom.py` (grouped BoM + DigiKey + Mouser CSVs), `sch_check.py`,
  `pcb_check.py`, `kinet.py` (netlist export and parse), `mouser.py`, `fp_qa.py`.
- `vendor/pwrlab`: LTspice simulation with closed-form cross-checks and an MCP server.
- Gates (`gates.py`): artefact presence and fill check, V-model check (G2), BoM completeness (G4), SVR
  presence (G5), ERC (G6) / DRC (G7) by regex over the text report, consistency, QC-register review row.

### 1.2 copperhead (what you are borrowing from)

"Cursor for circuit boards": a TypeScript CLI agent that edits real `.kicad_sch` / `.kicad_pcb` files. Its
core is two invariants that it enforces in code, not in prompts:

1. **Spec-gated in:** edit tools are absent from the agent's tool list until a change proposal validates.
2. **Verification-gated out:** nothing is "done" until ERC/DRC pass; every run is wrapped in a git snapshot and
   rolled back losslessly if verification cannot be satisfied.

Around that it has: a deterministic, LLM-free, network-free `check` command (ERC, DRC, doc drift, constraint
checks, legibility, spec validation) used by CI and a git pre-commit hook; an obligations ledger that blocks
`finish` while any sync duty is open; a fab release gate (`check --fab`); supplier BOM export (JLCPCB,
DigiKey, Mouser); an `explain` lookup; an MCP server exposing only outcome-level tools; and proposals for a
ngspice assertion gate and a datasheet cache.

Key copperhead files (paths relative to its repo root):

| Idea | File(s) |
|---|---|
| Spec and invariants | `openspec/specs/SPEC.md` (sections 1.3, 2.6, 4.2, 4.5, 7, 9) |
| Git snapshot / rollback | `src/util/git.ts` (`snapshot`, `restore`, `snapshotUntracked`) |
| Deterministic check | `src/commands/check.ts` |
| Doc drift | `src/memory/drift.ts`, `src/memory/bom-table.ts` |
| Constraint registry | `src/memory/constraints.ts` |
| Obligations ledger | `src/agent/ledger.ts` |
| Fab gate | `src/kicad/fab.ts`, `openspec/changes/add-fab-release-gate/` |
| Supplier BOM export | `src/kicad/bom-export.ts`, `openspec/changes/add-supplier-bom-export/` |
| Legibility checker | `src/kicad/legibility.ts`, `src/kicad/score.ts` |
| MCP server | `src/mcp/server.ts` |
| Explain lookup | `src/commands/explain.ts`, `openspec/changes/add-explain-command/` |
| SPICE gate (proposed) | `src/kicad/spice.ts`, `openspec/changes/add-spice-verification-gate/` |
| Datasheet cache (proposed) | `openspec/changes/add-part-research-tools/` |
| Pin dossier | `openspec/changes/add-symbol-pin-dossier/` |

### 1.3 What we deliberately do NOT borrow

- **Multi-provider LLM abstraction** (OpenAI, Codex, Cursor, compat endpoints). pe-forge is a Claude Code
  plugin; the host is the agent. WP10 (MCP server) is the portable route instead.
- **OpenSpec (Node dependency).** pe-forge already has V-model traceability and change requests
  (`forge vmodel cr`). WP6 extends those instead.
- **The REPL / TUI and the `create` pipeline.** pe-forge's `pe-run` supervisor and phase skills cover this.
- **copperhead's draft engine.** `forge schdraw` already fits pe-forge's style guide. We borrow only its
  legibility checks (WP11).

---

## 2. Gap summary

| Capability | copperhead | pe-forge today | WP |
|---|---|---|---|
| KiCad report parsing | `--format json`, structured violations | regex over text `.rpt`; `kicad-cli` path resolved in two different ways | WP1 |
| One deterministic check command, CI + pre-commit | `copperhead check`, hook installed by `init` | checks spread over `gate check`, `consistency`, `bom check`, `sch_check.py`, `pcb_check.py`; no pre-commit hook, no CI template | WP2 |
| Lossless rollback of failed edits | git snapshot (HEAD + stash object + untracked tree), restore, failed work kept as stash | `schdraw apply` writes a single `.bak` in scratch; no rollback for other edits | WP3 |
| Structured doc-vs-design drift | BOM.md / PINOUT.md vs schematic, `{doc, claim, actual}` | text-only superseded-term scan | WP4 |
| Mechanical "keep in sync" duties | obligations ledger blocks `finish` | Stop hook blocks on consistency only | WP5 |
| Change-gated edits after baseline | edit tools locked until proposal validates | rules in CLAUDE.md and reviewers; no hard lock | WP6 |
| Fab release gate | `check --fab`: routing, BOM readiness, sch-PCB match, output freshness by hash, docs presence | `release.sh` + G8 artefact presence + reviewers | WP7 |
| JLCPCB BOM/CPL natively | built in | delegated to external `kicad-happy` skill | WP8 |
| Deterministic lookup | `explain U1 / NET / U1.PIN` | agents read s-expressions or netlists | WP9 |
| Host-portable entry point | MCP server, 5 outcome tools, no raw edit tool | pwrlab MCP only; forge is Claude Code only | WP10 |
| Legibility of any sheet, with score | 13 finding families, advisory on hand-drawn sheets | 16 strict rules, only for sheets schdraw draws | WP11 |
| Behavioural check of small analog circuits | ngspice assertions per subsystem (proposed) | pwrlab covers power stages only | WP12 |
| Datasheet evidence trail | cached PDFs by hash, `VERIFIED(datasheet)` (proposed) | datasheet checks by agents, no cached evidence | WP13 |
| Branded, always-visible project state in the terminal | own TUI: logo, copper theme, fixed footer with run state | `forge status` on demand only; hooks print plain text | WP14 |

Where pe-forge is already stronger (do not regress): gated phases with independent reviewers, V-model,
council, verified LTspice simulation, alternates with price and stock, parts database, strict schematic style
with double connectivity check, manual-edit watch.

---

## 3. Work packages

Each WP has: **Why**, **Reference**, **Current state**, **Build**, **Acceptance criteria** (binary,
Given/When/Then style), **Tests**, **Out of scope**.

---

### WP1: Shared KiCad CLI layer with JSON reports

**Why.** Every later WP needs reliable, structured ERC/DRC results. Today `gates.kicad()` scrapes
`** Found N violations` from a text report and returns `-1` when the regex misses. `kicad-cli` is also found
three different ways: `kinet.py` hardcodes the macOS path, `gates.py` tries `shutil.which` then the macOS
path, and `sch_place.py` honours `$KICAD_CLI` first. copperhead normalises JSON reports into one
violation shape.

**Reference.** copperhead `src/kicad/cli.ts`, `src/kicad/report.ts`.

**Current state.** `forge/gates.py` (`kicad()`), `forge/schdraw.py` (`erc_errors`, `kicad_netlist`),
`library/tools/kinet.py` (`CLI`), `skills/kicad-place/scripts/sch_place.py` (`KCLI`).

**Build.**
- New module `forge/kicad.py` (stdlib):
  - `find_cli()`: order `$KICAD_CLI` env, `shutil.which("kicad-cli")`, macOS app path, common Linux paths.
    Single source of truth; make `kinet.py`, `sch_place.py`, `gates.py`, `schdraw.py` use it (for `library/`
    and `skills/` scripts, import via `PE_FORGE` path or keep a thin fallback that calls the same order).
  - `erc(sch_path, severity="error") -> Report`: runs
    `kicad-cli sch erc --format json --severity-<level> --exit-code-violations -o <tmp.json> <sch>`.
  - `drc(pcb_path, parity=True) -> Report`: runs
    `kicad-cli pcb drc --format json --schematic-parity --all-track-errors --exit-code-violations -o <tmp.json> <pcb>`.
  - `netlist(sch_path) -> dict[net, set[REF.PIN]]` (move `kicad_netlist` here).
  - `Report` dataclass: `ok: bool`, `tool_ok: bool` (did kicad-cli run and produce the file),
    `violations: list[{severity, type, description, items:[{ref, pos, sheet}]}]`, `unconnected: list`,
    `parity: list`, `raw_path: str`.
- Never trust exit code alone (pwrlab's own lesson with LTspice): `tool_ok` is false if the output file is
  missing or does not parse, and callers treat `tool_ok == False` as a failure with a clear message, never as
  a pass.
- Capture the real JSON from KiCad 10 for one clean and one broken fixture into `tests/fixtures/kicad/` and
  write the parser against those captured files (KiCad's JSON keys have shifted between majors; do not guess
  them, read the captured output).

**Acceptance criteria.**
- AC1.1 Given the existing short-circuit fixture, when `forge.kicad.erc` runs, then `ok` is false and at least
  one violation names a sheet and position.
- AC1.2 Given `kicad-cli` that exits 0 but writes no file (simulate with a stub on `$KICAD_CLI`), then
  `tool_ok` is false and `gates.check(..., "G6")` reports FAIL with "kicad-cli produced no report".
- AC1.3 `gates.kicad()` and `schdraw` produce the same pass/fail as before on all existing tests.
- AC1.4 `grep -rn "Applications/KiCad" forge library/tools skills` finds only `forge/kicad.py` (and its
  documented fallback copies, if any).

**Tests.** Parser tests on captured JSON (run without KiCad); live tests skipped without `kicad-cli`; stub-CLI
test for AC1.2.

**Out of scope.** Changing which rules ERC/DRC enforce.

---

### WP2: `forge check`, pre-commit hook and CI template

**Why.** copperhead's single most useful habit: one deterministic command, no LLM, no network, under a minute,
safe in CI and a pre-commit hook. Hardware repos get a green check like software.

**Reference.** copperhead `src/commands/check.ts`, `src/memory/scaffold.ts` (hook install), SPEC.md AC-2.

**Current state.** `forge gate check`, `forge consistency`, `forge vmodel check`, `forge bom check`,
`library/tools/sch_check.py`, `library/tools/pcb_check.py` (needs KiCad's Python `pcbnew`).

**Build.**
- New `forge/check.py`, command `forge check [--json] [--strict] [--fab] [--only a,b] [--module M-xx]`.
- Sub-checks, each returning `{status: pass|warn|fail|skip, findings:[{claim, actual, location}]}`:
  - `erc`: every root `.kicad_sch` under `08_PCB/` (same discovery as `gates.check` G6).
  - `drc`: every root `.kicad_pcb`, including unconnected count and schematic parity (from WP1).
  - `consistency`: `consistency.scan`.
  - `vmodel`: `vmodel.check`.
  - `bom`: `gates.bom_ok` on WIP BoM and each module BoM present.
  - `drift`: WP4 (skip with reason until WP4 lands).
  - `sch_lib`: `library/tools/sch_check.py --json --no-erc` (run as subprocess; `skip` with reason if it cannot run).
  - `pcb_extra`: `library/tools/pcb_check.py --json` (skip if `pcbnew` is unavailable).
  - `fab`: only with `--fab` (WP7).
- Exit code 0 when no `fail` (and, with `--strict`, no `warn` and no `skip` caused by a missing tool).
  A skipped check is never printed as passed.
- **Contract:** `forge/check.py` must not import anything that makes network calls or calls a model. Add a test
  that imports the module graph and asserts no `urllib`, `http`, `socket`-using module from `forge/` is loaded
  (mirror of copperhead's "check never imports a provider" test).
- `--json` output has stable keys: `{ok, checks: {name: {status, findings}}, duration_s}`.
- New command `forge hooks install [--uninstall]`: writes `.git/hooks/pre-commit` that runs
  `forge check --only erc,consistency,vmodel,bom,drift` and blocks the commit on fail. Refuses to overwrite a
  foreign hook (tell the user, offer `--chain` to call the existing hook first).
- `forge new` (scaffold) asks at intake whether to install the hook (default yes) and copies a CI template
  `template/.github/workflows/forge-check.yml` that installs KiCad 10 on ubuntu, then runs `forge check --json`
  and uploads the JSON as an artefact.
- `gates.check` should call the same sub-check functions so gates and `forge check` can never disagree.

**Acceptance criteria.**
- AC2.1 On a freshly scaffolded project with no KiCad files: `forge check` exits 0, ERC/DRC reported as `skip`
  with reason "no schematic yet", nothing reported as passed that did not run.
- AC2.2 With the short-circuit fixture copied into `08_PCB/`: exit 1, ERC finding names sheet and position.
- AC2.3 With a superseded term mentioned live in a doc: exit 1, `consistency` lists file:line.
- AC2.4 `forge check --json | python3 -m json.tool` succeeds; keys as specified.
- AC2.5 After `forge hooks install`, `git commit` of a change that introduces an ERC error is refused; with no
  errors it succeeds.
- AC2.6 Runs in under 60 s on the test fixture project (assert in test, skip when KiCad absent).
- AC2.7 No-network test passes.

**Tests.** As per ACs; hook test in a temp repo.

**Out of scope.** Fixing anything automatically.

---

### WP3: Transactional edits with lossless rollback (`forge txn`)

**Why.** copperhead wraps every mutation in a git snapshot; if verification fails it restores the tree
byte-for-byte, keeps the failed work as a named stash entry for inspection, and preserves the run audit trail.
pe-forge's `schdraw apply` keeps one `.bak` in a scratch folder, and agent edits made with Edit/Write (part
swaps, value changes, BoM edits, booklet updates across several files) have no rollback at all.

**Reference.** copperhead `src/util/git.ts` (read `snapshot`, `restore`, `snapshotUntracked`,
`restoreUntracked`, and the comments about why `git stash create` alone loses untracked files), SPEC.md
sections 4.5 and 7, AC-3.6, AC-3.8.

**Current state.** `forge/schdraw.py main()` apply branch; `forge/watch.py` already builds git tree objects
through a private index (reuse that technique for untracked files).

**Build.**
- New `forge/txn.py`, commands:
  - `forge txn begin "<what>" [--allow-dirty]` writes `.forge/txn.json`
    `{id, what, started, head, stash, untracked_tree, touched: []}`.
    - Refuse on a dirty tree without `--allow-dirty` (message lists the dirty files and the two ways out).
    - With `--allow-dirty`: `stash = git stash create` (tracked changes) plus `untracked_tree` built via a
      private index exactly like `watch.snapshot()`. Refuse by name any untracked file that cannot be read
      (it could not be restored).
    - Refuse if a transaction is already open.
  - `forge txn verify` runs `forge check --only erc,drc,drift,consistency` scoped to files changed since
    `begin`; prints result; does not close.
  - `forge txn commit [-m MSG]` requires a passing `verify` on the current tree hash, then `git add` the
    touched files and commits with message `MSG` plus a trailer `Forge-Txn: <id>`. Closes the transaction.
  - `forge txn rollback [--reason TEXT]`: first saves the failed work as a stash entry named
    `forge failed txn <id>: <what>` (only if anything changed), then `git reset --hard <head>`,
    `git clean -fd -e .forge/runs`, re-applies `stash` if present, re-materialises `untracked_tree`, and
    restores `.forge/runs/` from a temp copy taken before the reset (it may have been staged). Prints the stash
    ref and the run folder.
  - `forge txn status`.
- Every transaction writes `.forge/runs/<id>/summary.md`: what, start/end, files touched, verify results,
  outcome (committed / rolled back + reason). `.forge/runs/` is gitignored in the template.
- `forge schdraw apply` uses a transaction automatically when none is open: begin, write the sheet, run
  real-project ERC and KiCad-netlist-vs-brief comparison on the real file (not only the dry copy), commit on
  pass, roll back on fail. Keep the `.bak` behaviour for one release for safety.
- Skills: `pe-run`, `pe-bom`, `pe-schematic`, `pe-pcb` instruct the agent to wrap any multi-file design change
  in `forge txn begin` / `verify` / `commit`, and to roll back rather than hand-revert.
- Respect `forge watch`: a rollback must call `watch --mark` afterwards so the restored state is not reported
  as a user edit.

**Acceptance criteria.**
- AC3.1 Clean tree, begin, edit two tracked files and create one new file, rollback: `git status` clean, all
  files byte-identical to before, new file gone, a stash entry `forge failed txn ...` exists containing the edits.
- AC3.2 Dirty tree (one modified tracked file, one untracked file) without `--allow-dirty`: begin refuses.
  With `--allow-dirty`, edit, rollback: both the modified tracked file and the untracked file are back exactly
  as before.
- AC3.3 `.forge/runs/<id>/summary.md` survives a rollback even if it was staged.
- AC3.4 `schdraw apply` of a spec whose real-project ERC fails leaves the sheet byte-identical to before.
- AC3.5 `txn commit` refuses when the tree changed after the last passing verify.

**Tests.** Temp git repos; no KiCad needed except AC3.4 (skip without it).

**Out of scope.** Pushing; branch management.

---

### WP4: Structured drift check (`forge drift`)

**Why.** copperhead treats docs as the design memory and makes doc-vs-schematic disagreement a build failure,
reported as `{doc, claim, actual}`. pe-forge's consistency check only catches superseded words; it cannot see
that the BoM says 4.7 uF while the schematic says 10 uF, or that the sheet brief's netlist no longer matches a
user-drawn sheet.

**Reference.** copperhead `src/memory/drift.ts` (note the zero-symbol bootstrap carve-out and why it exists,
plus `emptySchematicWarning`), `src/memory/bom-table.ts` (header aliases, value and footprint normalisation).

**Current state.** `library/tools/kinet.py` (symbol and net extraction), `library/tools/sch_check.py`
(`db_rows`, MC_Parts lookups), BoM columns in `forge/gates.py BOM_REQUIRED`, templates
`template/90_Templates/T_Sheet_Brief.md`, `T_Design_Booklet.md`, `template/04_Hardware/BOM/WIP_BOM.csv`.

**Build.**
- New `forge/drift.py`, command `forge drift [--json] [--module M-xx]`, also a `forge check` sub-check.
- Comparisons (each finding is `{doc, claim, actual, location}`):
  1. **BoM vs schematic:** for each module `BOM.csv` and the WIP BoM, per Ref: exists in schematic; Value
     matches (normalise `4u7` = `4.7u` = `4.7 uF`; `10k` = `10 kΩ`); MPN or MC_ID matches the symbol's fields;
     footprint matches. Also schematic refs missing from the BoM (excluding power symbols, `Exclude_BOM`,
     DNP marked consistently on both sides).
  2. **Sheet brief netlist vs KiCad netlist:** parse the brief's netlist table/JSON, compare to
     `forge.kicad.netlist()` per net (same comparison `schdraw` already does in its K1 check; move that logic
     into `drift.py` and have schdraw call it). This makes user-drawn sheets (`schematic_mode = user`) get the
     same contract check that agent-drawn sheets get.
  3. **Design booklet values vs schematic:** if the booklet has a table with `Ref` and `Value` columns, compare
     like (1). Free prose is not checked (state this in the doc and the skill).
- Header matching by alias (`Ref`, `Reference`, `Designator`, `RefDes`; `MPN`, `Part Number`; ...) so
  reordered columns never break the reader. Use one table parser shared with WP8 export.
- **Bootstrap carve-out:** if a sheet has zero symbols, skip BoM-vs-schematic for it (BoMs legitimately lead
  the schematic in P4/P6), but emit a `warn` when the BoM lists refs and the module is past G6.

**Acceptance criteria.**
- AC4.1 Change one resistor value in a module BOM.csv: `forge drift` exits 1 and reports
  `{doc: modules/M-01.../BOM.csv, claim: "R5 = 10k", actual: "R5 = 4.7k in power.kicad_sch"}`.
- AC4.2 Add a wire in a user-drawn sheet that shorts two nets in the brief: drift reports the net mismatch.
- AC4.3 Equivalent value spellings never produce a finding (table-driven test of the normaliser).
- AC4.4 Empty sheet with a populated BoM before G6: no failure; after G6 passed: a warning.

**Out of scope.** Auto-fixing drift (that is the agent's job inside a WP3 transaction).

---

### WP5: Obligations ledger enforced by Claude Code hooks

**Why.** copperhead turns "keep everything in sync" into a mechanical gate: deterministic hooks after each tool
call record obligations (ERC needed, drift needed, changelog needed, constraint revisit), and `finish` is
refused while any is open. pe-forge already blocks Stop on superseded terms; generalise that.

**Reference.** copperhead `src/agent/ledger.ts` (`ObligationKind`, the reasoning about `gateLegibility`).

**Current state.** `hooks/hooks.json`, `forge/consistency.py main --hook` (exit 2 pattern, `stop_hook_active`
handling), `forge/watch.py`.

**Build.**
- New `forge/ledger.py`, state in `.forge/ledger.json`: list of `{kind, target, opened_by, opened_at, target_hash}`.
- Command `forge ledger record --hook` (PostToolUse on `Edit|Write|MultiEdit|Bash`): reads the hook JSON from
  stdin; for each file path the tool touched (for Bash, compare a cheap snapshot of `*.kicad_sch`,
  `*.kicad_pcb`, `*.csv`, `*.md` mtimes against the last one stored in the ledger) open obligations:
  | Touched | Obligation |
  |---|---|
  | `*.kicad_sch` | `erc:<sheet>`, `drift:<module>` |
  | `*.kicad_pcb` | `drc:<board>` |
  | module `BOM.csv`, WIP BoM | `drift:<module>`, `bom:<file>` |
  | `Design_Booklet.md`, `Sheet_Brief.md` | `drift:<module>` |
  | `.forge/superseded_terms.csv` | `consistency` |
  | `02_Requirements/**`, `trace.csv` | `vmodel` |
  | anything under a module after its G6 passed | `changelog:<module>` (a dated line in the module's revision history) |
- Command `forge ledger check --hook` (Stop hook, before `watch --mark`): re-runs only the checks for open
  obligations (via WP2 sub-checks); closes those that pass; if any remain open, exit 2 with a list and the
  exact command to see details. Respect `stop_hook_active` like `consistency --hook` does, so it cannot loop.
- Command `forge ledger show` and `forge ledger clear --reason TEXT` (manual escape hatch, logged to
  `01_Program/QC/QC_Register.md` as an override row).
- Merge the existing consistency Stop hook into the ledger (keep `forge consistency --hook` working as an alias).
- Obligations opened by **user** edits (seen by `watch --report`) are recorded too but reported as advisory on
  the first Stop, then blocking: the user's edit is design intent, but its consequences still need ERC/drift.
- Legibility (WP11) obligations block only for agent-drawn sheets (copperhead's rule: check reports, the
  drafting path gates).

**Acceptance criteria.**
- AC5.1 Agent edits a `.kicad_sch` that introduces an ERC error, then tries to stop: Stop is blocked with
  "open: erc:<sheet>".
- AC5.2 Agent fixes it: the next Stop passes and the obligation is closed with a timestamp.
- AC5.3 With `stop_hook_active` true the hook does not block again (no loop).
- AC5.4 Hooks are silent no-ops outside a pe-forge project (as today).

**Out of scope.** Tracking obligations across sessions on different machines.

---

### WP6: Change-gated design edits after baseline

**Why.** copperhead's first invariant: the agent cannot edit a KiCad file until a validated change proposal
exists; the edit tools are literally unavailable. pe-forge's equivalent today lives in prompts and reviewers.
Locking always would fight pe-forge's drafting phases, so lock only **after a module's schematic is
baselined** (its G6 passed) and after G7 for the board. That is classic engineering change control.

**Reference.** copperhead SPEC.md sections 1.3 and 2.6 (change workflow), AC-3.x; the "undispatched calls"
handling in section 4.4 shows how strict the lock is.

**Current state.** `forge vmodel cr CR-ID [SYS ...] --why TEXT`, `forge/state.py` (gate status per module),
`template/90_Templates/T_ADR.md`.

**Build.**
- Change requests as files: `01_Program/Changes/CR-NNN.md` with frontmatter
  `id, title, status (draft|approved|implemented|closed|rejected), modules, files_allowed (globs), requirements (SYS/HWR ids), reason, approved_by, approved_on`.
  Template `template/90_Templates/T_Change_Request.md`.
- `forge cr new "<title>" --modules M-02 [--files "08_PCB/m02/**"]`, `forge cr validate CR-NNN` (fields
  present, modules exist, if requirements changed then `vmodel impact` output is attached), `forge cr approve
  CR-NNN` (**only the owner runs this**; the skill must ask Mohit, never self-approve; record approver from
  `git config user.name`), `forge cr close CR-NNN` (requires `forge check` pass and links the WP3 txn commit).
- PreToolUse hook `forge cr guard --hook` on `Edit|Write|MultiEdit`: if the target path is a KiCad file,
  module BoM, booklet or sheet brief belonging to a module whose G6 (or board G7) has passed, and no
  `approved` CR lists that module and matches the path in `files_allowed`, deny with
  `permissionDecision: "deny"` and a reason that tells the agent to open a CR. `forge schdraw apply` and
  `forge txn begin` perform the same check (they run through Bash).
- On close, append the CR to the module's revision history and to the V-model change log (`vmodel cr`).
- `profile = lite` projects: guard warns instead of denying (configurable in `project.toml`
  `[process] change_control = "enforce" | "warn" | "off"`).

**Acceptance criteria.**
- AC6.1 Module with G6 passed, no CR: an Edit to its sheet is denied with a message naming `forge cr new`.
- AC6.2 Same module with an approved CR covering the path: the Edit is allowed.
- AC6.3 Module before G6: edits are never blocked.
- AC6.4 `forge cr approve` run from inside an agent context (env `CLAUDE_CODE` style marker or no TTY) refuses
  unless `--i-am-the-owner` is passed; the skill text forbids passing it.
- AC6.5 `change_control = "off"` disables the guard completely.

**Out of scope.** Multi-approver workflows.

---

### WP7: Fab release gate (`forge check --fab`)

**Why.** "Can I order this board today?" answered deterministically. copperhead's design is a good template:
routing completeness from DRC, schematic-PCB match, BOM readiness, output freshness by **content hash** (not
mtime), documentation presence; `UNVERIFIED` parts warn by default and fail with `--strict`.

**Reference.** copperhead `openspec/changes/add-fab-release-gate/design.md` (decisions D1 to D6),
`src/kicad/fab.ts`.

**Current state.** `skills/pe-release/SKILL.md`, `library/template/MC_Standard/release.sh` and
`Release.kicad_jobset`, `forge/phases.py` P8 artefacts.

**Build.** A `fab` sub-check in `forge/check.py` with these parts, each `pass|warn|fail`:
- `routing`: DRC `unconnected` empty (WP1 report).
- `sch_pcb_match`: DRC schematic parity empty, plus refdes and footprint join between schematic symbols and
  board footprints (both from s-expressions).
- `bom_ready`: every placed part has MPN, manufacturer, MC_Parts status not `Seed`/`Obsolete`, alternate
  present (reuse `bom_ok`); `NRND` and unreviewed parts warn, fail with `--strict`.
- `outputs_fresh`: `release.sh` writes `09_Release/<REV>/manifest.json` with SHA-256 of the `.kicad_pcb`,
  each `.kicad_sch`, the BoM CSV, plus the list of generated files and their hashes. The gate recomputes and
  fails on any mismatch with the one-line fix (`re-run ./release.sh`).
- `outputs_complete`: Gerber set covers every copper layer in the board stack-up plus mask, paste, silk and
  edge cuts; drill file(s) present; CPL row count equals placed SMD parts (excluding `Exclude_Board` and DNP).
- `power_rules`: PE-specific, worth adding because copperhead has nothing like it: `pcb_check.py`
  `PCB-PWRTRACE` and `PCB-THERMAL` findings are `fail`, and the board's net classes include the HV class
  whenever any net matches the HV patterns.
- `docs`: `Release_Notes.md`, `13_Validation/DVP_and_R.md`, `Layout_Spec.md` exist and are filled
  (`gates.filled`).
- Gate G8 automatically includes `forge check --fab --strict`.

**Acceptance criteria.**
- AC7.1 Edit the board after running `release.sh`: `outputs_fresh` fails naming the file.
- AC7.2 Remove a Gerber layer file: `outputs_complete` fails naming the layer.
- AC7.3 A part with status `Seed`: `bom_ready` fails; with `NRND`: warn, fail under `--strict`.
- AC7.4 `--json` has keys `fab.{routing, sch_pcb_match, bom_ready, outputs_fresh, outputs_complete, power_rules, docs}`.

**Out of scope.** Fab-house upload or quoting; panelisation and impedance checks.

---

### WP8: Native JLCPCB BoM + CPL export, LCSC field, spares

**Why.** copperhead exports JLCPCB, DigiKey and Mouser formats as a pure transform of an already
drift-checked BoM, with quantity for boards plus spares. pe-forge's `bom.py` does DigiKey and Mouser well but
relies on the external `kicad-happy` skill for JLCPCB.

**Reference.** copperhead `src/kicad/bom-export.ts`, `openspec/changes/add-supplier-bom-export/`.

**Current state.** `library/tools/bom.py` (`--boards N`), `library/database/schema.sql`,
`library/tools/mclib.py`, `skills/pe-release/SKILL.md` step 3.

**Build.**
- Schema: add `LCSC_PN TEXT NOT NULL DEFAULT ''` and `JLC_Rotation TEXT NOT NULL DEFAULT ''` (rotation offset
  in degrees for the CPL) to every table via a migration in `mclib.py` (idempotent `ALTER TABLE` per table;
  bump a `schema_version` row).
- `bom.py --jlcpcb`: writes `<name>-jlcpcb-bom.csv` (columns `Comment, Designator, Footprint, LCSC Part #`)
  and `<name>-jlcpcb-cpl.csv` (`Designator, Mid X, Mid Y, Layer, Rotation`) from
  `kicad-cli pcb export pos --format csv --units mm --side both --exclude-dnp`, renaming columns and applying
  `JLC_Rotation`. Parts with no LCSC number are listed on stderr and the command exits 1 unless `--allow-missing`.
- `--spares N` (default 0) for DigiKey/Mouser quantities: per line `qty_per_board * boards + spares`, with a
  rule for passives (spares = max(N, 10% rounded up)). JLCPCB files ignore it (they place exactly what is on
  the board) and say so once if the user set it.
- `release.sh` calls `bom.py --jlcpcb` when the project's `project.toml` says `fab_house = "jlcpcb"`.
- Update `skills/pe-release/SKILL.md` step 3 to use the native export; keep `kicad-happy` as an optional
  cross-check.

**Acceptance criteria.**
- AC8.1 Fixture board: JLC BoM has one line per value/footprint/LCSC group with designators joined; CPL has one
  row per placed SMD part.
- AC8.2 A part without LCSC: exit 1 and the part is named; with `--allow-missing`: exit 0 and a warning.
- AC8.3 Rotation offset of 90 for a part changes only that part's CPL rotation.
- AC8.4 Migration is idempotent (run twice, schema unchanged the second time).

**Out of scope.** Live LCSC stock lookups (later, via the datasheet/sourcing work in WP13).

---

### WP9: `forge explain` deterministic lookup

**Why.** Cheap, high value: agents stop paging raw s-expressions or netlists to answer "what is on this net"
or "what drives U3 pin 7". copperhead measured that pin lookups by reading files cost most of a stage's turns.

**Reference.** copperhead `openspec/changes/add-explain-command/`, `src/commands/explain.ts`, SPEC.md AC-17;
also `openspec/changes/add-symbol-pin-dossier/` (pin tables up front).

**Current state.** `library/tools/kinet.py load()`, `forge schdraw pins`.

**Build.**
- `forge explain <query> [--json] [--sheet PATH]`:
  - `U1`: value, MC_ID, MPN, footprint, sheet, every pin `number name -> net`; matching BoM row(s), booklet and
    brief mentions (file:line).
  - `NET_NAME`: every `REF.PIN` on it, net class, and which brief / booklet lines mention it.
  - `U1.7` or `U1.VCC`: the net; a pin name unique across the design resolves without a refdes.
  - Unknown: exit 1 with up to 5 nearest refs/nets (difflib).
- `forge explain --pins MC_ID`: the library symbol's pin table and unit count, warn if multi-unit (feeds the
  drafter before it writes a spec).
- No LLM, no network, no writes.
- Mention it in `skills/pe-schematic/SKILL.md`, `agents/pe-schematic-drafter.md`, `agents/schematic-reviewer.md`,
  `agents/pe-sw.md` (firmware pin mapping), and `skills/kicad/SKILL.md`.

**Acceptance criteria.**
- AC9.1 `forge explain GND` on the fixture lists every GND pin; `--json` parses with `ok: true, kind: "net"`.
- AC9.2 `forge explain U1.3` names the net; a repeated pin name without refdes fails with "use REF.PIN".
- AC9.3 Unknown query exits 1 with suggestions.

---

### WP10: `forge-mcp`, outcome-level MCP server

**Why.** Makes pe-forge usable from any MCP host (Cursor, Codex, Claude Desktop, another agent) while keeping
the gates: copperhead exposes only outcome tools and **no raw file-edit tool**, so a host agent cannot route
around verification.

**Reference.** copperhead `src/mcp/server.ts` (tool descriptions are worth reading for tone: each says
whether it calls a model, touches the network, or changes anything). In pe-forge, follow
`vendor/pwrlab/pwrlab/mcp_server.py` (its result contract `usable / verdict / checks / warnings` and
background jobs for long calls).

**Build.**
- `forge/mcp_server.py` (may depend on the same `mcp` package pwrlab uses; keep it out of the stdlib-only
  engine import path so `forge check` stays dependency-free). Entry point `forge-mcp`; `bootstrap.sh`
  registers it like pwrlab.
- Tools: `forge_status`, `forge_check` (args: only, fab, strict), `forge_explain`, `forge_gate_check`,
  `forge_drift`, `forge_schdraw_check` (spec JSON in), `forge_schdraw_apply` (runs inside a WP3 transaction and
  respects WP6 change control), `forge_cr_new`, `forge_cr_status`.
- Every result follows pwrlab's contract: `usable`, `verdict` (one honest sentence), `checks`, `warnings`.
- No tool writes arbitrary files. No tool approves a CR or passes a gate (those stay with the owner).
- Paths from the host are resolved inside the project root; anything outside is rejected.

**Acceptance criteria.**
- AC10.1 `forge_check` over MCP returns the same JSON as `forge check --json`.
- AC10.2 A path argument `../../etc/hosts` is rejected.
- AC10.3 `forge_schdraw_apply` on a baselined module without an approved CR is refused with the CR hint.
- AC10.4 Tool list contains no generic write/edit tool (asserted in a test).

---

### WP11: Legibility lint for any sheet, with an advisory score

**Why.** `schdraw` holds agent-drawn sheets to 16 strict rules, but user-drawn sheets (and sheets edited in
KiCad after drafting) get no readability check. copperhead's split is right: findings are **advisory** on
hand-drawn sheets and **blocking** only on sheets the engine authored; a human re-saving the sheet in KiCad
is the deliberate one-way door out of engine ownership.

**Reference.** copperhead `src/kicad/legibility.ts` (families: off-grid, symbol-overlap, text-collision,
wire-through-symbol, out-of-frame, ungrouped-symbol, unlabeled-group, group-overlap, empty-title-block,
label-orientation, low-utilization, crowding, cross-group-wire), `src/kicad/score.ts`, and in `fab.ts` the
`isEngineAuthoredSchematic` generator-stamp idea.

**Current state.** `forge/schdraw.py` `style_check`, `text_check`, `overlap`, `graphics_bbox`;
`skills/pe-schematic/references/style-guide.md`.

**Build.**
- `forge schdraw lint <sheet.kicad_sch> [--json]`: reconstruct geometry from an existing sheet (symbols with
  bboxes, wires, labels, power symbols, text) and run every pe-forge style rule that does not need a spec,
  plus copperhead's families that pe-forge lacks (out-of-frame, empty-title-block, low-utilization, crowding,
  label-orientation).
- Score 0 to 100, reported as a number plus the top 5 issues; never affects exit code for user-drawn sheets.
- Authorship: `schdraw apply` stamps the sheet with a property or comment `pe-forge:schdraw <spec-hash>`.
  If the sheet's content hash no longer matches the stamp (the user edited and saved it), it is treated as
  user-owned: advisory only. Document this in the style guide.
- `forge check` includes `legibility` as advisory; WP5 opens a blocking `legibility` obligation only for
  engine-owned sheets.

**Acceptance criteria.**
- AC11.1 A sheet that passed `schdraw check` lints with 0 errors.
- AC11.2 Moving one symbol off-grid in KiCad and saving: lint reports `off-grid` as advisory, exit 0.
- AC11.3 Score is deterministic (same sheet, same score).

---

### WP12: ngspice assertion gate for small analog circuits

**Why.** pwrlab verifies power stages with LTspice and closed-form cross-checks; that is pe-forge's strength.
But many failures on real boards are in small circuits pwrlab does not cover: current-sense amplifier gain
and filter corner, divider into an ADC with its source impedance, gate-drive RC, UVLO / OVP comparator
thresholds, aux supply feedback, NTC bias. copperhead's proposed gate turns these into declarative
assertions checked by ngspice from the schematic netlist.

**Reference.** copperhead `src/kicad/spice.ts` (assertion grammar and comparator types),
`openspec/changes/add-spice-verification-gate/` (scope, skip semantics, `--strict`).

**Current state.** `vendor/pwrlab` (provenance, checks, report patterns to reuse); `T_Sheet_Brief.md`;
`T_SVR.md` (Simulation Verification Record).

**Build.**
- A `## Simulation` block in the sheet brief, e.g.:
  ```
  ## Simulation
  scope: nets ISENSE_P, ISENSE_N, ADC_IOUT
  analysis: ac 10 1e7
  source: ISENSE_P = 0.05 ac 1
  assert: gain(ADC_IOUT, ISENSE_P) between 19.5 and 20.5 at 1k
  assert: f3db(ADC_IOUT) between 180k and 220k
  assert: V(ADC_IOUT) < 3.3 at op
  ```
  Implement `op`, `dc`, `ac`, `tran`; comparators `between`, `<`, `>`; measures `V()`, `I()`, `gain()`,
  `f3db()`, `peak()`, `settle()`. Keep the grammar small and documented in
  `skills/pe-simulate/references/assertions.md`.
- `forge spice check [--module M-xx]`: export netlist with `kicad-cli sch export netlist --format spice`,
  keep only the scoped nets and their parts, add sources, run `ngspice -b`, evaluate, write an SVR-style
  result file under the module's `Simulations/` so G5 counts it.
- Parts need SPICE models: add `Sim_Model` and `Sim_Pins` fields to MC_Parts (same migration approach as WP8);
  passives get ideal models automatically; a part with no model makes the assertion `skip` with the part
  named, `fail` under `--strict`.
- Every assertion also carries a closed-form expectation where one exists (`expect: 20 V/V`), and the check
  flags disagreement above 2% between ngspice and the closed form: the pwrlab rule that no number is believed
  without a second source.
- `forge check` runs it when ngspice is installed; otherwise `skip` with an install hint, never a pass.
- **Run the `roast` agent on this WP before committing.** It is a simulation check.
- Before building, look at the `kicad-happy:spice` skill already installed (see `skills/kicad/SKILL.md`) and
  reuse its netlist handling if it fits, rather than writing a second exporter.

**Acceptance criteria.**
- AC12.1 A divider fixture with an assertion that holds passes; changing one resistor makes it fail with the
  measured value and the bound.
- AC12.2 ngspice absent: `skip` with hint; `--strict` makes it fail.
- AC12.3 Closed-form disagreement above 2% is reported even if the assertion bound passes.

**Out of scope.** Switching power stages (pwrlab's job); EMC simulation.

---

### WP13: Datasheet evidence cache and VERIFIED status

**Why.** pe-forge already requires every symbol and footprint to be checked against the datasheet; the gap is
evidence. copperhead's proposal caches each datasheet in the repo by content hash with URL and retrieval date,
and a part only becomes `VERIFIED(datasheet)` when the confirming section is cited. That makes the part
librarian's work auditable offline and reproducible.

**Reference.** copperhead `openspec/changes/add-part-research-tools/` (proposal and design).

**Current state.** `agents/part-librarian.md`, `library/tools/fp_qa.py`, `library/tools/mouser.py`,
MC_Parts `Datasheet`, `Status`, `Sourcing_Date` fields.

**Build.**
- Cache: `$MC_LIB/datasheets/<sha256[:16]>.pdf` plus `$MC_LIB/datasheets/index.csv`
  (`sha256, mpn, manufacturer, url, retrieved, pages, title`). Git LFS recommended; document it. Licensing:
  datasheets are third-party copyrighted documents; keep the cache out of the public plugin repo (gitignored
  in pe-forge, committed only in the private MC_Lib).
- MC_Parts fields: `Verified_Against` (`sha256#page/section`), `Verified_By`, `Verified_On`. `Status`
  `Active` requires these to be filled; `fp_qa.py` checks pad count and pitch against the cited page values.
- `forge check` sub-check `parts_evidence`: every placed part's `Verified_Against` points at a cached file
  that exists and whose hash matches; sourcing snapshot older than N days (`project.toml`, default 90) warns.
- Update `agents/part-librarian.md` and `skills/pe-bom/SKILL.md` to fetch, cache, cite.

**Acceptance criteria.**
- AC13.1 Part with `Verified_Against` pointing to a missing hash: `parts_evidence` fails.
- AC13.2 Sourcing snapshot older than the limit: warn.

---

### WP14: Branded status line and hook chrome (custom UI inside Claude Code)

**Why.** copperhead built its own terminal app to get a logo, a brand colour and a fixed footer that always
shows run state (see section 1.2). We get most of that value without leaving Claude Code: Claude Code runs a
user-defined **status line** command after each assistant message and shows its output in a fixed bar at the
bottom of the screen, and hooks can print short user-facing messages. pe-forge's project state (phase, module,
gate, obligations, open transaction) is exactly what an engineer wants visible at all times instead of asking
for `forge status`. This is "level 1" of the custom-UI options: no new app, no agent loop, nothing that
breaks when Claude Code updates.

**Reference.**
- Claude Code docs, status line: <https://code.claude.com/docs/en/statusline>. **Read this first and code
  against it, not against this plan:** confirm the `statusLine` settings keys (`type`, `command`, `padding`),
  every field of the JSON passed on stdin (at least `model.display_name`, `workspace.current_dir`,
  `workspace.project_dir`; context-window and cost fields may be present), when it refreshes (after
  assistant messages, debounced; a run still in progress is cancelled when a new refresh starts), whether
  more than one output line is shown, and whether a plugin can ship a status line itself (assume it cannot;
  install into settings instead).
- Claude Code docs, hooks: the `systemMessage` field (shown to the user) versus `additionalContext` (shown
  to the model only).
- copperhead `src/agent/theme.ts` (truecolor with 256-colour fallback, colour disabled when not wanted),
  `src/agent/logo.ts` (block-character mark), `src/layout/repl-ui-layout.md` (row-by-row layout spec with
  colour annotations: write the same kind of spec for ours before coding).

**Current state.** `forge/cli_state.py status()` (prints phases, modules, next action; `ICON` map),
`forge/state.py` (`load`, `next_action`), `forge/meter.py` (scans Claude Code transcripts: far too slow to
call from a status line), `hooks/hooks.json`, `forge/consistency.py` and `forge/watch.py` hook output,
`bootstrap.sh` (already edits `~/.claude/settings.json` for `env`).

**Build.**
1. **Shared theme module `forge/ui.py`** (stdlib), used by the status line, hook messages and `forge status`:
   - `paint(style, text)`; truecolor when `$COLORTERM` is `truecolor`/`24bit`, else 256-colour fallback;
     no colour when `$NO_COLOR` is set or `[ui] color = false`.
   - Palette: `accent` (brand, default `#b87333`, overridable as `[ui] accent = "#RRGGBB"` in `project.toml`
     and in a user-level `~/.config/pe-forge/ui.toml`), `dim`, `bright`, `ok` (green), `warn` (amber),
     `err` (red).
   - Glyphs: `✓ ▶ ? · – ⚠ ▸ ◆` with an ASCII fallback set (`[ui] glyphs = "ascii"`) for terminals and fonts
     that lack them. Reuse `cli_state.ICON` semantics so status words never diverge.
   - `visible_len()` that ignores ANSI codes and counts wide characters correctly, and `fit(segments, width)`
     that drops the lowest-priority segments until the line fits (never cuts a segment in half).
   - Do not add dependencies (`rich` is reserved for a later level-2 app).
2. **`forge statusline`** (new command; reads the Claude Code JSON from stdin, prints one line):
   - Find the project from `workspace.project_dir`, then `workspace.current_dir`, then cwd. Outside a pe-forge
     project, run the previously installed status line command if one was saved (see install, below) with the
     same stdin, else print a minimal `model · folder` line. Never print an error or a traceback into the bar:
     on any exception print the minimal line and log the error to `.forge/cache/statusline.log`.
   - Segments, highest priority first:
     | Segment | Example | Source | Colour |
     |---|---|---|---|
     | mark + project | `◆ Buck48to12` | `prj.name` | accent, bold |
     | phase | `P6 Schematic` | `state.json` `phase` + `phases.py` short title | bright |
     | module | `M-02 Power stage` | `active_module` | bright |
     | gate | `G6 ? review` / `G6 ✓` | phase/module status | ok / warn |
     | obligations | `⚠ 2 open: erc, drift` | `.forge/ledger.json` (WP5) | warn; omit when 0 or file absent |
     | transaction | `txn open: swap Q3` | `.forge/txn.json` (WP3) | warn; omit when none |
     | change control | `CR-007` / `locked` | WP6 state for the active module | dim / warn |
     | last check | `check ✓ 14:02` / `check ✗ ERC` | `.forge/cache/check.json` (WP2 writes it) | ok / err |
     | budget | `meter AMBER` | `.forge/cache/meter.json` | ok / warn / err |
     | model | `Opus` | stdin `model.display_name` | dim |
     | git | `feat/wp03 ●` | `.git/HEAD` read directly, dirty marker from the watch snapshot if cheap | dim |
   - Separator ` · ` in `dim`. Width: Claude Code does not give the status line a TTY, so take the width from
     `[ui] statusline_width` (default 110) and let `fit()` drop segments from the bottom of the table.
   - If the docs confirm multi-line output is supported, add an optional second line (`[ui] statusline_lines = 2`)
     with `Next: <next_action text>` in dim. Default stays one line.
   - **Performance contract: under 100 ms, no subprocesses except at most one cheap `git` call, no network, no
     `kicad-cli`, no transcript scan.** It only reads small JSON files. Add a test that times it.
   - Missing files (projects without WP2 to WP6 yet) simply drop their segments; the command must work on
     today's pe-forge with only `state.json` present.
3. **Caches so the status line stays fast:**
   - `forge meter --cache` writes `.forge/cache/meter.json` (`{level, day_units, window_units, at}`); run it from
     the existing Stop hook (append to the Stop hook list, timeout 30 s) instead of from the status line.
   - `forge check` (WP2) writes `.forge/cache/check.json` (`{ok, failed: [names], at}`) on every run; until WP2
     lands, `forge gate check` writes it.
   - Add `.forge/cache/` to `template/.gitignore`.
4. **Install / uninstall: `forge statusline install [--user | --project] [--uninstall]`.**
   - `--project` (default inside a forge project) edits `<project>/.claude/settings.json`; `--user` edits
     `~/.claude/settings.json`. Merge, never overwrite: read the JSON, set
     `statusLine = {"type": "command", "command": "<abs path to bin/forge> statusline", "padding": 0}`, keep
     every other key, write atomically (temp file + rename), keep a `.bak` of the previous file.
   - If a different `statusLine` was already set, save it to `~/.config/pe-forge/statusline_prev.json` and
     chain to it outside pe-forge projects (so Mohit's own status line keeps working elsewhere).
   - `--uninstall` restores the previous `statusLine` (or removes the key) and leaves everything else.
   - `bootstrap.sh` offers the user-scope install (asks first, like its other steps); `forge new` offers the
     project-scope install at intake (default yes).
   - Tell the user once after install that Claude Code may need a restart to pick it up.
5. **Hook chrome (branded, short, user-facing messages):**
   - One helper `ui.hook_message(text)` that emits the hook JSON with `systemMessage` prefixed `pe-forge ▸`.
     Keep model-facing text (`additionalContext`) separate and unbranded, as today.
   - **SessionStart:** one line, e.g. `pe-forge ▸ Buck48to12 · P6 Schematic · M-02 · next: pass G6`
     (only inside a forge project). Verify in the hooks docs that `systemMessage` is displayed for this event;
     if it is not, skip the banner rather than inject it into the model context.
   - **Stop, when checks ran:** `pe-forge ▸ ERC ✓ · drift ✓ · consistency ✓` or the failures in `err` colour,
     once per change of result (do not repeat an identical line every turn; remember the last line in
     `.forge/cache/`).
   - **Gate passed / phase started** (`forge gate pass`, `forge phase start`): print a one-line coloured
     confirmation in the CLI using `ui.py`.
   - Never print more than two lines per hook event. The conversation must stay readable.
6. **`forge status` restyle:** same output content and order as today (scripts and tests may parse it), with
   colour from `ui.py` when stdout is a TTY, plain text otherwise. Add a three-row block-character pe-forge
   mark above it only with `forge status --banner` (keep the default compact).
7. **Docs:** a short "Your terminal" section in `README.md` with a screenshot or an ASCII mock of the bar, the
   install command, the `[ui]` options and how to uninstall. Write the layout spec first as
   `docs/ui-layout.md` (copperhead's `repl-ui-layout.md` style: each row, each segment, its colour) and get
   Mohit's OK on it before coding the renderer.

**Acceptance criteria.**
- AC14.1 Inside a scaffolded project with only `state.json`: `echo '{"model":{"display_name":"Opus"},"workspace":{"current_dir":"<prj>","project_dir":"<prj>"}}' | forge statusline`
  prints one line containing the project name, current phase and `Opus`, exit 0.
- AC14.2 With `.forge/ledger.json` holding two open obligations, the line contains `2 open`; with the file
  deleted the segment disappears and nothing else changes.
- AC14.3 Outside a forge project with a saved previous status line command, its output is printed unchanged.
- AC14.4 Invalid JSON on stdin, or a corrupt `state.json`: the minimal line is printed, exit 0, error logged.
- AC14.5 `NO_COLOR=1`: output contains no `\x1b[` sequences. `[ui] glyphs = "ascii"`: no non-ASCII characters.
- AC14.6 `fit()` with width 40 keeps the mark, project and phase and drops lower-priority segments whole.
- AC14.7 Median runtime of 20 runs under 100 ms on the test fixture project (test skips the assertion on CI
  machines flagged slow, but always records the number).
- AC14.8 `install` then `uninstall` leaves `settings.json` byte-identical to the original when no status line
  existed before, and restores the previous one when it did. Other keys (`env`, `permissions`, hooks) are
  untouched in both cases.
- AC14.9 The Stop-hook check line is not repeated when the result has not changed since the last turn.
- AC14.10 `forge status` without a TTY prints exactly the same text as before this WP (diff test).

**Tests.** All of the above with stdin fixtures and temp settings files; no Claude Code needed to run them.

**Out of scope.** A full terminal app (Agent SDK + `rich`/`Textual`) and a web UI: those are "level 2" and
"level 3" and get their own plan if a demo or customer needs them. `forge/ui.py` is written so that a level-2
app can reuse the theme and glyphs.

---

## 4. Order of work

| Phase | WPs | Why this order | Rough effort |
|---|---|---|---|
| A0. Quick win | WP14 | Independent of the others (missing state files just drop segments); visible every day from the first hour | 1 day |
| A. Foundation | WP1, WP2, WP3 | Structured reports, one check entry point and rollback are what everything else uses | 3 to 4 days |
| B. Enforcement | WP4, WP5, WP6 | Drift gives the ledger something real to enforce; change control sits on top of txn | 4 to 5 days |
| C. Release and lookup | WP7, WP8, WP9 | Directly shortens the 11 kW product's release; explain is quick | 3 to 4 days |
| D. Reach and depth | WP10, WP11, WP12, WP13 | Valuable, but each is independent and can wait for real need | 6 to 8 days |

Quick wins if time is short: WP1 + WP2 + WP9 (about two days) already give a CI-safe check and saves agent
turns on every schematic task.

---

## 5. Cross-cutting requirements

- **Honest status everywhere.** A check that did not run is `skip` with a reason, never `pass`. Exit 0 from a
  tool is not evidence (WP1 `tool_ok`). This is pwrlab's rule; apply it to all new code.
- **Same voice for every finding:** `{claim, actual, location}` (copperhead's drift voice). Human output one
  line per finding: `location: claim, actual`.
- **Manual edits stay design intent** (pe-forge Rule 5/6). Nothing in these WPs may revert a user edit
  silently. Rollback only touches changes made inside the transaction being rolled back.
- **Lite profile:** every new blocking behaviour has a `project.toml` switch and a lite default documented in
  the README.
- **Performance:** `forge check` (without `--fab` and without WP12) under 60 s on a 3-sheet project.
- **Backward compatibility:** existing projects without the new files keep working; new files are created on
  first use, never required retroactively for passed gates.

---

## 6. Tracker

| WP | Title | Branch | Status | Notes |
|---|---|---|---|---|
| WP1 | Shared KiCad CLI layer, JSON reports | `feat/wp01-kicad-json` | done | ERC JSON positions are 100 mm units in KiCad 10.0.6; old G6 regex missed KiCad 10 wording (now fixed) |
| WP2 | `forge check`, pre-commit, CI | `feat/wp02-forge-check` | done | empty sheet or board = skip ("no schematic yet"); gates G6/G7 call the same sub-checks and treat skip as not passed |
| WP3 | Transactions with rollback | `feat/wp03-txn` | done | schdraw apply closes its txn without committing (commit rule stays with the owner); same-stem .kicad_pro rule for text variables |
| WP4 | Structured drift | `feat/wp04-drift` | done | module sheet from brief frontmatter `kicad_sheet`; shared tables.py (aliases, value normaliser) for WP8 |
| WP5 | Obligations ledger hooks | `feat/wp05-ledger` | done | replaces the consistency Stop hook (alias kept); user edits advisory once, then blocking |
| WP6 | Change-gated edits after baseline | `feat/wp06-change-control` | done | lock check runs at txn verify (begin has no file list); shell edits caught there; lite = warn |
| WP7 | Fab release gate | `feat/wp07-fab-gate` | done | manifest lives with the outputs (08_PCB/<slug>/release/<REV>/manifest.json); parts under checks.fab.parts |
| WP8 | JLCPCB export, LCSC, spares | `feat/wp08-jlc-export` | done | bundled DB migrated to schema 2; MC_DB env overrides the DB path (tests); --pos FILE input |
| WP9 | `forge explain` | `feat/wp09-explain` | done | KiCad 10 netlist pin names carry a "_<pin>" suffix: stripped |
| WP10 | `forge-mcp` | `feat/wp10-mcp` | done | tools in stdlib forge/mcp_tools.py (testable without mcp), registered by forge/mcp_server.py; runs forge with the server interpreter (MCP hosts pass a minimal PATH) |
| WP11 | Legibility lint + score | `feat/wp11-lint` | done | ownership in .forge/schdraw_owned.json (no stamp on the drawing); fixed lib_symbols indentation (engine sheets hid their parts from existing()) |
| WP12 | ngspice assertion gate | `feat/wp12-spice-assert` | done | `roast` agent does not exist in pe-forge: independent review still owed; deck nodes renamed n_NET (ngspice reads / as division); schema 3 Sim_Model / Sim_Pins |
| WP13 | Datasheet evidence cache | `feat/wp13-datasheets` | done | cache gitignored here; missing evidence warns (fails --strict) so existing libraries keep working; pad check in forge (stdlib) instead of fp_qa |
| WP14 | Branded status line + hook chrome | `feat/wp14-statusline` | spec written | docs/ui-layout.md waits for the owner's OK (4 questions); renderer not coded |

---

## 7. Kick-off prompt for Claude Code

Paste this into Claude Code from the pe-forge repo root (with this file saved as
`docs/PLAN_copperhead_features.md`):

```text
Read docs/PLAN_copperhead_features.md completely. Follow section 0 exactly.
Start Phase A with WP1. Before coding: read the pe-forge files under "Current state" for WP1,
clone copperhead read-only to /tmp/copperhead and read the files under "Reference", then give me
a short plan (files to add or change, tests to add) and wait for my OK.
After my OK: implement on branch feat/wp01-kicad-json, run ./tests/run.sh, check every acceptance
criterion, update the tracker in section 6, commit locally, and report. Do not push.
```

For later WPs, reuse the same prompt with the WP number changed.

To start with the status line instead (Phase A0):

```text
Read docs/PLAN_copperhead_features.md completely. Follow section 0 exactly.
Do WP14 only. First read the Claude Code status line and hooks docs linked in WP14 and tell me
anything in WP14 that the docs contradict. Then write docs/ui-layout.md (the row and segment
layout with colours) and wait for my OK. After my OK: implement on branch feat/wp14-statusline,
run ./tests/run.sh, check every acceptance criterion, update the tracker, commit locally, and
show me how to install it. Do not push.
```
