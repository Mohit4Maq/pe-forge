#!/usr/bin/env python3
"""pe-forge self-test (stdlib unittest). Run:  python3 tests/test_forge.py   (or ./tests/run.sh)

Covers: scaffold, status / modules / gates, V-model build + check, consistency, ID linking, edit watch, BoM
check, and the schematic engine (a clean sheet must pass; a sheet with a short must be caught by both the
geometry check and KiCad's own netlist). Schematic tests are skipped when kicad-cli is not installed.
"""
import csv
import json
import re
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FORGE = [sys.executable, os.path.join(REPO, "forge", "__main__.py")]
FIX = os.path.join(REPO, "tests", "fixtures")
ENV = dict(os.environ, MC_LIB=os.path.join(REPO, "library"))   # always test against the bundled library
ENV.pop("CLAUDE_PROJECT_DIR", None)
_CFG = tempfile.mkdtemp(prefix="pe-forge-cfg-")          # status line records never touch the real ~/.config
ENV["PE_FORGE_CONFIG_DIR"] = os.environ["PE_FORGE_CONFIG_DIR"] = _CFG
ENV["PE_FORGE_OBSIDIAN_REGISTRY"] = os.environ["PE_FORGE_OBSIDIAN_REGISTRY"] = os.path.join(_CFG, "obsidian.json")


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

    def mirror_spec(self, label_net="VIN"):
        spec = {"sheet": "demo.kicad_sch", "netlist": {"VIN": ["J1.1", "R1.1"], "GND": ["J1.2", "R1.2"]},
                "parts": [{"id": "CON-0009", "ref": "J1", "x": 50.8, "y": 50.8, "rot": 0, "mirror": "y"},
                          {"id": "RES-0304", "ref": "R1", "x": 76.2, "y": 54.61, "rot": 0}],
                "connect": [{"net": "VIN", "path": ["J1.1", "R1.1"]},
                            {"net": "GND", "path": ["J1.2", [58.42, 53.34], [58.42, 60.96]]}],
                "stubs": [{"pin": "R1.2", "power": "GND", "dir": "D", "len": 2.54}],
                "power": [{"net": "GND", "x": 58.42, "y": 60.96, "dir": "D"}],
                "flags": [{"x": 58.42, "y": 57.15, "dir": "R", "net": "GND", "len": 5.08},
                          {"x": 68.58, "y": 50.8, "dir": "U", "net": "VIN"}],
                "labels": [{"net": label_net, "x": 63.5, "y": 50.8, "rot": 0, "kind": "local"}]}
        path = os.path.join(self.tmp, "m.json")
        json.dump(spec, open(path, "w"))
        return forge("schdraw", "check", path, cwd=self.tmp, check=False)

    def test_mid_wire_label_and_mirrored_connector_text(self):
        r = self.mirror_spec()
        self.assertNotIn("S7", r.stdout)                 # a label along its own wire names the rail
        self.assertNotIn("J1", "".join(l for l in r.stdout.splitlines() if "S13" in l))   # text above, off the body
        r = self.mirror_spec("GND")
        self.assertIn("would merge the nets", r.stdout)   # a label on another net's wire

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
                "for m in ('forge.drift', 'forge.fab', 'forge.insulation'):\n"
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
        import sqlite3
        self.assertIn("status Seed", json.dumps(self.fab()["parts"]["bom_ready"]))
        db = os.path.join(self.tmp, "parts.sqlite")              # the parts database is authoritative, not the sheet
        shutil.copy(os.path.join(REPO, "library/database/mc_parts.sqlite"), db)
        con = sqlite3.connect(db)
        for tbl in ("Capacitors", "Diodes", "Connectors", "Resistors"):
            con.execute(f"UPDATE {tbl} SET Status='Active', Alt_MPN='ALT-1', Verified_Against='0123456789abcdef#p1', "
                        "Verified_By='T', Verified_On='2026-11-07'")
        con.commit()
        os.environ["MC_DB"] = ENV["MC_DB"] = db
        try:
            self.assertEqual(self.fab()["parts"]["bom_ready"]["status"], "pass", self.fab()["parts"]["bom_ready"])
            con.execute("UPDATE Diodes SET Status='NRND' WHERE ID='DIO-0015'")
            con.commit()
            self.assertEqual(self.fab()["parts"]["bom_ready"]["status"], "warn")
            self.assertEqual(self.fab("--strict")["parts"]["bom_ready"]["status"], "fail")
            con.execute("UPDATE Diodes SET Status='Active', Verified_Against='' WHERE ID='DIO-0015'")
            con.commit()
            self.assertIn("Active but no datasheet evidence", json.dumps(self.fab()["parts"]["bom_ready"]))
        finally:
            con.close()
            os.environ.pop("MC_DB", None)
            ENV.pop("MC_DB", None)


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


@unittest.skipUnless(kicad_cli(), "kicad-cli not installed")
class Lint(unittest.TestCase):
    """WP11: forge schdraw lint."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pe-forge-lint-")
        self.dir = new_project("Lnt", self.tmp)
        git(self.dir, "add", "-A")
        git(self.dir, "commit", "-qm", "base", "--no-verify")
        self.sheet = os.path.join(self.dir, "08_PCB/lnt/demo.kicad_sch")
        forge("schdraw", "new", self.sheet, "--title", "Input protection")
        git(self.dir, "add", "-A")
        git(self.dir, "commit", "-qm", "sheet", "--no-verify")
        spec = dict(json.load(open(os.path.join(FIX, "input_protection.json"))), sheet=self.sheet)
        self.spec = os.path.join(self.dir, "00_Inbox/spec.json")
        json.dump(spec, open(self.spec, "w"))
        git(self.dir, "add", "-A")
        git(self.dir, "commit", "-qm", "spec", "--no-verify")
        forge("schdraw", "check", self.spec, cwd=self.dir)
        forge("schdraw", "apply", self.spec, cwd=self.dir)

    def tearDown(self):
        shutil.rmtree(self.tmp, True)

    def lint(self):
        r = forge("schdraw", "lint", self.sheet, "--json", cwd=self.dir, check=False)
        return r.returncode, json.loads(r.stdout)

    def test_engine_sheet_has_no_errors_and_score_is_stable(self):
        rc, d = self.lint()
        self.assertEqual(d["owner"], "engine")
        self.assertEqual(d["errors"], [], d["findings"])
        self.assertEqual(rc, 0)
        self.assertEqual(self.lint()[1]["score"], d["score"])

    def test_user_moves_a_part_off_grid_advice_only(self):
        t = open(self.sheet).read()
        m = re.search(r'\n\t\(symbol\n\t\t\(lib_id "MC_Parts:[^"]+"\)\n\t\t\(at ([-\d.]+) ', t)
        t = t[:m.start(1)] + str(round(float(m.group(1)) + 0.5, 2)) + t[m.end(1):]
        open(self.sheet, "w").write(t)
        rc, d = self.lint()
        self.assertEqual(d["owner"], "user")
        self.assertEqual(rc, 0)
        self.assertTrue(any(f.startswith("S1 ") and "off the 1.27 mm grid" in f for f in d["findings"]), d["findings"])
        c = json.loads(forge("check", "--only", "legibility", "--json", cwd=self.dir, check=False).stdout)
        self.assertEqual(c["checks"]["legibility"]["status"], "warn")
        self.assertTrue(c["ok"])


BRIEF_SIM = """---
module: M-01
kicad_sheet: 08_PCB/spc/spc.kicad_sch
---
# brief

