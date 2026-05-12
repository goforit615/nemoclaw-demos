"""
Chat Routes

Handles chat message routing and streaming responses.
Supports multimodal input (text + images) via VLM.
Integrates query decomposition for multi-step task execution.
Includes conversation memory for personalized, context-aware responses.
"""

import os
import sys
import json
import base64
import tempfile
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple

from fastapi import APIRouter, HTTPException, Query, UploadFile, File, Form
from fastapi.responses import StreamingResponse

# Add parent directory to path
parent_dir = Path(__file__).parent.parent.parent
if str(parent_dir) not in sys.path:
    sys.path.insert(0, str(parent_dir))

from common.debug import debug_print, get_debug_logger
from api.schemas.chat import (
    ChatRouteRequest,
    ChatRouteResponse,
    ChatStreamRequest,
    ChatMessageResponse,
    ToolType,
)

router = APIRouter()
logger = get_debug_logger(__name__)

# Route diagnostic print statements through the shared debug gate.
print = debug_print

SAVE_TO = os.environ.get("AGENTICTA_SAVE_TO", "/workspace/mnt/")

# ============================================
# Conversation Memory Integration
# ============================================
# Memory system for personalized, context-aware responses
# Stores conversation history and creates intelligent summaries

_memory_ops_cache: Dict[str, Any] = {}  # Cache memory instances per user


def _get_memory_ops(user_id: str):
    """
    Get or create a MemoryOps instance for a user.
    Uses lazy loading to avoid import errors if memory system is not available.
    
    Args:
        user_id: User identifier
        
    Returns:
        MemoryOps instance or None if memory system unavailable
    """
    global _memory_ops_cache
    
    if user_id in _memory_ops_cache:
        return _memory_ops_cache[user_id]
    
    try:
        from agent_memory import get_memory_ops
        
        # Initialize memory with reasonable defaults
        memory_ops = get_memory_ops(
            username=user_id,
            memory_dir=os.path.join(SAVE_TO, user_id, "memory"),
            rate_limit_delay=2.0,      # Avoid rate limits
            summary_interval=5         # Summarize every 5 turns
        )
        
        _memory_ops_cache[user_id] = memory_ops
        print(f"[MEMORY] Initialized conversation memory for user: {user_id}")
        return memory_ops
        
    except ImportError as e:
        print(f"[MEMORY] Memory system not available: {e}")
        return None
    except Exception as e:
        print(f"[MEMORY] Error initializing memory for {user_id}: {e}")
        return None


def _get_memory_context(memory_ops, query: str) -> Tuple[str, str]:
    """
    Get memory context and history summary for a query.
    
    Args:
        memory_ops: MemoryOps instance (or None)
        query: User's current message
        
    Returns:
        Tuple of (memory_context, history_summary)
    """
    if memory_ops is None:
        return "", ""
    
    try:
        memory_context = memory_ops.get_memory_context(query)
        history_summary = memory_ops.get_history_summary()
        return memory_context, history_summary
    except Exception as e:
        print(f"[MEMORY] Error getting memory context: {e}")
        return "", ""


async def _save_interaction_to_memory(
    memory_ops,
    user_message: str,
    bot_response: str,
    background: bool = True
) -> None:
    """
    Save a conversation interaction to memory (non-blocking by default).
    
    Args:
        memory_ops: MemoryOps instance (or None)
        user_message: User's message
        bot_response: Bot's response
        background: Whether to create summary in background (non-blocking)
    """
    if memory_ops is None:
        return
    
    try:
        await memory_ops.process_message(
            message=user_message,
            bot_response=bot_response,
            background_summary=background
        )
        print(f"[MEMORY] Saved interaction to memory (background={background})")
    except Exception as e:
        print(f"[MEMORY] Error saving interaction: {e}")

# Query decomposition retry configuration
MAX_DECOMPOSITION_RETRIES = 3
PLAN_FAILURE_MESSAGE = "I couldn't create a plan for your request. Please try rephrasing your question or breaking it into smaller parts."


def _call_query_decomposition_with_retry(
    user_input: str,
    chapter_name: str = "",
    sub_topic: str = "",
    study_material: str = "",
    stringified: str = "",
    memory_section: str = "",
    history_section: str = "",
    max_retries: int = MAX_DECOMPOSITION_RETRIES,
) -> Dict[str, Any]:
    """
    Call query decomposition with retry logic.
    
    Returns the parsed plan or raises an exception after max retries.
    """
    try:
        from query_decomposition import query_decomposition_call
    except ImportError as e:
        logger.warning("Query decomposition module not available: %s", e)
        return None
    
    last_error = None
    for attempt in range(max_retries):
        try:
            result = query_decomposition_call(
                user_input=user_input,
                chapter_name=chapter_name,
                sub_topic=sub_topic,
                study_material=study_material,
                stringified=stringified,
                memory_section=memory_section,
                history_section=history_section,
            )
            
            # Validate the result structure
            if result and isinstance(result, list) and len(result) > 0:
                plan = result[0]
                if "output_steps" in plan and len(plan.get("output_steps", [])) > 0:
                    return plan
            
            print(f"Query decomposition attempt {attempt + 1}: Invalid plan structure")
            last_error = "Invalid plan structure returned"
            
        except Exception as e:
            print(f"Query decomposition attempt {attempt + 1} failed: {e}")
            last_error = str(e)
    
    # All retries exhausted
    raise Exception(f"Query decomposition failed after {max_retries} attempts: {last_error}")


def _map_decomposition_tool_to_tool_type(tool_name: str) -> Optional[ToolType]:
    """Map query decomposition tool names to ToolType enum."""
    tool_mapping = {
        "chitchat": ToolType.CHITCHAT,
        "supplement": ToolType.SUPPLEMENT,
        "youtube_search": ToolType.SUPPLEMENT,  # youtube_search maps to supplement
        "book_calendar": ToolType.BOOK_CALENDAR,
        "minigame": ToolType.MINIGAME,
        "study_material": ToolType.STUDY_MATERIAL,
        "summary": ToolType.STUDY_MATERIAL,  # summary uses study_material handler
        "arxiv": ToolType.STUDY_MATERIAL,  # arxiv uses study_material handler for now
        "final_response": None,  # final_response is handled specially
        "none": None,  # none means cannot fulfill
    }
    return tool_mapping.get(tool_name.lower())


def _detect_tool(query: str) -> ToolType:
    """Keyword-based tool detection (fallback only — use query_routing when possible)."""
    import re
    lower_query = query.lower()

    def word_in(word, text):
        return bool(re.search(r'\b' + re.escape(word) + r'\b', text))

    if any(kw in lower_query for kw in ["video", "youtube", "tutorial", "watch"]):
        return ToolType.SUPPLEMENT

    if any(kw in lower_query for kw in ["schedule", "calendar", "remind", "exam"]) or \
       any(word_in(kw, lower_query) for kw in ["book", "session"]):
        return ToolType.BOOK_CALENDAR

    if any(word_in(kw, lower_query) for kw in ["game", "break", "minigame", "relax", "fun"]):
        return ToolType.MINIGAME

    if any(word_in(kw, lower_query) for kw in ["hello", "hi", "joke", "thanks"]) or \
       "how are you" in lower_query or "thank you" in lower_query:
        return ToolType.CHITCHAT

    return ToolType.STUDY_MATERIAL


def _get_backend():
    """Lazy load backend functions."""
    try:
        from nodes import init_user_storage, load_user_state
        from states import convert_to_json_safe
        return {
            "init_user_storage": init_user_storage,
            "load_user_state": load_user_state,
            "convert_to_json_safe": convert_to_json_safe,
            "available": True,
        }
    except ImportError:
        return {"available": False}


