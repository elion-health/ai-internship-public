"""LangGraph Studio exports for the local discovery and ingestion workflows."""

from ai_internship.config import Settings
from ai_internship.research.runtime import build_stateful_research_graph, setup_braintrust_tracing


def make_stateful_research_graph():
    settings = Settings()
    handler = setup_braintrust_tracing(settings)
    graph, _, _ = build_stateful_research_graph(settings)
    return graph.with_config({"callbacks": [handler] if handler is not None else []})


stateful_research_graph = make_stateful_research_graph()
