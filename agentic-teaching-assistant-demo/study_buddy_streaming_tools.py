"""
Streaming Query Router and Tool Execution for Study Buddy UI

This module provides streaming implementations of query routing and tool execution
with detailed step-by-step visualization in the chat interface.
"""

import os
import sys
from pathlib import Path
import asyncio
from typing import Generator, AsyncGenerator, Dict, Any, List, Tuple, Union
from datetime import datetime
import re

# Add parent directory to path
parent_dir = Path(__file__).parent.parent
if str(parent_dir) not in sys.path:
    sys.path.insert(0, str(parent_dir))

import gradio as gr
from colorama import Fore
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage

from nodes import load_user_state
from standalone_study_buddy_response_streaming import inference_call, query_routing, STUDY_BUDDY_SYS_PROMPT, study_buddy_response
from youtube_search import fetch_most_relevant_youtube_video
from calendar_assistant import create_event_with_ai
from services.calendar_service import CalendarService
from agent_memory import get_memory_ops
import json

# Study Break Games URL
GAMES_URL = os.environ.get("GAMES_URL", "http://localhost:8080")


def _env_float(name: str, default: float) -> float:
    raw_value = os.environ.get(name, str(default))
    try:
        return float(raw_value)
    except (ValueError, TypeError):
        print(
            f"[Config Warning] Invalid {name}={raw_value!r}; using default {default}",
            flush=True,
        )
        return default


def _env_int(name: str, default: int) -> int:
    raw_value = os.environ.get(name, str(default))
    try:
        return int(raw_value)
    except (ValueError, TypeError):
        print(
            f"[Config Warning] Invalid {name}={raw_value!r}; using default {default}",
            flush=True,
        )
        return default


YOUTUBE_MIN_RELEVANCE_SCORE = _env_float(
    "AGENTICTA_YOUTUBE_MIN_RELEVANCE_SCORE",
    15.0,
)
YOUTUBE_SOFT_RELEVANCE_SCORE = _env_float(
    "AGENTICTA_YOUTUBE_SOFT_RELEVANCE_SCORE",
    8.0,
)
YOUTUBE_MIN_KEYWORD_OVERLAP = _env_int(
    "AGENTICTA_YOUTUBE_MIN_KEYWORD_OVERLAP",
    1,
)

_YOUTUBE_GENERIC_TERMS = {
    "and",
    "for",
    "with",
    "from",
    "that",
    "this",
    "these",
    "those",
    "into",
    "about",
    "your",
    "what",
    "when",
    "where",
    "why",
    "how",
    "can",
    "could",
    "would",
    "should",
    "please",
    "show",
    "find",
    "tell",
    "give",
    "need",
    "want",
    "video",
    "videos",
    "youtube",
    "tutorial",
    "tutorials",
    "explained",
    "explain",
    "lesson",
    "lessons",
    "guide",
    "overview",
    "study",
    "learn",
    "learning",
    "course",
}

# ThinkTagFilter is in standalone_study_buddy_response_streaming, used internally by inference_call
# strip_think_tags removed - inference_call handles filtering by default


def inference_call_streaming_direct(system_prompt: str, user_prompt: str) -> Generator[str, None, None]:
    """
    Direct streaming inference call that yields chunks.
    
    Args:
        system_prompt: System instruction for the model
        user_prompt: User query/question
        
    Yields:
        Each chunk of text from the streaming response
    """
    # Use the existing inference_call which is already a generator
    for chunk in inference_call(system_prompt, user_prompt, stream_to_console=False):
        yield chunk


def extract_tool_parameters_streaming(query: str, tool_type: str) -> str:
    """
    Extract necessary parameters from the user query for the specific tool.
    Think tags are filtered by inference_call automatically.
    
    Args:
        query: User's query
        tool_type: Type of tool ('supplement', 'book_calendar', 'study_material')
        
    Returns:
        Extracted parameter as a string (think tags stripped)
    """
    if tool_type == 'supplement':
        # Extract search keywords for YouTube
        EXTRACT_PROMPT = f"""Extract the core search keywords from this user request for a YouTube search. 
Return ONLY the essential keywords or topic phrase, nothing else. Remove filler words like "find me", "show me", "video about", etc.

User request: "{query}"

Essential keywords:"""
        
    elif tool_type == 'book_calendar' or tool_type == 'calendar':
        # Extract event details
        EXTRACT_PROMPT = f"""Extract the key details for creating a calendar event from this request.
Return just the essential information needed.

User request: "{query}"

Event details:"""
        
    else:
        return query
    
    # Collect streaming response (think tags filtered by inference_call)
    output = "".join(inference_call_streaming_direct(None, EXTRACT_PROMPT))
    return output.strip().strip('"').strip("'").strip()


