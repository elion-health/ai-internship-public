"""Global test isolation for external observability services."""

import os
from types import SimpleNamespace

import pytest

# Set before application modules import ``load_dotenv()`` and LangSmith's decorators run.
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"


@pytest.fixture(autouse=True)
def _stub_braintrust_load_prompt(monkeypatch):
    """Never let tests hit the real Braintrust prompt API (see enrichment/profiles.py)."""
    import braintrust

    def _fake_load_prompt(*, project, slug, **kwargs):
        return SimpleNamespace(
            version=f"test-version:{slug}",
            build=lambda **build_kwargs: {
                "messages": [
                    {"role": "user", "content": build_kwargs.get("seeded_candidates", "")},
                    {"role": "system", "content": build_kwargs.get("source_content", "")},
                ]
            },
        )

    monkeypatch.setattr(braintrust, "load_prompt", _fake_load_prompt)
