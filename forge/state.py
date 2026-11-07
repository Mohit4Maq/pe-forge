"""Program state for the supervisor: phases, modules, gates (stored in .forge/state.json, committed with the project).

Module flow: a project whose scope is big is split into modules (forge module add). The system-level P2 and P3
define the split; then each module runs P2..P6 as a small project of its own and must pass its gates before the
next module starts (unless the user allows parallel modules). P7 / P8 start when every module has passed G6.
"""
import datetime as dt
import json
import os
import re

from .phases import MODULE_PHASES, PHASES

TODAY = lambda: dt.date.today().isoformat()


def _blank():
    return {"phase": "P0", "phases": {p[0]: {"status": "todo"} for p in PHASES}, "modules": [],
            "active_module": None, "parallel_modules": False, "history": []}


def load(prj):
    f = prj.p("tools_data", "state.json")
    if not os.path.exists(f):
        return _blank()
    with open(f) as fh:
        return json.load(fh)


def save(prj, st):
    os.makedirs(prj.p("tools_data"), exist_ok=True)
    with open(prj.p("tools_data", "state.json"), "w") as fh:
        json.dump(st, fh, indent=1)
        fh.write("\n")


def log(st, what):
    st["history"].append({"date": TODAY(), "event": what})


def module(st, mid):
    for m in st["modules"]:
        if m["id"] == mid:
            return m
    raise SystemExit(f"no module {mid}")


def next_module_id(st):
    n = max([int(m["id"].split("-")[1]) for m in st["modules"]] or [0]) + 1
    return f"M-{n:02d}"


def slug(s):
    return re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_")


def add_module(prj, st, name, purpose="", depends=(), folder=None):
    """A new module; `folder` adopts an existing folder (relative to the project) instead of making modules/M-xx_*."""
    mid = next_module_id(st)
    d = folder.rstrip("/") if folder else os.path.join(prj.paths["modules"], f"{mid}_{slug(name)}")
    m = {"id": mid, "name": name, "dir": d, "purpose": purpose, "depends": list(depends),
         "status": "todo", "phases": {p: {"status": "todo"} for p in MODULE_PHASES}}
    st["modules"].append(m)
    os.makedirs(os.path.join(prj.root, d), exist_ok=True)
    for sub in ("Simulations", "Reviews"):
        os.makedirs(os.path.join(prj.root, d, sub), exist_ok=True)
    brief = os.path.join(prj.root, d, "Module_Brief.md")
    if not os.path.exists(brief):
        with open(brief, "w") as fh:
            fh.write(MODULE_BRIEF.format(id=mid, name=name, purpose=purpose or "_(fill in)_",
                                         depends=", ".join(depends) or "none", date=TODAY()))
    log(st, f"module {mid} {name} added")
    return m


def next_action(st):
    """What the supervisor should do next, as (scope, phase, text)."""
    for pid in ("P0", "P1"):
        if st["phases"][pid]["status"] != "passed":
            return ("system", pid, f"work {pid} and pass its gate")
    for pid in ("P2", "P3"):
        if st["phases"][pid]["status"] != "passed":
            return ("system", pid, f"system-level {pid}: define the system requirements / architecture and the module split")
    open_mods = [m for m in st["modules"] if m["status"] != "passed"]
    if open_mods:
        ready = [m for m in open_mods if all(module(st, d)["status"] == "passed" for d in m["depends"])]
        m = next((x for x in ready if x["id"] == st.get("active_module")), None) or (ready[0] if ready else open_mods[0])
        for pid in MODULE_PHASES:
            if m["phases"][pid]["status"] not in ("passed", "skipped"):
                return (m["id"], pid, f"module {m['id']} {m['name']}: {pid}")
        return (m["id"], "-", f"module {m['id']}: all phases passed; mark it passed")
    for pid in ("P4", "P5", "P6") if not st["modules"] else ():
        if st["phases"][pid]["status"] != "passed":
            return ("system", pid, f"{pid} (single-module project)")
    for pid in ("P7", "P8"):
        if st["phases"][pid]["status"] != "passed":
            return ("system", pid, f"{pid}")
    return ("system", "-", "all gates passed: project released")


MODULE_BRIEF = """---
type: module-brief
module: {id}
status: draft
created: {date}
---
# {id} {name}: module brief

> Treat this module as a small project: its own requirements, topology, BoM, simulation and schematic sheet,
> verified at its own gates before the next module starts.

## Purpose
{purpose}

## Interfaces (link the ICD rows)
| Interface | To module | Signal / power | Limits |
|---|---|---|---|

## Requirements allocated to this module (from trace.csv, block = {id})
_(filled by pe-requirements)_

## Depends on
{depends}

## Gate record
| Gate | Date | Review | Verdict |
|---|---|---|---|
"""
