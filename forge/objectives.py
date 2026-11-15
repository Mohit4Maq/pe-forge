"""forge objectives: system efficiency and cost come first (Rule 16). Every design decision maximises efficiency
and minimises cost; an option that is not the best on either axis is a deviation, and a deviation stands only
when the owner has accepted it as a recorded decision.

  forge objectives                       targets and the status at each stage (trade, BoM, measured efficiency)
  forge objectives deviate "<what>" --gate G<n> [--axis efficiency|cost|both] [--why TEXT] [--recommend accept|revert]
                                         open the owner's decision for a deviation (an agent cannot accept it)

Targets, in project.toml:
  [objectives] efficiency_pct = 95       the minimum efficiency at rated load (the trade study and G5 measure it)
  [market] target_cost_usd = 40          the BoM cost target per unit (or [objectives] cost_usd)

What is checked (deterministic, the gates run it):
  G3  the topology trade has numbers for efficiency and cost in every option column, names the owner's choice,
      and that choice is the best on efficiency and the lowest on cost, else an accepted deviation D-xx
  G4  the WIP BoM roll-up (Σ Qty × Price at the volume break) is within the cost target, else an accepted deviation
  G5  the measured efficiency (ACCEPTED or PROVISIONAL result) meets efficiency_pct, else an accepted deviation
"""
import csv
import os
import re

TAG = "Deviation from the efficiency / cost rule"


def targets(prj):
    o = prj.cfg.get("objectives", {}) or {}
    cost = o.get("cost_usd", (prj.cfg.get("market", {}) or {}).get("target_cost_usd"))
    return {"efficiency_pct": _num(o.get("efficiency_pct")), "cost_usd": _num(cost)}


def _num(x):
    if x is None or x == "":
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        m = re.search(r"-?\d+(?:\.\d+)?", str(x))
        return float(m.group()) if m else None


# ---- deviations (owner decisions)

def deviations(prj):
    from . import decisions
    return [d for d in decisions.load(prj) if d["question"].startswith(TAG)]


def accepted(prj, axis, gate=None):
    """The accepted deviation records covering this axis (efficiency / cost), optionally at this gate."""
    out = []
    for d in deviations(prj):
        if d["status"] != "ANSWERED" or "accept" not in (d["answer"] or "").lower():
            continue
        ax = re.search(r"\((efficiency|cost|both)\)", d["question"])
        ax = ax.group(1) if ax else "both"
        if ax in (axis, "both") and (gate is None or d["gate"] == gate):
            out.append(d)
    return out


def deviate(prj, what, gate, axis="both", why="", recommend="revert to the best option", module="system"):
    from . import decisions
    q = f"{TAG} ({axis}): {what}"
    for d in decisions.load(prj):
        if d["question"] == q and d["status"] == "OPEN":
            return d
    return decisions.new(prj, q, gate, ["accept (reason recorded)", "revert to the best option"], recommend,
                         why or "Rule 16: every decision maximises efficiency and minimises cost unless the owner accepts a deviation", module)


def _tag(ds):
    return ", ".join(f"{d['id']} accepted" for d in ds)


# ---- G3: the topology trade

def trade_rows(text):
    """The markdown table: (headers, {row label: [cells]})."""
    lines = [l for l in text.splitlines() if l.strip().startswith("|")]
    if len(lines) < 2:
        return [], {}
    head = [c.strip() for c in lines[0].strip().strip("|").split("|")]
    rows = {}
    for l in lines[2:]:
        cells = [c.strip() for c in l.strip().strip("|").split("|")]
        if cells:
            rows[cells[0].strip("* ").lower()] = cells[1:]
    return head, rows


