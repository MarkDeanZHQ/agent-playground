from app.core.config import get_settings
from app.models.adapters import ClaudeModelAdapter, FakeModelAdapter, ModelAdapter, OpenAIModelAdapter
from app.tools.registry import ToolRegistry


def create_model_adapter(tools: ToolRegistry) -> ModelAdapter:
    provider = get_settings().model_provider
    if provider == "fake":
        return FakeModelAdapter()
    if provider == "claude":
        return ClaudeModelAdapter(tools)
    if provider == "openai":
        return OpenAIModelAdapter(tools)
    raise ValueError(f"Unsupported model provider: {provider}")
