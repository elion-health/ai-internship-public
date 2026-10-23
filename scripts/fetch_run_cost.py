"""Sum real OpenRouter $ cost for a stateful-research run, by thread_id.

Per-LLM-call cost already flows into Braintrust (CostTrackingBraintrustCallbackHandler
in research/runtime.py attaches metrics.cost to every LLM span) but nothing rolls it
up into a single number. This queries Braintrust via BTQL, filtering server-side on
metadata.metadata.thread_id (every span in a run's trace carries the LangChain
config's metadata under a nested
"metadata" key -- Braintrust's langchain integration wraps it, so the field the SDK
sets as config={"metadata": {"thread_id": ...}} ends up double-nested on the logged
event) -- pulling and filtering the whole project log client-side is not viable once
a project has any real history (confirmed: it hangs for minutes).
"""

from __future__ import annotations

import argparse

import requests

from ai_internship.config import Settings
from ai_internship.public_assets import project_name

BRAINTRUST_PROJECT = project_name()


def parse_args(argv: list[str] | None = None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--thread-id", required=True, action="append")
    return parser.parse_args(argv)


def fetch_project_id(api_key: str, project_name: str) -> str:
    resp = requests.get(
        "https://api.braintrust.dev/v1/project",
        headers={"Authorization": f"Bearer {api_key}"},
        params={"project_name": project_name},
    )
    resp.raise_for_status()
    objects = resp.json().get("objects", [])
    if not objects:
        raise ValueError(f"project '{project_name}' not found")
    return objects[0]["id"]


def fetch_events_for_thread(api_key: str, project_id: str, thread_id: str) -> list[dict]:
    headers = {"Authorization": f"Bearer {api_key}"}
    events = []
    cursor = None
    while True:
        query = {
            "select": [{"op": "star"}],
            "from": {
                "op": "function",
                "name": {"op": "ident", "name": ["project_logs"]},
                "args": [{"op": "literal", "value": project_id}],
            },
            "filter": {
                "op": "eq",
                "left": {"op": "ident", "name": ["metadata", "metadata", "thread_id"]},
                "right": {"op": "literal", "value": thread_id},
            },
            "limit": 200,
        }
        if cursor:
            query["cursor"] = cursor
        resp = requests.post(
            "https://api.braintrust.dev/btql",
            headers=headers,
            json={"query": query},
        )
        resp.raise_for_status()
        data = resp.json()
        events.extend(data.get("data", []))
        cursor = data.get("cursor")
        if not cursor:
            break
    return events


def cost_for_events(events: list[dict]) -> tuple[float, int]:
    total = 0.0
    span_count = 0
    for event in events:
        cost = (event.get("metrics") or {}).get("cost")
        if isinstance(cost, int | float):
            total += float(cost)
            span_count += 1
    return total, span_count


def main() -> int:
    args = parse_args()
    settings = Settings()
    project_id = fetch_project_id(settings.braintrust_api_key, BRAINTRUST_PROJECT)
    for thread_id in args.thread_id:
        events = fetch_events_for_thread(settings.braintrust_api_key, project_id, thread_id)
        total, span_count = cost_for_events(events)
        print(
            f"thread_id={thread_id} cost_usd={total:.4f} "
            f"priced_spans={span_count} total_spans={len(events)}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
