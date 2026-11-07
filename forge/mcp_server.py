"""forge-mcp: pe-forge over MCP, for any MCP host (Claude Desktop, Cursor, Codex, another agent).

Only outcome-level tools (forge/mcp_tools.py): status, check, explain, gate check, drift, schematic check / apply,
change request new / status. There is no generic file-edit tool, no gate pass and no CR approval, so a host agent
cannot route around verification or the owner. Needs the `mcp` package (installed with pwrlab by bootstrap.sh).

Run:  bin/forge-mcp      (bootstrap.sh registers it with Claude Code as the MCP server "forge")
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp.server.mcpserver import MCPServer  # noqa: E402

from forge import mcp_tools  # noqa: E402

mcp = MCPServer(
    name="forge",
    instructions=(
        "pe-forge: gated power-electronics design projects (KiCad 10). Every tool takes `project`: a path inside the "
        "project (the folder holding project.toml). Results follow {usable, verdict, checks, warnings}: report the "
        "verdict; a check that did not run is reported as skipped with its reason, never as passed.\n\n"
        "Read-only tools: forge_status, forge_check, forge_explain, forge_gate_check, forge_drift, forge_schdraw_check, "
        "forge_cr_status. Tools that write: forge_schdraw_apply (one sheet, after a clean check, inside a transaction, "
        "under change control) and forge_cr_new (one draft change request). None calls a model or the network. "
        "Passing a gate and approving a change request are the project owner's decisions and have no tool."),
)

for fn in mcp_tools.TOOLS:
    mcp.tool()(fn)


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