def _extract_relevance_keywords(text: str) -> set[str]:
    """Extract normalized keywords for lightweight relevance validation."""
    if not text:
        return set()
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    return {
        token for token in tokens
        if len(token) >= 3 and token not in _YOUTUBE_GENERIC_TERMS
    }


def _is_video_relevant(top_video: Dict[str, Any], query: str) -> tuple[bool, str]:
    """Apply minimum relevance-score and keyword-overlap gating."""
    raw_score = top_video.get("relevance_score")
    invalid_score_value = None
    try:
        score = float(raw_score if raw_score is not None else 0.0)
    except (ValueError, TypeError):
        score = 0.0
        invalid_score_value = raw_score

    query_keywords = _extract_relevance_keywords(query)
    if not query_keywords:
        if score >= YOUTUBE_SOFT_RELEVANCE_SCORE:
            if score >= YOUTUBE_MIN_RELEVANCE_SCORE:
                return True, "query has no strict keywords; score gate passed"
            return (
                True,
                (
                    f"soft relevance pass without strict keywords "
                    f"(score={score:.2f}, soft_threshold={YOUTUBE_SOFT_RELEVANCE_SCORE:.2f})"
                ),
            )
        if invalid_score_value is not None:
            return (
                False,
                (
                    f"invalid relevance_score={invalid_score_value!r}; "
                    f"treated as {score:.2f}, below soft_threshold={YOUTUBE_SOFT_RELEVANCE_SCORE:.2f}"
                ),
            )
        return (
            False,
            (
                f"relevance_score={score:.2f} below soft_threshold="
                f"{YOUTUBE_SOFT_RELEVANCE_SCORE:.2f}"
            ),
        )

    video_text = " ".join([
        str(top_video.get("title", "")),
        str(top_video.get("description", "")),
        str(top_video.get("channel", "")),
    ])
    video_keywords = _extract_relevance_keywords(video_text)
    overlap = query_keywords.intersection(video_keywords)

    required_overlap = max(1, YOUTUBE_MIN_KEYWORD_OVERLAP)

    # Strict pass: intended production-quality threshold.
    if score >= YOUTUBE_MIN_RELEVANCE_SCORE and len(overlap) >= required_overlap:
        return True, f"relevance passed (score={score:.2f}, overlap={sorted(overlap)})"

    # Soft pass: prefer somewhat related content over no video.
    if score >= YOUTUBE_SOFT_RELEVANCE_SCORE and len(overlap) >= 1:
        return (
            True,
            (
                f"soft relevance pass (score={score:.2f}, overlap={sorted(overlap)}, "
                f"soft_threshold={YOUTUBE_SOFT_RELEVANCE_SCORE:.2f})"
            ),
        )

    if len(overlap) < required_overlap:
        return (
            False,
            (
                f"keyword overlap too low ({len(overlap)}/{required_overlap}). "
                f"query={sorted(query_keywords)} overlap={sorted(overlap)} "
                f"(score={score:.2f}, min={YOUTUBE_MIN_RELEVANCE_SCORE:.2f}, soft={YOUTUBE_SOFT_RELEVANCE_SCORE:.2f})"
            ),
        )

    if invalid_score_value is not None:
        return (
            False,
            (
                f"invalid relevance_score={invalid_score_value!r}; treated as {score:.2f}. "
                f"Needs >= {YOUTUBE_MIN_RELEVANCE_SCORE:.2f} (or >= {YOUTUBE_SOFT_RELEVANCE_SCORE:.2f} with overlap)."
            ),
        )

    return (
        False,
        (
            f"relevance_score={score:.2f} below threshold={YOUTUBE_MIN_RELEVANCE_SCORE:.2f} "
            f"(soft={YOUTUBE_SOFT_RELEVANCE_SCORE:.2f}, overlap={sorted(overlap)})"
        ),
    )


