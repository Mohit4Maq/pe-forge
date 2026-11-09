"""LTspice execution layer.

Everything in here exists because of something that actually bit us on macOS.
See REPORT.md for the full forensics; the short version:

  * LTspice 26 for macOS is Windows LTspice.exe inside a bundled CodeWeavers
    CrossOver (Wine) bottle.  There is no native Mac binary any more.
  * /Applications/LTspice.app/Contents/MacOS/LTspice DISCARDS argv: it exits 0
    and writes nothing.  Never invoke it.  This is why `SimResult.ok` requires
    the artefacts to exist and never trusts a return code.
  * wine needs the FULL Windows path to the exe, and --bottle=ltspice (the
    shipped run_ltspice wrapper asks for --bottle=default, which does not exist).
  * Netlist paths go in as Z: paths (wine maps Z: -> /).
  * More than 3 concurrent instances is SLOWER THAN SEQUENTIAL.
"""
from __future__ import annotations

import concurrent.futures as cf
import os
import platform
import re
import shutil
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

# --------------------------------------------------------------------------- #
# locating the simulator
# --------------------------------------------------------------------------- #
_MAC_SS = Path("/Applications/LTspice.app/Contents/SharedSupport/ltspice")
_MAC_WINE = _MAC_SS / "LTspice" / "wine"
_MAC_EXE = r"C:\Program Files\ADI\LTspice\LTspice.exe"


def default_command() -> list[str]:
    """argv prefix that actually runs LTspice on this host."""
    env = os.environ.get("PWRLAB_LTSPICE")
    if env:
        if Path(env.strip('"')).exists():          # a plain path (Windows: C:\\...\\LTspice.exe): use it as-is
            return [env.strip('"')]
        import shlex                               # a command line; POSIX rules would eat Windows backslashes
        return [a.strip('"') for a in shlex.split(env, posix=os.name != "nt")]
    if platform.system() == "Darwin" and _MAC_WINE.exists():
        return [str(_MAC_WINE), "--enable-alt-loader=macdrv", "--wait-children",
                "--bottle=ltspice", _MAC_EXE]
    for cand in ("/Applications/LTspice.app/Contents/MacOS/LTspice",
                 r"C:\Program Files\ADI\LTspice\LTspice.exe",
                 "/mnt/c/Program Files/ADI/LTspice/LTspice.exe",      # WSL: the Windows install
                 shutil.which("ltspice") or "",
                 shutil.which("LTspice") or ""):
        if cand and Path(cand).exists():
            return [cand]
    raise FileNotFoundError(
        "No LTspice found. Set PWRLAB_LTSPICE to the full launch command."
    )


def is_wsl() -> bool:
    try:
        return "microsoft" in open("/proc/version").read().lower()
    except OSError:
        return False


def path_style(command=None) -> str:
    """How LTspice must be given file paths on this host:
    'native'  Windows: plain C:\\... paths
    'wsl'     Linux under WSL running the Windows LTspice.exe: wslpath -w
    'wine'    macOS / Linux running LTspice under Wine: Z:/... paths
    PWRLAB_PATH_STYLE overrides."""
    env = os.environ.get("PWRLAB_PATH_STYLE")
    if env in ("native", "wsl", "wine"):
        return env
    if os.name == "nt":
        return "native"
    exe = str(command[-1]) if command else ""
    if is_wsl() and exe.lower().endswith(".exe") and exe.startswith("/mnt/"):
        return "wsl"
    return "wine"


def _mnt_to_windows(posix: str) -> str:
    """/mnt/c/Users/x/run.net -> C:\\Users\\x\\run.net (fallback when wslpath is missing)."""
    m = re.match(r"^/mnt/([a-zA-Z])/(.*)$", posix)
    if not m:
        return posix
    return m.group(1).upper() + ":\\" + m.group(2).replace("/", "\\")


def win_path(p, style="wine") -> str:
    """The netlist path in the form LTspice can open (see path_style)."""
    p = Path(p).resolve()
    if style == "native":
        return str(p)
    if style == "wsl":
        try:
            return subprocess.run(["wslpath", "-w", str(p)], capture_output=True, text=True,
                                  check=True).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            return _mnt_to_windows(p.as_posix())
    return "Z:" + p.as_posix()


# Measured on an M3 Pro (11 cores) with a 6-point DAB sweep:
#   1 worker 12.5 s | 2 -> 7.5 s | 3 -> 4.2 s | 4 -> 14.5 s | 6 -> 19-21 s
# Wine/Rosetta contention makes >3 concurrent runs slower than sequential.
MAX_WORKERS = int(os.environ.get("PWRLAB_MAX_WORKERS", "3"))
DEFAULT_TIMEOUT = 600


