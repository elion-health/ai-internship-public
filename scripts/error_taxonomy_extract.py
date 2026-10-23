"""ELI-1252 -- pull every TP/FP/FN candidate (with its evidence quote) for
all 8 live-data vendors, both profiles, on the current production extractor
(openai/gpt-5.6-luna). This is a data-dump step only: no categorization, no
judgment. Output feeds the hand-judging pass.

Reuses scripts/pipeline_functions.py's run_customers_pipeline /
run_integrations_pipeline (ELI-1170) rather than re-implementing the
fetch -> candidate -> dedup -> TP/FP/FN split logic. Those functions build
`unique_candidates`/`tp`/`fp`/`fn`/`tn` without an evidence-quote field (the
ELI-1170 notebook never needed one), so this script re-derives the quote for
each unique candidate from the same raw `rows`/`edges_by_extraction_id`
structures the pipeline functions already return via `locals()`, instead of
duplicating their grouping/dedup logic.

Usage: uv run python scripts/error_taxonomy_extract.py
Output: data/eli-1252-error-taxonomy/raw_candidates.json
"""

import asyncio
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import build_frozen_case_set as bfc
import vendor_extraction_yield as vey
from neo4j import AsyncGraphDatabase
from pipeline_functions import run_customers_pipeline, run_fetch_stage, run_integrations_pipeline

from ai_internship.config import Settings
from ai_internship.evaluation.scorers import _normalize
from ai_internship.public_assets import init_dataset

VENDORS = list(bfc.MARKETPLACE_IDS)

DATA_DIR = Path(__file__).parent.parent / "data" / "eli-1252-error-taxonomy"
OUTPUT_PATH = DATA_DIR / "raw_candidates.json"


# The two pipelines don't share field/variable names (customers profile keys
# candidates by "customer" using a (subject_type,subject,object,object_type)
# naming helper; integrations profile keys them by "product" using a plain
# (subject,object) helper) -- see scripts/pipeline_functions.py's
# run_customers_pipeline vs run_integrations_pipeline. These per-profile specs
# translate each one into the same shape for the rest of this script.
CUSTOMERS_SPEC = {
    "name_key": "customer",
    "name_fn": lambda ctx: ctx["customer_name"],  # (subject, subj_type, object, obj_type)
    "name_fn_takes_types": True,
    "edges_by_extraction_id": "edges_by_extraction_id",
    "rows": "rows",
    "tp": "tp",
    "fp": "fp",
    "fn": "fn",
}
INTEGRATIONS_SPEC = {
    "name_key": "product",
    "name_fn": lambda ctx: ctx["integrated_product_name"],  # (subject, object)
    "name_fn_takes_types": False,
    "edges_by_extraction_id": "integrations_edges_by_extraction_id",
    "rows": "integrations_rows",
    "tp": "integrations_tp",
    "fp": "integrations_fp",
    "fn": "integrations_fn",
}


def _quote_lookup_kept(ctx: dict, spec: dict) -> dict[tuple[str, str], str]:
    """(normalized entity name, url) -> evidence quote (edge.fact) for this
    profile's *accepted* edges. Keyed by url too, not just entity name --
    an entity checked on more than one page (raw_count > 1) can have a
    different quote per page, and unique_kept/unique_rejected each already
    picked one specific representative url for the group. A name-only
    lookup would silently pair that group's url with whichever page's
    quote happened to be seen first, which can be a *different* page than
    the one the group's url/reason actually came from (PR review finding,
    Codex)."""
    lookup: dict[tuple[str, str], str] = {}
    name_fn = spec["name_fn"](ctx)
    extraction_id_to_url = {r["extraction_id"]: r["url"] for r in ctx[spec["rows"]]}
    for extraction_id, edges in ctx[spec["edges_by_extraction_id"]].items():
        url = extraction_id_to_url.get(extraction_id)
        for edge in edges:
            if spec["name_fn_takes_types"]:
                name = name_fn(
                    edge["subject_name"],
                    edge["subject_type"],
                    edge["object_name"],
                    edge["object_type"],
                )
            else:
                name = name_fn(edge["subject_name"], edge["object_name"])
            if name and url and edge.get("fact"):
                key = (_normalize(name), url)
                if key not in lookup:
                    lookup[key] = edge["fact"]
    return lookup


def _quote_lookup_rejected(ctx: dict, spec: dict) -> dict[tuple[str, str], str]:
    """(normalized entity name, url) -> evidence quote (assertion.quote)
    for this profile's *rejected* checks. See _quote_lookup_kept for why
    url is part of the key."""
    lookup: dict[tuple[str, str], str] = {}
    name_fn = spec["name_fn"](ctx)
    for row in ctx[spec["rows"]]:
        url = row["url"]
        for rejection in json.loads(row["grounding_rejections_json"] or "[]"):
            a = rejection.get("assertion", {})
            if spec["name_fn_takes_types"]:
                name = name_fn(
                    a.get("subject"),
                    a.get("subject_type"),
                    a.get("object"),
                    a.get("object_type"),
                )
            else:
                name = name_fn(a.get("subject"), a.get("object"))
            if name and url and a.get("quote"):
                key = (_normalize(name), url)
                if key not in lookup:
                    lookup[key] = a["quote"]
    return lookup