## Simulation
scope: nets {scope}
analysis: {analysis}
source: VOUT = {source}
{ignore}
assert: {assertion}
{expect}
"""


@unittest.skipUnless(kicad_cli(), "kicad-cli not installed")
class SpiceAssert(unittest.TestCase):
    """WP12: forge spice check."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pe-forge-spice-")
        self.dir = new_project("Spc", self.tmp)
        self.sheet = os.path.join(self.dir, "08_PCB/spc/spc.kicad_sch")
        shutil.copy(os.path.join(KFIX, "input_protection.kicad_sch"), self.sheet)
        forge("module", "add", "Sense", cwd=self.dir)
        self.mod = os.path.join(self.dir, "modules/M-01_Sense")

    def tearDown(self):
        shutil.rmtree(self.tmp, True)

    def brief(self, assertion="V(VSENSE) between 5.9 and 6.1 at op", expect="expect: 6", scope="VOUT, VSENSE, GND",
              analysis="op", source="12", ignore="ignore: D1"):
        open(os.path.join(self.mod, "Sheet_Brief.md"), "w").write(BRIEF_SIM.format(
            assertion=assertion, expect=expect, scope=scope, analysis=analysis, source=source, ignore=ignore))

    def spice(self, *a, env=None):
        e = dict(ENV, **(env or {}))
        return subprocess.run(FORGE + ["spice", "check", "--json", *a], cwd=self.dir, capture_output=True, text=True, env=e)

    @unittest.skipUnless(shutil.which("ngspice") or os.path.exists("/opt/homebrew/bin/ngspice"), "ngspice not installed")
    def test_divider_holds_then_fails_on_a_changed_resistor(self):
        self.brief()
        r = self.spice()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(json.loads(r.stdout)["status"], "pass")
        rec = open(os.path.join(self.mod, "Simulations/Sheet_Brief_spice.md")).read()
        self.assertIn("ACCEPTED", rec)
        self.assertIn("Simulation Verification Record", rec)
        t = open(self.sheet).read()
        head, tail = t.rsplit('(property "Value" "1"', 1)        # the last placed resistor: R2
        open(self.sheet, "w").write(head + '(property "Value" "3"' + tail)
        r = self.spice()
        self.assertEqual(r.returncode, 1)
        res = json.loads(r.stdout)["results"][0]
        self.assertEqual(res["status"], "fail")
        self.assertIn("measured 9; bound 5.9 to 6.1", res["detail"])

    @unittest.skipUnless(shutil.which("ngspice") or os.path.exists("/opt/homebrew/bin/ngspice"), "ngspice not installed")
    def test_closed_form_disagreement_is_reported_even_inside_the_bound(self):
        self.brief(expect="expect: 6.5")
        d = json.loads(self.spice().stdout)
        self.assertEqual(d["results"][0]["status"], "provisional")
        self.assertIn("disagree beyond 2 %", d["results"][0]["detail"])
        self.assertEqual(d["status"], "warn")

    @unittest.skipUnless(shutil.which("ngspice") or os.path.exists("/opt/homebrew/bin/ngspice"), "ngspice not installed")
    def test_ac_gain_and_unmodelled_part(self):
        self.brief("gain(VSENSE, VOUT) between 0.49 and 0.51 at 1k", "expect: 0.5", analysis="ac 10 1e5", source="0 ac 1")
        d = json.loads(self.spice().stdout)
        self.assertEqual(d["results"][0]["status"], "pass", d)
        self.brief(scope="VOUT, VSENSE, GND", ignore="")          # D1 reaches outside the scope: the block stops
        d = json.loads(self.spice().stdout)
        self.assertIn("D1 (reaches Net-_D1-A_)", d["results"][0]["detail"])
        self.brief(scope="VOUT, VSENSE, Net-(D1-A), GND", assertion="V(VOUT) between 11 and 13", expect="", ignore="")
        d = json.loads(self.spice().stdout)
        self.assertEqual(d["results"][0]["status"], "skip")
        self.assertIn("no SPICE model for D1, J1", d["results"][0]["detail"])
        self.assertIn("NOT RUN", open(os.path.join(self.mod, "Simulations/Sheet_Brief_spice.md")).read())
        sys.path.insert(0, REPO)
        from forge import config, gates
        prj = config.Project(self.dir)
        ok, msg = gates.sims_ok([os.path.join(self.mod, "Simulations/Sheet_Brief_spice.md")], prj)[0]
        self.assertFalse(ok)                                       # a record whose checks never ran keeps G5 closed
        self.assertIn("result NOT RUN", msg)

    @unittest.skipUnless(shutil.which("ngspice") or os.path.exists("/opt/homebrew/bin/ngspice"), "ngspice not installed")
    def test_sub_sheet_edit_makes_the_record_stale(self):
        for f in ("root.kicad_sch", "sub.kicad_sch"):
            shutil.copy(os.path.join(KFIX, "hier", f), os.path.join(self.dir, "08_PCB/spc", f))
        open(os.path.join(self.mod, "Sheet_Brief.md"), "w").write(
            "---\nmodule: M-01\nkicad_sheet: 08_PCB/spc/root.kicad_sch\n---\n# b\n\n## Simulation\n"
            "scope: sub/V+, sub/V-, GND\nignore: D1\nsource: sub/V+ = 12\nassert: V(sub/V-) between 5.9 and 6.1\n"
            "expect: 6\n")
        d = json.loads(self.spice().stdout)
        self.assertEqual(d["results"][0]["status"], "pass", d["results"][0]["detail"])
        from forge import config, gates
        prj = config.Project(self.dir)
        rec = os.path.join(self.mod, "Simulations/Sheet_Brief_spice.md")
        self.assertTrue(gates.sims_ok([rec], prj)[0][0])
        sub = os.path.join(self.dir, "08_PCB/spc/sub.kicad_sch")
        t = open(sub).read()
        head, tail = t.rsplit('(property "Value" "1"', 1)
        open(sub, "w").write(head + '(property "Value" "3"' + tail)
        ok, msg = gates.sims_ok([rec], prj)[0]
        self.assertFalse(ok)
        self.assertIn("stale", msg)

    @unittest.skipUnless(shutil.which("ngspice") or os.path.exists("/opt/homebrew/bin/ngspice"), "ngspice not installed")
    def test_part_excluded_from_simulation_stops_the_block(self):
        shutil.copy(os.path.join(KFIX, "excl.kicad_sch"), self.sheet)          # the load R2 is excluded from sim
        open(os.path.join(self.mod, "Sheet_Brief.md"), "w").write(
            "---\nmodule: M-01\nkicad_sheet: 08_PCB/spc/spc.kicad_sch\n---\n# b\n\n## Simulation\n"
            "scope: V+, V-, GND\nignore: D1\nsource: V+ = 12\nassert: V(V-) between 5.9 and 6.1\nexpect: 6\n")
        d = json.loads(self.spice().stdout)
        self.assertIn("not in KiCad's SPICE export", d["results"][0]["detail"])
        self.assertEqual(d["results"][0]["status"], "fail")

    @unittest.skipUnless(shutil.which("ngspice") or os.path.exists("/opt/homebrew/bin/ngspice"), "ngspice not installed")
    def test_parse_failure_is_a_rejected_row_and_closes_g5(self):
        self.brief(assertion="V(VSENSE) betwen 5.9 and 6.1")
        self.spice()
        rec = os.path.join(self.mod, "Simulations/Sheet_Brief_spice.md")
        self.assertIn("| REJECTED | does not parse", open(rec).read())
        from forge import config, gates
        self.assertFalse(gates.sims_ok([rec], config.Project(self.dir))[0][0])

    @unittest.skipUnless(shutil.which("ngspice") or os.path.exists("/opt/homebrew/bin/ngspice"), "ngspice not installed")
    def test_stale_record_keeps_g5_closed(self):
        self.brief()
        self.assertEqual(self.spice().returncode, 0)
        from forge import config, gates
        prj = config.Project(self.dir)
        rec = os.path.join(self.mod, "Simulations/Sheet_Brief_spice.md")
        self.assertTrue(gates.sims_ok([rec], prj)[0][0])
        t = open(self.sheet).read()
        head, tail = t.rsplit('(property "Value" "1"', 1)
        open(self.sheet, "w").write(head + '(property "Value" "3"' + tail)       # the drawing changes after the run
        ok, msg = gates.sims_ok([rec], prj)[0]
        self.assertFalse(ok)
        self.assertIn("stale", msg)

    def test_no_ngspice_is_a_skip_and_strict_fails(self):
        self.brief()
        r = self.spice(env={"NGSPICE": "/nonexistent/ngspice"})
        self.assertEqual(r.returncode, 0)
        d = json.loads(r.stdout)
        self.assertEqual(d["status"], "skip")
        self.assertIn("ngspice not found", d["reason"])
        self.assertEqual(self.spice("--strict", env={"NGSPICE": "/nonexistent/ngspice"}).returncode, 1)


@unittest.skipUnless(kicad_cli(), "kicad-cli not installed")
class PartsEvidence(unittest.TestCase):
    """WP13: datasheet cache, evidence pointers, sourcing age."""

    def setUp(self):
        import sqlite3
        self.tmp = tempfile.mkdtemp(prefix="pe-forge-ev-")
        self.db = os.path.join(self.tmp, "parts.sqlite")
        shutil.copy(os.path.join(REPO, "library/database/mc_parts.sqlite"), self.db)
        self.cache = os.path.join(self.tmp, "datasheets")
        self.env = dict(ENV, MC_DB=self.db, MC_DATASHEETS=self.cache)
        self.dir = new_project("Evd", self.tmp)
        shutil.copy(os.path.join(KFIX, "input_protection.kicad_sch"), os.path.join(self.dir, "08_PCB/evd/evd.kicad_sch"))
        self.con = sqlite3.connect(self.db)
        self.con.execute("UPDATE Resistors SET Sourcing_Date=? WHERE ID='RES-0184'", (dt_today(),))
        self.con.commit()

    def tearDown(self):
        self.con.close()
        shutil.rmtree(self.tmp, True)

    def tool(self, *a):
        return subprocess.run([sys.executable, os.path.join(REPO, "library/tools/datasheets.py"), *a],
                              capture_output=True, text=True, env=self.env, cwd=self.tmp)

    def ev(self, *a):
        r = subprocess.run(FORGE + ["check", "--only", "parts_evidence", "--json", *a], cwd=self.dir,
                           capture_output=True, text=True, env=self.env)
        return json.loads(r.stdout)["checks"]["parts_evidence"]

    def r1(self, f):
        return [x for x in f["findings"] if x["claim"].startswith("R1 ")]

    def test_cached_evidence_passes_then_a_missing_hash_fails(self):
        pdf = os.path.join(self.tmp, "rc0603.pdf")
        open(pdf, "wb").write(b"%PDF-1.4\n% test datasheet\n")
        r = self.tool("add", pdf, "--mpn", "RC0603FR-071RL", "--manufacturer", "YAGEO")
        self.assertEqual(r.returncode, 0, r.stderr)
        sha = r.stdout.split()[0]
        self.assertEqual(self.tool("verify", "RES-0184", sha, "--page", "3", "--section", "Dimensions").returncode, 0)
        self.assertIn("not copied into the project", json.dumps(self.r1(self.ev())))
        r = subprocess.run(FORGE + ["evidence", "sync"], cwd=self.dir, capture_output=True, text=True, env=self.env)
        self.assertIn("datasheet(s) in 04_Hardware/Datasheets/", r.stdout)
        self.assertTrue(os.path.exists(os.path.join(self.dir, f"04_Hardware/Datasheets/RC0603FR-071RL_{sha}.pdf")))
        idx = open(os.path.join(self.dir, "04_Hardware/Datasheets/Datasheets_Index.md")).read()
        self.assertIn(f"{sha}#p3/Dimensions", idx)
        self.assertEqual(self.r1(self.ev()), [])
        self.assertEqual(self.tool("check").returncode, 0)
        self.con.execute("UPDATE Resistors SET Verified_Against='deadbeefdeadbeef#p3' WHERE ID='RES-0184'")
        self.con.commit()
        f = self.ev()
        self.assertEqual(f["status"], "fail")
        self.assertIn("deadbeefdeadbeef.pdf is missing", json.dumps(self.r1(f)))

    def test_old_sourcing_snapshot_warns(self):
        self.con.execute("UPDATE Resistors SET Sourcing_Date='2020-01-01' WHERE ID='RES-0184'")
        self.con.commit()
        f = self.ev()
        self.assertEqual(f["status"], "warn")
        self.assertTrue(any("sourcing snapshot" in x["claim"] for x in self.r1(f)))
        self.assertEqual(self.ev("--strict")["status"], "fail")

    def test_new_part_needs_a_datasheet(self):
        mc = [sys.executable, os.path.join(REPO, "library/tools/mclib.py")]
        args = ["add", "Resistors", "MPN=TESTR-1", "Manufacturer=T", "Value=1k", "Package=0603",
                "Symbols=MC_Generic:R", "Footprints=Resistor_SMD:R_0603_1608Metric", "--no-mouser"]
        r = subprocess.run(mc + args, capture_output=True, text=True, env=self.env)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("every new part gets its datasheet", r.stdout + r.stderr)
        pdf = os.path.join(self.tmp, "t.pdf")
        open(pdf, "wb").write(b"%PDF-1.4\n% new part\n")
        r = subprocess.run(mc + args + ["--datasheet", pdf], capture_output=True, text=True, env=self.env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        pid = r.stdout.split()[0]
        ds = self.con.execute("SELECT Datasheet FROM Resistors WHERE ID=?", (pid,)).fetchone()[0]
        self.assertRegex(ds, r"^\$\{MC_LIB\}/datasheets/[0-9a-f]{16}\.pdf$")
        self.assertTrue(os.path.exists(os.path.join(self.cache, os.path.basename(ds))))

    def test_family_verify_previews_then_applies_and_skips_mismatched_footprints(self):
        pdf = os.path.join(self.tmp, "rc.pdf")
        open(pdf, "wb").write(b"%PDF-1.4\n% yageo rc\n")
        sha = self.tool("add", pdf, "--mpn", "RC0603 series").stdout.split()[0]
        r = self.tool("verify-family", sha, "--match", "RC0603*", "--page", "4", "--pads", "2", "--pitch", "1.65",
                      "--activate")
        self.assertIn("preview only", r.stdout)
        n = int(r.stdout.split()[0])
        self.assertGreater(n, 10)
        self.assertEqual(self.con.execute("SELECT count(*) FROM Resistors WHERE Verified_Against!=''").fetchone()[0], 0)
        r = self.tool("verify-family", sha, "--match", "RC0603*", "--page", "4", "--pads", "2", "--pitch", "1.65",
                      "--activate", "--apply")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.con.execute("SELECT count(*) FROM Resistors WHERE Status='Active' AND Verified_Against "
                                          "LIKE ?", (sha + "#p4%",)).fetchone()[0], n)
        r = self.tool("verify-family", sha, "--match", "RC0603*", "--page", "4", "--pads", "3")
        self.assertIn("SKIP", r.stdout)                         # a 2-pad footprint never takes 3-pad evidence
        todo = subprocess.run(FORGE + ["evidence", "todo"], cwd=self.dir, capture_output=True, text=True, env=self.env)
        self.assertNotIn("RC0603FR", todo.stdout)               # R1 / R2 are done now
        self.assertIn("C3216X7R1H106K160AC", todo.stdout)        # the capacitors are not

    def test_active_needs_evidence(self):
        r = subprocess.run([sys.executable, os.path.join(REPO, "library/tools/mclib.py"), "set", "RES-0184", "Status=Active"],
                           capture_output=True, text=True, env=self.env)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("needs datasheet evidence", r.stderr)


