"""forge adopt: bring a project that already has work in it under pe-forge, without changing anything in it.

  forge adopt [dir] [--name NAME] [--json]     survey only: what is there and what pe-forge would add (the default)
  forge adopt [dir] --apply [--name NAME]      do it: add pe-forge's files around the project's own
  forge adopt [dir] --undo                     remove exactly what the adoption added

What --apply guarantees:
  - every file that existed before is byte-for-byte unchanged afterwards (checked: each one is hashed before and
    after; a difference is reported and the adoption stops);
  - pe-forge's folders and files are only added; where one already exists (your CLAUDE.md, a Master_Plan.md) it is
    kept and pe-forge's version is written beside it as <name>.forge-proposed<ext>;
  - pe-forge works on your files where they are: project.toml [paths] points at your KiCad folder (pcb) instead
    of moving anything into 08_PCB/;
  - the phase starts where the work is: a drawn schematic -> P6 Schematic, a board with parts on it -> P7 PCB;
    the phases before it are "skipped" (done outside pe-forge), never "passed": their gates are still yours to pass;
  - your sheets and boards stay user-owned: the schematic engine only advises on them, `forge pcb setup` will not
    replace an outline it did not draw, `forge bom build` never overwrites a BoM it did not write;
  - .forge/adopt.json records every file added, so --undo removes exactly those (a file you edited since is kept).
"""
import hashlib
import json
import os
import re
import shutil

SKIP_DIRS = {".git", ".forge", "node_modules", "__pycache__", ".venv", "venv", ".obsidian", ".claude", ".idea",
             ".vscode", "build", "dist"}
MANIFEST = os.path.join(".forge", "adopt.json")


def _sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def _walk(root):
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if d not in SKIP_DIRS and not d.endswith("-backups")]
        for f in fn:
            yield os.path.join(dp, f)


def snapshot(root):
    """{relative path: sha256} of every file in the project (outside .git / .forge / tool folders)."""
    return {os.path.relpath(p, root).replace(os.sep, "/"): _sha(p) for p in _walk(root) if os.path.isfile(p)}


def _has(path, *keys):
    try:
        t = open(path, errors="ignore").read()
    except OSError:
        return False
    return any(k in t for k in keys)


def survey(root):
    root = os.path.abspath(root)
    files = [os.path.relpath(p, root).replace(os.sep, "/") for p in _walk(root)]
    pros = sorted(f for f in files if f.endswith(".kicad_pro"))
    schs = [f for f in files if f.endswith(".kicad_sch")]
    pcbs = [f for f in files if f.endswith(".kicad_pcb")]
    drawn = [f for f in schs if _has(os.path.join(root, f), "(lib_id", "\n\t(sheet\n")]
    placed = [f for f in pcbs if _has(os.path.join(root, f), "(footprint ", "(footprint\n")]
    routed = [f for f in pcbs if _has(os.path.join(root, f), "(segment", "(arc")]
    boms = [f for f in files if re.search(r"(^|[/_\- ])bo?m([_\- .]|$)", f.lower()) and f.lower().endswith((".csv", ".xlsx", ".xls", ".md"))]
    gerbers = [f for f in files if f.lower().endswith((".gbr", ".gtl", ".gbl", ".drl"))]
    docs = [f for f in files if f.lower().endswith((".md", ".pdf", ".docx")) and "/" not in f.strip("/") or f.lower().endswith(".md")]
    kicad_dirs = sorted({os.path.dirname(f) for f in pros} or {os.path.dirname(f) for f in schs + pcbs})
    if kicad_dirs:
        common = os.path.commonpath([d or "." for d in kicad_dirs]) if len(kicad_dirs) > 1 else os.path.dirname(kicad_dirs[0])
        pcb_root = (common or ".").replace(os.sep, "/")
    else:
        pcb_root = None
    if routed or gerbers:
        phase = "P7"
    elif placed:
        phase = "P7"
    elif drawn:
        phase = "P6"
    else:
        phase = "P1" if docs else "P0"
    return {"root": root, "is_pe_forge": os.path.exists(os.path.join(root, "project.toml")),
            "git": os.path.isdir(os.path.join(root, ".git")), "files": len(files),
            "kicad_projects": pros, "schematics": len(schs), "drawn_sheets": drawn, "boards": len(pcbs),
            "placed_boards": placed, "routed_boards": routed, "gerbers": len(gerbers), "boms": boms[:10],
            "docs": len([f for f in files if f.lower().endswith((".md", ".pdf", ".docx"))]),
            "own_claude_md": os.path.exists(os.path.join(root, "CLAUDE.md")),
            "pcb_root": pcb_root, "phase": phase}


