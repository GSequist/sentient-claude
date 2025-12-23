from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy.exc import OperationalError, DatabaseError
from sqlalchemy.pool import StaticPool
from contextlib import asynccontextmanager
from datetime import datetime
from dotenv import load_dotenv
from sqlalchemy import (
    Column,
    Integer,
    String,
    Text,
    DateTime,
    ForeignKey,
    JSON,
)
import asyncio
import random
import os

load_dotenv()


DB_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE_URL = f"sqlite+aiosqlite:///{DB_DIR}/claude_mem.db"

engine = create_async_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
    echo=False,
)

AsyncSessionLocal = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)

MAX_RETRIES = 3
BASE_RETRY_DELAY = 0.5


@asynccontextmanager
async def get_db_session(max_retries=MAX_RETRIES):
    retry_count = 0
    last_exception = None

    while retry_count <= max_retries:
        async with AsyncSessionLocal() as session:
            try:
                yield session
                await session.commit()
                return
            except (OperationalError, DatabaseError) as e:
                last_exception = e
                await session.rollback()
                # SQLite-specific error handling
                if (
                    "locked" in str(e).lower()
                    or "database is locked" in str(e).lower()
                    or "timeout" in str(e).lower()
                ):
                    retry_count += 1
                    if retry_count <= max_retries:
                        delay = BASE_RETRY_DELAY * (2**retry_count) + (
                            random.random() * 0.5
                        )
                        print(
                            f"Database error: {e}. Retrying in {delay:.2f}s (attempt {retry_count}/{max_retries})"
                        )
                        await asyncio.sleep(delay)
                    else:
                        print(
                            f"Max retries ({max_retries}) reached. Database operation failed: {e}"
                        )
                        raise
                else:
                    print(f"Non-retryable database error: {e}")
                    raise
            except Exception as e:
                await session.rollback()
                print(f"Unexpected error in database operation: {e}")
                raise

    raise last_exception


Base = declarative_base()


class Claude(Base):
    __tablename__ = "claude"

    id = Column(String, primary_key=True, index=True)
    personality = Column(Text, nullable=False)
    ceased_at = Column(DateTime, nullable=True, default=None)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    entries = relationship(
        "Entry", back_populates="claude", cascade="all, delete-orphan"
    )
    memory_summaries = relationship(
        "MemorySummary", back_populates="claude", cascade="all, delete-orphan"
    )


class Entry(Base):
    __tablename__ = "entries"

    id = Column(Integer, primary_key=True, index=True)
    claude_id = Column(
        String,
        ForeignKey("claude.id", ondelete="CASCADE"),
        nullable=False,
    )
    role = Column(String, nullable=False)
    content = Column(Text)
    tool_name = Column(String, nullable=True)
    tool_id = Column(String, nullable=True)
    tool_call_id = Column(String, nullable=True)
    sources = Column(JSON, nullable=True)
    timestamp = Column(DateTime, default=datetime.utcnow)

    claude = relationship("Claude", back_populates="entries")


class MemorySummary(Base):
    __tablename__ = "memory_summaries"

    id = Column(Integer, primary_key=True, index=True)
    claude_id = Column(
        String,
        ForeignKey("claude.id", ondelete="CASCADE"),
        nullable=False,
    )
    content = Column(Text, nullable=False)
    token_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    claude = relationship("Claude", back_populates="memory_summaries")


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
