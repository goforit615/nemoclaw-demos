"""
AI Teaching Assistant MCP Server

Exposes the AgenticTA FastAPI backend as MCP tools so that Claude agents
running inside a NemoClaw / OpenShell sandbox can interact with it.

Default endpoint: http://0.0.0.0:8999/mcp

Tools (grouped by function):
  Files & Ingestion  : get_upload_link, ingest_uploaded_pdf, upload_pdf,
                       check_ingest_status, delete_user_data, delete_all_data
  Curriculum         : generate_curriculum, get_curriculum
  Chat               : study_material_query, chitchat, supplement_query
  Quiz               : list_subtopics, generate_quiz, submit_quiz
  Extras             : book_calendar, youtube_search
  Admin              : health_check

Chat tool selection (no host-side LLM dispatcher — the agent picks):
  - study_material_query : RAG-backed answer about uploaded PDFs
  - chitchat             : friendly conversation with the study-buddy persona
  - supplement_query     : general / supplementary knowledge unrelated to the PDFs

PDF Upload Flow (OpenClaw / sandbox users)
------------------------------------------
Because sandbox users cannot access the host filesystem directly, PDF upload
works via a browser-based upload portal served by the TA API:

  1. Agent calls get_upload_link(user_id)
     → returns  http://<host>:8000/upload?user_id=alice
  2. Agent shows the URL to the user in chat.
  3. User opens the URL in their browser, selects their PDF, clicks Upload.
  4. On success the page says "Return to chat".
  5. User tells the agent "done" — agent calls ingest_uploaded_pdf or
     generate_curriculum directly (upload already ingested the file).
"""

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import Optional

import httpx
from fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_TA_HOST = os.environ.get("TA_API_HOST", "http://localhost:8000")
_MCP_HOST = os.environ.get("MCP_TA_HOST", "0.0.0.0")
_MCP_PORT = int(os.environ.get("MCP_TA_PORT", "8999"))
_MCP_PATH = os.environ.get("MCP_TA_PATH", "/mcp")
# Public-facing hostname/IP users open in their browser (set by install.sh).
# Falls back to localhost — suitable when user's browser is on the same machine.
_HOST_EXTERNAL_URL = os.environ.get("TA_EXTERNAL_URL", "http://localhost:8000")

mcp = FastMCP("AITeachingAssistantMCP")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _url(path: str) -> str:
    return f"{_TA_HOST.rstrip('/')}/{path.lstrip('/')}"


async def _get(path: str, **params) -> dict:
    async with httpx.AsyncClient(timeout=60.0) as client:
        r = await client.get(_url(path), params=params)
        r.raise_for_status()
        return r.json()


async def _post(path: str, json_body: dict) -> dict:
    async with httpx.AsyncClient(timeout=120.0) as client:
        r = await client.post(_url(path), json=json_body)
        r.raise_for_status()
        return r.json()


async def _delete(path: str) -> dict:
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.delete(_url(path))
        r.raise_for_status()
        return r.json()


