"""
ELI-1170 — Step 2 of 2: run different LLM models on the frozen miss cases.

WHAT THIS SCRIPT DOES (plain language):
Step 1 (build_frozen_case_set.py) already found the 86 cases where a golden
customer was missed, but the evidence for it was sitting right there in a
page we'd already scraped. That's a real extraction/grounding-stage miss, not
a fetch problem -- so it's the right test bed for "would a smarter model have
caught this?"

This script takes that frozen case list and, for each candidate model, replays
ONLY the extraction + grounding steps against the exact same page content
(no re-scraping, no touching the live pipeline). For each model we check: did
it find the missing customer as a candidate, and did it survive the grounding
check (the regex backstop + the ELI-1147 semantic check)?

Same model does BOTH jobs in a pass (extraction AND grounding), because
that's how production actually works today -- one INGESTION_LLM_MODEL setting
controls both roles (see research/runtime.py). We're not mixing e.g.
"extract with Terra, ground with Sol" -- each pass is one model doing the
whole job, so results stay apples-to-apples with the current baseline.

MODELS WE'RE COMPARING (agreed 2026-08-05, prices are per 1M tokens,
confirmed live against OpenRouter's actual catalog -- not guessed):
  - gpt-5.4-mini   $0.75 in / $4.50 out   <- current production baseline
  - gpt-5.6-luna   $0.10 in / $0.60 out   <- cheapest of the new generation
  - gpt-5.6-terra  $1.00 in / $6.00 out   <- mid-tier of the new generation
  - gpt-5.4        $2.50 in / $15.00 out  <- full-size, one generation up from mini
  - gpt-5.6-sol    $5.00 in / $30.00 out  <- priciest non-"pro" option

COST: with ~86 cases across maybe ~60 unique pages, this whole comparison
(all 5 models) should land somewhere around $10-15 total. Cheap. The
expensive "-pro" tier models are deliberately excluded.

IMPORTANT -- this script COSTS MONEY and MAKES REAL LLM API CALLS.
Every extraction() call and every grounding check hits OpenRouter for real.
It does NOT write anything to Neo4j and does NOT touch the live pipeline --
it's a read (Neo4j) + LLM calls (OpenRouter) + write-a-results-file only.

HOW TO RUN (after step 1 has produced frozen_extraction_misses.json):
    uv run python scripts/model_comparison_harness.py

OUTPUT:
    model_comparison_results.json -- one row per (model, vendor, customer)
    with whether it was found as a candidate and whether it survived
    grounding. Console output also prints a recovery-rate summary per model.
"""

import asyncio
import json

from langchain_openai import ChatOpenAI
from neo4j import AsyncGraphDatabase

from ai_internship.config import Settings
from ai_internship.discovery.store import DiscoveryStore
from ai_internship.enrichment.grounding import full_rejection_reason
from ai_internship.enrichment.profiles import LLMAssertionExtractor
from ai_internship.graph.local import LocalGraphAdapter
from ai_internship.research.runtime import OPENROUTER_BASE_URL

FROZEN_CASES_PATH = "frozen_extraction_misses.json"
RESULTS_PATH = "model_comparison_results.json"

# The 5 models we're comparing, confirmed real against OpenRouter's live
# catalog (queried 2026-08-05 -- these are NOT guesses). gpt-5.4-mini is
# listed first as the baseline / sanity control: since these 86 cases are
# BY DEFINITION things gpt-5.4-mini missed the first time, running it again
# here should recover close to 0/86 -- if it recovers a lot, that just means
# extraction isn't fully deterministic between runs, not that anything's
# "wrong."
CANDIDATE_MODELS = [
    "openai/gpt-5.4-mini",  # current production baseline / sanity control
    "openai/gpt-5.6-luna",  # cheap
    "openai/gpt-5.6-terra",  # mid
    "openai/gpt-5.4",  # full-size, one tier up from mini
    "openai/gpt-5.6-sol",  # priciest non-pro option
]


def group_cases_by_source(cases: list[dict]) -> dict[str, dict]:
    """Multiple missed customers can come from the SAME page (e.g. one Suki
    press release mentions 5 different hospitals). We don't want to pay for
    5 separate extraction calls on the same page -- so group cases by
    source_id first, extract once per page, then check every customer
    that page is supposed to contain in one go.

    Returns: {source_id: {"vendor": ..., "url": ..., "customers": [...]}}
    """
    grouped: dict[str, dict] = {}
    for case in cases:
        sid = case["source_id"]
        if sid not in grouped:
            grouped[sid] = {"vendor": case["vendor"], "url": case["url"], "customers": []}
        grouped[sid]["customers"].append(case["customer"])
    return grouped