def dt_today():
    import datetime
    return datetime.date.today().isoformat()


class StatusLine(unittest.TestCase):
    """WP14: forge statusline, ui theme, install / uninstall, hook chrome."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="pe-forge-sl-")
        cls.cfgdir = os.path.join(cls.tmp, "cfg")
        os.environ["PE_FORGE_CONFIG_DIR"] = cls.cfgdir
        ENV["PE_FORGE_CONFIG_DIR"] = cls.cfgdir
        cls.dir = new_project("Buck48to12", cls.tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, True)

    def bar(self, payload=None, env=None, raw=None):
        stdin = raw if raw is not None else json.dumps(payload or {
            "model": {"display_name": "Opus"}, "workspace": {"current_dir": self.dir, "project_dir": self.dir}})
        e = dict(ENV, COLUMNS="200", **(env or {}))
        r = subprocess.run(FORGE + ["statusline"], input=stdin, capture_output=True, text=True, env=e, cwd=self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.rstrip("\n")

    def test_basic_line(self):
        out = self.bar(env={"NO_COLOR": "1"})
        self.assertEqual(out.count("\n"), 0)
        for s in ("Buck48to12", "P0 Intake", "Opus"):
            self.assertIn(s, out)

    def test_ledger_segment_appears_and_disappears(self):
        led = os.path.join(self.dir, ".forge/ledger.json")
        json.dump({"open": [{"kind": "erc", "target": "a", "opened_by": "agent"},
                            {"kind": "drift", "target": "M-01", "opened_by": "agent"}]}, open(led, "w"))
        with_led = self.bar(env={"NO_COLOR": "1"})
        self.assertIn("2 open: erc, drift", with_led)
        os.remove(led)
        self.assertEqual(self.bar(env={"NO_COLOR": "1"}), with_led.replace(" · ⚠ 2 open: erc, drift", ""))

    def test_outside_project_chains_to_previous(self):
        os.makedirs(self.cfgdir, exist_ok=True)
        json.dump({"command": "echo my-own-line"}, open(os.path.join(self.cfgdir, "statusline_prev.json"), "w"))
        out = self.bar({"workspace": {"current_dir": "/", "project_dir": "/"}})
        self.assertEqual(out, "my-own-line")
        os.remove(os.path.join(self.cfgdir, "statusline_prev.json"))

    def test_bad_input_and_corrupt_state_give_minimal_line(self):
        self.assertEqual(self.bar(raw="not json"), "Claude · " + os.path.basename(self.tmp))
        st = os.path.join(self.dir, ".forge/state.json")
        good = open(st).read()
        open(st, "w").write("{broken")
        try:
            out = self.bar()
            self.assertIn("Opus", out)
            self.assertTrue(os.path.exists(os.path.join(self.dir, ".forge/cache/statusline.log")))
        finally:
            open(st, "w").write(good)

    def test_no_color_and_ascii(self):
        self.assertNotIn("\x1b[", self.bar(env={"NO_COLOR": "1"}))
        self.assertIn("\x1b[", self.bar())
        pt = os.path.join(self.dir, "project.toml")
        orig = open(pt).read()
        open(pt, "w").write(orig + '\n[ui]\nglyphs = "ascii"\ncolor = false\n')
        try:
            out = self.bar()
            self.assertTrue(all(ord(c) < 128 for c in out), out)
        finally:
            open(pt, "w").write(orig)

    def test_fit_drops_whole_segments(self):
        from forge import ui
        segs = [(1, "◆ Buck48to12"), (2, "P6 Schematic"), (3, "M-02 Power stage"), (4, "G6 open"), (11, "Opus")]
        line = ui.fit(segs, 40, " · ")
        self.assertEqual(line, "◆ Buck48to12 · P6 Schematic")          # mark, project and phase kept whole
        self.assertLessEqual(ui.visible_len(line), 40)
        self.assertEqual(ui.visible_len("\x1b[1mab\x1b[0m"), 2)

    def test_fast(self):
        ts = []
        for _ in range(20):
            t0 = time.time()
            self.bar()
            ts.append(time.time() - t0)
        med = sorted(ts)[10] * 1000
        print(f"\n    statusline median {med:.1f} ms", file=sys.stderr)
        if not os.environ.get("CI_SLOW"):
            self.assertLess(med, 100)

    def test_install_uninstall_restores_settings(self):
        from forge import statusline as sl
        d = os.path.join(self.tmp, "settings")
        os.makedirs(d, exist_ok=True)
        fresh = os.path.join(d, "fresh.json")
        sl.install(fresh)
        self.assertIn("statusLine", json.load(open(fresh)))
        sl.install(fresh, uninstall=True)
        self.assertFalse(os.path.exists(fresh))
        had = os.path.join(d, "had.json")
        orig = '{\n    "env": {"A": "1"},\n  "permissions": {"allow": ["Bash(ls)"]},\n  "statusLine": {"type": "command", "command": "mine"}\n}\n'
        open(had, "w").write(orig)
        sl.install(had)
        cur = json.load(open(had))
        self.assertEqual(cur["env"], {"A": "1"})
        self.assertIn("forge", cur["statusLine"]["command"])
        sl.install(had, uninstall=True)
        self.assertEqual(open(had).read(), orig)
        self.assertTrue(os.path.exists(os.path.join(self.dir, ".claude/settings.local.json")))   # forge new

    def test_status_text_unchanged_without_tty(self):
        out = forge("status", cwd=self.dir).stdout
        self.assertNotIn("\x1b[", out)
        self.assertTrue(out.startswith("Buck48to12  ·  profile full"))
        self.assertIn("  ▶ P0 Intake and project set-up", out)

    def test_stop_line_not_repeated(self):
        r1 = forge("ledger", "check", "--hook", cwd=self.dir, check=False, stdin="{}")
        r2 = forge("ledger", "check", "--hook", cwd=self.dir, check=False, stdin="{}")
        self.assertIn("pe-forge ▸ consistency ✓", json.loads(r1.stdout)["systemMessage"])
        self.assertEqual(r2.stdout.strip(), "")
        b = forge("watch", "--report", "--event", "SessionStart", cwd=self.dir).stdout
        self.assertIn("pe-forge ▸ Buck48to12 · P0 Intake", json.loads(b)["systemMessage"])


NG = shutil.which("ngspice") or ("/opt/homebrew/bin/ngspice" if os.path.exists("/opt/homebrew/bin/ngspice") else None)


def circuit(text):
    """KiCad-style SPICE export lines -> (cir, comps) as forge.kicad.design would give them."""
    comps, cir = {}, [".title test"]
    for line in text.strip().split("\n"):
        tok = line.split()
        cir.append(line.strip())
        if tok[0].startswith("."):
            continue
        ref, nodes = tok[0], tok[1:3]
        if len(tok) > 1 and tok[1].startswith("__"):
            ref, nodes = tok[0], tok[2:]                          # test shorthand: "U1 __U1 /INP /OUT /VCC"
            cir[-1] = f"{tok[0]} {tok[1]}"
        comps[ref] = {"fields": {}, "pins": {str(i + 1): {"net": n.lstrip("/"), "token": n}
                                             for i, n in enumerate(nodes)}}
    return "\n".join(cir + [".end"]), comps


@unittest.skipUnless(NG, "ngspice not installed")
class SpiceMeasures(unittest.TestCase):
    """The review's findings, each against a hand calculation."""

    def run_(self, block, text):
        from forge import spice
        blk = spice.parse_block("## Simulation\n" + block)
        cir, comps = circuit(text)
        with tempfile.TemporaryDirectory() as wd:
            res, deck, _ = spice.run_block(blk, cir, comps, NG, wd)
        return res

    RC = "R1 /VIN /OUT 1k\nC1 /OUT GND 159.155n"            # f_c = 1 / (2 pi R C) = 1000 Hz

    def test_op_reading_in_an_ac_block_is_the_operating_point(self):
        r = self.run_("scope: VIN, OUT, GND\nanalysis: ac 10 1e5\nsource: VIN = 5 ac 1\n"
                      "assert: V(OUT) between 4.9 and 5.1 at op\nexpect: 5\n"
                      "assert: f3db(OUT) between 950 and 1050\nexpect: 1000\n"
                      "assert: gain(OUT, VIN) between 0.69 and 0.72 at 1k\nexpect: 0.7071", self.RC)
        self.assertEqual([x["status"] for x in r], ["pass", "pass", "pass"], [x["detail"] for x in r])

    def test_currents_are_delivered_and_ac_is_a_magnitude(self):
        r = self.run_("scope: VIN, GND\nanalysis: op\nsource: VIN = 12\nassert: I(VIN) between 5.9 and 6.1\nexpect: 6",
                      "R1 /VIN GND 2")
        self.assertEqual(r[0]["status"], "pass", r[0]["detail"])
        r = self.run_("scope: VIN, OUT, GND\nanalysis: ac 10 1e5\nsource: VIN = 0 ac 1\n"
                      "assert: I(VIN) between 0.6e-3 and 0.8e-3 at 1k\nexpect: 0.7071m", self.RC)   # |1/(R(1+j))|
        self.assertEqual(r[0]["status"], "pass", r[0]["detail"])

    def test_active_part_reaching_outside_the_scope_stops_the_block(self):
        r = self.run_("scope: INP, OUT, GND\nsource: INP = 1\nassert: V(OUT) between 1.9 and 2.1\nexpect: 2",
                      "R1 /OUT /FB 10k\nR2 /FB GND 10k\nU1 __U1 /INP /FB /OUT /VCC GND")
        self.assertEqual(r[0]["status"], "fail")
        self.assertIn("U1 (reaches FB, VCC)", r[0]["detail"])

    def test_names_never_collide_and_ground_variants(self):
        r = self.run_("scope: V+, V-, GND\nsource: V+ = 12\nassert: V(V-) between 5.9 and 6.1\nexpect: 6",
                      "R1 /V+ /V- 1\nR2 /V- GND 1")
        self.assertEqual(r[0]["status"], "pass", r[0]["detail"])
        r = self.run_("scope: VIN, OUT, PGND\nsource: VIN = 12\nassert: V(OUT) between 5.9 and 6.1\nexpect: 6",
                      "R1 /VIN /OUT 1\nR2 /OUT PGND 1")
        self.assertEqual(r[0]["status"], "pass", r[0]["detail"])
        r = self.run_("scope: VIN, OUT, PGND, GND\nsource: VIN = 12\nassert: V(OUT) < 7", "R1 /VIN /OUT 1\nR2 /OUT PGND 1\nR3 /OUT GND 1")
        self.assertIn("several ground nets in scope", r[0]["detail"])

    def test_missing_or_misspelt_source_is_an_error_not_zero(self):
        r = self.run_("scope: VIN, OUT, GND\nsource: VINN = 12\nassert: V(OUT) < 1", self.RC)
        self.assertIn("'VINN' is not a net", r[0]["detail"])
        r = self.run_("scope: VIN, OUT, GND\nassert: V(OUT) < 1", self.RC)
        self.assertIn("no source", r[0]["detail"])

    def test_band_pass_corners_from_the_peak(self):
        # series C (high-pass, 100 Hz) then R-C low-pass (10 kHz), buffered by distance: corners near both
        bp = "C1 /VIN /A 1.59155u\nR1 /A GND 1k\nR2 /A /OUT 100k\nC2 /OUT GND 159.155p"
        r = self.run_("scope: VIN, A, OUT, GND\nanalysis: ac 1 1e7\nsource: VIN = 0 ac 1\n"
                      "assert: f3db_low(OUT, peak) between 80 and 125\nassert: f3db(OUT, peak) between 8k and 12.5k", bp)
        self.assertEqual([x["status"] for x in r], ["provisional", "provisional"], [x["detail"] for x in r])
        self.assertIn("no closed-form", r[0]["detail"])           # no expect: PROVISIONAL, never ACCEPTED
        r = self.run_("scope: VIN, A, OUT, GND\nanalysis: ac 1 1e7\nsource: VIN = 0 ac 1\nassert: f3db(OUT) < 1e9", bp)
        self.assertIn("use f3db(OUT, peak)", r[0]["detail"])      # a band-pass needs the explicit peak reference

    def test_peaking_low_pass_uses_the_conventional_reference(self):
        # series R-L-C, out across C: f0 1591.5 Hz, Q 2; |H| = 1/sqrt(2) of DC at x^2 = 2.2038 -> 2362.6 Hz
        r = self.run_("scope: VIN, A, OUT, GND\nanalysis: ac 10 1e6\nsource: VIN = 0 ac 1\n"
                      "assert: f3db(OUT) between 2300 and 2420\nexpect: 2362.6 ± 1%",
                      "R1 /VIN /A 50\nL1 /A /OUT 10m\nC1 /OUT GND 1u")
        self.assertEqual(r[0]["status"], "pass", r[0]["detail"])

    def test_settle_and_half_time_step(self):
        blk = "scope: VIN, OUT, GND\nanalysis: tran 1u {stop}\nsource: VIN = 0 pulse(0 1 0 1n 1n 1 2)\n" \
              "assert: settle(OUT, 2%) between 3.5m 4.3m\nexpect: 3.912m"
        rc = "R1 /VIN /OUT 1k\nC1 /OUT GND 1u"                 # tau 1 ms: 2 % after 3.912 ms
        r = self.run_(blk.format(stop="10m").replace("between 3.5m 4.3m", "between 3.5m and 4.3m"), rc)
        self.assertEqual(r[0]["status"], "pass", r[0]["detail"])
        self.assertIn("half time step", r[0]["detail"])
        r = self.run_(blk.format(stop="2m").replace("between 3.5m 4.3m", "between 3.5m and 4.3m"), rc)
        self.assertIn("has not settled by the end of the run", r[0]["detail"])

    def test_three_tau_window_is_not_settled(self):
        r = self.run_("scope: VIN, OUT, GND\nanalysis: tran 1u 300u\nsource: VIN = 0 pulse(0 1 0 1n 1n 1 2)\n"
                      "assert: settle(OUT, 2%) < 1m\nexpect: 391.2u", "R1 /VIN /OUT 100\nC1 /OUT GND 1u")   # tau 100 us
        self.assertIn("has not settled", r[0]["detail"])

    def test_model_from_mc_parts_fields(self):
        from forge import spice
        blk = spice.parse_block("## Simulation\nscope: VIN, A, GND\nsource: VIN = 5\n"
                                "assert: V(A) between 0.6 and 0.8\nexpect: 0.6925 ± 1%")
        cir, comps = circuit("R1 /VIN /A 1k\nD1 __D1 GND /A")     # D1 pin 1 = K on GND, pin 2 = A
        comps["D1"]["fields"] = {"Sim_Model": "D DX | .model DX D(Is=1e-14 N=1)", "Sim_Pins": "2 1"}
        with tempfile.TemporaryDirectory() as wd:
            r, deck, _ = spice.run_block(blk, cir, comps, NG, wd)
        self.assertEqual(r[0]["status"], "pass", r[0]["detail"])
        self.assertIn("D1 n_A 0 DX", deck)

    def test_nc_pin_and_inline_subckt_and_ic(self):
        from forge import spice
        blk = spice.parse_block("## Simulation\nscope: VIN, A, GND\nsource: VIN = 5\nassert: V(A) between 0.6 and 0.8\n"
                                "expect: 0.6925 ± 1%")
        cir, comps = circuit("R1 /VIN /A 1k\nU1 __U1 /A GND unconnected-(U1-NC-Pad3)")
        comps["U1"]["fields"] = {"Sim_Model": "D DX | .model DX D(Is=1e-14 N=1)", "Sim_Pins": "1 2"}
        with tempfile.TemporaryDirectory() as wd:
            r, deck, _ = spice.run_block(blk, cir, comps, NG, wd)
        self.assertEqual(r[0]["status"], "pass", r[0]["detail"])            # the NC pin does not stop the block
        blk = spice.parse_block("## Simulation\nscope: VIN, OUT, GND\nanalysis: tran 1u 1m\nsource: VIN = 0\n"
                                "assert: V(OUT) between 0.3 and 0.45 at 1m\nexpect: 0.3679")
        cir, comps = circuit("XU1 /VIN /OUT RCX\n.ic v(/OUT)=1\n.subckt RCX a b\nR1 a b 1k\n.ends\nC1 /OUT GND 1u")
        comps["U1"] = comps.pop("XU1")
        with tempfile.TemporaryDirectory() as wd:
            r, deck, _ = spice.run_block(blk, cir, comps, NG, wd)
        self.assertIn(".ic v(n_OUT)=1", deck)                               # renamed like every other node
        self.assertIn(".ends", deck)
        self.assertEqual(r[0]["status"], "pass", r[0]["detail"])            # 1 V decaying through 1 ms tau: e^-1

    def test_sources_unique_drawn_source_and_probing_a_source(self):
        r = self.run_("scope: +15V, -15V, MID, GND\nsource: +15V = 15\nsource: -15V = -15\n"
                      "assert: V(MID) between -0.01 and 0.01\nexpect: 0 ± 0.001",
                      "R1 /+15V /MID 1k\nR2 /MID /-15V 1k\nR3 /MID GND 1meg")
        self.assertEqual(r[0]["status"], "pass", r[0]["detail"])
        r = self.run_("scope: VIN, OUT, GND\nsource: VIN = 5\nassert: V(VIN) between 4.9 and 5.1\nexpect: 5", self.RC)
        self.assertIn("reads a net a source drives", r[0]["detail"])
        r = self.run_("scope: VIN, OUT, GND\nsource: VIN = 5\nassert: V(OUT) < 9\nexpect: 5",
                      self.RC + "\nV1 /VIN GND 5")
        self.assertIn("the sheet draws a source", r[0]["detail"])

    def test_log_patterns_and_units(self):
        from forge import spice
        for msg in ("Error on line 8", "ERROR: unknown subckt", "doAnalyses: TRAN:  Timestep too small",
                    "run simulation(s) aborted", "Warning: singular matrix:  check node n_x",
                    "Model issue on line 4 : unrecognized parameter (bf2) - ignored"):
            self.assertTrue(spice.LOG_BAD.search(msg), msg)
        self.assertEqual(spice.num("1Mhz"), 1e6)
        self.assertIn("dc 0.1", spice.source_line("V_S1_X", "n_X", "100 mV"))
        self.assertIn("sin(0.0 1.0 1000000.0)", spice.source_line("V_S1_X", "n_X", "0 sin(0 1 1Mhz)"))

    def test_grammar(self):
        from forge import spice
        for s, v in zip(("1M", "1meg", "1m5", "5.", "4u7", "10uF", "3.3V"), (1e6, 1e6, 1.5e-3, 5.0, 4.7e-6, 1e-5, 3.3)):
            self.assertAlmostEqual(spice.num(s), v, delta=abs(v) * 1e-12, msg=s)
        b = spice.parse_block("## Simulation\nassert: V(Net-(D1-A)) <=5\nexpect: 4\nassert: bad stuff\nexpect: 9\n"
                              "assert: V(X) between 6 and 5\nassert: V(Y) < 1\nexpect: 0")
        a = b["asserts"]
        self.assertEqual((a[0]["args"], a[0]["hi"], a[0]["hi_strict"], a[0]["expect"]), (["Net-(D1-A)"], 5, False, 4))
        self.assertIn("error", a[1])
        self.assertIn("lower bound is above", a[2]["error"])
        self.assertIn("absolute tolerance", a[3]["error"])
        r = self.run_("scope: VIN, OUT, GND\nsource: VIN = 0\nassert: gain(OUT, VIN) < 1", self.RC)
        self.assertIn("the input VIN is 0", r[0]["detail"])


