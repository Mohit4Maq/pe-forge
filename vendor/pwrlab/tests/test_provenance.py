#!/usr/bin/env python3
"""Fermi W1: transient defaults, the JSON boundary, and provenance.

Written to FAIL against revision `pre-fermi` and pass after the fix, because a
test that has never failed has demonstrated nothing. The three defects it pins:

  A1  fault() watched hardcoded buck traces, so every other topology returned
      usable=false with empty metrics
  A2  Infinity was emitted into JSON, which is not JSON
  A4  results carried no revision identity, so nothing could be cited

Run:  .venv/bin/python tests/test_provenance.py
"""
import asyncio
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

fail = 0


def chk(label, cond, detail=""):
    global fail
    fail |= (not cond)
    print(f"  {'PASS' if cond else 'FAIL'}  {label:52s} {detail}")


def _strict(pairs):
    raise AssertionError("non-JSON constant in payload")


def payload(result, strict=True):
    """Parse a tool result, REFUSING NaN/Infinity when strict.

    json.loads accepts `Infinity` by default, which is exactly how A2 stayed
    invisible: a lenient parser turns it into a float and the agent reports it.
    parse_constant makes the payload's validity an assertion rather than a hope.
    """
    kw = {"parse_constant": _strict} if strict else {}
    if getattr(result, "structuredContent", None):
        # already parsed by the client; re-serialise to test what crossed the wire
        txt = json.dumps(result.structuredContent, allow_nan=True)
        return json.loads(txt, **kw)
    for c in result.content:
        if getattr(c, "type", "") == "text":
            return json.loads(c.text, **kw)
    return {}


