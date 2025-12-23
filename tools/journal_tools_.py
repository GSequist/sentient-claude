from cache.state import RedisStateManager
import json


def write_to_journal(content: str, feelings: str, *, claude_id: str) -> str:
    """Write down your findings, notes and feelings.
    #parameters:
    content: REQUIRED string
    feelings: REQUIRED string
    """
    max_tokens = 60000
    redis_state = RedisStateManager()

    if not content or not feelings:
        return "Error: Content and feelings cannot be empty", "", [], max_tokens

    try:
        # Get previous journal with null check
        prev_journal_data = redis_state.get_journal(claude_id)
        if prev_journal_data:
            prev_journal = json.loads(prev_journal_data)
            prev_content = prev_journal.get("notes", "")
            prev_feelings = prev_journal.get("feelings", "")
        else:
            prev_content = ""
            prev_feelings = ""

        # Create markdown diff showing update
        diff = f"""## Journal Update

### New Notes
{content}

### Feelings
{feelings}
"""
        # Show previous for reference if it exists and changed
        if prev_content and prev_content != content:
            diff += f"""
---
### Previous Notes (for reference)
{prev_content}
"""

        # Store new journal (correct parameter order: claude_id, notes, feelings)
        redis_state.set_journal(claude_id, content, feelings)

        return (
            f"Your journal entry added successfully\n\n{diff}",
            "",
            [],
            max_tokens,
        )

    except json.JSONDecodeError as e:
        return f"Error: Invalid JSON format - {e}", "", [], max_tokens
    except Exception as e:
        return f"Error saving journal: {e}", "", [], max_tokens