class ResultsPanel(unittest.TestCase):
    """Results files, the side panel's feed, the HTML report and the V-cycle."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pe-forge-panel-")
        self.dir = new_project("Pnl", self.tmp)
        sys.path.insert(0, REPO)
        from forge import config, results
        self.prj, self.R = config.Project(self.dir), results

    def tearDown(self):
        shutil.rmtree(self.tmp, True)

    def sample(self, **kw):
        return dict({"id": "s1", "title": "Sample", "tool": "pwrlab buck", "grade": "ACCEPTED", "corners": ["36 V", "60 V"],
                     "metrics": [{"name": "Efficiency", "unit": "%", "values": [93.1, 91.4], "grade": "ACCEPTED"}],
                     "charts": [{"type": "line", "title": "Eff", "x": {"values": [0.1, 1.0]},
                                 "series": [{"name": "36 V", "values": [82, 93]}]},
                                {"type": "bars", "title": "Loss", "unit": "W", "items": [{"name": "Q1", "value": 0.4}]}]}, **kw)

    def test_validate_catches_shape_errors(self):
        self.assertEqual(self.R.validate(dict(self.sample(), schema=self.R.SCHEMA)), [])
        bad = dict(self.sample(grade="GOOD"), schema=self.R.SCHEMA)
        bad["metrics"][0]["values"] = [1]
        p = self.R.validate(bad)
        self.assertTrue(any("grade must be" in x for x in p))
        self.assertTrue(any("1 value(s) for 2 corner(s)" in x for x in p))
        with self.assertRaises(ValueError):
            self.R.write(self.prj, os.path.join(self.dir, "06_Simulations/Results/x.result.json"), bad)

    def test_panel_report_and_vcycle(self):
        self.R.write(self.prj, os.path.join(self.dir, "06_Simulations/Results/s1.result.json"), self.sample())
        d = json.loads(forge("panel", "--json", cwd=self.dir).stdout)
        self.assertTrue(d["ok"])
        self.assertEqual(d["results"][0]["title"], "Sample")
        self.assertEqual(d["vcycle"]["current"], "concept")
        self.assertEqual(len(d["vcycle"]["nodes"]), 10)
        self.assertEqual(d["vcycle"]["nodes"]["sysreq"]["pair"], "sysver")
        self.assertIn("Concept & needs", d["vcycle"]["nodes"]["concept"]["ask"])
        page = open(forge("report", cwd=self.dir).stdout.strip()).read()
        self.assertIn("<svg", page)
        self.assertIn("Sample", page)
        self.assertNotIn("http://", page.replace("http://www.w3.org", ""))      # offline: nothing fetched
        self.assertIn("Now: Concept & needs", forge("vcycle", cwd=self.dir).stdout)
        empty = tempfile.mkdtemp()
        self.assertFalse(json.loads(forge("panel", "--json", cwd=empty).stdout)["ok"])
        shutil.rmtree(empty)


class Vault(unittest.TestCase):
    """Every project is an Obsidian vault; the installer's project step."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pe-forge-vault-")
        sys.path.insert(0, REPO)
        from forge import vault
        self.V = vault

    def tearDown(self):
        shutil.rmtree(self.tmp, True)

    def test_new_project_is_a_vault(self):
        d = new_project("Vlt", self.tmp)
        app = json.load(open(os.path.join(d, ".obsidian/app.json")))
        self.assertFalse(app["useMarkdownLinks"])
        self.assertIn(".forge/", app["userIgnoreFilters"])
        self.assertIn(".obsidian/workspace*.json", open(os.path.join(d, ".gitignore")).read())

    def test_init_keeps_the_persons_settings_and_is_idempotent(self):
        d = os.path.join(self.tmp, "v")
        os.makedirs(os.path.join(d, ".obsidian"))
        json.dump({"useMarkdownLinks": True, "theme": "mine"}, open(os.path.join(d, ".obsidian/app.json"), "w"))
        self.V.init(d)
        app = json.load(open(os.path.join(d, ".obsidian/app.json")))
        self.assertTrue(app["useMarkdownLinks"])                # their choice wins
        self.assertEqual(app["theme"], "mine")
        self.assertEqual(self.V.init(d), [])                     # nothing left to write

    def test_register_and_fresh_folder_from_the_installer(self):
        d = os.path.join(self.tmp, "Fresh")
        os.makedirs(d)
        self.V.init(d)                                           # what install.sh leaves: a vault, nothing else
        if not self.V.obsidian_running():
            self.assertEqual(self.V.register(d), "registered")
            self.assertEqual(self.V.register(d), "known")
        ans = os.path.join(self.tmp, "a.json")
        json.dump({"name": "Fresh", "summary": "s", "owner": "T"}, open(ans, "w"))
        forge("new", d, "--answers", ans)                        # the intake builds the project right there
        self.assertTrue(os.path.exists(os.path.join(d, "project.toml")))
        busy = os.path.join(self.tmp, "Busy")
        os.makedirs(busy)
        open(os.path.join(busy, "notes.md"), "w").write("x")
        r = forge("new", busy, "--answers", ans, check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("is not empty", r.stdout + r.stderr)

    def test_installer_syntax_and_help(self):
        self.assertEqual(subprocess.run(["bash", "-n", os.path.join(REPO, "install.sh")]).returncode, 0)
        r = subprocess.run(["bash", os.path.join(REPO, "install.sh"), "--help"], capture_output=True, text=True)
        self.assertIn("--update", r.stdout)


def netlist_text(comps, nets):
    """A minimal kicadsexpr netlist: comps {ref: (value, mpn, mc_id)}, nets {name: (class, [(ref, pin, fn)])}."""
    c = "".join(f'\n\t\t(comp (ref "{r}") (value "{v}") (footprint "x:y")\n\t\t\t(fields (field (name "MPN") "{m}") '
                f'(field (name "MC_ID") "{i}")))' for r, (v, m, i) in comps.items())
    n = "".join(f'\n\t\t(net (code "{k}") (name "{name}") (class "{cls}")' +
                "".join(f' (node (ref "{r}") (pin "{p}") (pinfunction "{f}") (pintype "passive"))' for r, p, f in pins) + ")"
                for k, (name, (cls, pins)) in enumerate(nets.items(), 1))
    return f'(export (version "E")\n\t(components{c})\n\t(nets{n}))'


class Insulation(unittest.TestCase):
    """INS-1/2/3, NET-4 on synthetic netlists; CLS-2 through KiCad's DRC."""

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, REPO)
        from forge import insulation
        cls.I = insulation

    # an isolated gate driver U1: SELV side +5V / GND, HV side HV_DC_N and VCC2; R1 is a gate resistor on the HV side
    COMPS = {"U1": ("ISO", "ISO-1", "IC-9001"), "R1": ("10", "R10", "RES-1"), "C1": ("100n", "C100N", "CAP-1"),
             "Q1": ("SiC", "Q-1", "TRN-1")}

    def nets(self, **extra):
        n = {"+5V": ("Power", [("U1", "1", "VCC1"), ("C1", "1", "")]), "GND": ("Default", [("U1", "2", "GND1"), ("C1", "2", "")]),
             "/HV_DC_N": ("HV", [("U1", "3", "VEE2"), ("Q1", "2", "S")]),
             "/HV_DC_P": ("HV", [("Q1", "3", "D")]),
             "Net-(U1-OUT)": ("Default", [("U1", "4", "OUT"), ("R1", "1", "")]),
             "/HV_G": ("HV", [("R1", "2", ""), ("Q1", "1", "G")])}
        n.update(extra)
        return n

    def run_(self, nets, cfg=None, db=None, comps=None):
        parts, ns = self.I.parse(netlist_text(comps or self.COMPS, nets))
        cfg = {"barriers": {"ISO-1": "reinforced"}, **(cfg or {})}
        return self.I.analyse(parts, ns, cfg, db)

    def test_hv_side_net_without_hv_class_is_ins1(self):
        f, info = self.run_(self.nets())
        self.assertEqual([x["net"] for x in f["INS-1"]], ["Net-(U1-OUT)"])
        self.assertEqual(f["INS-2"], [])
        self.assertEqual(info["domains"], 2)

    def test_no_barrier_part_means_one_domain(self):
        f, _ = self.run_(self.nets(), cfg={"barriers": []})
        self.assertTrue(f["INS-2"])                      # +5V and GND now sit with HV: a path across

    def test_path_across_the_barrier_is_ins2_with_the_part(self):
        comps = dict(self.COMPS, R9=("1M", "R1M", "RES-2"))
        n = self.nets()
        n["GND"] = ("Default", n["GND"][1] + [("R9", "1", "")])
        n["/HV_DC_N"] = ("HV", n["/HV_DC_N"][1] + [("R9", "2", "")])
        f, _ = self.run_(n, comps=comps)
        hits = {x["net"]: x["actual"] for x in f["INS-2"]}
        self.assertIn("GND", hits)
        self.assertIn("via R9", hits["GND"])
        self.assertNotIn("GND", [x["net"] for x in f["INS-1"]])   # reported once, as the breach

    def test_allow_non_hv(self):
        f, _ = self.run_(self.nets(), cfg={"allow_non_hv": ["Net-(U1-OUT)"]})
        self.assertEqual(f["INS-1"], [])

    def test_db_iso_rating_marks_barrier_and_ins3_creepage(self):
        db = {"IC-9001": {"Iso_Rating": "reinforced 5 kVrms", "Iso_Creepage_mm": "8.0"}}
        f, info = self.run_(self.nets(), cfg={"barriers": [], "barrier_creepage_mm": 10.0}, db=db)
        self.assertIn("U1", info["barriers"])
        self.assertEqual(f["INS-2"], [])
        self.assertEqual(len(f["INS-3"]), 1)
        self.assertIn("8 mm", f["INS-3"][0]["actual"])
        f, _ = self.run_(self.nets(), cfg={"barriers": [], "barrier_creepage_mm": 10.0,
                                           "package_path": {"ISO-1": "slot + coat"}}, db=db)
        self.assertEqual(f["INS-3"], [])

    def test_net4_one_island_each(self):
        n = self.nets()
        n["/HV_DC_N"] = ("HV,HV_DCN", n["/HV_DC_N"][1])
        n["/HV_DC_P"] = ("HV,HV_DCP,HV_DCN", n["/HV_DC_P"][1])
        f, _ = self.run_(n, cfg={"islands": ["HV_DCN", "HV_DCP"]})
        got = {x["net"]: x["actual"] for x in f["NET-4"]}
        self.assertEqual(got, {"HV_DC_P": "in HV_DCP, HV_DCN", "HV_G": "in none"})

    @unittest.skipUnless(kicad_cli(), "kicad-cli not installed")
    def test_cls2_proves_the_dru_on_a_generated_board(self):
        sys.path.insert(0, REPO)
        from forge import kicad
        if not kicad.find_python():
            self.skipTest("KiCad's Python not found")
        tpl = os.path.join(REPO, "library", "template", "MC_Standard")
        dru, pro = os.path.join(tpl, "MC_Standard.kicad_dru"), os.path.join(tpl, "MC_Standard.kicad_pro")
        cfg = {"rule": [{"a": "HV", "b": "!HV", "clearance_mm": 2.5, "creepage_mm": 4.0},
                        {"a": "HV", "b": "!HV", "clearance_mm": 10.0}]}
        res = self.I.test_rules(dru, pro, cfg)
        # "!HV" is tested against every defined non-HV class of the template: Default and Power
        self.assertEqual([(c["kind"], c["b"], c["need"], ok) for c, ok, _, _ in res],
                         [("clearance", "!HV as Default", 2.5, True), ("clearance", "!HV as Power", 2.5, True),
                          ("creepage", "!HV as Default", 4.0, True), ("creepage", "!HV as Power", 4.0, True),
                          ("clearance", "!HV as Default", 10.0, False), ("clearance", "!HV as Power", 10.0, False)])
        self.assertFalse(any(w for *_, w in res[:4]))          # 0.5 mm wider than the rule: clean

    @unittest.skipUnless(kicad_cli(), "kicad-cli not installed")
    def test_cls2_rule_covering_only_default_misses_power_rails(self):
        sys.path.insert(0, REPO)
        from forge import kicad
        if not kicad.find_python():
            self.skipTest("KiCad's Python not found")
        tmp = tempfile.mkdtemp(prefix="pe-forge-cls2t-")
        self.addCleanup(shutil.rmtree, tmp, True)
        dru = os.path.join(tmp, "x.kicad_dru")
        open(dru, "w").write("(version 1)\n(rule \"HV to Default\"\n\t(condition \"A.hasNetclass('HV') && "
                             "B.hasNetclass('Default')\")\n\t(constraint clearance (min 4mm)))\n")
        pro = os.path.join(REPO, "library", "template", "MC_Standard", "MC_Standard.kicad_pro")
        res = self.I.test_rules(dru, pro, {"rule": [{"a": "HV", "b": "!HV", "clearance_mm": 4.0}]})
        self.assertEqual({c["b"]: ok for c, ok, _, _ in res}, {"!HV as Default": True, "!HV as Power": False})

    def test_basic_barrier_does_not_separate_selv(self):
        comps = dict(self.COMPS, CY=("10n", "Y2CAP", "CAP-9"))
        n = self.nets()
        n["/HV_DC_P"] = ("HV", n["/HV_DC_P"][1] + [("CY", "1", "")])
        n["GND"] = ("Default", n["GND"][1] + [("CY", "2", "")])
        db = {"CAP-9": {"Iso_Rating": "Y2 safety capacitor, 300 VAC"}}
        f, info = self.run_(n, comps=comps, db=db)
        self.assertEqual(info["barriers"]["CY"]["grade"], "basic")
        got = {x["net"]: x["actual"] for x in f["INS-2"]}
        self.assertIn("GND", got)
        self.assertIn("weaker barrier", got["GND"])
        self.assertIn("CY", got["GND"])
        self.assertEqual([x["net"] for x in f["INS-1"]], ["Net-(U1-OUT)"])   # the Y-cap still bounds the HV side

    def test_duplicate_reference_is_an_error_and_never_a_barrier(self):
        comps = dict(self.COMPS)
        n = self.nets()
        # a resistor that shares the isolator's reference, from HV_DC_N to GND
        text = netlist_text(comps, n).replace('(components', '(components\n\t\t(comp (ref "U1") (value "1M") '
                                                '(footprint "x:y")\n\t\t\t(fields (field (name "MPN") "R1M")))', 1)
        parts, ns = self.I.parse(text)
        ns["GND"]["pins"].append(("U1", "9", ""))
        parts["U1"]["nets"] += ["GND", "/HV_DC_N"]
        f, info = self.I.analyse(parts, ns, {"barriers": {"ISO-1": "reinforced"}}, None)
        self.assertEqual([x["ref"] for x in f["INS-0"]], ["U1"])
        self.assertEqual(f["INS-0"][0]["severity"], "error")
        self.assertNotIn("U1", info["barriers"])
        self.assertTrue(f["INS-2"])

    def test_unconnected_hv_pad_gets_its_own_fix(self):
        n = self.nets(**{"unconnected-(U1-NC-Pad5)": ("Default", [("U1", "5", "NC")])})
        n["/HV_DC_N"] = ("HV", n["/HV_DC_N"][1])
        comps = dict(self.COMPS, R2=("0", "R0", "RES-3"))
        n["unconnected-(U1-NC-Pad5)"] = ("Default", [("R2", "1", "")])
        n["/HV_DC_N"] = ("HV", n["/HV_DC_N"][1] + [("R2", "2", "")])
        f, _ = self.run_(n, comps=comps)
        fixes = {x["net"]: x["fix"] for x in f["INS-1"]}
        self.assertIn("unconnected pin", fixes["unconnected-(U1-NC-Pad5)"])

    def test_grade_parsing(self):
        g = self.I.grade_of
        self.assertEqual([g("reinforced 5.7 kVrms"), g("Y1 cap"), g("Y2 safety"), g("basic"), g("functional"), g("4 kVac")],
                         ["reinforced", "reinforced", "basic", "basic", "functional", None])

    @unittest.skipUnless(kicad_cli(), "kicad-cli not installed")
    def test_check_and_gate_on_a_real_sheet(self):
        tmp = tempfile.mkdtemp(prefix="pe-forge-ins-")
        self.addCleanup(shutil.rmtree, tmp, True)
        d = new_project("Hv", tmp)
        shutil.copy(os.path.join(KFIX, "hv_buck.kicad_sch"), os.path.join(d, "08_PCB/hv/hv.kicad_sch"))
        pro = os.path.join(d, "08_PCB/hv/hv.kicad_pro")
        j = json.load(open(pro))
        j["net_settings"]["netclass_patterns"].append({"netclass": "HV", "pattern": "*VIN"})
        json.dump(j, open(pro, "w"), indent=2)
        r = json.loads(forge("check", "--only", "insulation", "--json", cwd=d, check=False).stdout)
        c = r["checks"]["insulation"]
        self.assertEqual(c["status"], "fail")                 # no isolator: GND joined to VIN
        claims = " ".join(f["claim"] for f in c["findings"])
        self.assertIn("INS-2", claims)
        self.assertIn("NET-4 runs", claims)                   # not configured is said, not silent
        self.assertIn("INS-3 runs", claims)
        sys.path.insert(0, REPO)
        from forge import config, gates
        items = gates.check(config.Project(d), "G6")
        ins = [x for x in items if x[1].startswith("insulation")]
        self.assertTrue(ins and not ins[0][0], items)
        # the project says it has HV, but the pattern is gone: a fail, not a skip
        j["net_settings"]["netclass_patterns"].pop()
        json.dump(j, open(pro, "w"), indent=2)
        open(os.path.join(d, "project.toml"), "a").write("\n[insulation]\nbarrier_creepage_mm = 4.0\n")
        r = json.loads(forge("check", "--only", "insulation", "--json", cwd=d, check=False).stdout)
        self.assertEqual(r["checks"]["insulation"]["status"], "fail")
        self.assertIn("no net is in class HV", r["checks"]["insulation"]["findings"][0]["actual"])


