from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.schemas.api import CostEstimate, ProviderErrorInfo, UsageSummary


@dataclass(frozen=True)
class _ModelPricing:
    input_per_million: float
    output_per_million: float
    cache_write_per_million: float = 0.0
    cache_read_per_million: float = 0.0


_PRICE_TABLE: dict[str, _ModelPricing] = {
    "claude-opus-4-8": _ModelPricing(
        input_per_million=15.0,
        output_per_million=75.0,
        cache_write_per_million=18.75,
        cache_read_per_million=1.5,
    ),
    "claude-opus-4-7": _ModelPricing(
        input_per_million=15.0,
        output_per_million=75.0,
        cache_write_per_million=18.75,
        cache_read_per_million=1.5,
    ),
    "claude-fable-5": _ModelPricing(
        input_per_million=3.0,
        output_per_million=15.0,
        cache_write_per_million=3.75,
        cache_read_per_million=0.3,
    ),
    "gpt-4.1": _ModelPricing(input_per_million=2.0, output_per_million=8.0),
    "gpt-4.1-mini": _ModelPricing(input_per_million=0.4, output_per_million=1.6),
    "gpt-4o": _ModelPricing(input_per_million=5.0, output_per_million=15.0),
    "gpt-4o-mini": _ModelPricing(input_per_million=0.15, output_per_million=0.6),
}


def usage_summary_from_payload(provider: str, usage: dict[str, Any] | None) -> UsageSummary | None:
    if not usage:
        return None
    normalized: dict[str, int] = {}
    for key, value in usage.items():
        if isinstance(value, int):
            normalized[key] = value
    if not normalized:
        return None

    if provider == "claude":
        input_tokens = normalized.get("input_tokens", 0)
        output_tokens = normalized.get("output_tokens", 0)
        cache_creation_tokens = normalized.get("cache_creation_input_tokens")
        cache_read_tokens = normalized.get("cache_read_input_tokens")
        total_tokens = input_tokens + output_tokens
        if cache_creation_tokens is not None:
            total_tokens += cache_creation_tokens
        if cache_read_tokens is not None:
            total_tokens += cache_read_tokens
        return UsageSummary(
            provider=provider,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            cache_creation_input_tokens=cache_creation_tokens,
            cache_read_input_tokens=cache_read_tokens,
            raw=normalized,
        )

    if provider == "openai":
        input_tokens = normalized.get("prompt_tokens", 0)
        output_tokens = normalized.get("completion_tokens", 0)
        total_tokens = normalized.get("total_tokens", input_tokens + output_tokens)
        return UsageSummary(
            provider=provider,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            raw=normalized,
        )

    return UsageSummary(
        provider=provider,
        input_tokens=normalized.get("input_tokens", 0),
        output_tokens=normalized.get("output_tokens", 0),
        total_tokens=normalized.get("total_tokens", 0),
        raw=normalized,
    )


def estimate_cost(model: str | None, usage_summary: UsageSummary | None) -> tuple[CostEstimate | None, str | None]:
    if usage_summary is None:
        return None, None
    if not model or model not in _PRICE_TABLE:
        return None, "当前模型没有内置价格表，无法估算成本。"

    pricing = _PRICE_TABLE[model]
    input_cost = _per_million_cost(usage_summary.input_tokens, pricing.input_per_million)
    output_cost = _per_million_cost(usage_summary.output_tokens, pricing.output_per_million)
    cache_write_cost = _per_million_cost(
        usage_summary.cache_creation_input_tokens,
        pricing.cache_write_per_million,
    )
    cache_read_cost = _per_million_cost(
        usage_summary.cache_read_input_tokens,
        pricing.cache_read_per_million,
    )
    total_cost = round(input_cost + output_cost + cache_write_cost + cache_read_cost, 6)
    return (
        CostEstimate(
            currency="USD",
            input_cost=input_cost or None,
            output_cost=output_cost or None,
            cache_write_cost=cache_write_cost or None,
            cache_read_cost=cache_read_cost or None,
            total_cost=total_cost,
            price_version="builtin-2026-06-18",
        ),
        "成本字段为教学估算值，不等于供应商最终账单。",
    )


def classify_provider_error(provider: str, exc: Exception) -> ProviderErrorInfo:
    message = _stringify_exception(exc)
    lower_message = message.lower()
    exc_name = exc.__class__.__name__
    retryable = False
    code = "unknown"
    suggestion = "查看错误消息、provider 配置和 trace 原始内容。"

    if any(token in lower_message for token in ("api key", "authentication", "unauthorized", "invalid_api_key", "401")):
        code = "auth_failed"
        suggestion = "检查 API Key、环境变量和账户授权状态。"
    elif any(token in lower_message for token in ("rate limit", "429", "too many requests", "quota")):
        code = "rate_limited"
        retryable = True
        suggestion = "稍后重试，或降低请求频率并检查账户配额。"
    elif any(token in lower_message for token in ("timeout", "timed out", "readtimeout", "apitimeouterror")):
        code = "timeout"
        retryable = True
        suggestion = "检查网络质量、超时配置，必要时降低请求复杂度。"
    elif any(token in lower_message for token in ("model_not_found", "unknown model", "does not exist", "404")):
        code = "model_not_found"
        suggestion = "检查模型名、base URL 对应平台和账户可用模型列表。"
    elif any(
        token in lower_message
        for token in (
            "tool schema",
            "function schema",
            "invalid tools",
            "invalid tool",
            "tool_calls",
            "function calling",
        )
    ):
        code = "tool_schema_incompatible"
        suggestion = "检查 tools/function schema 兼容性，必要时关闭 tool calling 或切换协议模式。"
    elif any(token in lower_message for token in ("connection", "dns", "ssl", "network", "connecterror")):
        code = "network_error"
        retryable = True
        suggestion = "检查网络、代理、DNS 和供应商 endpoint 连通性。"
    elif "openaierror" in exc_name.lower() or "anthropicerror" in exc_name.lower():
        code = "provider_request_failed"
        suggestion = "检查 provider 响应详情、请求参数和供应商状态页。"

    return ProviderErrorInfo(
        code=code,
        provider=provider,
        message=message,
        retryable=retryable,
        exception_type=exc_name,
        suggestion=suggestion,
    )


def usage_display_text(usage_summary: UsageSummary | None) -> str:
    if usage_summary is None:
        return "n/a"
    parts = [
        f"input={usage_summary.input_tokens}",
        f"output={usage_summary.output_tokens}",
        f"total={usage_summary.total_tokens}",
    ]
    if usage_summary.cache_creation_input_tokens is not None:
        parts.append(f"cache_write={usage_summary.cache_creation_input_tokens}")
    if usage_summary.cache_read_input_tokens is not None:
        parts.append(f"cache_read={usage_summary.cache_read_input_tokens}")
    return " ".join(parts)


def cost_display_text(cost_estimate: CostEstimate | None) -> str:
    if cost_estimate is None or cost_estimate.total_cost is None:
        return "n/a"
    return f"~${cost_estimate.total_cost:.6f}"


def _per_million_cost(tokens: int | None, price_per_million: float) -> float:
    if not tokens or price_per_million <= 0:
        return 0.0
    return round((tokens / 1_000_000) * price_per_million, 6)


def _stringify_exception(exc: Exception) -> str:
    message = str(exc).strip()
    if message:
        return message
    return exc.__class__.__name__
