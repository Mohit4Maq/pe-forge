#!/usr/bin/env python3
"""pe-forge self-test (stdlib unittest). Run:  python3 tests/test_forge.py   (or ./tests/run.sh)

Covers: scaffold, status / modules / gates, V-model build + check, consistency, ID linking, edit watch, BoM
check, and the schematic engine (a clean sheet must pass; a sheet with a short must be caught by both the
geometry check and KiCad's own netlist). Schematic tests are skipped when kicad-cli is not installed.
"""
import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FORGE = [sys.executable, os.path.join(REPO, "forge", "__main__.py")]
FIX = os.path.join(REPO, "tests", "fixtures")
ENV = dict(os.environ, MC_LIB=os.path.join(REPO, "library"))   # always test against the bundled library
ENV.pop("CLAUDE_PROJECT_DIR", None)


def forge(*args, cwd=None, check=True, stdin=None):
    r = subprocess.run(FORGE + list(args), cwd=cwd, capture_output=True, text=True, env=ENV, input=stdin)
    if check and r.returncode != 0:
        raise AssertionError(f"forge {' '.join(args)} failed ({r.returncode}):\n{r.stdout}\n{r.stderr}")
    return r


def kicad_cli():
    sys.path.insert(0, os.path.join(REPO, "skills", "kicad-place", "scripts"))
    os.environ["MC_LIB"] = ENV["MC_LIB"]
    import sch_place
    return os.path.exists(sch_place.KCLI)


class Project(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="pe-forge-test-")
        cls.dir = os.path.join(cls.tmp, "demo")
        ans = {"name": "Demo48", "summary": "48 V to 12 V buck, self-test", "owner": "Tester", "type": "dc-dc",
               "corners": {"vin_V": [36, 60], "f_sw_kHz": [100, 250]}, "profile": "full", "schematic_mode": "agent"}
        p = os.path.join(cls.tmp, "intake.json")
        json.dump(ans, open(p, "w"))
        forge("new", cls.dir, "--answers", p)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, True)

    def test_01_scaffold(self):
        for f in ("project.toml", "CLAUDE.md", "01_Program/Master_Plan.md", "02_Requirements/V_Model/trace.csv",
                  "08_PCB/demo48/demo48.kicad_pro", ".forge/state.json", "03_Foundation/Intake_Answers.md"):
            self.assertTrue(os.path.exists(os.path.join(self.dir, f)), f)
        t = open(os.path.join(self.dir, "CLAUDE.md")).read()
        self.assertIn("Demo48", t)
        self.assertNotIn("{{", t)
        self.assertIn("36, 60", open(os.path.join(self.dir, "project.toml")).read())

    def test_02_gates_and_modules(self):
        out = forge("status", cwd=self.dir).stdout
        self.assertIn("Next: work P0", out)
        forge("gate", "pass", "G0", cwd=self.dir)
        r = forge("gate", "check", "G1", cwd=self.dir, check=False)
        self.assertEqual(r.returncode, 1)
        self.assertIn("missing: 03_Foundation/Market_Survey/Market_Survey.md", r.stdout)
        self.assertEqual(forge("gate", "pass", "G1", cwd=self.dir, check=False).returncode, 1)   # refuses
        forge("module", "add", "Aux supply", cwd=self.dir)
        forge("module", "add", "Power stage", "--depends", "M-01", cwd=self.dir)
        st = json.load(open(os.path.join(self.dir, ".forge/state.json")))
        self.assertEqual([m["id"] for m in st["modules"]], ["M-01", "M-02"])
        self.assertTrue(os.path.exists(os.path.join(self.dir, "modules/M-01_Aux_supply/Module_Brief.md")))

    def test_03_review_row_unlocks_gate(self):
        qc = os.path.join(self.dir, "01_Program/QC/QC_Register.md")
        with open(qc, "a") as fh:
            fh.write("| 2026-11-04 | G3 | system | Topology_Trade 0.1 | T3 | NOT READY | 1/0/0 | 0/0/0 | r1.md | OPEN |\n")
        r = forge("gate", "check", "G3", cwd=self.dir, check=False)
        self.assertIn("verdict NOT READY", r.stdout)
        with open(qc, "a") as fh:
            fh.write("| 2026-11-05 | G3 | system | Topology_Trade 0.2 | T3 | READY | 0/0/1 | 0/0/0 | r2.md | CLOSED |\n")
        r = forge("gate", "check", "G3", cwd=self.dir, check=False)
        self.assertIn("PASS latest G3 review (2026-11-05): verdict READY, status CLOSED", r.stdout)

    def test_04_vmodel(self):
        sr = os.path.join(self.dir, "02_Requirements/System_Requirements.md")
        t = open(sr).read().replace("| SYS-001 | _(fill in)_ | _(fill in)_ | _(fill in)_ | T |",
                                    "| SYS-001 | Output | 12 V ± 2 % at 0–42 A | intake | T |")
        open(sr, "w").write(t)
        vm = os.path.join(self.dir, "02_Requirements/V_Model")
        with open(os.path.join(vm, "trace.csv"), "a", newline="") as fh:
            csv.writer(fh).writerow(["HWR-PWR-01", "Output regulation", "Vout 12 V ± 2 %", "SYS-001", "M-02",
                                     "pe-hw", "", "", "TP-001", "T", "B", "all", "", ""])
        with open(os.path.join(vm, "verification.csv"), "a", newline="") as fh:
            csv.writer(fh).writerow(["TP-001", "Load regulation test", "L3", "Master_Plan", "pe-validation", "planned"])
        forge("vmodel", "build", cwd=self.dir)
        self.assertIn("0 error(s)", forge("vmodel", "check", cwd=self.dir).stdout)
        self.assertTrue(os.path.exists(os.path.join(vm, "items/SYS/SYS-001.md")))
        with open(os.path.join(vm, "trace.csv"), "a", newline="") as fh:
            csv.writer(fh).writerow(["HWR-PWR-02", "Bad", "x", "SYS-999", "M-02", "pe-hw", "", "", "", "T", "O", "all", "", ""])
        r = forge("vmodel", "check", cwd=self.dir, check=False)
        self.assertEqual(r.returncode, 1)
        self.assertIn("parent SYS-999 not in System_Requirements", r.stdout)

    def test_05_consistency_and_hook(self):
        with open(os.path.join(self.dir, ".forge/superseded_terms.csv"), "a") as fh:
            fh.write("LM5146,LM5148,2026-11-04,test\n")
        note = os.path.join(self.dir, "04_Hardware/note.md")
        open(note, "w").write("# Buck\nController is LM5146.\n")
        self.assertEqual(forge("consistency", cwd=self.dir, check=False).returncode, 1)
        r = forge("consistency", "--hook", cwd=self.dir, check=False, stdin="{}")
        self.assertEqual(r.returncode, 2)
        open(note, "w").write("# Buck\nController is LM5148 (replaces LM5146).\n")
        self.assertEqual(forge("consistency", cwd=self.dir, check=False).returncode, 0)

    def test_06_watch(self):
        forge("watch", "--mark", cwd=self.dir)
        open(os.path.join(self.dir, "00_Inbox/idea.md"), "w").write("owner edit\n")
        r = forge("watch", "--report", "--event", "UserPromptSubmit", cwd=self.dir)
        ctx = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("00_Inbox/idea.md", ctx)
        self.assertEqual(forge("watch", "--report", cwd=self.dir).stdout.strip(), "")   # nothing new

    def test_07_bom_check(self):
        bom = os.path.join(self.dir, "04_Hardware/BOM/WIP_BOM.csv")
        hdr = open(bom).readline().strip().split(",")
        row = dict.fromkeys(hdr, "x")
        row["Alt MPN"] = ""
        with open(bom, "a", newline="") as fh:
            csv.DictWriter(fh, hdr).writerow(row)
        r = forge("bom", "check", cwd=self.dir, check=False)
        self.assertEqual(r.returncode, 1)
        self.assertIn("1 incomplete", r.stdout)

    def test_08_hooks_silent_outside_project(self):
        empty = tempfile.mkdtemp()
        self.assertEqual(forge("watch", "--report", cwd=empty).stdout, "")
        self.assertEqual(forge("consistency", "--hook", cwd=empty, stdin="{}").returncode, 0)
        shutil.rmtree(empty)


