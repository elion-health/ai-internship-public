"""Read-only extraction-yield baseline (ELI-1136): per-vendor metrics on what
the `extract` step of the ingestion pipeline (enrichment/graph.py) actually
produces from the first-party corpus, before grounding/resolve filters
anything out. Reads whatever ExtractionRun/RetainedSource/FrontierUrl/Entity
data already exists in Neo4j from prior `run_stateful_research.py` passes;
does not call an LLM or write anything. Builds on ELI-1135's fetch-stage
baseline (same 3 vendors, same first-party-only scope).

Two gotchas this script exists specifically to avoid:

  * `RELATES_TO` is not a single schema in this shared Neo4j instance --
    ELI-1057's graphiti fuzzy-matching client writes its own native
    `RELATES_TO` edges (keys like `group_id`, `fact_embedding`, `uuid`) into
    the same graph, with NO `extraction_id` property at all. A query that
    scans `RELATES_TO` by anything other than an exact
    `extraction_id IN [...]` map filter will silently mix in unrelated
    graphiti edges (confirmed live: 4037 total RELATES_TO edges, only 135
    carry `extraction_id`).
  * Only `outcome = 'succeeded'` ExtractionRun records ever get real
    RELATES_TO edges persisted (traced in enrichment/graph.py's `persist`
    node: any other terminal outcome persists `nodes=[], edges=[]`). Every
    other terminal outcome's pre-grounding assertions survive only inside
    `ExtractionRun.grounding_rejections` (a JSON blob written by
    `finish_extraction`, discovery/store.py) -- that is the only place a
    rejected assertion's subject/relation/object is still readable. Combining
    accepted edges (succeeded runs) with grounding_rejections (every run) is
    what makes "pre-grounding yield" reconstructable without re-running the
    LLM.

Computes, per vendor, scoped to first-party sources only:
  * useful-source rate: % of first-party sources with >=1 ExtractionRun
    (any profile/extractor_version) that produced >=1 assertion pre-grounding
  * per (profile, extractor_version): outcome mix, relations/customers/
    products extracted -- extractor_version is a first-class grouping axis
    so a future run against a new model/prompt slots in without a rewrite;
    a profile a vendor has never run is omitted, not reported as zero
  * distribution of extracted relations across a URL-pattern-derived page
    type, using each profile's largest (primary) extractor_version group so
    a vendor with N extractor_version variants for the same profile doesn't
    have its page-type counts inflated Nx by counting the same pages once
    per variant

Writes overview.svg, page-types.svg, and one <profile>-extractor-versions.svg
per profile the vendor has actually run, plus metrics.json, under
--output-dir.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from neo4j import AsyncGraphDatabase

from ai_internship.config import Settings
from ai_internship.public_assets import research_targets

VENDORS = list(research_targets())

# Fixed categorical slots (dataviz skill palette), matching vendor_fetch_baseline.py
# where the concept overlaps so color identity stays consistent across reports.
COLOR_USEFUL = "#2a78d6"  # blue

# Every IngestionOutcome value (enrichment/contracts.py) gets a fixed color and
# a canonical display order, so render_extractor_version_svg can draw a bar for
# whatever outcomes actually show up in a variant's outcome_counts -- rather
# than a hardcoded 3-outcome list that silently drops any run hitting
# resolution_ambiguity/persistence_failure/no_profile (none of the 3 vendors'
# runs hit those today, but a future run could).
OUTCOME_COLORS = {
    "succeeded": "#1f8a4c",  # green -- survived grounding
    "grounding_failure": "#e34948",  # red -- reused from vendor_fetch_baseline's COLOR_ERRORED
    "resolution_ambiguity": "#8451a1",  # violet
    "no_facts": "#eb6834",  # orange -- reused from vendor_fetch_baseline's COLOR_BLOCKED
    "persistence_failure": "#52514e",  # dark grey -- infra failure, not a data-quality outcome
    "no_profile": "#c3c2b7",  # pale grey -- not applicable
}
_FALLBACK_OUTCOME_COLOR = "#c14e9b"  # magenta -- any outcome key not in the map above

# Categorical, CVD-safe-ish ordering for the 7 page-type buckets.
PAGE_TYPE_COLORS = {
    "case_study": "#2a78d6",  # blue
    "press_news_blog": "#1c9099",  # aqua
    "resources_collateral": "#c9a227",  # yellow
    "partner_integration_directory": "#3f9142",  # green
    "product_platform": "#8451a1",  # violet
    "about_company": "#c0392b",  # red
    "other": "#c14e9b",  # magenta
}
PAGE_TYPE_ORDER = list(PAGE_TYPE_COLORS)

INK_PRIMARY = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"
GRIDLINE = "#e1e0d9"
BASELINE = "#c3c2b7"
SURFACE = "#fcfcfb"


def page_type(url: str) -> str:
    """Bucket a URL into one of 7 mutually-exclusive, priority-ordered
    (first-match-wins) page types, based on URL shape alone -- a heuristic,
    not a content classifier. Order matters: e.g. FirstHx's
    /news/case-study-reduced-wait-times-firsthx-weight-clinic must land in
    case_study, not press_news_blog, so the case-study check runs first even
    though the URL also matches the /news prefix rule."""
    lowered = url.lower()
    path = re.sub(r"^https?://[^/]+", "", lowered)

    if (
        re.search(r"case-stud(y|ies)", lowered)
        or re.search(r"customer-stor(y|ies)", lowered)
        or re.search(r"success-stor(y|ies)", lowered)
        or "testimonial" in lowered
        or re.search(r"/(customers|partners)/?$", path)
    ):
        return "case_study"
    if re.match(r"^/(news|blog|press|media|insights|stories)(/|$)", path) or "-stories" in lowered:
        return "press_news_blog"
    if (
        "fact-sheet" in lowered
        or re.search(r"white-?paper", lowered)
        or re.search(r"e-?book", lowered)
        or "newsletter" in lowered
        or "webinar" in lowered
        or "download" in lowered
        or re.search(r"\bguide\b", lowered)
        or re.search(r"\breport\b", lowered)
    ):
        # Named "collateral," not "gated" -- a URL matching these keywords
        # isn't necessarily behind an actual download
        # wall; a whitepaper or newsletter can be a normal directly-readable
        # page. This bucket groups resource-shaped marketing collateral by
        # URL keyword, not verified gating, which isn't detectable from the
        # URL alone.
        return "resources_collateral"
    if (
        "integrat" in lowered
        or "marketplace" in lowered
        or "ecosystem" in lowered
        or "directory" in lowered
        or re.search(r"/integrations/?$", path)
    ):
        return "partner_integration_directory"
    host_match = re.match(r"^https?://([^/]+)", lowered)
    host = host_match.group(1) if host_match else ""
    subdomain = host.split(".")[0] if host.count(".") >= 2 else "www"
    if (
        "product" in lowered
        or "platform" in lowered
        or "solutions" in lowered
        or "oncology" in lowered
        or subdomain not in {"www", "page", "resources", "blog", "docs", "support"}
    ):
        return "product_platform"
    if (
        "about" in lowered
        or "contact" in lowered
        or "careers" in lowered
        or "team" in lowered
        or "leadership" in lowered
        or "company" in lowered
        or re.search(r"\bjobs\b", lowered)
    ):
        return "about_company"
    return "other"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("docs/experiments/eli-1136-extraction-yield"),
    )
    return parser.parse_args(argv)


_VARIANT_QUERY = """
MATCH (t:ResearchTarget {name: $vendor})-[:TRACKS]->(f:FrontierUrl)
WHERE f.origin = 'first_party' AND f.status = 'fetched'
MATCH (s:RetainedSource {source_id: f.latest_source_id})
MATCH (run:ExtractionRun)-[:EXTRACTED]->(s)
WHERE run.outcome <> 'running' AND run.target_id = t.target_id
WITH s, run
ORDER BY s.source_id, run.profile, run.extractor_version, run.revision DESC
WITH s.source_id AS source_id, coalesce(s.final_url, s.requested_url) AS url,
     run.profile AS profile, run.extractor_version AS extractor_version,
     collect(run)[0] AS latest_run
