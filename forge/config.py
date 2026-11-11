"""Project discovery and configuration (project.toml at the project root). Standard library only."""
import os
import subprocess
import sys

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    sys.exit("pe-forge needs Python 3.11 or newer (tomllib)")

FORGE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # the pe-forge repo / plugin root
def inside(path, root):
    """path relative to root with forward slashes, or None when it is not inside root. Case-insensitive and
    separator-agnostic on Windows (Claude Code hands hooks C:\\... paths there)."""
    p, r = os.path.realpath(path), os.path.realpath(root)
    if os.path.normcase(p) != os.path.normcase(r) and not os.path.normcase(p).startswith(os.path.normcase(r) + os.sep):
        return None
    return os.path.relpath(p, r).replace(os.sep, "/")


def interactive(stream=None):
    """True only when `stream` (default stdin) is a real console a person types into. isatty() is not enough on
    Windows: the NUL device reports as a terminal there, so an unattended agent would look like the owner.
    On Windows, GetConsoleMode succeeds only for a real console handle."""
    import sys
    s = stream or sys.stdin
    try:
        if not s or not s.isatty():
            return False
    except (AttributeError, ValueError):
        return False
    if os.name != "nt":
        return True
    try:
        import ctypes
        import msvcrt
        h = msvcrt.get_osfhandle(s.fileno())
        mode = ctypes.c_uint32()
        return bool(ctypes.windll.kernel32.GetConsoleMode(ctypes.c_void_p(h), ctypes.byref(mode)))
    except Exception:
        return False


LEVELS = ("light", "standard", "strict")
LIBRARY = os.environ.get("MC_LIB") or os.path.join(FORGE_ROOT, "library")
TEMPLATE = os.path.join(FORGE_ROOT, "template")

# Folder roles. Projects may remap any of them in project.toml [paths]; tools only use these keys.
DEFAULT_PATHS = {
    "inbox": "00_Inbox",
    "program": "01_Program",
    "plan": "01_Program/Master_Plan.md",
    "qc_register": "01_Program/QC/QC_Register.md",
    "reviews": "01_Program/QC/Reviews",
    "decisions": "01_Program/Decisions",
    "council": "01_Program/Decisions/Council",
    "registry": "01_Program/Registry",
    "requirements": "02_Requirements",
    "sys_req": "02_Requirements/System_Requirements.md",
    "vmodel": "02_Requirements/V_Model",
    "standards": "02_Requirements/Standards_and_Compliance.md",
    "foundation": "03_Foundation",
    "market": "03_Foundation/Market_Survey",
    "literature": "03_Foundation/Literature",
    "lit_notes": "03_Foundation/Lit_Notes",
    "ref_design": "03_Foundation/Ref_Design",
    "background": "03_Foundation/Background_Study.md",
    "hardware": "04_Hardware",
    "architecture": "04_Hardware/Architecture",
    "bom_csv": "04_Hardware/BOM/WIP_BOM.csv",
    "bom_md": "04_Hardware/BOM/WIP_BOM.md",
    "briefs": "04_Hardware/Schematic_Briefs",
    "booklets": "04_Hardware/Design_Booklets",
    "datasheets": "04_Hardware/Datasheets",
    "firmware": "05_Firmware",
    "frs": "05_Firmware/Firmware_Requirements_Spec.md",
    "sims": "06_Simulations",
    "sim_results": "06_Simulations/Results",
    "mechanical": "07_Mechanical",
    "pcb": "08_PCB",
    "release": "09_Release",
    "reliability": "12_Reliability",
    "validation": "13_Validation",
    "templates": "90_Templates",
    "modules": "modules",
    "tools_data": ".forge",
    "superseded_terms": ".forge/superseded_terms.csv",
    "consistency_allow": ".forge/consistency_allow.csv",
    "dvp": "13_Validation/DVP_and_R.md",
    "layout_spec": "04_Hardware/Layout/Layout_Spec.md",
}


def find_root(start=None):
    d = os.path.abspath(start or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    while True:
        if os.path.isfile(os.path.join(d, "project.toml")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


class Project:
    def __init__(self, root=None):
        self.root = root or find_root()
        if not self.root:
            raise SystemExit("not inside a pe-forge project (no project.toml found). Start one with /pe-new.")
        with open(os.path.join(self.root, "project.toml"), "rb") as fh:
            self.cfg = tomllib.load(fh)
        self.paths = dict(DEFAULT_PATHS, **self.cfg.get("paths", {}))

    def p(self, key, *more):
        return os.path.join(self.root, self.paths[key], *more)

    def rel(self, path):
        return os.path.relpath(path, self.root).replace(os.sep, "/")     # the same on Windows as elsewhere

    @property
    def name(self):
        return self.cfg["project"]["name"]

    @property
    def process(self):
        return self.cfg.get("process", {})

    @property
    def level(self):
        """How firmly the process is enforced: light (advise, never block; boards are not released) | standard
        (the default: obligations block, gates as written) | strict (also warnings fail, no gate overrides).
        project.toml [process] level; see forge/level.py."""
        lv = self.process.get("level", "standard")
        return lv if lv in LEVELS else "standard"

    def git(self, *a):
        return subprocess.run(["git", "-C", self.root, *a], capture_output=True, text=True).stdout


def project_or_none():
    r = find_root()
    return Project(r) if r else None
