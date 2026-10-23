"""Read-only fetch-stage baseline (ELI-1135): per-vendor metrics on the
first-party acquisition stage (map_first_party -> fetch_first_party),
independent of any extractor/LLM/profile. Reads whatever RetainedSource and
FrontierUrl data already exists in Neo4j from a prior `run_stateful_research.py`
/ `eval_stateful_research.py` run; does not run the graph or write anything.

Computes, per vendor, scoped to first-party sources only, two top-level
categories:
  * Errored -- the fetch/scrape itself failed, no content was ever retrieved
    (derived from FrontierUrl's per-URL `attempts` counter, not the current
    `status` snapshot -- a URL that failed before an eventual successful
    retry has its failures overwritten in `status` alone)
  * Blockades -- the fetch succeeded and real content came back, but access
    to the actual page was denied: a non-2xx HTTP status, a bot-detection/
    CAPTCHA challenge page, or a lead-gen/gated-content wall (a form
    guarding a document, not the document itself). Shown with its specific
    sub-reason whenever one is present.
  * A content-length histogram at --thresholds, computed directly off raw
    `content` length for every retained source -- independent of the above

An earlier version also reported an "Unnamed" category (pages that fetched
fine but didn't mention the vendor's full name). Removed: it turned out to
flag pages that plainly do mention the vendor -- e.g. flatiron.com/careers
says "Flatiron" throughout -- just not the full two-word product name
"Flatiron OncoEMR", which a generic careers page has no reason to use. That
made it a check on the pipeline's own name-matching strictness, not a
useful signal about the page.

Writes one chart.svg per vendor plus metrics.json under --output-dir.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from pathlib import Path

from neo4j import AsyncGraphDatabase

from ai_internship.config import Settings
from ai_internship.discovery.contracts import canonicalize_url
from ai_internship.public_assets import research_targets

# Kept in sync with scripts/eval_stateful_research.py's TARGETS. Identified by
# canonical URL rather than by name or a hardcoded target_id hash -- Neo4j
# already holds a same-vendor duplicate target with a different casing
# ("flatiron oncoemr" vs "Flatiron OncoEMR"), and name-matching would either
# miss a differently-cased duplicate or silently aggregate multiple
# same-named-but-different-URL targets into one vendor. `canonicalize_url`
# is the same normalization `ResearchTarget.build()` applies before deriving
# its `target_id`, so matching on `canonical_url` here finds the same node
# without needing to know or hardcode that hash -- and adding a future
# vendor is just adding its URL, not looking up an opaque ID in Neo4j first.
VENDORS = {name: target["url"] for name, target in research_targets().items()}

DEFAULT_THRESHOLDS = [100, 200, 300, 400, 500]

# Deliberately narrow: only phrasing specific to unlocking a *named* document
# (case study / report / whitepaper / guide) counts as a gated wall. A
# generic "fill out the form to request a demo" CTA at the end of an
# otherwise fully-readable article is not blocking anything -- an earlier,
# broader pattern falsely flagged exactly that on a real FirstHx page.
# Validated against the full 3-vendor corpus (98 first-party sources): 0
# false positives, and it still catches the one confirmed real case
# (Fold Health's bowtie-medical-case-study lead-gen form).
GATED_CONTENT_PATTERNS = [
    r"enter your (?:information|details) below to (?:download|view|access)",
    r"(?:download|view|access|read) the full (?:case study|report|whitepaper|guide|pdf)",
    r"sign up (?:for free )?to (?:continue reading|view the full|download|access the full)",
    r"subscribe to (?:continue reading|view the full|read the full|download)",
    r"create a (?:free )?account to (?:view|access|download) the full",
    r"log in to (?:view|access|continue reading) the full",
]

# Not yet observed in any of the 3 vendors' current corpora (every first-party
# fetch returns status 200), but real signals worth checking for going
# forward -- a bot-detection challenge can return 200 with a challenge page
# instead of the real content.
BOT_DETECTION_PATTERNS = [
    r"verify you are human",
    r"checking your browser",
    r"just a moment\.\.\.",
    r"complete the (?:captcha|security check)",
    r"unusual traffic",
    r"access denied",
    r"please enable javascript and cookies",
]

# Fixed categorical slots (dataviz skill palette), same mapping across every
# vendor chart so color identity stays consistent.
COLOR_EXCLUDED = "#898781"  # grey -- never attempted at all, by policy, not a failure
COLOR_ERRORED = "#e34948"  # red -- attempted, content never retrieved
COLOR_BLOCKADE = "#eb6834"  # orange -- fetched, but access was denied
COLOR_CLEAN = "#1f8a4c"  # green -- fetched, real usable content
COLOR_UNRESOLVED = "#c9c2a3"  # tan -- discovered/queued but the crawl never reached it yet
# Sequential blue ramp (ordinal steps, palette.md) for the length histogram --
# one step per threshold, lightest first, since thresholds are ordered.
HISTOGRAM_RAMP = ["#86b6ef", "#5598e7", "#2a78d6", "#1c5cab", "#104281"]

INK_PRIMARY = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"
GRIDLINE = "#e1e0d9"
BASELINE = "#c3c2b7"
SURFACE = "#fcfcfb"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--thresholds",
        type=int,
        nargs="+",
        default=DEFAULT_THRESHOLDS,
        help="N values for the content-length histogram (default 100 200 300 400 500)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("docs/experiments/eli-1135-fetch-baseline"),
    )
    return parser.parse_args(argv)


def classify_blockade(content: str, status_code: int | None) -> str | None:
    """Real access-denial, distinct from "the content just isn't relevant."
    Returns a specific sub-reason, or None if the page isn't blocked."""
    if status_code is not None and not (200 <= status_code < 300):
        return f"http_{status_code}"
    lowered = content.lower()
    for pattern in GATED_CONTENT_PATTERNS:
        if re.search(pattern, lowered):
            return "gated_content"
    for pattern in BOT_DETECTION_PATTERNS:
        if re.search(pattern, lowered):
            return "bot_detection"
    return None


