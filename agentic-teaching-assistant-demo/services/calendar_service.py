"""
Calendar Service - Gradio-agnostic calendar event creation.

This service wraps the existing calendar_assistant functions and returns DTOs.
The underlying logic is already Gradio-agnostic - this just provides a clean interface.

Usage:
    from services.calendar_service import CalendarService
    
    svc = CalendarService()
    result = svc.create_event_from_description("Meeting tomorrow at 3pm")
"""
import os
import sys
import tempfile
import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, Any
from dataclasses import dataclass, field

# Add parent directory to path for imports
parent_dir = Path(__file__).parent.parent
if str(parent_dir) not in sys.path:
    sys.path.insert(0, str(parent_dir))

try:
    import zoneinfo
except ImportError:
    from backports import zoneinfo

from icalendar import Calendar, Event, vCalAddress, Alarm
# ChatNVIDIA now accessed via llm.create_llm()
from langchain_core.messages import SystemMessage, HumanMessage
from colorama import Fore


# =============================================================================
# DTOs
# =============================================================================

@dataclass
class CalendarEventData:
    """Parsed event data from AI."""
    summary: str
    start_date: str
    start_time: str
    duration_hours: float
    description: str = ""
    location: str = ""
    organizer_email: str = ""
    organizer_name: str = ""
    reminder_hours: float = 1.0


@dataclass
class CalendarEventResult:
    """Result of calendar event creation."""
    success: bool
    file_path: Optional[str] = None
    filename: Optional[str] = None
    status_message: str = ""
    preview: Optional[str] = None
    event_data: Optional[CalendarEventData] = None
    error: Optional[str] = None


# =============================================================================
# CalendarService
# =============================================================================