@unittest.skipUnless(kicad_cli(), "kicad-cli not installed")
class SchematicEngine(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pe-forge-sch-")
        forge("schdraw", "new", os.path.join(self.tmp, "demo.kicad_sch"), "--title", "self-test")

    def tearDown(self):
        shutil.rmtree(self.tmp, True)

    def run_spec(self, name, act="check"):
        spec = os.path.join(self.tmp, name)
        shutil.copy(os.path.join(FIX, name), spec)
        return forge("schdraw", act, spec, cwd=self.tmp, check=False)

    def test_clean_sheet_passes_and_applies(self):
        r = self.run_spec("input_protection.json")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("errors 0 (ERC 0)", r.stdout)
        r = self.run_spec("input_protection.json", "apply")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        sch = open(os.path.join(self.tmp, "demo.kicad_sch")).read()
        self.assertGreaterEqual(sch.count("(wire"), 10)
        self.assertIn('(label "VSENSE"', sch)

    def test_short_is_caught_twice(self):
        r = self.run_spec("short_circuit.json")
        self.assertEqual(r.returncode, 1)
        self.assertIn("C3 VIN: drawn net also touches", r.stdout)     # own geometry
        self.assertIn("K1 KiCad netlist disagrees on VIN", r.stdout)  # KiCad's netlist
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "demo.kicad_sch.bak")))


KFIX = os.path.join(FIX, "kicad")
sys.path.insert(0, REPO)
from forge import kicad as fk  # noqa: E402


class KicadReports(unittest.TestCase):
    """WP1: parser tests on JSON captured from KiCad 10.0.6 (no KiCad needed)."""

    def load(self, name):
        return json.load(open(os.path.join(KFIX, name)))

    def test_erc_short_parsed_with_sheet_and_mm_position(self):
        r = fk.parse(self.load("erc_short.json"), "erc", "short.kicad_sch", has_project=False)
        self.assertTrue(r.tool_ok)
        self.assertFalse(r.ok)
        v = next(v for v in r.violations if v["type"] == "pin_to_pin")
        self.assertEqual(v["sheet"], "/")
        self.assertEqual(v["items"][0]["pos"], (55.88, 73.66))       # KiCad writes 0.5588: 100 mm units
        self.assertEqual(v["items"][0]["ref"], "#FLG005")
        self.assertFalse(any(v["type"] == "unresolved_variable" for v in r.violations))   # bare sheet

    def test_erc_unresolved_variables_count_inside_a_project(self):
        r = fk.parse(self.load("erc_clean.json"), "erc", "x.kicad_sch", has_project=False)
        self.assertTrue(r.ok)
        r = fk.parse(self.load("erc_clean.json"), "erc", "x.kicad_sch", has_project=True)
        self.assertFalse(r.ok)

    def test_drc_shapes(self):
        clean = fk.parse(self.load("drc_clean.json"), "drc", "c.kicad_pcb")
        self.assertTrue(clean.ok)
        broken = fk.parse(self.load("drc_broken.json"), "drc", "b.kicad_pcb")
        self.assertTrue(broken.ok)                      # only warnings at the default (error) level
        self.assertEqual(len(broken.warnings), 2)
        strict = fk.parse(self.load("drc_broken.json"), "drc", "b.kicad_pcb", severity=("error", "warning"))
        self.assertFalse(strict.ok)
        self.assertEqual(strict.violations[0]["items"][0]["pos"], (110.0, 90.0))
        par = fk.parse(self.load("drc_parity.json"), "drc", "p.kicad_pcb")
        self.assertEqual(len(par.parity), 6)
        self.assertIn("Missing footprint C1", par.parity[0]["description"])

    def test_wrong_report_shape_is_not_a_pass(self):
        r = fk.parse({"foo": 1}, "erc", "x.kicad_sch")
        self.assertFalse(r.tool_ok)
        self.assertFalse(r.ok)

    def test_stub_cli_without_report_fails_gate(self):
        tmp = tempfile.mkdtemp(prefix="pe-forge-stub-")
        try:
            stub = os.path.join(tmp, "kicad-cli")
            open(stub, "w").write("#!/bin/sh\nexit 0\n")
            os.chmod(stub, 0o755)
            ans = os.path.join(tmp, "a.json")
            json.dump({"name": "Stub", "summary": "s", "owner": "T"}, open(ans, "w"))
            prj = os.path.join(tmp, "p")
            forge("new", prj, "--answers", ans)
            shutil.copy(os.path.join(KFIX, "short_circuit.kicad_sch"), os.path.join(prj, "08_PCB/stub/stub.kicad_sch"))
            env = dict(ENV, KICAD_CLI=stub)
            r = subprocess.run(FORGE + ["gate", "check", "G6"], cwd=prj, capture_output=True, text=True, env=env)
            self.assertEqual(r.returncode, 1)
            self.assertRegex(r.stdout, r"FAIL ERC .*08_PCB/stub/stub\.kicad_sch: kicad-cli produced no report")
            old = os.environ.get("KICAD_CLI")
            os.environ["KICAD_CLI"] = stub
            try:
                rep = fk.erc(os.path.join(KFIX, "short_circuit.kicad_sch"))
            finally:
                os.environ.pop("KICAD_CLI") if old is None else os.environ.__setitem__("KICAD_CLI", old)
            self.assertFalse(rep.tool_ok)
            self.assertFalse(rep.ok)
        finally:
            shutil.rmtree(tmp, True)

    def test_one_kicad_search_order(self):
        hits = subprocess.run(["grep", "-rln", "Applications/KiCad", "forge", "library/tools", "skills",
                               "--include=*.py"], cwd=REPO, capture_output=True, text=True).stdout.split()
        self.assertEqual(sorted(hits), ["forge/kicad.py", "library/tools/kicad_paths.py"])