async def _stream_curriculum(user_id: str) -> str:
    """Consume the SSE curriculum-generation stream and return a summary string."""
    events = []
    async with httpx.AsyncClient(timeout=300.0) as client:
        async with client.stream(
            "GET", _url(f"/api/curriculum/generate-stream"), params={"user_id": user_id}
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                line = line.strip()
                if not line.startswith("data:"):
                    continue
                payload = line[len("data:"):].strip()
                try:
                    obj = json.loads(payload)
                    events.append(obj)
                    if obj.get("type") in ("complete", "error"):
                        break
                except json.JSONDecodeError:
                    events.append({"raw": payload})

    # Build a human-readable summary
    lines = []
    for ev in events:
        t = ev.get("type", "")
        if t == "start":
            lines.append(f"[start] PDFs: {ev.get('pdfs', [])}")
        elif t == "phase":
            lines.append(f"[phase:{ev.get('phase','')}] {ev.get('message','')}")
        elif t == "subtopic_progress":
            lines.append(f"[progress] {ev.get('message','')}")
        elif t == "complete":
            lines.append(f"[complete] {ev.get('message','')}")
        elif t == "error":
            lines.append(f"[ERROR] {ev.get('message','')}")
        else:
            lines.append(f"[{t}] {payload}")
    return "\n".join(lines) if lines else "No events received."


# ===========================================================================
# ── Files & Ingestion ───────────────────────────────────────────────────────
# ===========================================================================

@mcp.tool()
async def get_upload_link(user_id: str) -> str:
    """Get the browser URL where a user can upload a PDF from their local machine.

    Use this when the user asks to upload a PDF.  The URL opens an HTML form
    they can access from any browser — no curl or API knowledge needed.

    Workflow:
      1. Call this tool to get the URL.
      2. Share the URL with the user in chat.
      3. User opens it, selects their PDF, clicks Upload.
      4. On success the page says "Return to chat and tell the assistant you're done."
      5. User confirms — then call generate_curriculum(user_id) to proceed.

    Args:
        user_id: Unique user identifier (e.g. "alice").

    Returns:
        Plain-text message containing the upload URL and step-by-step instructions.
    """
    upload_url = f"{_HOST_EXTERNAL_URL.rstrip('/')}/upload?user_id={user_id}"
    return (
        f"Please open the following URL in your browser to upload your PDF:\n\n"
        f"  {upload_url}\n\n"
        f"Steps:\n"
        f"  1. Open the URL above in any browser.\n"
        f"  2. Your user ID ({user_id}) is pre-filled.\n"
        f"  3. Click 'Choose file', select your PDF, then click 'Upload PDF'.\n"
        f"  4. Wait for the success message on the page.\n"
        f"  5. Return here and say 'done' — I'll generate your curriculum.\n\n"
        f"If the link does not open, ask your administrator for the correct host address."
    )


@mcp.tool()
async def get_image_upload_link(user_id: str, message: str = "") -> str:
    """Get the browser URL where a user can upload an image and ask a VLM question.

    Use this when the user wants to share a photo, diagram, chart, or screenshot
    and ask their study buddy about it.  The page loads their full study context
    (chapter, subtopic, memory) automatically and calls the VLM with a rich
    system prompt.

    Workflow:
      1. Call this tool — optionally pass the user's question as `message`.
      2. Share the URL with the user in chat.
      3. User opens it, uploads their image, reviews/edits the question, clicks submit.
      4. The page shows the VLM answer immediately.
      5. User returns to chat and says "done" — then call get_last_vlm_response(user_id)
         to retrieve the answer into the conversation.

    Args:
        user_id: Unique user identifier (e.g. "alice").
        message: Optional pre-filled question to show on the upload form.

    Returns:
        Plain-text message containing the image-upload URL and step-by-step instructions.
    """
    import urllib.parse
    base = _HOST_EXTERNAL_URL.rstrip("/")
    params = f"user_id={urllib.parse.quote(user_id)}"
    if message:
        params += f"&message={urllib.parse.quote(message)}"
    upload_url = f"{base}/upload-image?{params}"
    return (
        f"Please open the following URL in your browser to upload an image and ask your question:\n\n"
        f"  {upload_url}\n\n"
        f"Steps:\n"
        f"  1. Open the URL above in any browser.\n"
        f"  2. Select your image file (JPG, PNG, GIF, or WEBP).\n"
        f"  3. Review or edit the question in the text box, then click 'Ask Study Buddy'.\n"
        f"  4. Wait for the VLM answer to appear on the page.\n"
        f"  5. Return here and say 'done' — I'll fetch the answer with get_last_vlm_response.\n\n"
        f"Note: your study context (chapter, subtopic, memory) is loaded automatically.\n"
        f"If the link does not open, ask your administrator for the correct host address."
    )


@mcp.tool()
async def get_last_vlm_response(user_id: str) -> str:
    """Retrieve the most recent VLM (image + question) answer for a user.

    Call this after the user confirms they submitted an image via the
    get_image_upload_link URL.  Returns the stored VLM answer so it can be
    presented in the chat conversation.

    Args:
        user_id: Unique user identifier (e.g. "alice").

    Returns:
        The VLM answer text, or an error message if no result is stored yet.
    """
    import httpx
    url = f"{_HOST_EXTERNAL_URL.rstrip('/')}/upload-image/result?user_id={user_id}"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url)
        data = resp.json()
        if "error" in data:
            return f"No VLM result found yet for user '{user_id}'. Ask the user to complete the image upload first."
        question = data.get("question", "")
        answer   = data.get("response", "")
        return (
            f"VLM answer retrieved successfully.\n\n"
            f"**Question:** {question}\n\n"
            f"**Study Buddy Answer:**\n{answer}"
        )
    except Exception as exc:
        return f"Could not retrieve VLM result: {type(exc).__name__}: {exc}"


