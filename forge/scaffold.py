"""forge new <dir> --name NAME [--answers intake.json] [--owner NAME] [--force]

Creates the project folder from template/, writes project.toml from the intake answers (the /pe-new skill
collects them as multiple-choice questions), copies the KiCad project template into 08_PCB/<slug>/, writes
.forge/state.json with P0 active, and runs `git init`. Nothing is committed: the skill asks first.

intake.json (all optional except name / summary):
{"name": "Buck48", "summary": "48 V to 12 V, 1 kW bidirectional DC-DC for e-bikes", "owner": "Jane",
 "type": "dc-dc", "specs": {"power_W": 1000, "vin_V": [36, 60], "vout_V": 12},
 "corners": {"vin_V": [36, 60], "f_sw_kHz": [100, 200], "ambient_C": [-20, 60], "load_pct": [10, 100]},
 "standards": ["IEC 62368-1", "CISPR 32 class B"], "volume": "1k/yr", "target_cost_usd": 40,
 "profile": "full", "schematic_mode": "ask", "reviews": "gate", "region": "IN",
 "distributors": ["mouser", "digikey", "lcsc"], "precommit_hook": true, "answers": {"question": "answer", ...}}
statusline (default true): the pe-forge bar in <project>/.claude/settings.local.json (this machine only).
precommit_hook (default true): install the `forge check` git pre-commit hook. The CI workflow
.github/workflows/forge-check.yml is always copied (it does nothing until the project is pushed to GitHub).
"""
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys

from . import config, state

PROFILES = {"full", "lite"}
MODES = {"user", "agent", "ask"}


