from typing import List, Dict, Any, Optional, Union
from anthropic import AsyncAnthropic
from dotenv import load_dotenv
import os
import asyncio
import json

load_dotenv()


async def model_call(
    input: Union[List[Dict[str, Any]], str],
    model="claude-4.5",
    encoded_image: Optional[Union[str, List[str]]] = None,
    tools: Optional[List[Dict[str, Any]]] = None,
    tool_choice: Optional[Dict[str, Any]] = None,
    stream: bool = False,
    thinking=False,
    max_tokens: int = 8000,
    client_timeout: int = 480,
):
    client = AsyncAnthropic(timeout=client_timeout)
    retries = 3
    sleep_time = 2

    if model == "opus-4.5":
        model = "claude-opus-4-5-20251101"
    elif model == "claude-4.5":
        model = "claude-sonnet-4-5-20250929"
    elif model == "claude-4.5-haiku":
        model = "claude-haiku-4-5-20251001"

    system_prompts = []
    messages = []

    if encoded_image:
        if not isinstance(input, str):
            raise ValueError("Image input requires string query")

        content = []
        if isinstance(encoded_image, str):
            content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": encoded_image,
                    },
                }
            )
        else:
            for img in encoded_image:
                content.append(
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": img,
                        },
                    }
                )

        content.append({"type": "text", "text": input})
        messages = [{"role": "user", "content": content}]
    elif isinstance(input, str):
        messages = [{"role": "user", "content": input}]
    else:
        for msg in input:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role in ("system"):
                if isinstance(content, str):
                    system_prompts.append(content)
                continue
            messages.append({"role": role, "content": content})

    api_parameters = {"model": model}

    if system_prompts:
        api_parameters["system"] = "\n".join(system_prompts)

    if tools:
        api_parameters["tools"] = tools

    if tool_choice:
        api_parameters["tool_choice"] = tool_choice

    if thinking:
        api_parameters["thinking"] = {"type": "enabled", "budget_tokens": 16000}
        api_parameters["max_tokens"] = 60000
        api_parameters["extra_headers"] = {
            "anthropic-beta": "interleaved-thinking-2025-05-14"
        }
    else:
        api_parameters["max_tokens"] = max_tokens

    api_parameters["messages"] = messages

    api_parameters["stream"] = stream

    for attempt in range(retries):
        try:
            response = await client.messages.create(**api_parameters)
            return response

        except Exception as e:
            print(f"\n[model_call]: {e}")
            if attempt < retries - 1:
                sleep_time = sleep_time * (2**attempt)
                print(f"\n[model_call]: Retrying in {sleep_time} seconds...")
                await asyncio.sleep(sleep_time)
            else:
                print(f"\n[model_call]: Failed after {retries} attempts")
                break

    return None


##########################################################