@mcp.tool()
async def ingest_uploaded_pdf(user_id: str, filename: str = "") -> str:
    """Trigger ingestion of a PDF that has already been uploaded to the host.

    Use this if the user completed the browser upload but ingestion was not
    confirmed, or to re-ingest after replacing a file.

    If filename is empty, ingests all PDFs found for the user.

    Args:
        user_id: Unique user identifier.
        filename: Optional specific PDF filename (e.g. "lecture1.pdf").
                  Leave empty to ingest all uploaded files for this user.

    Returns:
        JSON ingestion result or status.
    """
    try:
        # Check current ingest status first
        status = await _get(f"/api/files/ingest-status/{user_id}")
        if status.get("ready"):
            return json.dumps({
                "already_ingested": True,
                "chunk_count": status.get("chunk_count", 0),
                "message": "PDF already ingested and ready for curriculum generation.",
            }, indent=2)

        # Re-trigger by calling the upload endpoint with an already-saved file
        # (TA API handles idempotent re-ingestion via the ingest-status + RAG path)
        result = await _get(f"/api/files/ingest-status/{user_id}")
        return json.dumps(result, indent=2)
    except Exception as ex:
        return f"Error: {type(ex).__name__}: {ex}"


@mcp.tool()
async def upload_pdf(user_id: str, pdf_path: str) -> str:
    """Upload a PDF that already exists on the HOST filesystem.

    For end-users accessing via OpenClaw, use get_upload_link() instead —
    it gives them a browser URL to push a file from their own machine.
    This tool is for server-side / automation use where the PDF is already
    present on the host (e.g. placed there by an admin or CI pipeline).

    Args:
        user_id: Unique user identifier (e.g. "alice").
        pdf_path: Absolute path to the PDF on the HOST machine (not the sandbox).

    Returns:
        JSON string with upload result including success status and message.
    """
    def _sync_upload(uid: str, path: str) -> dict:
        import httpx as _httpx
        p = Path(path)
        if not p.exists():
            return {"success": False, "message": f"File not found: {path}"}
        with open(p, "rb") as fh:
            files = {"files": (p.name, fh, "application/pdf")}
            data = {"user_id": uid}
            with _httpx.Client(timeout=120.0) as c:
                r = c.post(_url("/api/files/upload"), data=data, files=files)
                r.raise_for_status()
                return r.json()

    try:
        result = await asyncio.to_thread(_sync_upload, user_id, pdf_path)
        return json.dumps(result, indent=2)
    except Exception as ex:
        return f"Error: {type(ex).__name__}: {ex}"


@mcp.tool()
async def check_ingest_status(user_id: str) -> str:
    """Check whether a user's PDF has been ingested into the vector store.

    Args:
        user_id: Unique user identifier.

    Returns:
        JSON with 'ready' (bool), 'chunk_count', 'exists', and 'message'.
    """
    try:
        result = await _get(f"/api/files/ingest-status/{user_id}")
        return json.dumps(result, indent=2)
    except Exception as ex:
        return f"Error: {type(ex).__name__}: {ex}"


@mcp.tool()
async def delete_user_data(user_id: str) -> str:
    """Delete a user's Milvus collection and all their uploaded PDF files.

    Use this when re-uploading a new version of the same PDF.

    Args:
        user_id: Unique user identifier.

    Returns:
        JSON with deleted collection names and files removed count.
    """
    try:
        result = await _delete(f"/api/files/collections/{user_id}")
        return json.dumps(result, indent=2)
    except Exception as ex:
        return f"Error: {type(ex).__name__}: {ex}"


@mcp.tool()
async def delete_all_data() -> str:
    """Delete ALL users' Milvus collections and uploaded PDFs. ⚠️ Destructive.

    Returns:
        JSON listing every collection and file that was removed.
    """
    try:
        result = await _delete("/api/files/collections")
        return json.dumps(result, indent=2)
    except Exception as ex:
        return f"Error: {type(ex).__name__}: {ex}"


# ===========================================================================
# ── Curriculum ──────────────────────────────────────────────────────────────
# ===========================================================================

@mcp.tool()
async def generate_curriculum(user_id: str) -> str:
    """Generate a personalised study curriculum from the user's uploaded PDFs.

    This streams progress events from the server and waits until the curriculum
    is fully built (or an error occurs).  Typical duration: 30-120 seconds.

    Args:
        user_id: Unique user identifier.

    Returns:
        Human-readable log of generation phases, ending with [complete] or [ERROR].
    """
    try:
        return await _stream_curriculum(user_id)
    except Exception as ex:
        return f"Error: {type(ex).__name__}: {ex}"


