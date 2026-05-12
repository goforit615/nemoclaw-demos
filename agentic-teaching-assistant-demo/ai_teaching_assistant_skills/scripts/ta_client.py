"""
AI Teaching Assistant — MCP Skill Client

Invoked by the NemoClaw sandbox agent as:
    python3 ta_client.py <tool_name> [--arg value ...]

The client connects to the MCP server running on the host machine and calls
the requested tool, printing the result to stdout.

Configuration is loaded from config.json (next to scripts/ dir) which provides
defaults for mandatory vars like user_id and server_url.
Override with CLI flags or environment variables.
"""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

# fastmcp must be installed in the skill venv
try:
    from fastmcp import Client
except ImportError:
    print(
        "ERROR: fastmcp is not installed. Run: pip install fastmcp",
        file=sys.stderr,
    )
    sys.exit(1)

# ---------------------------------------------------------------------------
# Config loading — reads config.json from the skill root directory
# ---------------------------------------------------------------------------

_SKILL_DIR = Path(__file__).resolve().parent.parent
_CONFIG_PATH = _SKILL_DIR / "config.json"


def _load_config() -> dict:
    """Load config.json if it exists, otherwise return empty dict."""
    if _CONFIG_PATH.is_file():
        try:
            return json.loads(_CONFIG_PATH.read_text())
        except (json.JSONDecodeError, OSError) as e:
            print(f"Warning: failed to load {_CONFIG_PATH}: {e}", file=sys.stderr)
    return {}


_CONFIG = _load_config()

_DEFAULT_URL = os.environ.get(
    "MCP_SERVER_URL", _CONFIG.get("server_url", "http://host.openshell.internal:8999/mcp")
)
_DEFAULT_USER_ID = _CONFIG.get("user_id", None)


# ---------------------------------------------------------------------------
# Async call helper
# ---------------------------------------------------------------------------

# Per-tool timeouts (seconds). generate_curriculum streams SSE for up to
# several minutes; chat tools / upload_pdf also need extra headroom.
_TOOL_TIMEOUTS: dict[str, float] = {
    "generate_curriculum":   300.0,
    "study_material_query":  120.0,
    "chitchat":              120.0,
    "supplement_query":      120.0,
    "upload_pdf":            120.0,
}
_DEFAULT_TIMEOUT = 60.0


async def _call(server_url: str, tool_name: str, args: dict) -> None:
    timeout = _TOOL_TIMEOUTS.get(tool_name, _DEFAULT_TIMEOUT)
    async with Client(server_url, timeout=timeout) as client:
        result = await client.call_tool(tool_name, args)
        # Result may be a CallToolResult object or a list of content blocks
        blocks = result.content if hasattr(result, "content") else result
        for block in blocks:
            if hasattr(block, "text"):
                print(block.text)
            else:
                print(str(block))


def call_tool(server_url: str, tool_name: str, args: dict) -> None:
    asyncio.run(_call(server_url, tool_name, args))


# ---------------------------------------------------------------------------
# CLI parsers
# ---------------------------------------------------------------------------

def _user_id_kwargs() -> dict:
    """Return argparse kwargs for --user-id, making it optional when config provides a default."""
    if _DEFAULT_USER_ID:
        return {"default": _DEFAULT_USER_ID, "help": f"User ID (default from config: {_DEFAULT_USER_ID})"}
    return {"required": True, "help": "User ID (set in config.json to make optional)"}


