from datetime import UTC, datetime
from enum import Enum
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def utc_now() -> datetime:
    return datetime.now(UTC)


class MessageRole(str, Enum):
    user = "user"
    assistant = "assistant"
    system = "system"
    tool = "tool"


class RunStatus(str, Enum):
    running = "running"
    completed = "completed"
    failed = "failed"
    max_loops = "max_loops"


class MemoryStatus(str, Enum):
    active = "active"
    superseded = "superseded"
    invalidated = "invalidated"
    archived = "archived"
    deleted = "deleted"


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("ses"))
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    messages: Mapped[list["Message"]] = relationship(back_populates="session")
    runs: Mapped[list["AgentRun"]] = relationship(back_populates="session")
    summary: Mapped["SessionSummary | None"] = relationship(back_populates="session")


class SessionSummary(Base):
    __tablename__ = "session_summaries"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("sum"))
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), unique=True, index=True)
    content: Mapped[str] = mapped_column(Text, default="")
    summary_json: Mapped[str] = mapped_column(Text, default="{}")
    covered_message_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    session: Mapped[Session] = relationship(back_populates="summary")


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("msg"))
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), index=True)
    role: Mapped[MessageRole] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    session: Mapped[Session] = relationship(back_populates="messages")


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("run"))
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), index=True)
    status: Mapped[RunStatus] = mapped_column(String(20), default=RunStatus.running)
    final_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    session: Mapped[Session] = relationship(back_populates="runs")
    steps: Mapped[list["AgentStep"]] = relationship(back_populates="run")
    tool_calls: Mapped[list["ToolCall"]] = relationship(back_populates="run")


class AgentStep(Base):
    __tablename__ = "agent_steps"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("step"))
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    step_index: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(50))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    run: Mapped[AgentRun] = relationship(back_populates="steps")


class ToolCall(Base):
    __tablename__ = "tool_calls"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("tool"))
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    arguments_json: Mapped[str] = mapped_column(Text)
    result_json: Mapped[str] = mapped_column(Text)
    is_error: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    run: Mapped[AgentRun] = relationship(back_populates="tool_calls")


class Memory(Base):
    __tablename__ = "memories"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("mem"))
    content: Mapped[str] = mapped_column(Text)
    memory_type: Mapped[str] = mapped_column(String(50), default="preference")
    scope: Mapped[str] = mapped_column(String(20), default="project")
    category: Mapped[str] = mapped_column(String(50), default="preference")
    source_kind: Mapped[str] = mapped_column(String(50), default="manual")
    confidence: Mapped[int] = mapped_column(Integer, default=3)
    session_id: Mapped[str | None] = mapped_column(ForeignKey("sessions.id"), nullable=True, index=True)
    owner_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    sensitivity: Mapped[str] = mapped_column(String(20), default="public")
    supersedes_memory_id: Mapped[str | None] = mapped_column(ForeignKey("memories.id"), nullable=True, index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    importance: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[MemoryStatus] = mapped_column(String(20), default=MemoryStatus.active)
    source_message_id: Mapped[str | None] = mapped_column(ForeignKey("messages.id"), nullable=True)
    use_count: Mapped[int] = mapped_column(Integer, default=0)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    conflict_key: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    versions: Mapped[list["MemoryVersion"]] = relationship(
        back_populates="memory",
        order_by="MemoryVersion.created_at",
    )


class MemoryVersion(Base):
    __tablename__ = "memory_versions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("memver"))
    memory_id: Mapped[str] = mapped_column(ForeignKey("memories.id"), index=True)
    content: Mapped[str] = mapped_column(Text)
    operation: Mapped[str] = mapped_column(String(30))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    memory: Mapped[Memory] = relationship(back_populates="versions")
