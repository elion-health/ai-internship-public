"""External-discovery A/B harness over the bundled illustrative products.

Why this exists: `eval_stateful_research.py` runs the whole pipeline and scores
against every golden entry, so the vendor's own site dominates the number and a
change to external discovery barely moves it. This harness runs *only* external
discovery plus the extraction of its own pages, and scores against the entries
external discovery is actually responsible for -- the ones no first-party page
can prove.

First-party is stubbed out rather than skipped by a request flag. An optional
--prior-run audit can supply leads from your own earlier pipeline run. Without
it both arms run without leads; the private historical corpus is not bundled.

    PYTHONPATH=src uv run python scripts/eval_external_arms.py \
        --arm judge-off --runs 2 --out judge-off.json

Arms differ in exactly one thing: whether the evidence judge runs at the keep
decision (ELI-1490). Budgets, models and the leads are held constant.
"""

# ruff: noqa: E402
from dotenv import load_dotenv

load_dotenv()

import argparse
import asyncio
import json
import os
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from loguru import logger
from reset_target_state import delete_scope, gather_scope

from ai_internship.config import Settings
from ai_internship.evaluation.scorers import _normalize
from ai_internship.public_assets import init_dataset
from ai_internship.research.acquisition import AcquisitionResult, FirstPartyAcquisition
from ai_internship.research.contracts import ResearchRequest, ResearchTarget
from ai_internship.research.graph import KNOWN_ENTITY_LEAD_CAP
from ai_internship.research.runtime import (
    build_stateful_research_graph,
    flush_braintrust,
    setup_braintrust_tracing,
)

DATASETS = {
    "customers": ("Marketplace Customers Dataset", "customers"),
    "integrations": ("Marketplace Integrations Dataset", "integrations"),
}
# The label extraction assigns to the non-vendor endpoint, per profile. Only
# that endpoint is a customer/partner name worth scoring or feeding as a lead.
LEAD_LABELS = {"customers": "HealthSystem", "integrations": "Product"}


class NoFirstPartyAcquisition(FirstPartyAcquisition):
    """Real external fetching, no first-party crawl.

    `run_external` is inherited untouched -- it is what actually fetches the
    pages external discovery selected, so stubbing it would leave extraction
    with nothing to read.
    """

    async def map_first_party(self, *, target, run_id: str, mode: str) -> AcquisitionResult:
        return AcquisitionResult()

    async def fetch_first_party(self, *, target, run_id: str) -> AcquisitionResult:
        return AcquisitionResult()


@dataclass
class Golden:
    """One vendor's golden entries, split by what first-party could reach."""

    name: str
    url: str
    product_id: str | None = None
    all_names: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    external_names: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))


def host_of(url: str) -> str:
    host = urlsplit(url or "").netloc.lower()
    return host[4:] if host.startswith("www.") else host


def is_vendor_host(host: str, vendor_host: str) -> bool:
    """Subdomains count as the vendor's own site.

    `support.vendor.com` is reachable by crawling the vendor, so treating it as
    third-party would inflate external discovery's denominator -- this matches
    analyze_golden_evidence_hosts.py's vendor_own and reproduces its 294/64.
    """
    return bool(vendor_host) and (host == vendor_host or host.endswith("." + vendor_host))


def load_golden() -> dict[str, Golden]:
    """Golden entries per vendor, plus the subset external discovery owns.

    "Owns" = every cited proof URL sits off the vendor's own domain. Those
    entries cannot be reached by crawling the vendor's site, so finding one at
    all means external discovery found it -- which is what makes this scorable
    without attributing each extracted name back to its source page.
    """
    vendors: dict[str, Golden] = {}
    for profile, (dataset_name, key) in DATASETS.items():
        for row in init_dataset(name=dataset_name):
            inp = row.get("input") or {}
            name = (inp.get("name") or "").strip()
            if not name:
                continue
            vendor = vendors.setdefault(
                name, Golden(name=name, url=inp.get("url") or "", product_id=inp.get("product_id"))
            )
            vendor_host = host_of(inp.get("url") or "")
            for entry in (row.get("expected") or {}).get(key) or []:
                entry_name = _normalize(entry.get("name") or "")
                if not entry_name:
                    continue
                vendor.all_names[profile].add(entry_name)
                urls = [
                    s.get("url") for s in (entry.get("supporting_evidence") or []) if s.get("url")
                ]
                if urls and not any(is_vendor_host(host_of(u), vendor_host) for u in urls):
                    vendor.external_names[profile].add(entry_name)
    return vendors