@router.post("/route", response_model=ChatRouteResponse)
async def route_message(request: ChatRouteRequest):
    """
    Route a chat message to the appropriate tool.
    """
    try:
        from standalone_study_buddy_response_streaming import query_routing
        
        tool_str = query_routing(
            request.message,
            request.context or "",
            request.chat_history or [],
        )
        
        tool_map = {
            "chitchat": ToolType.CHITCHAT,
            "supplement": ToolType.SUPPLEMENT,
            "book_calendar": ToolType.BOOK_CALENDAR,
            "calendar": ToolType.BOOK_CALENDAR,
            "minigame": ToolType.MINIGAME,
            "study_material": ToolType.STUDY_MATERIAL,
            "unclear": ToolType.UNCLEAR,
        }

        tool = tool_map.get(tool_str.lower(), ToolType.UNCLEAR)
        return ChatRouteResponse(tool=tool, parameters=None)
        
    except ImportError:
        tool = _detect_tool(request.message)
        return ChatRouteResponse(tool=tool, parameters=None)


@router.get("/stream")
async def stream_chat_response(
    user_id: str = Query(..., description="User identifier"),
    message: str = Query(..., description="User's message"),
    tool: Optional[str] = Query(None, description="Pre-determined tool to use"),
    chapter_number: Optional[int] = Query(None, description="Current chapter number"),
    subtopic_number: Optional[int] = Query(None, description="Current subtopic number"),
    timezone: Optional[str] = Query(None, description="User's timezone (e.g., America/Los_Angeles)"),
    use_decomposition: bool = Query(True, description="Use query decomposition for routing"),
):
    """
    Stream a chat response using Server-Sent Events (SSE).
    
    This endpoint provides real-time streaming of LLM responses using SSE.
    The response format is:
    - data: {"chunk": "text"} - For text chunks
    - data: {"video": {...}} - For YouTube video results
    - data: {"calendar_event": {...}} - For calendar events
    - data: {"minigame_link": "url"} - For minigame links
    - data: {"tool": "tool_name"} - Indicates which tool is being used
    - data: {"plan": {...}} - Query decomposition plan (when multi_steps=true)
    - data: {"step_progress": {...}} - Step execution progress
    - data: {"done": true} - Stream complete
    - data: {"error": "message"} - On error
    - data: {"plan_failed": "message"} - Plan creation failed
    """
    # ── fast context load (replaces load_user_state + Pydantic reconstruction) ──
    sub_idx = subtopic_number or 0
    try:
        from fast_store import get_store
        _fs = get_store(SAVE_TO)
        ctx = _fs.load_context(user_id, sub_idx)
    except Exception as _fse:
        logger.warning("[stream] fast_store unavailable, falling back: %s", _fse)
        ctx = {}

    # Fall back to JSON load only when fast_store has no state yet
    if not ctx:
        _backend = _get_backend()
        if _backend["available"]:
            _backend["init_user_storage"](SAVE_TO, user_id)
            _raw = _backend["load_user_state"](user_id)
            _safe = _backend["convert_to_json_safe"](_raw) if _raw else {}
            _curriculum = (_safe.get("curriculum") or [{}])[0]
            _chapter = _curriculum.get("active_chapter") or {}
            _subs = _chapter.get("sub_topics") or []
            _sub = _subs[sub_idx] if sub_idx < len(_subs) else {}
            ctx = {
                "user_id": user_id,
                "study_buddy_name": _safe.get("study_buddy_name", "Study Buddy"),
                "study_buddy_preference": _safe.get("study_buddy_preference", "friendly"),
                "study_buddy_persona": _safe.get("study_buddy_persona", ""),
                "chapter_name": _chapter.get("name", ""),
                "subtopic_name": _sub.get("sub_topic", ""),
                "study_material": _sub.get("study_material", ""),
                "quizzes": _sub.get("quizzes", []),
            }

    async def generate_stream():
        """Generate SSE stream of response chunks."""
        import asyncio

        try:
            # Try to use real backend with true LLM streaming
            from standalone_study_buddy_response_streaming import inference_call, STUDY_BUDDY_SYS_PROMPT
            from study_buddy_streaming_tools import (
                execute_tool_chitchat,
                execute_tool_supplement,
                execute_tool_calendar,
            )
            from search_and_filter_docs_streaming import filter_documents_by_file_name

            buddy_name     = ctx.get("study_buddy_name", "Study Buddy")
            buddy_pref     = ctx.get("study_buddy_preference", "friendly and helpful")
            buddy_persona  = ctx.get("study_buddy_persona") or buddy_pref
            chapter_name   = ctx.get("chapter_name", "General Studies")
            subtopic_name  = ctx.get("subtopic_name", "")
            study_material = ctx.get("study_material", "")
            quizzes        = ctx.get("quizzes", [])

            # Rebuild safe_user for tools that still expect a dict shaped like the old user state
            safe_user = {
                "user_id": user_id,
                "study_buddy_name": buddy_name,
                "study_buddy_preference": buddy_pref,
                "study_buddy_persona": buddy_persona,
                "curriculum": [{
                    "active_chapter": {
                        "name": chapter_name,
                        "sub_topics": [{"sub_topic": subtopic_name, "study_material": study_material, "quizzes": quizzes}],
                    }
                }],
            }
            
            # ── Memory context: fast_store reads (non-blocking, no LLM) ──────────
            memory_context = ""
            history_summary = ""
            try:
                from fast_store import get_store as _gs
                _fs = _gs(SAVE_TO)
                history_summary = _fs.build_history_string(user_id, n=5)
                memory_context  = _fs.read_memory_summary(user_id)
            except Exception:
                # Fall back to old memory_ops if fast_store hasn't been populated yet
                memory_ops = _get_memory_ops(user_id)
                memory_context, history_summary = _get_memory_context(memory_ops, message)
            
            # Track the full response for saving to memory later
            full_response_chunks = []
            
            print(f"[DEBUG] ===== STREAM START user={user_id} =====", flush=True)
            print(f"[DEBUG] Memory context available: {bool(memory_context)}", flush=True)
            print(f"[DEBUG] History summary available: {bool(history_summary)}", flush=True)
            
            # ============================================
            # Query Decomposition Integration
            # ============================================
            plan = None
            selected_tool = None
            
            print(f"[DEBUG] use_decomposition={use_decomposition}, tool={tool}", flush=True)
            print(f"[DEBUG] message (len={len(message)}): {message}", flush=True)
            
            if use_decomposition and not tool:
                print("[DEBUG] Calling query decomposition...", flush=True)
                try:
                    # Call query decomposition with retry
                    # NOTE: Don't pass full memory_context to decomposition - it confuses the
                    # smaller LLM model. Decomposition only needs to analyze what tools are
                    # required. Full memory context is injected during actual tool execution.
                    # Only pass a minimal indicator if there's conversation history.
                    minimal_history_hint = ""
                    if history_summary:
                        minimal_history_hint = "\n- Note: User has prior conversation history with this topic."
                    
                    plan = _call_query_decomposition_with_retry(
                        user_input=message,
                        chapter_name=chapter_name,
                        sub_topic=subtopic_name,
                        study_material=study_material[:2000] if study_material else "",
                        stringified=json.dumps(quizzes[:3]) if quizzes else "",
                        memory_section="",  # Don't pass full memory to decomposition
                        history_section=minimal_history_hint,
                    )
                    
                    print(f"[DEBUG] Decomposition result: {plan}", flush=True)
                    
                    if plan:
                        multi_steps = plan.get("multi_steps", False)
                        output_steps = plan.get("output_steps", [])
                        print(f"[DEBUG] multi_steps={multi_steps}, steps={len(output_steps)}, tools={[s.get('tool_name') for s in output_steps]}", flush=True)
                        
                        # Send the plan to frontend
                        yield f"data: {json.dumps({'plan': plan})}\n\n"
                        await asyncio.sleep(0)
                        
                        # Check if tool is "none" (cannot fulfill)
                        if output_steps and output_steps[0].get("tool_name") == "none":
                            rationale = output_steps[0].get("rationale", "This request cannot be fulfilled with available tools.")
                            yield f"data: {json.dumps({'chunk': rationale})}\n\n"
                            yield f"data: {json.dumps({'done': True})}\n\n"
                            return
                        
                        if multi_steps:
                            # ============================================
                            # Multi-Step Execution
                            # ============================================
                            accumulated_context = []  # Store results from each step
                            
                            try:
                                for step in output_steps:
                                    step_nr = step.get("step_nr", 0)
                                    tool_name = step.get("tool_name", "")
                                    rationale = step.get("rationale", "")
                                    
                                    print(f"[DEBUG] ===== STEP {step_nr}: {tool_name} =====", flush=True)
                                    # Send step progress
                                    yield f"data: {json.dumps({'step_progress': {'step_nr': step_nr, 'tool_name': tool_name, 'status': 'executing', 'rationale': rationale}})}\n\n"
                                    await asyncio.sleep(0)
                                    
                                    # Map to ToolType
                                    mapped_tool = _map_decomposition_tool_to_tool_type(tool_name)
                                    
                                    if tool_name == "final_response":
                                        # Final response - synthesize all accumulated context
                                        print(f"[DEBUG] Final response step, accumulated_context: {accumulated_context}")
                                        yield f"data: {json.dumps({'tool': 'study_material'})}\n\n"
                                        
                                        # Build context from accumulated results
                                        context_summary = "\n\n".join(accumulated_context) if accumulated_context else "Previous steps completed."
                                        
                                        # Include memory context for personalized final response
                                        memory_section = ""
                                        if memory_context or history_summary:
                                            memory_section = f"""

=== YOUR MEMORY OF THIS STUDENT ===
{history_summary if history_summary else ''}
{memory_context if memory_context else ''}
===================================
Use your memory to personalize the response when relevant.
"""
                                        
                                        system_prompt = f"""You are {buddy_name}, a helpful AI study companion. Your personality: {buddy_persona}.
{memory_section}
The user asked: "{message}"

You have completed the following steps to answer their request:
{context_summary}

IMPORTANT INSTRUCTIONS:
1. Provide a comprehensive final response that synthesizes all the information gathered.
2. If a YouTube video was found, it is ALREADY EMBEDDED in the UI - DO NOT generate fake or placeholder YouTube links.
3. You may reference the video by its actual title if one was found, but DO NOT make up URLs.
4. If no video was found, suggest the user search YouTube manually with specific search terms.
5. Be helpful and friendly.
6. Reference past conversations naturally if your memory is relevant to this topic.

Now provide your response:"""
                                        
                                        print(f"[DEBUG] Calling inference_call for final_response...")
                                        chunk_count = 0
                                        try:
                                            for chunk in inference_call(system_prompt, message, stream_to_console=False):
                                                chunk_count += 1
                                                full_response_chunks.append(chunk)
                                                yield f"data: {json.dumps({'chunk': chunk})}\n\n"
                                                await asyncio.sleep(0)
                                            print(f"[DEBUG] inference_call completed, {chunk_count} chunks sent")
                                        except Exception as e:
                                            print(f"[DEBUG] inference_call error: {e}")
                                            yield f"data: {json.dumps({'chunk': f'Error generating response: {str(e)}'})}\n\n"
                                        
                                        # Mark step complete
                                        yield f"data: {json.dumps({'step_progress': {'step_nr': step_nr, 'tool_name': tool_name, 'status': 'completed'}})}\n\n"
                                    
                                    elif mapped_tool == ToolType.SUPPLEMENT:
                                        # YouTube search - use topic context for better results
                                        yield f"data: {json.dumps({'tool': 'supplement'})}\n\n"
                                        
                                        # Construct a topic-aware search query
                                        # Extract keywords from the rationale if it mentions specific concepts
                                        raw_topic = subtopic_name or chapter_name or "educational content"
                                        
                                        # Clean the topic name - remove number prefixes like " 0:", " 1:", etc.
                                        if ':' in raw_topic:
                                            prefix = raw_topic.split(':', 1)[0].strip()
                                            if prefix.isdigit():
                                                search_topic = raw_topic.split(':', 1)[1].strip()
                                            else:
                                                search_topic = raw_topic
                                        else:
                                            search_topic = raw_topic
                                        
                                        # Try to extract a better search term from rationale or accumulated context
                                        # The rationale from query decomposition often contains what the user wants to learn
                                        search_keywords = []
                                        
                                        # Check rationale for specific concepts
                                        if rationale:
                                            # Extract quoted terms or key phrases from rationale
                                            import re
                                            quoted_terms = re.findall(r'"([^"]+)"', rationale)
                                            if quoted_terms:
                                                search_keywords.extend(quoted_terms)
                                            # Look for "about X" or "explaining X" patterns
                                            about_match = re.search(r'(?:about|explaining|on)\s+(?:the\s+)?(.+?)(?:\s+concept|\s+topic|\.|$)', rationale.lower())
                                            if about_match:
                                                search_keywords.append(about_match.group(1).strip())
                                        
                                        # Check accumulated context for concepts mentioned
                                        # The study_material step now provides actual LLM analysis
                                        if accumulated_context:
                                            last_context = accumulated_context[-1] if accumulated_context else ""
                                            
                                            # Try multiple patterns to extract key topics from the analysis
                                            extraction_patterns = [
                                                # Look for "most important concept is X" patterns
                                                r'(?:most\s+important|key|main|central)\s+(?:concept|topic|idea|theme|subject)\s+(?:is|:)\s*([^.]+)',
                                                # Look for quoted terms
                                                r'"([^"]+)"',
                                                # Look for "concept: X" or "concept of X" patterns
                                                r'concept[:\s]+(?:of\s+)?([^.]+)',
                                                # Look for "focuses on X" or "about X" patterns
                                                r'(?:focuses?\s+on|about|discusses?|covers?)\s+([^.]+)',
                                                # Look for "Key findings: X" patterns from our analysis format
                                                r'Key findings:\s*([^.]+)',
                                            ]
                                            
                                            for pattern in extraction_patterns:
                                                matches = re.findall(pattern, last_context, re.IGNORECASE)
                                                if matches:
                                                    # Clean and add the first match
                                                    extracted = matches[0].strip()
                                                    # Remove common filler words and limit length
                                                    extracted = re.sub(r'^(?:the|a|an)\s+', '', extracted, flags=re.IGNORECASE)
                                                    if len(extracted) > 5 and len(extracted) < 100:  # Reasonable topic length
                                                        search_keywords.append(extracted[:60])  # Limit query length
                                                        print(f"[DEBUG] Extracted keyword from context: '{extracted[:60]}' (pattern: {pattern[:30]}...)", flush=True)
                                                        break
                                        
                                        # Build the final search query
                                        if search_keywords:
                                            # Use the most specific keyword found
                                            search_query = f"{search_keywords[0]} tutorial explained"
                                        else:
                                            # Fall back to topic-based search
                                            search_query = f"{search_topic} tutorial"
                                            if "video" in rationale.lower():
                                                search_query = f"{search_topic} video tutorial"
                                            elif "explanation" in rationale.lower() or "explain" in rationale.lower():
                                                search_query = f"{search_topic} explained"
                                        
                                        print(f"[DEBUG] YouTube search query: '{search_query}' (topic: {search_topic}, keywords: {search_keywords})", flush=True)
                                        result = execute_tool_supplement(message, search_query, safe_user)
                                        
                                        # Debug: Print full result structure
                                        print(f"[DEBUG] execute_tool_supplement result:", flush=True)
                                        print(f"  - keys: {result.keys()}", flush=True)
                                        print(f"  - success: {result.get('success')}", flush=True)
                                        print(f"  - video_data type: {type(result.get('video_data'))}", flush=True)
                                        print(f"  - video_data value: {result.get('video_data')}", flush=True)
                                        
                                        video_data = result.get("video_data")
                                        # Check if video_data exists and is not None/False
                                        if video_data is not None and video_data:
                                            video_payload = {'video': video_data}
                                            print(f"[DEBUG] Sending video SSE: {json.dumps(video_payload)[:200]}", flush=True)
                                            yield f"data: {json.dumps(video_payload)}\n\n"
                                            # Include actual video details in context so LLM knows about it
                                            video_title = video_data.get('title', 'video')
                                            video_url = video_data.get('watchUrl', '') or f"https://www.youtube.com/watch?v={video_data.get('id', '')}"
                                            accumulated_context.append(
                                                f"Step {step_nr} (YouTube Search): Successfully found and embedded video:\n"
                                                f"  - Title: \"{video_title}\"\n"
                                                f"  - URL: {video_url}\n"
                                                f"  - Channel: {video_data.get('channelName', 'Unknown')}\n"
                                                f"  NOTE: This video is already embedded in the UI below. DO NOT generate fake or placeholder links."
                                            )
                                        else:
                                            print(f"[DEBUG] video_data is None or falsy. result.get('success'): {result.get('success')}", flush=True)
                                            accumulated_context.append(f"Step {step_nr} (YouTube Search): No relevant video found for '{search_topic}'. Suggest user search YouTube manually.")
                                        yield f"data: {json.dumps({'step_progress': {'step_nr': step_nr, 'tool_name': tool_name, 'status': 'completed'}})}\n\n"
                                    
                                    elif mapped_tool == ToolType.BOOK_CALENDAR:
                                        # Calendar booking
                                        print(f"[DEBUG] ===== CALENDAR STEP {step_nr} =====", flush=True)
                                        print(f"[DEBUG] Calendar step starting, rationale: {rationale}", flush=True)
                                        print(f"[DEBUG] Original message: {message}", flush=True)
                                        yield f"data: {json.dumps({'tool': 'book_calendar'})}\n\n"
                                        await asyncio.sleep(0)
                                        
                                        # Extract calendar-specific details from the user's original message
                                        # The user's message has the actual calendar request
                                        import re
                                        import concurrent.futures
                                        
                                        # Try to extract time-related phrases from the original message
                                        calendar_patterns = [
                                            r'book\s+(?:myself\s+)?(?:for\s+)?(\d+\s*(?:hour|hr|hours|hrs)s?\s+(?:on\s+)?(?:this\s+)?(?:next\s+)?(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)[^,\.]*)',
                                            r'(\d+\s*(?:hour|hr|hours|hrs)s?\s+(?:on\s+)?(?:this\s+)?(?:next\s+)?(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)[^,\.]*)',
                                            r'((?:this\s+|next\s+)?(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\s+(?:for\s+)?\d+\s*(?:hour|hr|hours|hrs)s?[^,\.]*)',
                                            r'((?:schedule|book|reserve)\s+[^,\.]+(?:hour|hr|hours|hrs|pm|am|morning|afternoon|evening)[^,\.]*)',
                                        ]
                                        
                                        calendar_query = None
                                        for pattern in calendar_patterns:
                                            match = re.search(pattern, message, re.IGNORECASE)
                                            if match:
                                                calendar_query = match.group(1).strip()
                                                break
                                        
                                        # If no pattern matched, use rationale if it has time info, otherwise use full message
                                        if not calendar_query:
                                            if any(kw in rationale.lower() for kw in ['hour', 'friday', 'monday', 'tuesday', 'wednesday', 'thursday', 'saturday', 'sunday', 'pm', 'am']):
                                                calendar_query = rationale
                                            else:
                                                # Extract the calendar-related part from the message
                                                # Look for "finally book..." or "book myself..." patterns
                                                book_match = re.search(r'(?:finally\s+)?book\s+(?:myself\s+)?(.+?)(?:\.|$)', message, re.IGNORECASE)
                                                if book_match:
                                                    calendar_query = f"book {book_match.group(1).strip()}"
                                                else:
                                                    calendar_query = message
                                        
                                        print(f"[DEBUG] Calendar query: '{calendar_query}' (extracted from message)", flush=True)
                                        print(f"[DEBUG] About to call calendar service in thread pool...", flush=True)
                                        
                                        # Calendar service makes a blocking LLM call internally
                                        # Run it in a thread pool to avoid blocking the async generator
                                        # and send keepalive messages while waiting
                                        try:
                                            print(f"[DEBUG] Getting event loop...", flush=True)
                                            loop = asyncio.get_event_loop()
                                            print(f"[DEBUG] Event loop obtained: {loop}", flush=True)
                                            
                                            # Create a future for the blocking calendar call
                                            print(f"[DEBUG] Creating ThreadPoolExecutor...", flush=True)
                                            with concurrent.futures.ThreadPoolExecutor() as executor:
                                                print(f"[DEBUG] Creating future with run_in_executor...", flush=True)
                                                future = loop.run_in_executor(
                                                    executor,
                                                    lambda: execute_tool_calendar(message, calendar_query, safe_user, timezone=timezone)
                                                )
                                                print(f"[DEBUG] Future created, entering keepalive loop...", flush=True)
                                                
                                                # Send keepalive messages while waiting for the calendar service
                                                # CRITICAL: Keep the connection alive during the blocking calendar operation
                                                keepalive_count = 0
                                                while not future.done():
                                                    await asyncio.sleep(0.5)  # Check every 0.5 seconds
                                                    keepalive_count += 1
                                                    # Send keepalive as data event every second to prevent connection timeout
                                                    if keepalive_count % 2 == 0:
                                                        print(f"[DEBUG] Sending keepalive {keepalive_count//2}...", flush=True)
                                                        # Send as a JSON data event (not just a comment) to ensure connection stays alive
                                                        yield f"data: {json.dumps({'status': 'processing', 'step': 'calendar', 'elapsed_seconds': keepalive_count//2})}\n\n"
                                                        await asyncio.sleep(0)
                                                    # Timeout after 90 seconds (increased from 60)
                                                    if keepalive_count > 180:
                                                        print(f"[DEBUG] Calendar operation timed out after 90 seconds", flush=True)
                                                        break
                                                
                                                print(f"[DEBUG] Keepalive loop done, awaiting future result...", flush=True)
                                                
                                                # Check if we timed out
                                                if not future.done():
                                                    print(f"[DEBUG] Calendar operation timed out, cancelling future...", flush=True)
                                                    future.cancel()
                                                    result = {
                                                        "success": False,
                                                        "calendar_status": "Calendar operation timed out. Please try a simpler time description."
                                                    }
                                                else:
                                                    result = await future
                                                    print(f"[DEBUG] Future result received: {result.keys() if result else 'None'}", flush=True)
                                            
                                            print(f"[DEBUG] Calendar result success: {result.get('success')}, has event_data: {bool(result.get('event_data'))}", flush=True)
                                            
                                            if result.get("event_data"):
                                                print(f"[DEBUG] Sending calendar_event SSE...", flush=True)
                                                yield f"data: {json.dumps({'calendar_event': result['event_data']})}\n\n"
                                                await asyncio.sleep(0)
                                                accumulated_context.append(f"Step {step_nr} (Calendar): Created event - {result.get('event_data', {}).get('title', 'event')}")
                                            else:
                                                error_msg = result.get('calendar_status', 'Unknown error')
                                                print(f"[DEBUG] Calendar failed: {error_msg}", flush=True)
                                                accumulated_context.append(f"Step {step_nr} (Calendar): Could not create event - {error_msg}")
                                        except Exception as calendar_error:
                                            print(f"[DEBUG] Calendar exception: {calendar_error}", flush=True)
                                            import traceback
                                            traceback.print_exc()
                                            accumulated_context.append(f"Step {step_nr} (Calendar): Error - {str(calendar_error)}")
                                        
                                        print(f"[DEBUG] Calendar step {step_nr} complete, sending status update...", flush=True)
                                        yield f"data: {json.dumps({'step_progress': {'step_nr': step_nr, 'tool_name': tool_name, 'status': 'completed'}})}\n\n"
                                        await asyncio.sleep(0)
                                        print(f"[DEBUG] Calendar step {step_nr} status sent.", flush=True)
                                    
                                    elif mapped_tool == ToolType.STUDY_MATERIAL:
                                        # Study material / summary / arxiv
                                        yield f"data: {json.dumps({'tool': 'study_material'})}\n\n"
                                        
                                        # For intermediate steps, actually call LLM to analyze the material
                                        # This ensures we extract meaningful context for subsequent steps (like youtube_search)
                                        topic = subtopic_name or chapter_name or 'current topic'
                                        
                                        # Build a focused prompt to extract key concepts
                                        analysis_prompt = f"""You are analyzing study material for a student.

Study Material Context:
- Topic: {topic}
- Material: {study_material[:3000] if study_material else "No specific material loaded."}

User Request: {message}
Step Rationale: {rationale}

Please provide a brief but specific analysis:
1. Identify the MOST IMPORTANT concept or topic from this material that relates to the user's request
2. Be specific - name the concept clearly
3. Keep your response focused and under 150 words

Your analysis:"""
                                        
                                        try:
                                            # Collect the analysis while sending keepalive messages
                                            # This prevents connection timeouts during LLM processing
                                            analysis_chunks = []
                                            chunk_count = 0
                                            last_keepalive = 0
                                            
                                            # NOTE: user_prompt cannot be empty - API requires at least 1 char
                                            # Use the original message as context for the analysis
                                            for chunk in inference_call(analysis_prompt, message or "Analyze this material.", stream_to_console=False):
                                                analysis_chunks.append(chunk)
                                                chunk_count += 1
                                                
                                                # Send keepalive every 10 chunks to prevent timeout
                                                if chunk_count - last_keepalive >= 10:
                                                    # Send an empty comment as keepalive (SSE comment format)
                                                    yield f": keepalive\n\n"
                                                    await asyncio.sleep(0)
                                                    last_keepalive = chunk_count
                                            
                                            analysis_result = "".join(analysis_chunks).strip()
                                            print(f"[DEBUG] Study material analysis result ({chunk_count} chunks): {analysis_result[:200]}...", flush=True)
                                            
                                            # Store the extracted concept for subsequent steps
                                            accumulated_context.append(
                                                f"Step {step_nr} ({tool_name}): Analyzed study material for '{topic}'.\n"
                                                f"Key findings: {analysis_result}"
                                            )
                                        except Exception as analysis_error:
                                            print(f"[DEBUG] Study material analysis error: {analysis_error}", flush=True)
                                            accumulated_context.append(f"Step {step_nr} ({tool_name}): Analyzed study material for '{topic}'")
                                        
                                        yield f"data: {json.dumps({'step_progress': {'step_nr': step_nr, 'tool_name': tool_name, 'status': 'completed'}})}\n\n"
                                    
                                    elif mapped_tool == ToolType.MINIGAME:
                                        # Minigame
                                        yield f"data: {json.dumps({'tool': 'minigame'})}\n\n"
                                        games_url = os.environ.get("GAMES_URL", "http://localhost:8080")
                                        yield f"data: {json.dumps({'minigame_link': games_url})}\n\n"
                                        accumulated_context.append(f"Step {step_nr} (Minigame): Provided game link for study break")
                                        yield f"data: {json.dumps({'step_progress': {'step_nr': step_nr, 'tool_name': tool_name, 'status': 'completed'}})}\n\n"
                                    
                                    elif mapped_tool == ToolType.CHITCHAT:
                                        # Chitchat (unlikely in multi-step but handle it)
                                        yield f"data: {json.dumps({'tool': 'chitchat'})}\n\n"
                                        accumulated_context.append(f"Step {step_nr} (Chitchat): Casual conversation")
                                        yield f"data: {json.dumps({'step_progress': {'step_nr': step_nr, 'tool_name': tool_name, 'status': 'completed'}})}\n\n"
                                    
                                    await asyncio.sleep(0.1)  # Small delay between steps for UI
                                
                                # Save multi-step interaction to memory
                                if full_response_chunks:
                                    full_response = "".join(full_response_chunks)
                                    asyncio.create_task(
                                        _save_interaction_to_memory(
                                            memory_ops,
                                            user_message=message,
                                            bot_response=full_response,
                                            background=True
                                        )
                                    )
                                
                                yield f"data: {json.dumps({'done': True})}\n\n"
                                return
                            except Exception as step_error:
                                # Handle errors during multi-step execution
                                print(f"[DEBUG] Multi-step execution error: {step_error}", flush=True)
                                import traceback
                                traceback.print_exc()
                                yield f"data: {json.dumps({'error': f'Multi-step execution failed: {str(step_error)}. Please try again with a simpler query.'})}\n\n"
                                yield f"data: {json.dumps({'done': True})}\n\n"
                                return
                        
                        else:
                            # Single step - get the tool from the plan
                            if output_steps:
                                tool_name = output_steps[0].get("tool_name", "study_material")
                                selected_tool = _map_decomposition_tool_to_tool_type(tool_name)
                                
                                # If final_response for single step, use study_material handler
                                if tool_name == "final_response":
                                    selected_tool = ToolType.STUDY_MATERIAL
                                elif selected_tool is None:
                                    selected_tool = ToolType.STUDY_MATERIAL
                    
                except Exception as e:
                    # Query decomposition failed - send error and fall back
                    print(f"[DEBUG] Query decomposition failed: {e}")
                    yield f"data: {json.dumps({'plan_failed': PLAN_FAILURE_MESSAGE})}\n\n"
                    # TODO: Insert guardrail/judge here in future
                    # Fall back to keyword-based detection
                    selected_tool = None
            else:
                print(f"[DEBUG] Skipping decomposition: use_decomposition={use_decomposition}, tool={tool}")
            
            # Fall back to provided tool or keyword detection
            if selected_tool is None:
                if tool:
                    try:
                        selected_tool = ToolType(tool)
                    except ValueError:
                        selected_tool = ToolType.STUDY_MATERIAL
                else:
                    selected_tool = _detect_tool(message)
            
            # Send the tool being used
            yield f"data: {json.dumps({'tool': selected_tool.value})}\n\n"
            
            # ============================================
            # Single Tool Execution (original logic)
            # ============================================
            if selected_tool == ToolType.CHITCHAT:
                # For chitchat, include study context and memory so LLM knows the student
                study_context = ""
                if study_material:
                    study_context = f"""

The student is currently studying: {chapter_name}
Current topic: {subtopic_name}
Study material excerpt:
{study_material[:1500] if study_material else 'No material loaded yet.'}

When the student asks questions, use this context to provide relevant answers about their studies."""
                
                # Add memory context for personalized responses
                memory_section = ""
                if memory_context or history_summary:
                    memory_section = f"""

=== YOUR MEMORY OF THIS STUDENT ===
{history_summary if history_summary else ''}
{memory_context if memory_context else ''}
===================================

Use your memory to personalize responses. Reference past conversations naturally when relevant."""
                
                system_prompt = f"You are {buddy_name}, a friendly AI study companion. Your personality: {buddy_persona}. Have a casual, supportive conversation while helping with their studies. IMPORTANT: Respond directly without using <think> tags - just give your natural conversational response.{study_context}{memory_section}"
                print(f"[DEBUG] CHITCHAT system_prompt length: {len(system_prompt)} chars", flush=True)
                print(f"[DEBUG] CHITCHAT memory_section present: {bool(memory_section)}, len: {len(memory_section)}", flush=True)
                for chunk in inference_call(system_prompt, message, stream_to_console=False):
                    full_response_chunks.append(chunk)
                    yield f"data: {json.dumps({'chunk': chunk})}\n\n"
                    await asyncio.sleep(0)  # Allow other tasks
                    
            elif selected_tool == ToolType.SUPPLEMENT:
                result = execute_tool_supplement(message, message, safe_user)
                if result.get("video_data"):
                    yield f"data: {json.dumps({'video': result['video_data']})}\n\n"
                response_text = result.get("output", "Here are some videos I found for you!")
                # Stream the text response
                for chunk in response_text.split('. '):
                    if chunk:
                        yield f"data: {json.dumps({'chunk': chunk + '. '})}\n\n"
                        await asyncio.sleep(0.05)
                        
            elif selected_tool == ToolType.BOOK_CALENDAR:
                result = execute_tool_calendar(message, message, safe_user, timezone=timezone)
                if result.get("event_data"):
                    yield f"data: {json.dumps({'calendar_event': result['event_data']})}\n\n"
                response_text = result.get("output", "I've created a calendar event for you!")
                for chunk in response_text.split('. '):
                    if chunk:
                        yield f"data: {json.dumps({'chunk': chunk + '. '})}\n\n"
                        await asyncio.sleep(0.05)
                        
            elif selected_tool == ToolType.MINIGAME:
                games_url = os.environ.get("GAMES_URL", "http://localhost:8080")
                yield f"data: {json.dumps({'minigame_link': games_url})}\n\n"
                response_text = "Time for a study break! Click the link to play some games and recharge."
                for chunk in response_text.split():
                    yield f"data: {json.dumps({'chunk': chunk + ' '})}\n\n"
                    await asyncio.sleep(0.03)

            elif selected_tool == ToolType.UNCLEAR:
                clarification = (
                    "I'm not quite sure what you're looking for — could you give me a bit more context? "
                    "For example, are you asking about the study material, want a YouTube video, "
                    "need to schedule a session, or something else?"
                )
                for chunk in clarification.split():
                    yield f"data: {json.dumps({'chunk': chunk + ' '})}\n\n"
                    await asyncio.sleep(0.02)

            else:  # STUDY_MATERIAL - use full streaming with RAG context
                # Build the full system prompt with context and memory
                system_prompt = STUDY_BUDDY_SYS_PROMPT.format(
                    study_buddy_name=buddy_name,
                    user_preference=buddy_persona,
                    chapter_name=chapter_name,
                    sub_topic=subtopic_name,
                    study_material=study_material[:2000] if study_material else "No study material available yet.",
                    list_of_quizzes=str(quizzes[:3]) if quizzes else "No quizzes available.",
                    user_input=message,
                    memory_context=memory_context,
                    history_summary=history_summary,
                )
                
                # True LLM streaming - yield each token as it arrives
                for chunk in inference_call(system_prompt, message, stream_to_console=False):
                    full_response_chunks.append(chunk)
                    yield f"data: {json.dumps({'chunk': chunk})}\n\n"
                    await asyncio.sleep(0)  # Allow other async tasks to run
            
            # ============================================
            # Save Interaction to Memory (Non-Blocking)
            # Save turn to fast_store (non-blocking append — O(1), no LLM call)
            if full_response_chunks:
                full_response = "".join(full_response_chunks)
                try:
                    from fast_store import get_store as _gs
                    asyncio.create_task(
                        _gs(SAVE_TO).append_turn_async(user_id, message, full_response)
                    )
                except Exception:
                    pass  # memory write failure must never block the response
            
            yield f"data: {json.dumps({'done': True})}\n\n"
            
        except ImportError as e:
            logger.warning("Backend import failed; using mock responses: %s", e)
            
            # Detect tool using keyword-based detection (since backend imports failed)
            # This handles the case where imports fail before selected_tool is set
            try:
                detected_tool = selected_tool
            except (UnboundLocalError, NameError):
                detected_tool = _detect_tool(message)
            
            # Send tool indicator
            yield f"data: {json.dumps({'tool': detected_tool.value if detected_tool else 'study_material'})}\n\n"
            
            # Use mock responses with simulated streaming
            mock_responses = {
                ToolType.CHITCHAT: "Hello! I'm here to help you with your studies. What would you like to learn about today?",
                ToolType.SUPPLEMENT: "I found some helpful videos for you! (Video search is being configured)",
                ToolType.BOOK_CALENDAR: "I can help you schedule a study session. (Calendar integration is being configured)",
                ToolType.MINIGAME: "Time for a study break! Visit the games page to relax.",
                ToolType.STUDY_MATERIAL: f"Great question about '{message}'! Let me explain this concept from your study materials. This is a mock response - in production, this would use RAG to retrieve relevant content and generate a personalized explanation based on your curriculum.",
            }
            
            response = mock_responses.get(detected_tool, "I'm here to help!")
            
            if detected_tool == ToolType.MINIGAME:
                games_url = os.environ.get("GAMES_URL", "http://localhost:8080")
                yield f"data: {json.dumps({'minigame_link': games_url})}\n\n"
            
            # Simulate word-by-word streaming
            import asyncio
            words = response.split()
            for i in range(0, len(words), 2):
                chunk = " ".join(words[i:i+2]) + " "
                yield f"data: {json.dumps({'chunk': chunk})}\n\n"
                await asyncio.sleep(0.05)
            
            yield f"data: {json.dumps({'done': True})}\n\n"
            
        except asyncio.CancelledError:
            # Client disconnected - this is normal for SSE, just stop the stream
            print(f"[SSE] Client disconnected (cancelled)")
            return
        except (ConnectionResetError, BrokenPipeError) as e:
            # Client disconnected - this is normal for SSE
            print(f"[SSE] Client disconnected: {type(e).__name__}")
            return
        except GeneratorExit:
            # Generator was closed (client disconnect)
            print(f"[SSE] Generator closed (client disconnect)")
            return
        except Exception as e:
            logger.exception("Unhandled stream error")
            try:
                yield f"data: {json.dumps({'error': str(e)})}\n\n"
                yield f"data: {json.dumps({'done': True})}\n\n"
            except (ConnectionResetError, BrokenPipeError, GeneratorExit):
                # Can't send error to client - they're gone
                pass
    
    async def safe_stream_wrapper():
        """Wrapper to suppress client disconnection errors at the yield level."""
        try:
            async for data in generate_stream():
                yield data
        except asyncio.CancelledError:
            print(f"[SSE] Stream cancelled by client")
        except (ConnectionResetError, BrokenPipeError, GeneratorExit):
            print(f"[SSE] Client disconnected during streaming")
        except Exception as e:
            # Log unexpected errors but don't crash
            logger.exception("Unexpected error in SSE stream wrapper")
    
    return StreamingResponse(
        safe_stream_wrapper(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "Access-Control-Allow-Origin": "*",
        },
    )


