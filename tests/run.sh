#!/bin/sh
# pe-forge tests: the forge engine + schematic engine (fast); pass --pwrlab to also run pwrlab's LTspice suite (minutes).
here=$(cd "$(dirname "$0")/.." && pwd)
python3 -W ignore "$here/tests/test_forge.py" || exit 1
if [ "$1" = "--pwrlab" ]; then (cd "$here/vendor/pwrlab" && "$here/.venv/bin/python" -m pytest -q tests); fi