def load_hints(path: Path | None = None) -> dict[str, dict[str, list[str]]]:
    """Names a previous full-pipeline run actually extracted, per vendor/profile.

    Taken from what the pipeline produced (true and false positives alike), not
    from the golden answer key -- a hand-picked list would be feeding the agent
    the answers. Ordered as the audit recorded them; the cap is applied by the
    production code, not here.
    """
    if path is None:
        return {}
    data = json.loads(path.read_text())
    hints: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for row in data.get("rows", []):
        if row.get("outcome") not in ("TP", "FP"):
            continue
        vendor, profile = row.get("vendor"), row.get("profile")
        entity = (row.get("entity") or "").strip()
        if not vendor or not entity or profile not in LEAD_LABELS:
            continue
        bucket = hints[vendor][profile]
        if entity not in bucket:
            bucket.append(entity)
    return hints


def seed_state(vendor: Golden, hints: list[str], profile: str) -> list[dict[str, object]]:
    """Fake first-party extraction results carrying the hint names.

    `route_external_profiles` reads leads out of `extraction_results`, so
    injecting them here exercises the real production path -- including
    KNOWN_ENTITY_LEAD_CAP -- rather than a test-only parameter.
    """
    if not hints:
        return []
    return [
        {
            "extraction_id": f"seed:{vendor.name}:{profile}",
            "profile": profile,
            "outcome": "succeeded",
            "nodes": [
                {"id": f"seed:{i}", "label": LEAD_LABELS[profile], "name": name}
                for i, name in enumerate(hints)
            ],
        }
    ]


def extracted_names(result: dict, profile: str, excluded_ids: set[str] | None = None) -> set[str]:
    label = LEAD_LABELS[profile]
    found: set[str] = set()
    for extraction in (result.get("trace_output") or {}).get("extractions") or []:
        if extraction.get("extraction_id") in (excluded_ids or set()):
            continue
        if extraction.get("profile") != profile:
            continue
        for node in extraction.get("nodes") or []:
            if node.get("label") != label:
                continue
            name = _normalize(node.get("name") or "")
            if name:
                found.add(name)
    return found


def score(found: set[str], golden: set[str]) -> dict[str, float | int]:
    hits = found & golden
    return {
        "golden": len(golden),
        "found": len(found),
        "hits": len(hits),
        "recall": len(hits) / len(golden) if golden else 0.0,
        "precision": len(hits) / len(found) if found else 0.0,
    }


async def reset_vendor(driver, vendor: Golden) -> None:
    """Wipe this target's own subgraph before the run.

    Without this the arms are not isolated: `plan_extraction` skips a source it
    has already extracted for the same profile and extractor version, so a
    second arm silently reuses the first arm's extractions and the comparison
    measures nothing. Entity nodes are shared across targets and are left alone.
    """
    target_id = ResearchTarget.build(vendor.name, vendor.url).target_id
    scope = await gather_scope(driver, target_id)
    await delete_scope(driver, target_id, scope)


async def run_vendor(graph, vendor: Golden, profiles: list[str], hints) -> dict:
    seeded: list[dict[str, object]] = []
    for profile in profiles:
        seeded += seed_state(vendor, hints.get(vendor.name, {}).get(profile, []), profile)
    state: dict[str, object] = {
        "request": ResearchRequest(
            name=vendor.name,
            url=vendor.url,
            profiles=profiles,
            marketplace_id=vendor.product_id,
        )
    }
    if seeded:
        state["extraction_results"] = seeded
    started = time.monotonic()
    result = await graph.ainvoke(state)
    out = {
        "vendor": vendor.name,
        "elapsed_seconds": round(time.monotonic() - started, 1),
        "leads_available": [n["name"] for item in seeded for n in item["nodes"]],
        "errors": result.get("errors", []),
        "profiles": {},
    }
    for profile in profiles:
        # Exclude injected records, not names independently rediscovered in new evidence.
        injected_ids = {item["extraction_id"] for item in seeded}
        found = extracted_names(result, profile, injected_ids)
        out["profiles"][profile] = {
            "names": sorted(found),
            "external_only": score(found, vendor.external_names[profile]),
            "all_golden": score(found, vendor.all_names[profile]),
        }
    return out


def totals(vendor_results: list[dict], profiles: list[str]) -> dict:
    summary: dict[str, dict] = {}
    for profile in profiles:
        for scope in ("external_only", "all_golden"):
            golden = sum(r["profiles"][profile][scope]["golden"] for r in vendor_results)
            hits = sum(r["profiles"][profile][scope]["hits"] for r in vendor_results)
            found = sum(r["profiles"][profile][scope]["found"] for r in vendor_results)
            summary.setdefault(profile, {})[scope] = {
                "golden": golden,
                "found": found,
                "hits": hits,
                "recall": round(hits / golden, 4) if golden else 0.0,
                "precision": round(hits / found, 4) if found else 0.0,
            }
    return summary


