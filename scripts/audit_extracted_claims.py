"""Audit what the pipeline actually claimed, in its last run.

Reads every relationship extraction wrote to the graph -- subject, object, the
evidence quote, the page it came from -- and checks each one against the golden
datasets. Reports facts only: whether the claim is in golden, whether the quote
names the organisation, whether the quote names the vendor, whether the quote is
reused across several claims, and whose domain the page is on.

No categories, no blame, no conclusion. Those are for whoever reads it.

    uv run python scripts/audit_extracted_claims.py --since 2026-10-09T00:00:00Z \
        --out data/runs/claims.md

Reads your local graph and bundled illustrative labels. No model calls.
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import difflib
import re
from pathlib import Path
from urllib.parse import urlsplit

from neo4j import AsyncGraphDatabase

from ai_internship.config import Settings
from ai_internship.evaluation.scorers import _normalize
from ai_internship.public_assets import init_dataset

DATASETS = {
    "customers": "Marketplace Customers Dataset",
    "integrations": "Marketplace Integrations Dataset",
}
# Same cutoff the name-variant check uses, so "Spire Orthopaedic" and "Spire
# Orthopedic" count as one organisation rather than appearing here as a claim
# golden does not have.
SAME_ORG_RATIO = 0.82

# `--since` is required, not optional: the graph accumulates across runs, and
# without a cut-off this picks up relationships months old alongside the run
# being audited (81 of 655 on the first pass). An audit of "the last run" has
# to say which run.
#
# The window is the extraction time, `created_at`, not `retrieved_at`.
# retrieved_at on the relationship is the *page's* retrieval time, copied
# through unchanged -- so extracting today from a page cached last month
# produces a relationship stamped last month, and an audit of today's run would
# not see it. Measured against the live graph for one cut-off: 651 relationships
# by created_at against 459 by retrieved_at, the 192 difference being exactly
# that case. This matters more as the cache ages, since every future run reads
# pages fetched long before it.
#
# created_at is a Neo4j datetime, and `--since` is parsed too: it takes any ISO
# offset, and the same instant written +00:00 and -05:00 does not sort the same
# way as a string.
CLAIMS_CYPHER = """
MATCH (subject)-[edge:RELATES_TO]->(object)
WHERE edge.source_id IS NOT NULL AND edge.profile IS NOT NULL
  AND edge.created_at IS NOT NULL AND edge.created_at >= datetime($since)
RETURN subject.name AS subject, object.name AS object, edge.name AS relation,
       edge.profile AS profile, edge.fact AS quote, edge.source_url AS url,
       edge.extraction_id AS extraction_id, toString(edge.created_at) AS extracted_at
