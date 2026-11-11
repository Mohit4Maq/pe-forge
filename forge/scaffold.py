"""forge new <dir> --name NAME [--answers intake.json] [--owner NAME] [--force]

Never replaces a file: a template file that already exists in <dir> is kept and pe-forge's version goes beside it
(<name>.forge-proposed<ext>). For a project that already has work in it, use `forge adopt`.

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
obsidian (default true): the folder is also an Obsidian vault (.obsidian/ settings; `forge vault open` opens it).
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


INSULATION_EXAMPLE = """# Insulation checks (forge insulation; forge check runs them when the schematic has HV nets).
# Uncomment and set from your insulation coordination (IEC 60664-1 / product standard). Values here are examples only.
# [insulation]
# hv_class = "HV"
# barrier_creepage_mm = 10.0            # reinforced barrier on the board (INS-3)
# islands = ["HV_DCP", "HV_DCN"]        # every HV net in exactly one (NET-4)
# barriers = []                         # extra barrier parts by MPN, besides MC_Parts Iso_Rating
# [protection]                          # surge clamps (forge protection): peak <= margin x abs max
# v_operating_max = 63                  # default: [specs] vin_V maximum
# margin = 0.9                          # e.g. the requirement's 90 % of the IC absolute maximum
# [[insulation.rule]]                   # CLS-2: the .kicad_dru must flag copper closer than this
# a = "HV"
# b = "!HV"
# clearance_mm = 2.5
# creepage_mm = 4.0
# [hp]                                  # High-Power PCB Rule Book (forge hp): on by default at >= 50 V or >= 5 A
# discrete_switches = false             # profile flags decide which rules apply: gate_driver, isolation, shunts,
# gate_driver = false                   # paralleled, emi_filter, heatsink, strips, wbg, multiphase, chassis
# sw_edge_mm = 10                       # SW-05; hf_loops, cm_chokes, hot_parts / precision, [hp.budget] for PL-06
# [hp.waive]                            # a rule that does not fit: "ID" = "reason"; a Must needs "(owner OK, date)"
"""


def project_toml(a):
    slug = re.sub(r"[^a-z0-9]+", "-", a["name"].lower()).strip("-")
    proj = {"name": a["name"], "slug": slug, "summary": a.get("summary", ""), "owner": a.get("owner", "the owner"),
            "type": a.get("type", "other"), "created": dt.date.today().isoformat()}
    proc = {"profile": a.get("profile", "full"), "schematic_mode": a.get("schematic_mode", "ask"),
            "reviews": a.get("reviews", "gate"), "parallel_modules": False,
            "level": a.get("level", "standard"),
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
             INSULATION_EXAMPLE,
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
    """The starter KiCad project in 08_PCB/<slug>/, only when that folder holds no KiCad project yet."""
    from . import guard
    src = os.path.join(config.FORGE_ROOT, "library", "template", "MC_Standard")
    out = os.path.join(dst, "08_PCB", slug)
    if os.path.isdir(out) and any(f.endswith((".kicad_pro", ".kicad_sch", ".kicad_pcb")) for f in os.listdir(out)):
        return out                                   # a KiCad project is already there: never touch it
    os.makedirs(out, exist_ok=True)
    for f in os.listdir(src):
        s = os.path.join(src, f)
        name = f.replace("MC_Standard", slug)
        if os.path.isdir(s):
            for dp, _, fs in os.walk(s):
                for g in fs:
                    rel = os.path.relpath(os.path.join(dp, g), s)
                    guard.write(dst, os.path.join(out, name, rel), open(os.path.join(dp, g), "rb").read())
        else:
            data = open(s, "rb").read()
            if name.endswith(".kicad_pro"):
                p = json.loads(data)
                tv = p.setdefault("text_variables", {})
                tv.update({"COMPANY": a.get("company", ""), "DESIGNER": a.get("owner", ""), "REVISION": "A",
                           "PCB_PN": "TBD", "RELEASE_DATE": "DRAFT"})
                p.setdefault("meta", {})["filename"] = f"{slug}.kicad_pro"
                data = json.dumps(p, indent=2)
            guard.write(dst, os.path.join(out, name), data)
    return out


TEXT_EXT = (".md", ".toml", ".csv", ".json", ".yml", ".yaml", ".txt", ".gitignore", ".gitattributes")


def copy_template(dst, subs, out=None):
    """Template files one by one through the write guard: a file that already exists is never replaced (pe-forge's
    version goes beside it as <name>.forge-proposed<ext>). Placeholders are filled in the template text only, never
    in the project's own files. Returns {"written": [...], "proposed": [...], "unchanged": [...]} (relative paths)."""
    from . import guard
    res = {"written": [], "proposed": [], "unchanged": []}
    for dp, dn, fs in os.walk(config.TEMPLATE):
        dn[:] = [d for d in dn if d not in (".git", "__pycache__")]
        for f in fs:
            if f == ".gitkeep" or f.startswith(".DS_Store") or " 2." in f or f.endswith(" 2"):
                continue
            s = os.path.join(dp, f)
            rel = os.path.relpath(s, config.TEMPLATE)
            data = open(s, "rb").read()
            if f.endswith(TEXT_EXT) or f in (".gitignore", ".gitattributes"):
                try:
                    txt = data.decode("utf-8")
                    for k, v in subs.items():
                        txt = txt.replace("{{" + k + "}}", v)
                    data = txt.encode("utf-8")
                except UnicodeDecodeError:
                    pass
            st, wp = guard.write(dst, os.path.join(dst, rel), data)
            key = {"written": "written", "updated": "written", "unchanged": "unchanged"}.get(st, "proposed")
            res[key].append(os.path.relpath(wp, dst).replace(os.sep, "/"))
        for d in dn:                                 # empty template folders exist too
            os.makedirs(os.path.join(dst, os.path.relpath(os.path.join(dp, d), config.TEMPLATE)), exist_ok=True)
    if out and res["proposed"]:
        out(f"  {len(res['proposed'])} file(s) already existed and were kept; pe-forge's versions are beside them "
            "(*.forge-proposed.*): review and merge, or `forge guard accept <path>`")
    return res


