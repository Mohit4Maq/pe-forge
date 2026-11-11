"""pe-forge command line.

  forge new <dir> --name NAME [--answers intake.json]   scaffold a project (P0)
  forge status                                          phases, modules, next action
  forge gate check|pass|fail G<n> [--module M-xx] [--note TEXT]
  forge phase start P<n> [--module M-xx]
  forge module add NAME [--purpose TEXT] [--depends M-01,M-02] [--dir FOLDER] | list | start M-xx | done M-xx
  forge vmodel build|check|impact|diff|cr|sync ...
  forge link [--apply]              ID registry + wiki links
  forge consistency [--hook]        superseded-term check (Rule 5)
  forge watch --mark|--report|--show [--event E]   manual-edit watch (Rule 6)
  forge meter [--json] [--cache]    token budget GREEN / AMBER / RED (--cache: for the status line)
  forge schdraw check|apply|pins|new ...           agent schematic drawing
  forge bom check [file]            BoM completeness (alternates, price, stock)
  forge bom build [--boards N] [--spares N] [--offline] [--jlcpcb]   BoM from the schematic, priced and stock-checked
                                    (primary + alternate, Mouser / LCSC / DigiKey), order files, supply-chain risk
  forge bom risk [--fresh] [--offline] [--json]   the supply-chain risk assessment (SC-1..SC-9)
  forge check [--json] [--strict] [--fab] [--only a,b] [--module M-xx]
                                    one deterministic check: ERC, DRC, consistency, V-model, BoM, drift,
                                    library checks (no model, no network; CI and pre-commit safe)
  forge hooks install [--chain] | --uninstall   git pre-commit hook running forge check (fast subset)
  forge cr new|validate|approve|close|reject|list|status|guard ...   change requests after baseline (G6 / G7)
  forge ledger record|check [--hook] | show | clear --reason T   sync obligations (hooks; Stop blocks while open)
  forge evidence todo [--write] | sync   datasheet work list per board; copy datasheets into the project
  forge spice check [--module M-xx] [--strict] [--json]   ngspice assertions from the briefs' ## Simulation blocks
  forge vcycle [--json]            the V-cycle: every stage, where the project is, what needs attention
  forge results check | forge panel --json | forge report [--open]   simulation results as data; the side panel's
                                    feed; the offline HTML report
  forge statusline [install [--project|--user] [--uninstall]]   the pe-forge bar in Claude Code (docs/ui-layout.md)
  forge explain U1 | NET | U1.7 | U1.VCC | --pins MC_ID [--json] [--sheet PATH]   deterministic design lookup
  forge fab manifest [<kicad dir>]  release manifest (SHA-256 of sources and outputs); release.sh runs it
  forge fab rules | panel [--rows N --cols N] | testpoints   fab-house capability check, KiKit panel, test access
  forge drift [--json] [--module M-xx]  BoM / brief / booklet against the drawn schematic {doc, claim, actual}
  forge pcb sync [--prune] [--dry-run]   update the board from the schematic headless (KiCad's F8)
  forge protection [--json]             surge clamps vs the system and every protected pin's abs max
  forge stress [--json]                 every part's V / I / temperature rating vs the operating corners, derated
  forge calc buck|boost ... | design | check   closed-form calculations; recompute every result's closed form
  forge hp [--all|--json|rules]         High-Power PCB Rule Book: applicable rules, auto-checked or for review
  forge insulation [--rules] [--json]   insulation from the netlist (INS-1/2/3, NET-4); --rules proves the
                                    .kicad_dru enforces [[insulation.rule]] (CLS-2)
  forge txn begin "<what>" [--allow-dirty] | status | verify | commit [-m MSG] | close | rollback [--reason T]
                                    transactional design edits: verify before commit, lossless rollback
  forge learn [summary|show|off|on|clear]   what the tool learned here (local only; opt out any time)
  forge feedback [--note T]         redacted summary for the pe-forge team, shown first, never sent by itself
  forge issue [--last] [--kind bug|rule|check|idea] [--rule ID] [--dry-run] [--yes]
                                    one-step GitHub issue with the context filled in (gh, or the browser)
  forge quick [dir] [--vin V] [--json] [--out F]   pe-forge's checks on any KiCad folder, no project, nothing written
  forge adopt [dir] [--apply|--undo] [--name N]   bring a half-done project under pe-forge without changing a file
                                    (starts in light mode)
  forge decision new|list|answer|withdraw ...   questions only the owner answers; a gate waits for them
  forge enclosure req|fit|entries|build|check|release [--json]   the enclosure chain (docs/PLAN_enclosure.md)
  forge review record G<n> <file> [--module M-xx]   bind an independent review to the deliverables it reviewed
  forge level [light|standard|strict]   how firmly the process is enforced (lowering: the owner, at a terminal)
  forge req infer [--apply] [--json]    draft system requirements from the existing design, for the owner to confirm
  forge guard status | accept <path>   files pe-forge generated; proposed copies beside files it would not overwrite
  forge doctor                      check the toolchain
  forge update [--yes]              update every tool, pe-forge and its plugins (runs install.sh --update)
  forge vault init|open [dir]       the project folder as an Obsidian vault (settings, register, open)
  forge kicad-setup [--apply]       register the library, MC_LIB path and ODBC driver in KiCad 10

KiCad is found by forge/kicad.py ($KICAD_CLI, PATH, macOS app, Linux paths); ERC / DRC come from its JSON reports.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Windows: run in Python's UTF-8 mode so open() reads and writes UTF-8 like on macOS / Linux (the default there is
# cp1252, which cannot read the project's ± µ Ω files). Re-run this process once with -X utf8.
# The status line is exempt (it must answer in < 100 ms; a second interpreter costs that much): it gets the
# in-process UTF-8 default below instead.
if os.name == "nt" and not sys.flags.utf8_mode and __name__ == "__main__" and not os.environ.get("PE_FORGE_NO_UTF8") \
        and sys.argv[1:2] != ["statusline"]:
    import subprocess as _sp
    sys.exit(_sp.call([sys.executable, "-X", "utf8", os.path.abspath(__file__), *sys.argv[1:]]))

# Windows: text files are written with LF like on macOS / Linux (Python's default there is CRLF, which made
# transactions, settings files and git round-trips differ byte for byte). One switch for every open() /
# Path.write_text() in pe-forge; reading is unchanged (universal newlines).
if os.name == "nt":
    import builtins as _bi
    import io as _io
    _open = _io.open

    def _lf_open(file, mode="r", buffering=-1, encoding=None, errors=None, newline=None, *a, **k):
        if newline is None and "b" not in mode and any(m in mode for m in "wax+"):
            newline = "\n"
        if encoding is None and "b" not in mode and not sys.flags.utf8_mode:
            encoding = "utf-8"                   # the status line runs without UTF-8 mode: read and write UTF-8
        return _open(file, mode, buffering, encoding, errors, newline, *a, **k)
    _bi.open = _io.open = _lf_open

# UTF-8 out, whatever the console or pipe: Windows defaults to cp1252 when redirected, which cannot encode
# pe-forge's ▸ ✓ ≥ → or the block logo (UnicodeEncodeError). errors="replace" so output can never crash a command.
for _s in (sys.stdout, sys.stderr):
    try:
        if _s and (getattr(_s, "encoding", "") or "").lower().replace("-", "") != "utf8":
            _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from forge import config  # noqa: E402


def need():
    return config.Project()


def main(argv):
    """Run one command; record it for `forge learn`; an unexpected error is saved for `forge issue --last`."""
    import time
    from forge import learn
    cmd = argv[0] if argv else "help"
    t0 = time.time()
    try:
        if os.environ.get("PE_FORGE_TEST_CRASH") == cmd:        # test hook: an unexpected error in this command
            raise RuntimeError("test crash")
        rc = _dispatch(argv)
    except (SystemExit, KeyboardInterrupt):
        raise
    except Exception as e:
        learn.record_error(cmd, e)
        import traceback
        traceback.print_exc()
        print(f"\npe-forge hit an internal error in `forge {cmd}`. Report it in one step: forge issue --last",
              file=sys.stderr)
        return 2
    if cmd not in ("learn", "feedback", "issue", "statusline", "meter", "panel", "help", "-h", "--help"):
        learn.record("command", command=cmd, rc=rc or 0, s=round(time.time() - t0, 1))
    return rc


def _dispatch(argv):
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd == "new":
        from forge import scaffold
        return scaffold.main(rest)
    if cmd == "vault":
        from forge import vault
        return vault.main(rest)
    if cmd == "update":
        os.execvp("bash", ["bash", os.path.join(config.FORGE_ROOT, "install.sh"), "--update", *rest])
    if cmd == "doctor":
        from forge import doctor
        return doctor.main(rest)
    if cmd == "kicad-setup":
        from forge import kicad_setup
        return kicad_setup.main(rest)
    if cmd == "schdraw":
        from forge import schdraw
        return schdraw.main(rest)
    if cmd == "evidence":
        from forge import evidence
        return evidence.main(need(), rest)
    if cmd == "spice":
        from forge import spice
        return spice.main(need(), rest)
    if cmd == "quick":                                          # any KiCad folder, no project needed
        from forge import quick
        return quick.main(rest)
    if cmd == "watch" and "--event" in rest and rest[rest.index("--event") + 1:][:1] == ["SessionStart"] \
            and config.find_root() is None:                     # a KiCad folder that is not a project yet
        from forge import quick
        return quick.main(["--hint"])
    if cmd == "adopt":                                          # before a project exists
        from forge import adopt
        return adopt.main(rest)
    if cmd == "guard":
        from forge import guard
        return guard.main(need(), rest)
    if cmd in ("learn", "feedback", "issue"):                   # work inside or outside a project
        from forge import learn
        if cmd == "learn":
            return learn.main_learn(rest)
        if cmd == "feedback":
            return learn.main_feedback(rest)
        root = config.find_root()
        return learn.main_issue(rest, config.Project().cfg if root else None)
    if cmd == "calc":                                   # closed forms need no project; calc check does
        from forge import calc
        return calc.main(config.project_or_none(), rest)
    if cmd == "hp" and rest[:1] == ["rules"]:           # the rule book itself needs no project
        from forge import hp
        return hp.main(None, rest)
    if cmd == "panel" and config.find_root() is None:
        print('{"ok": false, "error": "not inside a pe-forge project"}')
        return 0
    if cmd == "vcycle":
        from forge import vcycle
        return vcycle.main(need(), rest)
    if cmd in ("results", "panel", "report"):
        from forge import results
        return {"results": results.main_results, "panel": results.main_panel, "report": results.main_report}[cmd](need(), rest)
    if cmd == "statusline":
        from forge import statusline
        return statusline.main(rest)
    if cmd == "explain":
        from forge import explain
        return explain.main(config.project_or_none(), rest)
    if cmd == "fab" and rest[:1] in (["rules"], ["panel"], ["testpoints"]):
        from forge import fabhouse
        return fabhouse.main(need(), rest)
    if cmd == "fab":
        from forge import fab
        return fab.main(config.project_or_none(), rest)
    if cmd in ("watch", "consistency", "ledger") and config.find_root() is None or \
            (cmd == "meter" and "--cache" in rest and config.find_root() is None):
        return 0   # hooks outside a forge project: silent no-op
    if cmd == "cr" and rest[:1] == ["guard"] and config.find_root() is None:
        return 0   # hooks outside a forge project: silent no-op
    prj = need()
    if cmd == "decision":
        from forge import decisions
        return decisions.main(prj, rest)
    if cmd == "enclosure":
        from forge import enclosure
        return enclosure.main(prj, rest)
    if cmd == "review":
        from forge import gates
        return gates.main_review(prj, rest)
    if cmd == "level":
        from forge import level
        return level.main(prj, rest)
    if cmd == "req":
        from forge import reqinfer
        return reqinfer.main(prj, rest)
    if cmd == "status":
        from forge import cli_state
        return cli_state.status(prj, rest)
    if cmd in ("gate", "phase", "module"):
        from forge import cli_state
        return getattr(cli_state, cmd)(prj, rest)
    if cmd == "vmodel":
        from forge import vmodel
        return vmodel.main(prj, rest)
    if cmd == "link":
        from forge import linkids
        return linkids.main(prj, rest)
    if cmd == "consistency":
        from forge import consistency
        return consistency.main(prj, rest)
    if cmd == "watch":
        from forge import watch
        return watch.main(prj, rest)
    if cmd == "meter":
        from forge import meter
        return meter.main(prj, rest)
    if cmd == "check":
        from forge import check
        return check.main(prj, rest)
    if cmd == "cr":
        from forge import changes
        return changes.main(prj, rest)
    if cmd == "ledger":
        from forge import ledger
        return ledger.main(prj, rest)
    if cmd == "drift":
        from forge import drift
        return drift.main(prj, rest)
    if cmd == "pcb":
        from forge import pcb
        return pcb.main(prj, rest)
    if cmd == "hp":
        from forge import hp
        return hp.main(prj, rest)
    if cmd == "protection":
        from forge import protection
        return protection.main(prj, rest)
    if cmd == "stress":
        from forge import stress
        return stress.main(prj, rest)
    if cmd == "insulation":
        from forge import insulation
        return insulation.main(prj, rest)
    if cmd == "txn":
        from forge import txn
        return txn.main(prj, rest)
    if cmd == "hooks":
        from forge import check
        return check.hooks(prj, rest)
    if cmd == "bom" and rest[:1] in (["build"], ["risk"]):
        from forge import supply
        return supply.main(prj, rest)
    if cmd == "bom":
        from forge import gates
        f = rest[1] if len(rest) > 1 else prj.p("bom_csv")
        res = gates.bom_ok(f)
        for ok, msg in res:
            print(("PASS " if ok else "FAIL ") + msg)
        return 0 if all(ok for ok, _ in res) else 1
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]) or 0)
