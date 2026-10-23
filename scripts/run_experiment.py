"""Run one retained customer-research experiment with your own provider keys."""

import argparse
import importlib
from pathlib import Path

from ai_internship.config import Settings
from ai_internship.research.runtime import setup_braintrust_tracing

AGENTS = {
    "react-exa": ("customers_react_exa", "ReactExaCustomerAgent"),
    "react-firecrawl": ("customers_react_firecrawl", "ReactFirecrawlCustomerAgent"),
    "exa-openrouter": ("customers_react_exa_openrouter", "ExaOpenRouterCustomerAgent"),
    "exa-memory": ("customers_react_exa_memory", "ReactExaMemoryAgent"),
    "exa-memory-synth": ("customers_react_exa_memory_synth", "ReactExaMemorySynthAgent"),
    "exa-multiagent": ("customers_react_exa_multiagent", "ReactExaMultiAgentCustomerAgent"),
    "exa-servertool": ("customers_exa_servertool", "ExaServerToolCustomerAgent"),
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", choices=AGENTS, required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--url", required=True)
    parser.add_argument("--vendor")
    parser.add_argument("--model")
    parser.add_argument("--output", type=Path, default=Path("data/runs/experiment.json"))
    args = parser.parse_args()
    module, name = AGENTS[args.agent]
    agent_type = getattr(
        importlib.import_module(f"ai_internship.agents.customers.experiments.{module}"), name
    )
    settings = Settings()
    setup_braintrust_tracing(settings)
    options = {"settings": settings}
    if args.model:
        options["model"] = args.model
    result = agent_type(**options).analyze(args.url, args.name, args.vendor)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(result.model_dump_json(indent=2) + "\n")
    print(args.output)


if __name__ == "__main__":
    main()
