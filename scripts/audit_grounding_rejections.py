"""Read-only audit of real `grounding_failure` rejections (ELI-1143, step 1-2):
bucket every rejected assertion from the current Neo4j corpus by which
`grounding_rejection_reason` check rejected it (`enrichment/grounding.py`),
then emit a deterministic sample per bucket for hand-judging whether the
rejection was actually correct. Reuses whatever `ExtractionRun` data already
exists from prior pipeline runs (same corpus ELI-1136 read) -- does not call
an LLM or write anything.

Two gotchas inherited from `vendor_extraction_yield.py` (ELI-1136), both
still live here:

  * `ExtractionRun.target_id` must be checked against the joining
    `ResearchTarget`, not inferred from the shared `RetainedSource` node --
    `RetainedSource` is content-addressed and can be reached from more than
    one target (confirmed live for a legacy duplicate Flatiron target: see
    ELI-1136's PR review corrections).
  * Only the latest `revision` per (source, profile, extractor_version)
    counts -- an earlier interrupted or superseded revision's rejections are
    not "real" rejections against the current pipeline.

Restricted to `origin = 'first_party'`, same as ELI-1136 -- the corpus rerun
this audit reads (ELI-1139's `--skip-external` flag) only re-extracted
first-party sources with the ELI-1130 fix in place; external-origin
`ExtractionRun` data is still from before that fix and would reintroduce the
Medica/Powers Health seed-substring noise ELI-1130 fixed. `origin` is still
carried through as a field on every rejection (it's always "first_party"
today) so a future run covering external sources again doesn't need a query
change.

Writes rejections_by_code.json (every rejection, bucketed, full detail) and
a hand_judging_sample.md (a deterministic sample per bucket, with the same
subject/heading evidence-context string `grounding_rejection_reason` itself
inspects, so a judge sees what the check saw) under --output-dir.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
from collections import Counter, defaultdict
from pathlib import Path

from neo4j import AsyncGraphDatabase

from ai_internship.config import Settings
from ai_internship.enrichment.grounding import _evidence_context
from ai_internship.public_assets import research_targets

VENDORS = list(research_targets())

# Deterministic, not cryptographic -- exists only so re-running this script
# against an unchanged graph reproduces the exact same hand-judging sample,
# rather than a fresh random draw every time.
SAMPLE_SEED = 1143
SAMPLE_SIZE_PER_BUCKET = 8

_REJECTIONS_QUERY = """
MATCH (t:ResearchTarget {name: $vendor})-[:TRACKS]->(f:FrontierUrl)
WHERE f.origin = 'first_party'
MATCH (s:RetainedSource {source_id: f.latest_source_id})
MATCH (run:ExtractionRun)-[:EXTRACTED]->(s)
WHERE run.outcome = 'grounding_failure' AND run.target_id = t.target_id
WITH s, f, run
ORDER BY s.source_id, run.profile, run.extractor_version, run.revision DESC
WITH s, f.origin AS origin, run.profile AS profile,
     run.extractor_version AS extractor_version, collect(run)[0] AS latest_run
RETURN s.source_id AS source_id,
       coalesce(s.final_url, s.requested_url) AS url,
       s.content AS content,
       origin, profile, extractor_version,
       latest_run.extraction_id AS extraction_id,
       latest_run.revision AS revision,
       latest_run.grounding_rejections AS grounding_rejections_json