def toml_value(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(toml_value(x) for x in v) + "]"
    return json.dumps(str(v), ensure_ascii=False)


def toml_table(name, d):
    out = [f"[{name}]"]
    for k, v in d.items():
        if isinstance(v, dict):
            continue
        out.append(f"{re.sub(r'[^A-Za-z0-9_]', '_', k)} = {toml_value(v)}")
    return "\n".join(out) + "\n"


def project_toml(a):
    slug = re.sub(r"[^a-z0-9]+", "-", a["name"].lower()).strip("-")
    proj = {"name": a["name"], "slug": slug, "summary": a.get("summary", ""), "owner": a.get("owner", "the owner"),
            "type": a.get("type", "other"), "created": dt.date.today().isoformat()}
    proc = {"profile": a.get("profile", "full"), "schematic_mode": a.get("schematic_mode", "ask"),
            "reviews": a.get("reviews", "gate"), "parallel_modules": False,
            "change_control": a.get("change_control", "warn" if a.get("profile") == "lite" else "enforce"),
            "region": a.get("region", ""), "fab_house": a.get("fab_house", ""), "distributors": a.get("distributors", ["mouser", "digikey"])}
    if proc["profile"] not in PROFILES or proc["schematic_mode"] not in MODES:
        raise SystemExit(f"profile must be one of {PROFILES}, schematic_mode one of {MODES}")
    parts = ["# pe-forge project settings. Tools read this file; edit it freely (Rule 5 applies to the values).\n",
             toml_table("project", proj), toml_table("process", proc),
             toml_table("specs", a.get("specs", {})), toml_table("corners", a.get("corners", {})),
             toml_table("market", {k: a[k] for k in ("volume", "target_cost_usd", "region") if k in a}),
             '[standards]\nlist = ' + toml_value(a.get("standards", [])) + "\n",
             "[budget]\ndaily_cap_units = 20000000\nwindow_5h_cap_units = 8000000\namber_fraction = 0.7\nred_fraction = 0.9\n",
             "[vmodel]\nl0_all = []\nl0_cert = []\ncompliance_sections = []\n",
             "# ID families linked by `forge link` (Rule 9)\n",
             "[ids.D]\npattern = 'D-\\d{2,3}'\norigin = '01_Program/Master_Plan.md'\n",
             "[ids.E]\npattern = 'E-\\d{2,3}'\norigin = '01_Program/Master_Plan.md'\n",
             "[ids.S]\npattern = 'S-\\d{2,3}'\norigin = '01_Program/Master_Plan.md'\n",
             "[ids.V]\npattern = 'V-\\d{2,3}'\norigin = '01_Program/Master_Plan.md'\n",
             "[ids.ADR]\npattern = 'ADR-\\d{4}'\nfiles = '01_Program/Decisions'\n",
             "[ids.M]\npattern = 'M-\\d{2}'\norigin = '02_Requirements/Architecture/Module_Breakdown.md'\n"]
    return "\n".join(parts), slug


def fill(path, subs):
    try:
        t = open(path, encoding="utf-8").read()
    except (UnicodeDecodeError, IsADirectoryError):
        return
    n = t
    for k, v in subs.items():
        n = n.replace("{{" + k + "}}", v)
    if n != t:
        open(path, "w", encoding="utf-8").write(n)


def kicad_project(dst, slug, a):
    src = os.path.join(config.FORGE_ROOT, "library", "template", "MC_Standard")
    out = os.path.join(dst, "08_PCB", slug)
    os.makedirs(out, exist_ok=True)
    for f in os.listdir(src):
        s = os.path.join(src, f)
        name = f.replace("MC_Standard", slug)
        if os.path.isdir(s):
            shutil.copytree(s, os.path.join(out, name), dirs_exist_ok=True)
        else:
            shutil.copy(s, os.path.join(out, name))
    pro = os.path.join(out, f"{slug}.kicad_pro")
    if os.path.exists(pro):
        p = json.load(open(pro))
        tv = p.setdefault("text_variables", {})
        tv.update({"COMPANY": a.get("company", ""), "DESIGNER": a.get("owner", ""), "REVISION": "A",
                   "PCB_PN": "TBD", "RELEASE_DATE": "DRAFT"})
        p.setdefault("meta", {})["filename"] = f"{slug}.kicad_pro"
        json.dump(p, open(pro, "w"), indent=2)
    return out


def main(argv):
    if not argv or argv[0].startswith("-"):
        print(__doc__)
        return 1
    dst = os.path.abspath(argv[0])
    a = json.load(open(argv[argv.index("--answers") + 1])) if "--answers" in argv else {}
    if "--name" in argv:
        a["name"] = argv[argv.index("--name") + 1]
    if "--owner" in argv:
        a["owner"] = argv[argv.index("--owner") + 1]
    if not a.get("name"):
        raise SystemExit("--name or answers.name is required")
    if os.path.exists(os.path.join(dst, "project.toml")) and "--force" not in argv:
        raise SystemExit(f"{dst} already holds a pe-forge project")
    shutil.copytree(config.TEMPLATE, dst, dirs_exist_ok=True, ignore=shutil.ignore_patterns(".gitkeep"))
    for key, rel in config.DEFAULT_PATHS.items():   # every standard folder exists, whatever git kept of the template
        os.makedirs(os.path.join(dst, rel if not os.path.splitext(rel)[1] else os.path.dirname(rel)), exist_ok=True)
    toml, slug = project_toml(a)
    open(os.path.join(dst, "project.toml"), "w").write(toml)
    subs = {"NAME": a["name"], "DATE": dt.date.today().isoformat(), "OWNER": a.get("owner", "The owner"),
            "SUMMARY": a.get("summary", "")}
    for dp, _, fs in os.walk(dst):
        if "/.git" in dp:
            continue
        for f in fs:
            if f.endswith((".md", ".toml", ".csv")):
                fill(os.path.join(dp, f), subs)
    with open(os.path.join(dst, "03_Foundation", "Intake_Answers.md"), "w") as fh:
        fh.write(f"---\ntype: intake\ndate: {subs['DATE']}\n---\n# {a['name']}: intake answers\n\n"
                 f"**Summary:** {a.get('summary', '')}\n\n| Question | Answer |\n|---|---|\n")
        for q, ans in (a.get("answers") or {}).items():
            fh.write(f"| {q} | {ans} |\n")
        for k in ("type", "specs", "corners", "standards", "volume", "target_cost_usd", "profile", "schematic_mode",
                  "reviews", "region", "distributors"):
            if k in a:
                fh.write(f"| {k} | {json.dumps(a[k], ensure_ascii=False)} |\n")
    kicad_project(dst, slug, a)
    prj = config.Project(dst)
    st = state._blank()
    st["phases"]["P0"]["status"] = "active"
    state.log(st, "project created")
    state.save(prj, st)
    if not os.path.isdir(os.path.join(dst, ".git")):
        subprocess.run(["git", "init", "-q", "-b", "main", dst], check=False)
    if a.get("statusline", True):      # the pe-forge bar, for this project on this machine only
        from . import statusline
        try:
            statusline.install(os.path.join(dst, ".claude", "settings.local.json"))
        except Exception as e:
            print(f"  status line not installed: {e}")
    if a.get("precommit_hook", True):
        from . import check
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()):
            check.hooks(prj, ["install"])
    print(f"created {a['name']} at {dst}\n  KiCad project: 08_PCB/{slug}/"
          + ("\n  git pre-commit hook: forge check (remove with `forge hooks --uninstall`)" if a.get("precommit_hook", True) else "")
          + ("\n  status line: pe-forge bar in .claude/settings.local.json (restart Claude Code to see it)"
             if a.get("statusline", True) else "")
          + "\n  next: /pe-run (or `forge status`)")
    return 0