def trade_status(prj, path=None):
    """(ok, message, data): the chosen option's rank on efficiency and cost; None data when nothing to judge."""
    path = path or prj.p("architecture", "Topology_Trade.md")
    if not os.path.exists(path):
        return None, "no topology trade yet", None
    t = open(path, errors="ignore").read()
    head, rows = trade_rows(t)
    opts = [h for h in head[1:] if h and not re.match(r"source|benchmark|weight", h, re.I)]
    eff = next((v for k, v in rows.items() if "efficien" in k), None)
    cost = next((v for k, v in rows.items() if "cost" in k), None)
    if not opts or eff is None or cost is None:
        return False, "trade study has no efficiency and cost rows with option columns", None
    E = [_num(c) for c in eff[:len(opts)]]
    C = [_num(c) for c in cost[:len(opts)]]
    if any(x is None for x in E) or any(x is None for x in C) or len(E) < len(opts) or len(C) < len(opts):
        return False, "efficiency and cost cells must hold a number for every option (Rule 1 anchored)", None
    m = re.search(r"owner'?s choice:?\**\s*([^\n(|]+)", t, re.I)
    if not m or not m.group(1).strip() or m.group(1).strip().startswith(("…", "...", "_", "<")):
        return False, "the owner's choice is not stated (write **Owner's choice:** <option column> once decided)", None
    pick = m.group(1).strip().strip("*").strip()
    i = next((k for k, o in enumerate(opts) if o.lower() == pick.lower()), None)
    if i is None:
        i = next((k for k, o in enumerate(opts) if pick.lower() in o.lower() or o.lower() in pick.lower()), None)
    if i is None:
        return False, f"owner's choice '{pick}' is not one of the option columns ({', '.join(opts)})", None
    best_e, best_c = max(E), min(C)
    dev = []
    if E[i] < best_e:
        dev.append(("efficiency", f"{opts[i]} {E[i]:g} vs best {best_e:g} ({opts[E.index(best_e)]})"))
    if C[i] > best_c:
        dev.append(("cost", f"{opts[i]} {C[i]:g} vs lowest {best_c:g} ({opts[C.index(best_c)]})"))
    data = {"options": opts, "efficiency": E, "cost": C, "chosen": opts[i], "deviations": dev}
    if not dev:
        return True, f"owner's choice {opts[i]} is the most efficient ({E[i]:g}) and the lowest cost ({C[i]:g})", data
    missing = [ax for ax, _ in dev if not accepted(prj, ax)]
    acc = [d for ax, _ in dev for d in accepted(prj, ax)]
    msg = "; ".join(f"{ax}: {s}" for ax, s in dev)
    if missing:
        return False, f"owner's choice {opts[i]} deviates on {' and '.join(missing)} ({msg}): no accepted deviation (forge objectives deviate \"...\" --gate G3 --axis {'both' if len(missing) > 1 else missing[0]})", data
    return True, f"owner's choice {opts[i]} deviates ({msg}): {_tag(acc)}", data


# ---- G4: the BoM roll-up

def bom_cost(path):
    """(total, lines, basis): Σ Qty × Price_100 (Price_1 when no break price) over populated lines."""
    rows = list(csv.DictReader(open(path, newline="")))
    tot, n, b1 = 0.0, 0, 0
    for r in rows:
        if (r.get("DNP") or "").strip().lower() in ("yes", "y", "1"):
            continue
        q = _num(r.get("Qty")) or 0
        p = _num(r.get("Price_100"))
        if p is None:
            p = _num(r.get("Price_1"))
            b1 += 1 if p is not None else 0
        if p is None:
            continue
        tot += q * p
        n += 1
    return tot, n, b1


def bom_status(prj, path=None):
    tg = targets(prj)["cost_usd"]
    path = path or prj.p("bom_csv")
    if not os.path.exists(path):
        return None, "no BoM yet", None
    tot, n, b1 = bom_cost(path)
    if not n:
        return None, "BoM has no priced line yet", None
    basis = f"{n} priced line(s)" + (f", {b1} at the qty-1 price" if b1 else "")
    if tg is None:
        return True, f"BoM roll-up {tot:.2f} USD ({basis}); no cost target set ([market] target_cost_usd)", {"total": tot}
    if tot <= tg:
        return True, f"BoM roll-up {tot:.2f} USD within the target {tg:g} USD ({basis})", {"total": tot, "target": tg}
    acc = accepted(prj, "cost")
    if acc:
        return True, f"BoM roll-up {tot:.2f} USD over the target {tg:g} USD: {_tag(acc)}", {"total": tot, "target": tg}
    return False, f"BoM roll-up {tot:.2f} USD over the target {tg:g} USD ({basis}): cost down, raise the target with a reason, or forge objectives deviate \"...\" --gate G4 --axis cost", {"total": tot, "target": tg}


# ---- G5: the measured efficiency

def measured_efficiency(prj):
    from . import results
    best = None
    for d in results.load_results(prj):
        if d.get("grade") not in ("ACCEPTED", "PROVISIONAL"):
            continue
        for m in d.get("metrics") or []:
            if not re.search(r"efficien", m.get("name", ""), re.I):
                continue
            vals = [v for v in (m.get("values") or []) if isinstance(v, (int, float))]
            if not vals:
                continue
            v = max(vals)
            if v <= 1.0:
                v *= 100
            if best is None or v > best[0]:
                best = (v, d.get("id") or d.get("file"))
    return best


