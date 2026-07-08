# -*- coding: utf-8 -*-
"""Valide le serveur MCP vault-map de bout en bout via un vrai client stdio :
init -> list_tools -> call_tool(vault_map / outline / get_section / query)."""
import asyncio, sys, os
sys.stdout.reconfigure(encoding="utf-8")
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

HERE = os.path.dirname(os.path.abspath(__file__))
PY = os.path.join(HERE, ".venv", "Scripts", "python.exe")


async def main():
    params = StdioServerParameters(command=PY, args=[os.path.join(HERE, "server.py")])
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = await s.list_tools()
            print("OUTILS :", [t.name for t in tools.tools])

            async def call(name, **kw):
                res = await s.call_tool(name, kw)
                txt = res.content[0].text
                print(f"\n=== {name}({kw}) -> {len(txt)} chars ===")
                print("\n".join(txt.splitlines()[:6]))

            await call("vault_map")
            await call("outline", note="Repo-map-codebase-pour-Claude-Code")
            await call("get_section", note="Vault-map-repo-map-pour-les-notes", title="Verdict")
            await call("query", filter="status:challengee, score>5")
            print("\n[OK] handshake MCP stdio complet validé.")


asyncio.run(main())