@router.post("/message", response_model=ChatMessageResponse)
async def send_message(request: ChatStreamRequest):
    """
    Send a chat message and get a complete (non-streaming) response.
    Uses fast_store for context reads; falls back to JSON load only when state.txt absent.
    """
    try:
        from study_buddy_streaming_tools import (
            execute_tool_chitchat,
            execute_tool_supplement,
            execute_tool_calendar,
            execute_tool_study_material,
        )

        # ── Fast context load (no JSON parse, no Pydantic reconstruction) ─────
        subtopic_idx = request.subtopic_number or 0
        ctx: Dict[str, Any] = {}
        try:
            from fast_store import get_store
            ctx = get_store(SAVE_TO).load_context(request.user_id, subtopic_idx)
        except Exception as _fse:
            logger.warning("[/message] fast_store unavailable: %s", _fse)

        # Fall back to JSON load only when fast_store has no state yet
        if not ctx:
            backend = _get_backend()
            if backend["available"]:
                backend["init_user_storage"](SAVE_TO, request.user_id)
                _raw = backend["load_user_state"](request.user_id)
                _safe = backend["convert_to_json_safe"](_raw) if _raw else {}
                _curriculum = (_safe.get("curriculum") or [{}])[0]
                _chapter = _curriculum.get("active_chapter") or {}
                _subs = _chapter.get("sub_topics") or []
                _sub = _subs[subtopic_idx] if subtopic_idx < len(_subs) else {}
                ctx = {
                    "user_id": request.user_id,
                    "study_buddy_name": _safe.get("study_buddy_name", "Study Buddy"),
                    "study_buddy_preference": _safe.get("study_buddy_preference", "friendly"),
                    "study_buddy_persona": _safe.get("study_buddy_persona", ""),
                    "chapter_name": _chapter.get("name", ""),
                    "subtopic_name": _sub.get("sub_topic", ""),
                    "study_material": _sub.get("study_material", ""),
                    "quizzes": _sub.get("quizzes", []),
                }

        buddy_pref    = ctx.get("study_buddy_preference", "friendly and supportive")
        buddy_persona = ctx.get("study_buddy_persona") or buddy_pref
        chapter_name  = ctx.get("chapter_name", "")
        subtopic_name = ctx.get("subtopic_name", "")

        # Reconstruct safe_user dict for tools that expect the old shape
        safe_user = {
            "user_id": request.user_id,
            "study_buddy_name": ctx.get("study_buddy_name", "Study Buddy"),
            "study_buddy_preference": buddy_pref,
            "study_buddy_persona": buddy_persona,
            "curriculum": [{
                "active_chapter": {
                    "name": chapter_name,
                    "sub_topics": [{
                        "sub_topic": subtopic_name,
                        "study_material": ctx.get("study_material", ""),
                        "quizzes": ctx.get("quizzes", []),
                    }],
                }
            }],
        }

        # ── Load memory from fast_store (non-blocking reads) ──────────────────
        history_summary = ""
        memory_context = ""
        try:
            from fast_store import get_store as _gs
            _fs = _gs(SAVE_TO)
            history_summary = _fs.build_history_string(request.user_id, n=5)
            memory_context = _fs.read_memory_summary(request.user_id)
        except Exception:
            # Fall back to old memory_ops if fast_store memory not populated yet
            memory_ops = _get_memory_ops(request.user_id)
            memory_context, history_summary = _get_memory_context(memory_ops, request.message)

        # ── Tool selection — caller (the agent) MUST supply request.tool ──────
        # No host-side LLM router. The OpenClaw agent has its own LLM and
        # decides which handler to invoke; we just execute it deterministically.
        if not request.tool:
            raise HTTPException(
                status_code=400,
                detail=(
                    "request.tool is required. Call the MCP per-intent tool "
                    "(study_material_query, chitchat, or supplement_query) "
                    "instead of dispatching from the host."
                ),
            )
        selected_tool = request.tool

        # ── Execute the selected tool ─────────────────────────────────────────
        if selected_tool == ToolType.UNCLEAR:
            result = {
                "output": (
                    "I'm not quite sure what you're looking for — could you give me a bit more context? "
                    "For example, are you asking about the study material, want a YouTube video, "
                    "need to schedule a session, or something else?"
                )
            }
        elif selected_tool == ToolType.CHITCHAT:
            result = execute_tool_chitchat(request.message, safe_user, buddy_persona, history_summary or "")
        elif selected_tool == ToolType.SUPPLEMENT:
            result = execute_tool_supplement(request.message, request.message, safe_user)
        elif selected_tool == ToolType.BOOK_CALENDAR:
            result = execute_tool_calendar(request.message, request.message, safe_user)
        elif selected_tool == ToolType.MINIGAME:
            result = {"output": "Time for a study break!", "minigame_link": os.environ.get("GAMES_URL", "http://localhost:8080")}
        else:  # STUDY_MATERIAL
            result = execute_tool_study_material(
                request.message,
                safe_user,
                buddy_persona,
                memory_context or "",
                history_summary or "",
            )

        # Save turn to fast_store (non-blocking append — O(1) file write, no LLM)
        if result.get("output"):
            import asyncio as _asyncio
            try:
                from fast_store import get_store as _gs
                _asyncio.create_task(
                    _gs(SAVE_TO).append_turn_async(request.user_id, request.message, result["output"])
                )
            except Exception:
                pass  # memory write failure must never block the response

        return ChatMessageResponse(
            content=result.get("output", ""),
            tool_used=selected_tool,
            youtube_video=result.get("video_data"),
            calendar_event=result.get("event_data"),
            minigame_link=result.get("minigame_link"),
        )

    except ImportError as e:
        logger.warning("Backend import failed; using fallback: %s", e)
        selected_tool = request.tool or _detect_tool(request.message)
        mock_responses = {
            ToolType.CHITCHAT: "Hello! I'm your study buddy.",
            ToolType.SUPPLEMENT: "Here are some videos for you!",
            ToolType.BOOK_CALENDAR: "I've created a calendar event for you.",
            ToolType.MINIGAME: "Time for a study break!",
            ToolType.STUDY_MATERIAL: f"Let me explain about '{request.message}'...",
            ToolType.UNCLEAR: "Could you clarify what you're looking for?",
        }
        return ChatMessageResponse(
            content=mock_responses.get(selected_tool, "I'm here to help!"),
            tool_used=selected_tool,
            minigame_link=os.environ.get("GAMES_URL", "http://localhost:8080") if selected_tool == ToolType.MINIGAME else None,
        )