def plan_text(sv, name):
    L = [f"Adopting {sv['root']} as pe-forge project '{name}' (survey only, nothing changed yet)", "",
         f"  files: {sv['files']}  ·  git: {'yes' if sv['git'] else 'no (git init will be run)'}"]
    if sv["kicad_projects"]:
        L.append("  KiCad: " + ", ".join(sv["kicad_projects"][:4]))
    L.append(f"  schematics: {sv['schematics']} ({len(sv['drawn_sheets'])} with parts)  ·  boards: {sv['boards']} "
             f"({len(sv['placed_boards'])} with parts, {len(sv['routed_boards'])} routed)  ·  Gerbers: {sv['gerbers']}")
    if sv["boms"]:
        L.append("  your BoM files (kept as they are): " + ", ".join(sv["boms"][:4]))
    L += ["", "pe-forge would:"]
    if sv["pcb_root"] is not None:
        L.append(f"  - use your KiCad files where they are: [paths] pcb = \"{sv['pcb_root']}\" (nothing moved)")
    else:
        L.append("  - create a starter KiCad project in 08_PCB/ (none found)")
    L.append(f"  - start at {sv['phase']} ({'drawn schematic' if sv['phase'] == 'P6' else 'board with parts' if sv['phase'] == 'P7' else 'no design files yet'}); "
             "earlier phases marked skipped (done outside pe-forge), not passed")
    L.append("  - add its folders, plan, register and templates only where they are missing")
    if sv["own_claude_md"]:
        L.append("  - keep your CLAUDE.md; pe-forge's rules go beside it as CLAUDE.forge-proposed.md for you to merge")
    L += ["  - add the status line (merged into .claude/settings.local.json), the Obsidian vault settings, and the",
          "    forge check pre-commit hook (not if you already have a hook)",
          "", "Every existing file stays byte-for-byte as it is (checked after). Undo: forge adopt --undo",
          "Go ahead: forge adopt --apply" + (f" --name {name}" if name else "")]
    return "\n".join(L)


def apply(root, name, out=print):
    from . import config, scaffold, state
    root = os.path.abspath(root)
    sv = survey(root)
    if sv["is_pe_forge"]:
        raise SystemExit(f"{root} is already a pe-forge project (project.toml): nothing to adopt")
    before = snapshot(root)
    paths = {"pcb": sv["pcb_root"]} if sv["pcb_root"] is not None else {}
    a = {"name": name, "summary": "adopted project", "schematic_mode": "user"}
    made = scaffold.create(root, a, paths=paths, kicad=sv["pcb_root"] is None, out=out)
    # the phase: where the work is; earlier phases skipped (not passed)
    prj = config.Project(root)
    st = state.load(prj)
    order = [p for p in st["phases"]]
    cur = sv["phase"]
    for p in order:
        if p == cur:
            break
        st["phases"][p]["status"] = "skipped"
        st["phases"][p]["note"] = "adopted: done outside pe-forge"
    st["phases"]["P0"]["status"] = "skipped" if cur != "P0" else "active"
    st["phases"][cur]["status"] = "active"
    st["phase"] = cur
    state.log(st, f"adopted at {cur} (forge adopt)")
    state.save(prj, st)
    after = snapshot(root)
    changed = sorted(k for k, h in before.items() if after.get(k) != h)
    added = sorted(k for k in after if k not in before)
    rec = {"version": 1, "name": name, "phase": cur, "paths": paths, "added": {k: after[k] for k in added},
           "git_init": made["extras"]["git_init"], "statusline": made["extras"]["statusline"],
           "hook": made["extras"]["hook"], "obsidian": made["extras"]["obsidian"]}
    os.makedirs(os.path.join(root, ".forge"), exist_ok=True)
    json.dump(rec, open(os.path.join(root, MANIFEST), "w"), indent=1)
    if changed:                                      # must never happen: say so loudly
        raise SystemExit("adopt changed existing files, which it must never do: " + ", ".join(changed[:10])
                         + ". Run `forge adopt --undo` and report it: forge issue --last")
    return sv, added, made