async def run_model_pass(
    model_name: str,
    grouped_sources: dict[str, dict],
    store: DiscoveryStore,
    graph: LocalGraphAdapter,
    settings: Settings,
) -> list[dict]:
    """Run ONE model against every frozen page, and report per-customer
    whether that model found + grounded them. This is where the actual
    money gets spent -- every extractor.extract() and every
    full_rejection_reason() call below is a real LLM call."""

    # Build this model exactly the way the real pipeline does (same base
    # URL, same wrapper) -- just swapping which model string we pass in.
    model = ChatOpenAI(
        base_url=OPENROUTER_BASE_URL, api_key=settings.openrouter_api_key, model=model_name
    )
    extractor = LLMAssertionExtractor(model)

    results = []
    for source_id, info in grouped_sources.items():
        # Pull the exact same page content the original run used -- this is
        # what makes the comparison fair (frozen input, only the model changes).
        source = await store.load_source(source_id)
        if source is None:
            # Shouldn't normally happen (this source_id came straight out of
            # Neo4j moments ago in step 1) but just in case the DB changed.
            for customer in info["customers"]:
                results.append(
                    {
                        "model": model_name,
                        "vendor": info["vendor"],
                        "customer": customer,
                        "source_id": source_id,
                        "found_as_candidate": False,
                        "grounded": False,
                        "note": "source not found in Neo4j",
                    }
                )
            continue

        # "Seeded entities" are known company/vendor names the graph already
        # recognizes -- feeding them in helps the extractor recognize names
        # it's seen before. Same step the real pipeline does.
        seeded = await graph.find_seed_candidates({"HealthSystem", "Product"}, source.content)

        # THE ACTUAL EXTRACTION CALL -- this model reads the page and
        # proposes candidate facts (assertions) about who uses this product.
        # target_name matches production (graph.py passes work.target_name) --
        # without it the customer-extraction prompt's {{target_product_name}}
        # slot is empty, so this harness would silently test a different,
        # easier extraction path than what actually ships (PR #36 review).
        assertions = await extractor.extract(
            source, ["customers"], seeded, target_name=info["vendor"]
        )

        # Grounding is checked per-assertion, and it's an async LLM call
        # (the ELI-1147 semantic check) -- so we cache the result per
        # assertion object rather than re-checking the same assertion twice
        # if two different customer names happen to match the same one.
        grounding_cache: dict[int, str | None] = {}

        async def grounded_ok(a) -> bool:
            key = id(a)
            if key not in grounding_cache:
                # THE ACTUAL GROUNDING CALL -- checks both the regex
                # backstop and the ELI-1147 semantic check (hypothetical
                # language / wrong subject type / etc) using this same model.
                grounding_cache[key] = await full_rejection_reason(
                    a,
                    source.content,
                    profile="customers",
                    target_name=info["vendor"],
                    chat_model=model,
                )
            # None means "no rejection reason" == the assertion is accepted.
            return grounding_cache[key] is None

        # Now check each customer we expected from this page: did the model
        # propose an assertion that names them, and if so, did it survive
        # grounding?
        for customer in info["customers"]:
            needle = customer.lower()
            matches = [
                a
                for a in assertions
                if needle in (a.subject or "").lower() or needle in (a.object or "").lower()
            ]
            if not matches:
                results.append(
                    {
                        "model": model_name,
                        "vendor": info["vendor"],
                        "customer": customer,
                        "source_id": source_id,
                        "found_as_candidate": False,
                        "grounded": False,
                        "note": "not in extracted assertions",
                    }
                )
                continue

            # A customer might show up in more than one assertion (e.g. once
            # as subject, once buried in another sentence) -- if ANY of them
            # grounds successfully, count the customer as recovered.
            grounded_any = False
            for a in matches:
                if await grounded_ok(a):
                    grounded_any = True
                    break

            results.append(
                {
                    "model": model_name,
                    "vendor": info["vendor"],
                    "customer": customer,
                    "source_id": source_id,
                    "found_as_candidate": True,
                    "grounded": grounded_any,
                    "note": "" if grounded_any else "found but grounding rejected all matches",
                }
            )

    return results


async def main():
    # Load the frozen case list step 1 built -- this is our fixed test set,
    # same for every model.
    with open(FROZEN_CASES_PATH) as f:
        frozen_cases = json.load(f)
    grouped_sources = group_cases_by_source(frozen_cases)

    settings = Settings()
    driver = AsyncGraphDatabase.driver(
        settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
    )
    store = DiscoveryStore(driver)
    graph = LocalGraphAdapter(driver)

    all_results = []
    try:
        # Run every model, one at a time, against the exact same frozen pages.
        for model_name in CANDIDATE_MODELS:
            print(
                f"\n=== Running {model_name} against {len(grouped_sources)} pages "
                f"({len(frozen_cases)} frozen cases) ==="
            )
            results = await run_model_pass(model_name, grouped_sources, store, graph, settings)
            all_results.extend(results)

            recovered = sum(1 for r in results if r["found_as_candidate"] and r["grounded"])
            # Guard against an empty frozen case set (a real, valid outcome
            # once the pipeline recovers everything, or a genuinely empty
            # input file) -- dividing by zero here would crash main() after
            # the paid model calls already ran, discarding that work
            # (PR #36 review, Macroscope).
            pct = f"{recovered / len(frozen_cases) * 100:.1f}%" if frozen_cases else "n/a"
            print(f"{model_name}: recovered {recovered}/{len(frozen_cases)} ({pct})")
    finally:
        await driver.close()

    # Save everything so we can slice/dice later without re-running (re-running
    # means spending money again).
    with open(RESULTS_PATH, "w") as f:
        json.dump(all_results, f, indent=2)

    print(f"\nFull results written to {RESULTS_PATH}")
    print("\n=== SUMMARY BY MODEL ===")
    for model_name in CANDIDATE_MODELS:
        model_results = [r for r in all_results if r["model"] == model_name]
        recovered = sum(1 for r in model_results if r["found_as_candidate"] and r["grounded"])
        found_not_grounded = sum(
            1 for r in model_results if r["found_as_candidate"] and not r["grounded"]
        )
        total = len(model_results)
        print(
            f"{model_name}: {recovered}/{total} recovered, "
            f"{found_not_grounded}/{total} found-but-rejected-by-grounding"
        )


if __name__ == "__main__":
    asyncio.run(main())