@router.post("/message-with-image")
async def send_message_with_image(
    user_id: str = Form(...),
    message: str = Form(...),
    subtopic_number: int = Form(0),
    images: List[UploadFile] = File(default=[]),
):
    """
    Send a chat message with optional image attachments.
    Uses VLM (Vision Language Model) for multimodal understanding.
    
    This endpoint handles:
    - Text-only messages (falls back to regular LLM)
    - Messages with images (uses VLM for analysis)
    """
    backend = _get_backend()
    
    if backend["available"]:
        backend["init_user_storage"](SAVE_TO, user_id)
    
    # Process uploaded images
    image_paths = []
    temp_files = []
    
    try:
        for img in images:
            if img.filename:
                # Save to temp file
                suffix = Path(img.filename).suffix or ".jpg"
                temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
                content = await img.read()
                temp_file.write(content)
                temp_file.close()
                temp_files.append(temp_file.name)
                image_paths.append(temp_file.name)
        
        # Try to use VLM backend
        try:
            from standalone_study_buddy_response_streaming import study_buddy_response
            
            user_state = backend["load_user_state"](user_id) if backend["available"] else None
            safe_user = backend["convert_to_json_safe"](user_state) if user_state else {"user_id": user_id}
            buddy_pref = safe_user.get("study_buddy_preference", "friendly and helpful")
            buddy_name = safe_user.get("study_buddy_name", "Study Buddy")
            # Use generated persona if available, otherwise fall back to raw preference
            buddy_persona = safe_user.get("study_buddy_persona") or buddy_pref
            
            # Get curriculum context
            curriculum_list = safe_user.get("curriculum", [])
            curriculum = curriculum_list[0] if curriculum_list else {}
            active_chapter = curriculum.get("active_chapter", {})
            chapter_name = active_chapter.get("name", "General Studies")
            subtopics = active_chapter.get("sub_topics", [])
            current_subtopic = subtopics[subtopic_number] if subtopics and subtopic_number < len(subtopics) else {}
            subtopic_name = current_subtopic.get("sub_topic", "")
            study_material = current_subtopic.get("study_material", "")
            quizzes = current_subtopic.get("quizzes", [])
            
            # Call study_buddy_response with image if provided
            uploaded_img_loc = image_paths[0] if image_paths else None
            
            response = study_buddy_response(
                chapter_name=chapter_name,
                sub_topic=subtopic_name,
                study_material=study_material,
                list_of_quizzes=quizzes,
                user_input=message,
                study_buddy_name=buddy_name,
                user_preference=buddy_persona,
                uploaded_img_loc=uploaded_img_loc,
                memory_context="",
                history_summary="",
            )
            
            return {
                "success": True,
                "content": response,
                "used_vlm": bool(image_paths),
                "images_processed": len(image_paths),
            }
            
        except ImportError:
            # VLM not available
            if image_paths:
                return {
                    "success": True,
                    "content": f"I received your image(s), but image analysis is not available right now. Regarding your question: '{message}' - I'd be happy to help once the VLM service is connected!",
                    "used_vlm": False,
                    "images_processed": len(image_paths),
                }
            else:
                return {
                    "success": True,
                    "content": f"Let me help you with: '{message}'. (VLM service not available)",
                    "used_vlm": False,
                    "images_processed": 0,
                }
                
    finally:
        # Clean up temp files
        for temp_path in temp_files:
            try:
                os.unlink(temp_path)
            except Exception:
                pass


