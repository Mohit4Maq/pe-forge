"""forge decision: the questions only the customer / owner can answer, and the gates that wait for them.

  forge decision new "<question>" --gate G<n> [--options "a | b | c"] [--recommend "a"] [--why TEXT] [--module M-xx]
  forge decision list [--open] [--json]
  forge decision answer D-nn "<answer>" [--note TEXT]        the owner, at a terminal (an agent cannot answer for them)
  forge decision withdraw D-nn --why TEXT                    the question no longer applies (recorded, not deleted)

Why: the tool must not guess where the design can go two ways (the input connector family, the enclosure's IP
code, a part swap that changes cost, a waiver of a Must rule). It writes the question here with its options,
a recommendation and the consequence of each; `forge gate check G<n>` refuses while any decision tagged to that
gate (or an earlier one) is OPEN. The owner answers in their own terminal (the same rule as cr approve); the
answer is recorded with the date and copied into Master_Plan.md section 2 (D-xx) so the plan stays the record.

Stored in .forge/decisions.json (committed with the project).
"""
import datetime as dt
import json
import os
import re

FILE = os.path.join(".forge", "decisions.json")
ORDER = ["G0", "G1", "G2", "G3", "G4", "G5", "G6", "G7", "GE", "G8"]


def load(prj):
    try:
        return json.load(open(os.path.join(prj.root, FILE)))
    except (OSError, ValueError):
        return []


def save(prj, d):
    p = os.path.join(prj.root, FILE)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    json.dump(d, open(p, "w"), indent=1)


def new(prj, question, gate, options=(), recommend="", why="", module="system"):
    d = load(prj)
    n = max([int(x["id"].split("-")[1]) for x in d] or [0]) + 1
    rec = {"id": f"D-{n:02d}", "question": question, "gate": gate, "module": module, "options": list(options),
           "recommend": recommend, "why": why, "status": "OPEN", "opened": dt.date.today().isoformat(),
           "answer": "", "answered": "", "note": ""}
    d.append(rec)
    save(prj, d)
    _plan(prj, rec)
    return rec


def _plan(prj, rec):
    """Append / update the D-xx row in Master_Plan.md section 2 (owner decisions)."""
    plan = prj.p("plan")
    if not os.path.exists(plan):
        return
    t = open(plan).read()
    row = (f"| {rec['id']} | {rec['question'].replace('|', '/')} | {' / '.join(rec['options']).replace('|', '/') or '-'} | "
           f"{rec['recommend'] or '-'} | {rec['gate']} | {rec['status']}" + (f": {rec['answer']}" if rec["answer"] else "") + " |")
    if re.search(rf"(?m)^\| {rec['id']} \|", t):
        t = re.sub(rf"(?m)^\| {rec['id']} \|.*$", row.replace("\\", "\\\\"), t)
    else:
        m = re.search(r"(?m)^## 2\. Owner decisions.*\n(?:.*\n)*?\|---.*\n", t)
        if m:
            t = t[:m.end()] + row + "\n" + t[m.end():]
        else:
            t = t.rstrip("\n") + "\n\n## 2. Owner decisions (D-xx)\n| ID | Question | Options | Recommended | Gate | Status |\n|---|---|---|---|---|---|\n" + row + "\n"
    open(plan, "w").write(t)


def open_for(prj, gate, module="system"):
    """Decisions that block this gate: OPEN ones tagged to it or to an earlier gate, for this module or the system."""
    g = ORDER.index(gate) if gate in ORDER else len(ORDER)
    return [x for x in load(prj) if x["status"] == "OPEN" and x["gate"] in ORDER and ORDER.index(x["gate"]) <= g
            and x["module"] in (module, "system")]


def gate_items(prj, gate, mod=None):
    want = (mod or {}).get("id", "system")
    o = open_for(prj, gate, want)
    return [(not o, f"owner decisions: {len(o)} open" + (": " + "; ".join(f"{x['id']} {x['question'][:60]}" for x in o[:3])
                                                        + " (forge decision list --open; the owner answers)" if o else ""))]


def answer(prj, did, text, note=""):
    from . import changes
    if changes.agent_context():
        raise SystemExit(f"{did} is the owner's to answer. Run in your own terminal:\n  ! forge decision answer {did} \"<answer>\"")
    d = load(prj)
    rec = next((x for x in d if x["id"] == did), None)
    if not rec:
        raise SystemExit(f"no decision {did}")
    if rec["status"] != "OPEN":
        raise SystemExit(f"{did} is {rec['status']}")
    rec.update(status="ANSWERED", answer=text, answered=dt.date.today().isoformat(), note=note)
    save(prj, d)
    _plan(prj, rec)
    return rec


def withdraw(prj, did, why):
    d = load(prj)
    rec = next((x for x in d if x["id"] == did), None)
    if not rec:
        raise SystemExit(f"no decision {did}")
    rec.update(status="WITHDRAWN", note=why, answered=dt.date.today().isoformat())
    save(prj, d)
    _plan(prj, rec)
    return rec


def _opt(argv, k, default=None):
    return argv[argv.index(k) + 1] if k in argv else default


def main(prj, argv):
    if argv[:1] == ["new"] and len(argv) > 1:
        gate = _opt(argv, "--gate")
        if not gate:
            raise SystemExit("--gate G<n> is required: which gate waits for this answer")
        opts = [o.strip() for o in (_opt(argv, "--options") or "").split("|") if o.strip()]
        rec = new(prj, argv[1], gate, opts, _opt(argv, "--recommend", ""), _opt(argv, "--why", ""), _opt(argv, "--module", "system"))
        print(f"{rec['id']} opened for {gate}: {rec['question']}\n  options: {' | '.join(opts) or '-'}  recommended: {rec['recommend'] or '-'}"
              f"\n  the owner answers: forge decision answer {rec['id']} \"<answer>\"")
        return 0
    if argv[:1] == ["answer"] and len(argv) > 2:
        rec = answer(prj, argv[1], argv[2], _opt(argv, "--note", ""))
        print(f"{rec['id']} answered: {rec['answer']}")
        return 0
    if argv[:1] == ["withdraw"] and len(argv) > 1:
        rec = withdraw(prj, argv[1], _opt(argv, "--why", ""))
        print(f"{rec['id']} withdrawn")
        return 0
    d = load(prj)
    if "--open" in argv:
        d = [x for x in d if x["status"] == "OPEN"]
    if "--json" in argv:
        print(json.dumps(d, indent=1))
        return 0
    if not d:
        print("no decisions recorded")
        return 0
    for x in d:
        print(f"{x['id']} [{x['status']}] {x['gate']} {x['module']}: {x['question']}")
        if x["options"]:
            print(f"    options: {' | '.join(x['options'])}" + (f"   recommended: {x['recommend']}" if x["recommend"] else ""))
        if x["why"]:
            print(f"    why: {x['why']}")
        if x["answer"]:
            print(f"    answer ({x['answered']}): {x['answer']}" + (f" ({x['note']})" if x["note"] else ""))
    return 0
