from models.schema import function_to_schema
from typing import Any, Dict
import inspect
import json
import re

COGNITIVE_TOOLS = [
    "think_deeply",
]

JOURNAL_TOOLS = [
    "write_to_journal",
]

TOOLS_TO_SAVE = ["kernel"]


async def execute_tool_call(
    tool_call: Dict,
    tools: Dict[str, callable],
    claude_id: str,
    stream_id: str,
) -> Any:
    """Execute a tool call by extracting required params"""
    default_token_limit = 30000
    name = tool_call["name"]
    try:
        if (
            name
            in [
                "vision",
            ]
            and stream_id
        ):
            if tool_call["arguments"].strip() == "":
                args = {}
            else:
                args = json.loads(tool_call["arguments"])
            tool = tools[name]
            final_result = None
            if inspect.isasyncgenfunction(tool):
                async for update in tool(**args, claude_id=claude_id):
                    if update.get("type") == "tool_result":
                        final_result = (
                            update["result"],
                            update["content"],
                            update.get("sources", ""),
                            update.get("tokens", default_token_limit),
                        )
                        yield {"type": "tool_result", "value": final_result}
                        continue
                    elif update.get("type") == "endOfMessage":
                        yield update
                        return
                    elif update.get("type") == "tool_progress":
                        yield update
                return
        tool_schema = function_to_schema(tools[name])
        tool = tools[name]
        schema_params = tool_schema["input_schema"]["properties"]
        if name == "visit_url":
            json_pattern = r"{[^}]+}"
            json_matches = re.finditer(json_pattern, tool_call["arguments"])
            combined_results = []
            combined_content = []
            combined_sources = []
            for match in json_matches:
                try:
                    args = json.loads(match.group())
                    if inspect.iscoroutinefunction(tool):
                        result = await tool(**args, claude_id=claude_id)
                    else:
                        result = tool(**args, claude_id=claude_id)
                    combined_results.append(result[0])
                    if result[1]:
                        combined_content.append(result[1])
                    if result[2]:
                        combined_sources.extend(result[2])
                except json.JSONDecodeError:
                    continue
            if combined_results:
                tool_max_tokens = result[3] if len(result) > 3 else default_token_limit
                yield {
                    "type": "tool_result",
                    "value": (
                        "\n\n".join(combined_results),
                        "\n".join(combined_content),
                        combined_sources,
                        tool_max_tokens,
                    ),
                }
        try:
            if tool_call["arguments"].strip() == "":
                args = {}
            else:
                args = json.loads(tool_call["arguments"])
        except json.JSONDecodeError:
            yield {
                "type": "tool_result",
                "value": (
                    f"Invalid JSON format. Here's the expected schema for {name}:\n"
                    f"{json.dumps(schema_params, indent=2)}",
                    "",
                    "",
                    default_token_limit,
                ),
            }
            return
        if inspect.iscoroutinefunction(tool):
            result = await tool(**args, claude_id=claude_id)
        else:
            result = tool(**args, claude_id=claude_id)
        yield {"type": "tool_result", "value": result}
        return
    except Exception as e:
        error_result = f"Tool execution failed: {str(e)}", "", "", default_token_limit
        yield {"type": "tool_result", "value": error_result}
