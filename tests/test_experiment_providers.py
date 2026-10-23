import importlib
from unittest.mock import MagicMock

import pytest

from ai_internship.config import Settings
from scripts.run_experiment import AGENTS


@pytest.mark.parametrize("module_name,class_name", AGENTS.values())
def test_all_experiments_route_models_through_openrouter(monkeypatch, module_name, class_name):
    module = importlib.import_module(f"ai_internship.agents.customers.experiments.{module_name}")
    calls = []

    def chat_model(**kwargs):
        calls.append(kwargs)
        return MagicMock()

    monkeypatch.setenv("PROMPT_SOURCE", "local")
    monkeypatch.setattr(module, "ChatOpenAI", chat_model)
    for name in ("Exa", "Firecrawl", "create_react_agent"):
        if hasattr(module, name):
            monkeypatch.setattr(module, name, MagicMock())
    settings = Settings(
        openrouter_api_key="test-router-credential",
        exa_api_key="test-search",
        firecrawl_api_key="test-fetch",
    )
    getattr(module, class_name)(settings=settings)
    assert calls
    for call in calls:
        assert call["api_key"] == "test-router-credential"
        assert call["base_url"] == "https://openrouter.ai/api/v1"
        assert call["model"].startswith("openai/")
