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


if __name__ == "__main__":
    unittest.main(verbosity=2)