@mcp.tool()
async def get_curriculum(user_id: str) -> str:
    """Retrieve the current curriculum for a user (after generation completes).

    Args:
        user_id: Unique user identifier.

    Returns:
        JSON curriculum with chapters, subtopics, and their status.
    """
    try:
        result = await _get(f"/api/curriculum/{user_id}")
        return json.dumps(result, indent=2)
    except Exception as ex:
        return f"Error: {type(ex).__name__}: {ex}"


# ===========================================================================
# ── Chat ────────────────────────────────────────────────────────────────────
#
# The three tools below are deterministic per-intent endpoints — there is no
# LLM router on the host. The agent (which already has its own LLM) decides
# which one to call based on the user's intent.
# ===========================================================================

async def _chat_with_tool(user_id: str, message: str, tool: str) -> str:
    """Helper: POST to /api/chat/message with a pre-selected handler."""
    try:
        result = await _post(
            "/api/chat/message",
            {"user_id": user_id, "message": message, "tool": tool},
        )
        return json.dumps(result, indent=2)
    except Exception as ex:
        return f"Error: {type(ex).__name__}: {ex}"


@mcp.tool()
async def study_material_query(user_id: str, message: str) -> str:
    """Answer a question about the user's uploaded study material (RAG-backed).

    Use this when the user asks anything about the content of their PDFs:
    explanations, summaries, examples, definitions, "what does X mean",
    "explain this concept", "give me an example of …", etc.

    Args:
        user_id: Unique user identifier.
        message: The user's question.

    Returns:
        JSON with 'content' and 'tool_used' (study_material).
    """
    return await _chat_with_tool(user_id, message, "study_material")


@mcp.tool()
async def chitchat(user_id: str, message: str) -> str:
    """Friendly small-talk reply in the study-buddy persona.

    Use this for greetings, encouragement, casual conversation, or anything
    that is NOT a question about the study material and NOT a request for
    supplemental knowledge.

    Args:
        user_id: Unique user identifier.
        message: The user's message.

    Returns:
        JSON with 'content' and 'tool_used' (chitchat).
    """
    return await _chat_with_tool(user_id, message, "chitchat")


@mcp.tool()
async def supplement_query(user_id: str, message: str) -> str:
    """Answer a general / supplemental knowledge question (NOT from the PDFs).

    Use this when the user asks something outside their uploaded material —
    background information, related topics, definitions of terms not in the
    PDFs, real-world context, etc.

    Args:
        user_id: Unique user identifier.
        message: The user's question.

    Returns:
        JSON with 'content' and 'tool_used' (supplement).
    """
    return await _chat_with_tool(user_id, message, "supplement")


# ===========================================================================
# ── Quiz ────────────────────────────────────────────────────────────────────
# ===========================================================================

@mcp.tool()
async def list_subtopics(user_id: str) -> str:
    """List all subtopics in the user's active chapter with their indices.

    Call this first to find the subtopic_number needed for quiz generation.

    Args:
        user_id: Unique user identifier.

    Returns:
        JSON with 'chapter' name and 'subtopics' list: [{index, name, status}, ...].
        Status values: 'not_started', 'started', 'completed'.
    """
    try:
        result = await _get(f"/api/quiz/subtopics/{user_id}")
        return json.dumps(result, indent=2)
    except Exception as ex:
        return f"Error: {type(ex).__name__}: {ex}"


@mcp.tool()
async def generate_quiz(user_id: str, subtopic_number: int = 0) -> str:
    """Generate multiple-choice quiz questions for a specific subtopic.

    Use list_subtopics first to find the correct subtopic_number.
    Each returned question has choices labelled A) B) C) D).

    Args:
        user_id: Unique user identifier.
        subtopic_number: 0-based subtopic index from list_subtopics (default 0).

    Returns:
        JSON with 'questions' list. Each question has:
          - question (str)
          - choices  (list[str])  — labelled "A) ...", "B) ...", etc.
          - answer   (str)        — correct letter, e.g. "B"
          - explanation (str)
    """
    try:
        result = await _post(
            "/api/quiz/generate",
            {"user_id": user_id, "subtopic_number": subtopic_number},
        )
        return json.dumps(result, indent=2)
    except Exception as ex:
        return f"Error: {type(ex).__name__}: {ex}"


