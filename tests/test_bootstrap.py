import json

import pytest


def test_bootstrap_recreates_versions_and_labels_in_new_project(tmp_path):
    from scripts.bootstrap_braintrust import bootstrap

    (tmp_path / "prompts").mkdir()
    (tmp_path / "datasets").mkdir()
    (tmp_path / "prompts" / "demo.json").write_text(
        json.dumps(
            {
                "name": "Demo",
                "slug": "demo",
                "latest": "old2",
                "versions": [
                    {
                        "version": "old1",
                        "prompt_data": {"prompt": {"type": "completion", "content": "one"}},
                    },
                    {
                        "version": "old2",
                        "prompt_data": {"prompt": {"type": "completion", "content": "two"}},
                    },
                ],
            }
        )
    )
    row = {"input": {"name": "Acme"}, "expected": {"customers": [{"name": "Mercy"}]}}
    (tmp_path / "datasets" / "customers.json").write_text(
        json.dumps({"name": "Customers", "rows": [row]})
    )
    calls = []

    def api(method, path, body=None, params=None):
        calls.append((method, path, body, params))
        if method == "GET":
            return {"objects": []}
        if path == "project":
            return {"id": "new-project"}
        if path == "prompt":
            return {"id": "new-prompt", "_xact_id": "new1"}
        if path == "prompt/new-prompt":
            return {"id": "new-prompt", "_xact_id": "new2"}
        if path == "dataset":
            return {"id": "new-dataset"}
        return {}

    result = bootstrap(api, "my-project", tmp_path)
    assert result["prompt_versions"]["demo"] == {"old1": "new1", "old2": "new2"}
    inserts = [c for c in calls if c[1] == "dataset/new-dataset/insert"]
    assert inserts[0][2]["events"][0]["expected"] == row["expected"]
    assert all(
        c[2]["project_id"] == "new-project"
        for c in calls
        if c[1] in ("prompt", "dataset") and c[0] == "POST"
    )


def test_bootstrap_refuses_existing_project(tmp_path):
    from scripts.bootstrap_braintrust import bootstrap

    calls = []

    def api(method, path, body=None, params=None):
        calls.append(method)
        return {"objects": [{"id": "existing"}]}

    with pytest.raises(ValueError, match="already exists"):
        bootstrap(api, "existing", tmp_path)
    assert calls == ["GET"]


def test_seed_contains_identity_nodes_not_answer_relationships():
    from scripts.seed_catalog import catalog_nodes

    rows = [
        {
            "input": {"name": "Acme", "product_id": "p1"},
            "expected": {"integrations": [{"name": "Partner"}]},
        }
    ]
    nodes = catalog_nodes(rows)
    assert {(n["name"], n["label"]) for n in nodes} == {("Acme", "Product"), ("Partner", "Product")}
    assert next(n for n in nodes if n["name"] == "Acme")["marketplace_id"] == "p1"
    assert all(set(n) <= {"uuid", "seed_id", "marketplace_id", "name", "label"} for n in nodes)
