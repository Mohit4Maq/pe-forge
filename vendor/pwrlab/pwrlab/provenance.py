"""Who produced this number, with what, and can it be reproduced.

A simulation result is evidence only if the thing that produced it can be
named. `checks.py` decides whether a number may be BELIEVED; this module
decides whether it may be CITED. They are different questions and both have to
be answered before a result goes into a validation package.

Three facts are recorded with every result:

    model_version   the git revision of pwrlab itself, plus whether the working
                    tree was dirty when the run happened
    toolchain_hash  a digest over the simulator command, its version string and
                    the Python/library versions that post-processed the raw file
    run_utc         when

The dirty flag is the one that matters in practice. A run made against
uncommitted edits cannot be reproduced by anyone else, including its author a
week later, so W4's evidence gate refuses it. Exploration is allowed to be
dirty -- evidence is not. Being unable to point at the code that produced a
number is the same failure as not checking the number.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
from dataclasses import dataclass, asdict
from functools import lru_cache
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LOCKFILE = REPO / "toolchain.lock.json"

# Post-processing libraries whose version can change a reported number. numpy
# and pandas do the arithmetic; spicelib parses the .raw. matplotlib only draws,
# so it is recorded for completeness but is not part of the hash.
_NUMERIC_DEPS = ("numpy", "pandas", "spicelib")
_RECORDED_DEPS = _NUMERIC_DEPS + ("matplotlib", "mcp")


def _git(*args) -> str | None:
    try:
        out = subprocess.run(("git", "-C", str(REPO)) + args,
                             capture_output=True, text=True, timeout=10)
        return out.stdout.strip() if out.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


@lru_cache(maxsize=1)
def git_sha() -> str | None:
    return _git("rev-parse", "HEAD")


def is_dirty() -> bool:
    """True if tracked files differ from HEAD, or git is unavailable.

    Unavailable git is deliberately treated as DIRTY rather than as clean: an
    unknown provenance must never be able to pass the evidence gate by default.
    """
    st = _git("status", "--porcelain", "--untracked-files=no")
    if st is None:
        return True
    return bool(st)


def _dep_versions() -> dict:
    out = {}
    for name in _RECORDED_DEPS:
        try:
            from importlib.metadata import version
            out[name] = version(name)
        except Exception:
            out[name] = None
    return out


def _simulator_identity() -> dict:
    """The simulator command, and its version string where it can be had.

    LTspice has no `--version` that works headless under Wine, so the binary's
    size and mtime stand in for a version: they change when the app is updated,
    which is the only property actually needed here.
    """
    from .sim import default_command
    try:
        cmd = default_command()
    except FileNotFoundError as e:
        return {"command": None, "error": str(e), "binary": None}
    binary = None
    for part in cmd:
        p = Path(part)
        if p.exists() and p.is_file():
            binary = {"path": str(p), "bytes": p.stat().st_size,
                      "mtime": int(p.stat().st_mtime)}
            break
    return {"command": cmd, "binary": binary,
            "env_override": os.environ.get("PWRLAB_LTSPICE")}


@dataclass(frozen=True)
class Provenance:
    model_version: str | None
    dirty: bool
    toolchain_hash: str
    python: str
    platform: str
    max_workers: int

    def as_dict(self) -> dict:
        return asdict(self)


@lru_cache(maxsize=1)
def _toolchain_payload() -> dict:
    from .sim import MAX_WORKERS
    return {
        "simulator": _simulator_identity(),
        "python": sys.version.split()[0],
        "platform": f"{platform.system()} {platform.release()} {platform.machine()}",
        "dependencies": _dep_versions(),
        "max_workers": MAX_WORKERS,
    }


def toolchain_hash() -> str:
    """Digest over everything that could change a number.

    Only the NUMERIC dependencies enter the hash. A matplotlib bump must not
    invalidate an evidence package, because matplotlib cannot change a
    measurement -- but a numpy or spicelib bump can, so those do.
    """
    p = _toolchain_payload()
    material = {
        "simulator": p["simulator"].get("binary"),
        "command": p["simulator"].get("command"),
        "python": p["python"],
        "platform": p["platform"],
        "numeric_deps": {k: p["dependencies"].get(k) for k in _NUMERIC_DEPS},
    }
    blob = json.dumps(material, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


@lru_cache(maxsize=1)
def current() -> Provenance:
    p = _toolchain_payload()
    return Provenance(
        model_version=git_sha(),
        dirty=is_dirty(),
        toolchain_hash=toolchain_hash(),
        python=p["python"],
        platform=p["platform"],
        max_workers=p["max_workers"],
    )


def stamp() -> dict:
    """The provenance block attached to every MCP payload."""
    c = current()
    d = c.as_dict()
    if c.dirty:
        d["evidence_eligible"] = False
        d["why_not_evidence"] = (
            "working tree is dirty (or git unavailable): this run cannot be "
            "reproduced from a named revision, so it must not be cited as "
            "validation evidence")
    else:
        d["evidence_eligible"] = True
    return d


def write_lockfile(path=LOCKFILE, baseline_suite_seconds: float | None = None) -> Path:
    """Record the toolchain, so a later change to it is visible rather than silent."""
    p = dict(_toolchain_payload())
    p["toolchain_hash"] = toolchain_hash()
    p["model_version"] = git_sha()
    if baseline_suite_seconds is not None:
        p["baseline_suite_seconds"] = baseline_suite_seconds
    path = Path(path)
    path.write_text(json.dumps(p, indent=2, sort_keys=True) + "\n")
    return path


def check_lockfile(path=LOCKFILE) -> tuple[bool, str]:
    """Has the toolchain moved since the lockfile was written?"""
    path = Path(path)
    if not path.exists():
        return False, f"no lockfile at {path}; run `pwrlab lock`"
    try:
        rec = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        return False, f"lockfile is not valid JSON: {e}"
    now = toolchain_hash()
    if rec.get("toolchain_hash") != now:
        return False, (f"toolchain changed: lockfile {rec.get('toolchain_hash')} "
                       f"vs current {now}. Results are not comparable across "
                       f"this boundary without re-running the baseline.")
    return True, f"toolchain matches lockfile ({now})"
