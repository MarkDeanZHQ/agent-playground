import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import and_, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import Settings, get_settings
from app.db.models import AgentRun, AgentStep, Memory, MemoryStatus, RunStatus, ToolCall
from app.db.session import get_session
from app.memory.service import (
    InvalidMemoryOperationError,
    InvalidMemoryPayloadError,
    MemoryNotFoundError,
    MemoryService,
)
from app.models.adapters import ClaudeModelAdapter, ModelAdapterError, OpenAIModelAdapter
from app.schemas.api import (
    ChatRequest,
    ChatResponse,
    CostEstimate,
    CreateMemoryRequest,
    CreateSessionRequest,
    DashboardModelErrorSummary,
    DashboardRunStatsResponse,
    DashboardRunSummary,
    MemoryResponse,
    MemoryVersionResponse,
    ModelHealthResponse,
    ProviderErrorInfo,
    RunSummaryResponse,
    RunTraceResponse,
    SessionResponse,
    StepResponse,
    ToolCallResponse,
    ToolCallResult,
    ToolDefinitionResponse,
    ToolInvokeRequest,
    UpdateMemoryRequest,
    UsageSummary,
)
from app.services.chat import ChatService
from app.services.model_observability import classify_model_error
from app.tools.builtin import build_default_registry

router = APIRouter(prefix="/api/v1")

RUN_LIST_MAX_LIMIT = 100
RUN_STATS_DEFAULT_SAMPLE_SIZE = 20


def _memory_status_value(status: MemoryStatus | str) -> str:
    return getattr(status, "value", status)


def _memory_response(memory: Memory) -> MemoryResponse:
    return MemoryResponse(
        id=memory.id,
        content=memory.content,
        memory_type=memory.memory_type,
        scope=memory.scope,
        category=memory.category,
        source_kind=memory.source_kind,
        confidence=memory.confidence,
        session_id=memory.session_id,
        owner_id=memory.owner_id,
        sensitivity=memory.sensitivity,
        supersedes_memory_id=memory.supersedes_memory_id,
        expires_at=memory.expires_at,
        importance=memory.importance,
        status=_memory_status_value(memory.status),
        source_message_id=memory.source_message_id,
        use_count=memory.use_count,
        last_used_at=memory.last_used_at,
        conflict_key=memory.conflict_key,
        created_at=memory.created_at,
        updated_at=memory.updated_at,
        versions=[
            MemoryVersionResponse(
                id=version.id,
                memory_id=version.memory_id,
                content=version.content,
                operation=version.operation,
                created_at=version.created_at,
            )
            for version in memory.versions
        ],
    )


async def _refresh_memory_versions(db: AsyncSession, memory: Memory) -> Memory:
    await db.refresh(memory, attribute_names=["versions"])
    return memory


async def _commit_memory_response(db: AsyncSession, memory: Memory) -> MemoryResponse:
    await db.commit()
    await _refresh_memory_versions(db, memory)
    return _memory_response(memory)