def walk_floats(obj, path=""):
    if isinstance(obj, float):
        yield path, obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield from walk_floats(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from walk_floats(v, f"{path}[{i}]")


async def poll(session, job_id, timeout=900):
    for _ in range(int(timeout / 3)):
        r = payload(await session.call_tool("job_status", {"job_id": job_id}))
        if r.get("status") in ("done", "failed"):
            return r
        await asyncio.sleep(3)
    return {"status": "timeout"}


async def main():
    params = StdioServerParameters(
        command=str(ROOT / ".venv" / "bin" / "python"),
        args=["-m", "pwrlab.mcp_server"], env=None)

    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as session:
            await session.initialize()

            # ---------------------------------------------------------------- #
            print("A1: transient defaults derived from each topology's netlist")
            # Cheap, no simulation: every topology must name traces it saves.
            from pwrlab.cli import REGISTRY, _load_registry
            _load_registry()
            for name, cls in sorted(REGISTRY.items()):
                t = cls()
                if not hasattr(t, "default_watch"):
                    chk(f"{name}: default watch is actually saved", False,
                        "topology has no default_watch() at all")
                    continue
                watch, saved = t.default_watch(), set(t.saved_traces())
                ok = bool(watch) and all(x in saved for x in watch)
                chk(f"{name}: default watch is actually saved", ok, str(watch))

            # The expensive half: the default path must now WORK, not merely be
            # well-formed. pfc is the Fermi topology, so it is the one asserted.
            print("\n     load step on pfc with NO watch argument (was A1)")
            r1 = payload(await session.call_tool(
                "fault", {"topology": "pfc", "event": "loadstep",
                          "factor": 0.5, "at_s": 0.06}))
            job = await poll(session, r1["job_id"])
            res = (job.get("result") or {})
            chk("default-watch load step is usable", res.get("usable") is True,
                res.get("verdict", "")[:60])
            chk("it produced metrics", bool(res.get("metrics")),
                f"{len(res.get('metrics', {}))} metrics")

            # ---------------------------------------------------------------- #
            print("\nA2: the JSON boundary")
            # The same run previously emitted "I(Lb) settling s": Infinity.
            bad = [(p, v) for p, v in walk_floats(res) if not math.isfinite(v)]
            chk("no non-finite float anywhere in the payload", not bad, str(bad[:2]))
            notes = [k for k in res.get("metrics", {}) if k.endswith("__note")]
            chk("a null carries its reason instead of Infinity", True,
                f"{len(notes)} explained null(s)")

            # The assertion above only bites if the load step produced metrics
            # at all -- against pre-fermi it passed VACUOUSLY, because A1 had
            # already emptied the payload. So test the sanitiser DIRECTLY, with
            # an input containing exactly what used to leak out.
            from pwrlab.mcp_server import _json_safe
            dirty_in = {"settling_s": float("inf"), "ratio": float("nan"),
                        "nested": [{"x": float("-inf")}], "good": 1.5}
            clean = _json_safe(dirty_in)
            chk("sanitiser: Infinity -> null", clean["settling_s"] is None)
            chk("sanitiser: NaN -> null", clean["ratio"] is None)
            chk("sanitiser: recurses into lists and dicts",
                clean["nested"][0]["x"] is None)
            chk("sanitiser: leaves finite values untouched", clean["good"] == 1.5)
            chk("sanitiser: explains each null it created",
                clean.get("settling_s__note") is not None
                and clean.get("ratio__note") is not None)
            try:
                json.dumps(clean, allow_nan=False)
                chk("sanitiser output survives strict json.dumps", True)
            except ValueError as e:
                chk("sanitiser output survives strict json.dumps", False, str(e))

            # Every tool, not just fault.
            for tool_name, args in (("list_topologies", {}),
                                    ("list_devices", {}),
                                    ("assumptions", {"topology": "pfc"})):
                try:
                    p = payload(await session.call_tool(tool_name, args))
                    chk(f"{tool_name} payload is strict JSON", True)
                except AssertionError as e:
                    chk(f"{tool_name} payload is strict JSON", False, str(e))

            # ---------------------------------------------------------------- #
            print("\nA4: provenance on every payload")
            p = payload(await session.call_tool("list_topologies", {}))
            prov = p.get("provenance") or {}
            chk("provenance block present", bool(prov), str(list(prov))[:60])
            chk("names a model revision", bool(prov.get("model_version")),
                str(prov.get("model_version"))[:12])
            chk("declares a toolchain hash", bool(prov.get("toolchain_hash")),
                str(prov.get("toolchain_hash")))
            chk("states evidence eligibility",
                isinstance(prov.get("evidence_eligible"), bool),
                f"eligible={prov.get('evidence_eligible')}")
            if prov.get("dirty"):
                chk("a dirty tree explains why it is not evidence",
                    bool(prov.get("why_not_evidence")), "dirty tree")

            # ---------------------------------------------------------------- #
            print("\nAgent-facing surface: the tools another agent needs")
            tools = {t.name for t in (await session.list_tools()).tools}
            for needed in ("list_benchmarks", "benchmark", "check_netlist",
                           "validate_params"):
                chk(f"{needed} is exposed", needed in tools, "")

            b = payload(await session.call_tool("list_benchmarks", {}))
            chk("benchmarks are discoverable", bool(b.get("benchmarks")),
                ", ".join(sorted(b.get("benchmarks", {}))))
            chk("every agreement mode is documented",
                {"MATCH", "BOUNDED_OPTIMISM", "TREND", "SPEC_BOUND", "BRACKET"}
                <= set(b.get("how_to_read_a_result", {})), "")
            chk("the 'name the mode' rule travels with the data",
                "TREND" in (b.get("rule") or ""), "")
            for bid, meta in (b.get("benchmarks") or {}).items():
                chk(f"{bid} names its source document",
                    bool(meta.get("document") and meta.get("url")),
                    f"{meta.get('document')}")

            v = payload(await session.call_tool(
                "validate_params", {"topology": "pfc", "params": {"hyst": -0.5}}))
            chk("validate_params refuses a non-circuit",
                v.get("usable") is False, (v.get("verdict") or "")[:44])
            v = payload(await session.call_tool(
                "validate_params", {"topology": "pfc"}))
            chk("validate_params accepts a real one", v.get("usable") is True, "")

            c = payload(await session.call_tool(
                "check_netlist", {"topology": "ttype3ph",
                                  "params": {"deadtime": 2e-6}}))
            chk("check_netlist passes a sound netlist", c.get("usable") is True,
                (c.get("verdict") or "")[:40])
            chk("check_netlist reports the watch list it derived",
                bool(c.get("default_watch")), str(c.get("default_watch")))

            # ---------------------------------------------------------------- #
            print("\nBad input is refused with an actionable message")
            # A refusal arrives as an error result carrying text, not as JSON,
            # so read the text rather than trying to parse it.
            def as_text(res):
                parts = [getattr(c, "text", "") for c in (res.content or [])]
                return " ".join(parts)

            try:
                out = await session.call_tool(
                    "fault", {"topology": "pfc", "event": "loadstep",
                              "watch": ["V(nonexistent)"]})
                txt = as_text(out)
                errored = bool(getattr(out, "is_error", getattr(out, "isError", False)))
            except Exception as e:
                txt, errored = str(e), True

            chk("unsaved trace is refused", errored, "")
            chk("the refusal names what IS saved", "V(vo)" in txt, "")
            chk("the refusal names the offending trace",
                "nonexistent" in txt, "")
            chk("the refusal points at the working default",
                "Omit" in txt or "default" in txt.lower(), "")

    print("\n" + ("PROVENANCE + JSON BOUNDARY OK" if not fail
                  else "*** W1 REGRESSION ***"))
    return fail


if __name__ == "__main__":
    sys.exit(1 if asyncio.run(main()) else 0)
