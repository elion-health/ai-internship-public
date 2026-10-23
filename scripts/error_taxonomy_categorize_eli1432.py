"""ELI-1432 step 4 -- mechanical (non-judgment) categorization pass over
data/eli-1432-error-taxonomy/raw_candidates.json.

Most of the FN taxonomy ELI-1252 hand-built by reading quotes is actually
already recoverable programmatically here, because the current pipeline's
semantic grounding check (ELI-1147/1192) tags every "proposed then rejected"
candidate with a specific, self-descriptive rejection code (e.g.
"relationship_not_actually_happened", "integration_partner_is_backend_infrastructure")
-- ELI-1252 predates that granularity and had to infer the same distinctions
by hand. This script:

1. Maps every FN row's `reason_code` to an ELI-1252-style category via
   CODE_TO_CATEGORY (mechanical, no LLM/manual judgment -- the code already
   *is* the pipeline's own judgment).
2. For the two synthetic "never proposed" codes
   (never_proposed_fetch_miss / never_proposed_extraction_miss), splits
   fetch-miss further into "vendor's own site" vs "off-site" by comparing
   golden's own supporting_evidence URL hostname against the vendor's own
   domain (TARGETS[vendor]["url"]) -- same distinction ELI-1252 items 1/2
   drew, but here read directly from golden's citation instead of a second
   round of manual lookup. `source_type` in golden ("vendor_website" etc.)
   is deliberately NOT trusted for this -- confirmed live (Experian Health
   PaymentSafe / Arrowhead Regional Medical Center) that a citation can be
   labeled "vendor_website" while its actual url is a third-party site
   (sanbernardino.legistar.com), so hostname comparison is the only
   reliable signal.
3. unresolved_endpoint rows get a live Neo4j check (see
   _categorize_unresolved_endpoint) instead of a blanket mapping, since the
   rejection code alone can't distinguish a real seed gap from an ambiguous
   match (PR review finding -- see CODE_TO_CATEGORY's comment).
4. Leaves FP rows and the ~25 non-code-derivable rows uncategorized here --
   those need real judgment (reading the quote), done separately.

Usage: uv run python scripts/error_taxonomy_categorize_eli1432.py
Output: data/eli-1432-error-taxonomy/categorized.json
"""

import asyncio
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).parent))

from neo4j import AsyncGraphDatabase

from ai_internship.config import Settings
from ai_internship.evaluation.scorers import _normalize
from ai_internship.public_assets import init_dataset, research_targets

DATA_DIR = Path(__file__).parent.parent / "data" / "eli-1432-error-taxonomy"
RAW_PATH = DATA_DIR / "raw_candidates.json"
OUTPUT_PATH = DATA_DIR / "categorized.json"

WORKTREE_EVAL_SCRIPT = None  # Targets now come from the bundled dataset.

# Real grounding-rejection codes -> ELI-1252-style category. Names are
# self-descriptive (they're the pipeline's own semantic-check verdict, per
# grounding.py's full_rejection_reason / Braintrust-hosted prompt) --
# mapped onto the closest ELI-1252 bucket rather than inventing new
# category names, so this taxonomy stays comparable to that precedent doc.
CODE_TO_CATEGORY = {
    "relationship_not_actually_happened": "Not actually real yet / evidence doesn't hold up",
    "relationship_claim_not_supported": "Evidence doesn't hold up",
    "subject_not_real_organization": "Not a real named organization",
    "subject_is_individual": "Not a real named organization",
    "unnamed_object": "Not a real named organization",
    "integration_partner_not_specific_entity": "Not a real named organization",
    "integration_partner_not_third_party": "Relationship-type mismatch (own product)",
    "integration_partner_is_backend_infrastructure": "Relationship-type mismatch (backend infra)",
    "target_as_integration_partner": "Relationship-type mismatch (self-reference)",
    "self_edge": "Relationship-type mismatch (self-reference)",
    "subject_not_in_evidence": "Evidence doesn't hold up",
    "object_not_in_evidence": "Evidence doesn't hold up",
    "quote_not_in_source": "Evidence integrity (fabricated/altered quote)",
    "subject_named_only_in_url": "Evidence doesn't hold up (named only in URL)",
    "object_named_only_in_url": "Evidence doesn't hold up (named only in URL)",
    "meta_commentary_quote": "Evidence doesn't hold up (meta-commentary, not real content)",
    "target_not_endpoint": "Relationship-type mismatch (target not a real endpoint)",
    "relationship_shape_contradicts_profile": "Relationship-type mismatch (wrong relation shape)",
    "incomplete_relationship": "Evidence doesn't hold up (incomplete relationship)",
    # unresolved_endpoint is deliberately NOT mapped here -- see
    # _categorize_unresolved_endpoint. graph.py's verify_grounding filters out
    # BOTH "ambiguous" (multiple seeded Product nodes share a normalized name)
    # and "unmatched" (no seed at all) entities before grounding, and both
    # produce this same rejection code -- so blanket-mapping every occurrence
    # to "missing marketplace_id" conflates a real seed gap with an ambiguous
    # match, which is a different problem (PR review finding). Verified live
    # against current Neo4j state instead of assumed.
}


def _load_targets(path: Path) -> dict:
    return research_targets()


def _hostname(url: str | None) -> str | None:
    if not url:
        return None
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def _base_domain(host: str | None) -> str | None:
    """Naive eTLD+1 (last two dot-separated labels) -- not a real public-suffix
    lookup (doesn't handle co.uk-style multi-label TLDs), but every vendor in
    TARGETS uses a single-label TLD (.com/.ai/.care/.health/...), so this is
    exact enough here. Used so a TARGETS url pointing at a subdomain (e.g.
    `ehr.meditech.com`) and a citation on the parent root domain
    (`meditech.com`), or the reverse, both compare equal -- a plain
    `h.endswith("." + vendor_host)` check (PR review finding) only caught one
    direction, misclassifying genuine same-company evidence as off-site."""
    if not host:
        return None
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