RETURN source_id, url, profile, extractor_version,
       latest_run.extraction_id AS extraction_id,
       latest_run.revision AS revision,
       latest_run.outcome AS outcome,
       latest_run.grounding_rejections AS grounding_rejections_json
"""

# Exact map-pattern filter on extraction_id -- deliberately NOT a bare
# `MATCH ()-[e:RELATES_TO]->()` scan, which would also match graphiti's
# unrelated native RELATES_TO edges that carry no extraction_id at all.
_EDGES_QUERY = """
MATCH (subj:Entity)-[edge:RELATES_TO]->(obj:Entity)
WHERE edge.extraction_id IN $extraction_ids
RETURN edge.extraction_id AS extraction_id, edge.fact AS fact,
       [l IN labels(subj) WHERE l <> 'Entity'][0] AS subject_type, subj.name AS subject_name,
       [l IN labels(obj) WHERE l <> 'Entity'][0] AS object_type, obj.name AS object_name
"""


def _assertions_for_row(row: dict, edges_by_extraction_id: dict[str, list[dict]]) -> list[dict]:
    assertions: list[dict] = []
    if row["outcome"] == "succeeded":
        for edge in edges_by_extraction_id.get(row["extraction_id"], []):
            # collapse_edges (graph/edges.py) joins distinct quotes for the same
            # (subject, relation, object) triple with " | ", so one edge can
            # represent >1 accepted assertion -- but `fact` is raw extracted
            # quote text with no delimiter escaping, so splitting on " | " to
            # recover that count would overcount any quote that happens to
            # contain that literal sequence itself (confirmed live: 20 real
            # accepted edges already do, e.g. a markdown table in the quote).
            # Counting one edge as one assertion undercounts only in the
            # narrower case where collapse_edges actually merged 2+ distinct
            # quotes for the same triple -- a known, accepted tradeoff since
            # overcounting from unrelated quote content would be worse.
            assertions.append(
                {
                    "subject_type": edge["subject_type"],
                    "subject": edge["subject_name"],
                    "object_type": edge["object_type"],
                    "object": edge["object_name"],
                }
            )
    for rejection in json.loads(row["grounding_rejections_json"] or "[]"):
        a = rejection.get("assertion", {})
        assertions.append(
            {
                "subject_type": a.get("subject_type"),
                "subject": a.get("subject"),
                "object_type": a.get("object_type"),
                "object": a.get("object"),
            }
        )
    return assertions


def pct(n: int, d: int) -> float:
    return round(100 * n / d, 1) if d else 0.0


async def compute_vendor_metrics(driver, vendor: str) -> dict:
    rows, _, _ = await driver.execute_query(_VARIANT_QUERY, vendor=vendor)
    rows = [dict(r) for r in rows]

    succeeded_ids = [r["extraction_id"] for r in rows if r["outcome"] == "succeeded"]
    edges_by_extraction_id: dict[str, list[dict]] = defaultdict(list)
    if succeeded_ids:
        edge_rows, _, _ = await driver.execute_query(_EDGES_QUERY, extraction_ids=succeeded_ids)
        for r in edge_rows:
            edges_by_extraction_id[r["extraction_id"]].append(dict(r))

    by_variant: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in rows:
        by_variant[(row["profile"], row["extractor_version"])].append(row)

    all_sources_attempted: set[str] = set()
    all_sources_useful: set[str] = set()
    profile_source_attempted: dict[str, set[str]] = defaultdict(set)
    profile_source_useful: dict[str, set[str]] = defaultdict(set)
    profiles_out: dict[str, dict] = {}
    # profile -> candidates for the single representative variant used for
    # page-type bucketing (so N extractor_version variants of the same
    # profile don't inflate page-type relation counts Nx). Picked once, after
    # every variant has been processed, via an explicit deterministic sort
    # key -- not incrementally during iteration, since Python dict iteration
    # order over by_variant follows Cypher result order, not source count,
    # and a strict "keep if strictly greater" comparison would silently keep
    # whichever tied variant happened to be seen first (real case: FirstHx's
    # customers profile has 3 variants that all cover all 49 sources).
    variant_candidates: dict[str, list[tuple[int, int, str, list[dict]]]] = defaultdict(list)

    for (profile, extractor_version), variant_rows in by_variant.items():
        outcome_counts: Counter[str] = Counter()
        n_relations = 0
        customers: set[str] = set()
        products: set[str] = set()
        source_ids_this_variant: set[str] = set()

        for row in variant_rows:
            outcome_counts[row["outcome"]] += 1
            source_ids_this_variant.add(row["source_id"])
            all_sources_attempted.add(row["source_id"])
            profile_source_attempted[profile].add(row["source_id"])

            assertions = _assertions_for_row(row, edges_by_extraction_id)
            if assertions:
                all_sources_useful.add(row["source_id"])
                profile_source_useful[profile].add(row["source_id"])
            n_relations += len(assertions)
            for a in assertions:
                if a["subject_type"] == "HealthSystem" and a["subject"]:
                    customers.add(a["subject"])
                if a["object_type"] == "HealthSystem" and a["object"]:
                    customers.add(a["object"])
                if a["subject_type"] == "Product" and a["subject"]:
                    products.add(a["subject"])
                if a["object_type"] == "Product" and a["object"]:
                    products.add(a["object"])

        profiles_out.setdefault(profile, {"extractor_versions": []})
        profiles_out[profile]["extractor_versions"].append(
            {
                "extractor_version": extractor_version,
                "extractor_version_label": (extractor_version or "unknown")[:8],
                "sources_attempted": len(source_ids_this_variant),
                "outcome_counts": dict(outcome_counts),
                "relations_extracted": n_relations,
                "customers_extracted": len(customers),
                "products_extracted": len(products),
            }
        )

        variant_candidates[profile].append(
            (len(source_ids_this_variant), n_relations, extractor_version, variant_rows)
        )

    # Deterministic pick: most source coverage, then most relations found,
    # then extractor_version string as a final, fully reproducible tiebreak.
    primary_variant_rows: dict[str, list[dict]] = {
        profile: max(candidates, key=lambda c: (c[0], c[1], c[2]))[3]
        for profile, candidates in variant_candidates.items()
    }

    for profile, data in profiles_out.items():
        data["extractor_versions"].sort(key=lambda v: v["extractor_version_label"])
        # Union across every extractor_version variant of this profile -- not
        # a single variant's count -- so the overview chart's (n/d) annotation
        # always matches sources_useful_pct, even if variants ever cover
        # different source sets.
        data["sources_attempted"] = len(profile_source_attempted[profile])
        data["sources_useful"] = len(profile_source_useful[profile])
        data["sources_useful_pct"] = pct(
            len(profile_source_useful[profile]), len(profile_source_attempted[profile])
        )

    # Page-type breakdown: each profile's primary (largest) extractor_version
    # variant contributes once per source -- summed across profiles (customers
    # + integrations are genuinely different extraction attempts over the same
    # page, not redundant re-runs), never across extractor_version variants of
    # the same profile.
    page_type_relations: Counter[str] = Counter()
    url_by_source = {r["source_id"]: r["url"] for r in rows}
    for variant_rows in primary_variant_rows.values():
        for row in variant_rows:
            assertions = _assertions_for_row(row, edges_by_extraction_id)
            if not assertions:
                continue
            bucket = page_type(url_by_source.get(row["source_id"], ""))
            page_type_relations[bucket] += len(assertions)

    total_page_type_relations = sum(page_type_relations.values())
    page_type_breakdown = {
        bucket: {
            "relations": page_type_relations.get(bucket, 0),
            "pct": pct(page_type_relations.get(bucket, 0), total_page_type_relations),
        }
        for bucket in PAGE_TYPE_ORDER
        if page_type_relations.get(bucket, 0) > 0
    }

    return {
        "vendor": vendor,
        "sources_attempted": len(all_sources_attempted),
        "sources_useful": len(all_sources_useful),
        "sources_useful_pct": pct(len(all_sources_useful), len(all_sources_attempted)),
        "profiles": profiles_out,
        "page_type_breakdown": page_type_breakdown,
    }


def _bar(x: float, width: float, height: float, plot_bottom: float, color: str) -> str:
    """A column: 4px-rounded top, square at the baseline, capped at <=24px thick."""
    width = min(width, 24)
    if height <= 0:
        return ""
    r = min(4, height)
    y = plot_bottom - height
    return (
        f'<path d="M{x},{plot_bottom} L{x},{y + r} '
        f"Q{x},{y} {x + r},{y} L{x + width - r},{y} "
        f"Q{x + width},{y} {x + width},{y + r} "
        f'L{x + width},{plot_bottom} Z" fill="{color}"/>'
    )


def _render_bar_chart(
    vendor: str, title: str, subtitle: str, bars: list[tuple[str, float, int, int, str]]
) -> str:
    """Shared renderer for all 3 chart types: a flat sequence of labeled,
    percent-height bars with (n/d) count annotations, same visual grammar as
    vendor_fetch_baseline.py's render_chart_svg (margins, gridlines, bar
    shape)."""
    w = max(280, 200 + 90 * len(bars))
    h = 380
    margin_left, margin_right, margin_top, margin_bottom = 56, 20, 92, 56
    plot_w = w - margin_left - margin_right
    plot_h = h - margin_top - margin_bottom
    plot_bottom = margin_top + plot_h

    slot_w = plot_w / max(len(bars), 1)
    bar_w = 24

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
        f'viewBox="0 0 {w} {h}" font-family="system-ui, -apple-system, \'Segoe UI\', sans-serif">',
        f'<rect x="0" y="0" width="{w}" height="{h}" fill="{SURFACE}"/>',
        f'<text x="{margin_left}" y="28" font-size="16" font-weight="600" '
        f'fill="{INK_PRIMARY}">{vendor}</text>',
        f'<text x="{margin_left}" y="44" font-size="12" fill="{INK_SECONDARY}">{title}'
        f" &#183; {subtitle}</text>",
    ]

    for gridline_pct in (0, 25, 50, 75, 100):
        y = plot_bottom - (gridline_pct / 100) * plot_h
        parts.append(
            f'<line x1="{margin_left}" y1="{y}" x2="{margin_left + plot_w}" y2="{y}" '
            f'stroke="{GRIDLINE}" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{margin_left - 10}" y="{y + 4}" font-size="11" fill="{INK_MUTED}" '
            f'text-anchor="end">{gridline_pct}%</text>'
        )
    parts.append(
        f'<line x1="{margin_left}" y1="{plot_bottom}" '
        f'x2="{margin_left + plot_w}" y2="{plot_bottom}" '
        f'stroke="{BASELINE}" stroke-width="1"/>'
    )

    for i, (label, pct_value, n, d, color) in enumerate(bars):
        slot_x = margin_left + i * slot_w
        bar_x = slot_x + (slot_w - bar_w) / 2
        bar_h = (pct_value / 100) * plot_h
        parts.append(_bar(bar_x, bar_w, bar_h, plot_bottom, color))
        cap_y = plot_bottom - bar_h
        parts.append(
            f'<text x="{bar_x + bar_w / 2}" y="{cap_y - 8}" font-size="13" font-weight="600" '
            f'fill="{INK_PRIMARY}" text-anchor="middle">{pct_value}%</text>'
        )
        parts.append(
            f'<text x="{bar_x + bar_w / 2}" y="{cap_y - 22}" font-size="10" fill="{INK_MUTED}" '
            f'text-anchor="middle">({n}/{d})</text>'
        )
        for line_i, line in enumerate(label.split("\n")):
            parts.append(
                f'<text x="{slot_x + slot_w / 2}" y="{plot_bottom + 20 + line_i * 13}" '
                f'font-size="11" fill="{INK_SECONDARY}" text-anchor="middle">{line}</text>'
            )

    parts.append("</svg>")
    return "\n".join(parts)


def render_overview_svg(metrics: dict) -> str:
    bars = [
        (
            "Useful\n(any profile)",
            metrics["sources_useful_pct"],
            metrics["sources_useful"],
            metrics["sources_attempted"],
            COLOR_USEFUL,
        )
    ]
    for profile, data in sorted(metrics["profiles"].items()):
        bars.append(
            (
                f"Useful\n({profile})",
                data["sources_useful_pct"],
                data["sources_useful"],
                data["sources_attempted"],
                COLOR_USEFUL,
            )
        )
    return _render_bar_chart(
        metrics["vendor"],
        "Extraction useful-source rate",
        f"{metrics['sources_attempted']} first-party sources with an extraction attempt",
        bars,
    )


def render_extractor_version_svg(vendor: str, profile: str, variants: list[dict]) -> str:
    bars = []
    for variant in variants:
        d = variant["sources_attempted"]
        oc = variant["outcome_counts"]
        label = variant["extractor_version_label"]
        annotation = (
            f"R:{variant['relations_extracted']} "
            f"C:{variant['customers_extracted']} "
            f"P:{variant['products_extracted']}"
        )
        # Canonical outcomes first (fixed order/colors), then any outcome key
        # present that isn't in OUTCOME_COLORS (shouldn't happen given
        # IngestionOutcome's fixed enum, but never drop a row silently).
        ordered_keys = [k for k in OUTCOME_COLORS if k in oc]
        ordered_keys += [k for k in oc if k not in OUTCOME_COLORS]
        for outcome_key in ordered_keys:
            n = oc[outcome_key]
            color = OUTCOME_COLORS.get(outcome_key, _FALLBACK_OUTCOME_COLOR)
            bars.append((f"{label}\n{outcome_key}\n{annotation}", pct(n, d), n, d, color))
    return _render_bar_chart(
        vendor,
        f"Extraction outcome by extractor_version ({profile})",
        f"{len(variants)} extractor_version variant(s) on record",
        bars,
    )


def render_page_type_svg(metrics: dict) -> str:
    bars = []
    total = sum(v["relations"] for v in metrics["page_type_breakdown"].values())
    for bucket in PAGE_TYPE_ORDER:
        entry = metrics["page_type_breakdown"].get(bucket)
        if not entry:
            continue
        bars.append(
            (
                bucket.replace("_", "\n"),
                entry["pct"],
                entry["relations"],
                total,
                PAGE_TYPE_COLORS[bucket],
            )
        )
    return _render_bar_chart(
        metrics["vendor"],
        "Extracted relations by page/URL type",
        f"{total} pre-grounding relations across primary extractor_version per profile",
        bars,
    )


async def main_async(vendors: list[str]) -> list[dict]:
    settings = Settings()
    driver = AsyncGraphDatabase.driver(
        settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
    )
    results = []
    try:
        for vendor in vendors:
            results.append(await compute_vendor_metrics(driver, vendor))
    finally:
        await driver.close()
    return results


def main() -> int:
    args = parse_args()
    results = asyncio.run(main_async(VENDORS))

    args.output_dir.mkdir(parents=True, exist_ok=True)
    for metrics in results:
        slug = metrics["vendor"].lower().replace(" ", "-")
        vendor_dir = args.output_dir / slug
        vendor_dir.mkdir(parents=True, exist_ok=True)
        (vendor_dir / "overview.svg").write_text(render_overview_svg(metrics) + "\n")
        (vendor_dir / "page-types.svg").write_text(render_page_type_svg(metrics) + "\n")
        for profile, data in metrics["profiles"].items():
            svg = render_extractor_version_svg(
                metrics["vendor"], profile, data["extractor_versions"]
            )
            (vendor_dir / f"{profile}-extractor-versions.svg").write_text(svg + "\n")
        print(json.dumps(metrics, indent=2))

    (args.output_dir / "metrics.json").write_text(json.dumps(results, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
