import json
from pathlib import Path


def test_langgraph_exposes_only_stateful_research():
    config = json.loads((Path(__file__).parents[1] / "langgraph.json").read_text())

    assert config["graphs"] == {
        "stateful_research": "./src/ai_internship/studio.py:stateful_research_graph"
    }