def _raise_memory_http_error(
    exc: MemoryNotFoundError | InvalidMemoryOperationError | InvalidMemoryPayloadError,
) -> None:
    if isinstance(exc, MemoryNotFoundError):
        raise HTTPException(status_code=404, detail="Memory not found") from exc
    if isinstance(exc, InvalidMemoryOperationError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/sessions", response_model=SessionResponse)
async def create_session(
    payload: CreateSessionRequest,
    db: AsyncSession = Depends(get_session),
) -> SessionResponse:
    session = await ChatService(db).create_session(payload.title)
    return SessionResponse(session_id=session.id, title=session.title)


@router.post("/chat", response_model=ChatResponse)
async def chat(payload: ChatRequest, db: AsyncSession = Depends(get_session)) -> ChatResponse:
    return await ChatService(db).chat(payload.message, payload.session_id)


@router.post("/chat/stream")
async def chat_stream(payload: ChatRequest, db: AsyncSession = Depends(get_session)) -> StreamingResponse:
    service = ChatService(db)
    return StreamingResponse(
        service.stream_chat(payload.message, payload.session_id),
        media_type="text/event-stream",
    )


@router.get("/tools", response_model=list[ToolDefinitionResponse])
async def list_tools() -> list[ToolDefinitionResponse]:
    return [ToolDefinitionResponse(**tool) for tool in build_default_registry().list_definitions()]


@router.post("/tools/{name}/invoke", response_model=ToolCallResult)
async def invoke_tool(name: str, payload: ToolInvokeRequest) -> ToolCallResult:
    return await build_default_registry().execute(name, payload.arguments)



def _model_name_for_provider(settings: Settings) -> str | None:
    provider = settings.model_provider
    if provider == "fake":
        return "fake"
    if provider == "claude":
        return settings.claude_model
    if provider == "openai":
        return settings.openai_model
    return None


def _static_model_health_response(provider: str, model_name: str | None, live: bool) -> ModelHealthResponse:
    settings = get_settings()
    if provider == "fake":
        return ModelHealthResponse(
            provider=provider,
            model="fake",
            status="ok",
            live=False,
            message="FakeModelAdapter 可用；未请求真实 LLM。",
            tool_calling_enabled=True,
            tool_calling_status="ok",
            tool_calling_message="FakeModelAdapter 使用内置规则模拟工具调用。",
        )
    protocol_mode = settings.effective_openai_protocol_mode if provider == "openai" else None
    legacy_env_suffix = (
        "（当前仍兼容旧环境变量 OPENAI_COMPATIBILITY_MODE）"
        if settings.openai_protocol_mode_uses_legacy_env
        else ""
    )
    return ModelHealthResponse(
        provider=provider,
        model=model_name,
        status="not_checked",
        live=live,
        message="配置已加载；当前是静态检查，尚未请求真实模型。追加 ?live=true 可执行一次真实连通性检查。",
        protocol_mode=protocol_mode,
        tool_calling_enabled=settings.openai_tool_calling if provider == "openai" else None,
        tool_calling_status="not_checked" if provider == "openai" else None,
        tool_calling_message=(
            f"OpenAI 协议模式仅影响参数与流式兼容策略，不再自动禁用 tools。{legacy_env_suffix}".strip()
            if provider == "openai"
            else None
        ),
    )


async def _live_model_health_response(provider: str, model_name: str | None) -> ModelHealthResponse:
    registry = build_default_registry()
    settings = get_settings()
    try:
        if provider == "claude":
            adapter = ClaudeModelAdapter(registry)
        elif provider == "openai":
            adapter = OpenAIModelAdapter(registry)
        else:
            raise ModelAdapterError(f"Unsupported model provider: {provider}")
        turn = await adapter.next_turn("请只回复 ok，用于健康检查。", "health_check", [])
    except ModelAdapterError as exc:
        return ModelHealthResponse(
            provider=provider,
            model=model_name,
            status="unavailable",
            live=True,
            message=str(exc),
            protocol_mode=settings.effective_openai_protocol_mode if provider == "openai" else None,
            tool_calling_enabled=settings.openai_tool_calling if provider == "openai" else None,
            tool_calling_status="unavailable" if provider == "openai" else None,
            tool_calling_message="真实模型请求失败，无法判断 tool calling 能力。" if provider == "openai" else None,
            error_info=ProviderErrorInfo(**exc.error_info) if getattr(exc, "error_info", None) else None,
        )

    tool_calling_enabled = settings.openai_tool_calling if provider == "openai" else None
    tool_calling_status: str | None = None
    tool_calling_message: str | None = None
    if provider == "openai":
        protocol_mode = settings.effective_openai_protocol_mode
        if not settings.openai_tool_calling:
            tool_calling_status = "not_checked"
            tool_calling_message = "已显式关闭 OPENAI_TOOL_CALLING，未执行 tool calling 健康检查。"
        else:
            try:
                tool_turn = await adapter.next_turn(
                    "请调用 text_stats 工具统计文本 hello world 的字符数、行数和单词数。",
                    "tool_health_check",
                    [],
                )
            except ModelAdapterError as exc:
                tool_calling_status = "unavailable"
                tool_calling_message = f"tool calling 健康检查失败：{exc}"
            else:
                if tool_turn.kind == "tool_call" and tool_turn.tool_calls:
                    tool_calling_status = "ok"
                    tool_calling_message = f"模型返回了工具调用：{tool_turn.tool_calls[0].name}"
                else:
                    tool_calling_status = "unsupported"
                    tool_calling_message = (
                        "模型可连通，但没有返回 tool_calls；"
                        "当前 endpoint 可能不支持 function calling。"
                    )
    else:
        protocol_mode = None

    return ModelHealthResponse(
        provider=provider,
        model=model_name,
        status="ok" if turn.kind == "final" else "degraded",
        live=True,
        message="真实模型连通性检查完成。" if turn.kind == "final" else "模型可连通，但健康检查触发了工具调用。",
        protocol_mode=protocol_mode,
        tool_calling_enabled=tool_calling_enabled,
        tool_calling_status=tool_calling_status,
        tool_calling_message=tool_calling_message,
        usage_summary=turn.usage_summary,
        estimated_cost=turn.estimated_cost,
        cost_notice=turn.cost_notice,
    )


def _parse_step_json(content: str) -> dict[str, object] | None:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _usage_cost_error_from_steps(
    steps: list[AgentStep],
) -> tuple[UsageSummary | None, CostEstimate | None, ProviderErrorInfo | None, str | None]:
    latest_usage: UsageSummary | None = None
    latest_cost: CostEstimate | None = None
    latest_error: ProviderErrorInfo | None = None
    latest_notice: str | None = None
    for step in sorted(steps, key=lambda item: item.step_index):
        payload = _parse_step_json(step.content)
        if not payload:
            continue
        if step.kind == "token_usage":
            usage_summary = payload.get("usage_summary")
            estimated_cost = payload.get("estimated_cost")
            if isinstance(usage_summary, dict):
                latest_usage = UsageSummary(**usage_summary)
            if isinstance(estimated_cost, dict):
                latest_cost = CostEstimate(**estimated_cost)
            notice = payload.get("cost_notice")
            if isinstance(notice, str) and notice:
                latest_notice = notice
        if step.kind == "model_error":
            error_info = payload.get("error_info")
            if isinstance(error_info, dict):
                latest_error = ProviderErrorInfo(**error_info)
    return latest_usage, latest_cost, latest_error, latest_notice

@router.get("/models/health", response_model=ModelHealthResponse)
async def model_health(live: bool = False) -> ModelHealthResponse:
    settings = get_settings()
    provider = settings.model_provider
    model_name = _model_name_for_provider(settings)

    if provider == "fake" or not live:
        return _static_model_health_response(provider, model_name, live)
    return await _live_model_health_response(provider, model_name)


@router.get("/runs", response_model=list[RunSummaryResponse])
async def list_runs(
    session_id: str | None = None,
    status: RunStatus | None = None,
    tool_name: str | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    limit: int = 20,
    offset: int = 0,
    db: AsyncSession = Depends(get_session),
) -> list[RunSummaryResponse]:
    safe_limit = min(max(limit, 1), RUN_LIST_MAX_LIMIT)
    safe_offset = max(offset, 0)

    step_counts = (
        select(AgentStep.run_id, func.count(AgentStep.id).label("step_count")).group_by(AgentStep.run_id).subquery()
    )
    tool_counts = (
        select(ToolCall.run_id, func.count(ToolCall.id).label("tool_count"))
        .group_by(ToolCall.run_id)
        .subquery()
    )
    query = (
        select(
            AgentRun,
            func.coalesce(step_counts.c.step_count, 0),
            func.coalesce(tool_counts.c.tool_count, 0),
        )
        .outerjoin(step_counts, step_counts.c.run_id == AgentRun.id)
        .outerjoin(tool_counts, tool_counts.c.run_id == AgentRun.id)
        .order_by(AgentRun.created_at.desc())
        .limit(safe_limit)
        .offset(safe_offset)
    )
    if session_id:
        query = query.where(AgentRun.session_id == session_id)
    if status is not None:
        query = query.where(AgentRun.status == status)
    if tool_name:
        query = query.where(
            exists(
                select(ToolCall.id).where(
                    and_(ToolCall.run_id == AgentRun.id, ToolCall.name == tool_name),
                )
            )
        )
    if created_from is not None:
        query = query.where(AgentRun.created_at >= created_from)
    if created_to is not None:
        query = query.where(AgentRun.created_at <= created_to)

    result = await db.execute(query)
    return [
        RunSummaryResponse(
            id=run.id,
            session_id=run.session_id,
            status=getattr(run.status, "value", run.status),
            final_answer=run.final_answer,
            created_at=run.created_at,
            finished_at=run.finished_at,
            step_count=step_count,
            tool_count=tool_count,
            duration_ms=int((run.finished_at - run.created_at).total_seconds() * 1000)
            if run.finished_at is not None
            else None,
        )
        for run, step_count, tool_count in result.all()
    ]


@router.get("/dashboard/run-stats", response_model=DashboardRunStatsResponse)
async def dashboard_run_stats(
    sample_size: int = RUN_STATS_DEFAULT_SAMPLE_SIZE,
    db: AsyncSession = Depends(get_session),
) -> DashboardRunStatsResponse:
    safe_sample_size = min(max(sample_size, 1), RUN_LIST_MAX_LIMIT)
    runs_result = await db.execute(
        select(AgentRun)
        .order_by(AgentRun.created_at.desc())
        .limit(safe_sample_size)
    )
    runs = list(runs_result.scalars())
    failed_runs = sum(1 for run in runs if getattr(run.status, "value", run.status) == RunStatus.failed.value)
    durations = [
        int((run.finished_at - run.created_at).total_seconds() * 1000)
        for run in runs
        if run.finished_at is not None
    ]
    latest_run = runs[0] if runs else None
    latest_run_summary = (
        DashboardRunSummary(
            id=latest_run.id,
            status=getattr(latest_run.status, "value", latest_run.status),
            created_at=latest_run.created_at,
            finished_at=latest_run.finished_at,
            duration_ms=int((latest_run.finished_at - latest_run.created_at).total_seconds() * 1000)
            if latest_run.finished_at is not None
            else None,
        )
        if latest_run is not None
        else None
    )
    latest_error_run_result = await db.execute(
        select(AgentRun)
        .join(AgentStep, AgentStep.run_id == AgentRun.id)
        .where(AgentStep.kind == "model_error")
        .order_by(AgentRun.created_at.desc(), AgentStep.created_at.desc())
        .limit(1)
        .options(selectinload(AgentRun.steps))
    )
    latest_error_run = latest_error_run_result.scalar_one_or_none()
    latest_model_error: str | None = None
    latest_model_error_detail: DashboardModelErrorSummary | None = None
    latest_usage_summary: UsageSummary | None = None
    latest_estimated_cost: CostEstimate | None = None
    latest_error_info: ProviderErrorInfo | None = None
    latest_cost_notice: str | None = None
    if latest_error_run is not None:
        latest_error_step = next(
            (
                step
                for step in sorted(latest_error_run.steps, key=lambda item: item.step_index, reverse=True)
                if step.kind == "model_error"
            ),
            None,
        )
        (
            latest_usage_summary,
            latest_estimated_cost,
            latest_error_info,
            latest_cost_notice,
        ) = _usage_cost_error_from_steps(list(latest_error_run.steps))
        if latest_error_info is not None:
            latest_model_error = latest_error_info.message
        elif latest_error_step is not None:
            payload = _parse_step_json(latest_error_step.content)
            if payload and payload.get("message"):
                latest_model_error = str(payload["message"])
        else:
            latest_model_error = None

        if latest_model_error:
            classification = classify_model_error(latest_model_error)
            latest_model_error_detail = DashboardModelErrorSummary(
                run_id=latest_error_run.id,
                message=latest_model_error,
                created_at=latest_error_step.created_at if latest_error_step is not None else None,
                run_status=getattr(latest_error_run.status, "value", latest_error_run.status),
                is_latest_run=bool(latest_run is not None and latest_error_run.id == latest_run.id),
                provider=latest_error_info.provider if latest_error_info is not None else None,
                phase=classification["phase"],
                error_type=classification["error_type"],
                error_code=classification["error_code"],
            )
    return DashboardRunStatsResponse(
        sample_size=len(runs),
        failed_runs=failed_runs,
        average_duration_ms=int(sum(durations) / len(durations)) if durations else None,
        latest_model_error=latest_model_error,
        latest_run=latest_run_summary,
        latest_model_error_detail=latest_model_error_detail,
        latest_usage_summary=latest_usage_summary,
        latest_estimated_cost=latest_estimated_cost,
        latest_error_info=latest_error_info,
        latest_cost_notice=latest_cost_notice,
    )


@router.get("/runs/{run_id}", response_model=RunTraceResponse)
async def get_run(run_id: str, db: AsyncSession = Depends(get_session)) -> RunTraceResponse:
    result = await db.execute(
        select(AgentRun)
        .where(AgentRun.id == run_id)
        .options(selectinload(AgentRun.steps), selectinload(AgentRun.tool_calls))
    )
    run = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    usage_summary, estimated_cost, error_info, cost_notice = _usage_cost_error_from_steps(list(run.steps))
    return RunTraceResponse(
        id=run.id,
        session_id=run.session_id,
        status=getattr(run.status, "value", run.status),
        final_answer=run.final_answer,
        steps=[
            StepResponse(id=step.id, step_index=step.step_index, kind=step.kind, content=step.content)
            for step in sorted(run.steps, key=lambda item: item.step_index)
        ],
        tool_calls=[
            ToolCallResponse(
                id=call.id,
                name=call.name,
                arguments_json=call.arguments_json,
                result_json=call.result_json,
                is_error=call.is_error,
            )
            for call in sorted(run.tool_calls, key=lambda item: item.created_at)
        ],
        usage_summary=usage_summary,
        estimated_cost=estimated_cost,
        error_info=error_info,
        cost_notice=cost_notice,
    )


@router.get("/memories", response_model=list[MemoryResponse])
async def list_memories(
    query: str | None = None,
    status: MemoryStatus | None = None,
    limit: int = 20,
    db: AsyncSession = Depends(get_session),
) -> list[MemoryResponse]:
    service = MemoryService(db)
    await service.invalidate_expired()
    memories = await service.list_memories(query=query, status=status, limit=limit)
    for memory in memories:
        await db.refresh(memory, attribute_names=["versions"])
    return [_memory_response(memory) for memory in memories]


@router.post("/memories", response_model=MemoryResponse)
async def create_memory(
    payload: CreateMemoryRequest,
    db: AsyncSession = Depends(get_session),
) -> MemoryResponse:
    service = MemoryService(db)
    try:
        memory = await service.create_memory(
            content=payload.content,
            importance=payload.importance,
            memory_type=payload.memory_type,
            scope=payload.scope,
            category=payload.category,
            source_kind=payload.source_kind,
            confidence=payload.confidence,
            session_id=payload.session_id,
            owner_id=payload.owner_id,
            sensitivity=payload.sensitivity,
            expires_at=payload.expires_at,
        )
        return await _commit_memory_response(db, memory)
    except (MemoryNotFoundError, InvalidMemoryOperationError, InvalidMemoryPayloadError) as exc:
        await db.rollback()
        _raise_memory_http_error(exc)


@router.patch("/memories/{memory_id}", response_model=MemoryResponse)
async def update_memory(
    memory_id: str,
    payload: UpdateMemoryRequest,
    db: AsyncSession = Depends(get_session),
) -> MemoryResponse:
    service = MemoryService(db)
    try:
        memory = await service.update_memory(
            memory_id=memory_id,
            content=payload.content,
            importance=payload.importance,
            memory_type=payload.memory_type,
            scope=payload.scope,
            category=payload.category,
            source_kind=payload.source_kind,
            confidence=payload.confidence,
            session_id=payload.session_id,
            owner_id=payload.owner_id,
            sensitivity=payload.sensitivity,
            expires_at=payload.expires_at,
        )
        return await _commit_memory_response(db, memory)
    except (MemoryNotFoundError, InvalidMemoryOperationError, InvalidMemoryPayloadError) as exc:
        await db.rollback()
        _raise_memory_http_error(exc)


@router.post("/memories/{memory_id}/archive", response_model=MemoryResponse)
async def archive_memory(memory_id: str, db: AsyncSession = Depends(get_session)) -> MemoryResponse:
    service = MemoryService(db)
    try:
        memory = await service.archive_memory(memory_id)
        return await _commit_memory_response(db, memory)
    except (MemoryNotFoundError, InvalidMemoryOperationError, InvalidMemoryPayloadError) as exc:
        await db.rollback()
        _raise_memory_http_error(exc)


@router.post("/memories/{memory_id}/delete", response_model=MemoryResponse)
async def soft_delete_memory(memory_id: str, db: AsyncSession = Depends(get_session)) -> MemoryResponse:
    service = MemoryService(db)
    try:
        memory = await service.soft_delete_memory(memory_id)
        return await _commit_memory_response(db, memory)
    except (MemoryNotFoundError, InvalidMemoryOperationError, InvalidMemoryPayloadError) as exc:
        await db.rollback()
        _raise_memory_http_error(exc)


@router.post("/memories/{memory_id}/restore", response_model=MemoryResponse)
async def restore_memory(memory_id: str, db: AsyncSession = Depends(get_session)) -> MemoryResponse:
    service = MemoryService(db)
    try:
        memory = await service.restore_memory(memory_id)
        return await _commit_memory_response(db, memory)
    except (MemoryNotFoundError, InvalidMemoryOperationError, InvalidMemoryPayloadError) as exc:
        await db.rollback()
        _raise_memory_http_error(exc)