def _rows_for(
    bucket: list[dict],
    spec: dict,
    kept_lookup: dict,
    rejected_lookup: dict,
    vendor: str,
    profile: str,
    outcome: str,
) -> list[dict]:
    out = []
    name_key = spec["name_key"]
    for c in bucket:
        name = c[name_key]
        quote = None
        if name:
            key = (_normalize(name), c["url"])
            if c["kept"]:
                # Macroscope: the old fallback ignored URL entirely once the
                # exact (entity, url) key missed, matching on the normalized
                # entity name alone -- for a mixed dedup group (kept on one
                # page, rejected on another), that could pull a quote from a
                # DIFFERENT page than the one this row actually reports,
                # falsely attributing evidence to the wrong URL. Leaving
                # `quote` unset when the exact key isn't there is more honest
                # than a plausible-but-wrong quote.
                quote = kept_lookup.get(key)
            else:
                quote = rejected_lookup.get(key)
        out.append(
            {
                "vendor": vendor,
                "profile": profile,
                "outcome": outcome,
                "entity": name,
                "subject": c["subject"],
                "object": c["object"],
                "reason_code": c["reason"],
                "url": c["url"],
                "raw_count": c["raw_count"],
                "quote": quote,
            }
        )
    return out


def _fuzzy_name_pattern(name: str) -> re.Pattern:
    """Whole-word pattern for `name` that also matches the space/hyphen
    variation `_normalize()` collapses in real pipeline matching (Macroscope:
    a literal search for the raw name missed real matches like "eVisitNB"
    for golden's "eVisit NB", wrongly downgrading a genuine extraction-stage
    miss to a "page never fetched" fetch-stage miss). Built by escaping each
    space/hyphen-delimited token and allowing `[-\\s]*` between them, rather
    than normalizing `content` itself, so a real match still carries real
    offsets into the ORIGINAL text for the evidence quote below."""
    tokens = [t for t in re.split(r"[-\s]+", name.strip()) if t]
    joined = r"[-\s]*".join(re.escape(t) for t in tokens)
    return re.compile(rf"(?<!\w){joined}(?!\w)", re.IGNORECASE)


async def _never_proposed_fn_rows(vendor, profile, missed_names, fetched_sources):
    """Golden entries that never became a candidate at all -- not even a
    rejected one (the pipeline_functions.py `fn` bucket only covers
    candidates that were proposed and then rejected; `missed_norm` /
    `integrations_missed` is the separate, un-exported set of golden
    entries with zero candidates, PR review finding, Codex). For each,
    check whether the name appears (whole-word, space/hyphen-flexible) in
    any fetched first-party page to split fetch-stage misses (page never
    fetched) from extraction-stage misses (page fetched, name present, but
    no candidate ever proposed)."""
    rows = []
    for name in sorted(missed_names):
        pattern = _fuzzy_name_pattern(name)
        found = None
        for _source_id, url, content in fetched_sources:
            m = pattern.search(content)
            if m:
                start, end = max(0, m.start() - 80), min(len(content), m.end() + 80)
                found = (url, content[start:end].strip())
                break
        url, quote, reason_code = (
            (found[0], found[1], "never_proposed_extraction_miss")
            if found
            else (None, None, "never_proposed_fetch_miss")
        )
        rows.append(
            {
                "vendor": vendor,
                "profile": profile,
                "outcome": "FN",
                "entity": name,
                "subject": None,
                "object": None,
                "reason_code": reason_code,
                "url": url,
                "raw_count": 0,
                "quote": quote,
            }
        )
    return rows


