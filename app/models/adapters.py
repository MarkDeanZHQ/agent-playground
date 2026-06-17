import json
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from anthropic import AnthropicError, AsyncAnthropic
from openai import AsyncOpenAI, OpenAIError

from app.core.config import get_settings
from app.schemas.api import ModelTurn, ToolCallRequest, ToolCallResult
from app.tools.registry import ToolRegistry


class ModelAdapterError(RuntimeError):
    """Sanitized model-provider error safe to record in traces and responses."""


class ModelAdapter(ABC):
    @abstractmethod
    async def next_turn(
        self,
        user_message: str,
        context: str,
        tool_results: list[ToolCallResult],
    ) -> ModelTurn:
        raise NotImplementedError

    async def stream_turn(
        self,
        user_message: str,
        context: str,
        tool_results: list[ToolCallResult],
    ) -> AsyncIterator[str | ModelTurn]:
        turn = await self.next_turn(user_message, context, tool_results)
        if turn.kind == "final" and turn.content:
            yield turn.content
        yield turn


class FakeModelAdapter(ModelAdapter):
    async def next_turn(
        self,
        user_message: str,
        context: str,
        tool_results: list[ToolCallResult],
    ) -> ModelTurn:
        lower = user_message.lower()

        if tool_results:
            summary = "; ".join(f"{result.name}: {result.content}" for result in tool_results)
            return ModelTurn(kind="final", content=f"我已经根据工具结果完成处理：{summary}", finish_reason="fake")

        if any(keyword in user_message for keyword in ("提取", "extract", "字段", "json")):
            extract_text = user_message
            for separator in ("：", ":"):
                if separator in user_message:
                    extract_text = user_message.split(separator, 1)[1].strip()
                    break
            tool_call = ToolCallRequest(
                name="json_extract",
                arguments={
                    "text": extract_text,
                    "fields": ["name", "email", "city"],
                },
            )
            return ModelTurn(
                kind="tool_call",
                tool_call=tool_call,
                tool_calls=[tool_call],
                finish_reason="fake_tool_call",
            )

        if any(keyword in user_message for keyword in ("创建待办", "添加待办", "todo create", "新增待办")):
            title = user_message.split("：", 1)[-1].strip() if "：" in user_message else user_message
            tool_call = ToolCallRequest(name="todo_create", arguments={"title": title})
            return ModelTurn(
                kind="tool_call",
                tool_call=tool_call,
                tool_calls=[tool_call],
                finish_reason="fake_tool_call",
            )

        if any(keyword in user_message for keyword in ("待办列表", "列出待办", "todo list")):
            tool_call = ToolCallRequest(name="todo_list", arguments={})
            return ModelTurn(
                kind="tool_call",
                tool_call=tool_call,
                tool_calls=[tool_call],
                finish_reason="fake_tool_call",
            )

        if "统计" in user_message or "count" in lower or "stats" in lower:
            tool_call = ToolCallRequest(name="text_stats", arguments={"text": user_message})
            return ModelTurn(
                kind="tool_call",
                tool_call=tool_call,
                tool_calls=[tool_call],
                finish_reason="fake_tool_call",
            )

        if "笔记" in user_message or "note" in lower or "search" in lower:
            keyword = user_message.split()[-1] if user_message.split() else user_message
            tool_call = ToolCallRequest(name="note_search", arguments={"query": keyword})
            return ModelTurn(
                kind="tool_call",
                tool_call=tool_call,
                tool_calls=[tool_call],
                finish_reason="fake_tool_call",
            )

        asks_about_preference = any(keyword in user_message for keyword in ("偏好", "喜欢", "什么"))
        if asks_about_preference and "memories:" in context:
            memory_block = context.split("memories:", 1)[1].split("\n\n", 1)[0]
            memories = [line.removeprefix("- ").strip() for line in memory_block.splitlines() if line.strip()]
            if memories:
                return ModelTurn(
                    kind="final",
                    content="根据已保存记忆，" + "；".join(memories),
                    finish_reason="fake",
                )

        if context:
            return ModelTurn(kind="final", content=f"基于当前上下文，我的回答是：{user_message}", finish_reason="fake")
        return ModelTurn(kind="final", content=f"这是一个无需工具的回答：{user_message}", finish_reason="fake")



