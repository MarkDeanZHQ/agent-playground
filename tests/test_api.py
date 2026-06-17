import json
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

os.environ["AGENT_PLAYGROUND_DATABASE_URL"] = "sqlite+aiosqlite:///./test_agent_playground.db"

from app.agent.runner import AgentRunner  # noqa: E402
from app.db.models import Message, MessageRole  # noqa: E402
from app.db.session import AsyncSessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models.adapters import ModelAdapterError  # noqa: E402
from app.services.model_observability import (  # noqa: E402
    classify_provider_error,
    estimate_cost,
    usage_summary_from_payload,
)
from app.tools.builtin import build_default_registry  # noqa: E402


@pytest.mark.asyncio
async def test_health_endpoint():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_chat_endpoint_returns_agent_response():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/chat", json={"message": "请统计 hello world"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["session_id"]
    assert payload["run_id"]
    assert payload["message_id"]
    assert "text_stats" in payload["used_tools"]


@pytest.mark.asyncio
async def test_tools_endpoint_lists_default_tools():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/tools")

    assert response.status_code == 200
    tools = response.json()
    names = {tool["name"] for tool in tools}
    assert {"text_stats", "note_search", "json_extract", "todo_create", "todo_list"}.issubset(names)
    assert all("input_schema" in tool for tool in tools)
    assert all("examples" in tool for tool in tools)
    assert all("learning_notes" in tool for tool in tools)


@pytest.mark.asyncio
async def test_model_health_endpoint_reports_fake_provider_without_live_call():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/models/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["provider"] == "fake"
    assert payload["model"] == "fake"
    assert payload["status"] == "ok"
    assert payload["live"] is False
    assert payload["tool_calling_status"] == "ok"
    assert payload["usage_summary"] is None
    assert payload["estimated_cost"] is None