"""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("docs/experiments/eli-1143-grounding-audit"),
    )
    return parser.parse_args(argv)


def _rejections_for_row(row: dict, vendor: str) -> list[dict]:
    rejections = []
    for item in json.loads(row["grounding_rejections_json"] or "[]"):
        assertion = item.get("assertion", {})
        quote = assertion.get("quote", "")
        rejections.append(
            {
                "code": item["code"],
                "vendor": vendor,
                "origin": row["origin"],
                "profile": row["profile"],
                "extractor_version": row["extractor_version"],
                "source_id": row["source_id"],
                "url": row["url"],
                "subject_type": assertion.get("subject_type"),
                "subject": assertion.get("subject"),
                "relation": assertion.get("relation"),
                "object_type": assertion.get("object_type"),
                "object": assertion.get("object"),
                "quote": quote,
                # Same heading + quote string `grounding_rejection_reason`
                # itself checks endpoint-name presence against -- shows a
                # judge exactly what evidence the check saw, not just the
                # quote in isolation.
                "evidence_context": _evidence_context(quote, row["content"]) if quote else "",
            }
        )
    return rejections


async def fetch_rejections(driver) -> list[dict]:
    all_rejections: list[dict] = []
    for vendor in VENDORS:
        rows, _, _ = await driver.execute_query(_REJECTIONS_QUERY, vendor=vendor)
        for row in rows:
            all_rejections.extend(_rejections_for_row(dict(row), vendor))
    return all_rejections


def bucket_by_code(rejections: list[dict]) -> dict[str, list[dict]]:
    buckets: dict[str, list[dict]] = defaultdict(list)
    for rejection in rejections:
        buckets[rejection["code"]].append(rejection)
    return dict(buckets)


def sample_for_hand_judging(buckets: dict[str, list[dict]]) -> dict[str, list[dict]]:
    rng = random.Random(SAMPLE_SEED)
    samples = {}
    for code, items in buckets.items():
        # Sort first for a deterministic population order before sampling --
        # Cypher/dict iteration order isn't guaranteed stable across runs.
        ordered = sorted(items, key=lambda r: (r["vendor"], r["source_id"], r["quote"]))
        samples[code] = rng.sample(ordered, k=min(SAMPLE_SIZE_PER_BUCKET, len(ordered)))
    return samples


def render_summary(buckets: dict[str, list[dict]]) -> str:
    total = sum(len(items) for items in buckets.values())
    lines = [f"Total grounding_failure rejections: {total}", ""]
    by_vendor: dict[str, Counter[str]] = defaultdict(Counter)
    for code, items in buckets.items():
        for item in items:
            by_vendor[item["vendor"]][code] += 1

    lines.append(f"{'code':<28} {'count':>6} {'%':>6}")
    for code, items in sorted(buckets.items(), key=lambda kv: -len(kv[1])):
        n = len(items)
        lines.append(f"{code:<28} {n:>6} {100 * n / total:>5.1f}%")

    lines.append("")
    for vendor in VENDORS:
        vendor_total = sum(by_vendor[vendor].values())
        if not vendor_total:
            continue
        lines.append(f"-- {vendor} ({vendor_total} rejections) --")
        for code, n in by_vendor[vendor].most_common():
            lines.append(f"  {code:<26} {n:>6} {100 * n / vendor_total:>5.1f}%")
    return "\n".join(lines)


def render_hand_judging_md(samples: dict[str, list[dict]], buckets: dict[str, list[dict]]) -> str:
    lines = ["# ELI-1143 grounding-rejection hand-judging sample", ""]
    lines.append(
        f"Deterministic sample (seed={SAMPLE_SEED}, up to "
        f"{SAMPLE_SIZE_PER_BUCKET}/bucket) of real `grounding_failure` "
        "rejections, for judging whether each rejection was correct. "
        "For each: was rejecting this assertion the right call?"
    )
    lines.append("")
    for code, items in sorted(samples.items(), key=lambda kv: -len(buckets[kv[0]])):
        lines.append(f"## `{code}` ({len(buckets[code])} total, {len(items)} sampled)")
        lines.append("")
        for item in items:
            lines.append(
                f"- **{item['vendor']}** / {item['profile']} / {item['origin']} -- {item['url']}"
            )
            lines.append(
                f"  - assertion: `{item['subject_type']}:{item['subject']}` "
                f"-{item['relation']}-> `{item['object_type']}:{item['object']}`"
            )
            lines.append(f"  - quote: {item['quote']!r}")
            if item["evidence_context"] and item["evidence_context"] != item["quote"].lower():
                lines.append(f"  - evidence context checked: {item['evidence_context']!r}")
            lines.append("  - verdict: ")
            lines.append("")
    return "\n".join(lines)


async def main_async() -> tuple[list[dict], dict[str, list[dict]], dict[str, list[dict]]]:
    settings = Settings()
    driver = AsyncGraphDatabase.driver(
        settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
    )
    try:
        rejections = await fetch_rejections(driver)
    finally:
        await driver.close()
    buckets = bucket_by_code(rejections)
    samples = sample_for_hand_judging(buckets)
    return rejections, buckets, samples


def main() -> int:
    args = parse_args()
    rejections, buckets, samples = asyncio.run(main_async())

    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "rejections_by_code.json").write_text(
        json.dumps(buckets, indent=2, sort_keys=True) + "\n"
    )
    (args.output_dir / "hand_judging_sample.md").write_text(
        render_hand_judging_md(samples, buckets) + "\n"
    )
    summary = render_summary(buckets)
    print(summary)
    (args.output_dir / "summary.txt").write_text(summary + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
