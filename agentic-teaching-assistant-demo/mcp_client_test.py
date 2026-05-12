"""
AI Teaching Assistant MCP Client — Host-Side Test Script

Run this on the host machine to test the MCP server end-to-end.

Usage:
    # Start the MCP server first (in another terminal):
    python3 ai_teaching_assistant_mcp_server.py

    # Then run tests:
    python3 mcp_client_test.py                         # run all tests
    python3 mcp_client_test.py health_check            # single tool
    python3 mcp_client_test.py study_material_query    # single test
    python3 mcp_client_test.py --url http://localhost:9001/mcp health_check
"""

import argparse
import asyncio
import json
import sys
from typing import Optional

try:
    from fastmcp import Client
except ImportError:
    print("ERROR: fastmcp not installed.  Run: pip install fastmcp httpx", file=sys.stderr)
    sys.exit(1)

_DEFAULT_URL = "http://localhost:9001/mcp"
_USER_ID = "testuser"


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

async def call(client: "Client", tool: str, **kwargs) -> str:
    result = await client.call_tool(tool, kwargs)
    parts = []
    for block in result:
        if hasattr(block, "text"):
            parts.append(block.text)
        else:
            parts.append(str(block))
    return "\n".join(parts)


def _header(name: str) -> None:
    print(f"\n{'='*60}")
    print(f"  {name}")
    print(f"{'='*60}")


# ---------------------------------------------------------------------------
# Individual tests
# ---------------------------------------------------------------------------

async def test_health_check(client):
    _header("health_check")
    out = await call(client, "health_check")
    print(out)


async def test_check_ingest_status(client):
    _header("check_ingest_status")
    out = await call(client, "check_ingest_status", user_id=_USER_ID)
    print(out)


async def test_list_subtopics(client):
    _header("list_subtopics")
    out = await call(client, "list_subtopics", user_id=_USER_ID)
    print(out)
    return out  # caller may parse it


async def test_generate_quiz(client, subtopic_number: int = 0):
    _header(f"generate_quiz (subtopic {subtopic_number})")
    out = await call(client, "generate_quiz", user_id=_USER_ID, subtopic_number=subtopic_number)
    print(out)
    return out


async def test_submit_quiz(client, subtopic_number: int = 0, answers: str = "A,A,A"):
    _header(f"submit_quiz (subtopic {subtopic_number}, answers={answers})")
    out = await call(
        client,
        "submit_quiz",
        user_id=_USER_ID,
        subtopic_number=subtopic_number,
        answers=answers,
    )
    print(out)


async def test_study_material_query(client, message: str = "What are the key concepts in this chapter?"):
    _header("study_material_query")
    print(f"  message: {message!r}")
    out = await call(client, "study_material_query", user_id=_USER_ID, message=message)
    print(out)


async def test_chitchat(client, message: str = "Thanks, you're a great tutor!"):
    _header("chitchat")
    print(f"  message: {message!r}")
    out = await call(client, "chitchat", user_id=_USER_ID, message=message)
    print(out)


async def test_supplement_query(client, message: str = "What's the history of backpropagation?"):
    _header("supplement_query")
    print(f"  message: {message!r}")
    out = await call(client, "supplement_query", user_id=_USER_ID, message=message)
    print(out)


async def test_youtube_search(client, query: str = "Claude AI skills tutorial"):
    _header("youtube_search")
    out = await call(client, "youtube_search", query=query)
    print(out)


async def test_get_curriculum(client):
    _header("get_curriculum")
    out = await call(client, "get_curriculum", user_id=_USER_ID)
    # Pretty-print only top level to keep output manageable
    try:
        data = json.loads(out)
        print(json.dumps({k: v for k, v in data.items() if k != "curriculum"}, indent=2))
        if "curriculum" in data:
            print(f"  ... curriculum present ({len(str(data['curriculum']))} chars)")
    except Exception:
        print(out[:500])


async def test_list_tools(client):
    _header("list_tools (MCP meta)")
    tools = await client.list_tools()
    for t in tools:
        print(f"  {t.name:30s} — {(t.description or '').splitlines()[0]}")


# ---------------------------------------------------------------------------
# Run all / single
# ---------------------------------------------------------------------------

_ALL_TESTS = {
    "health_check": test_health_check,
    "check_ingest_status": test_check_ingest_status,
    "list_subtopics": test_list_subtopics,
    "generate_quiz": test_generate_quiz,
    "submit_quiz": test_submit_quiz,
    "study_material_query": test_study_material_query,
    "chitchat": test_chitchat,
    "supplement_query": test_supplement_query,
    "youtube_search": test_youtube_search,
    "get_curriculum": test_get_curriculum,
    "list_tools": test_list_tools,
}


async def run_all(server_url: str) -> None:
    print(f"[test] connecting to {server_url}")
    async with Client(server_url) as client:
        # Always start with meta + health
        await test_list_tools(client)
        await test_health_check(client)
        await test_check_ingest_status(client)
        await test_get_curriculum(client)
        await test_list_subtopics(client)
        await test_generate_quiz(client, subtopic_number=0)
        await test_submit_quiz(client, subtopic_number=0, answers="A,A,A")
        await test_study_material_query(client)
        await test_chitchat(client)
        await test_supplement_query(client)
        await test_youtube_search(client)
    print("\n[test] All tests completed.")


async def run_one(server_url: str, tool_name: str) -> None:
    fn = _ALL_TESTS.get(tool_name)
    if fn is None:
        print(f"Unknown test: {tool_name!r}. Available: {', '.join(_ALL_TESTS)}", file=sys.stderr)
        sys.exit(1)
    print(f"[test] connecting to {server_url}")
    async with Client(server_url) as client:
        await fn(client)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="AI Teaching Assistant MCP test client")
    parser.add_argument(
        "--url",
        default=_DEFAULT_URL,
        help=f"MCP server URL (default: {_DEFAULT_URL})",
    )
    parser.add_argument(
        "test",
        nargs="?",
        default=None,
        help=f"Single test to run. One of: {', '.join(_ALL_TESTS)}. Omit to run all.",
    )
    args = parser.parse_args()

    if args.test:
        asyncio.run(run_one(args.url, args.test))
    else:
        asyncio.run(run_all(args.url))


if __name__ == "__main__":
    main()
