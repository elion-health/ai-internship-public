import hashlib
import json

import pytest


def test_manifest_matches_ten_product_bundle():
    from ai_internship.public_assets import assets_dir

    root = assets_dir()
    manifest = json.loads((root / "manifest.json").read_text())
    assert len(manifest["products"]) == 10
    for entry in manifest["datasets"]:
        path = root / entry["file"]
        data = json.loads(path.read_text())
        assert entry["rows"] == len(data["rows"]) == 10
        assert entry["relationships"] == sum(len(r["expected"][path.stem]) for r in data["rows"])
        assert {r["input"]["name"] for r in data["rows"]} == set(manifest["products"])
        assert hashlib.sha256(path.read_bytes()).hexdigest() == entry["sha256"]
    for entry in manifest["prompts"]:
        assert hashlib.sha256((root / entry["file"]).read_bytes()).hexdigest() == entry["sha256"]
        data = json.loads((root / entry["file"]).read_text())
        assert entry["versions"] == len(data["versions"]) == 1
        assert entry["latest"] == data["latest"] == data["versions"][0]["version"]


@pytest.mark.asyncio
async def test_evidence_judge_uses_bundled_prompts_without_hosted_access(monkeypatch):
    import braintrust

    from ai_internship.discovery.deep_agent import _load_judge_prompt

    def no_remote(**kwargs):
        raise AssertionError("Unexpected hosted prompt access")

    monkeypatch.setenv("PROMPT_SOURCE", "local")
    monkeypatch.setenv("PROMPT_VERSIONS", "{}")
    monkeypatch.setattr(braintrust, "load_prompt", no_remote)
    for profile in ("customers", "integrations"):
        assert await _load_judge_prompt(profile)


def test_dataset_export_preserves_labels_and_quotes_but_removes_administration():
    from ai_internship.public_assets import public_dataset_row

    quote = 'Dr. Example said: "We use Acme."'
    row = {
        "id": "old-row",
        "project_id": "private-project",
        "comments": ["private review"],
        "input": {"name": "Acme", "url": "https://example.com"},
        "expected": {
            "customers": [
                {"name": "Mercy", "supporting_evidence": [{"snippet": quote, "url": None}]}
            ]
        },
        "metadata": {
            "product_name": "Acme",
            "last_reviewed_by": "Internal Reviewer",
            "calibration_note": "private",
            "rejected_ai_values": ["private"],
        },
    }
    result = public_dataset_row(row)
    assert result["expected"] == row["expected"]
    assert result["input"]["name"] == "Acme"
    assert result["input"]["url"] == "https://example.com"
    assert result["metadata"] == {"product_name": "Acme"}
    assert set(result) == {"input", "expected", "metadata"}


def test_dataset_export_replaces_internal_identity_consistently():
    from ai_internship.public_assets import public_dataset_row

    row = {
        "input": {
            "name": "ExampleProduct",
            "url": "https://product.example",
            "product_id": "private-id",
            "internal_note": "private",
        },
        "metadata": {
            "product_id": "private-id",
            "source": "private-environment",
            "product_name": "ExampleProduct",
        },
        "expected": {"customers": []},
    }
    exported = public_dataset_row(row)
    assert "private" not in json.dumps(exported)
    assert exported["input"]["product_id"]
    assert (
        exported["input"]["product_id"]
        == public_dataset_row({**row, "input": {**row["input"], "product_id": "another-id"}})[
            "input"
        ]["product_id"]
    )
    assert public_dataset_row(exported) == exported
    assert row["input"]["product_id"] == "private-id"


@pytest.mark.asyncio
async def test_referent_routes_render_offline_and_versions_affect_reuse(monkeypatch):
    import braintrust

    from ai_internship.enrichment.profiles import extractor_version
    from ai_internship.enrichment.referent_resolution import (
        FIRST_PARTY_ENTRYPOINT,
        _load_referent_prompt,
    )

    def no_remote(**kwargs):
        raise AssertionError("Unexpected hosted prompt access")

    monkeypatch.setenv("PROMPT_SOURCE", "local")
    monkeypatch.setenv("PROMPT_VERSIONS", "{}")
    monkeypatch.setattr(braintrust, "load_prompt", no_remote)
    for relation in ("Adopts", "Integrates"):
        versions = []
        for origin in (None, FIRST_PARTY_ENTRYPOINT):
            prompt = await _load_referent_prompt(relation, origin)
            text = str(
                prompt.build(
                    subject="ExampleClinic",
                    object="ExampleProduct",
                    page_content="Example source text",
                    source_url="https://example.com",
                )
            )
            assert "Example source text" in text
            versions.append(prompt.version)
        assert versions[0] != versions[1]

    from ai_internship.enrichment import profiles

    before = await extractor_version("customers", model="offline-test")
    original = profiles.load_prompt

    def new_version(*, slug, **kwargs):
        prompt = original(slug=slug, **kwargs)
        if "firstparty" in slug:
            from types import SimpleNamespace

            return SimpleNamespace(version="changed-first-party-prompt")
        return prompt

    monkeypatch.setattr(profiles, "load_prompt", new_version)
    assert await extractor_version("customers", model="offline-test") != before


def test_local_prompt_loads_exact_version_without_login(tmp_path, monkeypatch):
    from ai_internship.public_assets import load_prompt

    data = {
        "name": "Demo",
        "slug": "demo",
        "latest": "2",
        "versions": [
            {
                "version": "1",
                "prompt_data": {
                    "prompt": {
                        "type": "chat",
                        "messages": [{"role": "user", "content": "Old {{name}}"}],
                    },
                    "options": {"model": "test"},
                },
            },
            {
                "version": "2",
                "prompt_data": {
                    "prompt": {
                        "type": "chat",
                        "messages": [{"role": "user", "content": "New {{name}}"}],
                    },
                    "options": {"model": "test"},
                },
            },
        ],
    }
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "demo.json").write_text(json.dumps(data))
    monkeypatch.setenv("RESEARCH_ASSETS_DIR", str(tmp_path))
    monkeypatch.setenv("PROMPT_SOURCE", "local")
    monkeypatch.setenv("PROMPT_VERSIONS", '{"demo":"1"}')
    monkeypatch.delenv("BRAINTRUST_API_KEY", raising=False)
    prompt = load_prompt(slug="demo")
    assert prompt.version == "1"
    assert prompt.build(name="Juan") == {
        "model": "test",
        "messages": [{"role": "user", "content": "Old Juan"}],
    }
    assert load_prompt(slug="demo", version="2").version == "2"
    with pytest.raises(ValueError, match="not bundled"):
        load_prompt(slug="demo", version="missing")


def test_hosted_prompt_uses_new_project_and_explicit_version(monkeypatch):
    import braintrust

    from ai_internship.public_assets import load_prompt

    monkeypatch.setenv("PROMPT_SOURCE", "braintrust")
    monkeypatch.setenv("BRAINTRUST_PROJECT", "juans-research")
    monkeypatch.setenv("PROMPT_VERSIONS", "{}")
    monkeypatch.setenv("BRAINTRUST_API_KEY", "test-key")
    calls = []
    monkeypatch.setattr(braintrust, "load_prompt", lambda **kwargs: calls.append(kwargs))
    load_prompt(slug="demo", version="new-account-version")
    assert calls == [
        {
            "project": "juans-research",
            "slug": "demo",
            "version": "new-account-version",
            "api_key": "test-key",
        }
    ]