@unittest.skipUnless(kicad_cli(), "kicad-cli not installed")
class KicadLive(unittest.TestCase):
    def test_erc_on_short_sheet(self):
        r = fk.erc(os.path.join(KFIX, "short_circuit.kicad_sch"))
        self.assertTrue(r.tool_ok, r.error)
        self.assertFalse(r.ok)
        it = r.violations[0]["items"][0]
        self.assertEqual(r.violations[0]["sheet"], "/")
        self.assertIsNotNone(it["pos"])

    def test_netlist_and_clean_sheet(self):
        r = fk.erc(os.path.join(KFIX, "input_protection.kicad_sch"))
        self.assertTrue(r.ok, r.lines())
        nets = fk.netlist(os.path.join(KFIX, "input_protection.kicad_sch"))
        self.assertTrue(nets and any("J1.1" in p for p in nets.values()))


def new_project(name, tmp, **extra):
    ans = os.path.join(tmp, name + ".json")
    json.dump(dict({"name": name, "summary": "s", "owner": "T"}, **extra), open(ans, "w"))
    d = os.path.join(tmp, name)
    forge("new", d, "--answers", ans)
    return d


def git(d, *a):
    return subprocess.run(["git", "-C", d, "-c", "user.name=T", "-c", "user.email=t@example.invalid", *a],
                          capture_output=True, text=True, env=ENV)


