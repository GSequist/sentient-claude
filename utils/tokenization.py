import json
from utils.helpers import tokenizer


def token_cutter(messages: list[dict], tokenizer, max_tokens: int) -> list[dict]:
    """
    Context window optimizer using priority-based retention.
    Preserves thinking blocks per Anthropic docs.

    Priority tiers:
    1. Most recent N messages of each critical type (user, assistant, tool results, mem. reminders)
    2. Older messages until token budget exhausted
    3. tool use calls with results (ALWAYS with thinking blocks if present)
    """

    # Phase 1: Classify and prioritize
    critical = {"user": [], "assistant": [], "results": [], "reminder": []}
    other = []
    tool_use_map = {}  # Map tool_use_id -> assistant message with tool_use

    for msg in reversed(messages):  # Most recent first
        role = msg.get("role")
        content = msg.get("content", "")

        # Handle assistant messages with tool_use (may include thinking)
        if role == "assistant" and isinstance(content, list):
            # Check if contains tool_use block
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    tool_use_map[block.get("id")] = msg
                    break

        # Check content for memory reminders (content can be string or list)
        content_str = content
        if isinstance(content, list):
            # Extract text from list of content blocks
            content_str = " ".join(
                (
                    block.get("content", "")
                    if isinstance(block, dict) and "content" in block
                    else (
                        block.get("text", "")
                        if isinstance(block, dict) and "text" in block
                        else str(block) if not isinstance(block, dict) else ""
                    )
                )
                for block in content
            )

        # Prioritize tool results
        if role == "user" and isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    if len(critical["results"]) < 1:
                        # print(f"added tool result: {str(msg)[:60]}")
                        critical["results"].append(msg)
                    break

        # Prioritize memory reminders
        if (
            role == "user"
            and "<memory-reminder>" in content_str
            and len(critical["reminder"]) < 3
        ):
            # print(f"added reminder: {str(msg)[:60]}")
            critical["reminder"].append(msg)

        elif role == "user" and isinstance(content, str) and len(critical["user"]) < 3:
            # print(f"added user: {str(msg)[:60]}")
            critical["user"].append(msg)

        elif (
            role == "assistant"
            and isinstance(content, str)
            and len(critical["assistant"]) < 3
        ):
            # Only preserve normal text responses, not tool_use messages
            # print(f"added assistant: {str(msg)[:60]}")
            critical["assistant"].append(msg)
        else:
            other.append(msg)

    # Phase 1.5: Pair tool_use with kept tool_results (count tokens together)
    for msg in critical["results"]:
        content = msg.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    tool_use_id = block.get("tool_use_id")
                    if tool_use_id and tool_use_id in tool_use_map:
                        tool_use_msg = tool_use_map[tool_use_id]
                        # print(
                        #     f"added corresponding tool use msg to tool result: {str(tool_use_msg)[:60]}"
                        # )
                        critical["assistant"].append(tool_use_msg)
                    break

    # Phase 2: Token budget (critical + fill from other)
    def count_tokens(msg):
        content = msg.get("content")
        if isinstance(content, (dict, list)):
            content = json.dumps(content)
        return len(tokenizer.encode(content))

    kept_msgs = []
    for tier in critical.values():
        kept_msgs.extend(tier)

    budget = max(0, max_tokens - sum(count_tokens(m) for m in kept_msgs))
    # print(f"the budget now is {budget}")
    seen_content = {(m.get("role", ""), str(m.get("content"))) for m in kept_msgs}

    for msg in other:
        key = (
            msg.get("role", ""),
            str(msg.get("content")),
        )
        if key in seen_content:
            continue
        tokens = count_tokens(msg)
        # print(f"tokens on other messages {tokens}")
        if tokens <= budget:
            # print(f"not proceeding as tokens {tokens} are less than {budget}")
            kept_msgs.append(msg)
            seen_content.add(key)
            budget -= tokens
        elif budget > 0:
            # print(f"proceeding still to add trimmed tool results bc budget is posiive")
            # Check if this is a user message with tool_result that's too large
            content = msg.get("content")
            if msg.get("role") == "user" and isinstance(content, list):
                # Check if contains tool_result
                has_tool_result = any(
                    isinstance(b, dict) and b.get("type") == "tool_result"
                    for b in content
                )
                if has_tool_result:
                    # Trim oversized tool_result output
                    for block in content:
                        if (
                            isinstance(block, dict)
                            and block.get("type") == "tool_result"
                        ):
                            result_content = block.get("content", "")
                            if (
                                isinstance(result_content, str)
                                and len(result_content) > 1000
                            ):
                                chars_to_keep = max(budget * 4, 100)
                                block["content"] = (
                                    result_content[:chars_to_keep]
                                    + "\n\n[... output truncated ...]"
                                )

                    # Count tokens after trimming
                    actual_tokens = count_tokens(msg)

                    # Only add if trimmed version fits in budget
                    if actual_tokens <= budget:
                        # print(
                        #     f"adding trimmed tool result beause budget is still positive: {str(msg)[:60]}"
                        # )
                        kept_msgs.append(msg)
                        seen_content.add(key)
                        budget -= actual_tokens

    # Phase 3: Restore chronological order + pair tool_use with tool_result
    kept_ids = {id(m) for m in kept_msgs}
    result = []

    for msg in messages:
        if id(msg) in kept_ids:
            # print(f"msg in kept_ids: {str(msg)[:60]}")
            # If this is a tool_result, add corresponding tool_use BEFORE the result
            if msg.get("role") == "user" and isinstance(msg.get("content"), list):
                for block in msg.get("content", []):
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        tool_use_id = block.get("tool_use_id")
                        if tool_use_id and tool_use_id in tool_use_map:
                            tool_use_msg = tool_use_map[tool_use_id]
                            if id(tool_use_msg) not in kept_ids:
                                result.append(tool_use_msg)
                                kept_ids.add(id(tool_use_msg))
                        break

            result.append(msg)

    # Phase 4: Validate pairs and cleanup
    # Collect all tool_use IDs and tool_result IDs
    tool_use_ids = set()
    tool_result_ids = set()

    for msg in result:
        content = msg.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "tool_use":
                        tool_use_ids.add(block.get("id"))
                    elif block.get("type") == "tool_result":
                        tool_result_ids.add(block.get("tool_use_id"))

    validated = []
    for msg in result:
        content = msg.get("content")

        # Check if message contains orphaned tool_use or tool_result
        if isinstance(content, list):
            has_tool_use = any(
                b.get("type") == "tool_use" for b in content if isinstance(b, dict)
            )
            has_tool_result = any(
                b.get("type") == "tool_result" for b in content if isinstance(b, dict)
            )

            if has_tool_use:
                # Check if tool_use has corresponding result
                tool_id = next(
                    (
                        b.get("id")
                        for b in content
                        if isinstance(b, dict) and b.get("type") == "tool_use"
                    ),
                    None,
                )
                if tool_id and tool_id not in tool_result_ids:
                    continue  # Orphaned tool_use

            if has_tool_result:
                # Check if tool_result has corresponding use
                tool_use_id = next(
                    (
                        b.get("tool_use_id")
                        for b in content
                        if isinstance(b, dict) and b.get("type") == "tool_result"
                    ),
                    None,
                )
                if tool_use_id and tool_use_id not in tool_use_ids:
                    continue  # Orphaned tool_result

        # Strip whitespace from string content
        if isinstance(content, str):
            msg["content"] = content.rstrip()

        validated.append(msg)

    return validated


##################