@dataclass(frozen=True)
class ClaudeResponseParts:
    stop_reason: str
    usage: dict[str, int]
    assistant_content: list[dict[str, Any]]
    tool_calls: list[ToolCallRequest]

class ClaudeModelAdapter(ModelAdapter):
    def __init__(self, tools: ToolRegistry) -> None:
        settings = get_settings()
        kwargs: dict[str, Any] = {"timeout": settings.llm_timeout_seconds, "max_retries": settings.llm_max_retries}
        if settings.anthropic_api_key:
            kwargs["api_key"] = settings.anthropic_api_key
        self.client = AsyncAnthropic(**kwargs)
        self.model = settings.claude_model
        self.max_tokens = settings.claude_max_tokens
        self.effort = settings.claude_effort
        self.thinking = settings.claude_thinking
        self.tool_definitions = tools.list_definitions()
        self.pending_assistant_content: list[dict[str, Any]] | None = None

    async def next_turn(
        self,
        user_message: str,
        context: str,
        tool_results: list[ToolCallResult],
    ) -> ModelTurn:
        messages = self._build_messages(user_message, context, tool_results)
        try:
            response = await self.client.messages.create(**self._request_kwargs(messages))
        except AnthropicError as exc:
            raise ModelAdapterError(f"Claude 请求失败：{exc.__class__.__name__}") from exc
        return self._turn_from_response(response)

    async def stream_turn(
        self,
        user_message: str,
        context: str,
        tool_results: list[ToolCallResult],
    ) -> AsyncIterator[str | ModelTurn]:
        messages = self._build_messages(user_message, context, tool_results)
        try:
            async with self.client.messages.stream(**self._request_kwargs(messages)) as stream:
                async for text in stream.text_stream:
                    if text:
                        yield str(text)
                response = await stream.get_final_message()
        except AnthropicError as exc:
            raise ModelAdapterError(f"Claude 流式请求失败：{exc.__class__.__name__}") from exc
        yield self._turn_from_response(response)

    def _request_kwargs(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": (
                "你是 Agent Playground 的教学型 Agent。"
                "你可以选择调用工具，也可以直接回答。"
                "如果工具结果已经足够，请用中文给出简明最终答案。"
            ),
            "messages": messages,
            "output_config": {"effort": self.effort},
        }
        if self.tool_definitions:
            kwargs["tools"] = self.tool_definitions
        if self.model == "claude-fable-5":
            kwargs["thinking"] = {"type": "adaptive"}
        elif self.model in {"claude-opus-4-8", "claude-opus-4-7"} and self.thinking != "adaptive":
            kwargs["thinking"] = {"type": "adaptive"}
        elif self.thinking != "disabled":
            kwargs["thinking"] = {"type": self.thinking}
        return kwargs

    def _turn_from_response(self, response: Any) -> ModelTurn:
        parts = self._response_parts(response)
        if parts.stop_reason == "refusal":
            return self._handle_refusal_response(parts)
        if parts.stop_reason == "tool_use" or parts.tool_calls:
            return self._build_tool_call_turn(parts)
        return self._build_final_turn(parts)

    def _response_parts(self, response: Any) -> ClaudeResponseParts:
        assistant_content: list[dict[str, Any]] = []
        tool_calls: list[ToolCallRequest] = []
        for block in getattr(response, "content", []):
            serialized = self._serialize_content_block(block)
            assistant_content.append(serialized)
            if serialized.get("type") == "tool_use":
                tool_calls.append(self._tool_call_from_block(serialized))
        return ClaudeResponseParts(
            stop_reason=self._safe_str(getattr(response, "stop_reason", None)),
            usage=self._usage_dict(getattr(response, "usage", None)),
            assistant_content=assistant_content,
            tool_calls=tool_calls,
        )

    def _handle_refusal_response(self, parts: ClaudeResponseParts) -> ModelTurn:
        self.pending_assistant_content = None
        return ModelTurn(
            kind="final",
            content="Claude 拒绝了本次请求，请调整输入后重试。",
            finish_reason=parts.stop_reason,
            usage=parts.usage,
        )

    def _tool_call_from_block(self, block: dict[str, Any]) -> ToolCallRequest:
        raw_input = block.get("input", {})
        arguments = raw_input if isinstance(raw_input, dict) else {}
        return ToolCallRequest(
            id=self._safe_str(block.get("id")),
            name=str(block.get("name", "")),
            arguments=arguments,
        )

    def _build_tool_call_turn(self, parts: ClaudeResponseParts) -> ModelTurn:
        if not parts.tool_calls:
            self.pending_assistant_content = None
            raise ModelAdapterError("Claude 响应 stop_reason=tool_use，但没有 tool_use 内容块。")
        self.pending_assistant_content = parts.assistant_content
        return ModelTurn(
            kind="tool_call",
            tool_call=parts.tool_calls[0],
            tool_calls=parts.tool_calls,
            finish_reason=parts.stop_reason,
            usage=parts.usage,
        )

    def _build_final_turn(self, parts: ClaudeResponseParts) -> ModelTurn:
        self.pending_assistant_content = None
        text = "".join(
            str(block.get("text", "")) for block in parts.assistant_content if block.get("type") == "text"
        )
        truncated = parts.stop_reason == "max_tokens"
        if truncated and text:
            text = f"{text}\n\n（模型输出达到最大 token 限制，结果可能被截断。）"
        return ModelTurn(
            kind="final",
            content=text or "Claude 未返回文本内容。",
            finish_reason=parts.stop_reason,
            usage=parts.usage,
            truncated=truncated,
        )

    def _build_messages(
        self,
        user_message: str,
        context: str,
        tool_results: list[ToolCallResult],
    ) -> list[dict[str, Any]]:
        content = f"用户消息：{user_message}\n\n当前上下文：\n{context}"
        messages: list[dict[str, Any]] = [{"role": "user", "content": content}]
        if tool_results:
            if not self.pending_assistant_content:
                raise ModelAdapterError("Claude 工具结果缺少对应的 assistant tool_use 上下文。")
            messages.append({"role": "assistant", "content": self.pending_assistant_content})
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": result.id,
                            "content": result.content,
                            "is_error": result.is_error,
                        }
                        for result in tool_results
                        if result.id
                    ],
                }
            )
        return messages

    def _serialize_content_block(self, block: Any) -> dict[str, Any]:
        if hasattr(block, "model_dump"):
            dumped = block.model_dump(mode="json")
            if isinstance(dumped, dict):
                return dumped
        block_type = getattr(block, "type", None)
        if block_type == "text":
            return {"type": "text", "text": getattr(block, "text", "")}
        if block_type == "tool_use":
            raw_input = getattr(block, "input", {})
            return {
                "type": "tool_use",
                "id": getattr(block, "id", None),
                "name": getattr(block, "name", ""),
                "input": raw_input if isinstance(raw_input, dict) else {},
            }
        if block_type in {"thinking", "redacted_thinking"}:
            return {"type": block_type}
        return {"type": str(block_type or "unknown")}

    def _usage_dict(self, usage: Any) -> dict[str, int]:
        result: dict[str, int] = {}
        for key in ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"):
            value = getattr(usage, key, None)
            if isinstance(value, int):
                result[key] = value
        return result

    def _safe_str(self, value: Any) -> str | None:
        return value if isinstance(value, str) else None