class ForgeCheck(unittest.TestCase):
    """WP2: forge check, the pre-commit hook and the no-network contract."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pe-forge-check-")
        self.dir = new_project("Chk", self.tmp)
        self.sheet = os.path.join(self.dir, "08_PCB/chk/chk.kicad_sch")

    def tearDown(self):
        shutil.rmtree(self.tmp, True)

    def check(self, *a):
        return forge("check", *a, cwd=self.dir, check=False)

    def test_fresh_project_passes_with_honest_skips(self):
        r = self.check("--json")
        self.assertEqual(r.returncode, 0, r.stdout)
        d = json.loads(r.stdout)
        self.assertEqual(set(d), {"ok", "checks", "duration_s"})
        self.assertEqual(d["checks"]["erc"]["status"], "skip")
        self.assertIn("no schematic yet", d["checks"]["erc"]["reason"])
        self.assertEqual(d["checks"]["drc"]["status"], "skip")
        self.assertIn("no board yet", d["checks"]["drc"]["reason"])
        for name, c in d["checks"].items():
            self.assertIn(c["status"], ("pass", "warn", "fail", "skip"))
            self.assertIsInstance(c["findings"], list)
        txt = self.check().stdout
        self.assertIn("SKIP erc: skipped (no schematic yet", txt)
        self.assertNotIn("PASS erc", txt)
        self.assertTrue(os.path.exists(os.path.join(self.dir, ".forge/cache/check.json")))

    def test_superseded_term_fails_with_file_and_line(self):
        with open(os.path.join(self.dir, ".forge/superseded_terms.csv"), "a") as fh:
            fh.write("LM5146,LM5148,2026-11-04,test\n")
        open(os.path.join(self.dir, "04_Hardware/n.md"), "w").write("# n\nuse LM5146 here\n")
        r = self.check("--only", "consistency")
        self.assertEqual(r.returncode, 1)
        self.assertIn("04_Hardware/n.md:2: 'LM5146' is superseded by 'LM5148'", r.stdout)

    def test_no_network_or_model_imports(self):
        code = ("import sys; sys.path.insert(0, %r)\n"
                "import forge.check, forge.gates, forge.kicad, forge.vmodel, forge.consistency\n"
                "for m in ('forge.drift', 'forge.fab'):\n"
                "    try: __import__(m)\n"
                "    except ImportError: pass\n"
                "net = ('socket', 'ssl', 'http', 'urllib.request', 'requests', 'httpx', 'anthropic', 'openai', 'mcp')\n"
                "bad = sorted(m for m in sys.modules if any(m == n or m.startswith(n + '.') for n in net))\n"
                "print(','.join(bad))" % REPO)
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "")

    def test_foreign_hook_not_overwritten(self):
        hook = os.path.join(self.dir, ".git/hooks/pre-commit")
        forge("hooks", "--uninstall", cwd=self.dir)
        open(hook, "w").write("#!/bin/sh\necho mine\n")
        self.assertEqual(forge("hooks", "install", cwd=self.dir, check=False).returncode, 1)
        self.assertEqual(open(hook).read(), "#!/bin/sh\necho mine\n")
        forge("hooks", "install", "--chain", cwd=self.dir)
        self.assertIn("pe-forge-prev", open(hook).read())
        forge("hooks", "--uninstall", cwd=self.dir)
        self.assertEqual(open(hook).read(), "#!/bin/sh\necho mine\n")

    @unittest.skipUnless(kicad_cli(), "kicad-cli not installed")
    def test_short_fails_with_sheet_and_position(self):
        shutil.copy(os.path.join(KFIX, "short_circuit.kicad_sch"), self.sheet)
        r = self.check("--json")
        self.assertEqual(r.returncode, 1)
        d = json.loads(r.stdout)
        self.assertLess(d["duration_s"], 60)
        erc = d["checks"]["erc"]
        self.assertEqual(erc["status"], "fail")
        loc = erc["findings"][0]["location"]
        self.assertRegex(loc, r"08_PCB/chk/chk\.kicad_sch sheet / @ \([\d.]+, [\d.]+\) mm")

    @unittest.skipUnless(kicad_cli(), "kicad-cli not installed")
    def test_precommit_hook_blocks_erc_error(self):
        self.assertTrue(os.path.exists(os.path.join(self.dir, ".git/hooks/pre-commit")))   # installed by forge new
        git(self.dir, "add", "-A")
        r = git(self.dir, "commit", "-qm", "clean project")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        shutil.copy(os.path.join(KFIX, "short_circuit.kicad_sch"), self.sheet)
        git(self.dir, "add", "-A")
        r = git(self.dir, "commit", "-qm", "adds a short")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("FAIL erc", r.stdout + r.stderr)


class Transactions(unittest.TestCase):
    """WP3: forge txn begin / verify / commit / rollback."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pe-forge-txn-")
        self.dir = new_project("Txn", self.tmp)
        git(self.dir, "add", "-A")
        r = git(self.dir, "commit", "-qm", "base")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def tearDown(self):
        shutil.rmtree(self.tmp, True)

    def p(self, rel):
        return os.path.join(self.dir, rel)

    def read(self, rel):
        return open(self.p(rel), "rb").read()

    def txn(self, *a):
        return forge("txn", *a, cwd=self.dir, check=False)

    def test_clean_rollback_is_lossless_and_keeps_failed_work(self):
        a, b = "CLAUDE.md", "01_Program/Master_Plan.md"
        a0, b0 = self.read(a), self.read(b)
        self.assertEqual(self.txn("begin", "swap U1").returncode, 0)
        open(self.p(a), "ab").write(b"\nedit a\n")
        open(self.p(b), "ab").write(b"\nedit b\n")
        open(self.p("04_Hardware/new.md"), "w").write("new\n")
        r = self.txn("rollback", "--reason", "test")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(git(self.dir, "status", "--porcelain").stdout.strip(), "")
        self.assertEqual(self.read(a), a0)
        self.assertEqual(self.read(b), b0)
        self.assertFalse(os.path.exists(self.p("04_Hardware/new.md")))
        stash = git(self.dir, "stash", "list").stdout
        self.assertIn("forge failed txn", stash)
        self.assertIn("swap U1", stash)
        shown = git(self.dir, "stash", "show", "--name-only", "stash@{0}").stdout
        self.assertIn("04_Hardware/new.md", shown)
        self.assertIn("CLAUDE.md", shown)

    def test_dirty_tree_needs_allow_dirty_and_is_restored(self):
        a, b, u = "CLAUDE.md", "01_Program/Master_Plan.md", "00_Inbox/mine.md"
        open(self.p(a), "ab").write(b"\nmy uncommitted edit\n")
        open(self.p(u), "w").write("my untracked note\n")
        a1, b0, u0 = self.read(a), self.read(b), self.read(u)
        r = self.txn("begin", "x")
        self.assertEqual(r.returncode, 1)
        self.assertIn("uncommitted changes", r.stdout)
        self.assertEqual(self.txn("begin", "x", "--allow-dirty").returncode, 0)
        open(self.p(a), "ab").write(b"agent edit\n")
        open(self.p(b), "ab").write(b"agent edit\n")
        open(self.p(u), "w").write("agent overwrote it\n")
        open(self.p("04_Hardware/n.md"), "w").write("n\n")
        self.assertEqual(self.txn("rollback").returncode, 0)
        self.assertEqual(self.read(a), a1)
        self.assertEqual(self.read(b), b0)
        self.assertEqual(self.read(u), u0)
        self.assertFalse(os.path.exists(self.p("04_Hardware/n.md")))

    def test_run_record_survives_rollback_even_when_staged(self):
        self.txn("begin", "audit")
        tid = json.load(open(self.p(".forge/txn.json")))["id"]
        open(self.p("CLAUDE.md"), "ab").write(b"x\n")
        git(self.dir, "add", "-f", ".forge/runs")
        self.txn("rollback", "--reason", "staged audit")
        s = open(self.p(f".forge/runs/{tid}/summary.md")).read()
        self.assertIn("rolled back: staged audit", s)
        self.assertFalse(os.path.exists(self.p(".forge/txn.json")))

    def test_commit_needs_verify_of_current_tree(self):
        self.txn("begin", "value change")
        open(self.p("04_Hardware/calc.md"), "w").write("R5 = 10k\n")
        r = self.txn("commit")
        self.assertEqual(r.returncode, 1)
        self.assertIn("no passing", r.stdout)
        self.assertEqual(self.txn("verify").returncode, 0)
        open(self.p("04_Hardware/calc.md"), "a").write("R6 = 1k\n")
        r = self.txn("commit")
        self.assertEqual(r.returncode, 1)
        self.assertIn("changed after the last passing verify", r.stdout)
        self.assertEqual(self.txn("verify").returncode, 0)
        r = self.txn("commit", "-m", "calc: R5 and R6")
        self.assertEqual(r.returncode, 0, r.stdout)
        log = git(self.dir, "log", "-1", "--format=%B").stdout
        self.assertIn("calc: R5 and R6", log)
        self.assertIn("Forge-Txn: T", log)
        self.assertEqual(git(self.dir, "status", "--porcelain").stdout.strip(), "")

    def test_second_begin_refused(self):
        self.txn("begin", "one")
        r = self.txn("begin", "two")
        self.assertEqual(r.returncode, 1)
        self.assertIn("already open", r.stdout)

    @unittest.skipUnless(kicad_cli(), "kicad-cli not installed")
    def test_schdraw_apply_rolls_back_on_real_project_failure(self):
        from forge import schdraw as sd
        sheet = self.p("08_PCB/txn/demo.kicad_sch")
        forge("schdraw", "new", sheet, "--title", "t")
        git(self.dir, "add", "-A")
        git(self.dir, "commit", "-qm", "empty sheet")
        before = self.read("08_PCB/txn/demo.kicad_sch")
        spec = json.load(open(os.path.join(FIX, "input_protection.json")))
        spec["sheet"] = sheet
        sp_path = self.p("00_Inbox/spec.json")
        json.dump(spec, open(sp_path, "w"))
        git(self.dir, "add", "-A")
        git(self.dir, "commit", "-qm", "spec")
        self.assertEqual(forge("schdraw", "check", sp_path, cwd=self.dir, check=False).returncode, 0)
        real = sd.post_apply_check
        sd.post_apply_check = lambda *a: ["forced: real-project ERC found a new error"]
        cwd = os.getcwd()
        os.chdir(self.dir)
        try:
            with self.assertRaises(SystemExit):
                sd.main(["apply", sp_path])
        finally:
            sd.post_apply_check = real
            os.chdir(cwd)
        self.assertEqual(self.read("08_PCB/txn/demo.kicad_sch"), before)
        self.assertFalse(os.path.exists(self.p(".forge/txn.json")))
        self.assertIn("forge failed txn", git(self.dir, "stash", "list").stdout)
        # the real post-apply check passes for this clean spec: applied, transaction closed
        self.assertEqual(forge("schdraw", "check", sp_path, cwd=self.dir, check=False).returncode, 0)
        r = forge("schdraw", "apply", sp_path, cwd=self.dir, check=False)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("closed (verified)", r.stdout)
        self.assertNotEqual(self.read("08_PCB/txn/demo.kicad_sch"), before)