# --------------------------------------------------------------------------- #
# logs
# --------------------------------------------------------------------------- #
def read_log(path) -> str:
    """Read a .log, handling UTF-16 BOMs (older LTspice builds emit UTF-16LE)."""
    raw = Path(path).read_bytes()
    for bom, enc in ((b"\xff\xfe", "utf-16-le"), (b"\xfe\xff", "utf-16-be"),
                     (b"\xef\xbb\xbf", "utf-8-sig")):
        if raw.startswith(bom):
            return raw.decode(enc, errors="replace")
    head = raw[:512]
    if head.count(b"\x00") > len(head) // 4:
        return raw.decode("utf-16-le", errors="replace")
    return raw.decode("utf-8", errors="replace")


_NUM = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"


def parse_meas(log_text: str, names) -> dict:
    """Extract .meas results. LTspice writes `name: expr=VALUE at T` or `name=VALUE`."""
    out = {n: None for n in names}
    for line in log_text.splitlines():
        s = line.strip()
        for n in names:
            if out[n] is not None:
                continue
            m = re.match(rf"{re.escape(n)}\s*[:=]", s, re.IGNORECASE)
            if not m:
                continue
            m2 = re.search(rf"=\s*({_NUM})", s[m.end() - 1:])
            if m2:
                out[n] = float(m2.group(1))
    return out


_ERR_RE = re.compile(
    r"\b(error|fatal|failed|singular|too many|could not|convergence)\b", re.IGNORECASE)
_BENIGN = re.compile(r"floating|ignoring|deprecated", re.IGNORECASE)


def log_problems(log_text: str) -> list[str]:
    out = []
    for line in log_text.splitlines():
        s = line.strip()
        if _ERR_RE.search(s) and not _BENIGN.search(s):
            out.append(s)
    return out