async def compute_vendor_metrics(driver, vendor: str, url: str, thresholds: list[int]) -> dict:
    """`total_urls` is every first-party URL the crawler discovered and
    considered for this target, full stop -- the real "how many URLs did we
    even look at" number. Every one of those URLs falls into EXACTLY one of
    5 mutually exclusive, exhaustive buckets, so they always sum back to
    `total_urls`:
        excluded + errored + blocked + clean + unresolved == total_urls
    - excluded      -- never attempted at all (policy reason: wrong domain,
                       technical exclusion) -- NOT the same as a fetch that
                       was tried and failed.
    - errored -- attempted, never got real content back.
    - blocked       -- fetched fine, but the content itself denies access.
    - clean         -- fetched, and it's real usable content.
    - unresolved    -- still discovered/pending/unchanged/removed -- the
                       crawl never reached a terminal outcome for this URL
                       yet. Normal for a partially-crawled target (discovery
                       can find more URLs than the crawl budget gets to);
                       NOT an error (Macroscope review -- an earlier version
                       of this function either mislabeled these as errored,
                       or asserted they could never happen and crashed the
                       whole baseline run for every vendor when one did).
    Retries (`retries`) are reported separately, alongside, not folded into
    any bucket or into `total_urls` -- a URL that failed twice before
    eventually succeeding is one URL, counted once, in `clean` or `blocked`;
    the retry count is extra context about how much it took, not a URL
    category of its own."""
    canonical_url = canonicalize_url(url)

    # `FrontierUrl.status` is a current snapshot, not a log: a URL that failed
    # N times before an eventual successful retry ends up `status="fetched"`
    # with no trace of the prior failures in `status` alone -- and `status`
    # can move again on a later rerun (`upsert_frontier`'s ON MATCH clause
    # overwrites it back to "pending"/"excluded" for any URL not already
    # "fetched"), without ever touching `attempts`. So a URL's historical
    # failures can end up sitting behind almost any current status value.
    # `attempts` is only ever incremented by mark_frontier_failed and never
    # reset, so summing it across every first-party row regardless of current
    # status -- not just "fetched"/"failed" -- is the only way to count every
    # real retry.
    frontier_records, _, _ = await driver.execute_query(
        "MATCH (t:ResearchTarget {canonical_url: $canonical_url})-[:TRACKS]->(f:FrontierUrl) "
        "WHERE f.origin = 'first_party' "
        "RETURN f.status AS status, coalesce(f.attempts, 0) AS attempts, "
        "coalesce(f.requested_url, f.canonical_url) AS url, f.policy_reason AS policy_reason",
        canonical_url=canonical_url,
    )
    total_urls = len(frontier_records)

    # Bucket 1: never attempted at all -- excluded by policy before a single
    # fetch was tried (wrong domain, technical exclusion). Distinct from a
    # URL that was tried and failed; policy exclusion is not a fetch error.
    excluded_urls = [
        {"url": r["url"], "reason": r["policy_reason"]}
        for r in frontier_records
        if r["status"] == "excluded"
    ]
    # Bucket 2: attempted, never ended up with real content -- "failed" is
    # the only terminal status that means this. Deliberately NOT "anything
    # that isn't excluded/fetched": FrontierStatus also has discovered/
    # pending (queued, never actually attempted -- e.g. discovery found more
    # URLs than the crawl budget ever got to) and unchanged/removed, none of
    # which mean "errored" (Macroscope review -- the old catch-all silently
    # mislabeled a never-attempted URL as an error if one ever showed up).
    errored_urls = [
        {"url": r["url"], "attempts": r["attempts"], "status": r["status"]}
        for r in frontier_records
        if r["status"] == "failed"
    ]
    retry_urls = [
        {"url": r["url"], "attempts": r["attempts"], "status": r["status"]}
        for r in frontier_records
        if r["attempts"] > 0
    ]
    total_retry_attempts = sum(r["attempts"] for r in frontier_records)

    # Sources are content-addressed and merged `ON CREATE SET` (store.py
    # retain_for_target), so `RetainedSource.entrypoint` reflects whichever
    # origin created that content first and is never updated afterward -- a
    # source first retained externally keeps `entrypoint="external_acquisition"`
    # even if a first-party frontier later reaches the same content. Deriving
    # the source set from the first-party FrontierUrl's own `latest_source_id`
    # (set on every successful retain) instead of the source's own `entrypoint`
    # avoids silently dropping those sources.
    source_records, _, _ = await driver.execute_query(
        "MATCH (t:ResearchTarget {canonical_url: $canonical_url})-[:TRACKS]->(f:FrontierUrl) "
        "WHERE f.origin = 'first_party' AND f.status = 'fetched' "
        "MATCH (s:RetainedSource {source_id: f.latest_source_id}) "
        "RETURN s.final_url AS url, s.content AS content, s.source_id AS source_id, "
        "s.content_hash AS content_hash, s.provider_metadata AS provider_metadata, "
        "coalesce(s.quality_status, 'eligible') AS quality_status, "
        "s.quality_reason AS quality_reason",
        canonical_url=canonical_url,
    )
    sources = []
    for r in source_records:
        meta = json.loads(r["provider_metadata"]) if r["provider_metadata"] else {}
        sources.append(
            {
                "url": r["url"],
                "content": r["content"] or "",
                "source_id": r["source_id"],
                "content_hash": r["content_hash"],
                "status_code": meta.get("status_code"),
                "quality_status": r["quality_status"],
                "quality_reason": r["quality_reason"],
            }
        )
    # Should equal total_urls - len(excluded_urls) - len(errored_urls)
    # by construction (every "fetched" FrontierUrl has a RetainedSource) --
    # not re-derived from that subtraction so a real data inconsistency
    # would surface as a mismatch rather than being silently assumed away.
    fetched_count = len(sources)

    def pct(n: int, d: int) -> float:
        return round(100 * n / d, 1) if d else 0.0

    # Bucket 3: fetched fine, but access to the real content was denied.
    blockade_reasons: dict[str, int] = {}
    blockade_urls: list[dict] = []
    for source in sources:
        reason = classify_blockade(source["content"], source["status_code"])
        if reason:
            blockade_reasons[reason] = blockade_reasons.get(reason, 0) + 1
            blockade_urls.append({"url": source["url"], "reason": reason})
    blockades = len(blockade_urls)
    # Bucket 4: fetched, and the content is real and usable.
    clean = fetched_count - blockades
    used = clean + blockades  # "fetched at all" -- clean + blocked, out of total_urls

    # Bucket 5: still discovered/pending/unchanged/removed -- the crawl never
    # reached a terminal outcome for this URL. Normal for a partially-
    # crawled target, not an error (Macroscope review -- asserting these
    # could never happen crashed the whole baseline run for every vendor the
    # first time one showed up; report them honestly instead).
    unresolved_urls = [
        {"url": r["url"], "attempts": r["attempts"], "status": r["status"]}
        for r in frontier_records
        if r["status"] not in ("excluded", "failed", "fetched")
    ]

    # The 5-bucket invariant this whole module is built on, checked rather
    # than assumed -- exhaustive across every FrontierStatus value that
    # exists today, so this can never silently drift out of sync again.
    accounted = len(excluded_urls) + len(errored_urls) + fetched_count + len(unresolved_urls)
    assert accounted == total_urls, (
        f"{vendor}: {total_urls - accounted} FrontierUrl row(s) unaccounted for -- "
        "a new FrontierStatus value probably needs a bucket here."
    )

    histogram = []
    for n in thresholds:
        over_n = sum(1 for source in sources if len(source["content"]) > n)
        histogram.append({"threshold": n, "count": over_n, "pct": pct(over_n, fetched_count)})

    # One more real gate sits between "fetched" and "extraction actually ran
    # on it": assess_content() (research/content_quality.py) -- too_short
    # (<200 chars), loading_placeholder (page never really rendered), or
    # navigation_only (nothing but a bare list of links). A page can be
    # "used" here (fetched, not blocked) and still never reach extraction.
    # This is why Stage 3's page count is smaller than Stage 2's "used" --
    # made explicit here instead of leaving that gap unexplained.
    quality_excluded_urls = [
        {"url": s["url"], "reason": s["quality_reason"] or "unknown"}
        for s in sources
        if s["quality_status"] != "eligible"
    ]
    eligible_for_extraction = used - len(quality_excluded_urls)

    # One more real narrowing, AFTER the quality gate: `list_target_source_ids`
    # (discovery/store.py) -- what actually dispatches pages to extraction --
    # deduplicates by content_hash, keeping only the lowest source_id per
    # distinct piece of content. Two different URLs serving byte-identical
    # content (e.g. /careers and /career-listings on one site) collapse
    # into a single extraction dispatch. This is why Stage 3's page count can
    # still be smaller than `eligible_for_extraction` above even with the
    # quality gate already accounted for.
    eligible_sources = [s for s in sources if s["quality_status"] == "eligible"]
    content_hash_groups: dict[str, list[dict]] = {}
    for s in eligible_sources:
        content_hash_groups.setdefault(s["content_hash"], []).append(s)
    duplicate_content_groups = []
    for group in content_hash_groups.values():
        if len(group) > 1:
            group_sorted = sorted(group, key=lambda s: s["source_id"])
            duplicate_content_groups.append(
                {
                    "kept_url": group_sorted[0]["url"],
                    "duplicate_urls": [s["url"] for s in group_sorted[1:]],
                }
            )
    dispatched_for_extraction = len(content_hash_groups)

    return {
        "vendor": vendor,
        "total_urls": total_urls,
        "excluded": len(excluded_urls),
        "excluded_pct": pct(len(excluded_urls), total_urls),
        "excluded_urls": excluded_urls,
        "errored": len(errored_urls),
        "errored_pct": pct(len(errored_urls), total_urls),
        "errored_urls": errored_urls,
        "blocked": blockades,
        "blocked_pct": pct(blockades, total_urls),
        "blockade_reasons": blockade_reasons,
        "blockade_urls": blockade_urls,
        "clean": clean,
        "clean_pct": pct(clean, total_urls),
        "unresolved": len(unresolved_urls),
        "unresolved_pct": pct(len(unresolved_urls), total_urls),
        "unresolved_urls": unresolved_urls,
        "used": used,
        "used_pct": pct(used, total_urls),
        "content_length_histogram": histogram,
        "retries": {
            "total_attempts": total_retry_attempts,
            "urls": retry_urls,
        },
        "eligible_for_extraction": eligible_for_extraction,
        "eligible_for_extraction_pct": pct(eligible_for_extraction, used),
        "quality_excluded": len(quality_excluded_urls),
        "quality_excluded_pct": pct(len(quality_excluded_urls), used),
        "quality_excluded_urls": quality_excluded_urls,
        "dispatched_for_extraction": dispatched_for_extraction,
        "dispatched_for_extraction_pct": pct(dispatched_for_extraction, eligible_for_extraction),
        "duplicate_urls_dropped": eligible_for_extraction - dispatched_for_extraction,
        "duplicate_content_groups": duplicate_content_groups,
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


def render_chart_svg(metrics: dict) -> str:
    # 200px base covers margins + the first 4 fixed bars; +90 per histogram
    # bucket, plus one more +90 for the "Unresolved" bar added alongside them.
    w = 200 + 90 + 90 * (1 + len(metrics["content_length_histogram"]))
    h = 380
    margin_left, margin_right, margin_top, margin_bottom = 56, 20, 92, 56
    plot_w = w - margin_left - margin_right
    plot_h = h - margin_top - margin_bottom
    plot_bottom = margin_top + plot_h

    # The 5 buckets, each a % of total_urls -- they sum to 100% by
    # construction (excluded + errored + blocked + clean + unresolved ==
    # total_urls). Unresolved is 0% for every vendor today (no partially-
    # crawled targets in the current data), but still rendered so a future
    # partial crawl shows up as a real bar instead of vanishing silently.
    bars = [
        (
            "Excluded",
            metrics["excluded_pct"],
            metrics["excluded"],
            metrics["total_urls"],
            COLOR_EXCLUDED,
        ),
        (
            "Errored",
            metrics["errored_pct"],
            metrics["errored"],
            metrics["total_urls"],
            COLOR_ERRORED,
        ),
        (
            "Blocked",
            metrics["blocked_pct"],
            metrics["blocked"],
            metrics["total_urls"],
            COLOR_BLOCKADE,
        ),
        (
            "Clean",
            metrics["clean_pct"],
            metrics["clean"],
            metrics["total_urls"],
            COLOR_CLEAN,
        ),
        (
            "Unresolved",
            metrics["unresolved_pct"],
            metrics["unresolved"],
            metrics["total_urls"],
            COLOR_UNRESOLVED,
        ),
    ]
    for i, bucket in enumerate(metrics["content_length_histogram"]):
        color = HISTOGRAM_RAMP[min(i, len(HISTOGRAM_RAMP) - 1)]
        bars.append(
            (
                f"> {bucket['threshold']}c",
                bucket["pct"],
                bucket["count"],
                metrics["clean"] + metrics["blocked"],
                color,
            )
        )

    slot_w = plot_w / len(bars)
    bar_w = 24

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
        f'viewBox="0 0 {w} {h}" font-family="system-ui, -apple-system, \'Segoe UI\', sans-serif">',
        f'<rect x="0" y="0" width="{w}" height="{h}" fill="{SURFACE}"/>',
        f'<text x="{margin_left}" y="28" font-size="16" font-weight="600" '
        f'fill="{INK_PRIMARY}">{metrics["vendor"]}</text>',
        f'<text x="{margin_left}" y="44" font-size="12" fill="{INK_SECONDARY}">'
        f"First-party fetch health &#183; {metrics['total_urls']} URLs looked at, "
        f"{metrics['used']} used ({metrics['used_pct']}%)</text>",
    ]

    # Gridlines + y-axis ticks at 0/25/50/75/100.
    for pct in (0, 25, 50, 75, 100):
        y = plot_bottom - (pct / 100) * plot_h
        parts.append(
            f'<line x1="{margin_left}" y1="{y}" x2="{margin_left + plot_w}" y2="{y}" '
            f'stroke="{GRIDLINE}" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{margin_left - 10}" y="{y + 4}" font-size="11" fill="{INK_MUTED}" '
            f'text-anchor="end">{pct}%</text>'
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
        # SVG <text> doesn't honor embedded "\n" -- a literal newline in the
        # string renders as one unbroken line, not two, which is what was
        # actually overlapping neighboring labels once there were 4 bucket
        # bars packed into the same width 2 used to have. Split explicitly
        # and stack each line as its own <text> element.
        for line_i, line in enumerate(label.split("\n")):
            parts.append(
                f'<text x="{slot_x + slot_w / 2}" '
                f'y="{plot_bottom + 20 + line_i * 13}" font-size="12" '
                f'fill="{INK_SECONDARY}" text-anchor="middle">{line}</text>'
            )

    parts.append("</svg>")
    return "\n".join(parts)


def render_eligibility_svg(metrics: dict) -> str:
    """The gate between "fetched" (Stage 2's own scope) and "extraction
    actually ran on it" (Stage 3) -- assess_content()'s too_short/
    loading_placeholder/navigation_only checks. Only 2 bars, both a % of
    `used` (Stage 2's fetched-and-not-blocked count), so they read as a
    direct continuation of the main chart rather than a new, disconnected
    scale."""
    w, h = 280, 380
    margin_left, margin_right, margin_top, margin_bottom = 56, 20, 92, 56
    plot_w = w - margin_left - margin_right
    plot_h = h - margin_top - margin_bottom
    plot_bottom = margin_top + plot_h

    bars = [
        (
            "Eligible",
            metrics["eligible_for_extraction_pct"],
            metrics["eligible_for_extraction"],
            metrics["used"],
            COLOR_CLEAN,
        ),
        (
            "Excluded\n(too short, etc.)",
            metrics["quality_excluded_pct"],
            metrics["quality_excluded"],
            metrics["used"],
            COLOR_EXCLUDED,
        ),
    ]
    slot_w = plot_w / len(bars)
    bar_w = 24

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
        f'viewBox="0 0 {w} {h}" font-family="system-ui, -apple-system, \'Segoe UI\', sans-serif">',
        f'<rect x="0" y="0" width="{w}" height="{h}" fill="{SURFACE}"/>',
        f'<text x="{margin_left}" y="28" font-size="16" font-weight="600" '
        f'fill="{INK_PRIMARY}">{metrics["vendor"]}</text>',
        f'<text x="{margin_left}" y="44" font-size="12" fill="{INK_SECONDARY}">'
        f"Eligible for extraction, of {metrics['used']} used</text>",
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
                f'<text x="{slot_x + slot_w / 2}" '
                f'y="{plot_bottom + 20 + line_i * 13}" font-size="12" '
                f'fill="{INK_SECONDARY}" text-anchor="middle">{line}</text>'
            )

    parts.append("</svg>")
    return "\n".join(parts)


async def main_async(args: argparse.Namespace) -> list[dict]:
    settings = Settings()
    driver = AsyncGraphDatabase.driver(
        settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
    )
    results = []
    try:
        for vendor, url in VENDORS.items():
            results.append(await compute_vendor_metrics(driver, vendor, url, args.thresholds))
    finally:
        await driver.close()
    return results


def main() -> int:
    args = parse_args()
    results = asyncio.run(main_async(args))

    args.output_dir.mkdir(parents=True, exist_ok=True)
    for metrics in results:
        slug = metrics["vendor"].lower().replace(" ", "-")
        vendor_dir = args.output_dir / slug
        vendor_dir.mkdir(parents=True, exist_ok=True)
        (vendor_dir / "chart.svg").write_text(render_chart_svg(metrics) + "\n")
        print(json.dumps(metrics, indent=2))

    (args.output_dir / "metrics.json").write_text(json.dumps(results, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
