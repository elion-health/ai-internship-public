import importlib.util
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "run_stateful_research", Path(__file__).parents[1] / "scripts" / "run_stateful_research.py"
)
assert _SPEC and _SPEC.loader
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


def test_target_cli_defaults_to_initial():
    args = _MODULE.parse_args(
        [
            "--name",
            "Fathom",
            "--url",
            "https://fathomhealth.com",
            "--profile",
            "customers",
        ]
    )

    assert args.mode == "initial"
    assert args.profile == ["customers"]


def test_target_cli_accepts_resume_and_forced_profile():
    args = _MODULE.parse_args(
        [
            "--name",
            "Fathom",
            "--url",
            "https://fathomhealth.com",
            "--profile",
            "customers",
            "--profile",
            "integrations",
            "--mode",
            "resume",
            "--force-profile",
            "customers",
        ]
    )

    assert args.mode == "resume"
    assert args.force_profile == ["customers"]


def test_target_cli_accepts_thread_id():
    args = _MODULE.parse_args(
        [
            "--name",
            "Fathom",
            "--url",
            "https://fathomhealth.com",
            "--profile",
            "customers",
            "--thread-id",
            "thread-1",
        ]
    )

    assert args.thread_id == "thread-1"


def test_target_cli_accepts_skip_external():
    args = _MODULE.parse_args(
        [
            "--name",
            "Fathom",
            "--url",
            "https://fathomhealth.com",
            "--profile",
            "customers",
            "--skip-external",
        ]
    )

    assert args.skip_external is True


def test_target_cli_accepts_first_party_only():
    args = _MODULE.parse_args(
        [
            "--name",
            "Fathom",
            "--url",
            "https://fathomhealth.com",
            "--profile",
            "customers",
            "--first-party-only",
        ]
    )

    assert args.first_party_only is True


def test_target_cli_does_not_expose_refresh():
    with pytest.raises(SystemExit):
        _MODULE.parse_args(
            [
                "--name",
                "Fathom",
                "--url",
                "https://fathomhealth.com",
                "--profile",
                "customers",
                "--mode",
                "refresh",
            ]
        )


@pytest.mark.asyncio
async def test_cli_runs_without_braintrust_callbacks(monkeypatch):
    class Graph:
        def with_config(self, config):
            assert None not in config.get("callbacks", [])
            return self

        async def ainvoke(self, state, config):
            return {"metrics": {}}

    class Driver:
        async def close(self):
            pass

    async def flush():
        pass

    monkeypatch.setattr(_MODULE, "setup_braintrust_tracing", lambda settings: None)
    monkeypatch.setattr(
        _MODULE, "build_stateful_research_graph", lambda settings: (Graph(), Driver(), None)
    )
    monkeypatch.setattr(_MODULE, "flush_braintrust", flush)
    args = _MODULE.parse_args(
        ["--name", "Acme", "--url", "https://example.com", "--profile", "customers"]
    )
    assert await _MODULE.main_async(args) == 0