async def _categorize_unresolved_endpoint(driver, row: dict) -> str:
    """Live-check the current Neo4j state for this rejection's subject/object,
    instead of assuming every unresolved_endpoint means "no seeded node" --
    graph.py's verify_grounding filters out BOTH "ambiguous" and "unmatched"
    entities before grounding, and both produce this same code (PR review
    finding). Checks Product-labeled nodes only (the only label
    _CREATABLE_LABELS excludes from free creation, per graphiti_resolve.py)."""
    names = {n for n in (row.get("subject"), row.get("object")) if n}
    zero_match = []
    ambiguous_match = []
    for name in names:
        records, _, _ = await driver.execute_query(
            "MATCH (n:Product) WHERE toLower(trim(n.name)) = toLower(trim($name)) "
            "RETURN count(n) AS n",
            name=name,
        )
        count = records[0]["n"] if records else 0
        if count == 0:
            zero_match.append(name)
        elif count >= 2:
            ambiguous_match.append(name)
    if ambiguous_match:
        return "Blocked by ambiguous entity match (not a genuine extraction miss)"
    if zero_match:
        return "Blocked by missing marketplace_id (not a genuine extraction miss)"
    # Every endpoint resolves cleanly to exactly one node *right now*, yet
    # this candidate was rejected as unresolved at extraction time -- the
    # seed most likely didn't exist yet when extraction ran and was added
    # since (e.g. ELI-1427), but this (source, profile) pair was never
    # re-extracted because plan_extraction skips anything already terminal.
    # Same staleness pattern as ELI-1441, just for seed data instead of
    # crawled content -- a real, if small, distinct bucket from a live gap.
    return "Blocked at extraction time, resolves cleanly now (stale, never re-extracted)"


def _load_golden_evidence(dataset, key: str) -> dict[tuple[str, str], list[dict]]:
    """(vendor, normalized entity name) -> list of supporting_evidence dicts."""
    out: dict[tuple[str, str], list[dict]] = {}
    for sample in dataset:
        vendor = sample.get("metadata", {}).get("product_name", "")
        expected = sample.get("expected", {})
        items = expected.get(key, []) if isinstance(expected, dict) else []
        for item in items:
            if not isinstance(item, dict):
                continue
            name = item.get("name", "")
            evidence = item.get("supporting_evidence") or []
            if name and evidence:
                out[(vendor, _normalize(name))] = evidence
    return out


def _categorize_never_proposed(row: dict, targets: dict, golden_evidence: dict) -> str:
    vendor_host = _hostname(targets.get(row["vendor"], {}).get("url"))
    key_field = "customers" if row["profile"] == "customers" else "integrations"
    evidence = golden_evidence[key_field].get((row["vendor"], _normalize(row["entity"] or "")))
    if row["reason_code"] == "never_proposed_extraction_miss":
        return "Fetched the right page, extraction/grounding still failed"
    # never_proposed_fetch_miss: name not found in any fetched page. Split by
    # where golden's own citation actually lives.
    if not evidence:
        return "Never fetched (no URL in golden's own evidence to check domain)"
    hosts = {_hostname(e.get("url")) for e in evidence if e.get("url")}
    hosts.discard(None)
    if not hosts:
        return "Never fetched (no URL in golden's own evidence to check domain)"
    vendor_base = _base_domain(vendor_host)
    if vendor_base and any(_base_domain(h) == vendor_base for h in hosts):
        return "Never fetched -- evidence sits on the vendor's own site"
    return "Never fetched -- evidence only exists off-site"


async def main():
    settings = Settings()
    raw = json.loads(RAW_PATH.read_text())
    targets = _load_targets(WORKTREE_EVAL_SCRIPT)

    customers_dataset = list(
        init_dataset(
            project="ai-internship",
            name="Marketplace Customers Dataset",
            api_key=settings.braintrust_api_key,
        )
    )
    integrations_dataset = list(
        init_dataset(
            project="ai-internship",
            name="Marketplace Integrations Dataset",
            api_key=settings.braintrust_api_key,
        )
    )
    golden_evidence = {
        "customers": _load_golden_evidence(customers_dataset, "customers"),
        "integrations": _load_golden_evidence(integrations_dataset, "integrations"),
    }

    driver = AsyncGraphDatabase.driver(
        settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
    )
    fn_categorized = 0
    fn_needs_judgment = 0
    try:
        for row in raw["rows"]:
            if row["outcome"] == "FP" or row["outcome"] == "TP":
                row["category"] = None  # needs a separate hand-judgment/spot-check pass
                continue
            if row["outcome"] != "FN":
                continue
            code = row["reason_code"]
            if code in ("never_proposed_fetch_miss", "never_proposed_extraction_miss"):
                row["category"] = _categorize_never_proposed(row, targets, golden_evidence)
                fn_categorized += 1
            elif code == "unresolved_endpoint":
                row["category"] = await _categorize_unresolved_endpoint(driver, row)
                fn_categorized += 1
            elif code in CODE_TO_CATEGORY:
                row["category"] = CODE_TO_CATEGORY[code]
                fn_categorized += 1
            else:
                row["category"] = None
                fn_needs_judgment += 1
    finally:
        await driver.close()

    OUTPUT_PATH.write_text(json.dumps(raw, indent=2))
    print(f"FN rows mechanically categorized: {fn_categorized}", file=sys.stderr)
    print(
        f"FN rows still needing judgment (real code, no mapping): {fn_needs_judgment}",
        file=sys.stderr,
    )
    print(f"Wrote {OUTPUT_PATH}", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main())