class ValueNormaliser(unittest.TestCase):
    """WP4 AC4.3: equivalent spellings never drift; different values always do."""

    def test_table(self):
        from forge.tables import same_value
        same = [("4u7", "4.7u"), ("4u7", "4.7 uF"), ("4.7µF", "4u7"), ("10k", "10 kΩ"), ("10K", "10000"),
                ("10k", "10 k"), ("2R2", "2.2 Ω"), ("100n", "0.1u"), ("100nF 50V", "100n 50 V"), ("1", "1Ω"),
                ("B360B", "b360b"), ("Ihold≥3A", "Ihold>=3A"), ("0R", "0"), ("1M", "1 MΩ"), ("22p", "22 pF")]
        diff = [("10k", "4.7k"), ("10u", "10n"), ("1M", "1m"), ("4.7uF", "4.7uH"), ("100n 50V", "100n 25V")]
        for a, b in same:
            self.assertTrue(same_value(a, b), (a, b))
        for a, b in diff:
            self.assertFalse(same_value(a, b), (a, b))

    def test_refs_and_aliases(self):
        from forge import tables
        self.assertEqual(tables.refs("C1, C2 C5-C7"), ["C1", "C2", "C5", "C6", "C7"])
        self.assertEqual(tables.key_of("Designator"), "ref")
        self.assertEqual(tables.key_of("MPN (primary)"), "mpn")
        self.assertEqual(tables.key_of("Pins (Ref.pin)"), "pins")


BOM_HDR = open(os.path.join(REPO, "template/04_Hardware/BOM/WIP_BOM.csv")).readline().strip().split(",")
PARTS = {"C1": "10µF", "C2": "10 uF", "D1": "B360B", "J1": "MSTBA_2,5/2-G-5,08", "R1": "1", "R2": "1 Ω"}


@unittest.skipUnless(kicad_cli(), "kicad-cli not installed")
class Drift(unittest.TestCase):
    """WP4: forge drift."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pe-forge-drift-")
        self.dir = new_project("Drf", self.tmp)
        forge("module", "add", "Power", cwd=self.dir)
        self.mod = os.path.join(self.dir, "modules/M-01_Power")
        self.sheet = os.path.join(self.dir, "08_PCB/drf/drf.kicad_sch")

    def tearDown(self):
        shutil.rmtree(self.tmp, True)

    def bom(self, **change):
        with open(os.path.join(self.mod, "BOM.csv"), "w", newline="") as fh:
            w = csv.DictWriter(fh, BOM_HDR)
            w.writeheader()
            for ref, val in PARTS.items():
                w.writerow(dict(dict.fromkeys(BOM_HDR, "x"), Ref=ref, Value=change.get(ref, val), DNP="",
                                MPN="", Module="M-01"))

    def brief(self, netlist, sheet="08_PCB/drf/drf.kicad_sch"):
        rows = "\n".join(f"| {n} | power | {', '.join(p)} | |" for n, p in netlist.items())
        open(os.path.join(self.mod, "Sheet_Brief.md"), "w").write(
            f"---\ntype: sheet-brief\nmodule: M-01\nkicad_sheet: {sheet}\n---\n# brief\n\n"
            "## Netlist\n| Net | Class | Pins (Ref.pin) | Notes |\n|---|---|---|---|\n" + rows + "\n")

    def drift(self, *a):
        return forge("drift", *a, cwd=self.dir, check=False)

    def test_bom_value_change_is_reported(self):
        shutil.copy(os.path.join(KFIX, "input_protection.kicad_sch"), self.sheet)
        self.bom()
        r = self.drift()
        self.assertEqual(r.returncode, 0, r.stdout)
        self.bom(R1="10k")
        r = self.drift("--json")
        self.assertEqual(r.returncode, 1)
        f = json.loads(r.stdout)["findings"]
        self.assertEqual(len(f), 1, f)
        self.assertEqual(f[0]["doc"], "modules/M-01_Power/BOM.csv")
        self.assertEqual(f[0]["claim"], "R1 = 10k")
        self.assertEqual(f[0]["actual"], "R1 = 1 in drf.kicad_sch")
        self.assertEqual(forge("check", "--only", "drift", cwd=self.dir, check=False).returncode, 1)

    def test_brief_netlist_catches_a_short_on_a_user_drawn_sheet(self):
        net = json.load(open(os.path.join(FIX, "short_circuit.json")))["netlist"]
        shutil.copy(os.path.join(KFIX, "input_protection.kicad_sch"), self.sheet)
        self.brief(net)
        self.assertEqual(self.drift().returncode, 0, self.drift().stdout)
        shutil.copy(os.path.join(KFIX, "short_circuit.kicad_sch"), self.sheet)
        r = self.drift()
        self.assertEqual(r.returncode, 1)
        self.assertIn("modules/M-01_Power/Sheet_Brief.md: net VIN joins", r.stdout)

    def test_empty_sheet_is_bootstrap_until_g6(self):
        self.bom()
        r = self.drift()
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertNotIn("WARN", r.stdout)
        st = os.path.join(self.dir, ".forge/state.json")
        s = json.load(open(st))
        s["modules"][0]["phases"]["P6"]["status"] = "passed"
        json.dump(s, open(st, "w"))
        r = self.drift()
        self.assertEqual(r.returncode, 0)
        self.assertIn("WARN modules/M-01_Power/BOM.csv", r.stdout)
        d = json.loads(forge("check", "--only", "drift", "--json", cwd=self.dir, check=False).stdout)
        self.assertEqual(d["checks"]["drift"]["status"], "warn")


class Ledger(unittest.TestCase):
    """WP5: obligations recorded by PostToolUse, enforced at Stop."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pe-forge-ledger-")
        self.dir = new_project("Led", self.tmp)
        self.sheet = os.path.join(self.dir, "08_PCB/led/led.kicad_sch")

    def tearDown(self):
        shutil.rmtree(self.tmp, True)

    def hook(self, sub, payload):
        return forge("ledger", sub, "--hook", cwd=self.dir, check=False, stdin=json.dumps(payload))

    def write(self, fixture):
        self.hook("record", {"tool_name": "Read", "tool_input": {}})      # baseline snapshot
        shutil.copy(os.path.join(KFIX, fixture), self.sheet)
        return self.hook("record", {"tool_name": "Write", "tool_input": {"file_path": self.sheet}, "cwd": self.dir})

    @unittest.skipUnless(kicad_cli(), "kicad-cli not installed")
    def test_erc_error_blocks_stop_until_fixed(self):
        self.write("short_circuit.kicad_sch")
        r = self.hook("check", {"stop_hook_active": False})
        self.assertEqual(r.returncode, 2)
        self.assertIn("open: erc:08_PCB/led/led.kicad_sch", r.stderr)
        r = self.hook("check", {"stop_hook_active": True})          # no loop: never blocks twice in a row
        self.assertEqual(r.returncode, 0)
        self.assertIn("still open", json.loads(r.stdout)["systemMessage"])
        shutil.copy(os.path.join(KFIX, "input_protection.kicad_sch"), self.sheet)
        self.hook("record", {"tool_name": "Write", "tool_input": {"file_path": self.sheet}, "cwd": self.dir})
        r = self.hook("check", {"stop_hook_active": False})
        self.assertEqual(r.returncode, 0, r.stderr)
        d = json.load(open(os.path.join(self.dir, ".forge/ledger.json")))
        self.assertEqual(d["open"], [])
        erc = [o for o in d["closed"] if o["kind"] == "erc"]
        self.assertTrue(erc and erc[-1]["closed_at"])

    @unittest.skipUnless(kicad_cli(), "kicad-cli not installed")
    def test_user_edit_is_advice_first_then_blocking(self):
        self.hook("record", {"tool_name": "Read", "tool_input": {}})
        self.hook("check", {})                                      # baseline at a Stop
        shutil.copy(os.path.join(KFIX, "short_circuit.kicad_sch"), self.sheet)   # nobody recorded this edit
        r = self.hook("check", {})
        self.assertEqual(r.returncode, 0)
        self.assertIn("outside Claude", json.loads(r.stdout)["systemMessage"])
        r = self.hook("check", {})
        self.assertEqual(r.returncode, 2)

    def test_superseded_term_blocks_like_the_old_hook(self):
        with open(os.path.join(self.dir, ".forge/superseded_terms.csv"), "a") as fh:
            fh.write("LM5146,LM5148,2026-11-04,test\n")
        open(os.path.join(self.dir, "04_Hardware/n.md"), "w").write("# n\nuse LM5146\n")
        self.assertEqual(self.hook("check", {}).returncode, 2)
        self.assertEqual(forge("consistency", "--hook", cwd=self.dir, check=False, stdin="{}").returncode, 2)

    def test_silent_outside_project(self):
        empty = tempfile.mkdtemp()
        for sub in ("record", "check"):
            r = forge("ledger", sub, "--hook", cwd=empty, check=False, stdin="{}")
            self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))
        shutil.rmtree(empty)

    def test_clear_is_logged(self):
        self.hook("record", {"tool_name": "Read", "tool_input": {}})
        open(os.path.join(self.dir, "02_Requirements/x.md"), "w").write("x\n")
        self.hook("record", {"tool_name": "Write", "tool_input": {"file_path": os.path.join(self.dir, "02_Requirements/x.md")}})
        self.assertIn("vmodel:project", forge("ledger", "show", cwd=self.dir).stdout)
        forge("ledger", "clear", "--reason", "owner test", cwd=self.dir)
        self.assertIn("OVERRIDE: owner test", open(os.path.join(self.dir, "01_Program/QC/QC_Register.md")).read())