class OpenAIModelAdapter(ModelAdapter):
    def __init__(self, tools: ToolRegistry) -> None:
        settings = get_settings()
        kwargs: dict[str, Any] = {"timeout": settings.llm_timeout_seconds, "max_retries": settings.llm_max_retries}
        if settings.openai_api_key:
            kwargs["api_key"] = settings.openai_api_key
        if settings.openai_base_url:
            kwargs["base_url"] = settings.openai_base_url
        if settings.openai_user_agent:
            kwargs["default_headers"] = {"User-Agent": settings.openai_user_agent}
        self.client = AsyncOpenAI(**kwargs)
        self.model = settings.openai_model
        self.max_tokens = settings.openai_max_tokens
        protocol_mode = settings.effective_openai_protocol_mode
        custom_base_url = bool(settings.openai_base_url and "api.openai.com" not in settings.openai_base_url)
        self.compatibility_mode = protocol_mode == "on" or (protocol_mode == "auto" and custom_base_url)
        self.protocol_mode = protocol_mode
        self.tool_calling_enabled = settings.openai_tool_calling
        self.token_parameter = "max_tokens" if self.compatibility_mode else settings.openai_token_parameter
        self.tool_definitions = [self._to_openai_tool(tool) for tool in tools.list_definitions()]
        if not self.tool_calling_enabled:
            self.tool_definitions = []
        self.pending_assistant_message: dict[str, Any] | None = None

    async def next_turn(
        self,
        user_message: str,
        context: str,
        tool_results: list[ToolCallResult],
    ) -> ModelTurn:
        try:
            response = await self.client.chat.completions.create(
                **self._request_kwargs(self._build_messages(user_message, context, tool_results))
            )
            return self._turn_from_response(response)
        except ModelAdapterError:
            raise
        except OpenAIError as exc:
            raise ModelAdapterError(f"OpenAI 请求失败：{exc.__class__.__name__}: {self._safe_str(exc)}") from exc
        except Exception as exc:
            raise ModelAdapterError(f"OpenAI 请求异常：{exc.__class__.__name__}: {self._safe_str(exc)}") from exc

    async def stream_turn(
        self,
        user_message: str,
        context: str,
        tool_results: list[ToolCallResult],
    ) -> AsyncIterator[str | ModelTurn]:
        if self.compatibility_mode:
            async for part in self._stream_turn_compatible(user_message, context, tool_results):
                yield part
            return

        try:
            async with self.client.chat.completions.stream(
                **self._request_kwargs(self._build_messages(user_message, context, tool_results))
            ) as stream:
                async for event in stream:
                    if getattr(event, "type", None) == "content.delta":
                        delta = getattr(event, "delta", None)
                        if delta is None:
                            delta = getattr(event, "content", "")
                        if delta:
                            yield str(delta)
                response = await stream.get_final_completion()
            turn = self._turn_from_response(response)
        except ModelAdapterError:
            raise
        except OpenAIError as exc:
            raise ModelAdapterError(f"OpenAI 流式请求失败：{exc.__class__.__name__}: {self._safe_str(exc)}") from exc
        except Exception as exc:
            raise ModelAdapterError(f"OpenAI 流式请求异常：{exc.__class__.__name__}: {self._safe_str(exc)}") from exc
        yield turn

    async def _stream_turn_compatible(
        self,
        user_message: str,
        context: str,
        tool_results: list[ToolCallResult],
    ) -> AsyncIterator[str | ModelTurn]:
        try:
            content_parts: list[str] = []
            finish_reason = ""
            usage: dict[str, int] = {}
            stream = await self.client.chat.completions.create(
                **self._request_kwargs(self._build_messages(user_message, context, tool_results)),
                stream=True,
            )
            async for chunk in stream:
                usage.update(self._usage_dict(getattr(chunk, "usage", None)))
                choices = getattr(chunk, "choices", None) or []
                if not choices:
                    continue
                choice = choices[0]
                finish_reason = self._safe_str(getattr(choice, "finish_reason", None)) or finish_reason
                delta = getattr(choice, "delta", None)
                if delta is None:
                    continue
                text = self._safe_str(getattr(delta, "content", None))
                if text:
                    content_parts.append(text)
                    yield text
        except ModelAdapterError:
            raise
        except OpenAIError as exc:
            message = f"OpenAI 兼容流式请求失败：{exc.__class__.__name__}: {self._safe_str(exc)}"
            raise ModelAdapterError(message) from exc
        except Exception as exc:
            message = f"OpenAI 兼容流式请求异常：{exc.__class__.__name__}: {self._safe_str(exc)}"
            raise ModelAdapterError(message) from exc

        content = "".join(content_parts) or "OpenAI 模型未返回文本内容。"
        truncated = finish_reason == "length"
        if truncated and content:
            content = f"{content}\n\n（模型输出达到最大 token 限制，结果可能被截断。）"
        yield ModelTurn(kind="final", content=content, finish_reason=finish_reason, usage=usage, truncated=truncated)

    def _request_kwargs(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            self.token_parameter: self.max_tokens,
        }
        if self.tool_definitions:
            kwargs["tools"] = self.tool_definitions
        return kwargs

    def _turn_from_response(self, response: Any) -> ModelTurn:
        choices = getattr(response, "choices", None)
        if not choices:
            raise ModelAdapterError("OpenAI 响应中没有 choices。")
        choice = choices[0]
        finish_reason = self._safe_str(getattr(choice, "finish_reason", None))
        message = getattr(choice, "message", None)
        if message is None:
            raise ModelAdapterError("OpenAI 响应中没有 message。")
        usage = self._usage_dict(getattr(response, "usage", None))
        if getattr(message, "tool_calls", None):
            tool_calls = []
            for tool_call in message.tool_calls:
                function = getattr(tool_call, "function", None)
                raw_arguments = getattr(function, "arguments", "")
                tool_calls.append(
                    ToolCallRequest(
                        id=self._safe_str(getattr(tool_call, "id", None)),
                        name=str(getattr(function, "name", "")),
                        arguments=self._parse_tool_arguments(raw_arguments),
                    )
                )
            self.pending_assistant_message = {
                "role": "assistant",
                "content": getattr(message, "content", None),
                "tool_calls": [self._serialize_tool_call(tool_call) for tool_call in message.tool_calls],
            }
            return ModelTurn(
                kind="tool_call",
                tool_call=tool_calls[0],
                tool_calls=tool_calls,
                finish_reason=finish_reason,
                usage=usage,
            )

        self.pending_assistant_message = None
        content = getattr(message, "content", None) or "OpenAI 模型未返回文本内容。"
        truncated = finish_reason == "length"
        if truncated and content:
            content = f"{content}\n\n（模型输出达到最大 token 限制，结果可能被截断。）"
        return ModelTurn(kind="final", content=content, finish_reason=finish_reason, usage=usage, truncated=truncated)

    def _build_messages(
        self,
        user_message: str,
        context: str,
        tool_results: list[ToolCallResult],
    ) -> list[dict[str, Any]]:
        content = f"用户消息：{user_message}\n\n当前上下文：\n{context}"
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": "你是 Agent Playground 的教学型 Agent。必要时调用工具，否则直接用中文回答。",
            },
            {"role": "user", "content": content},
        ]
        if tool_results:
            if not self.pending_assistant_message:
                raise ModelAdapterError("OpenAI 工具结果缺少对应的 assistant tool_calls 上下文。")
            messages.append(self.pending_assistant_message)
            messages.extend(
                {
                    "role": "tool",
                    "tool_call_id": result.id,
                    "content": result.content,
                }
                for result in tool_results
                if result.id
            )
        return messages

    def _to_openai_tool(self, tool: dict[str, Any]) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool["description"],
                "parameters": tool["input_schema"],
            },
        }

    def _serialize_tool_call(self, tool_call: Any) -> dict[str, Any]:
        function = getattr(tool_call, "function", None)
        return {
            "id": getattr(tool_call, "id", None),
            "type": "function",
            "function": {
                "name": getattr(function, "name", ""),
                "arguments": getattr(function, "arguments", "{}"),
            },
        }

    def _parse_tool_arguments(self, arguments: str) -> dict[str, Any]:
        try:
            parsed = json.loads(arguments or "{}")
        except json.JSONDecodeError:
            return {"_parse_error": "invalid_json", "_raw_arguments": arguments}
        if isinstance(parsed, dict):
            return parsed
        return {"_parse_error": "arguments_must_be_object", "_raw_arguments": arguments}

    def _usage_dict(self, usage: Any) -> dict[str, int]:
        result: dict[str, int] = {}
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            value = getattr(usage, key, None)
            if isinstance(value, int):
                result[key] = value
        return result

    def _safe_str(self, value: Any) -> str:
        if isinstance(value, str):
            return value
        if value is None:
            return ""
        return str(value)
