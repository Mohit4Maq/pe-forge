"""forge status / gate / phase / module."""
import os

from . import state as S
from .phases import GATE_OF, GATE_REVIEWERS, LITE_REVIEWERS, MODULE_PHASES, PHASE, PHASES

ICON = {"todo": "·", "active": "▶", "in-review": "?", "passed": "✓", "skipped": "–"}


def _opt(rest, name):
    return rest[rest.index(name) + 1] if name in rest else None


def _theme():
    from . import ui
    return ui.Theme(color=ui.tty())


def status(prj, rest=()):
    st = S.load(prj)
    T = _theme()
    if "--banner" in rest:
        from . import ui
        cfg = prj.cfg.get("ui", {})
        for row in ui.banner(ui.Theme(cfg, color=T.color)):
            print(row)
    STY = {"passed": "ok", "active": "accent", "in-review": "warn"}
    paint_line = lambda s, line: T.paint(STY[s], line) if s in STY else line
    print(f"{prj.name}  ·  profile {prj.process.get('profile', 'full')}  ·  level {prj.level}  ·  schematic mode "
          f"{prj.process.get('schematic_mode', 'ask')}")
    for p in PHASES:
        s = st["phases"][p[0]]
        print(paint_line(s["status"], f"  {ICON[s['status']]} {p[0]} {p[2]}"
                         + (f"  [{p[1]} passed {s.get('date')}]" if s["status"] == "passed" else "")))
    if st["modules"]:
        print("Modules:")
        for m in st["modules"]:
            ph = " ".join(f"{k}{ICON[v['status']]}" for k, v in m["phases"].items())
            dep = f"  depends {', '.join(m['depends'])}" if m["depends"] else ""
            print(f"  {ICON[m['status']]} {m['id']} {m['name']:<28} {ph}{dep}")
    scope, ph, txt = S.next_action(st)
    print(f"Next: {txt}" + (f"  (skill {PHASE[ph][3]}, gate {GATE_OF[ph]})" if ph in PHASE else ""))
    return 0


def _target(st, rest):
    mid = _opt(rest, "--module")
    return (S.module(st, mid), mid) if mid else (None, None)


def phase(prj, rest):
    st = S.load(prj)
    if not rest or rest[0] != "start":
        print("forge phase start P<n> [--module M-xx]")
        return 1
    pid = rest[1]
    m, mid = _target(st, rest)
    if m:
        if pid not in MODULE_PHASES:
            raise SystemExit(f"{pid} is not a module phase ({', '.join(MODULE_PHASES)})")
        m["phases"][pid]["status"] = "active"
        m["status"] = "active"
        st["active_module"] = mid
    else:
        st["phases"][pid]["status"] = "active"
        st["phase"] = pid
    S.log(st, f"{mid or 'system'} {pid} started")
    S.save(prj, st)
    print(_theme().paint("accent", f"▶ {mid or 'system'} {pid} active"))
    return 0


def gate(prj, rest):
    from . import gates
    if len(rest) < 2:
        print("forge gate check|pass|fail G<n> [--module M-xx] [--note TEXT]")
        return 1
    act, g = rest[0], rest[1]
    st = S.load(prj)
    m, mid = _target(st, rest)
    pid = next(p for p, gg in GATE_OF.items() if gg == g)
    if act == "check":
        res = gates.check(prj, g, m)
        for ok, msg in res:
            print(("PASS " if ok else "FAIL ") + msg)
        rv = (LITE_REVIEWERS if prj.process.get("profile") == "lite" else GATE_REVIEWERS)[g]
        print(f"Reviewers for {g}: {', '.join(rv) or 'none'} (+ pe-compliance if the work touches insulation, "
              "protection, stored energy, grid or EMC)")
        ok = all(o for o, _ in res)
        print(f"{g} {'READY TO PASS' if ok else 'NOT READY'}")
        return 0 if ok else 1
    if act == "pass":
        res = gates.check(prj, g, m)
        bad = [msg for ok, msg in res if not ok]
        if bad and "--override" not in rest:
            print("cannot pass: " + "; ".join(bad))
            return 1
        if bad and prj.level == "strict":
            print("cannot pass: process level is strict, gates have no override (" + "; ".join(bad) + ")")
            return 1
        tgt = m["phases"][pid] if m else st["phases"][pid]
        tgt.update(status="passed", date=S.TODAY(), note=_opt(rest, "--note") or "")
        if bad:
            tgt["override"] = _opt(rest, "--note") or "owner override"
        if m and all(m["phases"][p]["status"] in ("passed", "skipped") for p in MODULE_PHASES):
            m["status"] = "passed"
        S.log(st, f"{mid or 'system'} {g} passed" + (" (OVERRIDE by owner)" if bad else ""))
        S.save(prj, st)
        print(_theme().paint("ok", f"✓ {mid or 'system'} {g} passed"))
        return 0
    if act == "fail":
        tgt = m["phases"][pid] if m else st["phases"][pid]
        tgt["status"] = "active"
        S.log(st, f"{mid or 'system'} {g} failed: {_opt(rest, '--note') or ''}")
        S.save(prj, st)
        return 0
    return 1


def module(prj, rest):
    st = S.load(prj)
    if not rest or rest[0] == "list":
        for m in st["modules"]:
            print(f"{m['id']} {m['name']} [{m['status']}] {m['dir']}")
        return 0
    if rest[0] == "add":
        dep = [d for d in (_opt(rest, "--depends") or "").split(",") if d]
        folder = _opt(rest, "--dir")
        if folder and not os.path.isdir(os.path.join(prj.root, folder)):
            raise SystemExit(f"--dir {folder}: no such folder in the project")
        m = S.add_module(prj, st, rest[1], _opt(rest, "--purpose") or "", dep, folder)
        S.save(prj, st)
        print(f"added {m['id']} → {m['dir']}")
        return 0
    if rest[0] in ("start", "done"):
        m = S.module(st, rest[1])
        m["status"] = "active" if rest[0] == "start" else "passed"
        st["active_module"] = m["id"] if rest[0] == "start" else None
        S.log(st, f"module {m['id']} {rest[0]}")
        S.save(prj, st)
        return 0
    return 1
