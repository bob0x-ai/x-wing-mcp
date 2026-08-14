import sys
from pathlib import Path

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[2]


def test_mcp_stdio_lists_tools_and_calls_status():
    async def run():
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "xdata.server"],
            cwd=str(ROOT),
        )
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            tools_result = await session.list_tools()
            tools = {tool.name: tool for tool in tools_result.tools}

            assert set(tools) == {
                "x_fetch_urls",
                "x_read_user_posts",
                "x_search_posts",
                "x_read_owned_timeline",
                "x_read_mentions",
                "x_read_thread",
                "x_read_replies",
                "x_read_quotes",
                "x_read_follow_graph",
                "x_read_article",
                "x_collect_posts",
                "x_data_status",
                "x_data_healthcheck",
                "x_usage_stats",
            }
            for tool in tools.values():
                schema = getattr(tool, "input_schema", None)
                if schema is None:
                    schema = getattr(tool, "inputSchema")
                properties = schema.get("properties", {})
                assert "provider" not in properties

            status = await session.call_tool("x_data_status", {})

            assert status.is_error is False
            assert status.structured_content["status"] == "ok"
            assert status.structured_content["server"] == "x-data"
            assert "summary" in status.structured_content
            assert "details" not in status.structured_content

            health = await session.call_tool("x_data_healthcheck", {"mode": "basic"})

            assert health.is_error is False
            assert health.structured_content["status"] == "ok"
            assert health.structured_content["server"] == "x-data"
            assert "summary" in health.structured_content

    anyio.run(run)
