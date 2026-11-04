#!/usr/bin/env python3
"""Drive the MCP server as a real client does: over stdio, through the protocol.

Calling the decorated functions directly would prove nothing about whether the
server actually works -- it would skip the transport, the schema generation and
the JSON round trip, which is where MCP servers usually break.
"""
import asyncio
import json
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
    print(f"  {'PASS' if cond else 'FAIL'}  {label:50s} {detail}")


def payload(result):
    if getattr(result, "structuredContent", None):
        return result.structuredContent
    for c in result.content:
        if getattr(c, "type", "") == "text":
            try:
                return json.loads(c.text)
            except json.JSONDecodeError:
                return {"text": c.text}
    return {}


async def poll(session, job_id, timeout=600):
    for _ in range(int(timeout / 3)):
        r = payload(await session.call_tool("job_status", {"job_id": job_id}))
        if r.get("status") in ("done", "failed"):
            return r
        await asyncio.sleep(3)
    return {"status": "timeout"}


async def main():
    params = StdioServerParameters(
        command=str(ROOT / ".venv/bin/python"),
        args=["-m", "pwrlab.mcp_server"], cwd=str(ROOT))
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as session:
            await session.initialize()

            print("Protocol")
            tools = await session.list_tools()
            names = {t.name for t in tools.tools}
            chk("server handshakes and lists tools", len(names) >= 8,
                f"{len(names)} tools: {', '.join(sorted(names))}")
            need = {"list_topologies", "analyze", "sweep", "tolerance",
                    "losses", "fault", "job_status", "doctor", "list_devices"}
            chk("all expected tools present", need <= names,
                f"missing: {need - names or 'none'}")
            an = next(t for t in tools.tools if t.name == "analyze")
            props = set(an.input_schema.get("properties", {}))
            chk("analyze exposes a usable schema",
                {"topology", "params"} <= props, f"properties: {sorted(props)}")

            print("\nDiscovery")
            t = payload(await session.call_tool("list_topologies", {}))
            chk("lists all 8 topologies", len(t.get("topologies", {})) == 8,
                ", ".join(sorted(t.get("topologies", {}))))

            print("\nA valid design must be reported as valid")
            a = payload(await session.call_tool(
                "analyze", {"topology": "dab", "params": {"phi_deg": 45},
                            "converge": False}))
            chk("usable=true for a known-good DAB point", a.get("usable") is True,
                a.get("verdict", "")[:60])
            err = a.get("error_vs_theory_pct", {})
            chk("power within 0.2 % of closed form",
                abs(err.get("P_in", 99)) < 0.2, f"{err.get('P_in')} %")
            chk("checks are returned, not just numbers",
                len(a.get("checks", [])) >= 8, f"{len(a.get('checks',[]))} checks")

            print("\nAn INVALID design must be reported as invalid")
            # Nsettle=0 measures during the startup transient
            b = payload(await session.call_tool(
                "analyze", {"topology": "dab",
                            "params": {"phi_deg": 45, "Nsettle": 0, "Rdamp": 5.0},
                            "converge": False}))
            chk("usable=false for a deliberately broken point",
                b.get("usable") is False, b.get("verdict", "")[:70])
            chk("verdict says plainly the numbers must not be used",
                "NOT USABLE" in b.get("verdict", ""), "")
            chk("warnings explain why", len(b.get("warnings", [])) > 0,
                (b.get("warnings") or [""])[0][:60])

            print("\nErrors are actionable, not stack traces")
            e = payload(await session.call_tool(
                "analyze", {"topology": "nonexistent"}))
            txt = json.dumps(e)
            chk("unknown topology names the valid options", "dab" in txt, "")
            e2 = payload(await session.call_tool(
                "analyze", {"topology": "buck", "params": {"nonsense": 1}}))
            chk("unknown parameter names the valid ones",
                "Valid:" in json.dumps(e2) or "no parameter" in json.dumps(e2), "")

            print("\nBackground jobs")
            j = payload(await session.call_tool(
                "sweep", {"topology": "buck", "grid": {"D": [0.3, 0.5, 0.7]}}))
            chk("sweep returns a job id immediately", "job_id" in j,
                j.get("job_id", ""))
            res = await poll(session, j["job_id"])
            chk("sweep job completes", res.get("status") == "done",
                res.get("status", ""))
            sr = res.get("result", {})
            chk("all sweep points validated", sr.get("usable") is True,
                sr.get("verdict", "")[:60])
            chk("sweep returns per-point rows", len(sr.get("rows", [])) == 3,
                f"{len(sr.get('rows',[]))} rows")

            print("\nTolerance, losses, faults")
            j = payload(await session.call_tool(
                "tolerance", {"topology": "buck", "mode": "sensitivity",
                              "tolerances": {"L": 0.2, "Rload": 0.05},
                              "outputs": ["I_L_ripple", "I_L_rms"]}))
            res = await poll(session, j["job_id"])
            s = {(d["output"], d["param"]): d["S"]
                 for d in res.get("result", {}).get("sensitivities", [])}
            chk("sensitivity dI/dL matches 1/L physics",
                abs(s.get(("I_L_ripple", "L"), 0) + 1) < 0.05,
                f"S = {s.get(('I_L_ripple','L'))}")

            j = payload(await session.call_tool(
                "losses", {"topology": "dab", "device": "SiC_1200V_40m",
                           "rth_heatsink": 0.2}))
            res = await poll(session, j["job_id"])
            L = res.get("result", {})
            chk("loss analysis converges", L.get("converged") is True,
                f"{L.get('total_device_loss_W')} W, "
                f"Tj {L.get('hottest_junction_C')} C")
            chk("device caveat is carried in the payload",
                "representative" in L.get("device_library_caveat", "").lower(), "")

            j = payload(await session.call_tool(
                "fault", {"topology": "buck", "event": "short", "at_s": 1.5e-3,
                          "tstop_s": 2.5e-3, "watch": ["I(L1)"],
                          "limit_hi": 100.0}))
            res = await poll(session, j["job_id"])
            F = res.get("result", {})
            chk("short circuit is flagged as exceeding its limit",
                F.get("usable") is False, F.get("verdict", "")[:60])

    print("\n" + ("MCP SERVER OK" if not fail else "*** MCP SERVER PROBLEM ***"))
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