@router.post("/stream-with-image")
async def stream_message_with_image(
    user_id: str = Form(...),
    message: str = Form(...),
    subtopic_number: int = Form(0),
    images: List[UploadFile] = File(default=[]),
):
    """
    Stream a chat response with optional image attachments.
    Uses VLM for image understanding, streams the response.
    """
    backend = _get_backend()
    
    if backend["available"]:
        backend["init_user_storage"](SAVE_TO, user_id)
    
    # Process uploaded images
    image_paths = []
    temp_files = []
    
    for img in images:
        if img.filename:
            suffix = Path(img.filename).suffix or ".jpg"
            temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
            content = await img.read()
            temp_file.write(content)
            temp_file.close()
            temp_files.append(temp_file.name)
            image_paths.append(temp_file.name)
    
    async def generate_stream():
        import asyncio
        
        try:
            # Indicate if images were received
            if image_paths:
                yield f"data: {json.dumps({'images_received': len(image_paths)})}\n\n"
            
            yield f"data: {json.dumps({'tool': 'study_material'})}\n\n"
            
            try:
                from standalone_study_buddy_response_streaming import study_buddy_response, inference_call
                
                user_state = backend["load_user_state"](user_id) if backend["available"] else None
                safe_user = backend["convert_to_json_safe"](user_state) if user_state else {"user_id": user_id}
                buddy_pref = safe_user.get("study_buddy_preference", "friendly and helpful")
                buddy_name = safe_user.get("study_buddy_name", "Study Buddy")
                # Use generated persona if available, otherwise fall back to raw preference
                buddy_persona = safe_user.get("study_buddy_persona") or buddy_pref
                
                # Get curriculum context
                curriculum_list = safe_user.get("curriculum", [])
                curriculum = curriculum_list[0] if curriculum_list else {}
                active_chapter = curriculum.get("active_chapter", {})
                chapter_name = active_chapter.get("name", "General Studies")
                subtopics = active_chapter.get("sub_topics", [])
                current_subtopic = subtopics[subtopic_number] if subtopics and subtopic_number < len(subtopics) else {}
                subtopic_name = current_subtopic.get("sub_topic", "")
                study_material = current_subtopic.get("study_material", "")
                quizzes = current_subtopic.get("quizzes", [])
                
                if image_paths:
                    # Use VLM (non-streaming, then chunk the response)
                    response = study_buddy_response(
                        chapter_name=chapter_name,
                        sub_topic=subtopic_name,
                        study_material=study_material,
                        list_of_quizzes=quizzes,
                        user_input=message,
                        study_buddy_name=buddy_name,
                        user_preference=buddy_persona,
                        uploaded_img_loc=image_paths[0],
                        memory_context="",
                        history_summary="",
                    )
                    
                    # Stream the VLM response word by word
                    words = response.split()
                    for i in range(0, len(words), 3):
                        chunk = " ".join(words[i:i+3]) + " "
                        yield f"data: {json.dumps({'chunk': chunk})}\n\n"
                        await asyncio.sleep(0.03)
                else:
                    # No images - use regular streaming LLM
                    from standalone_study_buddy_response_streaming import STUDY_BUDDY_SYS_PROMPT
                    
                    system_prompt = STUDY_BUDDY_SYS_PROMPT.format(
                        study_buddy_name=buddy_name,
                        user_preference=buddy_persona,
                        chapter_name=chapter_name,
                        sub_topic=subtopic_name,
                        study_material=study_material[:2000] if study_material else "No study material available.",
                        list_of_quizzes=str(quizzes[:3]) if quizzes else "No quizzes.",
                        user_input=message,
                        memory_context="",
                        history_summary="",
                    )
                    
                    for chunk in inference_call(system_prompt, message, stream_to_console=False):
                        yield f"data: {json.dumps({'chunk': chunk})}\n\n"
                        await asyncio.sleep(0)
                
                yield f"data: {json.dumps({'done': True})}\n\n"
                
            except ImportError:
                # Mock response
                if image_paths:
                    response = f"I received your image! While VLM analysis isn't available, I can see you're asking about: '{message}'. Let me help with the text portion of your question."
                else:
                    response = f"Great question about '{message}'! Let me explain this concept from your study materials."
                
                words = response.split()
                for i in range(0, len(words), 2):
                    chunk = " ".join(words[i:i+2]) + " "
                    yield f"data: {json.dumps({'chunk': chunk})}\n\n"
                    await asyncio.sleep(0.05)
                
                yield f"data: {json.dumps({'done': True})}\n\n"
                
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
            yield f"data: {json.dumps({'done': True})}\n\n"
        finally:
            # Clean up temp files
            for temp_path in temp_files:
                try:
                    os.unlink(temp_path)
                except Exception:
                    pass
    
    return StreamingResponse(
        generate_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "Access-Control-Allow-Origin": "*",
        },
    )