"""


def host_of(url: str) -> str:
    host = urlsplit(url or "").netloc.lower()
    return host[4:] if host.startswith("www.") else host


def names_in(text: str, name: str) -> bool:
    """Whole-word check that `text` contains `name`, ignoring case and punctuation.

    Deliberately literal: the point of the report is to say whether the evidence
    spells the organisation out, not to decide whether a reader would infer it.
    """
    if not text or not name:
        return False
    normalized = re.sub(r"[^a-z0-9]+", " ", text.lower())
    target = re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()
    return bool(target) and re.search(rf"(?<!\w){re.escape(target)}(?!\w)", normalized) is not None


async def load_claims(settings: Settings, since: str) -> list[dict]:
    driver = AsyncGraphDatabase.driver(
        settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
    )
    try:
        records, _, _ = await driver.execute_query(CLAIMS_CYPHER, since=since)
        return [dict(r) for r in records]
    finally:
        await driver.close()


def load_golden() -> tuple[dict[tuple[str, str], set[str]], dict[str, str]]:
    golden: dict[tuple[str, str], set[str]] = collections.defaultdict(set)
    vendor_url: dict[str, str] = {}
    for profile, dataset in DATASETS.items():
        for row in init_dataset(name=dataset):
            inp = row.get("input") or {}
            vendor = (inp.get("name") or "").strip()
            if not vendor:
                continue
            vendor_url[vendor] = inp.get("url") or ""
            for entry in (row.get("expected") or {}).get(profile) or []:
                name = _normalize(entry.get("name") or "")
                if name:
                    golden[(vendor, profile)].add(name)
    return golden, vendor_url


def describe(claims: list[dict], golden, vendor_url) -> list[dict]:
    """Attach only checkable facts to each claim."""
    vendors = {_normalize(v): v for v in vendor_url}
    rows = []
    # One row per (vendor, organisation, profile). The same customer found on
    # two pages is two relationships in the graph but one claim, and counting
    # relationships overstates the total -- 412 against 323 for one run. Extra
    # pages for a claim already kept are recorded as `also_on`.
    by_claim: dict[tuple[str, str, str], dict] = {}
    for claim in claims:
        profile = claim["profile"]
        # customers extract HealthSystem -> Adopts -> Product, integrations
        # Product -> Integrates -> Product: the vendor sits on opposite ends.
        vendor_side = claim["object"] if profile == "customers" else claim["subject"]
        other_side = claim["subject"] if profile == "customers" else claim["object"]
        vendor = vendors.get(_normalize(vendor_side or ""))
        entries = list(golden.get((vendor, profile), set())) if vendor else []
        normalized = _normalize(other_side or "")
        in_golden = normalized in entries or bool(
            difflib.get_close_matches(normalized, entries, n=1, cutoff=SAME_ORG_RATIO)
        )
        quote = (claim.get("quote") or "").strip()
        claim_host = host_of(claim.get("url") or "")
        vendor_host = host_of(vendor_url.get(vendor, "")) if vendor else ""
        key = (vendor or f"(unmatched: {vendor_side})", normalized, profile)
        if key in by_claim:
            by_claim[key]["also_on"].append(claim.get("url") or "")
            continue
        by_claim[key] = {
            "vendor": vendor or f"(unmatched: {vendor_side})",
            "profile": profile,
            "claimed": other_side,
            "url": claim.get("url") or "",
            "quote": quote,
            "in_golden": in_golden,
            "quote_names_claimed": names_in(quote, other_side or ""),
            "quote_names_vendor": names_in(quote, vendor_side or ""),
            "quote_shared_with": 0,
            "extracted_at": claim.get("extracted_at"),
            "also_on": [],
            # Both hosts must be present: golden allows a vendor with no URL and
            # a claim can carry none, and "" == "" would read as vendor-hosted.
            "on_vendor_domain": bool(claim_host) and claim_host == vendor_host,
        }
        rows.append(by_claim[key])

    # Counted over the kept rows, not the raw relationships, and skipping rows
    # with no quote at all. Counting relationships made a claim found on two
    # pages share a quote with itself, and an empty quote is not evidence
    # reused -- it is evidence absent, which the row already says.
    quote_users: collections.Counter[str] = collections.Counter(
        row["quote"] for row in rows if row["quote"]
    )
    for row in rows:
        row["quote_shared_with"] = quote_users[row["quote"]] - 1 if row["quote"] else 0
    return rows


def render(rows: list[dict]) -> str:
    out = ["# What the pipeline claimed, and what is checkably true about each claim", ""]
    stamps = [r["extracted_at"] for r in rows if r.get("extracted_at")]
    window = f" ({min(stamps)[:16]} to {max(stamps)[:16]})" if stamps else ""
    out.append(f"{len(rows)} claims{window}.\n")

    for profile in ("customers", "integrations"):
        subset = [r for r in rows if r["profile"] == profile]
        if not subset:
            continue
        in_golden = [r for r in subset if r["in_golden"]]
        out.append(f"## {profile} — {len(subset)} claims\n")
        out.append("| | count |")
        out.append("|---|---|")
        counts = [
            ("in golden", len(in_golden)),
            ("not in golden", len(subset) - len(in_golden)),
            (
                "quote spells out the claimed organisation",
                sum(r["quote_names_claimed"] for r in subset),
            ),
            ("quote spells out the vendor", sum(r["quote_names_vendor"] for r in subset)),
            ("quote reused for another claim", sum(1 for r in subset if r["quote_shared_with"])),
            ("page is on the vendor's own domain", sum(r["on_vendor_domain"] for r in subset)),
        ]
        out += [f"| {label} | {value} |" for label, value in counts]
        out.append("")

        missing = [r for r in subset if not r["in_golden"]]
        out.append(f"### The {len(missing)} not in golden\n")
        out.append("Each row is the claim, then the evidence, verbatim.\n")
        for row in sorted(missing, key=lambda r: (r["vendor"], str(r["claimed"]))):
            flags = []
            if not row["quote_names_claimed"]:
                flags.append("quote does not name it")
            if not row["quote_names_vendor"]:
                flags.append("quote does not name the vendor")
            if row["quote_shared_with"]:
                flags.append(f"same quote used for {row['quote_shared_with']} other claim(s)")
            if row["on_vendor_domain"]:
                flags.append("vendor's own site")
            suffix = f" — _{'; '.join(flags)}_" if flags else ""
            out.append(f"- **{row['vendor']} → {row['claimed']}**{suffix}")
            out.append(f"  {row['url']}")
            quote = " ".join(row["quote"].split())[:300] if row["quote"] else "(no quote stored)"
            out.append(f"  > {quote}\n")
    return "\n".join(out) + "\n"


async def main_async(args) -> int:
    settings = Settings()
    claims = await load_claims(settings, args.since)
    if not claims:
        raise SystemExit(f"no extraction relationships at or after {args.since}")
    golden, vendor_url = load_golden()
    report = render(describe(claims, golden, vendor_url))
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(report)
        print(f"wrote {args.out} ({len(claims)} claims)")
    else:
        print(report)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", help="write markdown here instead of stdout")
    parser.add_argument(
        "--since",
        required=True,
        help="ISO timestamp; only relationships extracted at or after this are audited",
    )
    return asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
