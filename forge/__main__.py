"""pe-forge command line.

  forge new <dir> --name NAME [--answers intake.json]   scaffold a project (P0)
  forge status                                          phases, modules, next action
  forge gate check|pass|fail G<n> [--module M-xx] [--note TEXT]
  forge phase start P<n> [--module M-xx]
  forge module add NAME [--purpose TEXT] [--depends M-01,M-02] | list | start M-xx | done M-xx
  forge vmodel build|check|impact|diff|cr|sync ...
  forge link [--apply]              ID registry + wiki links
  forge consistency [--hook]        superseded-term check (Rule 5)
  forge watch --mark|--report|--show [--event E]   manual-edit watch (Rule 6)
  forge meter [--json] [--cache]    token budget GREEN / AMBER / RED (--cache: for the status line)
  forge schdraw check|apply|pins|new ...           agent schematic drawing
  forge bom check [file]            BoM completeness (alternates, price, stock)
  forge check [--json] [--strict] [--fab] [--only a,b] [--module M-xx]
                                    one deterministic check: ERC, DRC, consistency, V-model, BoM, drift,
                                    library checks (no model, no network; CI and pre-commit safe)
  forge hooks install [--chain] | --uninstall   git pre-commit hook running forge check (fast subset)
  forge cr new|validate|approve|close|reject|list|status|guard ...   change requests after baseline (G6 / G7)
  forge ledger record|check [--hook] | show | clear --reason T   sync obligations (hooks; Stop blocks while open)
  forge evidence todo [--write] | sync   datasheet work list per board; copy datasheets into the project
  forge spice check [--module M-xx] [--strict] [--json]   ngspice assertions from the briefs' ## Simulation blocks
  forge statusline [install [--project|--user] [--uninstall]]   the pe-forge bar in Claude Code (docs/ui-layout.md)
  forge explain U1 | NET | U1.7 | U1.VCC | --pins MC_ID [--json] [--sheet PATH]   deterministic design lookup
  forge fab manifest [<kicad dir>]  release manifest (SHA-256 of sources and outputs); release.sh runs it
  forge drift [--json] [--module M-xx]  BoM / brief / booklet against the drawn schematic {doc, claim, actual}
  forge txn begin "<what>" [--allow-dirty] | status | verify | commit [-m MSG] | close | rollback [--reason T]
                                    transactional design edits: verify before commit, lossless rollback
  forge doctor                      check the toolchain
  forge kicad-setup [--apply]       register the library, MC_LIB path and ODBC driver in KiCad 10

KiCad is found by forge/kicad.py ($KICAD_CLI, PATH, macOS app, Linux paths); ERC / DRC come from its JSON reports.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from forge import config  # noqa: E402


def need():
    return config.Project()


def main(argv):
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd == "new":
        from forge import scaffold
        return scaffold.main(rest)
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
    if cmd == "statusline":
        from forge import statusline
        return statusline.main(rest)
    if cmd == "explain":
        from forge import explain
        return explain.main(config.project_or_none(), rest)
    if cmd == "fab":
        from forge import fab
        return fab.main(config.project_or_none(), rest)
    if cmd in ("watch", "consistency", "ledger") and config.find_root() is None or \
            (cmd == "meter" and "--cache" in rest and config.find_root() is None):
        return 0   # hooks outside a forge project: silent no-op
    if cmd == "cr" and rest[:1] == ["guard"] and config.find_root() is None:
        return 0   # hooks outside a forge project: silent no-op
    prj = need()
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
    if cmd == "txn":
        from forge import txn
        return txn.main(prj, rest)
    if cmd == "hooks":
        from forge import check
        return check.hooks(prj, rest)
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
