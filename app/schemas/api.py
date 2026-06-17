from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class CreateSessionRequest(BaseModel):
    title: str | None = None


class SessionResponse(BaseModel):
    session_id: str
    title: str | None = None


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    session_id: str | None = None


class ChatResponse(BaseModel):
    session_id: str
    run_id: str
    message_id: str
    answer: str
    used_tools: list[str] = Field(default_factory=list)
    used_memories: list[str] = Field(default_factory=list)


class ToolCallRequest(BaseModel):
    id: str | None = None
    name: str
    arguments: dict[str, Any]


class ToolInvokeRequest(BaseModel):
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolDefinitionResponse(BaseModel):
    name: str
    description: str
    input_schema: dict[str, Any]
    examples: list[dict[str, Any]] = Field(default_factory=list)
    learning_notes: list[str] = Field(default_factory=list)


class ToolCallResult(BaseModel):
    id: str | None = None
    name: str
    arguments: dict[str, Any]
    content: str
    is_error: bool = False


class ModelTurn(BaseModel):
    kind: Literal["final", "tool_call"]
    content: str | None = None
    tool_call: ToolCallRequest | None = None
    tool_calls: list[ToolCallRequest] = Field(default_factory=list)
    finish_reason: str | None = None
    usage: dict[str, int] = Field(default_factory=dict)
    truncated: bool = False
    usage_summary: UsageSummary | None = None
    estimated_cost: CostEstimate | None = None
    error_info: ProviderErrorInfo | None = None
    cost_notice: str | None = None


class StreamEvent(BaseModel):
    event: str
    data: dict[str, Any]


class ModelHealthResponse(BaseModel):
    provider: str
    model: str | None = None
    status: Literal["ok", "not_checked", "degraded", "unavailable"]
    live: bool = False
    message: str
    protocol_mode: str | None = None
    tool_calling_enabled: bool | None = None
    tool_calling_status: Literal["ok", "not_checked", "unsupported", "unavailable"] | None = None
    tool_calling_message: str | None = None
    usage_summary: UsageSummary | None = None
    estimated_cost: CostEstimate | None = None
    error_info: ProviderErrorInfo | None = None
    cost_notice: str | None = None


class CreateMemoryRequest(BaseModel):
    content: str = Field(min_length=1)
    importance: int = Field(default=2, ge=1, le=5)
    memory_type: str = "preference"
    scope: str = "project"
    category: str = "preference"
    source_kind: str = "manual"
    confidence: int = Field(default=3, ge=1, le=5)
    session_id: str | None = None
    owner_id: str | None = None
    sensitivity: str = "public"
    expires_at: datetime | None = None

    @field_validator("content")
    @classmethod
    def content_must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("content must not be blank")
        return normalized


class UpdateMemoryRequest(BaseModel):
    content: str | None = Field(default=None, min_length=1)
    importance: int | None = Field(default=None, ge=1, le=5)
    memory_type: str | None = None
    scope: str | None = None
    category: str | None = None
    source_kind: str | None = None
    confidence: int | None = Field(default=None, ge=1, le=5)
    session_id: str | None = None
    owner_id: str | None = None
    sensitivity: str | None = None
    expires_at: datetime | None = None

    @field_validator("content")
    @classmethod
    def content_must_not_be_blank(cls, value: str | None) -> str | None:
        if value is None:
            return value
        normalized = value.strip()
        if not normalized:
            raise ValueError("content must not be blank")
        return normalized

    @model_validator(mode="after")
    def at_least_one_field(self) -> UpdateMemoryRequest:
        if (
            self.content is None
            and self.importance is None
            and self.memory_type is None
            and self.scope is None
            and self.category is None
            and self.source_kind is None
            and self.confidence is None
            and self.session_id is None
            and self.owner_id is None
            and self.sensitivity is None
            and self.expires_at is None
        ):
            raise ValueError("at least one field must be provided")
        return self


class MemoryVersionResponse(BaseModel):
    id: str
    memory_id: str
    content: str
    operation: str
    created_at: datetime


class MemoryResponse(BaseModel):
    id: str
    content: str
    memory_type: str
    scope: str
    category: str
    source_kind: str
    confidence: int
    session_id: str | None = None
    owner_id: str | None = None
    sensitivity: str
    supersedes_memory_id: str | None = None
    expires_at: datetime | None = None
    importance: int
    status: str
    source_message_id: str | None = None
    use_count: int = 0
    last_used_at: datetime | None = None
    conflict_key: str | None = None
    created_at: datetime
    updated_at: datetime
    versions: list[MemoryVersionResponse] = Field(default_factory=list)


class StepResponse(BaseModel):
    id: str
    step_index: int
    kind: str
    content: str


class ToolCallResponse(BaseModel):
    id: str
    name: str
    arguments_json: str
    result_json: str
    is_error: bool


class RunSummaryResponse(BaseModel):
    id: str
    session_id: str
    status: str
    final_answer: str | None
    created_at: datetime
    finished_at: datetime | None
    tool_count: int
    step_count: int
    duration_ms: int | None = None


class DashboardRunStatsResponse(BaseModel):
    sample_size: int
    failed_runs: int
    average_duration_ms: int | None = None
    latest_model_error: str | None = None
    latest_usage_summary: UsageSummary | None = None
    latest_estimated_cost: CostEstimate | None = None
    latest_error_info: ProviderErrorInfo | None = None
    latest_cost_notice: str | None = None


class RunTraceResponse(BaseModel):
    id: str
    session_id: str
    status: str
    final_answer: str | None
    steps: list[StepResponse]
    tool_calls: list[ToolCallResponse]
    usage_summary: UsageSummary | None = None
    estimated_cost: CostEstimate | None = None
    error_info: ProviderErrorInfo | None = None
    cost_notice: str | None = None


class UsageSummary(BaseModel):
    provider: str
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cache_creation_input_tokens: int | None = None
    cache_read_input_tokens: int | None = None
    raw: dict[str, int] = Field(default_factory=dict)


class CostEstimate(BaseModel):
    currency: str = "USD"
    input_cost: float | None = None
    output_cost: float | None = None
    cache_write_cost: float | None = None
    cache_read_cost: float | None = None
    total_cost: float | None = None
    price_version: str | None = None


class ProviderErrorInfo(BaseModel):
    code: str
    provider: str
    message: str
    retryable: bool = False
    exception_type: str | None = None
    suggestion: str | None = None