class PcbSync(unittest.TestCase):
    """forge pcb sync: headless Update PCB from Schematic; schematic parity must come out clean."""

    def test_field_rename_and_parse(self):
        sys.path.insert(0, REPO)
        from forge import pcb
        text = netlist_text({"R1": ("10k", "R10K", "RES-1")}, {"/A": ("Default", [("R1", "1", "")])})
        text = text.replace('(field (name "MC_ID")', '(field (name "Sim_Pins") "1 2") (field (name "MC_ID")')
        c = pcb.components(text)[0]
        self.assertEqual(c["pads"], {"1": "/A"})
        self.assertIn("Sim.Pins", c["fields"])            # KiCad's rename on load, or parity fails
        self.assertNotIn("Sim_Pins", c["fields"])

    @unittest.skipUnless(kicad_cli(), "kicad-cli not installed")
    def test_sync_gives_zero_parity_and_is_idempotent(self):
        sys.path.insert(0, REPO)
        from forge import kicad
        if not kicad.find_python():
            self.skipTest("KiCad's Python not found")
        tmp = tempfile.mkdtemp(prefix="pe-forge-sync-")
        self.addCleanup(shutil.rmtree, tmp, True)
        d = new_project("Sy", tmp)
        shutil.copy(os.path.join(KFIX, "hv_buck.kicad_sch"), os.path.join(d, "08_PCB/sy/sy.kicad_sch"))
        r = forge("pcb", "sync", cwd=d, check=False)
        self.assertIn("added:", r.stdout)
        self.assertIn("DRC schematic parity: 0 issue(s)", r.stdout, r.stdout + r.stderr)
        r = forge("pcb", "sync", cwd=d, check=False)
        self.assertNotIn("added:", r.stdout)                 # second run updates in place
        self.assertIn("parity: 0 issue(s)", r.stdout)

    def test_newest_kicad_prefs_by_version_number(self):
        sys.path.insert(0, REPO)
        from forge import pcb
        tmp = tempfile.mkdtemp(prefix="pe-forge-prefs-")
        self.addCleanup(shutil.rmtree, tmp, True)
        for v in ("9.0", "10.0"):
            os.makedirs(os.path.join(tmp, "kicad", v))
        old = pcb.glob_kicad_prefs
        try:
            pcb.glob_kicad_prefs = lambda: sorted([os.path.join(tmp, "kicad", v) for v in ("9.0", "10.0")],
                                                  key=lambda p: tuple(int(x) for x in os.path.basename(p).split(".")))
            open(os.path.join(tmp, "kicad", "10.0", "fp-lib-table"), "w").write(
                '(fp_lib_table\n  (lib (name "NEWLIB") (type "KiCad") (uri "/x/NEWLIB.pretty") (options "") (descr ""))\n)\n')
            open(os.path.join(tmp, "kicad", "9.0", "fp-lib-table"), "w").write(
                '(fp_lib_table\n  (lib (name "OLDLIB") (type "KiCad") (uri "/x/OLDLIB.pretty") (options "") (descr ""))\n)\n')
            d = pcb.lib_dirs()
            self.assertIn("NEWLIB", d)                   # 10.0 is newer than 9.0 (numbers, not text)
            self.assertNotIn("OLDLIB", d)
        finally:
            pcb.glob_kicad_prefs = old

    def test_copper_findings_rule12(self):
        sys.path.insert(0, REPO)
        from forge import pcb

        class P:
            cfg = {"pcb": {}}
        rep = {"board_mm2": 2500.0, "gnd_vias": 14, "layers": {
            "F.Cu": {"copper_pct": 22.4, "gnd_pct": 10.4, "gnd_pieces": 3},
            "In1.Cu": {"copper_pct": 86.0, "gnd_pct": 86.0, "gnd_pieces": 1},
            "B.Cu": {"copper_pct": 84.0, "gnd_pct": 84.0, "gnd_pieces": 1}}}
        claims = " | ".join(c for c, _, _ in pcb.copper_findings(P, rep))
        self.assertIn("F.Cu copper coverage", claims)              # the empty top layer is flagged
        self.assertIn("GND stitching", claims)
        rep["layers"]["F.Cu"]["copper_pct"], rep["gnd_vias"] = 78.4, 68
        rep["layers"]["In1.Cu"]["gnd_pieces"] = 2
        claims = " | ".join(c for c, _, _ in pcb.copper_findings(P, rep))
        self.assertEqual(claims, "In1.Cu reference plane is one piece")   # a split reference plane is flagged
        rep["layers"]["In1.Cu"]["gnd_pieces"] = 1
        rep["solid_pads"] = [{"pad": "J1.2", "th": True, "zone": "forge:fill", "local": False},
                             {"pad": "U1.9", "th": False, "zone": "forge:fill", "local": True}]
        claims = " | ".join(c for c, _, _ in pcb.copper_findings(P, rep))
        self.assertIn("J1.2 joins forge:fill through thermal relief", claims)   # solid TH pad without a reason
        self.assertNotIn("U1.9", claims)                                        # datasheet solid pad (placement.json)

    def test_connector_findings_rule13(self):
        sys.path.insert(0, REPO)
        from forge import pcb

        class P:
            cfg = {"pcb": {}}
        ok = {"ref": "J1", "declared": True, "side": "left", "overhang": 0, "source": "ds p5", "inboard": 0.0,
              "outward": True, "blocked_by": [], "datasheet_face": True}
        self.assertEqual(pcb.connector_findings(P, {"connectors": [ok]}), [])
        bad = dict(ok, inboard=0.95, outward=False, blocked_by=["TP5"], datasheet_face=False)
        claims = " | ".join(c for c, _, _ in pcb.connector_findings(P, {"connectors": [bad, {"ref": "J2", "declared": False, "near": 0.7}]}))
        for s in ("J1 mating face on the left edge flush", "opening (plug / wire entry) faces", "corridor",
                  "from the datasheet drawing", "J2 at the board edge is declared"):
            self.assertIn(s, claims)

    def test_xtalk_rule14(self):
        sys.path.insert(0, REPO)
        from forge import pcb

        class P:
            cfg = {"pcb": {"layer_direction": {"In2.Cu": "H", "B.Cu": "V"}}}
        seg = lambda net, a, b_, w=0.25: {"net": net, "w": w, "a": a, "b": b_}
        rep = {"layers": [
            {"name": "F.Cu", "fill_pct": 70, "segs": []},
            {"name": "In1.Cu", "fill_pct": 90, "segs": []},
            {"name": "In2.Cu", "fill_pct": 50, "segs": [seg("EN", [0, 10], [20, 10])]},
            {"name": "B.Cu", "fill_pct": 85, "segs": [seg("EN", [0, 10], [0, 40]), seg("PWR", [5, 0], [5, 30], w=1.0)]}]}
        stats, pairs, F = pcb.xtalk_analyse(P, rep)
        self.assertEqual(F, [])                                       # L3 H / L4 V, power track not a signal
        self.assertTrue(stats[1]["plane"])
        self.assertEqual(pairs, [{"a": "In2.Cu", "b": "B.Cu", "shielded": False}])
        rep["layers"][3]["segs"].append(seg("FB", [2, 10.4], [18, 10.4]))   # parallel under EN, 0.4 mm away
        stats, pairs, F = pcb.xtalk_analyse(P, rep)
        claims = " | ".join(c for c, _, _ in F)
        self.assertIn("B.Cu signal routing mostly vertical", claims)
        self.assertIn("no broadside parallel signal runs between In2.Cu and B.Cu", claims)

    def test_review_form_has_project_rules(self):
        sys.path.insert(0, REPO)
        from forge import pcb
        self.assertEqual([i for i, _ in pcb.PROJECT_RULES], ["R-11", "R-12", "R-13", "R-14", "R-15"])

    def test_hp_rulebook_catalogue_and_profile(self):
        sys.path.insert(0, REPO)
        from forge import hp
        cat = hp.catalogue()
        self.assertGreaterEqual(len(cat), 120)                       # parsed from the book, one source of truth
        ids = {r["id"] for r in cat}
        for i in ("STK-01", "PL-06", "GD-08", "SW-05", "GND-02", "SNS-01", "FLT-03", "TH-02", "TX-04", "HC-11", "HV-01"):
            self.assertIn(i, ids)

        class P:
            cfg = {"specs": {"vin_V": [36, 63]}, "hp": {}}
        prof = hp.profile(P)
        self.assertTrue(prof["enabled"])                               # >= 50 V turns it on by default
        rule = {r["id"]: r for r in cat}
        self.assertFalse(hp.applies(rule["GD-01"], prof)[0])           # no gate driver on the board
        self.assertTrue(hp.applies(rule["SW-05"], prof)[0])
        P.cfg["hp"] = {"gate_driver": True}
        self.assertTrue(hp.applies(rule["GD-01"], hp.profile(P))[0])
        P.cfg = {"specs": {"vin_V": [10, 14], "iout_A": 2}, "hp": {}}
        self.assertFalse(hp.profile(P)["enabled"])

    def test_hp_auto_and_waivers(self):
        sys.path.insert(0, REPO)
        from forge import hp

        class P:
            cfg = {"specs": {"vin_V": 63}, "pcb": {"copper_oz": [2, 1, 1, 2], "copper_finish": "ENIG"},
                   "hp": {"waive": {"SW-05": "50 mm board (owner OK 2026-11-08)", "SW-02": "TP4 is through-hole"}},
                   "insulation": {}}
        g = {"sw": {"F.Cu": 24.0, "B.Cu": 3.1}, "sw_edge_mm": 2.6, "sw_connector_mm": [("J2", 20.0)],
             "sw_overlap": {"/FB@B.Cu": 3.0}, "exposed_pads": [{"pad": "U1.9", "area": 14.5, "vias": 12, "pitch": 1.1}],
             "via_in_pad": 12, "notes": ["FAB: Cu 2/1/1/2 oz"], "cm_copper": {}, "pads": {}}
        A = hp.auto(P, g, hp.profile(P))
        self.assertFalse(A["SW-02"][0]); self.assertFalse(A["SW-05"][0]); self.assertFalse(A["GD-08"][0])
        self.assertFalse(A["TH-03"][0])                                # vias in pads without the IPC-4761 note
        self.assertTrue(A["TH-02"][0]); self.assertTrue(A["STK-05"][0]); self.assertFalse(A["HV-01"][0])
        rows = {r["id"]: r for r in hp.evaluate(P, None, with_geometry=False)[1]}
        self.assertEqual(rows["SW-05"]["status"], "WAIVED")             # Must waived with the owner's OK
        self.assertEqual(rows["SW-02"]["status"], "WAIVER INVALID")     # Must waived without it

    def test_stackup_block_thickness(self):
        sys.path.insert(0, REPO)
        from forge import pcb
        blk = pcb.stackup_block([2, 1, 1, 2], 1.6)
        th = sum(float(x) for x in re.findall(r"\(thickness ([\d.]+)\)", blk))
        self.assertAlmostEqual(th, 1.6, places=2)
        self.assertEqual(blk.count('(type "copper")'), 4)
        self.assertIn('(layer "F.Cu" (type "copper") (thickness 0.07))', blk)

    @unittest.skipUnless(kicad_cli(), "kicad-cli not installed")
    def test_setup_and_place(self):
        sys.path.insert(0, REPO)
        from forge import kicad
        if not kicad.find_python():
            self.skipTest("KiCad's Python not found")
        tmp = tempfile.mkdtemp(prefix="pe-forge-place-")
        self.addCleanup(shutil.rmtree, tmp, True)
        d = new_project("Pl", tmp)
        shutil.copy(os.path.join(KFIX, "hv_buck.kicad_sch"), os.path.join(d, "08_PCB/pl/pl.kicad_sch"))
        open(os.path.join(d, "project.toml"), "a").write(
            "\n[pcb]\noutline_mm = [40, 30]\ncopper_oz = [2, 1, 1, 2]\nhole_inset_mm = 3.5\n")
        forge("pcb", "sync", cwd=d)
        r = forge("pcb", "setup", cwd=d)
        self.assertIn("40 x 30 mm, 4 copper layers (2/1/1/2 oz)", r.stdout)
        board = open(os.path.join(d, "08_PCB/pl/pl.kicad_pcb")).read()
        self.assertEqual(board.count("(stackup"), 1)
        forge("pcb", "setup", cwd=d)                       # re-run replaces, never stacks a second block
        self.assertEqual(open(os.path.join(d, "08_PCB/pl/pl.kicad_pcb")).read().count("(stackup"), 1)
        board_t = open(os.path.join(d, "08_PCB/pl/pl.kicad_pcb")).read()
        self.assertEqual(board_t.count('"forge-keepout:H'), 4)   # copper keep-out at each mounting hole
        zones = os.path.join(d, "zones.json")
        json.dump([{"name": "gnd", "layer": "B.Cu", "net": "GND", "rect": [0.5, 0.5, 39.5, 29.5]}], open(zones, "w"))
        forge("pcb", "zones", zones, cwd=d, check=False)
        board_t = open(os.path.join(d, "08_PCB/pl/pl.kicad_pcb")).read()
        self.assertEqual(board_t.count('"forge-keepout:H'), 4)   # a pours run must not remove them
        self.assertEqual(board_t.count('"forge:gnd"'), 1)
        json.dump({"zones": [{"name": "gnd", "layer": "B.Cu", "net": "GND", "rect": [0.5, 0.5, 39.5, 29.5]},
                             {"name": "gndt", "layer": "F.Cu", "net": "GND", "rect": [0.5, 0.5, 39.5, 29.5]},
                             {"name": "gnd2", "layer": "In1.Cu", "net": "GND", "rect": [0.5, 0.5, 39.5, 29.5]},
                             {"name": "gnd3", "layer": "In2.Cu", "net": "GND", "rect": [0.5, 0.5, 39.5, 29.5]}],
                   "stitch": {"net": "GND", "pitch": 4.0}}, open(zones, "w"))
        r = forge("pcb", "zones", zones, cwd=d, check=False)
        self.assertRegex(r.stdout, r"\d+ stitching via")            # stitched where GND covers every layer
        self.assertNotIn("clearance", r.stdout)                    # no stitching via breaks a clearance
        board_t = open(os.path.join(d, "08_PCB", "t", "t.kicad_pcb")).read() if os.path.exists(os.path.join(d, "08_PCB", "t", "t.kicad_pcb")) else None
        r = forge("pcb", "copper", cwd=d, check=False)
        self.assertNotIn("joins forge:gnd", r.stdout)             # pours default to thermal relief (Rule 12)
        table = os.path.join(d, "placement.json")
        json.dump({"U1": [20, 15, 0], "R1": [20, 15, 0]}, open(table, "w"))   # R1 on top of U1
        r = forge("pcb", "place", table, cwd=d, check=False)
        self.assertEqual(r.returncode, 1)
        self.assertIn("courtyard overlap", r.stdout)
        r = forge("pcb", "sync", cwd=d)
        self.assertIn("parity: 0 issue(s)", r.stdout)       # holes are marked board-only
        pro = os.path.join(d, "08_PCB/pl/pl.kicad_pro")
        j = json.load(open(pro))
        j["net_settings"]["classes"].append(dict(j["net_settings"]["classes"][0], name="ES2", clearance=0.6))
        json.dump(j, open(pro, "w"), indent=2)
        forge("pcb", "setup", cwd=d)
        forge("pcb", "sync", cwd=d)
        names = [c["name"] for c in json.load(open(pro))["net_settings"]["classes"]]
        self.assertIn("ES2", names)                           # KiCad's save must not reset the owner's classes

    @unittest.skipUnless(kicad_cli(), "kicad-cli not installed")
    def test_review_against_every_rule_and_board_hash(self):
        tmp = tempfile.mkdtemp(prefix="pe-forge-rev-")
        self.addCleanup(shutil.rmtree, tmp, True)
        d = new_project("Rv", tmp)
        os.makedirs(os.path.join(d, "04_Hardware/Layout"), exist_ok=True)
        open(os.path.join(d, "04_Hardware/Layout/Layout_Spec.md"), "w").write(
            "---\nrev: 0.1\n---\n| # | Rule |\n|---|---|\n| L-01 | cap at VIN |\n| L-02 | GND plane |\n")
        r = forge("pcb", "review-status", cwd=d, check=False)
        self.assertEqual(r.returncode, 1)
        self.assertIn("no independent PCB review yet", r.stdout)
        form = os.path.join(d, forge("pcb", "review-new", "placement", cwd=d).stdout.strip())
        t = open(form).read()
        self.assertIn("| L-01 |", t)
        self.assertIn("board_sha:", t)
        r = forge("pcb", "review-status", cwd=d, check=False)
        self.assertIn("rules without a verdict: L-01, L-02, R-11, R-12, R-13, R-14, R-15", r.stdout)   # project rules enforced
        self.assertIn("| R-14 |", t)
        import re as _re
        t = _re.sub(r"^\| (R-1[1-5]) \| ([^|]*) \| \| ([^|]*)\|$", r"| \1 | \2 | PASS | \3|", t, flags=_re.M)
        t = t.replace("| L-01 | cap at VIN | | |", "| L-01 | cap at VIN | PASS | C4 1.0 mm from pin 3 |")
        t = t.replace("| L-02 | GND plane | | |", "| L-02 | GND plane | FAIL | L2 split under U1 |")
        open(form, "w").write(t.replace("verdict: OPEN", "verdict: READY"))
        r = forge("pcb", "review-status", cwd=d, check=False)
        self.assertIn("rules FAIL: L-02", r.stdout)
        t = open(form).read()
        open(form, "w").write(t.replace("| FAIL | L2 split under U1 |", "| PASS | solid |").replace("verdict: READY", "verdict: READY WITH FIXES"))
        r = forge("pcb", "review-status", cwd=d, check=False)
        self.assertIn("fix the findings and review again", r.stdout)   # with fixes is not a pass
        t = open(form).read()
        open(form, "w").write(t.replace("verdict: READY WITH FIXES", "verdict: READY"))
        r = forge("pcb", "review-status", cwd=d, check=False)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        board = os.path.join(d, "08_PCB/rv/rv.kicad_pcb")
        open(board, "a").write("\n")                       # any later board change makes the review stale
        r = forge("pcb", "review-status", cwd=d, check=False)
        self.assertIn("the board changed after the review", r.stdout)