def execute_tool_chitchat(
    query: str,
    user_state: Dict,
    buddy_pref: str,
    chat_history_str: str
) -> Dict[str, Any]:
    """Execute chitchat tool."""
    timestamp = datetime.now().strftime("%H:%M:%S")
    
    try:
        user_preference = user_state.get("study_buddy_preference", buddy_pref if buddy_pref else "friendly and supportive")
        study_buddy_name = user_state.get("study_buddy_name", "Study Buddy")
        
        # Get chapter context
        curriculum = user_state.get("curriculum", [{}])[0]
        active_chapter = curriculum.get("active_chapter")
        if active_chapter:
            chapter_name = active_chapter.get("name", "your studies") if isinstance(active_chapter, dict) else getattr(active_chapter, 'name', "your studies")
        else:
            chapter_name = "your studies"
        
        # Chitchat prompt
        prompt = f"""You are a friendly study assistant named {study_buddy_name}.

Your communication style: {user_preference}

The user is currently studying: {chapter_name}

The user wants to have a casual conversation unrelated to their study material.
Respond in a brief, friendly, and warm manner (1-2 sentences maximum).
Gently guide the conversation back to studying if appropriate.

{chat_history_str}

User message: {query}

Response:"""
        
        output = "".join(inference_call_streaming_direct(None, prompt))
        
        return {
            "name": "chitchat",
            "input": query,
            "output": output,
            "timestamp": timestamp,
            "success": True
        }
    except Exception as e:
        return {
            "name": "chitchat",
            "input": query,
            "output": f"Error: {str(e)}",
            "timestamp": timestamp,
            "success": False
        }


def execute_tool_supplement(
    query: str,
    parameters: str,
    user_state: Dict
) -> Dict[str, Any]:
    """Execute supplement (YouTube) tool."""
    timestamp = datetime.now().strftime("%H:%M:%S")
    
    try:
        print(Fore.YELLOW + f"🔍 Searching YouTube for: '{parameters}'" + Fore.RESET)
        top_video = fetch_most_relevant_youtube_video(parameters, search_limit=15)
        
        # Debug: Print what we got from YouTube search
        print(f"[YouTube Debug] fetch_most_relevant_youtube_video returned: {type(top_video)}", flush=True)
        if top_video:
            print(f"[YouTube Debug] top_video keys: {top_video.keys()}", flush=True)
            print(f"[YouTube Debug] video_id: '{top_video.get('video_id', 'MISSING')}'", flush=True)
        else:
            print(f"[YouTube Debug] top_video is None - no results found", flush=True)
        
        # Transform video data to match frontend YouTubeVideo type
        video_data = None
        if top_video:
            is_relevant, relevance_reason = _is_video_relevant(top_video, parameters)
            print(
                f"[YouTube Debug] Relevance gate result: {is_relevant} ({relevance_reason})",
                flush=True,
            )
            if not is_relevant:
                top_video = None
                output = (
                    "I couldn't find a sufficiently relevant video for your request. "
                    f"Try searching YouTube directly with: \"{parameters}\"."
                )

        if top_video:
            video_id = top_video.get('video_id', '')
            print(f"[YouTube Debug] Checking video_id: '{video_id}' (type: {type(video_id)})", flush=True)
            
            if video_id and video_id != 'N/A' and video_id != '':
                video_data = {
                    "id": video_id,
                    "title": top_video.get('title', 'Unknown'),
                    "channelName": top_video.get('channel', 'Unknown'),
                    "duration": top_video.get('duration', ''),
                    "thumbnailUrl": top_video.get('thumbnail', f"https://img.youtube.com/vi/{video_id}/mqdefault.jpg"),
                    "embedUrl": f"https://www.youtube.com/embed/{video_id}",
                    "watchUrl": top_video.get('url', f"https://www.youtube.com/watch?v={video_id}"),
                    "viewCount": top_video.get('views_text', ''),
                }
                output = f"I found a great video for you: **{top_video['title']}**"
                print(f"[YouTube Debug] ✓ Created video_data with id: {video_id}", flush=True)
            else:
                print(f"[YouTube Debug] ✗ video_id invalid or N/A", flush=True)
                output = f"I found a video but couldn't get the embed URL. You can watch it here: {top_video.get('url', 'N/A')}"
        else:
            print(f"[YouTube Debug] ✗ No video returned from search", flush=True)
            output = "I couldn't find a relevant video for your request. Try searching YouTube directly, or I can help explain concepts from your study materials."
        
        print(f"[YouTube Debug] Returning: success={bool(video_data)}, video_data={'SET' if video_data else 'None'}", flush=True)
        
        return {
            "name": "youtube_search",
            "input": parameters,
            "output": output,
            "timestamp": timestamp,
            "success": bool(video_data),
            "video_data": video_data
        }
    except Exception as e:
        return {
            "name": "youtube_search",
            "input": parameters,
            "output": f"Error searching YouTube: {str(e)}",
            "timestamp": timestamp,
            "success": False,
            "video_data": None
        }