def build_parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="ta_client",
        description="AI Teaching Assistant MCP skill client",
    )
    root.add_argument(
        "--server-url",
        default=_DEFAULT_URL,
        help=f"MCP server URL (default: {_DEFAULT_URL})",
    )

    sub = root.add_subparsers(dest="tool", required=True)

    uid = _user_id_kwargs()

    # ── Admin ────────────────────────────────────────────────────────────────
    sub.add_parser("health_check", help="Check API health")

    # ── Upload links ─────────────────────────────────────────────────────────
    p = sub.add_parser("get_upload_link", help="Get browser URL for PDF upload")
    p.add_argument("--user-id", **uid)

    p = sub.add_parser("get_image_upload_link", help="Get browser URL to upload an image and ask a VLM question")
    p.add_argument("--user-id", **uid)
    p.add_argument("--message", default="", help="Pre-filled question to show on the upload form")

    p = sub.add_parser("get_last_vlm_response", help="Retrieve last VLM answer after image upload")
    p.add_argument("--user-id", **uid)

    # ── Files & Ingestion ────────────────────────────────────────────────────
    p = sub.add_parser("upload_pdf", help="Upload a PDF for a user")
    p.add_argument("--user-id", **uid)
    p.add_argument("--pdf-path", required=True, help="Absolute path to the PDF on host")

    p = sub.add_parser("check_ingest_status", help="Check vector store ingestion status")
    p.add_argument("--user-id", **uid)

    p = sub.add_parser("delete_user_data", help="Delete a user's data and vector collection")
    p.add_argument("--user-id", **uid)

    sub.add_parser("delete_all_data", help="⚠️ Delete ALL users' data")

    # ── Curriculum ───────────────────────────────────────────────────────────
    p = sub.add_parser("generate_curriculum", help="Generate study curriculum from uploaded PDFs")
    p.add_argument("--user-id", **uid)

    p = sub.add_parser("get_curriculum", help="Retrieve current curriculum")
    p.add_argument("--user-id", **uid)

    # ── Chat (per-intent — agent picks the tool, no host-side LLM router) ────
    p = sub.add_parser(
        "study_material_query",
        help="Ask a question about the user's uploaded study material (RAG-backed)",
    )
    p.add_argument("--user-id", **uid)
    p.add_argument("--message", required=True)

    p = sub.add_parser(
        "chitchat",
        help="Friendly small-talk reply in the study-buddy persona",
    )
    p.add_argument("--user-id", **uid)
    p.add_argument("--message", required=True)

    p = sub.add_parser(
        "supplement_query",
        help="Answer a general/supplemental knowledge question (not from the PDFs)",
    )
    p.add_argument("--user-id", **uid)
    p.add_argument("--message", required=True)

    # ── Quiz ─────────────────────────────────────────────────────────────────
    p = sub.add_parser("list_subtopics", help="List subtopics with indices")
    p.add_argument("--user-id", **uid)

    p = sub.add_parser("generate_quiz", help="Generate quiz for a subtopic")
    p.add_argument("--user-id", **uid)
    p.add_argument("--subtopic-number", type=int, default=0, help="0-based subtopic index")

    p = sub.add_parser("submit_quiz", help="Submit quiz answers (comma-separated: A,B,C)")
    p.add_argument("--user-id", **uid)
    p.add_argument("--subtopic-number", type=int, required=True)
    p.add_argument(
        "--answers",
        required=True,
        help="Comma-separated answers per question, e.g. 'B,A,C' or '1,0,2'",
    )

    # ── Extras ───────────────────────────────────────────────────────────────
    p = sub.add_parser("book_calendar", help="Book a calendar study session")
    p.add_argument("--user-id", **uid)
    p.add_argument("--text", required=True, help="Natural language description of the session")

    p = sub.add_parser("youtube_search", help="Search YouTube for educational videos")
    p.add_argument("--query", required=True)

    sub.add_parser("get_study_break_link", help="Get browser URL for Study Break Games")

    return root


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    server_url = args.server_url
    tool = args.tool

    # Build kwargs dict for the tool call
    tool_args: dict = {}

    if tool == "health_check":
        pass  # no args

    elif tool == "get_upload_link":
        tool_args = {"user_id": args.user_id}

    elif tool == "get_image_upload_link":
        tool_args = {"user_id": args.user_id, "message": args.message}

    elif tool == "get_last_vlm_response":
        tool_args = {"user_id": args.user_id}

    elif tool == "upload_pdf":
        tool_args = {"user_id": args.user_id, "pdf_path": args.pdf_path}

    elif tool == "check_ingest_status":
        tool_args = {"user_id": args.user_id}

    elif tool == "delete_user_data":
        tool_args = {"user_id": args.user_id}

    elif tool == "delete_all_data":
        pass

    elif tool == "generate_curriculum":
        tool_args = {"user_id": args.user_id}

    elif tool == "get_curriculum":
        tool_args = {"user_id": args.user_id}

    elif tool == "study_material_query":
        tool_args = {"user_id": args.user_id, "message": args.message}

    elif tool == "chitchat":
        tool_args = {"user_id": args.user_id, "message": args.message}

    elif tool == "supplement_query":
        tool_args = {"user_id": args.user_id, "message": args.message}

    elif tool == "list_subtopics":
        tool_args = {"user_id": args.user_id}

    elif tool == "generate_quiz":
        tool_args = {"user_id": args.user_id, "subtopic_number": args.subtopic_number}

    elif tool == "submit_quiz":
        tool_args = {
            "user_id": args.user_id,
            "subtopic_number": args.subtopic_number,
            "answers": args.answers,
        }

    elif tool == "book_calendar":
        tool_args = {"user_id": args.user_id, "text": args.text}

    elif tool == "youtube_search":
        tool_args = {"query": args.query}

    elif tool == "get_study_break_link":
        pass  # no args

    else:
        print(f"Unknown tool: {tool}", file=sys.stderr)
        sys.exit(1)

    call_tool(server_url, tool, tool_args)


if __name__ == "__main__":
    main()