# --------------------------------------------------------------------------- #
# results
# --------------------------------------------------------------------------- #
@dataclass
class SimResult:
    params: dict
    meas: dict = field(default_factory=dict)
    returncode: int | None = None
    timed_out: bool = False
    wall_s: float = 0.0
    solver_s: float | None = None
    raw_path: Path | None = None
    log_path: Path | None = None
    raw_bytes: int = 0
    problems: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Artefacts must EXIST. A zero return code proves nothing on macOS."""
        return (not self.timed_out
                and self.raw_path is not None and self.raw_path.exists()
                and self.log_path is not None and self.log_path.exists()
                and not self.problems)

    @property
    def why_not_ok(self) -> str:
        if self.timed_out:
            return f"timed out after {self.wall_s:.0f} s"
        if self.raw_path is None or not self.raw_path.exists():
            hint = ("rc=0 with no .raw is the signature of the macOS stub, which "
                    "discards argv" if self.returncode == 0 else
                    "non-zero rc usually means a netlist syntax error; check the .log")
            return f"no .raw written (rc={self.returncode}) -- {hint}"
        if self.log_path is None or not self.log_path.exists():
            return "no .log written"
        if self.problems:
            return "simulator reported: " + "; ".join(self.problems[:3])
        return ""


# --------------------------------------------------------------------------- #
# running
# --------------------------------------------------------------------------- #
class Runner:
    """Runs netlists. One scratch dir per run: LTspice writes .raw/.log next to
    the netlist and takes no output-path argument, so concurrent runs of the same
    file would collide."""

    def __init__(self, command=None, timeout=DEFAULT_TIMEOUT, max_workers=MAX_WORKERS,
                 workroot=None, keep=False):
        self.command = command or default_command()
        self.timeout = timeout
        self.max_workers = max_workers
        self.keep = keep
        self.path_style = path_style(self.command)
        root = workroot or os.environ.get("PWRLAB_WORKROOT")   # WSL: on /mnt/c for the Windows LTspice
        self.workroot = Path(root) if root else Path(tempfile.mkdtemp(prefix="pwrlab_"))
        self.workroot.mkdir(parents=True, exist_ok=True)

    def run_text(self, netlist_text: str, params: dict, measurements,
                 tag: str = "run", suffix: str = ".net") -> SimResult:
        wd = self.workroot / tag
        wd.mkdir(parents=True, exist_ok=True)
        net = wd / f"{tag}{suffix}"
        net.write_text(netlist_text)
        return self.run_file(net, params, measurements)

    def run_file(self, netlist: Path, params: dict, measurements) -> SimResult:
        netlist = Path(netlist)
        wd = netlist.parent
        cmd = self.command + ["-Run", "-b", win_path(netlist, self.path_style)]
        res = SimResult(params=dict(params))

        t0 = time.perf_counter()
        proc = subprocess.Popen(cmd, cwd=wd, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, start_new_session=os.name != "nt")
        try:
            proc.wait(timeout=self.timeout)
            res.returncode = proc.returncode
        except subprocess.TimeoutExpired:
            if os.name == "nt":      # Windows: kill LTspice and its children
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
            else:                    # kill the process GROUP: wineserver children outlive a plain kill
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            proc.wait()
            res.timed_out = True
        res.wall_s = time.perf_counter() - t0

        raw, log = netlist.with_suffix(".raw"), netlist.with_suffix(".log")
        res.raw_path = raw if raw.exists() else None
        res.log_path = log if log.exists() else None
        res.raw_bytes = raw.stat().st_size if raw.exists() else 0

        if log.exists():
            text = read_log(log)
            res.meas = parse_meas(text, measurements)
            res.problems = log_problems(text)
            m = re.search(r"Total elapsed time:\s*([\d.]+)", text)
            res.solver_s = float(m.group(1)) if m else None
        return res

    def map(self, jobs, progress=None) -> list[SimResult]:
        """jobs: list of (tag, netlist_text, params, measurements, suffix)."""
        results: list[SimResult] = [None] * len(jobs)  # type: ignore
        nw = max(1, min(len(jobs), self.max_workers))
        done = 0
        with cf.ThreadPoolExecutor(max_workers=nw) as pool:
            futs = {pool.submit(self.run_text, j[1], j[2], j[3], j[0],
                                j[4] if len(j) > 4 else ".net"): i
                    for i, j in enumerate(jobs)}
            for fut in cf.as_completed(futs):
                i = futs[fut]
                results[i] = fut.result()
                done += 1
                if progress:
                    progress(done, len(jobs), results[i])
        return results

    def cleanup(self):
        if not self.keep:
            shutil.rmtree(self.workroot, ignore_errors=True)


# --------------------------------------------------------------------------- #
# scratch retention
# --------------------------------------------------------------------------- #
def dir_size(path) -> int:
    p = Path(path)
    if not p.exists():
        return 0
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())


def find_run_dirs(root) -> list:
    """Directories that actually hold simulation output, at any depth.

    Pruning only top-level entries silently does nothing whenever runs are
    nested one level down -- which is exactly what the test suites produce
    (work/bench/<case>/...). The observed failure was 316 MB in a single
    top-level directory surviving a 100 MB cap untouched.
    """
    root = Path(root)
    if not root.exists():
        return []
    seen = set()
    for f in root.rglob("*.raw"):
        seen.add(f.parent)
    for f in root.rglob("*.log"):
        seen.add(f.parent)
    # keep only the outermost directory of each run, not its children
    out = []
    for d in sorted(seen, key=lambda x: len(x.parts)):
        if not any(str(d).startswith(str(o) + os.sep) for o in out):
            out.append(d)
    return out


def prune_work(root, keep_recent: int = 5, max_bytes: int = 500_000_000,
               dry_run: bool = False) -> dict:
    """Keep scratch bounded: newest `keep_recent` run directories, under `max_bytes`.

    LTspice writes a .raw next to every netlist and these are megabytes each, so
    an unattended agent accumulates gigabytes fast -- this project reached 1 GB
    across 263 .raw files during development because nothing ever called
    Runner.cleanup(). The newest runs are kept because they are what a follow-up
    question ("now plot that") needs.
    """
    root = Path(root)
    runs = sorted(find_run_dirs(root), key=lambda d: d.stat().st_mtime,
                  reverse=True)
    removed, freed = [], 0
    total = dir_size(root)
    for i, d in enumerate(runs):
        # The newest `keep_recent` runs are never removed, whatever the size cap
        # says: a follow-up question ("now plot that", "show the waveform")
        # needs them, and letting the cap delete everything would make a single
        # large sweep un-inspectable.
        if i < keep_recent:
            continue
        if (total - freed) <= max_bytes:
            break
        size = dir_size(d)
        removed.append(str(d))
        freed += size
        if not dry_run:
            shutil.rmtree(d, ignore_errors=True)
    if not dry_run:
        # drop directories left empty by the removals
        for d in sorted(root.rglob("*"), key=lambda x: -len(x.parts)):
            if d.is_dir() and not any(d.iterdir()):
                d.rmdir()
    return {"removed": removed, "freed_bytes": freed,
            "remaining_bytes": dir_size(root)}