def undo(root, out=print):
    root = os.path.abspath(root)
    mf = os.path.join(root, MANIFEST)
    if not os.path.exists(mf):
        raise SystemExit("no adoption recorded here (.forge/adopt.json)")
    rec = json.load(open(mf))
    # first what needs the project still in place (the hook and the status line), then the files
    if rec.get("hook"):
        from . import check, config
        try:
            import contextlib, io
            with contextlib.redirect_stdout(io.StringIO()):
                check.hooks(config.Project(root), ["--uninstall"])
        except SystemExit:
            pass
    if rec.get("statusline") == "merged":
        from . import statusline
        try:
            statusline.install(os.path.join(root, ".claude", "settings.local.json"), uninstall=True)
        except Exception:
            pass
    kept, removed = [], 0
    for rel, h in rec["added"].items():
        p = os.path.join(root, rel)
        if not os.path.exists(p):
            continue
        if _sha(p) != h:
            kept.append(rel)                         # edited since adoption: yours now
            continue
        os.remove(p)
        removed += 1
    for rel in rec.get("obsidian", []):
        p = os.path.join(root, rel)
        if os.path.exists(p) and rel not in rec["added"]:
            os.remove(p)
    for dp, dn, fn in sorted(os.walk(root, topdown=False), key=lambda x: -len(x[0])):   # folders adopt created, if empty
        if "/.git" in dp.replace(os.sep, "/") or dp == root:
            continue
        try:
            if not os.listdir(dp):
                os.rmdir(dp)
        except OSError:
            pass
    shutil.rmtree(os.path.join(root, ".forge"), ignore_errors=True)
    for d in (".claude", ".obsidian"):
        p = os.path.join(root, d)
        if os.path.isdir(p) and not os.listdir(p):
            os.rmdir(p)
    if rec.get("git_init"):
        out("  the git repository pe-forge created is kept (.git): delete it yourself if you do not want it")
    out(f"adoption undone: {removed} added file(s) removed" + (f"; kept {len(kept)} you edited since: {', '.join(kept[:5])}" if kept else ""))
    return 0


def main(argv):
    pos = [a for i, a in enumerate(argv) if not a.startswith("--") and (i == 0 or argv[i - 1] != "--name")]
    root = os.path.abspath(pos[0] if pos else os.getcwd())
    if "--undo" in argv:
        return undo(root)
    name = argv[argv.index("--name") + 1] if "--name" in argv else os.path.basename(root.rstrip(os.sep))
    if "--apply" not in argv:
        sv = survey(root)
        if "--json" in argv:
            print(json.dumps(sv, indent=1))
        elif sv["is_pe_forge"]:
            print(f"{root} is already a pe-forge project (project.toml)")
        else:
            print(plan_text(sv, name if "--name" in argv else ""))
        return 0
    sv, added, made = apply(root, name)
    print(f"adopted {name}: {len(added)} file(s) added, every existing file unchanged (verified)")
    print(f"  phase: {sv['phase']}" + (f"  ·  KiCad: [paths] pcb = \"{sv['pcb_root']}\"" if sv["pcb_root"] is not None else ""))
    if made["proposed"]:
        print(f"  kept your versions of {len(made['proposed'])} file(s); pe-forge's are beside them: "
              + ", ".join(made["proposed"][:4]) + ("…" if len(made["proposed"]) > 4 else ""))
    print("  next: forge status  ·  undo: forge adopt --undo")
    return 0