class ChangeControl(unittest.TestCase):
    """WP6: design files locked after G6 need an approved change request."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pe-forge-cr-")
        self.dir = new_project("Ccl", self.tmp)
        forge("module", "add", "Power", cwd=self.dir)
        self.bom = os.path.join(self.dir, "modules/M-01_Power/BOM.csv")
        open(self.bom, "w").write("Ref,Value\nR1,10k\n")

    def tearDown(self):
        shutil.rmtree(self.tmp, True)

    def baseline(self):
        st = os.path.join(self.dir, ".forge/state.json")
        s = json.load(open(st))
        s["modules"][0]["phases"]["P6"].update(status="passed", date="2026-11-07")
        json.dump(s, open(st, "w"))

    def guard(self, path):
        r = forge("cr", "guard", "--hook", cwd=self.dir, check=False,
                  stdin=json.dumps({"tool_name": "Edit", "tool_input": {"file_path": path}, "cwd": self.dir}))
        self.assertEqual(r.returncode, 0)
        return json.loads(r.stdout)["hookSpecificOutput"] if r.stdout.strip().startswith('{"hook') else None

    def test_not_locked_before_g6(self):
        self.assertIsNone(self.guard(self.bom))

    def test_locked_after_g6_until_cr_approved(self):
        self.baseline()
        d = self.guard(self.bom)
        self.assertEqual(d["permissionDecision"], "deny")
        self.assertIn("forge cr new", d["permissionDecisionReason"])
        self.assertIsNone(self.guard(os.path.join(self.dir, "00_Inbox/notes.md")))     # other files stay free
        forge("cr", "new", "R1 to 4.7k", "--modules", "M-01", "--files", "modules/M-01_Power/BOM.csv",
              "--reason", "review finding", cwd=self.dir)
        r = forge("cr", "approve", "CR-001", cwd=self.dir, check=False)              # no TTY: agent context
        self.assertEqual(r.returncode, 1)
        self.assertIn("owner only", r.stdout + r.stderr)
        self.assertEqual(self.guard(self.bom)["permissionDecision"], "deny")        # draft does not unlock
        forge("cr", "approve", "CR-001", "--i-am-the-owner", cwd=self.dir)
        self.assertIsNone(self.guard(self.bom))
        self.assertEqual(self.guard(os.path.join(self.mod_dir(), "Design_Booklet.md"))["permissionDecision"], "deny")

    def mod_dir(self):
        return os.path.join(self.dir, "modules/M-01_Power")

    def test_off_disables_the_guard(self):
        self.baseline()
        pt = os.path.join(self.dir, "project.toml")
        txt = open(pt).read()
        self.assertIn('change_control = "enforce"', txt)
        open(pt, "w").write(txt.replace('change_control = "enforce"', 'change_control = "off"'))
        self.assertIsNone(self.guard(self.bom))

    def test_txn_verify_catches_shell_edits_of_locked_files(self):
        git(self.dir, "add", "-A")
        git(self.dir, "commit", "-qm", "base", "--no-verify")       # the stub BoM is incomplete on purpose
        self.baseline()
        forge("txn", "begin", "edit", "--allow-dirty", cwd=self.dir)
        open(self.bom, "a").write("R2,1k\n")
        r = forge("txn", "verify", cwd=self.dir, check=False)
        self.assertEqual(r.returncode, 1)
        self.assertIn("FAIL change_control", r.stdout)


GERBERS = ["F_Cu.gtl", "In1_Cu.g1", "In2_Cu.g2", "B_Cu.gbl", "F_Mask.gts", "B_Mask.gbs", "F_Paste.gtp",
           "F_Silkscreen.gto", "B_Silkscreen.gbo", "Edge_Cuts.gm1"]


@unittest.skipUnless(kicad_cli(), "kicad-cli not installed")
class FabGate(unittest.TestCase):
    """WP7: forge check --fab."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pe-forge-fab-")
        self.dir = new_project("Fab", self.tmp)
        self.k = os.path.join(self.dir, "08_PCB/fab")
        shutil.copy(os.path.join(KFIX, "broken.kicad_pcb"), os.path.join(self.k, "fab.kicad_pcb"))
        shutil.copy(os.path.join(KFIX, "input_protection.kicad_sch"), os.path.join(self.k, "fab.kicad_sch"))
        g = os.path.join(self.k, "release/A/gerbers")
        os.makedirs(g)
        for n in GERBERS:
            open(os.path.join(g, f"fab-{n}"), "w").write("G04 test*\n")
        open(os.path.join(g, "fab.drl"), "w").write("M48\n")
        open(os.path.join(self.k, "release/A/fab-pos.csv"), "w").write("Ref,Val,Package,PosX,PosY,Rot,Side\n")
        forge("fab", "manifest", self.k, cwd=self.dir)

    def tearDown(self):
        shutil.rmtree(self.tmp, True)

    def fab(self, *a):
        r = forge("check", "--only", "fab", "--json", *a, cwd=self.dir, check=False)
        return json.loads(r.stdout)["checks"]["fab"]

    def test_parts_and_fresh_outputs(self):
        f = self.fab()
        self.assertEqual(set(f["parts"]), {"routing", "sch_pcb_match", "bom_ready", "outputs_fresh",
                                           "outputs_complete", "power_rules", "docs"})
        self.assertEqual(f["parts"]["outputs_fresh"]["status"], "pass", f["parts"]["outputs_fresh"])
        self.assertEqual(f["parts"]["outputs_complete"]["status"], "pass", f["parts"]["outputs_complete"])
        self.assertEqual(f["parts"]["sch_pcb_match"]["status"], "fail")      # parts not placed on the board yet
        self.assertEqual(f["parts"]["docs"]["status"], "fail")

    def test_board_edit_after_release_is_stale(self):
        open(os.path.join(self.k, "fab.kicad_pcb"), "a").write("\n")
        p = self.fab()["parts"]["outputs_fresh"]
        self.assertEqual(p["status"], "fail")
        self.assertTrue(any(x["location"] == "fab.kicad_pcb" and "re-run ./release.sh" in x["actual"] for x in p["findings"]))

    def test_missing_gerber_layer_named(self):
        os.remove(os.path.join(self.k, "release/A/gerbers/fab-F_Mask.gts"))
        p = self.fab()["parts"]["outputs_complete"]
        self.assertEqual(p["status"], "fail")
        self.assertIn("Gerber for F.Mask", [x["claim"] for x in p["findings"]])

    def test_seed_fails_nrnd_warns_then_fails_strict(self):
        self.assertIn("status Seed", json.dumps(self.fab()["parts"]["bom_ready"]))
        sch = os.path.join(self.k, "fab.kicad_sch")
        t = open(sch).read().replace('(property "Status" "Seed"', '(property "Status" "Active"')
        t = t.replace('(property "Alt_MPN" ""', '(property "Alt_MPN" "ALT-1"')
        head, tail = t.rsplit('(property "Status" "Active"', 1)      # the last one is a placed part, not lib_symbols
        t = head + '(property "Status" "NRND"' + tail
        open(sch, "w").write(t)
        self.assertEqual(self.fab()["parts"]["bom_ready"]["status"], "warn")
        self.assertEqual(self.fab("--strict")["parts"]["bom_ready"]["status"], "fail")