class Protection(unittest.TestCase):
    """PROT-1..4: surge clamp sized against the system and every protected pin's absolute maximum."""

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, REPO)
        from forge import insulation, protection
        cls.I, cls.P = insulation, protection

    def run_(self, clamp, absmax, cfg):
        comps = {"D2": ("TVS", "TVS-1", "PRT-1"), "U1": ("BUCK", "IC-1", "IC-1"), "C1": ("1u", "C1U", "CAP-1")}
        nets = {"/VIN": ("Default", [("D2", "1", "K"), ("U1", "3", "VIN"), ("C1", "1", "")]),
                "GND": ("Default", [("D2", "2", "A"), ("U1", "1", "GND"), ("C1", "2", "")])}
        parts, ns = self.I.parse(netlist_text(comps, nets))
        db = {"PRT-1": {"Clamp_V": clamp}, "IC-1": {"Abs_Max_V": absmax}}
        return self.P.analyse(parts, ns, cfg, db, {"vin_V": [36, 63]})

    def test_clamp_over_abs_max_is_prot2_and_infeasible_is_prot3(self):
        f, info = self.run_("VRWM=64; VBR_max=78.6; VC=103@5.9A", "VIN=85; EN=VIN+0.3", {"margin": 0.9})
        self.assertEqual(info["v_operating_max"], 63)          # from [specs] vin_V when not set
        self.assertEqual(len(f["PROT-2"]), 1)
        self.assertIn("103.0 V", f["PROT-2"][0]["actual"])
        self.assertIn("76.5 V", f["PROT-2"][0]["claim"])
        self.assertEqual(len(f["PROT-3"]), 1)                  # 76.5 / 63 = 1.21: no standard TVS fits

    def test_good_sizing_passes(self):
        f, _ = self.run_("VRWM=64; VBR_max=78.6; VC=103@5.9A", "VIN=120", {"margin": 0.9})
        self.assertEqual(f["PROT-2"] + f["PROT-3"] + f["PROT-1"], [])

    def test_standoff_below_operating_is_prot1(self):
        f, _ = self.run_("VRWM=58; VBR_max=71; VC=93.6@6.4A", "VIN=120", {"v_operating_max": 63})
        self.assertEqual(len(f["PROT-1"]), 1)

    def test_surge_current_interpolates_and_missing_data_is_reported(self):
        cd = self.P.clamp_data("VRWM=64; VBR_max=78.6; VC=103@5.9A; VC2=133.9@29.5A")
        self.assertAlmostEqual(self.P.v_clamp(cd, 0.0), 78.6)
        self.assertAlmostEqual(self.P.v_clamp(cd, 5.9), 103.0)
        self.assertTrue(103.0 < self.P.v_clamp(cd, 10.0) < 133.9)
        f, info = self.run_("VRWM=64; VBR_max=78.6; VC=103@5.9A", "", {"margin": 0.9})
        self.assertEqual([x["ref"] for x in f["PROT-4"]], ["U1"])   # no Abs_Max_V: not checked, never a pass
        self.assertEqual(f["PROT-2"], [])


