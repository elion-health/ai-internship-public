"""Run target-centric, stateful local research."""

from __future__ import annotations

import argparse
import asyncio
import time
from uuid import uuid4

from loguru import logger

from ai_internship.config import Settings
from ai_internship.research.contracts import ResearchRequest
from ai_internship.research.runtime import (
    build_stateful_research_graph,
    flush_braintrust,
    setup_braintrust_tracing,
)


def parse_args(argv: list[str] | None = None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True)
    parser.add_argument("--url", required=True)
    parser.add_argument("--profile", action="append", required=True)
    parser.add_argument("--mode", choices=("initial", "resume"), default="initial")
    parser.add_argument("--force-profile", action="append", default=[])
    parser.add_argument("--skip-external", action="store_true")
    parser.add_argument("--first-party-only", action="store_true")
    parser.add_argument("--thread-id")
    return parser.parse_args(argv)


async def main_async(args) -> int:
    settings = Settings()
    handler = setup_braintrust_tracing(settings)
    graph, driver, graphiti_client = build_stateful_research_graph(settings)
    graph = graph.with_config({"callbacks": [handler] if handler is not None else []})
    try:
        request = ResearchRequest(
            name=args.name,
            url=args.url,
            profiles=args.profile,
            mode=args.mode,
            force_profiles=args.force_profile,
            skip_external=args.skip_external,
            first_party_only=args.first_party_only,
        )
        thread_id = args.thread_id or str(uuid4())
        started = time.monotonic()
        result = await graph.ainvoke(
            {"request": request},
            config={
                "configurable": {"thread_id": thread_id},
                "metadata": {"thread_id": thread_id},
            },
        )
        elapsed_seconds = time.monotonic() - started
        logger.info(f"thread_id={thread_id} wall_clock_seconds={elapsed_seconds:.2f}")
        logger.info(f"thread_id={thread_id} metrics={result.get('metrics')}")
    finally:
        await driver.close()
        if graphiti_client is not None:
            await graphiti_client.close()
        await flush_braintrust()
    return 0


def main() -> int:
    return asyncio.run(main_async(parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