async def main():
    settings = Settings()
    driver = AsyncGraphDatabase.driver(
        settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
    )
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

    all_rows = []
    summary = []
    skipped = []

    try:
        for vendor in VENDORS:
            print(f"=== {vendor} ===", file=sys.stderr)
            fetch_metrics = await run_fetch_stage(vendor, driver)
            fetched_sources = await bfc.get_fetched_sources(driver, vendor)

            try:
                customers_ctx = await run_customers_pipeline(
                    vendor, customers_dataset, driver, vey, bfc, fetch_metrics
                )
            except (IndexError, KeyError):
                # No ExtractionRun exists yet under PRODUCTION_MODEL for this
                # vendor/profile -- it hasn't been (re-)run under the current
                # extractor version. Not a code bug: skip and report, rather
                # than silently kicking off a fresh extraction run (that
                # writes to Neo4j and costs LLM calls -- a different action
                # than this read-only autopsy).
                print("  SKIP customers: no run under current production model", file=sys.stderr)
                skipped.append(
                    {
                        "vendor": vendor,
                        "profile": "customers",
                        "reason": "no_run_under_production_model",
                    }
                )
                customers_ctx = None

            if customers_ctx is not None:
                kept_q = _quote_lookup_kept(customers_ctx, CUSTOMERS_SPEC)
                rej_q = _quote_lookup_rejected(customers_ctx, CUSTOMERS_SPEC)
                c_tp = _rows_for(
                    customers_ctx[CUSTOMERS_SPEC["tp"]],
                    CUSTOMERS_SPEC,
                    kept_q,
                    rej_q,
                    vendor,
                    "customers",
                    "TP",
                )
                c_fp = _rows_for(
                    customers_ctx[CUSTOMERS_SPEC["fp"]],
                    CUSTOMERS_SPEC,
                    kept_q,
                    rej_q,
                    vendor,
                    "customers",
                    "FP",
                )
                c_fn = _rows_for(
                    customers_ctx[CUSTOMERS_SPEC["fn"]],
                    CUSTOMERS_SPEC,
                    kept_q,
                    rej_q,
                    vendor,
                    "customers",
                    "FN",
                )
                # missed_norm = golden_norm - extracted_norm, where extracted_norm
                # only counts ACCEPTED names -- so it also includes every name that
                # WAS proposed and rejected (c_fn), not just names with zero
                # candidates. Subtract those out or every rejected FN gets a
                # duplicate "never proposed" row (PR review finding, Macroscope).
                rejected_norms = {_normalize(c["entity"]) for c in c_fn}
                missed_names = [
                    customers_ctx["raw_golden_by_norm"][n]
                    for n in customers_ctx["missed_norm"]
                    if n not in rejected_norms
                ]
                c_never = await _never_proposed_fn_rows(
                    vendor, "customers", missed_names, fetched_sources
                )
                all_rows += c_tp + c_fp + c_fn + c_never
            else:
                c_tp = c_fp = c_fn = c_never = []

            try:
                integrations_ctx = await run_integrations_pipeline(
                    vendor,
                    integrations_dataset,
                    driver,
                    vey,
                    bfc,
                    (
                        customers_ctx["metrics"]
                        if customers_ctx is not None
                        else await vey.compute_vendor_metrics(driver, vendor)
                    ),
                    fetch_metrics,
                )
            except (IndexError, KeyError):
                print("  SKIP integrations: no run under current production model", file=sys.stderr)
                skipped.append(
                    {
                        "vendor": vendor,
                        "profile": "integrations",
                        "reason": "no_run_under_production_model",
                    }
                )
                integrations_ctx = None

            if integrations_ctx is not None:
                i_kept_q = _quote_lookup_kept(integrations_ctx, INTEGRATIONS_SPEC)
                i_rej_q = _quote_lookup_rejected(integrations_ctx, INTEGRATIONS_SPEC)
                i_tp = _rows_for(
                    integrations_ctx[INTEGRATIONS_SPEC["tp"]],
                    INTEGRATIONS_SPEC,
                    i_kept_q,
                    i_rej_q,
                    vendor,
                    "integrations",
                    "TP",
                )
                i_fp = _rows_for(
                    integrations_ctx[INTEGRATIONS_SPEC["fp"]],
                    INTEGRATIONS_SPEC,
                    i_kept_q,
                    i_rej_q,
                    vendor,
                    "integrations",
                    "FP",
                )
                i_fn = _rows_for(
                    integrations_ctx[INTEGRATIONS_SPEC["fn"]],
                    INTEGRATIONS_SPEC,
                    i_kept_q,
                    i_rej_q,
                    vendor,
                    "integrations",
                    "FN",
                )
                rejected_integration_norms = {_normalize(c["entity"]) for c in i_fn}
                missed_integration_names = [
                    integrations_ctx["integrations_raw_golden_by_norm"][n]
                    for n in integrations_ctx["integrations_missed"]
                    if n not in rejected_integration_norms
                ]
                i_never = await _never_proposed_fn_rows(
                    vendor, "integrations", missed_integration_names, fetched_sources
                )
                all_rows += i_tp + i_fp + i_fn + i_never
            else:
                i_tp = i_fp = i_fn = i_never = []

            summary.append(
                {
                    "vendor": vendor,
                    "customers": {
                        "tp": len(c_tp),
                        "fp": len(c_fp),
                        "fn": len(c_fn),
                        "fn_never_proposed": len(c_never),
                    },
                    "integrations": {
                        "tp": len(i_tp),
                        "fp": len(i_fp),
                        "fn": len(i_fn),
                        "fn_never_proposed": len(i_never),
                    },
                }
            )
            print(
                f"  customers TP={len(c_tp)} FP={len(c_fp)} FN={len(c_fn)}+{len(c_never)}never | "
                f"integrations TP={len(i_tp)} FP={len(i_fp)} FN={len(i_fn)}+{len(i_never)}never",
                file=sys.stderr,
            )
    finally:
        await driver.close()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"summary": summary, "skipped": skipped, "rows": all_rows}
    OUTPUT_PATH.write_text(json.dumps(payload, indent=2))
    print(f"\nWrote {len(all_rows)} rows to {OUTPUT_PATH}", file=sys.stderr)
    if skipped:
        print(f"Skipped (no run under production model): {skipped}", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main())