@pytest.mark.asyncio
async def test_model_health_endpoint_reports_openai_protocol_and_tool_calling_fields(monkeypatch):
    monkeypatch.setenv("AGENT_PLAYGROUND_MODEL_PROVIDER", "openai")
    monkeypatch.setenv("AGENT_PLAYGROUND_OPENAI_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("AGENT_PLAYGROUND_OPENAI_PROTOCOL_MODE", "auto")
    monkeypatch.setenv("AGENT_PLAYGROUND_OPENAI_TOOL_CALLING", "true")
    from app.core.config import get_settings

    get_settings.cache_clear()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/v1/models/health")
    finally:
        get_settings.cache_clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["provider"] == "openai"
    assert payload["protocol_mode"] == "auto"
    assert payload["tool_calling_enabled"] is True
    assert payload["tool_calling_status"] == "not_checked"


@pytest.mark.asyncio
async def test_tool_invoke_endpoint_returns_success_and_observable_error():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        success = await client.post("/api/v1/tools/text_stats/invoke", json={"arguments": {"text": "hello world"}})
        missing = await client.post("/api/v1/tools/missing/invoke", json={"arguments": {}})
        failure = await client.post("/api/v1/tools/note_search/invoke", json={"arguments": {"query": ""}})
        structured = await client.post(
            "/api/v1/tools/json_extract/invoke",
            json={
                "arguments": {
                    "text": "name: Alice\nemail: alice@example.com",
                    "fields": ["name", "email"],
                }
            },
        )
        todo_create = await client.post(
            "/api/v1/tools/todo_create/invoke",
            json={"arguments": {"title": "写完 tools lab 文档"}},
        )
        todo_list = await client.post("/api/v1/tools/todo_list/invoke", json={"arguments": {}})

    assert success.status_code == 200
    assert success.json()["content"] == "characters=11, lines=1, words=2"
    assert success.json()["is_error"] is False
    assert missing.json()["is_error"] is True
    assert "Unknown tool" in missing.json()["content"]
    assert failure.json()["is_error"] is True
    assert "query is required" in failure.json()["content"]
    assert structured.json()["is_error"] is False
    assert '"name": "Alice"' in structured.json()["content"]
    assert todo_create.json()["is_error"] is False
    assert "写完 tools lab 文档" in todo_create.json()["content"]
    assert todo_list.json()["is_error"] is False
    assert "写完 tools lab 文档" in todo_list.json()["content"]


@pytest.mark.asyncio
async def test_memories_endpoint_supports_query_status_and_source_fields():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        chat_response = await client.post("/api/v1/chat", json={"message": "请记住：我偏好 FastAPI 示例"})
        memory_response = await client.get("/api/v1/memories", params={"query": "FastAPI", "status": "active"})

    assert chat_response.status_code == 200
    assert memory_response.status_code == 200
    memories = memory_response.json()
    assert any("FastAPI" in memory["content"] for memory in memories)
    assert {"source_message_id", "created_at", "updated_at"}.issubset(memories[0])
    assert {"scope", "category", "source_kind", "confidence", "sensitivity"}.issubset(memories[0])
    assert "expires_at" in memories[0]


@pytest.mark.asyncio
async def test_memory_management_endpoints_complete_lifecycle():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        create_response = await client.post(
            "/api/v1/memories",
            json={
                "content": " 手动 API 记忆 ",
                "importance": 3,
                "memory_type": "preference",
                "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
            },
        )
        memory = create_response.json()

        update_response = await client.patch(
            f"/api/v1/memories/{memory['id']}",
            json={"content": "更新后的 API 记忆", "importance": 4},
        )
        archive_response = await client.post(f"/api/v1/memories/{memory['id']}/archive")
        delete_response = await client.post(f"/api/v1/memories/{memory['id']}/delete")
        restore_response = await client.post(f"/api/v1/memories/{memory['id']}/restore")
        list_response = await client.get("/api/v1/memories", params={"query": "API", "status": "active"})

    assert create_response.status_code == 200
    assert create_response.json()["content"] == "手动 API 记忆"
    assert create_response.json()["scope"] == "project"
    assert create_response.json()["category"] == "preference"
    assert create_response.json()["expires_at"] is not None
    assert update_response.status_code == 200
    assert update_response.json()["content"] == "更新后的 API 记忆"
    assert archive_response.status_code == 200
    assert archive_response.json()["status"] == "archived"
    assert delete_response.status_code == 200
    assert delete_response.json()["status"] == "deleted"
    assert restore_response.status_code == 200
    restored = restore_response.json()
    assert restored["status"] == "active"
    assert [version["operation"] for version in restored["versions"]] == [
        "created",
        "updated",
        "archived",
        "deleted",
        "restored",
    ]
    assert any(memory["id"] == restored["id"] for memory in list_response.json())


@pytest.mark.asyncio
async def test_memory_management_endpoints_validate_payload_and_missing_id():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        blank_response = await client.post("/api/v1/memories", json={"content": "   "})
        bad_importance_response = await client.post(
            "/api/v1/memories",
            json={"content": "bad importance", "importance": 6},
        )
        empty_patch_response = await client.patch("/api/v1/memories/missing", json={})
        missing_response = await client.post("/api/v1/memories/missing/archive")

    assert blank_response.status_code == 422
    assert bad_importance_response.status_code == 422
    assert empty_patch_response.status_code == 422
    assert missing_response.status_code == 404


@pytest.mark.asyncio
async def test_memory_management_endpoints_reject_invalid_state_transitions():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        create_response = await client.post("/api/v1/memories", json={"content": "不可编辑删除态"})
        memory_id = create_response.json()["id"]
        await client.post(f"/api/v1/memories/{memory_id}/delete")
        update_deleted_response = await client.patch(f"/api/v1/memories/{memory_id}", json={"content": "should fail"})

        first_response = await client.post("/api/v1/chat", json={"message": "请记住：我偏好 superseded API 示例"})
        await client.post(
            "/api/v1/chat",
            json={
                "message": "请记住：我偏好以后用 superseded API 示例 v2",
                "session_id": first_response.json()["session_id"],
            },
        )
        superseded_list = await client.get("/api/v1/memories", params={"status": "superseded", "query": "superseded"})
        superseded_id = superseded_list.json()[0]["id"]
        restore_superseded_response = await client.post(f"/api/v1/memories/{superseded_id}/restore")

    assert update_deleted_response.status_code == 409
    assert restore_superseded_response.status_code == 409


@pytest.mark.asyncio
async def test_memory_superseded_trace_is_recorded():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first_response = await client.post("/api/v1/chat", json={"message": "请记住：我偏好 FastAPI 示例"})
        chat_response = await client.post(
            "/api/v1/chat",
            json={
                "message": "请记住：我偏好以后用 FastAPI 示例 v2",
                "session_id": first_response.json()["session_id"],
            },
        )
        run_id = chat_response.json()["run_id"]
        trace_response = await client.get(f"/api/v1/runs/{run_id}")

    assert trace_response.status_code == 200
    kinds = {step["kind"] for step in trace_response.json()["steps"]}
    assert "memory_superseded" in kinds
    assert "memory_saved" in kinds


@pytest.mark.asyncio
async def test_memories_endpoint_invalidates_expired_memory():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        create_response = await client.post(
            "/api/v1/memories",
            json={
                "content": "我偏好 expired-api FastAPI 示例",
                "expires_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
            },
        )
        active_response = await client.get("/api/v1/memories", params={"query": "expired-api", "status": "active"})
        invalidated_response = await client.get(
            "/api/v1/memories",
            params={"query": "expired-api", "status": "invalidated"},
        )

    assert create_response.status_code == 200
    assert active_response.json() == []
    invalidated = invalidated_response.json()
    assert invalidated[0]["id"] == create_response.json()["id"]
    assert invalidated[0]["status"] == "invalidated"


@pytest.mark.asyncio
async def test_chat_memory_trace_records_pending_confirmation_for_same_conflict_key():
    token = uuid4().hex[:8]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first_response = await client.post(
            "/api/v1/chat",
            json={"message": f"请记住：我偏好 FastAPI pending-confirm {token} 示例"},
        )
        second_response = await client.post(
            "/api/v1/chat",
            json={
                "message": f"请记住：我偏好 Django pending-confirm {token} 示例",
                "session_id": first_response.json()["session_id"],
            },
        )
        trace_response = await client.get(f"/api/v1/runs/{second_response.json()['run_id']}")
        memories_response = await client.get("/api/v1/memories", params={"query": token})

    active_memories = [memory for memory in memories_response.json() if memory["status"] == "active"]
    assert len(active_memories) == 2
    steps = trace_response.json()["steps"]
    policy_payload = json.loads(next(step["content"] for step in steps if step["kind"] == "memory_policy_decision"))
    saved_payload = json.loads(next(step["content"] for step in steps if step["kind"] == "memory_saved"))
    assert policy_payload["conflict_decision"]["resolution"] == "pending_confirmation"
    assert policy_payload["conflict_decision"]["outcome"] == "coexists"
    assert saved_payload["conflict_resolution"] == "pending_confirmation"
    assert saved_payload["conflict_outcome"] == "coexists"
    assert saved_payload["supersedes_memory_id"] is None


@pytest.mark.asyncio
async def test_run_trace_endpoint_returns_steps():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        chat_response = await client.post("/api/v1/chat", json={"message": "请统计 hello world"})
        run_id = chat_response.json()["run_id"]

        trace_response = await client.get(f"/api/v1/runs/{run_id}")

    assert trace_response.status_code == 200
    trace = trace_response.json()
    assert trace["id"] == run_id
    assert trace["steps"]
    assert trace["tool_calls"]
    assert {"memory_retrieval_started", "memory_retrieved", "memory_policy_decision"}.issubset(
        {step["kind"] for step in trace["steps"]}
    )


def test_usage_summary_normalizes_claude_and_openai_payloads():
    claude_summary = usage_summary_from_payload(
        "claude",
        {
            "input_tokens": 120,
            "output_tokens": 30,
            "cache_creation_input_tokens": 50,
            "cache_read_input_tokens": 10,
        },
    )
    openai_summary = usage_summary_from_payload(
        "openai",
        {"prompt_tokens": 100, "completion_tokens": 40, "total_tokens": 140},
    )

    assert claude_summary is not None
    assert claude_summary.total_tokens == 210
    assert openai_summary is not None
    assert openai_summary.input_tokens == 100
    assert openai_summary.output_tokens == 40


def test_cost_estimate_returns_notice_for_unknown_model_and_value_for_known_model():
    usage_summary = usage_summary_from_payload(
        "openai",
        {"prompt_tokens": 1000, "completion_tokens": 500, "total_tokens": 1500},
    )

    estimate, notice = estimate_cost("gpt-4.1", usage_summary)
    unknown_estimate, unknown_notice = estimate_cost("unknown-model", usage_summary)

    assert estimate is not None
    assert estimate.total_cost is not None
    assert notice is not None
    assert unknown_estimate is None
    assert "没有内置价格表" in str(unknown_notice)


def test_provider_error_classification_covers_auth_rate_limit_timeout_and_tool_schema():
    auth = classify_provider_error("openai", Exception("401 invalid_api_key"))
    rate = classify_provider_error("openai", Exception("429 rate limit exceeded"))
    timeout = classify_provider_error("openai", Exception("ReadTimeout request timed out"))
    tool_schema = classify_provider_error("openai", Exception("invalid tools schema"))

    assert auth.code == "auth_failed"
    assert rate.code == "rate_limited"
    assert rate.retryable is True
    assert timeout.code == "timeout"
    assert tool_schema.code == "tool_schema_incompatible"


@pytest.mark.asyncio
async def test_chat_context_includes_recent_messages_from_same_session_only():
    first_message = "第一轮：我正在学习短期上下文"
    other_session_message = "隔离会话：不应进入上下文"
    second_message = "第二轮：请结合刚才的话"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first_response = await client.post("/api/v1/chat", json={"message": first_message})
        first_payload = first_response.json()
        await client.post("/api/v1/chat", json={"message": other_session_message})
        second_response = await client.post(
            "/api/v1/chat",
            json={"message": second_message, "session_id": first_payload["session_id"]},
        )
        trace_response = await client.get(f"/api/v1/runs/{second_response.json()['run_id']}")

    assert second_response.status_code == 200
    assert trace_response.status_code == 200
    contexts = [
        json.loads(step["content"])["context"]
        for step in trace_response.json()["steps"]
        if step["kind"] == "context_built"
    ]
    assert contexts
    context = contexts[0]
    assert "recent_messages:" in context
    assert first_message in context
    assert first_payload["answer"] in context
    assert second_message in context
    assert other_session_message not in context


@pytest.mark.asyncio
async def test_long_session_context_includes_summary_without_current_message(monkeypatch):
    monkeypatch.setenv("AGENT_PLAYGROUND_SUMMARY_TRIGGER_MESSAGE_COUNT", "4")
    monkeypatch.setenv("AGENT_PLAYGROUND_SUMMARY_RECENT_MESSAGE_KEEP", "2")
    from app.core.config import get_settings

    get_settings.cache_clear()
    current_message = "第六轮：当前 turn 不应提前进入摘要"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first_response = await client.post("/api/v1/chat", json={"message": "第一轮：记住我的偏好是 FastAPI"})
        session_id = first_response.json()["session_id"]
        for message in [
            "第二轮：目标是学习 Agent 上下文压缩",
            "第三轮：必须保留最近消息窗口",
            "第四轮：不要把长期记忆和 session summary 混淆",
            "第五轮：最近内容用于原文上下文",
        ]:
            await client.post("/api/v1/chat", json={"message": message, "session_id": session_id})
        response = await client.post("/api/v1/chat", json={"message": current_message, "session_id": session_id})
        trace_response = await client.get(f"/api/v1/runs/{response.json()['run_id']}")

    assert response.status_code == 200
    steps = trace_response.json()["steps"]
    kinds = {step["kind"] for step in steps}
    assert {"session_summary_checked", "session_summary_updated", "session_summary_used"}.issubset(kinds)
    contexts = [json.loads(step["content"])["context"] for step in steps if step["kind"] == "context_built"]
    context_traces = [json.loads(step["content"])["context_trace"] for step in steps if step["kind"] == "context_built"]
    assert contexts
    context = contexts[0]
    assert "session_summary:" in context
    assert "recent_messages:" in context
    recent_section = context.split("recent_messages:", 1)[1].split("\n\n", 1)[0]
    assert current_message not in recent_section
    assert context_traces[0]["blocks"]
    assert context_traces[0]["total_budget_chars"] > 0
    assert context_traces[0]["budget_unit"] == "chars"
    summary_step = next(step for step in steps if step["kind"] == "session_summary_used")
    summary = json.loads(summary_step["content"])["summary"]
    summary_json = json.loads(summary_step["content"])["summary_json"]
    assert "第一轮：记住我的偏好是 FastAPI" in summary
    assert "done" in summary_json
    assert current_message not in summary


@pytest.mark.asyncio
async def test_memory_roundtrip_retrieves_injects_and_uses_saved_memory():
    memory_message = "请记住：我偏好 FastAPI 示例"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first_response = await client.post("/api/v1/chat", json={"message": memory_message})
        second_response = await client.post(
            "/api/v1/chat",
            json={"message": "FastAPI 是我的什么偏好？", "session_id": first_response.json()["session_id"]},
        )
        trace_response = await client.get(f"/api/v1/runs/{second_response.json()['run_id']}")

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    payload = second_response.json()
    assert payload["used_memories"]
    assert "FastAPI" in payload["answer"]
    contexts = [
        json.loads(step["content"])
        for step in trace_response.json()["steps"]
        if step["kind"] == "context_built"
    ]
    assert any(memory_message in context["context"] for context in contexts)
    assert any(any(block["name"] == "memories" for block in context["context_trace"]["blocks"]) for context in contexts)


@pytest.mark.asyncio
async def test_chat_updates_memory_usage_and_trace_explains_retrieval():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first_response = await client.post(
            "/api/v1/chat",
            json={"message": "请记住：我偏好 FastAPI usage-feedback 示例"},
        )
        second_response = await client.post(
            "/api/v1/chat",
            json={"message": "FastAPI usage-feedback 示例", "session_id": first_response.json()["session_id"]},
        )
        memories_response = await client.get("/api/v1/memories", params={"query": "usage-feedback"})
        trace_response = await client.get(f"/api/v1/runs/{second_response.json()['run_id']}")

    assert memories_response.status_code == 200
    memory = memories_response.json()[0]
    assert memory["use_count"] == 1
    assert memory["last_used_at"] is not None
    assert memory["conflict_key"] == "preference:framework-example"

    trace = trace_response.json()
    memory_steps = [step for step in trace["steps"] if step["kind"] == "memory_retrieved"]
    assert memory_steps
    payload = json.loads(memory_steps[0]["content"])
    assert payload["terms"]
    assert payload["matches"][0]["score"] > 0
    assert payload["matches"][0]["matched_terms"]
    assert payload["matches"][0]["reason"]
    assert payload["matches"][0]["conflict_key"] == "preference:framework-example"
    assert payload["matches"][0]["rank_signals"]["term_hits"] > 0


@pytest.mark.asyncio
async def test_chat_memory_trace_records_conflict_decision_and_supersedes_link():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first_response = await client.post(
            "/api/v1/chat",
            json={"message": "请记住：我偏好 FastAPI conflict-link 示例"},
        )
        second_response = await client.post(
            "/api/v1/chat",
            json={
                "message": "请记住：我偏好以后用 Django conflict-link 示例",
                "session_id": first_response.json()["session_id"],
            },
        )
        trace_response = await client.get(f"/api/v1/runs/{second_response.json()['run_id']}")
        memories_response = await client.get("/api/v1/memories", params={"query": "conflict-link"})

    memories = memories_response.json()
    active = next(memory for memory in memories if memory["status"] == "active")
    superseded = next(memory for memory in memories if memory["status"] == "superseded")
    assert active["supersedes_memory_id"] == superseded["id"]

    steps = trace_response.json()["steps"]
    policy_payload = json.loads(next(step["content"] for step in steps if step["kind"] == "memory_policy_decision"))
    saved_payload = json.loads(next(step["content"] for step in steps if step["kind"] == "memory_saved"))
    superseded_payload = json.loads(next(step["content"] for step in steps if step["kind"] == "memory_superseded"))
    assert policy_payload["conflict_decision"]["resolution"] == "supersedes"
    assert policy_payload["conflict_decision"]["outcome"] == "supersedes"
    assert saved_payload["conflict_resolution"] == "supersedes"
    assert saved_payload["conflict_outcome"] == "supersedes"
    assert saved_payload["supersedes_memory_id"] == superseded["id"]
    assert superseded_payload["resolution"] == "supersedes"
    assert superseded_payload["outcome"] == "supersedes"


@pytest.mark.asyncio
async def test_run_list_endpoint_returns_summaries():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        chat_response = await client.post("/api/v1/chat", json={"message": "请统计 hello world"})
        run_id = chat_response.json()["run_id"]

        response = await client.get("/api/v1/runs")

    assert response.status_code == 200
    runs = response.json()
    assert any(run["id"] == run_id for run in runs)
    run = next(run for run in runs if run["id"] == run_id)
    assert run["status"] == "completed"
    assert run["tool_count"] >= 1
    assert run["step_count"] >= 1
    assert run["duration_ms"] is not None


@pytest.mark.asyncio
async def test_run_list_endpoint_supports_status_tool_and_time_filters():
    class FailingModel:
        async def next_turn(self, user_message, context, tool_results):
            raise ModelAdapterError("forced failure for filtering")

    async with AsyncSessionLocal() as db:
        failed_run = await AgentRunner(db, build_default_registry(), model=FailingModel()).run("ses_failed", "你好", [])
        await db.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        completed_response = await client.post("/api/v1/chat", json={"message": "请统计 hello world"})
        created_from = datetime.now(UTC) - timedelta(minutes=5)
        created_to = datetime.now(UTC) + timedelta(minutes=5)
        failed_only = await client.get("/api/v1/runs", params={"status": "failed"})
        text_stats_runs = await client.get("/api/v1/runs", params={"tool_name": "text_stats"})
        time_filtered = await client.get(
            "/api/v1/runs",
            params={"created_from": created_from.isoformat(), "created_to": created_to.isoformat()},
        )

    assert failed_only.status_code == 200
    assert all(run["status"] == "failed" for run in failed_only.json())
    assert any(run["id"] == failed_run.id for run in failed_only.json())

    assert text_stats_runs.status_code == 200
    assert any(run["id"] == completed_response.json()["run_id"] for run in text_stats_runs.json())

    assert time_filtered.status_code == 200
    returned_ids = {run["id"] for run in time_filtered.json()}
    assert completed_response.json()["run_id"] in returned_ids
    assert failed_run.id in returned_ids


@pytest.mark.asyncio
async def test_dashboard_run_stats_endpoint_returns_recent_failure_and_model_error():
    class FailingModel:
        async def next_turn(self, user_message, context, tool_results):
            raise ModelAdapterError("forced dashboard failure")

    async with AsyncSessionLocal() as db:
        await AgentRunner(db, build_default_registry(), model=FailingModel()).run("ses_failed_stats", "你好", [])
        await db.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/v1/chat", json={"message": "请统计 hello world"})
        response = await client.get("/api/v1/dashboard/run-stats", params={"sample_size": 20})

    assert response.status_code == 200
    payload = response.json()
    assert payload["sample_size"] >= 2
    assert payload["failed_runs"] >= 1
    assert payload["average_duration_ms"] is not None
    assert payload["latest_model_error"]


@pytest.mark.asyncio
async def test_stream_chat_emits_observable_events():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/chat/stream", json={"message": "请统计 hello world"})

    assert response.status_code == 200
    body = response.text
    assert "event: run_started" in body
    assert "event: memory_retrieval_started" in body
    assert "event: memory_retrieved" in body
    assert "event: model_turn" in body
    assert "event: tool_call" in body
    assert "event: tool_result" in body
    assert "event: latency_metric" in body
    assert "event: message_delta" in body
    assert "event: run_finished" in body


@pytest.mark.asyncio
async def test_stream_chat_emits_message_delta_before_run_finished():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/chat/stream", json={"message": "你好"})

    assert response.status_code == 200
    body = response.text
    assert body.index("event: message_delta") < body.index("event: run_finished")


@pytest.mark.asyncio
async def test_stream_chat_persists_assistant_message():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/chat/stream", json={"message": "你好"})

    assert response.status_code == 200
    assert "event: run_finished" in response.text
    session_id = None
    for line in response.text.splitlines():
        if not line.startswith("data: "):
            continue
        data = json.loads(line.removeprefix("data: "))
        if data.get("session_id"):
            session_id = data["session_id"]
            break
    assert session_id is not None

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Message).where(Message.session_id == session_id).order_by(Message.created_at.asc(), Message.id.asc())
        )
        messages = list(result.scalars())

    roles = [message.role for message in messages]
    assert roles == [MessageRole.user, MessageRole.assistant]
    assert messages[1].content
