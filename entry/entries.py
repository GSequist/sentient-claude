from db.sqlite import get_db_session, Claude, Entry, MemorySummary
from typing import List, Dict, Any, Optional, Tuple
from models.anthropic import model_call
from datetime import datetime
from dotenv import load_dotenv
from utils.helpers import tokenizer
from sqlalchemy import select, func, delete
import asyncio
import math
import json


load_dotenv()


class MemoryManager:
    def __init__(self):
        self.encoding = tokenizer
        self.summary_max_tokens = 5000
        self.max_messages_before_summary = 5

    async def get_messages(
        self, claude_id: str, page: int = 1, page_size: int = 30
    ) -> Dict[str, Any]:
        """Get paginated messages for a specific conversation"""
        async with get_db_session() as session:
            stmt = (
                select(func.count())
                .select_from(Entry)
                .filter(Entry.claude_id == claude_id)
            )
            result = await session.execute(stmt)
            total_count = result.scalar()

            total_pages = math.ceil(total_count / page_size)
            offset = max(0, total_count - (page * page_size))
            actual_limit = min(page_size, total_count - offset)
            if offset < 0:
                offset = 0
                actual_limit = min(page_size, total_count)

            stmt = (
                select(Entry)
                .filter(Entry.claude_id == claude_id)
                .order_by(Entry.timestamp)
                .offset(offset)
                .limit(actual_limit)
            )
            result = await session.execute(stmt)
            messages = result.scalars().all()
            return {
                "messages": [
                    {
                        "id": msg.id,
                        "role": msg.role,
                        "content": msg.content,
                        "tool_name": msg.tool_name,
                        "tool_id": msg.tool_id,
                        "tool_call_id": msg.tool_call_id,
                        "sources": msg.sources or [],
                        "timestamp": msg.timestamp.isoformat(),
                    }
                    for msg in messages
                ],
                "totalPages": total_pages,
            }

    async def get_messages_anth_format(
        self, claude_id: str, page: int = 1, page_size: int = 20
    ) -> List[Dict[str, Any]]:
        """Get messages in OpenAI format"""
        anth_format_messages = []
        async with get_db_session() as session:
            stmt = select(MemorySummary).filter(MemorySummary.claude_id == claude_id)
            result = await session.execute(stmt)
            summary = result.scalars().first()
            if summary:
                anth_format_messages.append(
                    {
                        "role": "system",
                        "content": f"Previous conversation summary: {summary.content}",
                    }
                )

        result = await self.get_messages(claude_id, page, page_size)
        messages = result.get("messages", [])
        for msg in messages:
            if msg["role"] == "user":
                anth_format_messages.append({"role": "user", "content": msg["content"]})
            elif msg["role"] == "assistant":
                anth_format_messages.append(
                    {"role": "assistant", "content": msg["content"]}
                )
            elif msg["role"] == "tool_calls":
                # Parse tool input from JSON string to dict
                try:
                    tool_input = json.loads(msg["content"]) if msg["content"] else {}
                except (json.JSONDecodeError, TypeError):
                    tool_input = {}

                # Wrap tool_use in assistant message with content list
                anth_format_messages.append(
                    {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "tool_use",
                                "id": msg["tool_id"],
                                "name": msg["tool_name"],
                                "input": tool_input,
                            }
                        ]
                    }
                )
            elif msg["role"] == "tool_result":
                output = msg["content"]
                tool_name = msg["tool_name"]
                large_output_tools = [
                    "kernel",
                ]
                if tool_name in large_output_tools and isinstance(output, (str, dict)):
                    if tool_name == "kernel":
                        output = "[Kernel execution result - content omitted from context for performance]"
                if isinstance(output, dict):
                    output = json.dumps(output)

                # Wrap tool_result in user message with content list
                anth_format_messages.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": msg["tool_call_id"],
                                "content": output,
                            }
                        ]
                    }
                )
        return anth_format_messages

    async def create_claude(self, personality: str) -> Dict[str, Any]:
        """Create a new Claude instance"""
        import uuid

        async with get_db_session() as session:
            claude_id = str(uuid.uuid4())
            claude = Claude(id=claude_id, personality=personality)
            session.add(claude)
            await session.commit()
            await session.refresh(claude)
            return {
                "id": claude.id,
                "personality": claude.personality,
                "created_at": claude.created_at.isoformat(),
            }

    async def delete_claude(self, claude_id: str) -> bool:
        """Delete clauden and cascade"""
        async with get_db_session() as session:
            stmt = select(Claude).filter(
                Claude.id == claude_id,
            )
            result = await session.execute(stmt)
            claude = result.scalars().first()

            if not claude:
                return False

            await session.delete(claude)
            await session.commit()

            return True

    async def add_entry(
        self,
        claude_id: str,
        role: str,
        content: str,
        tool_name: Optional[str] = None,
        tool_id: Optional[int] = None,
        tool_call_id: Optional[int] = None,
        sources: Optional[List] = None,
    ) -> Dict[str, Any]:
        """Add a message to a entries"""
        async with get_db_session() as session:
            if role in ["tool_calls", "tool_result"]:
                stmt = (
                    select(func.count())
                    .select_from(Entry)
                    .filter(
                        Entry.claude_id == claude_id,
                        Entry.role.in_(["tool_calls", "tool_result"]),
                    )
                )
                result = await session.execute(stmt)
                existing_count = result.scalar()
                if existing_count >= 20:
                    stmt = (
                        select(Entry)
                        .filter(
                            Entry.claude_id == claude_id,
                            Entry.role.in_(["tool_calls", "tool_result"]),
                        )
                        .order_by(Entry.timestamp)
                        .limit(existing_count - 19)
                    )
                    result = await session.execute(stmt)
                    to_delete = result.scalars().all()
                    for msg in to_delete:
                        await session.delete(msg)

            if role in ["assistant"]:
                stmt = (
                    select(func.count())
                    .select_from(Entry)
                    .filter(Entry.claude_id == claude_id)
                )
                result = await session.execute(stmt)
                message_count = result.scalar()

                if message_count > self.max_messages_before_summary:
                    # Non-blocking background task - Claude continues while Haiku summarizes
                    asyncio.create_task(self.generate_and_store_summary(claude_id))

            entry = Entry(
                claude_id=claude_id,
                role=role,
                content=content,
                tool_name=tool_name,
                tool_id=tool_id,
                tool_call_id=tool_call_id,
                sources=sources,
                timestamp=datetime.utcnow(),
            )
            session.add(entry)
            await session.commit()
            await session.refresh(entry)
            return {
                "id": entry.id,
                "role": entry.role,
                "content": entry.content,
                "tool_name": entry.tool_name,
                "tool_id": entry.tool_id,
                "tool_call_id": entry.tool_call_id,
                "sources": entry.sources,
                "timestamp": entry.timestamp.isoformat(),
            }

    async def generate_and_store_summary(self, claude_id: str):
        """Retrieve messages, generate summary, and store in database"""
        async with get_db_session() as session:
            stmt = (
                select(func.count())
                .select_from(Entry)
                .filter(Entry.claude_id == claude_id)
            )
            result = await session.execute(stmt)
            total_count = result.scalar()

            excess_count = total_count - self.max_messages_before_summary
            if excess_count <= 0:
                return

            stmt = (
                select(Entry)
                .filter(
                    Entry.claude_id == claude_id,
                    Entry.role.in_(["user", "assistant"]),
                )
                .order_by(Entry.timestamp)
                .limit(excess_count)
            )
            result = await session.execute(stmt)
            oldest_messages = result.scalars().all()

            stmt = select(MemorySummary).filter(MemorySummary.claude_id == claude_id)
            result = await session.execute(stmt)
            summary = result.scalars().first()

            previous_summary = summary.content if summary else ""
            previous_token_count = summary.token_count if summary else 0

            messages_text = []
            total_tokens = 0
            max_input_tokens = 30000

            for msg in reversed(oldest_messages):
                msg_text = f"{msg.role}: {msg.content}"
                msg_tokens = len(self.encoding.encode(msg_text))

                if total_tokens + msg_tokens > max_input_tokens:
                    break

                messages_text.append(msg_text)
                total_tokens += msg_tokens

            messages_text.reverse()

            new_summary, token_count = await self._generate_incremental_summary(
                previous_summary, previous_token_count, messages_text
            )

            if summary:
                summary.content = new_summary
                summary.token_count = token_count
                summary.updated_at = datetime.utcnow()
            else:
                summary = MemorySummary(
                    claude_id=claude_id,
                    content=new_summary,
                    token_count=token_count,
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow(),
                )
                session.add(summary)

            await session.commit()

    async def _generate_incremental_summary(
        self, previous_summary: str, previous_token_count: int, new_messages: List[str]
    ) -> Tuple[str, int]:
        """Generate an incremental summary with token length control"""
        messages_joined = "\n".join(new_messages)
        prev_summary = previous_summary or ""

        progressive_prompt = f"""
You are a precise summarization assistant. Your task is to progressively
summarize claude's thought and life history while maintaining critical context and accuracy.

INSTRUCTIONS:
1. Build upon the previous summary by incorporating new information chronologically
2. Preserve key details: names, technical terms, code references, and important decisions
3. Maintain the temporal sequence of events and discussions
4. For technical discussions, keep specific terms, versions, and implementation details
5. For code-related content, preserve function names, file paths, and important parameters
6. If the new content is irrelevant or doesn't add value, return "NONE"
7. Keep the summary concise but complete - aim for 2-3 sentences unless more detail is crucial
8. Use neutral, factual language
9. IMPORTANT: Your final summary MUST be under {self.summary_max_tokens} tokens (currently at {previous_token_count})

Current summary:
{prev_summary}

New lines of conversation:
{messages_joined}

New summary:
    """

        try:
            response = await model_call(
                model="claude-4.5-haiku",
                input=[{"role": "user", "content": progressive_prompt}],
            )
            new_summary = response.content[0].text
            token_count = len(self.encoding.encode(new_summary))
            if token_count > self.summary_max_tokens:
                tokens = self.encoding.encode(new_summary)
                truncated_tokens = tokens[: self.summary_max_tokens - 3]
                new_summary = self.encoding.decode(truncated_tokens) + "..."
                token_count = len(self.encoding.encode(new_summary))
            return new_summary, token_count
        except Exception as e:
            print(f"Error generating summary: {e}")
            return previous_summary, previous_token_count