class CalendarService:
    """
    Gradio-agnostic calendar service.
    
    Creates calendar events from natural language descriptions using AI.
    Returns DTOs that can be converted to any UI framework format.
    """
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "meta/llama-3.1-405b-instruct",
        timezone: str = "Europe/Paris"
    ):
        """
        Initialize CalendarService.
        
        Args:
            api_key: NVIDIA Inference Hub API key. If None, reads from INFERENCE_API_KEY env var.
            model: LLM model to use for parsing.
            timezone: Default timezone for events.
        """
        self.api_key = api_key or os.environ.get('INFERENCE_API_KEY')
        self.model = model
        self.timezone = timezone
        self._tz = zoneinfo.ZoneInfo(timezone)
    
    def create_calendar_event(
        self,
        summary: str,
        start_datetime: datetime,
        duration_hours: float,
        description: str = "",
        location: str = "",
        organizer_email: str = "",
        organizer_name: str = "",
        reminder_hours: float = 1.0
    ) -> bytes:
        """
        Create a calendar event and return ICS content.
        
        Args:
            summary: Event title
            start_datetime: Start datetime (should have timezone)
            duration_hours: Duration in hours
            description: Event description
            location: Event location
            organizer_email: Organizer email
            organizer_name: Organizer name
            reminder_hours: Hours before event to remind (0 to disable)
            
        Returns:
            ICS file content as bytes
        """
        cal = Calendar()
        cal.add('prodid', '-//NVIDIA AI Calendar Creator//EN')
        cal.add('version', '2.0')
        cal.add('calscale', 'GREGORIAN')
        
        event = Event()
        event.add('summary', summary)
        
        # Ensure datetime has timezone
        if start_datetime.tzinfo is None:
            start_datetime = start_datetime.replace(tzinfo=self._tz)
        
        event.add('dtstart', start_datetime)
        
        # Calculate end time
        end_datetime = start_datetime + timedelta(hours=duration_hours)
        event.add('dtend', end_datetime)
        
        # Add dtstamp (current time in UTC for compatibility)
        event.add('dtstamp', datetime.now(zoneinfo.ZoneInfo("UTC")))
        
        # Generate UID
        uid_base = f"{summary}{start_datetime.isoformat()}"
        uid_hash = hashlib.md5(uid_base.encode()).hexdigest()
        event['uid'] = uid_hash
        
        if location:
            event.add('location', location)
        
        if description:
            event.add('description', description)
        
        if organizer_email:
            organizer = vCalAddress(f'mailto:{organizer_email}')
            if organizer_name:
                organizer.params['CN'] = organizer_name
            event['organizer'] = organizer
        
        # Add reminder alarm if requested
        if reminder_hours > 0:
            alarm = Alarm()
            alarm.add('action', 'DISPLAY')
            alarm.add('trigger', timedelta(hours=-reminder_hours))
            alarm.add('description', f'Reminder: {summary}')
            event.add_component(alarm)
        
        cal.add_component(event)
        
        return cal.to_ical()
    
    def parse_datetime(self, date_str: str, time_str: str) -> datetime:
        """
        Parse date and time strings into datetime object.
        
        Args:
            date_str: Date string (YYYY-MM-DD or ISO format)
            time_str: Time string (HH:MM)
            
        Returns:
            datetime object with timezone
        """
        try:
            # Handle different date formats
            if 'T' in date_str:  # ISO format
                dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
            else:
                dt = datetime.strptime(date_str, '%Y-%m-%d')
            
            # Add time if provided
            if time_str and ':' in time_str:
                hour, minute = map(int, time_str.split(':')[:2])
                dt = dt.replace(hour=hour, minute=minute)
            
            # Add timezone
            dt = dt.replace(tzinfo=self._tz)
            return dt
        except Exception as e:
            print(Fore.YELLOW + f"Warning: Could not parse datetime, using now: {e}" + Fore.RESET)
            return datetime.now(self._tz)
    
    def parse_event_with_ai(self, user_input: str) -> tuple[Optional[Dict[str, Any]], Optional[str]]:
        """
        Use AI to parse natural language into event parameters.
        
        Args:
            user_input: Natural language description of the event
            
        Returns:
            Tuple of (event_data_dict, error_message)
        """
        try:
            from llm import create_llm
            llm = create_llm("calendar_parsing")
            
            current_date = datetime.now(self._tz).strftime("%Y-%m-%d")
            system_prompt = f"""You are a calendar assistant. Parse user requests into structured event data.
Return ONLY a valid JSON object with these fields:
{{
    "summary": "Event title",
    "description": "Event description",
    "start_date": "YYYY-MM-DD",
    "start_time": "HH:MM",
    "duration_hours": float,
    "location": "Location (optional)",
    "organizer_email": "email@example.com (optional)",
    "organizer_name": "Name (optional)",
    "reminder_hours": 1
}}

Current date for reference: {current_date}
Note: All times are in {self.timezone} timezone

Example input: "Schedule a team meeting tomorrow at 2pm for 2 hours about Q4 planning"
Example output: {{"summary": "Team Meeting - Q4 Planning", "start_date": "2024-11-29", "start_time": "14:00", "duration_hours": 2.0, "description": "Quarterly planning discussion", "reminder_hours": 1}}

IMPORTANT: Return ONLY the JSON object, no explanations.
"""
            
            messages = [
                SystemMessage(content=system_prompt),
                HumanMessage(content=user_input)
            ]
            
            response = llm.invoke(messages)
            response_text = response.content.strip()
            
            # Extract JSON from response
            if '```json' in response_text:
                start = response_text.find('```json') + 7
                end = response_text.find('```', start)
                response_text = response_text[start:end].strip()
            elif '```' in response_text:
                start = response_text.find('```') + 3
                end = response_text.find('```', start)
                response_text = response_text[start:end].strip()
            
            event_data = json.loads(response_text)
            return event_data, None
            
        except json.JSONDecodeError as e:
            return None, f"Failed to parse AI response as JSON: {e}"
        except Exception as e:
            return None, f"Error parsing with AI: {str(e)}"
    
    def create_event_from_description(self, description: str) -> CalendarEventResult:
        """
        Create a calendar event from a natural language description.
        
        This is the main entry point for the service.
        
        Args:
            description: Natural language description of the event
            
        Returns:
            CalendarEventResult with file path and status
        """
        if not description or not description.strip():
            return CalendarEventResult(
                success=False,
                status_message="Please describe the event you want to create",
                error="Empty description"
            )
        
        if not self.api_key:
            return CalendarEventResult(
                success=False,
                status_message="NVIDIA Inference Hub API key not configured. Set INFERENCE_API_KEY environment variable.",
                error="Missing API key"
            )
        
        try:
            # Parse with AI
            event_dict, error = self.parse_event_with_ai(description)
            
            if error:
                return CalendarEventResult(
                    success=False,
                    status_message=f"Failed to parse event: {error}",
                    error=error
                )
            
            # Create event data DTO
            event_data = CalendarEventData(
                summary=event_dict['summary'],
                start_date=event_dict['start_date'],
                start_time=event_dict['start_time'],
                duration_hours=float(event_dict['duration_hours']),
                description=event_dict.get('description', ''),
                location=event_dict.get('location', ''),
                organizer_email=event_dict.get('organizer_email', ''),
                organizer_name=event_dict.get('organizer_name', ''),
                reminder_hours=float(event_dict.get('reminder_hours', 1))
            )
            
            # Parse datetime
            start_dt = self.parse_datetime(event_data.start_date, event_data.start_time)
            
            # Create ICS content
            ics_content = self.create_calendar_event(
                summary=event_data.summary,
                start_datetime=start_dt,
                duration_hours=event_data.duration_hours,
                description=event_data.description,
                location=event_data.location,
                organizer_email=event_data.organizer_email,
                organizer_name=event_data.organizer_name,
                reminder_hours=event_data.reminder_hours
            )
            
            # Save to temp file
            event_name_safe = "".join(
                c for c in event_data.summary 
                if c.isalnum() or c in (' ', '-', '_')
            ).strip()[:50]
            filename = f"event_{event_name_safe}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.ics"
            
            temp_file = tempfile.NamedTemporaryFile(
                mode='wb', 
                delete=False, 
                suffix='.ics'
            )
            temp_file.write(ics_content)
            temp_file.close()
            
            # Build status message
            status_message = self._build_success_message(event_data, start_dt, filename)
            
            return CalendarEventResult(
                success=True,
                file_path=temp_file.name,
                filename=filename,
                status_message=status_message,
                preview=ics_content.decode('utf-8'),
                event_data=event_data
            )
            
        except Exception as e:
            print(Fore.RED + f"Error creating calendar event: {e}" + Fore.RESET)
            import traceback
            traceback.print_exc()
            return CalendarEventResult(
                success=False,
                status_message=f"Error creating event: {str(e)}",
                error=str(e)
            )
    
    def _build_success_message(
        self, 
        event_data: CalendarEventData, 
        start_dt: datetime,
        filename: str
    ) -> str:
        """Build a formatted success message."""
        return f"""✅ **Event Created Successfully!**

📅 **Event Details:**
- **Title:** {event_data.summary}
- **Date & Time:** {start_dt.strftime('%Y-%m-%d %H:%M')} ({self.timezone})
- **Duration:** {event_data.duration_hours} hours
- **Location:** {event_data.location or 'Not specified'}
- **Description:** {event_data.description or 'Not specified'}

📥 **Download the .ics file and double-click to add to your calendar!**

💡 File: `{filename}`"""