async def main_async(args) -> int:
    settings = Settings()
    if settings.discovery_evidence_judge != (args.arm == "judge-on"):
        raise SystemExit(
            f"--arm {args.arm} but settings.discovery_evidence_judge="
            f"{settings.discovery_evidence_judge}; set DISCOVERY_EVIDENCE_JUDGE before running"
        )
    setup_braintrust_tracing(settings)
    vendors = load_golden()
    hints = load_hints(args.prior_run)
    selected = sorted(vendors.values(), key=lambda v: v.name)
    if args.only:
        wanted = {n.lower() for n in args.only}
        selected = [v for v in selected if v.name.lower() in wanted]
        missing = wanted - {v.name.lower() for v in selected}
        if missing:
            raise SystemExit(f"no such vendor(s): {sorted(missing)}")
    if args.vendors:
        selected = selected[: args.vendors]
    logger.info(
        "arm={} judge={} vendors={} runs={} concurrency={}",
        args.arm,
        settings.discovery_evidence_judge,
        len(selected),
        args.runs,
        args.concurrency,
    )

    runs = []
    for run_index in range(args.runs):
        graph, driver, graphiti_client = build_stateful_research_graph(
            settings, first_party_class=NoFirstPartyAcquisition
        )
        # Vendors are independent: separate targets, separate DiscoverySessions
        # (the engine builds one per call via session_factory), separate Neo4j
        # writes. One vendor takes about 6 minutes, so running 61 sequentially
        # would put a single run near 7 hours.
        limit = asyncio.Semaphore(args.concurrency)
        done = 0

        async def one(vendor: Golden) -> dict:
            nonlocal done
            async with limit:
                try:
                    await reset_vendor(driver, vendor)
                    out = await run_vendor(graph, vendor, args.profiles, hints)
                except Exception as exc:  # noqa: BLE001 -- one vendor must not kill the run
                    logger.error("{} failed: {}", vendor.name, exc)
                    out = {"vendor": vendor.name, "fatal": str(exc)}
                done += 1
                logger.info("[{}/{}] {}", done, len(selected), vendor.name)
                return out

        try:
            vendor_results = await asyncio.gather(*(one(v) for v in selected))
        finally:
            await driver.close()
            await graphiti_client.close()
            await flush_braintrust()
        ok = [r for r in vendor_results if "fatal" not in r]
        runs.append(
            {
                "run": run_index + 1,
                "vendors_ok": len(ok),
                "vendors_failed": len(vendor_results) - len(ok),
                "totals": totals(ok, args.profiles),
                "per_vendor": list(vendor_results),
            }
        )
        logger.info("run {} totals: {}", run_index + 1, json.dumps(runs[-1]["totals"]))

    payload = {
        "arm": args.arm,
        "profiles": args.profiles,
        "lead_cap": KNOWN_ENTITY_LEAD_CAP,
        "prior_run_provided": args.prior_run is not None,
        "settings": {
            "discovery_model": settings.discovery_llm_model,
            "ingestion_model": settings.ingestion_llm_model,
            "search_cap": settings.discovery_max_search_calls,
            "scrape_cap": settings.discovery_max_scrape_calls,
            "evidence_judge": settings.discovery_evidence_judge,
        },
        "runs": runs,
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(payload, indent=2))
    logger.info("wrote {}", args.out)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", choices=("judge-off", "judge-on"), required=True)

    def positive(value: str) -> int:
        number = int(value)
        if number < 1:
            raise argparse.ArgumentTypeError("must be 1 or more")
        return number

    def non_negative(value: str) -> int:
        number = int(value)
        if number < 0:
            raise argparse.ArgumentTypeError("must be 0 or more")
        return number

    parser.add_argument(
        "--runs",
        type=positive,
        default=2,
        help="repeats of the whole arm. Two minimum: ELI-1488 measured run-to-run "
        "noise of about 7 on 89 targets, so a single run cannot settle anything.",
    )
    parser.add_argument(
        "--vendors", type=non_negative, default=0, help="limit for a smoke test; 0 means all"
    )
    parser.add_argument(
        "--only",
        nargs="+",
        default=[],
        help="run just these vendor names -- for re-running the ones that failed",
    )
    parser.add_argument(
        "--concurrency",
        type=positive,
        default=12,
        help="vendors in flight at once. The ceiling is the slowest upstream rate "
        "limit (Firecrawl, then OpenRouter), not local CPU -- raise it until "
        "failures appear in the per-vendor errors, then back off.",
    )
    parser.add_argument(
        "--profiles",
        nargs="+",
        choices=("customers", "integrations"),
        default=["customers", "integrations"],
    )
    parser.add_argument("--out", required=True)
    parser.add_argument(
        "--prior-run",
        type=Path,
        help="Optional locally generated categorized audit; no historical leads bundled",
    )
    args = parser.parse_args()
    # A bad value here otherwise fails inside every vendor task, leaving a file
    # of zero totals that exits successfully and reads like a real result
    # (PR #82 review).
    if len(set(args.profiles)) != len(args.profiles):
        parser.error("--profiles must not repeat a profile")
    # Settings reads this at construction, so it has to be set before the import
    # of Settings takes effect for this process.
    os.environ["DISCOVERY_EVIDENCE_JUDGE"] = "true" if args.arm == "judge-on" else "false"
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
