#!/bin/bash
# Every test in one command. Exits non-zero on any failure.
# Each suite runs ONCE: its output is captured and the exit code taken from the
# same run (an earlier version ran everything twice, doubling an already slow
# suite because LTspice runs are seconds, not milliseconds).
cd "$(dirname "$0")/.."
PY=.venv/bin/python
fail=0

stage() {             # stage <label> <command...>
  echo "### $1"; shift
  out=$("$@" 2>&1); rc=$?
  echo "$out" | tail -"${TAIL:-4}"
  [ $rc -ne 0 ] && fail=1
  echo
}

TAIL=3  stage "1. toolchain doctor"                   $PY -m pwrlab.cli doctor
TAIL=3  stage "2. regression fault injection"         $PY tests/test_catches_regressions.py
TAIL=9  stage "3. analytical agreement"               $PY tests/test_accuracy.py
TAIL=3  stage "4. line-frequency topologies"          $PY tests/test_line_topologies.py
TAIL=3  stage "5. scenarios: tolerance/loss/faults"   $PY tests/test_scenarios.py
TAIL=4  stage "5b. datasheet device models"            $PY tests/test_devices.py
TAIL=4  stage "5c. device selection + thermal"         $PY tests/test_selection.py
TAIL=4  stage "6. loss model vs published benchmark"   $PY tests/test_loss_benchmark.py
TAIL=3  stage "7. MCP server (over the real protocol)" $PY tests/test_mcp.py
TAIL=6  stage "6b. provenance + JSON boundary"        $PY tests/test_provenance.py
TAIL=8  stage "6c. adversarial: can it be made to lie" $PY tests/test_adversarial.py
TAIL=9  stage "6d. benchmark vs published hardware"   $PY tests/test_benchmarks.py --quick
TAIL=9  stage "6e. benchmark: three-level T-type/NPC" $PY tests/test_benchmark_threelevel.py --quick
TAIL=3  stage "7b. T-type schematic == netlist"        $PY verify_ttype_asc.py
TAIL=2  stage "8. original deliverables"              ./verify.sh

# The suites run hundreds of simulations; each .raw is megabytes. Leave the
# scratch directory bounded rather than half a gigabyte heavier than we found it.
$PY -m pwrlab.cli clean --keep-recent 2 --max-mb 100 2>/dev/null | tail -2

[ $fail = 0 ] && echo "ALL TESTS PASSED" || echo "*** FAILURES ***"
exit $fail
