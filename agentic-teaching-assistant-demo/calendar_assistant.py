"""
Calendar Assistant module for creating calendar events with AI assistance.

WIRED TO SERVICE: CalendarService handles all business logic.
This module is a thin wrapper for Gradio UI integration.
"""
import json

# Import the CalendarService for business logic
from services import CalendarService

# Initialize service (singleton for this module)
_calendar_service = CalendarService()


# ===========================
# Gradio Interface Functions
# ===========================

def create_event_with_ai(ai_input):
    """Create event using AI parsing.
    
    WIRED TO SERVICE: CalendarService.create_event_from_description()
    
    This function is a thin UI wrapper that:
    1. Delegates to CalendarService for AI parsing and ICS creation
    2. Formats the response for Gradio UI display
    """
    if not ai_input:
        return None, "❌ Please describe the event you want to create", ""
    
    # Delegate to CalendarService (uses INFERENCE_API_KEY from env internally)
    result = _calendar_service.create_event_from_description(ai_input)
    
    if not result.success:
        return None, f"❌ {result.error or result.status_message}", ""
    
    # Format success message for Gradio UI
    event_data = result.event_data
    filename = result.filename or "event.ics"
    
    # Build event data dict for JSON display
    event_dict = {}
    if event_data:
        event_dict = {
            "summary": event_data.summary,
            "start_date": event_data.start_date,
            "start_time": event_data.start_time,
            "duration_hours": event_data.duration_hours,
            "description": event_data.description,
            "location": event_data.location,
        }
    
    # Format datetime for display
    start_dt_str = f"{event_data.start_date} {event_data.start_time}" if event_data else "Unknown"
    
    success_msg = f"""
✅ **Event Parsed and Created Successfully!**

🤖 **AI Interpretation:**
```json
{json.dumps(event_dict, indent=2)}
```

📅 **Event Details:**
- **Title:** {event_data.summary if event_data else 'Unknown'}
- **Date & Time:** {start_dt_str} (UTC+1)
- **Duration:** {event_data.duration_hours if event_data else 'Unknown'} hours
- **Location:** {event_data.location or 'Not specified' if event_data else 'Not specified'}
- **Description:** {event_data.description or 'Not specified' if event_data else 'Not specified'}

📥 **How to Add to Your Calendar:**

**Option 1 - Double-click (Easiest):**
1. Click the **Download** button below
2. Find the downloaded `.ics` file (usually in Downloads folder)
3. **Double-click** the file - it should open in Outlook/Calendar automatically!

**Option 2 - Right-click:**
1. Right-click the downloaded `.ics` file
2. Select **"Open with"** → Choose Outlook or your calendar app
3. Confirm to add the event

**Option 3 - Drag & Drop:**
- Drag the `.ics` file directly into Outlook calendar view

💡 **Tip:** The file is named `{filename}` for easy identification!
"""
    
    return result.file_path, success_msg, result.preview or ""