def create(dst, a, paths=None, kicad=True, out=print):
    """Lay out a pe-forge project in dst without replacing anything that is there. Returns what was created."""
    from . import guard
    if os.path.exists(os.path.join(dst, "project.toml")):
        raise SystemExit(f"{dst} already holds a pe-forge project (project.toml)")
    os.makedirs(dst, exist_ok=True)
    had_git = os.path.isdir(os.path.join(dst, ".git"))
    subs = {"NAME": a["name"], "DATE": dt.date.today().isoformat(), "OWNER": a.get("owner", "The owner"),
            "SUMMARY": a.get("summary", "")}
    made = copy_template(dst, subs, out)
    eff = dict(config.DEFAULT_PATHS, **(paths or {}))
    for key, rel in eff.items():                     # every standard folder exists (mapped ones already do)
        os.makedirs(os.path.join(dst, rel if not os.path.splitext(rel)[1] else os.path.dirname(rel)), exist_ok=True)
    toml, slug = project_toml(a)
    if paths:
        toml += "\n[paths]                       # the project's own folders (forge adopt)\n" + "".join(
            f'{k} = "{v}"\n' for k, v in sorted(paths.items()))
    st_, _ = guard.write(dst, os.path.join(dst, "project.toml"), toml)
    made["written"].append("project.toml")
    intake = (f"---\ntype: intake\ndate: {subs['DATE']}\n---\n# {a['name']}: intake answers\n\n"
              f"**Summary:** {a.get('summary', '')}\n\n| Question | Answer |\n|---|---|\n")
    for q, ans in (a.get("answers") or {}).items():
        intake += f"| {q} | {ans} |\n"
    for k in ("type", "specs", "corners", "standards", "volume", "target_cost_usd", "profile", "schematic_mode",
              "reviews", "region", "distributors"):
        if k in a:
            intake += f"| {k} | {json.dumps(a[k], ensure_ascii=False)} |\n"
    ip = os.path.join(dst, "03_Foundation", "Intake_Answers.md")
    s_, wp = guard.write(dst, ip, intake)
    made["written" if s_ in ("written", "updated", "unchanged") else "proposed"].append(os.path.relpath(wp, dst).replace(os.sep, "/"))
    if kicad:
        kicad_project(dst, slug, a)
    prj = config.Project(dst)
    st = state._blank()
    st["phases"]["P0"]["status"] = "active"
    state.log(st, "project created")
    state.save(prj, st)
    if not had_git:
        subprocess.run(["git", "init", "-q", "-b", "main", dst], check=False)
    extras = {"git_init": not had_git, "obsidian": [], "statusline": None, "hook": False}
    if a.get("obsidian", True):        # the project folder is also an Obsidian vault (never overwrites a setting)
        from . import vault
        extras["obsidian"] = [os.path.relpath(w, dst).replace(os.sep, "/") for w in (vault.init(dst) or [])]
    if a.get("statusline", True):      # the pe-forge bar, for this project on this machine only (merged, with a .bak)
        from . import statusline
        sl = os.path.join(dst, ".claude", "settings.local.json")
        existed = os.path.exists(sl)
        try:
            statusline.install(sl)
            extras["statusline"] = "merged" if existed else "created"
        except Exception as e:
            out(f"  status line not installed: {e}")
    if a.get("precommit_hook", True):  # never replaces someone else's hook (forge hooks install --chain keeps it)
        from . import check
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = check.hooks(prj, ["install"])
        extras["hook"] = rc == 0
        if rc:
            out("  git pre-commit hook: you already have one; not replaced (forge hooks install --chain adds ours after it)")
    made["extras"] = extras
    made["slug"] = slug
    return made


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
    if os.path.isdir(dst) and "--force" not in argv:
        extra = [f for f in os.listdir(dst) if f not in (".obsidian", ".git", ".gitignore", ".DS_Store", ".claude", "00_Inbox")]
        if extra:
            raise SystemExit(f"{dst} is not empty ({', '.join(sorted(extra)[:5])}). For a project that already has "
                             "work in it use `forge adopt` (it maps your folders and never replaces a file); "
                             "--force lays out pe-forge here without replacing anything either")
    made = create(dst, a)
    slug = made["slug"]
    print(f"created {a['name']} at {dst}\n  KiCad project: 08_PCB/{slug}/"
          + ("\n  git pre-commit hook: forge check (remove with `forge hooks --uninstall`)" if made["extras"]["hook"] else "")
          + ("\n  status line: pe-forge bar in .claude/settings.local.json (restart Claude Code to see it)"
             if made["extras"]["statusline"] else "")
          + "\n  next: /pe-run (or `forge status`)")
    return 0
