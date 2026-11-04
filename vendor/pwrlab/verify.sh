#!/bin/bash
# One-command independent verification of the whole workflow.
#   ./verify.sh
# Exits non-zero if any check fails.  Re-runs every simulation from scratch;
# nothing is taken from cached results.
set -u
cd "$(dirname "$0")"
PY=.venv/bin/python
fail=0
chk() { if [ "$1" = "0" ]; then echo "  PASS  $2"; else echo "  FAIL  $2"; fail=1; fi; }

echo "=============================================================="
echo " 1. LTspice present, and the working invocation is reachable"
echo "=============================================================="
SS=/Applications/LTspice.app/Contents/SharedSupport/ltspice
[ -x "$SS/LTspice/wine" ]; chk $? "bundled wine at $SS/LTspice/wine"
[ -f "$SS/support/ltspice/drive_c/Program Files/ADI/LTspice/LTspice.exe" ]
chk $? "LTspice.exe inside the CrossOver bottle"
V=$(defaults read /Applications/LTspice.app/Contents/Info.plist CFBundleShortVersionString 2>/dev/null)
echo "        version: ${V:-unknown}"
echo "        session: $(launchctl managername 2>/dev/null) (needs 'Aqua' for batch mode)"

echo
echo "=============================================================="
echo " 2. The native stub really is a silent no-op (the core trap)"
echo "=============================================================="
T=$(mktemp -d); cp stage1/rc_lowpass.net "$T/"
/Applications/LTspice.app/Contents/MacOS/LTspice -b "$T/rc_lowpass.net" >/dev/null 2>&1
rc=$?
if [ "$rc" = "0" ] && [ ! -f "$T/rc_lowpass.raw" ]; then
  echo "  PASS  stub exits 0 and writes NO .raw  <-- why exit codes must not be trusted"
else
  echo "  NOTE  stub behaviour changed (rc=$rc, raw exists: $([ -f "$T/rc_lowpass.raw" ] && echo yes || echo no))"
  echo "        if it now works, LTspice gained a native CLI -- re-check run_sweep.WINE_CMD"
fi
rm -rf "$T"

echo
echo "=============================================================="
echo " 3. Stage 1 smoke test: tau == R*C"
echo "=============================================================="
$PY - <<'EOF'
import sys; sys.path.insert(0,'.')
import run_sweep as rs
from pathlib import Path
r = rs.run_one("stage1/rc_lowpass.net", {}, ["tau","vfinal"], Path("work/v/rc"), timeout=120)
tau, err = r["tau"], abs(r["tau"]-1e-3)/1e-3*100
print(f"        tau = {tau*1e3:.6f} ms vs R*C = 1.000000 ms  ({err:+.5f} %)")
print(f"        .raw written: {r['raw_exists']}   .log written: {r['log_exists']}")
sys.exit(0 if (r["raw_exists"] and r["log_exists"] and err < 0.01) else 1)
EOF
chk $? "RC time constant within 0.01 % and artefacts written"

echo
echo "=============================================================="
echo " 4. Schematics netlist to exactly the validated circuits"
echo "=============================================================="
$PY - <<'EOF'
import re, subprocess, sys, os
from pathlib import Path
sys.path.insert(0,'.')
import run_sweep as rs
W = Path("work/v/asc"); W.mkdir(parents=True, exist_ok=True)
def norm(s):
    s = re.sub(r"\s+"," ",s.strip()).replace("µ","u").replace("μ","u")
    return re.sub(r"\(\s+","(",s)
def comps(p):
    return {norm(l) for l in Path(p).read_text(errors="replace").splitlines()
            if l.strip() and not l.strip().startswith(("*","."))}
bad = 0
for asc, net in [("stage1/rc_lowpass.asc","stage1/rc_lowpass.net"),
                 ("stage3/dab.asc","stage3/dab.net")]:
    dst = W/Path(asc).name
    dst.write_text(Path(asc).read_text())
    subprocess.run(rs.WINE_CMD + ["-netlist", rs.win_path(dst)],
                   cwd=W, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   timeout=300, start_new_session=True)
    gen = dst.with_suffix(".net")
    if not gen.exists():
        print(f"        {asc}: LTspice produced no netlist"); bad = 1; continue
    a, b = comps(net), comps(gen)
    print(f"        {Path(asc).name:16s} {len(a):>2d} components  "
          f"{'IDENTICAL to ' + Path(net).name if a==b else 'DIFFERS'}")
    for d in sorted(a ^ b): print("            ", d)
    bad |= (a != b)
sys.exit(bad)
EOF
chk $? "dab.asc and rc_lowpass.asc are electrically the validated netlists"

echo
echo "=============================================================="
echo " 5. Stage 3 sweep vs closed-form theory (budget: 2 %)"
echo "=============================================================="
$PY sweep_stage3.py
chk $? "sweep ran"
$PY - <<'EOF'
import pandas as pd, sys
d = pd.read_csv("results.csv")
w = d["err_pct"].abs().max()
print(f"        worst |error| vs analytical : {w:.4f} %   (budget 2 %)")
print(f"        worst |DC offset| in window : {d['IL_dc_offset'].abs().max():.3f} A")
print(f"        RMS current vs closed form  : max dev "
      f"{(d['IL_rms']-d['IL_rms_analytical']).abs().max():.4f} A")
print(f"        ZVS at every phi            : {(d['zvs']=='yes').all()}")
sys.exit(0 if (w < 2.0 and (d["zvs"]=="yes").all()) else 1)
EOF
chk $? "all six phase shifts within 2 % and ZVS confirmed"

echo
echo "=============================================================="
echo " 6. Schematic gives the same answer as the netlist"
echo "=============================================================="
$PY - <<'EOF'
import sys; sys.path.insert(0,'.')
import run_sweep as rs, pandas as pd
from pathlib import Path
ref = pd.read_csv("results.csv").set_index("phi")
worst = 0.0
for phi in (30, 90):
    r = rs.run_one("stage3/dab.asc", {"phi":phi}, ["Pin"], Path(f"work/v/a{phi}"), timeout=600)
    if r["Pin"] is None: print(f"        phi={phi}: no result"); sys.exit(1)
    d = abs(r["Pin"]-ref.loc[phi,"P_sim"])/ref.loc[phi,"P_sim"]; worst = max(worst,d)
    print(f"        phi={phi:>2}  .asc {r['Pin']:10.4f} W   .net {ref.loc[phi,'P_sim']:10.4f} W   rel {d:.1e}")
sys.exit(0 if worst < 1e-9 else 1)
EOF
chk $? ".asc reproduces .net results"

echo
echo "=============================================================="
[ "$fail" = "0" ] && echo " ALL CHECKS PASSED" || echo " SOMETHING FAILED (see FAIL lines above)"
echo "=============================================================="
rm -rf work/v
exit $fail
