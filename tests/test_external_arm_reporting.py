import importlib
import json
from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_rediscovered_lead_counts_but_seed_only_name_does_not(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / "scripts"))
    module = importlib.import_module("eval_external_arms")
    vendor = module.Golden(name="ExampleVendor", url="https://vendor.example")
    vendor.all_names["customers"] = {"examplehospital"}
    vendor.external_names["customers"] = {"examplehospital"}

    class Graph:
        async def ainvoke(self, state):
            return {
                "trace_output": {
                    "extractions": [
                        *state["extraction_results"],
                        {
                            "extraction_id": "real:1",
                            "profile": "customers",
                            "nodes": [{"label": "HealthSystem", "name": "Example Hospital"}],
                        },
                    ]
                }
            }

    result = await module.run_vendor(
        Graph(),
        vendor,
        ["customers"],
        {"ExampleVendor": {"customers": ["Example Hospital", "Seed Only Clinic"]}},
    )
    assert result["profiles"]["customers"]["names"] == ["examplehospital"]
    assert result["profiles"]["customers"]["all_golden"]["hits"] == 1


def test_summary_keeps_every_run_in_a_file(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / "scripts"))
    module = importlib.import_module("summarize_judge_ab")
    runs = []
    for hits in (1, 2):
        profiles = {
            p: {s: {"golden": 2, "found": 2, "hits": hits} for s in ("external_only", "all_golden")}
            for p in ("customers", "integrations")
        }
        runs.append({"per_vendor": [{"profiles": profiles}]})
    path = tmp_path / "judge-off.json"
    path.write_text(json.dumps({"runs": runs}))
    summaries = module.totals_for(path)
    assert len(summaries) == 2
    assert [s[("customers", "external_only")]["hits"] for s in summaries] == [1, 2]
