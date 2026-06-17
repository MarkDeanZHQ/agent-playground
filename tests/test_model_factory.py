from unittest.mock import patch

import pytest

from app.models.adapters import ClaudeModelAdapter, FakeModelAdapter, OpenAIModelAdapter
from app.models.factory import create_model_adapter
from app.tools.builtin import build_default_registry


def test_model_factory_uses_fake_provider(monkeypatch):
    monkeypatch.setenv("AGENT_PLAYGROUND_MODEL_PROVIDER", "fake")
    from app.core.config import get_settings

    get_settings.cache_clear()

    adapter = create_model_adapter(build_default_registry())

    assert isinstance(adapter, FakeModelAdapter)


@pytest.mark.parametrize(
    ("provider", "adapter_cls", "patch_target"),
    [
        ("claude", ClaudeModelAdapter, "app.models.adapters.AsyncAnthropic"),
        ("openai", OpenAIModelAdapter, "app.models.adapters.AsyncOpenAI"),
    ],
)
def test_model_factory_uses_real_provider_adapters(monkeypatch, provider, adapter_cls, patch_target):
    monkeypatch.setenv("AGENT_PLAYGROUND_MODEL_PROVIDER", provider)
    from app.core.config import get_settings

    get_settings.cache_clear()

    with patch(patch_target):
        adapter = create_model_adapter(build_default_registry())

    assert isinstance(adapter, adapter_cls)