def execute_tool_calendar(
    query: str,
    parameters: str,
    user_state: Dict,
    timezone: str = None
) -> Dict[str, Any]:
    """Execute calendar booking tool.
    
    Args:
        query: The user's query
        parameters: Parameters for the calendar event
        user_state: User state dictionary
        timezone: User's timezone (e.g., 'America/Los_Angeles'). If None, defaults to Europe/Paris.
    """
    print(f"[DEBUG] execute_tool_calendar CALLED - query: {query[:100]}, parameters: {parameters[:100]}", flush=True)
    timestamp = datetime.now().strftime("%H:%M:%S")
    
    try:
        print(f"[DEBUG] execute_tool_calendar - getting chapter context...", flush=True)
        # Get chapter context to enhance the calendar event
        curriculum = user_state.get("curriculum", [{}])[0]
        active_chapter = curriculum.get("active_chapter")
        chapter_context = ""
        
        if active_chapter:
            if isinstance(active_chapter, dict):
                chapter_name = active_chapter.get("name", "")
            else:
                chapter_name = getattr(active_chapter, 'name', "")
            
            if chapter_name:
                chapter_context = f" for {chapter_name}"
        
        # Enhance message with study context
        enhanced_message = parameters
        if "study" not in parameters.lower() and chapter_context:
            enhanced_message = f"{parameters} (Study session{chapter_context})"
        
        # Validate timezone if provided
        valid_timezone = None
        if timezone:
            try:
                import zoneinfo
                zoneinfo.ZoneInfo(timezone)  # Validate it's a real timezone
                valid_timezone = timezone
            except Exception:
                print(Fore.RED + f"⚠️ Invalid timezone '{timezone}', using default" + Fore.RESET)
        
        print(Fore.YELLOW + f"📅 Creating calendar event: '{enhanced_message}' (timezone: {valid_timezone or 'default'})" + Fore.RESET)
        print(f"[DEBUG] execute_tool_calendar - creating CalendarService instance...", flush=True)
        
        # Use CalendarService directly to get structured data
        # Pass user's timezone if provided and valid
        calendar_service = CalendarService(timezone=valid_timezone) if valid_timezone else CalendarService()
        print(f"[DEBUG] execute_tool_calendar - calling create_event_from_description...", flush=True)
        result = calendar_service.create_event_from_description(enhanced_message)
        print(f"[DEBUG] execute_tool_calendar - calendar service returned, success={result.success}", flush=True)
        
        # Transform to frontend CalendarEvent format
        event_data = None
        if result.success and result.event_data:
            ed = result.event_data
            # Generate a unique ID
            import hashlib
            event_id = hashlib.md5(f"{ed.summary}{ed.start_date}{ed.start_time}".encode()).hexdigest()[:12]
            
            # Read ICS content if file exists
            ics_content = None
            if result.file_path:
                try:
                    with open(result.file_path, 'r') as f:
                        ics_content = f.read()
                except Exception:
                    pass
            
            event_data = {
                "id": event_id,
                "title": ed.summary,
                "date": ed.start_date,
                "time": ed.start_time,
                "location": ed.location or None,
                "description": ed.description or None,
                "icsContent": ics_content,
            }
            
            output = f"I've created a calendar event: **{ed.summary}** on {ed.start_date} at {ed.start_time}."
        else:
            output = f"I tried to create a calendar event but encountered an issue: {result.status_message}"
        
        return {
            "name": "create_calendar_event",
            "input": enhanced_message,
            "output": output,
            "timestamp": timestamp,
            "success": result.success,
            "event_data": event_data,
            "calendar_file": result.file_path,
            "calendar_status": result.status_message,
            "calendar_preview": result.preview
        }
    except Exception as e:
        return {
            "name": "create_calendar_event",
            "input": parameters,
            "output": f"Error creating calendar event: {str(e)}",
            "timestamp": timestamp,
            "success": False,
            "event_data": None
        }


def execute_tool_minigame(query: str) -> Dict[str, Any]:
    """Execute minigame tool."""
    timestamp = datetime.now().strftime("%H:%M:%S")
    
    output = f'''<div style="margin: 10px 0;">
    <a href="{GAMES_URL}" target="_blank" 
       style="display: inline-block; padding: 10px 20px; 
              background-color: #0066cc; color: white; 
              text-decoration: none; border-radius: 5px;">
        🎮 Open Game
    </a>
</div>'''
    
    return {
        "name": "minigame",
        "input": query,
        "output": output,
        "timestamp": timestamp,
        "success": True
    }


