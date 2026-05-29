"""End-to-end test against a running local MCP server (section-aware API)."""
import asyncio
import json

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

import os
URL = os.environ.get("MCP_URL", "http://127.0.0.1:8091/mcp")


def unwrap(r):
    data = r.structuredContent if r.structuredContent is not None else json.loads(r.content[0].text)
    return data["result"] if isinstance(data, dict) and set(data.keys()) == {"result"} else data


async def main() -> None:
    async with streamablehttp_client(URL) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            print("=== tools ===")
            for t in tools.tools:
                print(f"  {t.name}: {t.description.splitlines()[0]}")

            print("\n=== search_99d('AI services and system integrators', 5) ===")
            r = await session.call_tool("search_99d", {"query": "AI services and system integrators", "limit": 5})
            for item in unwrap(r):
                print(f"  [{item['score']}] {item['section_title']}  ({item['kind']})")
                print(f"      from: {item['post_title']} ({item['date']})")
                print(f"      summary: {item['section_summary']}")
                print()

            print("=== search_99d('Cliff Club community for early employees', 3) ===")
            r = await session.call_tool("search_99d", {"query": "Cliff Club community for early employees", "limit": 3})
            for item in unwrap(r):
                print(f"  [{item['score']}] {item['section_title']}  ({item['kind']})")
                print(f"      from: {item['post_title']} ({item['date']})")
                print()

            print("=== list_recent(3) ===")
            r = await session.call_tool("list_recent", {"limit": 3})
            for item in unwrap(r):
                print(f"  {item['date']}  {item['title']}  ({item['main_section_count']} main sections)")

            print("\n=== get_section('ai-accenture-not-accenture-for-ai', 1) ===")
            r = await session.call_tool("get_section", {"slug": "ai-accenture-not-accenture-for-ai", "section_idx": 1})
            sec = unwrap(r)
            print(f"  title: {sec['section_title']}")
            print(f"  kind: {sec['kind']}")
            print(f"  summary: {sec['summary']}")
            print(f"  body (first 200): {sec['body_md'][:200]}")


if __name__ == "__main__":
    asyncio.run(main())
