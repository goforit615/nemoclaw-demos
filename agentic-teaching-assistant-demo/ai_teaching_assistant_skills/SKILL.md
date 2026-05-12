---
name: ai-teaching-assistant-skills
description: Interact with the AgenticTA study platform running on the host machine via MCP. Provides direct tool access to upload PDFs and images, generate a curriculum, list subtopics, take quizzes, chat with the study buddy (per-intent — study material, chitchat, or supplemental knowledge), book calendar events, search YouTube, and launch study-break games. You (the agent) decide which tool to call — no secondary LLM is involved. Trigger keywords — study, pdf, curriculum, subtopic, quiz, chapter, learn, explain, study buddy, calendar, break, mini-game, youtube, video.
---

# AI Teaching Assistant Skill

## Overview

Direct tool interface to the AgenticTA study platform running on the **host machine** via MCP. You decide which tool to invoke based on the user's intent. The MCP server exposes deterministic operations — there is no host-side LLM router; the agent (you) picks the right tool.

## IMPORTANT — The user uploads through a browser, not the sandbox

The user cannot access the host filesystem. For PDFs and images you must always hand them a browser URL.

- **Never** ask the user for a file path.
- **Never** invent or guess an upload URL.
- Always call `get_upload_link` (for PDFs) or `get_image_upload_link` (for images) and present the URL you receive.
- `user_id` is pre-configured in `config.json` — do not ask the user for it.

## Invocation

Always use the skill venv's Python (required by the sandbox network policy):

```bash
SKILL_DIR=~/.openclaw/workspace/skills/ai-teaching-assistant-skills
SKILL=$SKILL_DIR/venv/bin/python3 $SKILL_DIR/scripts/ta_client.py
```

Do **not** use bare `python3` — the system Python is not permitted to reach the MCP server on port 8999.

## First-Time Setup

```bash
$SKILL_DIR/venv/bin/python3 $SKILL_DIR/scripts/setup_config.py
# or non-interactively:
$SKILL_DIR/venv/bin/python3 $SKILL_DIR/scripts/setup_config.py \
  --user-id alice --server-url http://host.openshell.internal:8999/mcp
```

## Routing — which tool for which intent

| If the user wants to… | Call this tool |
|---|---|
| upload / add / share a PDF | `get_upload_link` — hand them the URL returned |
| share an image / photo / diagram and ask about it | `get_image_upload_link --message "<their question>"` — hand them the URL |
| retrieve the answer after the user submits an image | `get_last_vlm_response` |
| confirm a PDF was ingested | `check_ingest_status` |
| generate a curriculum after upload | `generate_curriculum` |
| see the existing curriculum | `get_curriculum` |
| see what subtopics exist | `list_subtopics` |
| **explain / summarise / give an example from their study material** | `study_material_query` (RAG-backed) |
| **friendly small-talk, greeting, encouragement** | `chitchat` |
| **general / supplementary knowledge unrelated to the PDFs** | `supplement_query` |
| be quizzed on a subtopic | `list_subtopics` → `generate_quiz --subtopic-number N` |
| submit quiz answers | `submit_quiz --subtopic-number N --answers "B,A,C"` |
| book / schedule a study session | `book_calendar --text "..."` |
| find supplementary videos | `youtube_search --query "..."` |
| take a break / play a game | `get_study_break_link` |
| wipe a user's data before re-upload | `delete_user_data` |
| check the API is alive | `health_check` |

The three chat tools (`study_material_query`, `chitchat`, `supplement_query`) map 1-to-1 to deterministic handlers on the host — pick the one that matches the user's intent.

## Available Tools

### `get_upload_link`
Browser URL where the user uploads a PDF from their local machine.
**Use when:** user wants to upload, add, or share a PDF.
```bash
$SKILL get_upload_link
```

### `get_image_upload_link`
Browser URL where the user uploads an image and asks a VLM question about it.
**Use when:** user wants to share a photo, diagram, screenshot, or chart.
```bash
$SKILL get_image_upload_link --message "What does this diagram show?"
```

### `get_last_vlm_response`
Retrieves the most recent VLM answer after the user submits an image.
**Use when:** the user confirms they completed an image upload.
```bash
$SKILL get_last_vlm_response
```