def efficiency_status(prj):
    tg = targets(prj)["efficiency_pct"]
    if tg is None:
        return True, "no efficiency target set ([objectives] efficiency_pct)", None
    best = measured_efficiency(prj)
    if best is None:
        return None, f"efficiency target {tg:g} %: no accepted efficiency result yet", None
    v, src = best
    if v >= tg:
        return True, f"measured efficiency {v:.2f} % meets the target {tg:g} % ({src})", {"value": v}
    acc = accepted(prj, "efficiency")
    if acc:
        return True, f"measured efficiency {v:.2f} % below the target {tg:g} % ({src}): {_tag(acc)}", {"value": v}
    return False, f"measured efficiency {v:.2f} % below the target {tg:g} % ({src}): improve the design, or forge objectives deviate \"...\" --gate G5 --axis efficiency", {"value": v}


# ---- gates and check

def gate_items(prj, gate, mod=None):
    out = []
    if gate == "G3":
        ok, msg, _ = trade_status(prj, os.path.join(prj.root, mod["dir"], "Topology_Trade.md") if mod else None)
        if ok is not None:
            out.append((ok, "efficiency / cost first (Rule 16): " + msg))
    if gate == "G4":
        ok, msg, _ = bom_status(prj, os.path.join(prj.root, mod["dir"], "BOM.csv") if mod else None)
        if ok is not None:
            out.append((ok, "cost target (Rule 16): " + msg))
    if gate in ("G5", "G8"):
        ok, msg, _ = efficiency_status(prj)
        if ok is not None:
            out.append((ok, "efficiency target (Rule 16): " + msg))
    return out


def as_check(prj, opts):
    from .check import finding, result
    F, seen = [], 0
    for (ok, msg, _), where in ((trade_status(prj), "04_Hardware/Architecture/Topology_Trade.md"),
                                (bom_status(prj), "04_Hardware/BOM/WIP_BOM.csv"),
                                (efficiency_status(prj), "06_Simulations/Results")):
        if ok is None:
            continue
        seen += 1
        if not ok:
            F.append(finding("efficiency and cost first, deviations accepted by the owner (Rule 16)", msg, where,
                             "best option, cost down, or forge objectives deviate"))
    if not seen:
        return result("skip", reason="no trade study, BoM or efficiency result yet")
    return result("warn" if F else "pass", F, reason=f"{seen} stage(s) judged")


def main(prj, argv):
    if argv[:1] == ["deviate"]:
        what = argv[1] if len(argv) > 1 and not argv[1].startswith("--") else ""
        if not what:
            print("forge objectives deviate \"<what and why it is better>\" --gate G<n> [--axis efficiency|cost|both]")
            return 1
        g = argv[argv.index("--gate") + 1] if "--gate" in argv else "G3"
        ax = argv[argv.index("--axis") + 1] if "--axis" in argv else "both"
        why = argv[argv.index("--why") + 1] if "--why" in argv else ""
        rec = argv[argv.index("--recommend") + 1] if "--recommend" in argv else "revert to the best option"
        mod = argv[argv.index("--module") + 1] if "--module" in argv else "system"
        d = deviate(prj, what, g, ax, why, rec, mod)
        print(f"{d['id']} opened for the owner at {g} ({ax}): they answer with forge decision answer {d['id']} \"accept ...\" or \"revert ...\"")
        return 0
    if argv and argv[0] not in ("status", "--json"):
        print(__doc__)
        return 1
    tg = targets(prj)
    print(f"targets: efficiency >= {tg['efficiency_pct'] or '-'} %, cost <= {tg['cost_usd'] or '-'} USD")
    worst = 0
    for name, (ok, msg, _) in (("trade", trade_status(prj)), ("BoM", bom_status(prj)), ("efficiency", efficiency_status(prj))):
        print(("  -    " if ok is None else "  PASS " if ok else "  FAIL ") + f"{name:10s} {msg}")
        worst = max(worst, 0 if ok is not False else 1)
    ds = deviations(prj)
    for d in ds:
        print(f"  {d['id']} {d['status']}: {d['question'][len(TAG) + 1:]}" + (f" -> {d['answer']}" if d["answer"] else ""))
    return worst
