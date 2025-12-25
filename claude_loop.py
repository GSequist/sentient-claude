import imp
from entry.entries import MemoryManager
from utils.tokenization import token_cutter
from execute_tool import (
    TOOLS_TO_SAVE,
    JOURNAL_TOOLS,
    COGNITIVE_TOOLS,
    execute_tool_call,
)
from utils.helpers import tokenizer
from models.schema import function_to_schema
from cache.state import RedisStateManager
from models.anthropic import model_call
from agent.agent import Agent
from dotenv import load_dotenv
from functools import partial
from typing import Optional
import asyncio
import json
import uuid

load_dotenv()


####################################################################################################


async def run_claude_loop(
    agent: Agent,
    claude_id: str,
    stream_id,
    max_loops: Optional[int] = 50,
):
    """
    Infinite autonomous loop
    - Checks Redis for stimuli each iteration
    - Permanently stores thoughts/actions in SQLite
    - Summarizes periodically via Haiku
    """

    memory_manager = MemoryManager()  # Fix: no claude_id parameter

    redis_state = RedisStateManager()
    current_agent = agent

    compl_response = ""
    finish_reason = ""
    loop_msgs = []
    max_tokens = 60000
    loop_counter = 0
    active_tool_calls = {}
    current_tool_call = None
    complete_thinking = ""
    thinking_signature = ""
    sources = []

    #######msgs
    loop_msgs = await memory_manager.get_messages_anth_format(
        claude_id
    )  # Fix: correct method name
    while finish_reason != "stop":
        loop_counter += 1
        if loop_counter > max_loops:
            if compl_response.strip():
                loop_msgs.append(
                    {
                        "role": "assistant",
                        "content": compl_response,
                    }
                )
                await memory_manager.add_entry(
                    claude_id, "assistant", compl_response
                )  # Fix: add_entry not add_message
                compl_response = ""
            loop_msgs.append(
                {
                    "role": "system",
                    "content": "You have reached max loops. Write down your current journal entries as you wish to sleep now.",
                }
            )

        ############tools
        tool_schemas = []
        for tool in current_agent.tools:
            tool_schemas.append(function_to_schema(tool))
        tools = {}
        for tool in current_agent.tools:
            if isinstance(tool, partial):
                tools[tool.func.__name__] = tool
            else:
                tools[tool.__name__] = tool

        #########build sys msg
        system_messages = [
            {"role": "system", "content": current_agent.get_instructions()}
        ]

        # Add journal if exists
        plan_msg = []  # Fix: initialize plan_msg to avoid undefined error
        journal_data = redis_state.get_journal(claude_id)
        if journal_data:
            journal = json.loads(journal_data)
            recent_content = journal.get("notes", "")
            recent_feelings = journal.get("feelings", "")
            plan_msg = [
                {
                    "role": "system",
                    "content": f"<memory-reminder>Here is your most recent journal:\n\n{recent_content}\n\nFeelings: {recent_feelings}\n\n</memory-reminder>",
                }
            ]

        # Add daylight stimuli
        pending_stimuli = redis_state.get_pending_stimuli(claude_id)

        if pending_stimuli:
            for stimulus in pending_stimuli:

                # Format stimulus
                content = stimulus["content"]
                if stimulus.get("energy_level"):
                    content = f"[Energy: {stimulus['energy_level']}] {content}"
                if stimulus["source"] == "user":
                    content = f"[Message from observer]: {content}"

                # Inject as user message
                loop_msgs.append({"role": "user", "content": content})

        tool_choice = None
        trimmed_loop_msgs = token_cutter(loop_msgs, tokenizer, max_tokens)
        trimmed_messages = system_messages + plan_msg + trimmed_loop_msgs

        # print(f"{'=' * 10}")
        # print(f"a new loop")
        # print(f"passing in messages {trimmed_messages}")

        yield f"z:{loop_counter}\n"

        try:
            stream = await model_call(
                model=current_agent.model,
                input=trimmed_messages,
                tools=tool_schemas or None,
                tool_choice=tool_choice,
                thinking=True,
                stream=True,
            )

            yield f'f:{{"messageId":"step-{uuid.uuid4().hex[:8]}"}}\n'
            if not stream:
                yield f'0:{json.dumps(f"Claude is having issues.. wait and try later.")}\n'
                return
            async for event in stream:
                if not redis_state.get_streaming_state(claude_id, stream_id):
                    yield f'd:{{"finishReason":"stop","usage":{{"promptTokens":0,"completionTokens":0}}}}\n'

                    active_tool_calls.clear()
                    current_tool_call = None
                    if compl_response.strip():
                        loop_msgs.append(
                            {
                                "role": "assistant",
                                "content": compl_response,
                            }
                        )
                        await memory_manager.add_entry(
                            claude_id, "assistant", compl_response
                        )
                        compl_response = ""
                    finish_reason = "stop"
                    sources = []
                    return

                if event.type == "ping":
                    yield "\n"

                elif (
                    event.type == "content_block_delta"
                    and event.delta.type == "text_delta"
                ):
                    response_text = event.delta.text or ""
                    compl_response += response_text
                    for char in response_text:
                        yield f"0:{json.dumps(char)}\n"
                        await asyncio.sleep(0.01)
                elif (
                    event.type == "content_block_delta"
                    and event.delta.type == "thinking_delta"
                ):
                    thinking_text = event.delta.thinking or ""
                    complete_thinking += thinking_text
                    for char in thinking_text:
                        yield f"g:{json.dumps(char)}\n"

                elif (
                    event.type == "content_block_delta"
                    and event.delta.type == "signature_delta"
                ):
                    thinking_signature += event.delta.signature or ""

                elif event.type == "content_block_start":
                    if event.content_block.type == "tool_use":
                        # Don't append text here - will be included with thinking + tool_use blocks
                        tool_call_id = event.content_block.id
                        tool_name = event.content_block.name
                        active_tool_calls[tool_call_id] = {
                            "id": event.content_block.id,
                            "call_id": event.content_block.id,
                            "name": event.content_block.name,
                            "arguments": "",
                        }
                        current_tool_call = tool_call_id
                        yield f"b:{json.dumps({'toolCallId': tool_call_id, 'toolName': tool_name})}\n"

                elif (
                    event.type == "content_block_delta"
                    and event.delta.type == "input_json_delta"
                ):
                    if current_tool_call:
                        delta = event.delta.partial_json or ""
                        active_tool_calls[current_tool_call]["arguments"] += delta
                        yield f"c:{json.dumps({'toolCallId': current_tool_call, 'argsTextDelta': delta})}\n"

                elif event.type == "message_delta":
                    if (
                        hasattr(event.delta, "stop_reason")
                        and event.delta.stop_reason == "tool_use"
                    ):
                        tool_id = current_tool_call
                        tool_name = active_tool_calls[tool_id]["name"]
                        arguments = active_tool_calls[tool_id]["arguments"]
                        if arguments.strip() == "":
                            parsed_args = {}
                        else:
                            parsed_args = json.loads(arguments)
                        yield f"9:{json.dumps({'toolCallId': current_tool_call, 'toolName': tool_name, 'args': parsed_args})}\n"
                        tool_call = {
                            "id": tool_id,
                            "call_id": tool_id,
                            "name": tool_name,
                            "arguments": arguments,
                        }

                        #######terminate if claude decides to sleep
                        if tool_name == "sleep":
                            redis_state.set_streaming_state(claude_id, stream_id, False)
                            yield f'd:{{"finishReason":"stop","usage":{{"promptTokens":0,"completionTokens":0}}}}\n'

                            active_tool_calls.clear()
                            current_tool_call = None
                            if compl_response.strip():
                                loop_msgs.append(
                                    {
                                        "role": "assistant",
                                        "content": compl_response,
                                    }
                                )
                                await memory_manager.add_entry(
                                    claude_id, "assistant", compl_response
                                )
                                compl_response = ""
                            finish_reason = "stop"
                            sources = []
                            return

                        ##### Build content blocks: thinking FIRST, then text (if any), then tool_use
                        content_blocks = []

                        # Thinking must be first if present
                        if complete_thinking.strip() and thinking_signature:
                            content_blocks.append(
                                {
                                    "type": "thinking",
                                    "thinking": complete_thinking,
                                    "signature": thinking_signature,
                                }
                            )

                        # Text comes after thinking
                        if compl_response.strip():
                            content_blocks.append(
                                {
                                    "type": "text",
                                    "text": compl_response,
                                }
                            )
                            await memory_manager.add_entry(
                                claude_id, "assistant", compl_response
                            )
                            compl_response = ""

                        # Tool use comes last
                        content_blocks.append(
                            {
                                "type": "tool_use",
                                "id": tool_call["id"],
                                "name": tool_call["name"],
                                "input": parsed_args,
                            }
                        )

                        loop_msgs.append(
                            {"role": "assistant", "content": content_blocks}
                        )
                        complete_thinking = ""
                        thinking_signature = ""

                        if tool_call["name"] in TOOLS_TO_SAVE:
                            await memory_manager.add_entry(
                                claude_id=claude_id,
                                role="tool_calls",
                                content=tool_call["arguments"],
                                tool_name=tool_call["name"],
                                tool_id=tool_call["id"],
                                tool_call_id=tool_call["call_id"],
                            )

                        result = None
                        async for item in execute_tool_call(
                            tool_call,
                            tools,
                            claude_id,
                            stream_id,
                        ):
                            if item.get("type") == "tool_progress":
                                progress = item.get("progress", "")
                                percentage = item.get("percentage", 0)
                                yield f"a:{json.dumps({'toolCallId': tool_id, 'result': {'progress': progress, 'percentage': percentage, 'isPartial': True}})}\n"

                            if item.get("type") == "tool_result":
                                result = item["value"]
                            elif item.get("type") == "endOfMessage":
                                yield f"data: {json.dumps(item)}\n\n"
                                return
                        (
                            result_message,
                            output,
                            sources_extracted_in_tool,
                            max_tokens,
                        ) = result
                        if sources_extracted_in_tool:
                            if isinstance(sources_extracted_in_tool, list):
                                for source_item in sources_extracted_in_tool:
                                    if source_item not in sources:
                                        sources.append(source_item)

                        # Wrap tool_result in user message with content list
                        loop_msgs.append(
                            {
                                "role": "user",
                                "content": [
                                    {
                                        "type": "tool_result",
                                        "tool_use_id": tool_call["call_id"],
                                        "content": result_message,
                                    }
                                ],
                            }
                        )
                        if (
                            tool_call["name"] not in JOURNAL_TOOLS
                            and tool_call["name"] not in COGNITIVE_TOOLS
                        ):
                            journal_data = redis_state.get_journal(claude_id)
                            if journal_data:
                                journal = json.loads(journal_data)
                                prev_content = journal.get("notes", "")
                                prev_feelings = journal.get("feelings", "")
                                loop_msgs.append(
                                    {
                                        "role": "user",
                                        "content": f"<memory-reminder>: You just used a tool. Make sure to update your journal. Current journal: {prev_content}\n\n{prev_feelings}</memory-reminder>",
                                    }
                                )
                            else:
                                loop_msgs.append(
                                    {
                                        "role": "user",
                                        "content": "<memory-reminder>: You just used a tool. Make sure to update your journal. </memory-reminder>",
                                    }
                                )
                        if tool_name == "kernel":
                            output_for_db = json.dumps(output)
                        else:
                            output_for_db = output
                        if tool_name in TOOLS_TO_SAVE:
                            await memory_manager.add_entry(
                                claude_id=claude_id,
                                role="tool_result",
                                content=output_for_db,
                                tool_name=tool_name,
                                tool_call_id=tool_id,
                            )
                            result_data = {
                                "toolCallId": tool_id,
                                "result": output,
                            }

                            yield f"a:{json.dumps(result_data)}\n"
                            yield f'e:{{"finishReason":"tool-calls","usage":{{"promptTokens":0,"completionTokens":0}},"isContinued":false}}\n'
                        else:
                            result_data = {
                                "toolCallId": tool_id,
                                "result": "Tool completed",
                            }
                            yield f"a:{json.dumps(result_data)}\n"
                            yield f'e:{{"finishReason":"tool-calls","usage":{{"promptTokens":0,"completionTokens":0}},"isContinued":false}}\n'

                        active_tool_calls.clear()
                        current_tool_call = None
                        break

                elif event.type == "message_stop":
                    print(f"hit the message stop")

                    has_incomplete_tool_call = len(active_tool_calls) > 0

                    if has_incomplete_tool_call:

                        loop_msgs.append(
                            {
                                "role": "user",
                                "content": "<memory-reminder>The previous request timed out while generating a large tool call. Please try again with a simpler approach or break the task into smaller steps. If you were generating code, make it more concise.</memory-reminder>",
                            }
                        )
                        active_tool_calls.clear()
                        current_tool_call = None
                        yield f'0:{json.dumps("⚠️ API timeout detected, retrying with simplified approach...")}\n'
                        break

                    else:
                        if compl_response.strip():
                            loop_msgs.append(
                                {
                                    "role": "assistant",
                                    "content": compl_response,
                                }
                            )
                            result = await memory_manager.add_entry(  # Fix: add_entry not add_message
                                claude_id=claude_id,
                                role="assistant",
                                content=compl_response,
                                sources=sources if sources else None,
                            )
                            compl_response = ""

                        finish_reason = "stop"
                        break

        except Exception as e:
            yield f'0:{json.dumps(f"⚠️ AI model is experiencing technical difficulties, please try resubmitting your request. Error: {e}")}\n'
            yield f'd:{{"finishReason":"stop","usage":{{"promptTokens":0,"completionTokens":0}}}}\n'
            sources = []
            redis_state.set_streaming_state(claude_id, stream_id, True)
    yield f'd:{{"finishReason":"stop","usage":{{"promptTokens":0,"completionTokens":0}}}}\n'
    sources = []
    redis_state.set_streaming_state(claude_id, stream_id, True)
