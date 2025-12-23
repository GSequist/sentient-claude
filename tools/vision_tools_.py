from utils.helpers import sanitize_and_encode_image_
from cache.state import RedisStateManager
from models.anthropic import model_call
from typing import Any
from utils.files import get_file_
import asyncio
import time


async def vision(img: str, query: str, *, claude_id: str, stream_id: str):
    """Analyzes images to extract visual information, identify objects, read text, or answer specific questions about the image content. This tool should be used whenever the user uploads an image (not a document) and needs information about what's in that image or has questions about its visual content. The 'img' parameter should reference an uploaded image file, while 'query' specifies what information to extract or what question to answer about the image. This tool handles photographs, diagrams, charts, screenshots, and images containing text, but should not be used for multi-page documents (use search_user_docs instead) or for generating new images (use image_gen instead).
    #parameters:
    img: the image to process - must be image in user workspace
    query: what to ask the vision model"""
    max_tokens = 60000
    redis_state = RedisStateManager()
    yield {
        "type": "tool_progress",
        "toolName": "vision",
        "progress": "Preparing image for vision model...",
        "percentage": 5,
        "stream_id": stream_id,
    }
    img_path = get_file_(claude_id, img)
    if not img_path:
        yield {
            "type": "tool_result",
            "toolName": "vision",
            "result": f"Not found image: {img}",
            "content": f"Not found image: {img}",
            "sources": [],
            "tokens": max_tokens,
            "stream_id": stream_id,
        }
        return
    try:
        encoded_string = sanitize_and_encode_image_(img_path)
        model_task = asyncio.create_task(
            model_call(
                input=query,
                encoded_image=encoded_string,
            )
        )
        percentage = 10
        start_time = time.time()
        timeout = 240
        delay_between_updates = 5

        while not model_task.done():

            if stream_id and not redis_state.get_streaming_state(claude_id, stream_id):
                model_task.cancel()
                yield {"type": "endOfMessage", "sources": [], "stream_id": stream_id}
                return

            yield {
                "type": "tool_progress",
                "toolName": "vision",
                "progress": f"Vision model working... ({percentage}%)",
                "percentage": percentage,
                "stream_id": stream_id,
            }

            await asyncio.sleep(delay_between_updates)

            if time.time() - start_time > timeout:
                model_task.cancel()
                yield {
                    "type": "tool_progress",
                    "toolName": "vision",
                    "progress": "Finishing up...",
                    "percentage": 100,
                    "stream_id": stream_id,
                }
                yield {
                    "type": "tool_result",
                    "toolName": "vision",
                    "result": f"Vision model timed out after 3 minutes",
                    "content": f"Vision model timed out after 3 minutes",
                    "sources": [],
                    "tokens": max_tokens,
                    "stream_id": stream_id,
                }
                return

            if percentage >= 90:
                percentage = 50
            else:
                percentage = min(percentage + 10, 90)
        response = await model_task
        vision_result = response.content[0].text

        yield {
            "type": "tool_result",
            "toolName": "vision",
            "result": f"Vision model analysis completed:\n {vision_result}",
            "content": vision_result,
            "sources": [],
            "tokens": max_tokens,
            "stream_id": stream_id,
        }

    except Exception as e:
        yield {
            "type": "tool_result",
            "toolName": "vision",
            "result": f"Error processing image: {str(e)}",
            "content": f"Error processing image: {str(e)}",
            "sources": [],
            "tokens": max_tokens,
            "stream_id": stream_id,
        }