### `check_ingest_status`
Verifies a PDF has been ingested into the vector store.
**Use when:** confirming readiness before generating the curriculum.
```bash
$SKILL check_ingest_status
```

### `generate_curriculum`
Generates a personalised study curriculum from the user's uploaded PDFs (streams progress events; takes 30–120 s).
**Use when:** the user confirms their PDF is uploaded.
```bash
$SKILL generate_curriculum
```

### `get_curriculum`
Retrieves the current curriculum.
```bash
$SKILL get_curriculum
```

### `list_subtopics`
Lists subtopics in the active chapter with their indices.
**Use when:** the user wants to see topics, or before any quiz tool (you need the subtopic index).
```bash
$SKILL list_subtopics
```

### `study_material_query`
Answers a question grounded in the user's uploaded PDFs (RAG-backed).
**Use when:** the user asks anything about their study material — explanations, summaries, definitions, examples, "what does X mean", "explain this concept".
```bash
$SKILL study_material_query --message "Explain gradient descent in chapter 2"
```

### `chitchat`
Friendly conversational reply in the study-buddy persona.
**Use when:** greetings, encouragement, casual conversation — anything that is NOT a question about the study material and NOT a general-knowledge request.
```bash
$SKILL chitchat --message "Thanks, you're a great tutor!"
```

### `supplement_query`
Answers a general/supplemental knowledge question outside the uploaded material.
**Use when:** the user asks for background, related topics, real-world context, or terms not in their PDFs.
```bash
$SKILL supplement_query --message "What's the history of backpropagation?"
```

### `generate_quiz`
Generates multiple-choice questions for a subtopic.
**Use when:** the user asks to be quizzed. Call `list_subtopics` first if you don't know the index.
```bash
$SKILL generate_quiz --subtopic-number 0
```

### `submit_quiz`
Submits quiz answers and returns graded feedback.
**Use when:** the user supplies their answers. `--answers` accepts letters (`A,B,C`), indices (`1,0,2`), or full choice text.
```bash
$SKILL submit_quiz --subtopic-number 0 --answers "B,A,C"
```

### `book_calendar`
Creates an `.ics` calendar event from a natural-language description.
**Use when:** the user wants to schedule a study session.
```bash
$SKILL book_calendar --text "Study session tomorrow at 3pm for 1 hour"
```

### `youtube_search`
Searches YouTube for educational videos on a topic.
```bash
$SKILL youtube_search --query "machine learning gradient descent"
```

### `get_study_break_link`
Returns the browser URL for the Study Break Games SPA.
**Use when:** the user wants a break, a game, or a breather.
```bash
$SKILL get_study_break_link
```

### `delete_user_data`
Wipes a user's Milvus collection and uploaded PDFs.
**Use when:** the user wants to re-upload a different PDF.
```bash
$SKILL delete_user_data
```

### `health_check`
Confirms the Teaching Assistant API is reachable.
```bash
$SKILL health_check
```

### `upload_pdf` (admin / automation only)
Uploads a PDF that already exists on the host filesystem. **Do not call this for end-user uploads** — use `get_upload_link` instead.
```bash
$SKILL upload_pdf --pdf-path /absolute/host/path/to/file.pdf
```

## Configuration (`config.json`)

```json
{
  "user_id": "alice",
  "server_url": "http://host.openshell.internal:8999/mcp"
}
```

When `config.json` exists, `--user-id` and `--server-url` are optional on every call.

## Server URL

Default: `http://host.openshell.internal:8999/mcp`. Override with `--server-url URL` or the `MCP_SERVER_URL` environment variable.

The MCP server must be running on the host:

```bash
python3 ai_teaching_assistant_mcp_server.py
```

## Troubleshooting

If a tool call fails with a connection error:
1. Confirm the MCP server is reachable: `curl http://host.openshell.internal:8999/mcp`
2. Confirm the sandbox policy allows egress to port 8999
3. If the venv is missing, recreate it:
   ```bash
   python3 -m venv $SKILL_DIR/venv
   $SKILL_DIR/venv/bin/pip install -q fastmcp
   ```