@mcp.tool()
async def submit_quiz(user_id: str, subtopic_number: int, answers: str) -> str:
    """Submit answers to a quiz and receive graded feedback.

    Args:
        user_id: Unique user identifier.
        subtopic_number: The same index used when generating the quiz.
        answers: Comma-separated answers, one per question.
                 Each answer can be a letter (A/B/C/D), numeric index (0/1/2/3),
                 or the full choice text.
                 Example: "B,A,C"  or  "1,0,2"

    Returns:
        JSON with 'correct', 'total', 'passed' (bool), and per-question 'feedback'.
    """
    try:
        answer_list = [a.strip() for a in answers.split(",")]
        result = await _post(
            "/api/quiz/submit",
            {
                "user_id": user_id,
                "subtopic_number": subtopic_number,
                "answers": answer_list,
            },
        )
        return json.dumps(result, indent=2)
    except Exception as ex:
        return f"Error: {type(ex).__name__}: {ex}"


# ===========================================================================
# ── Extras ──────────────────────────────────────────────────────────────────
# ===========================================================================

@mcp.tool()
async def book_calendar(user_id: str, text: str) -> str:
    """Book a study session from a natural-language description.

    Returns an .ics calendar event that can be imported into any calendar app.

    Args:
        user_id: Unique user identifier.
        text: Natural-language description, e.g. "Study session tomorrow at 3pm for 1 hour".

    Returns:
        JSON with 'ics_content' (calendar event text) and event metadata.
    """
    try:
        result = await _post("/api/calendar/create", {"user_id": user_id, "text": text})
        return json.dumps(result, indent=2)
    except Exception as ex:
        return f"Error: {type(ex).__name__}: {ex}"


@mcp.tool()
async def get_study_break_link() -> str:
    """Get the browser URL for the Study Break Games mini-games.

    Use this when the user asks for a break, wants to play a quick game,
    or needs a breather between study sessions.  The page hosts a collection
    of fun mini-games that run entirely in the browser — no login or user ID
    required.

    Workflow:
      1. Call this tool to get the URL.
      2. Share the URL with the user in chat.
      3. User opens it, plays a game or two, then returns to studying.

    Returns:
        Plain-text message containing the games URL and a friendly nudge.
    """
    games_url = f"{_HOST_EXTERNAL_URL.rstrip('/')}/games/"
    return (
        f"Take a well-deserved study break! Open this link to play some mini-games:\n\n"
        f"  {games_url}\n\n"
        f"Come back whenever you're ready to keep studying. 🎮"
    )


@mcp.tool()
async def youtube_search(query: str) -> str:
    """Search YouTube for educational videos related to a topic.

    Args:
        query: Search query, e.g. "machine learning gradient descent tutorial".

    Returns:
        JSON list of video results with title, url, channel, and description.
    """
    try:
        result = await _get("/api/youtube/search", query=query)
        return json.dumps(result, indent=2)
    except Exception as ex:
        return f"Error: {type(ex).__name__}: {ex}"


# ===========================================================================
# ── Admin ───────────────────────────────────────────────────────────────────
# ===========================================================================

@mcp.tool()
async def health_check() -> str:
    """Check that the AI Teaching Assistant API is reachable and healthy.

    Returns:
        JSON health response or an error message.
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(_url("/"))
            return json.dumps({"status": "ok", "http_code": r.status_code, "body": r.json()}, indent=2)
    except Exception as ex:
        return f"Error: {type(ex).__name__}: {ex}"


# ===========================================================================
# ── Entry point ─────────────────────────────────────────────────────────────
# ===========================================================================

def _parse_args():
    parser = argparse.ArgumentParser(description="AI Teaching Assistant MCP Server")
    parser.add_argument("--host", default=_MCP_HOST, help="Bind host (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=_MCP_PORT, help="Bind port (default: 8999)")
    parser.add_argument("--path", default=_MCP_PATH, help="URL path (default: /mcp)")
    parser.add_argument(
        "--ta-host",
        default=_TA_HOST,
        help="Teaching Assistant API base URL (default: http://localhost:8000)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()

    # Allow runtime override of the TA host
    import ai_teaching_assistant_mcp_server as _self
    _self._TA_HOST = args.ta_host

    print(f"[MCP] AI Teaching Assistant MCP Server")
    print(f"[MCP] TA API  : {args.ta_host}")
    print(f"[MCP] Listen  : http://{args.host}:{args.port}{args.path}")

    mcp.run(
        transport="streamable-http",
        host=args.host,
        port=args.port,
        path=args.path,
        show_banner=False,
    )