class Learn(unittest.TestCase):
    """forge learn / feedback / issue: local learning, one-step issues, redaction, opt-out."""

    def setUp(self):
        self.cfg = tempfile.mkdtemp(prefix="pe-forge-learn-")
        self.env = dict(ENV, PE_FORGE_CONFIG_DIR=self.cfg, PE_FORGE_NO_BROWSER="1")
        self.cwd = tempfile.mkdtemp(prefix="pe-forge-nowhere-")

    def run_forge(self, *args, env=None, stdin=None):
        return subprocess.run(FORGE + list(args), cwd=self.cwd, capture_output=True, text=True,
                              env=env or self.env, input=stdin)

    def fake_gh(self, auth_ok=True):
        d = tempfile.mkdtemp(prefix="fake-gh-")
        log = os.path.join(d, "calls.txt")
        open(os.path.join(d, "gh"), "w").write(
            "#!/bin/sh\n"
            f'echo "$@" >> "{log}"\n'
            'if [ "$1" = "auth" ]; then exit ' + ("0" if auth_ok else "1") + "; fi\n"
            'if [ "$1" = "issue" ]; then echo https://github.com/me/fb/issues/7; exit 0; fi\n')
        os.chmod(os.path.join(d, "gh"), 0o755)
        return d, log

    def test_crash_is_saved_and_reported_in_one_step(self):
        env = dict(self.env, PE_FORGE_TEST_CRASH="doctor")
        r = self.run_forge("doctor", env=env)
        self.assertEqual(r.returncode, 2)
        self.assertIn("forge issue --last", r.stderr)                    # the one-step hint
        err = json.load(open(os.path.join(self.cfg, "last_error.json")))
        self.assertEqual((err["command"], err["type"]), ("doctor", "RuntimeError"))
        r = self.run_forge("issue", "--last", "--dry-run")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Title: [bug] doctor: RuntimeError: test crash", r.stdout)
        self.assertIn('File "pe-forge/forge/__main__.py"', r.stdout)     # the trace inside pe-forge, readable
        self.assertNotIn(os.path.expanduser("~"), r.stdout)               # no home path
        self.assertIn("issues/new?title=%5Bbug%5D", r.stdout)

    def test_issue_filed_with_gh(self):
        d, log = self.fake_gh(auth_ok=True)
        env = dict(self.env, PATH=d + os.pathsep + self.env["PATH"])
        r = self.run_forge("issue", "--kind", "rule", "--rule", "SW-05", "--note", "50 mm board, see /VIN",
                           "--repo", "me/fb", "--yes", env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Filed: https://github.com/me/fb/issues/7", r.stdout)
        calls = open(log).read()
        self.assertIn("issue create --repo me/fb --title [rule] SW-05 does not fit this board", calls)
        self.assertIn("<net>", r.stdout)                                  # net names redacted
        self.assertNotIn("/VIN", r.stdout)

    def test_issue_falls_back_to_browser_and_needs_a_yes(self):
        d, log = self.fake_gh(auth_ok=False)
        env = dict(self.env, PATH=d + os.pathsep + self.env["PATH"])
        r = self.run_forge("issue", "--kind", "idea", "--note", "panel button", "--repo", "me/fb", env=env)
        self.assertEqual(r.returncode, 1)                                 # not a terminal and no --yes: nothing filed
        self.assertIn("add --yes", r.stdout)
        r = self.run_forge("issue", "--kind", "idea", "--note", "panel button", "--repo", "me/fb", "--yes", env=env)
        self.assertIn("Opened the pre-filled issue page", r.stdout)
        self.assertIn("https://github.com/me/fb/issues/new?title=%5Bidea%5D+panel+button", r.stdout)
        self.assertNotIn("issue create", open(log).read())               # gh not logged in: never used

    def test_learning_log_summary_and_opt_out(self):
        sys.path.insert(0, REPO)
        os.environ["PE_FORGE_CONFIG_DIR"] = self.cfg
        from forge import learn
        root = tempfile.mkdtemp(prefix="pe-forge-prj-")
        open(os.path.join(root, "project.toml"), "w").write('[project]\nname = "Acme Inverter"\n')
        learn.record("rule", root, rule="SW-05", strength="Must", status="WAIVED", reason="small board (owner OK)")
        learn.record("rule", root, rule="SW-02", strength="Must", status="AUTO FAIL", reason="")
        learn.record("gate", root, gate="G7", ok=False, failed=2)
        s = learn.summary(learn.events(root))
        self.assertEqual(s["waivers"], {"SW-05": "small board (owner OK)"})
        self.assertEqual(s["failing_rules"], {"SW-02": 1})
        self.assertEqual(s["gates"]["G7"], {"runs": 1, "passed": 0})
        form = os.path.join(root, "PCB_routing_x.md")
        open(form, "w").write("| R-12 | x | FAIL | y |\n## Other findings\n### Major\n- **M-1: TP4** ...\n"
                              "- **m-34 (new)** EN ...\n| m-13 | OPEN, raised | ... |\nNo Critical findings.\n")

        class P:
            pass
        P.root = root
        learn.record_review(P, form, "READY WITH FIXES")
        learn.record_review(P, form, "READY WITH FIXES")                  # recorded once per form and verdict
        rv = [e for e in learn.events(root) if e["kind"] == "review"]
        self.assertEqual(len(rv), 1)
        self.assertEqual(rv[0]["escaped"], {"Critical": 0, "Major": 1, "Minor": 1})
        self.assertEqual(rv[0]["failed_rules"], ["R-12"])
        self.assertIn("<project>", learn.redact("Acme Inverter fails at /SW", "Acme Inverter"))
        os.environ["PE_FORGE_LEARN"] = "0"
        try:
            learn.record("gate", root, gate="G8", ok=True, failed=0)
        finally:
            del os.environ["PE_FORGE_LEARN"]
        self.assertFalse(any(e.get("gate") == "G8" for e in learn.events(root)))   # opted out: nothing recorded

    def test_feedback_is_shown_and_never_sent(self):
        r = self.run_forge("feedback", "--note", "the rule book is great")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("## pe-forge feedback", r.stdout)
        self.assertIn("Nothing was sent", r.stdout)
        r = self.run_forge("learn", "off")
        self.assertIn("learning off", r.stdout)
        self.assertFalse(json.load(open(os.path.join(self.cfg, "config.json")))["learn"])


class VaultOpen(unittest.TestCase):
    def test_opens_its_own_vault_by_id_never_an_enclosing_one(self):
        sys.path.insert(0, REPO)
        from forge import vault
        home = tempfile.mkdtemp(prefix="home-")
        prj = os.path.join(home, "work", "Buck")
        os.makedirs(prj)
        reg = os.path.join(tempfile.mkdtemp(), "obsidian.json")
        json.dump({"vaults": {"aaaa": {"path": home, "open": True}, "bbbb": {"path": prj}}}, open(reg, "w"))
        old = os.environ.get("PE_FORGE_OBSIDIAN_REGISTRY")
        os.environ["PE_FORGE_OBSIDIAN_REGISTRY"] = reg
        try:
            self.assertEqual(vault.vault_id(prj), "bbbb")
            self.assertEqual(vault.enclosing_vaults(prj), [os.path.realpath(home)])
            url = vault.open_url(prj, "04_Hardware/Layout/Layout_Spec.md")
            self.assertTrue(url.startswith("obsidian://open?vault=bbbb&file=04_Hardware"))
            self.assertNotIn("path=", url)                    # by path Obsidian would pick the home-folder vault
            self.assertIsNone(vault.open_url(os.path.join(home, "other")))
        finally:
            if old is None:
                del os.environ["PE_FORGE_OBSIDIAN_REGISTRY"]
            else:
                os.environ["PE_FORGE_OBSIDIAN_REGISTRY"] = old


if __name__ == "__main__":
    unittest.main(verbosity=2)