def execute_tool_study_material(
    query: str,
    user_state: Dict,
    buddy_pref: str,
    memory_context: str = "",
    history_summary: str = ""
) -> Dict[str, Any]:
    """Execute study material tool - returns complete response (non-streaming)."""
    timestamp = datetime.now().strftime("%H:%M:%S")
    
    try:
        curriculum = user_state.get("curriculum", [{}])[0]
        active_chapter = curriculum.get("active_chapter")
        
        if not active_chapter:
            return {
                "name": "study_material",
                "input": query,
                "output": "Please select a chapter to start studying first!",
                "timestamp": timestamp,
                "success": False
            }
        
        # Get chapter details
        chapter_name = active_chapter.get("name", "Unknown Chapter") if isinstance(active_chapter, dict) else getattr(active_chapter, 'name', "Unknown Chapter")
        sub_topics = active_chapter.get("sub_topics", []) if isinstance(active_chapter, dict) else getattr(active_chapter, 'sub_topics', [])
        
        if not sub_topics:
            sub_topic = "General"
            study_material = "No study material available yet."
            list_of_quizzes = []
        else:
            first_subtopic = sub_topics[0]
            sub_topic = first_subtopic.get("sub_topic", "Unknown") if isinstance(first_subtopic, dict) else getattr(first_subtopic, 'sub_topic', "Unknown")
            
            # Get display_markdown or fallback to study_material
            if isinstance(first_subtopic, dict):
                study_material = first_subtopic.get("display_markdown") or first_subtopic.get("study_material", "No material available.")
            else:
                study_material = getattr(first_subtopic, 'display_markdown', None) or getattr(first_subtopic, 'study_material', "No material available.")
            
            list_of_quizzes = first_subtopic.get("quizzes", []) if isinstance(first_subtopic, dict) else getattr(first_subtopic, 'quizzes', [])
        
        # Get user preferences
        user_preference = user_state.get("study_buddy_preference", buddy_pref if buddy_pref else "friendly and supportive")
        study_buddy_name = user_state.get("study_buddy_name", "Study Buddy")
        
        # Enhance study material with memory context
        enhanced_study_material = study_material
        if memory_context:
            enhanced_study_material = f"""{study_material}

---
{memory_context}

{history_summary}"""
        
        # Call study buddy response (collects all tokens)
        output = study_buddy_response(
            chapter_name=chapter_name,
            sub_topic=sub_topic,
            study_material=enhanced_study_material,
            list_of_quizzes=list_of_quizzes,
            user_input=query,
            study_buddy_name=study_buddy_name,
            user_preference=user_preference,
            uploaded_img_loc=None,
            memory_context=memory_context,
            history_summary=history_summary
        )
        
        return {
            "name": "study_material",
            "input": query,
            "output": output,
            "timestamp": timestamp,
            "success": True,
            "chapter": chapter_name,
            "subtopic": sub_topic
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {
            "name": "study_material",
            "input": query,
            "output": f"Error: {str(e)}",
            "timestamp": timestamp,
            "success": False
        }


async def execute_tool_study_material_streaming(
    query: str,
    messages: List,
    user_state: Dict,
    buddy_pref: str,
    memory_context: str = "",
    history_summary: str = ""
) -> AsyncGenerator[Union[Tuple, Dict], None]:
    """
    Execute study material tool with TRUE streaming.
    
    Yields (messages, None, None, None) tuples during streaming.
    Yields tool_execution dict as the LAST item.
    
    Usage:
        async for result in execute_tool_study_material_streaming(...):
            if isinstance(result, dict):
                tool_execution = result
            else:
                yield result
    """
    timestamp = datetime.now().strftime("%H:%M:%S")
    
    # Add assistant message placeholder
    messages.append({"role": "assistant", "content": ""})
    streaming_idx = len(messages) - 1
    
    try:
        curriculum = user_state.get("curriculum", [{}])[0]
        active_chapter = curriculum.get("active_chapter")
        
        if not active_chapter:
            messages[streaming_idx]["content"] = "Please select a chapter to start studying first!"
            yield messages, None, None, None
            yield {
                "name": "study_material",
                "input": query,
                "timestamp": timestamp,
                "success": False
            }
            return
        
        # Get chapter details
        chapter_name = active_chapter.get("name", "Unknown Chapter") if isinstance(active_chapter, dict) else getattr(active_chapter, 'name', "Unknown Chapter")
        sub_topics = active_chapter.get("sub_topics", []) if isinstance(active_chapter, dict) else getattr(active_chapter, 'sub_topics', [])
        
        if not sub_topics:
            sub_topic = "General"
            study_material = "No study material available yet."
            list_of_quizzes = []
        else:
            first_subtopic = sub_topics[0]
            sub_topic = first_subtopic.get("sub_topic", "Unknown") if isinstance(first_subtopic, dict) else getattr(first_subtopic, 'sub_topic', "Unknown")
            if isinstance(first_subtopic, dict):
                study_material = first_subtopic.get("display_markdown") or first_subtopic.get("study_material", "No material available.")
            else:
                study_material = getattr(first_subtopic, 'display_markdown', None) or getattr(first_subtopic, 'study_material', "No material available.")
            list_of_quizzes = first_subtopic.get("quizzes", []) if isinstance(first_subtopic, dict) else getattr(first_subtopic, 'quizzes', [])
        
        # Get user preferences
        user_preference = user_state.get("study_buddy_preference", buddy_pref or "friendly")
        study_buddy_name = user_state.get("study_buddy_name", "Study Buddy")
        stringified = json.dumps(list_of_quizzes, ensure_ascii=False, indent=2)
        
        # Build prompt
        user_prompt_str = STUDY_BUDDY_SYS_PROMPT.format(
            study_buddy_name=study_buddy_name,
            user_preference=user_preference,
            chapter_name=chapter_name,
            sub_topic=sub_topic,
            study_material=study_material,
            list_of_quizzes=stringified,
            user_input=query,
            memory_context=memory_context or "",
            history_summary=history_summary or ""
        )
        
        # TRUE STREAMING - inference_call handles think tag filtering
        print(Fore.GREEN + "⚡ TRUE STREAMING (from execute_tool)..." + Fore.RESET)
        for chunk in inference_call(None, user_prompt_str, stream_to_console=True):
            if chunk:
                messages[streaming_idx]["content"] += chunk
                yield messages, None, None, None
                await asyncio.sleep(0)
        
        print(Fore.GREEN + "✓ TRUE STREAMING complete!" + Fore.RESET)
        
        # Yield tool_execution as last item
        yield {
            "name": "study_material",
            "input": query,
            "timestamp": timestamp,
            "success": True,
            "chapter": chapter_name,
            "subtopic": sub_topic
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        messages[streaming_idx]["content"] += f"\n\nError: {str(e)}"
        yield messages, None, None, None
        yield {
            "name": "study_material",
            "input": query,
            "timestamp": timestamp,
            "success": False
        }


async def send_message_streaming(
    prompt: str,
    messages: List,
    buddy_pref: str,
    username: str,
    show_details: bool = True
):
    """
    Streaming send_message with query routing and tool execution visualization.
    
    Args:
        prompt: User's input message
        messages: Chat history (list of message dicts)
        buddy_pref: User's study buddy preference
        username: Username
        show_details: Whether to show detailed routing and tool execution steps
    
    Yields:
        Updated messages list, calendar_file, calendar_status, calendar_preview
    """
    if not prompt.strip():
        yield messages, None, None, None
        return
    
    # Initialize calendar data
    calendar_file_path = None
    calendar_status_msg = None
    calendar_preview_text = None
    
    # Work with a copy to avoid mutation issues
    messages = list(messages) if messages else []
    
    # Debug logging
    print(Fore.CYAN + f"📥 send_message_streaming called with prompt: '{prompt[:50]}...'" + Fore.RESET)
    print(Fore.CYAN + f"   Existing messages: {len(messages)}" + Fore.RESET)
    
    # Add user message (separate from all assistant responses)
    messages.append({"role": "user", "content": prompt})
    print(Fore.GREEN + f"✓ User message added. Total messages: {len(messages)}" + Fore.RESET)
    
    yield messages, None, None, None
    await asyncio.sleep(0.05)  # Small delay for rendering
    
    try:
        # Get memory ops
        try:
            memory_ops = get_memory_ops(username, rate_limit_delay=2.0)
            print(Fore.CYAN + f"✓ Memory system active for user: {username}", Fore.RESET)
        except Exception as e:
            print(Fore.YELLOW + f"Memory system unavailable: {e}", Fore.RESET)
            memory_ops = None
        
        # Load user state
        user_state = load_user_state(username)
        if not user_state or "curriculum" not in user_state or len(user_state["curriculum"]) == 0:
            messages.append({
                "role": "assistant",
                "content": "I'm having trouble loading your study context. Please make sure you've generated a curriculum first."
            })
            yield messages, None, None, None
            return
        
        # Extract chapter info for routing
        chapter_name_for_routing = None
        sub_topic_for_routing = None
        curriculum = user_state["curriculum"][0]
        active_chapter = curriculum.get("active_chapter")
        
        if active_chapter:
            if isinstance(active_chapter, dict):
                chapter_name_for_routing = active_chapter.get("name", "Unknown Chapter")
                sub_topics = active_chapter.get("sub_topics", [])
            else:
                chapter_name_for_routing = getattr(active_chapter, 'name', "Unknown Chapter")
                sub_topics = getattr(active_chapter, 'sub_topics', [])
            
            if sub_topics and len(sub_topics) > 0:
                first_subtopic = sub_topics[0]
                if isinstance(first_subtopic, dict):
                    sub_topic_for_routing = first_subtopic.get("sub_topic", "Unknown Sub-topic")
                else:
                    sub_topic_for_routing = getattr(first_subtopic, 'sub_topic', "Unknown Sub-topic")
        
        # Prepare chat history for routing
        chat_history_str = ""
        for msg in messages[:-1]:  # Exclude the user message we just added
            if isinstance(msg, dict):
                role = msg.get("role", "user")
                content = msg.get("content", "")
                # Skip metadata messages
                if msg.get("metadata") is None:
                    chat_history_str += f"{role}: {content}\n"
        
        # Show routing indicator (assistant message)
        if show_details:
            messages.append({
                "role": "assistant",
                "content": "🧭 *Analyzing your query...*",
                "metadata": {"title": "⚙️ Step 1: Query Routing"}
            })
            yield messages, None, None, None
            await asyncio.sleep(0.1)
        
        # Step 1: Route the query
        print(Fore.CYAN + f"🔀 Routing query: '{prompt[:50]}...'" + Fore.RESET)
        raw_classification = query_routing(
            prompt,
            chat_history_str,
            chapter_name=chapter_name_for_routing,
            sub_topic=sub_topic_for_routing
        )
        route_classification = raw_classification.strip().lower()
        print(Fore.CYAN + f"✓ Query classified as: {route_classification}" + Fore.RESET)
        
        # Update routing indicator
        if show_details:
            messages[-1] = {
                "role": "assistant",
                "content": f"✓ **Routed to:** `{route_classification}`",
                "metadata": {"title": "🧭 Query Routing Complete"}
            }
            yield messages, None, None, None
            await asyncio.sleep(0.2)
        
        # Get memory context if available
        memory_context = ""
        history_summary = ""
        if memory_ops:
            try:
                memory_context = memory_ops.get_memory_context(prompt)
                history_summary = memory_ops.get_history_summary()
                if memory_context:
                    print(Fore.MAGENTA + f"✓ Added memory context to prompt", Fore.RESET)
            except Exception as e:
                print(Fore.YELLOW + f"Error getting memory context: {e}", Fore.RESET)
        
        # Step 2: Extract parameters if needed (not for chitchat or minigame)
        tool_execution = None
        
        if route_classification in ['supplement', 'book_calendar', 'calendar']:
            if show_details:
                messages.append({
                    "role": "assistant",
                    "content": f"🔍 *Extracting parameters for {route_classification}...*",
                    "metadata": {"title": "⚙️ Step 2: Parameter Extraction"}
                })
                yield messages, None, None, None
                await asyncio.sleep(0.1)
            
            parameters = extract_tool_parameters_streaming(prompt, route_classification)
            
            if show_details:
                messages[-1] = {
                    "role": "assistant",
                    "content": f"✓ **Extracted:** `{parameters}`",
                    "metadata": {"title": "🔍 Parameters Extracted"}
                }
                yield messages, None, None, None
                await asyncio.sleep(0.2)
        else:
            parameters = prompt
        
        # Step 3: Execute tool
        if show_details:
            messages.append({
                "role": "assistant",
                "content": f"⚡ *Executing {route_classification} tool...*",
                "metadata": {"title": "⚙️ Tool Execution Starting"}
            })
            yield messages, None, None, None
            await asyncio.sleep(0.1)
        
        # Execute the appropriate tool
        tool_execution = None
        
        if "unclear" in route_classification:
            clarification = (
                "I'm not quite sure what you're looking for — could you give me a bit more context? "
                "For example, are you asking about the study material, want a YouTube video, "
                "need to schedule a session, or something else?"
            )
            tool_execution = {"name": "unclear", "output": clarification, "success": True}
        elif "chitchat" in route_classification:
            tool_execution = execute_tool_chitchat(prompt, user_state, buddy_pref, chat_history_str)
        elif "supplement" in route_classification:
            tool_execution = execute_tool_supplement(prompt, parameters, user_state)
        elif "book_calendar" in route_classification or "calendar" in route_classification:
            tool_execution = execute_tool_calendar(prompt, parameters, user_state)
            # Store calendar data
            if tool_execution.get("success"):
                calendar_file_path = tool_execution.get("calendar_file")
                calendar_status_msg = tool_execution.get("calendar_status")
                calendar_preview_text = tool_execution.get("calendar_preview")
        elif "minigame" in route_classification:
            tool_execution = execute_tool_minigame(prompt)
        else:  # study_material - TRUE STREAMING
            async for result in execute_tool_study_material_streaming(
                prompt, messages, user_state, buddy_pref, memory_context, history_summary
            ):
                if isinstance(result, dict):
                    tool_execution = result
                else:
                    yield result
        
        # Display tool execution result (append, don't overwrite)
        if show_details and tool_execution:
            tool_input = tool_execution['input']
            tool_summary = f"**🛠️ Tool Executed:**\n\n"
            tool_summary += f"- **Function:** `{tool_execution['name']}`\n"
            tool_summary += f"- **Input:** `{tool_input[:100]}{'...' if len(tool_input) > 100 else ''}`\n"
            tool_summary += f"- **Time:** {tool_execution['timestamp']}\n"
            tool_summary += f"- **Status:** {'✓ Success' if tool_execution['success'] else '✗ Failed'}"
            
            messages.append({
                "role": "assistant",
                "content": tool_summary,
                "metadata": {"title": "⚡ Tool Execution Complete"}
            })
            yield messages, calendar_file_path, calendar_status_msg, calendar_preview_text
            await asyncio.sleep(0.3)
        
        # Step 4: Stream final response (skip for study_material - already streamed)
        is_study_material = tool_execution and tool_execution.get("name") == "study_material"
        
        if not is_study_material and show_details:
            messages.append({
                "role": "assistant",
                "content": "💬 *Generating response...*",
                "metadata": {"title": "⚙️ Step 4: Response Generation"}
            })
            yield messages, calendar_file_path, calendar_status_msg, calendar_preview_text
            await asyncio.sleep(0.1)
        
        # Remove generation indicator if present
        if not is_study_material and show_details:
            messages.pop()
        
        # Stream the final response (skip for study_material - already streamed)
        if not is_study_material and tool_execution and tool_execution.get("output"):
            messages.append({"role": "assistant", "content": ""})
            streaming_idx = len(messages) - 1
            
            # For most tools, output is already complete, so just display it
            # For study_material, we could stream it, but it's already generated
            output = tool_execution["output"]
            
            # Simulate streaming by yielding chunks
            chunk_size = 50
            for i in range(0, len(output), chunk_size):
                chunk = output[i:i+chunk_size]
                messages[streaming_idx] = {
                    "role": "assistant",
                    "content": output[:i+chunk_size]
                }
                yield messages, calendar_file_path, calendar_status_msg, calendar_preview_text
                await asyncio.sleep(0.01)
            
            # Ensure full output is shown
            messages[streaming_idx] = {"role": "assistant", "content": output}
            yield messages, calendar_file_path, calendar_status_msg, calendar_preview_text
        elif not is_study_material:
            # Only show error for non-streaming tools that failed
            messages.append({
                "role": "assistant",
                "content": "I encountered an issue processing your request."
            })
            yield messages, calendar_file_path, calendar_status_msg, calendar_preview_text
        
        # Add completion indicator
        if show_details:
            timestamp = datetime.now().strftime("%H:%M:%S")
            tool_name = tool_execution['name'] if tool_execution else 'none'
            messages.append({
                "role": "assistant",
                "content": f"✨ **Complete!** [{timestamp}] (Tool: {tool_name})",
                "metadata": {"title": "🎯 Done"}
            })
            yield messages, calendar_file_path, calendar_status_msg, calendar_preview_text
        
        # Process message through memory system
        if memory_ops and tool_execution:
            try:
                # Get chapter name for memory
                chapter_name_for_memory = None
                if active_chapter:
                    if isinstance(active_chapter, dict):
                        chapter_name_for_memory = active_chapter.get("name")
                    else:
                        chapter_name_for_memory = getattr(active_chapter, 'name', None)
                
                memory_result = await asyncio.to_thread(
                    lambda: asyncio.run(
                        memory_ops.process_message(
                            message=prompt,
                            bot_response=tool_execution.get("output", ""),
                            context={
                                "username": username,
                                "chapter": chapter_name_for_memory,
                            }
                        )
                    )
                )
                print(Fore.GREEN + f"✓ Memory processed: {memory_result['turns']} turns", Fore.RESET)
            except Exception as e:
                error_msg = str(e)
                if "429" not in error_msg and "Too Many Requests" not in error_msg:
                    print(Fore.RED + f"Error processing memory: {e}", Fore.RESET)
    
    except Exception as e:
        import traceback
        error_detail = traceback.format_exc()
        error_msg = f"❌ **Error during execution:**\n\n```\n{str(e)}\n```"
        
        print(Fore.RED + f"Error in send_message_streaming: {e}", Fore.RESET)
        traceback.print_exc()
        
        # Remove any pending indicators
        while messages and isinstance(messages[-1], dict) and messages[-1].get("metadata") is not None:
            messages.pop()
        
        messages.append({
            "role": "assistant",
            "content": error_msg,
            "metadata": {"title": "⚠️ Error"}
        })
        yield messages, None, None, None