@unittest.skipUnless(kicad_cli(), "kicad-cli not installed")
class JlcExport(unittest.TestCase):
    """WP8: bom.py --jlcpcb, LCSC field, spares, schema migration."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pe-forge-jlc-")
        self.db = os.path.join(self.tmp, "parts.sqlite")
        shutil.copy(os.path.join(REPO, "library/database/mc_parts.sqlite"), self.db)
        import sqlite3
        con = sqlite3.connect(self.db)
        for table, pid, lc, rot in [("Capacitors", "CAP-0035", "C13585", ""), ("Diodes", "DIO-0015", "C8678", "90"),
                                    ("Connectors", "CON-0009", "C2896", ""), ("Resistors", "RES-0184", "C22936", "")]:
            con.execute(f"UPDATE {table} SET LCSC_PN=?, JLC_Rotation=? WHERE ID=?", (lc, rot, pid))
        con.commit()
        self.con = con
        self.sch = os.path.join(self.tmp, "ip.kicad_sch")
        shutil.copy(os.path.join(KFIX, "input_protection.kicad_sch"), self.sch)
        self.pos = os.path.join(self.tmp, "pos.csv")
        rows = ["Ref,Val,Package,PosX,PosY,Rot,Side"] + [f'"{r}","v","p",{10 + i},20,0,top'
                                                          for i, r in enumerate(["C1", "C2", "D1", "J1", "R1", "R2"])]
        open(self.pos, "w").write("\n".join(rows) + "\n")

    def tearDown(self):
        self.con.close()
        shutil.rmtree(self.tmp, True)

    def bom(self, *a):
        env = dict(ENV, MC_DB=self.db)
        return subprocess.run([sys.executable, os.path.join(REPO, "library/tools/bom.py"), self.sch, "-o", self.tmp,
                               "--pos", self.pos, *a], capture_output=True, text=True, env=env)

    def rows(self, name):
        return list(csv.DictReader(open(os.path.join(self.tmp, name))))

    def test_bom_groups_and_cpl_rotation(self):
        r = self.bom("--jlcpcb")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        b = self.rows("ip-jlcpcb-bom.csv")
        self.assertEqual(len(b), 4)
        self.assertIn({"Comment": "10µF", "Designator": "C1,C2", "Footprint": "C_1206_3216Metric",
                       "LCSC Part #": "C13585"}, b)
        cpl = {x["Designator"]: x for x in self.rows("ip-jlcpcb-cpl.csv")}
        self.assertEqual(len(cpl), 6)
        self.assertEqual(cpl["D1"]["Rotation"], "90")       # only the part with an offset turns
        self.assertEqual(cpl["R1"]["Rotation"], "0")
        self.assertEqual(cpl["C1"]["Layer"], "Top")

    def test_missing_lcsc_fails_unless_allowed(self):
        self.con.execute("UPDATE Resistors SET LCSC_PN='' WHERE ID='RES-0184'")
        self.con.commit()
        r = self.bom("--jlcpcb")
        self.assertEqual(r.returncode, 1)
        self.assertIn("R1 (1", r.stderr)
        r = self.bom("--jlcpcb", "--allow-missing")
        self.assertEqual(r.returncode, 0)
        self.assertIn("warning: 2 part(s) without an LCSC number", r.stderr)

    def test_spares(self):
        r = self.bom("--boards", "10", "--spares", "2")
        self.assertEqual(r.returncode, 0, r.stderr)
        q = {x["Customer Reference"]: int(x["Quantity"]) for x in self.rows("ip-digikey.csv")}
        self.assertEqual(q["C1,C2"], 22)      # passive: 20 + max(2, 10 % of 20)
        self.assertEqual(q["D1"], 12)

    def test_migration_idempotent(self):
        env = dict(ENV, MC_DB=self.db)
        mc = [sys.executable, os.path.join(REPO, "library/tools/mclib.py"), "init"]
        dump = lambda: subprocess.run(["sqlite3", self.db, ".schema"], capture_output=True, text=True).stdout
        subprocess.run(mc, env=env, check=True, capture_output=True)
        s1 = dump()
        subprocess.run(mc, env=env, check=True, capture_output=True)
        self.assertEqual(dump(), s1)
        self.assertIn("LCSC_PN", s1)


class ExplainLookup(unittest.TestCase):
    """WP9: the lookup rules, without KiCad."""

    def test_pin_name_needs_ref_when_repeated(self):
        from forge.explain import Lookup
        comps = {"U1": {"pins": {"1": {"name": "VCC", "net": "P5V", "type": "power_in"}}},
                 "U2": {"pins": {"1": {"name": "VCC", "net": "P5V", "type": "power_in"},
                                 "2": {"name": "EN", "net": "EN", "type": "input"}}}}
        L = Lookup(comps, {"P5V": {"U1.1", "U2.1"}, "EN": {"U2.2"}})
        kind, data = L.resolve("VCC")
        self.assertEqual(kind, "ambiguous")
        self.assertEqual(L.resolve("U2.VCC")[0], "pin")
        self.assertEqual(L.resolve("EN")[0], "net")          # a net name wins over a pin name


@unittest.skipUnless(kicad_cli(), "kicad-cli not installed")
class Explain(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pe-forge-explain-")
        self.dir = new_project("Exp", self.tmp)
        shutil.copy(os.path.join(KFIX, "input_protection.kicad_sch"), os.path.join(self.dir, "08_PCB/exp/exp.kicad_sch"))

    def tearDown(self):
        shutil.rmtree(self.tmp, True)

    def ex(self, *a):
        return forge("explain", *a, cwd=self.dir, check=False)

    def test_net(self):
        r = self.ex("GND", "--json")
        self.assertEqual(r.returncode, 0, r.stdout)
        d = json.loads(r.stdout)
        self.assertEqual((d["ok"], d["kind"]), (True, "net"))
        self.assertEqual(sorted(p["pin"] for p in d["pins"]), ["C1.2", "C2.2", "J1.2", "R2.2"])

    def test_pin_and_part(self):
        self.assertIn("-> net VOUT", self.ex("D1.1").stdout)
        self.assertIn("-> net VOUT", self.ex("D1.K").stdout)
        os.makedirs(os.path.join(self.dir, "04_Hardware/Design_Booklets"), exist_ok=True)
        open(os.path.join(self.dir, "04_Hardware/Design_Booklets/B.md"), "w").write("# b\n\nD1 blocks reverse input.\n")
        out = self.ex("D1").stdout
        self.assertIn("pin    2 A", out)
        self.assertIn("04_Hardware/Design_Booklets/B.md:3", out)

    def test_unknown_suggests(self):
        r = self.ex("VOTU")
        self.assertEqual(r.returncode, 1)
        self.assertIn("did you mean", r.stdout)
        self.assertIn("VOUT", r.stdout)


class McpTools(unittest.TestCase):
    """WP10: the outcome-level tools behind forge-mcp (no MCP package needed to test them)."""

    @classmethod
    def setUpClass(cls):
        from forge import mcp_tools
        cls.T = mcp_tools
        cls.tmp = tempfile.mkdtemp(prefix="pe-forge-mcp-")
        cls.dir = new_project("Mcp", cls.tmp)
        os.environ["MC_LIB"] = ENV["MC_LIB"]

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, True)

    def test_check_matches_cli(self):
        r = self.T.forge_check(self.dir)
        cli = json.loads(forge("check", "--json", cwd=self.dir, check=False).stdout)
        self.assertEqual(set(r), {"usable", "verdict", "checks", "warnings", "report"})
        self.assertEqual({k: v["status"] for k, v in r["report"]["checks"].items()},
                         {k: v["status"] for k, v in cli["checks"].items()})
        self.assertEqual(r["usable"], cli["ok"])

    def test_paths_outside_project_refused(self):
        r = self.T.forge_explain(self.dir, "GND", sheet="../../etc/hosts")
        self.assertFalse(r["usable"])
        self.assertIn("outside the project", r["verdict"])
        r = self.T.forge_schdraw_check(self.dir, {"sheet": "../../etc/x.kicad_sch"})
        self.assertIn("refused", r["verdict"])
        self.assertIn("not inside a pe-forge project", self.T.forge_status("/")["verdict"])

    def test_no_generic_write_or_owner_tools(self):
        names = [f.__name__ for f in self.T.TOOLS]
        for bad in ("write", "edit", "file", "approve", "pass", "delete", "shell", "run"):
            self.assertFalse([n for n in names if bad in n.split("_")], bad)
        self.assertEqual(len(names), 9)

    @unittest.skipUnless(kicad_cli(), "kicad-cli not installed")
    def test_apply_on_baselined_module_needs_cr(self):
        forge("module", "add", "Power", cwd=self.dir)
        sheet = "08_PCB/mcp/power.kicad_sch"
        forge("schdraw", "new", os.path.join(self.dir, sheet), "--title", "t")
        mod = os.path.join(self.dir, "modules/M-01_Power")
        open(os.path.join(mod, "Sheet_Brief.md"), "w").write(f"---\nmodule: M-01\nkicad_sheet: {sheet}\n---\n# b\n")
        st = os.path.join(self.dir, ".forge/state.json")
        s = json.load(open(st))
        s["modules"][0]["phases"]["P6"]["status"] = "passed"
        json.dump(s, open(st, "w"))
        spec = dict(json.load(open(os.path.join(FIX, "input_protection.json"))), sheet=sheet)
        self.assertTrue(self.T.forge_schdraw_check(self.dir, spec)["usable"])
        r = self.T.forge_schdraw_apply(self.dir, spec)
        self.assertFalse(r["usable"])
        self.assertIn("forge cr new", r["output"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
