"""Shared per-vendor pipeline functions for the ELI-1170 notebook (fetch,
customers, integrations, mini-vs-luna model comparison). Mechanically
extracted from Suki's originally fully-worked-through cells -- see
build_pipeline_functions.py in the ELI-1170 scratchpad for how this file
is regenerated. Imported by the notebook, not run directly."""

import json

import vendor_extraction_yield as vey

from ai_internship.evaluation.scorers import _multilabel_precision, _multilabel_recall, _normalize

GOOD, BAD, ACCENT = "#0ca30c", "#d03b3b", "#2a78d6"
NEUTRAL = "#eda100"
BORDER = "1px solid rgba(128,128,128,0.35)"
ROW_BORDER = "1px solid rgba(128,128,128,0.15)"
MUTED = "#888888"


def box(label, n, color, caption, width="auto"):
    return f"""
    <div style="border:{BORDER}; border-top:4px solid {color}; border-radius:6px; padding:10px 16px;
                text-align:center; {"flex:1;" if width == "auto" else f"width:{width};"}">
      <div style="font-size:24px; font-weight:700; color:{color};">{n}</div>
      <div style="font-size:11px; color:{MUTED}; font-weight:600;">{label}</div>
      <div style="font-size:11px; color:{MUTED}; margin-top:4px;">{caption}</div>
    </div>"""


def arrow(text):
    return f"""
    <div style="text-align:center; font-size:11px; color:{MUTED}; margin:4px 0; font-style:italic;">
      &#9660; {text}
    </div>"""


def section_label(text, color):
    return (
        f'<div style="font-size:11px; font-weight:700; color:{color}; text-transform:uppercase; '
        f'letter-spacing:0.05em; margin:16px 0 6px;">{text}</div>'
    )


def stat(label, value, color):
    return f"""
    <div style="border:{BORDER}; border-top:4px solid {color}; border-radius:6px; padding:14px 16px; text-align:center; flex:1;">
      <div style="font-size:32px; font-weight:700; color:{color};">{value}</div>
      <div style="font-size:12px; color:{MUTED}; margin-top:2px;">{label}</div>
    </div>"""


def bucket(tag, color, items, by_norm):
    names = [by_norm[n] for n in sorted(items)]
    rows = "".join(
        f'<div style="padding:3px 0; border-bottom:{ROW_BORDER}; font-family:ui-monospace,monospace; font-size:12px;">{n}</div>'
        for n in names
    )
    return f"""
    <div style="border:{BORDER}; border-top:4px solid {color}; border-radius:6px; padding:12px 14px; flex:1;">
      <div style="font-size:12px; font-weight:600; color:{color};">{tag}</div>
      <div style="font-size:24px; font-weight:700; margin:2px 0;">{len(names)}</div>
      <details><summary style="cursor:pointer; font-size:12px; color:{ACCENT};">show names</summary>
        <div style="max-height:180px; overflow-y:auto; margin-top:6px;">{rows or "<i>none</i>"}</div>
      </details>
    </div>"""


def customer_name(subject, subject_type, obj, object_type):
    """The HealthSystem-typed endpoint of a customers-profile relation, whichever side it's on."""
    if subject_type == "HealthSystem":
        return subject
    if object_type == "HealthSystem":
        return obj
    return None


async def run_fetch_stage(vendor, driver):
    VENDOR = vendor
    import vendor_fetch_baseline as vfb
    from IPython.display import SVG, display

    fetch_metrics = await vfb.compute_vendor_metrics(
        driver, VENDOR, vfb.VENDORS[VENDOR], vfb.DEFAULT_THRESHOLDS
    )

    print(f"Total URLs looked at: {fetch_metrics['total_urls']}")
    print(
        f"  Excluded, never tried (wrong domain/policy): {fetch_metrics['excluded']} ({fetch_metrics['excluded_pct']}%)"
    )
    print(
        f"  Errored (tried, no content came back): {fetch_metrics['errored']} ({fetch_metrics['errored_pct']}%)"
    )
    print(
        f"  Blocked (fetched, access denied): {fetch_metrics['blocked']} ({fetch_metrics['blocked_pct']}%)"
    )
    print(
        f"  Clean (fetched, usable content): {fetch_metrics['clean']} ({fetch_metrics['clean_pct']}%)"
    )
    bucket_sum = (
        fetch_metrics["excluded"]
        + fetch_metrics["errored"]
        + fetch_metrics["blocked"]
        + fetch_metrics["clean"]
    )
    print(
        f"  (sums to {bucket_sum}, matches total looked at: {bucket_sum == fetch_metrics['total_urls']})"
    )
    print(
        f"\nUsed = clean + blocked, out of total looked at: {fetch_metrics['used']}/{fetch_metrics['total_urls']} ({fetch_metrics['used_pct']}%)"
    )
    print(
        f"Retries (separate from the buckets above): {fetch_metrics['retries']['total_attempts']} total attempts "
        f"across {len(fetch_metrics['retries']['urls'])} URLs that needed one"
    )

    for h in fetch_metrics["content_length_histogram"]:
        print(
            f"  >{h['threshold']}c: {h['count']}/{fetch_metrics['clean'] + fetch_metrics['blocked']} fetched ({h['pct']}%)"
        )

    if fetch_metrics["excluded_urls"]:
        print(f"\nExcluded URLs ({len(fetch_metrics['excluded_urls'])}):")
        for e in fetch_metrics["excluded_urls"]:
            print(f"  [{e['reason']}] {e['url']}")

    print(f"\nErrored ({len(fetch_metrics['errored_urls'])}):")
    for e in fetch_metrics["errored_urls"]:
        print(f"  [{e['attempts']}x, now {e['status']}] {e['url']}")

    print(f"\nBlocked ({len(fetch_metrics['blockade_urls'])}):")
    for b in fetch_metrics["blockade_urls"]:
        print(f"  [{b['reason']}] {b['url']}")

    display(SVG(vfb.render_chart_svg(fetch_metrics)))

    # --- One more real gate, before extraction ever sees a page ---
    # A page can be "used" above (fetched, not blocked) and STILL never reach
    # extraction: assess_content() (research/content_quality.py) drops it if
    # it's under 200 characters, a "loading..." placeholder that never really
    # rendered, or nothing but a bare list of markdown links. This is why
    # Stage 3's page count is smaller than the "used" number above -- shown
    # here explicitly instead of leaving that gap unexplained.
    print(f"\nOf the {fetch_metrics['used']} used, how many are eligible for extraction?")
    print(
        f"  Eligible: {fetch_metrics['eligible_for_extraction']} ({fetch_metrics['eligible_for_extraction_pct']}%)"
    )
    print(
        f"  Excluded (too short / placeholder / nav-only): {fetch_metrics['quality_excluded']} "
        f"({fetch_metrics['quality_excluded_pct']}%)"
    )
    if fetch_metrics["quality_excluded_urls"]:
        print("\n  Excluded URLs:")
        for e in fetch_metrics["quality_excluded_urls"]:
            print(f"    [{e['reason']}] {e['url']}")

    display(SVG(vfb.render_eligibility_svg(fetch_metrics)))

    # --- One more real narrowing, right before extraction actually runs ---
    # The dispatch query (discovery/store.py's list_target_source_ids) dedupes
    # by content_hash, not URL -- two different URLs serving byte-identical
    # content collapse into one extraction run. This is why the number Stage 3
    # opens with can still be smaller than "eligible" above, even with the
    # quality gate already accounted for.
    print(
        f"\nOf the {fetch_metrics['eligible_for_extraction']} eligible, how many are distinct content "
        f"(not just distinct URLs)?"
    )
    print(
        f"  Distinct content, actually dispatched: {fetch_metrics['dispatched_for_extraction']} "
        f"({fetch_metrics['dispatched_for_extraction_pct']}%)"
    )
    print(f"  Duplicate-content URLs dropped: {fetch_metrics['duplicate_urls_dropped']}")
    if fetch_metrics["duplicate_content_groups"]:
        print(
            "\n  Duplicate-content groups (kept URL -> URL(s) with identical content, not sent again):"
        )
        for g in fetch_metrics["duplicate_content_groups"]:
            print(f"    {g['kept_url']} <- {', '.join(g['duplicate_urls'])}")

    print(
        f"\nTotal pages actually sent to extraction: {fetch_metrics['dispatched_for_extraction']}"
    )
    return fetch_metrics


async def run_customers_pipeline(
    vendor, dataset, driver, vey, bfc, fetch_metrics, pinned_extractor_version=None
):
    VENDOR = vendor

    def get_raw_golden(dataset, vendor: str) -> list[str]:
        for sample in dataset:
            if sample.get("metadata", {}).get("product_name", "") == vendor:
                expected = sample.get("expected", {})
                customers = (
                    expected.get("customers", [])
                    if isinstance(expected, dict)
                    else getattr(expected, "customers", [])
                )
                return [c.get("name", c) if isinstance(c, dict) else c for c in customers]
        return []

    golden_raw = get_raw_golden(dataset, VENDOR)
    golden_evidenced = bfc.get_expected(
        dataset, VENDOR
    )  # filtered to entries with a supporting_evidence citation

    print(f"Golden-set size: {len(golden_raw)}")
    print(
        f"  {len(golden_evidenced)} have a citation, {len(golden_raw) - len(golden_evidenced)} don't: "
        f"{sorted(set(golden_raw) - set(golden_evidenced))}"
    )

    from ai_internship.enrichment.profiles import extractor_version as compute_extractor_version

    metrics = await vey.compute_vendor_metrics(driver, VENDOR)
    customers_profile = metrics["profiles"]["customers"]

    # This section is the LUNA baseline specifically (ELI-1170 compares it
    # against a separate mini pass later in 1.14) -- pin to the luna model's
    # hash explicitly, not "most recent pass regardless of model". Once a
    # second model gets run for real comparison, its pass becomes the newest
    # timestamp, and "most recent" alone would silently swap this whole
    # section's baseline out from under it (confirmed live: re-running this
    # notebook after Suki's mini pass landed picked mini's pass here instead
    # of luna's, mislabeling every number in 1.1-1.13 as "luna"). Most-recent
    # tie-breaking still applies, just scoped to luna's own passes only.
    # ELI-1228 (Codex review, PR #45): pinned_extractor_version lets a caller pin
    # this to a specific historical hash instead of re-resolving live -- otherwise
    # re-running this notebook after the prompts are edited again silently swaps
    # in whatever's newest (or crashes on timestamp_rows[0] if nothing's run yet
    # under the fresh hash), breaking reproducibility of a committed results
    # notebook. Defaults to None (unchanged live-lookup behavior) so ELI-1170's
    # own notebook, which doesn't pass this, is unaffected.
    PRODUCTION_MODEL = "openai/gpt-5.6-luna"
    LUNA_CUSTOMERS_HASH = pinned_extractor_version or await compute_extractor_version(
        "customers", model=PRODUCTION_MODEL
    )
    timestamp_rows, _, _ = await driver.execute_query(
        "MATCH (t:ResearchTarget {name: $vendor})-[:TRACKS]->(f:FrontierUrl) "
        "WHERE f.origin = 'first_party' AND f.status = 'fetched' "
        "MATCH (s:RetainedSource {source_id: f.latest_source_id}) "
        "MATCH (run:ExtractionRun)-[:EXTRACTED]->(s) "
        "WHERE run.profile = 'customers' AND run.outcome <> 'running' AND run.target_id = t.target_id "
        "  AND run.extractor_version = $luna_hash "
        "RETURN run.extractor_version AS ev, max(run.completed_at) AS latest "
        "ORDER BY latest DESC",
        vendor=VENDOR,
        luna_hash=LUNA_CUSTOMERS_HASH,
    )
    PRIMARY_EXTRACTOR_VERSION = timestamp_rows[0]["ev"]
    primary_variant = next(
        v
        for v in customers_profile["extractor_versions"]
        if v["extractor_version"] == PRIMARY_EXTRACTOR_VERSION
    )
    LUNA_HASH = LUNA_CUSTOMERS_HASH[:8]
    PRIMARY_MODEL = (
        "openai/gpt-5.6-luna"
        if primary_variant["extractor_version_label"] == LUNA_HASH
        else "unknown"
    )

    print(f"Model: {PRIMARY_MODEL}")

    rows, _, _ = await driver.execute_query(vey._VARIANT_QUERY, vendor=VENDOR)
    rows = [
        dict(r)
        for r in rows
        if r["profile"] == "customers" and r["extractor_version"] == PRIMARY_EXTRACTOR_VERSION
    ]

    succeeded_ids = [r["extraction_id"] for r in rows if r["outcome"] == "succeeded"]
    edges_by_extraction_id = {}
    if succeeded_ids:
        edge_rows, _, _ = await driver.execute_query(vey._EDGES_QUERY, extraction_ids=succeeded_ids)
        for r in edge_rows:
            edges_by_extraction_id.setdefault(r["extraction_id"], []).append(dict(r))

    # customers_profile["sources_attempted"]/["sources_useful"] are UNIONED
    # across every extractor_version variant this vendor has ever run for this
    # profile (compute_vendor_metrics' own definition) -- NOT scoped to just
    # this primary pass. A vendor with more than one customers pass (Suki has
    # 3) can have that union disagree with what this single pass actually did:
    # confirmed live, Suki's union said 119 "useful" pages, a number that
    # doesn't actually back the candidate-check funnel below, which only ever
    # looks at this primary pass's own rows. Recomputed here from `rows`
    # directly so every number below is guaranteed to share the same scope.
    primary_pages_attempted = {r["source_id"] for r in rows}
    primary_pages_useful = {
        r["source_id"]
        for r in rows
        if (r["outcome"] == "succeeded" and edges_by_extraction_id.get(r["extraction_id"]))
        or json.loads(r["grounding_rejections_json"] or "[]")
    }

    match = len(primary_pages_attempted) == fetch_metrics["dispatched_for_extraction"]
    print(
        f"Pages with an extraction run: {len(primary_pages_attempted)} "
        f"(matches Stage 2's dispatched-for-extraction count: {match})"
    )
    print(
        f"Of those, {len(primary_pages_useful)} produced a candidate fact "
        f"({100 * len(primary_pages_useful) / len(primary_pages_attempted):.1f}%)"
    )

    accepted_count = sum(
        len(edges_by_extraction_id.get(r["extraction_id"], []))
        for r in rows
        if r["outcome"] == "succeeded"
    )
    rejected_count = sum(len(json.loads(r["grounding_rejections_json"] or "[]")) for r in rows)
    total_candidates = accepted_count + rejected_count
    # Exactly how these productive pages turn into total_candidates (a page can
    # yield more than one) is shown as a visual step in the funnel below --
    # these two counts are just the raw kept/rejected split.

    from collections import Counter

    from IPython.display import HTML, display

    GOOD, BAD, ACCENT = "#0ca30c", "#d03b3b", "#2a78d6"
    BORDER = "1px solid rgba(128,128,128,0.35)"
    ROW_BORDER = "1px solid rgba(128,128,128,0.15)"
    MUTED = "#888888"

    def customer_name(subject, subject_type, obj, object_type):
        """The HealthSystem-typed endpoint of a customers-profile relation, whichever side it's on."""
        if subject_type == "HealthSystem":
            return subject
        if object_type == "HealthSystem":
            return obj
        return None

    golden_norm_set = {_normalize(n) for n in golden_raw}

    # One row per candidate check: a page mentions a possible customer, and
    # grounding checks it -- pass (kept) or fail (rejected).
    candidates = []
    for row in rows:
        if row["outcome"] == "succeeded":
            for edge in edges_by_extraction_id.get(row["extraction_id"], []):
                cust = customer_name(
                    edge["subject_name"],
                    edge["subject_type"],
                    edge["object_name"],
                    edge["object_type"],
                )
                candidates.append(
                    {
                        "kept": True,
                        "subject": edge["subject_name"],
                        "object": edge["object_name"],
                        "reason": None,
                        "customer": cust,
                        "url": row["url"],
                    }
                )
        for rejection in json.loads(row["grounding_rejections_json"] or "[]"):
            a = rejection.get("assertion", {})
            cust = customer_name(
                a.get("subject"), a.get("subject_type"), a.get("object"), a.get("object_type")
            )
            candidates.append(
                {
                    "kept": False,
                    "subject": a.get("subject"),
                    "object": a.get("object"),
                    "reason": rejection.get("code"),
                    "customer": cust,
                    "url": row["url"],
                }
            )

    # What "failed" actually means, in plain English -- keyed to the exact
    # grounding_rejection_reason codes (enrichment/grounding.py) so this never
    # drifts from what the check really tested.
    REASON_MEANING = {
        "relationship_not_actually_happened": f"the page never actually said this org uses {VENDOR}",
        "relationship_claim_not_supported": "the page only hinted at it, never said it outright",
        "subject_not_real_organization": 'the "customer" wasn\'t a real, named company',
        "quote_not_in_source": "the quote grounding checked against isn't in the page at all",
    }
    reason_counts = Counter(c["reason"] for c in candidates if c["reason"])
    reason_caption = "; ".join(
        f"{n} {REASON_MEANING.get(r, r)}" for r, n in reason_counts.most_common()
    )

    # The same customer can be checked on more than one page. Group ALL 135
    # checks (passed and failed together) by customer name -- this is NOT a
    # further filter on the checks that passed, it's a re-group of every check
    # by a DIFFERENT thing (who it's about, not whether it passed). A customer's
    # edge exists in the graph if it passed on at least one page, so a customer
    # with even one passing check counts as kept overall, even if it failed on
    # other pages.
    groups = {}
    for c in candidates:
        key = (
            _normalize(c["customer"]) if c["customer"] else f"__none__{c['subject']}|{c['object']}"
        )
        groups.setdefault(key, []).append(c)

    same_kept, same_rejected, mixed = [], [], []
    for group in groups.values():
        kepts = [c["kept"] for c in group]
        if all(kepts):
            same_kept.append(group)
        elif not any(kepts):
            same_rejected.append(group)
        else:
            mixed.append(group)

    def raw_counts(groups_list):
        kept_n = sum(1 for g in groups_list for c in g if c["kept"])
        rej_n = sum(1 for g in groups_list for c in g if not c["kept"])
        return kept_n, rej_n

    sk_kept, sk_rej = raw_counts(same_kept)
    sr_kept, sr_rej = raw_counts(same_rejected)
    mx_kept, mx_rej = raw_counts(mixed)

    unique_kept = [
        {
            "customer": g[0]["customer"],
            "subject": g[0]["subject"],
            "object": g[0]["object"],
            "reason": None,
            "url": g[0]["url"],
            "raw_count": len(g),
            "kept": True,
            "is_golden": bool(g[0]["customer"]) and _normalize(g[0]["customer"]) in golden_norm_set,
        }
        for g in same_kept + mixed
    ]
    unique_rejected = [
        {
            "customer": g[0]["customer"],
            "subject": g[0]["subject"],
            "object": g[0]["object"],
            # Pick the reason and URL from the same rejected check -- the
            # page that actually produced the reason shown, not just
            # whichever check happened to be first in the group.
            "reason": next((c["reason"] for c in g if c["reason"]), None),
            "url": next((c["url"] for c in g if c["reason"]), g[0]["url"]),
            "raw_count": len(g),
            "kept": False,
            "is_golden": bool(g[0]["customer"]) and _normalize(g[0]["customer"]) in golden_norm_set,
        }
        for g in same_rejected
    ]
    unique_candidates = unique_kept + unique_rejected

    def box(label, n, color, caption, width="auto"):
        return f"""
        <div style="border:{BORDER}; border-top:4px solid {color}; border-radius:6px; padding:10px 16px;
                    text-align:center; {"flex:1;" if width == "auto" else f"width:{width};"}">
          <div style="font-size:24px; font-weight:700; color:{color};">{n}</div>
          <div style="font-size:11px; color:{MUTED}; font-weight:600;">{label}</div>
          <div style="font-size:11px; color:{MUTED}; margin-top:4px;">{caption}</div>
        </div>"""

    def arrow(text):
        return f"""
        <div style="text-align:center; font-size:11px; color:{MUTED}; margin:4px 0; font-style:italic;">
          &#9660; {text}
        </div>"""

    def section_label(text, color):
        return (
            f'<div style="font-size:11px; font-weight:700; color:{color}; text-transform:uppercase; '
            f'letter-spacing:0.05em; margin:16px 0 6px;">{text}</div>'
        )

    sequence_html = f"""
    <div style="font-family: system-ui, sans-serif; max-width:640px;">
      {section_label("From fetch to candidates", ACCENT)}
      {
        box(
            "productive pages",
            len(primary_pages_useful),
            ACCENT,
            f"of {len(primary_pages_attempted)} pages with an extraction run, these produced &ge;1 candidate",
            width="100%",
        )
    }
      {arrow("a single page can produce more than one candidate check")}
      {
        section_label(
            "Raw checks &mdash; NOT deduplicated (one page mention = one check, same customer can appear more than once)",
            ACCENT,
        )
    }
      {
        box(
            "candidate checks",
            len(candidates),
            ACCENT,
            "a page seems to mention a customer &mdash; one check per mention",
            width="100%",
        )
    }
      {arrow("each check either passes or fails")}
      <div style="display:flex; gap:10px;">
        {
        box("checks that passed", accepted_count, GOOD, "the page text actually backs up the claim")
    }
        {
        box(
            "checks that failed",
            rejected_count,
            BAD,
            f"the claim wasn't backed up: {reason_caption}",
        )
    }
      </div>

      {
        section_label(
            "Deduplicated &mdash; grouped by customer name, regardless of pass/fail", ACCENT
        )
    }
      {
        arrow(
            f"group ALL {len(candidates)} checks above (passed AND failed together) by customer name"
        )
    }
      {
        box(
            "distinct customers checked",
            len(groups),
            ACCENT,
            "same customer name counted once, no matter how many pages mentioned it",
            width="100%",
        )
    }
      {
        arrow(
            f"a customer counts as kept if it passed on at least one page ({len(mixed)} customers passed on one page, failed on another, and still count as kept)"
        )
    }
      <div style="display:flex; gap:10px;">
        {box("kept", len(unique_kept), GOOD, "this customer now has a real edge in the graph")}
        {box("rejected", len(unique_rejected), BAD, "never passed a single check, on any page")}
      </div>
    </div>
    """
    display(HTML(sequence_html))

    detail_html = f"""
    <div style="font-family: system-ui, sans-serif; max-width:720px; margin-top:14px;">
      <div style="font-size:11px; color:{MUTED}; margin-bottom:6px;">Deduplicated customers, broken down by consistency across their checks:</div>
      <div style="display:flex; gap:10px;">
        <div style="border:{BORDER}; border-top:4px solid {GOOD}; border-radius:6px; padding:12px 14px; flex:1;">
          <div style="font-size:11px; font-weight:600; color:{GOOD};">PASSED EVERY CHECK</div>
          <div style="font-size:22px; font-weight:700; margin:2px 0;">{len(same_kept)}</div>
          <div style="font-size:12px; color:{MUTED};">{sk_kept} raw checks, all passed</div>
        </div>
        <div style="border:{BORDER}; border-top:4px solid {BAD}; border-radius:6px; padding:12px 14px; flex:1;">
          <div style="font-size:11px; font-weight:600; color:{BAD};">FAILED EVERY CHECK</div>
          <div style="font-size:22px; font-weight:700; margin:2px 0;">{len(same_rejected)}</div>
          <div style="font-size:12px; color:{MUTED};">{sr_rej} raw checks, all failed</div>
        </div>
        <div style="border:{BORDER}; border-top:4px solid {ACCENT}; border-radius:6px; padding:12px 14px; flex:1;">
          <div style="font-size:11px; font-weight:600; color:{ACCENT};">PASSED SOME, FAILED OTHERS</div>
          <div style="font-size:22px; font-weight:700; margin:2px 0;">{len(mixed)}</div>
          <div style="font-size:12px; color:{MUTED};">{mx_kept} raw checks passed, {mx_rej} failed &rarr; still counts as kept</div>
        </div>
      </div>
    </div>
    """
    display(HTML(detail_html))

    def relation_list(items):
        rows_html = "".join(
            f'<div style="padding:3px 0; border-bottom:{ROW_BORDER}; font-family:ui-monospace,monospace; font-size:12px;">'
            f"{c['subject']} &rarr; {c['object']}"
            + (f" &times;{c['raw_count']}" if c["raw_count"] > 1 else "")
            + (
                f' <span style="color:{MUTED};">[{REASON_MEANING.get(c["reason"], c["reason"])}]</span>'
                f' &mdash; <a href="{c["url"]}" target="_blank" rel="noopener noreferrer" style="color:{ACCENT};">source</a>'
                if c["reason"]
                else ""
            )
            + "</div>"
            for c in items
        )
        return rows_html or "<i>none</i>"

    list_html = f"""
    <div style="font-family: system-ui, sans-serif; display:flex; gap:10px; max-width:760px; margin-top:10px;">
      <div style="border:{BORDER}; border-top:4px solid {GOOD}; border-radius:6px; padding:12px 14px; flex:1;">
        <div style="font-size:12px; font-weight:600; color:{GOOD};">KEPT &mdash; deduplicated customers ({len(unique_kept)})</div>
        <div style="max-height:220px; overflow-y:auto; margin-top:6px;">{relation_list(unique_kept)}</div>
      </div>
      <div style="border:{BORDER}; border-top:4px solid {BAD}; border-radius:6px; padding:12px 14px; flex:1;">
        <div style="font-size:12px; font-weight:600; color:{BAD};">REJECTED &mdash; deduplicated customers ({len(unique_rejected)})</div>
        <div style="max-height:220px; overflow-y:auto; margin-top:6px;">{relation_list(unique_rejected)}</div>
      </div>
    </div>
    """
    display(HTML(list_html))

    tp = [c for c in unique_candidates if c["kept"] and c["is_golden"]]
    fp = [c for c in unique_candidates if c["kept"] and not c["is_golden"]]
    fn = [c for c in unique_candidates if not c["kept"] and c["is_golden"]]
    tn = [c for c in unique_candidates if not c["kept"] and not c["is_golden"]]

    def cell(tag, icon, color, title, items, show_reason):
        rows = "".join(
            f'<div style="padding:3px 0; border-bottom:{ROW_BORDER}; font-family:ui-monospace,monospace; font-size:12px;">'
            f"{c['subject']} &rarr; {c['object']}"
            + (
                f' <span style="color:{MUTED};">[{REASON_MEANING.get(c["reason"], c["reason"])}]</span>'
                f' &mdash; <a href="{c["url"]}" target="_blank" rel="noopener noreferrer" style="color:{ACCENT};">source</a>'
                if show_reason and c["reason"]
                else ""
            )
            + "</div>"
            for c in items
        )
        return f"""
        <div style="border:{BORDER}; border-top:4px solid {color}; border-radius:6px; padding:12px 14px;">
          <div style="font-size:12px; font-weight:600; color:{color};">{icon} {tag}</div>
          <div style="font-size:28px; font-weight:700; margin:2px 0;">{len(items)}</div>
          <div style="font-size:12px; color:{MUTED}; margin-bottom:8px;">{title}</div>
          <details><summary style="cursor:pointer; font-size:12px; color:{ACCENT};">show relations</summary>
            <div style="max-height:220px; overflow-y:auto; margin-top:6px;">{rows or "<i>none</i>"}</div>
          </details>
        </div>"""

    grid_html = f"""
    <div style="font-family: system-ui, sans-serif;">
      <div style="font-size:11px; color:{MUTED}; margin-bottom:8px;">All 4 counts below are deduplicated customers (from the {len(unique_candidates)} in the previous step), not raw checks.</div>
      <div style="display:grid; grid-template-columns: 90px 1fr 1fr; gap:10px; align-items:stretch; max-width:760px;">
        <div></div>
        <div style="text-align:center; font-size:12px; font-weight:600; color:{MUTED};">KEPT</div>
        <div style="text-align:center; font-size:12px; font-weight:600; color:{MUTED};">REJECTED</div>

        <div style="display:flex; align-items:center; justify-content:flex-end; font-size:12px; font-weight:600; color:{MUTED}; padding-right:6px;">GOLDEN MATCH</div>
        {cell("TP", "&#10003;", GOOD, "properly accepted", tp, show_reason=False)}
        {cell("FN", "&#10007;", BAD, "wrongly rejected", fn, show_reason=True)}

        <div style="display:flex; align-items:center; justify-content:flex-end; font-size:12px; font-weight:600; color:{MUTED}; padding-right:6px;">NO GOLDEN MATCH</div>
        {cell("FP", "&#10007;", BAD, "wrongly accepted (unconfirmed, not proven fake)", fp, show_reason=False)}
        {cell("TN", "&#10003;", GOOD, "properly rejected", tn, show_reason=True)}
      </div>
    </div>
    """
    display(HTML(grid_html))

    # No F1/precision/recall here on purpose -- any score built from these 4
    # counts would be measured against "customers the model happened to
    # propose," not the real 20-name golden set, so it's gameable (proposing
    # fewer candidates could raise it) and not a real performance number. The
    # stat tiles below give the real, ground-truth F1/precision/recall.

    from IPython.display import HTML, display

    # Scoped to THIS pass (unique_kept, from Stage 3) -- not a raw Neo4j query,
    # which can include stale active edges left over from an older extraction
    # pass that this run's grounding never even looked at.
    extracted = {c["customer"] for c in unique_kept if c["customer"]}

    golden_norm = {_normalize(n) for n in golden_raw}
    extracted_norm = {_normalize(n) for n in extracted}
    raw_golden_by_norm = {_normalize(n): n for n in golden_raw}
    raw_extracted_by_norm = {_normalize(n): n for n in extracted}

    matched_norm = golden_norm & extracted_norm
    missed_norm = golden_norm - extracted_norm
    extra_norm = extracted_norm - golden_norm

    real_recall = _multilabel_recall(list(golden_norm), list(extracted_norm))
    real_precision = _multilabel_precision(list(golden_norm), list(extracted_norm))
    real_f1 = (
        0.0
        if (real_precision + real_recall) == 0
        else 2 * real_precision * real_recall / (real_precision + real_recall)
    )

    NEUTRAL = "#eda100"

    def stat(label, value, color):
        return f"""
        <div style="border:{BORDER}; border-top:4px solid {color}; border-radius:6px; padding:14px 16px; text-align:center; flex:1;">
          <div style="font-size:32px; font-weight:700; color:{color};">{value}</div>
          <div style="font-size:12px; color:{MUTED}; margin-top:2px;">{label}</div>
        </div>"""

    def bucket(tag, color, items, by_norm):
        names = [by_norm[n] for n in sorted(items)]
        rows = "".join(
            f'<div style="padding:3px 0; border-bottom:{ROW_BORDER}; font-family:ui-monospace,monospace; font-size:12px;">{n}</div>'
            for n in names
        )
        return f"""
        <div style="border:{BORDER}; border-top:4px solid {color}; border-radius:6px; padding:12px 14px; flex:1;">
          <div style="font-size:12px; font-weight:600; color:{color};">{tag}</div>
          <div style="font-size:24px; font-weight:700; margin:2px 0;">{len(names)}</div>
          <details><summary style="cursor:pointer; font-size:12px; color:{ACCENT};">show names</summary>
            <div style="max-height:180px; overflow-y:auto; margin-top:6px;">{rows or "<i>none</i>"}</div>
          </details>
        </div>"""

    html = f"""
    <div style="font-family: system-ui, sans-serif; max-width:760px;">
      <div style="display:flex; gap:10px; margin-bottom:14px;">
        {stat("F1", f"{real_f1:.1%}", ACCENT)}
        {stat("Precision", f"{real_precision:.1%}", ACCENT)}
        {stat("Recall", f"{real_recall:.1%}", ACCENT)}
      </div>
      <div style="display:flex; gap:10px;">
        {bucket("MATCHED", GOOD, matched_norm, raw_golden_by_norm)}
        {bucket("MISSED", BAD, missed_norm, raw_golden_by_norm)}
        {bucket("EXTRA (not golden)", NEUTRAL, extra_norm, raw_extracted_by_norm)}
      </div>
    </div>
    """
    display(HTML(html))

    fetched_sources = await bfc.get_fetched_sources(driver, VENDOR)

    verified = []
    unverifiable = []
    for customer in golden_evidenced:
        needle = customer.lower()
        hit_url = next(
            (url for _, url, content in fetched_sources if needle in content.lower()), None
        )
        if hit_url:
            verified.append({"customer": customer, "url": hit_url})
        else:
            unverifiable.append(customer)

    verified_rows = "".join(
        f'<div style="padding:4px 0; border-bottom:{ROW_BORDER}; font-size:12px;">'
        f'<a href="{v["url"]}" target="_blank" rel="noopener noreferrer" style="color:{ACCENT}; text-decoration:none;">{v["customer"]}</a></div>'
        for v in verified
    )
    unverifiable_rows = "".join(
        f'<div style="padding:4px 0; border-bottom:{ROW_BORDER}; font-family:ui-monospace,monospace; font-size:12px;">{c}</div>'
        for c in unverifiable
    )

    # The headline is "14 of 20 genuinely appear on the vendor's site, evidenced" --
    # that's a property of the golden data, real regardless of our own tooling.
    # The 12/2 split below it is a DIFFERENT thing: how much of that 14 our own
    # fetch + substring search could independently re-confirm. Not finding one
    # isn't proof it's absent -- it's a limit of our search, not the ground
    # truth -- so it gets a quieter color, not a green/red pass-fail read.
    html = f"""
    <div style="font-family: system-ui, sans-serif; max-width:640px;">
      <div style="border:{BORDER}; border-top:4px solid {GOOD}; border-radius:6px; padding:16px 18px;">
        <div style="font-size:36px; font-weight:700; color:{GOOD};">{len(golden_evidenced)} <span style="font-size:16px; color:{MUTED}; font-weight:400;">of {len(golden_raw)} golden</span></div>
        <div style="font-size:13px; color:{MUTED}; margin-top:2px;">genuinely appear on {VENDOR}'s own website, backed by real evidence</div>
      </div>

      <div style="border:{BORDER}; border-top:4px solid {NEUTRAL}; border-radius:6px; padding:12px 16px; margin-top:12px;">
        <div style="font-size:11px; font-weight:600; color:{NEUTRAL};">OF THOSE {len(golden_evidenced)} &mdash; WHAT OUR OWN FETCH COULD RE-CONFIRM</div>
        <div style="display:flex; gap:20px; margin-top:8px;">
          <div><span style="font-size:20px; font-weight:700;">{len(verified)}</span> <span style="font-size:12px; color:{MUTED};">found in a fetched page</span></div>
          <div><span style="font-size:20px; font-weight:700;">{len(unverifiable)}</span> <span style="font-size:12px; color:{MUTED};">not found &mdash; fetch/search limitation, not proof they're absent</span></div>
        </div>
      </div>

      <div style="display:flex; gap:10px; margin-top:12px;">
        <div style="border:{BORDER}; border-radius:6px; padding:12px 14px; flex:1;">
          <div style="font-size:12px; font-weight:600; color:{MUTED};">FOUND ({len(verified)})</div>
          <div style="max-height:200px; overflow-y:auto; margin-top:6px;">{verified_rows}</div>
        </div>
        <div style="border:{BORDER}; border-radius:6px; padding:12px 14px; flex:1;">
          <div style="font-size:12px; font-weight:600; color:{MUTED};">NOT FOUND ({len(unverifiable)})</div>
          <div style="max-height:200px; overflow-y:auto; margin-top:6px;">{unverifiable_rows or "<i>none</i>"}</div>
        </div>
      </div>
    </div>
    """
    display(HTML(html))

    # Ground truth here is EVERY cited golden customer (golden_evidenced) --
    # not narrowed to just the subset Stage 5 could independently re-confirm.
    # Not finding a customer's name with a plain substring search isn't proof
    # it's absent from the site -- it could be a name-variant mismatch or a
    # page we never fetched, i.e. our own verification falling short, not proof
    # the customer is unfindable. Only golden entries with NO citation at all
    # (Braintrust itself has zero evidence for them) are excluded -- that's the
    # one exclusion that's actually justified.
    ceiling_truth_norm = {_normalize(n) for n in golden_evidenced}
    raw_ceiling_by_norm = {_normalize(n): n for n in golden_evidenced}

    ceiling_matched = ceiling_truth_norm & extracted_norm
    ceiling_missed = ceiling_truth_norm - extracted_norm
    ceiling_extra = extracted_norm - ceiling_truth_norm

    ceiling_recall = _multilabel_recall(list(ceiling_truth_norm), list(extracted_norm))
    ceiling_precision = _multilabel_precision(list(ceiling_truth_norm), list(extracted_norm))
    ceiling_f1 = (
        0.0
        if (ceiling_precision + ceiling_recall) == 0
        else 2 * ceiling_precision * ceiling_recall / (ceiling_precision + ceiling_recall)
    )

    html = f"""
    <div style="font-family: system-ui, sans-serif; max-width:760px;">
      <p style="font-size:12px; color:{MUTED}; margin-bottom:10px;">
        Scored against all {len(golden_evidenced)} cited golden customers -- including the 2 Stage 5 couldn't
        re-confirm in the fetched content. Only the {len(golden_raw) - len(golden_evidenced)} golden entries with
        no citation at all are excluded.
      </p>
      <div style="display:flex; gap:10px; margin-bottom:14px;">
        {stat("F1", f"{ceiling_f1:.1%}", ACCENT)}
        {stat("Precision", f"{ceiling_precision:.1%}", ACCENT)}
        {stat("Recall", f"{ceiling_recall:.1%}", ACCENT)}
      </div>
      <div style="display:flex; gap:10px;">
        {bucket("MATCHED", GOOD, ceiling_matched, raw_ceiling_by_norm)}
        {bucket("MISSED", BAD, ceiling_missed, raw_ceiling_by_norm)}
        {bucket("EXTRA (not in the 14)", NEUTRAL, ceiling_extra, raw_extracted_by_norm)}
      </div>
    </div>
    """
    display(HTML(html))

    from IPython.display import HTML, display

    def summary_card(label, color, value, caption):
        return f"""
        <div style="border:{BORDER}; border-top:4px solid {color}; border-radius:6px; padding:14px 16px;">
          <div style="font-size:11px; font-weight:600; color:{color}; letter-spacing:0.02em;">{label}</div>
          <div style="font-size:22px; font-weight:700; margin:4px 0 2px;">{value}</div>
          <div style="font-size:12px; color:{MUTED};">{caption}</div>
        </div>"""

    summary_html = f"""
    <div style="font-family: system-ui, sans-serif; max-width:820px;">
      <div style="display:grid; grid-template-columns: repeat(3, 1fr); gap:10px;">
        {
        summary_card(
            "1 &middot; GOLDEN SET",
            MUTED,
            len(golden_raw),
            f"{len(golden_evidenced)} cited, {len(golden_raw) - len(golden_evidenced)} uncited",
        )
    }
        {
        summary_card(
            "2 &middot; FETCH &amp; USE (ELI-1135)",
            ACCENT,
            f"{fetch_metrics['used']}/{fetch_metrics['total_urls']} used ({fetch_metrics['used_pct']}%)",
            f"of {fetch_metrics['total_urls']} URLs looked at: {fetch_metrics['excluded']} excluded, "
            f"{fetch_metrics['errored']} errored, {fetch_metrics['blocked']} blocked, "
            f"{fetch_metrics['clean']} clean",
        )
    }
        {
        summary_card(
            "3 &middot; GROUNDING (ELI-1136)",
            ACCENT,
            f"{len(unique_kept)}/{len(unique_candidates)} customers kept (deduplicated)",
            f"TP {len(tp)} &middot; FP {len(fp)} &middot; FN {len(fn)} &middot; TN {len(tn)} (all deduplicated customers)",
        )
    }
        {
        summary_card(
            "4 &middot; REAL F1 / P / R",
            GOOD,
            f"{real_f1:.0%} / {real_precision:.0%} / {real_recall:.0%}",
            f"vs. full {len(golden_raw)}-name golden set",
        )
    }
        {
        summary_card(
            "5 &middot; EVIDENCE-VERIFIED",
            GOOD,
            f"{len(golden_evidenced)}/{len(golden_raw)} on-site",
            f"{len(verified)} of those {len(golden_evidenced)} confirmed by us, {len(unverifiable)} unconfirmed (fetch limit)",
        )
    }
        {
        summary_card(
            "6 &middot; CEILING F1 / P / R",
            GOOD,
            f"{ceiling_f1:.0%} / {ceiling_precision:.0%} / {ceiling_recall:.0%}",
            f"vs. all {len(golden_evidenced)} cited golden",
        )
    }
      </div>
      <p style="font-size:12px; color:{MUTED}; margin-top:10px;">Model: {PRIMARY_MODEL}.</p>
    </div>
    """
    display(HTML(summary_html))
    return locals()


async def run_integrations_pipeline(
    vendor,
    integrations_dataset,
    driver,
    vey,
    bfc,
    metrics,
    fetch_metrics,
    pinned_extractor_version=None,
):
    VENDOR = vendor
    from IPython.display import HTML, display

    from ai_internship.enrichment.profiles import extractor_version as compute_extractor_version

    fetched_sources = await bfc.get_fetched_sources(driver, VENDOR)

    # cell() closes over REASON_MEANING, which is redefined per-profile
    # (correction #12's integrations-specific wording differs from customers')
    # -- it must be a fresh LOCAL definition here, not a shared global, or it
    # would look up whichever REASON_MEANING happened to be defined last in
    # some OTHER function's scope instead of this one's.
    def cell(tag, icon, color, title, items, show_reason):
        rows = "".join(
            f'<div style="padding:3px 0; border-bottom:{ROW_BORDER}; font-family:ui-monospace,monospace; font-size:12px;">'
            f"{c['subject']} &rarr; {c['object']}"
            + (
                f' <span style="color:{MUTED};">[{REASON_MEANING.get(c["reason"], c["reason"])}]</span>'
                f' &mdash; <a href="{c["url"]}" target="_blank" rel="noopener noreferrer" style="color:{ACCENT};">source</a>'
                if show_reason and c["reason"]
                else ""
            )
            + "</div>"
            for c in items
        )
        return f"""
        <div style="border:{BORDER}; border-top:4px solid {color}; border-radius:6px; padding:12px 14px;">
          <div style="font-size:12px; font-weight:600; color:{color};">{icon} {tag}</div>
          <div style="font-size:28px; font-weight:700; margin:2px 0;">{len(items)}</div>
          <div style="font-size:12px; color:{MUTED}; margin-bottom:8px;">{title}</div>
          <details><summary style="cursor:pointer; font-size:12px; color:{ACCENT};">show relations</summary>
            <div style="max-height:220px; overflow-y:auto; margin-top:6px;">{rows or "<i>none</i>"}</div>
          </details>
        </div>"""

    def get_raw_golden_integrations(dataset, vendor: str) -> list[str]:
        for sample in dataset:
            if sample.get("metadata", {}).get("product_name", "") == vendor:
                expected = sample.get("expected", {})
                integrations = (
                    expected.get("integrations", [])
                    if isinstance(expected, dict)
                    else getattr(expected, "integrations", [])
                )
                return [i.get("name", i) if isinstance(i, dict) else i for i in integrations]
        return []

    def get_expected_integrations(dataset, vendor: str) -> list[str]:
        """Same supporting_evidence filter as build_frozen_case_set.get_expected, applied to integrations."""
        for sample in dataset:
            if sample.get("metadata", {}).get("product_name", "") == vendor:
                expected = sample.get("expected", {})
                integrations = (
                    expected.get("integrations", [])
                    if isinstance(expected, dict)
                    else getattr(expected, "integrations", [])
                )
                names = []
                for i in integrations:
                    if not isinstance(i, dict):
                        names.append(i)
                        continue
                    if i.get("supporting_evidence"):
                        names.append(i.get("name", i))
                return names
        return []

    integrations_golden_raw = get_raw_golden_integrations(integrations_dataset, VENDOR)
    integrations_golden_evidenced = get_expected_integrations(integrations_dataset, VENDOR)

    print(f"Golden-set size: {len(integrations_golden_raw)}")
    print(
        f"  {len(integrations_golden_evidenced)} have a citation, {len(integrations_golden_raw) - len(integrations_golden_evidenced)} don't: "
        f"{sorted(set(integrations_golden_raw) - set(integrations_golden_evidenced))}"
    )

    integrations_profile = metrics["profiles"]["integrations"]

    # This section is the LUNA baseline (ELI-1170's mini comparison lives in
    # 1.14) -- pin to the luna model's hash explicitly, not "most recent pass
    # regardless of model". Once a second model gets run, its pass becomes the
    # newest timestamp, and "most recent" alone would silently swap this whole
    # section's baseline out from under it (same bug already confirmed live on
    # the customers section above). Most-recent tie-breaking (correction #1)
    # still applies, just scoped to luna's own passes only.
    # ELI-1228 (Codex review, PR #45): same pinning as run_customers_pipeline
    # above -- defaults to None (unchanged live-lookup behavior) so ELI-1170's
    # own notebook is unaffected.
    PRODUCTION_MODEL = "openai/gpt-5.6-luna"
    LUNA_INTEGRATIONS_HASH = pinned_extractor_version or await compute_extractor_version(
        "integrations", model=PRODUCTION_MODEL
    )
    timestamp_rows, _, _ = await driver.execute_query(
        "MATCH (t:ResearchTarget {name: $vendor})-[:TRACKS]->(f:FrontierUrl) "
        "WHERE f.origin = 'first_party' AND f.status = 'fetched' "
        "MATCH (s:RetainedSource {source_id: f.latest_source_id}) "
        "MATCH (run:ExtractionRun)-[:EXTRACTED]->(s) "
        "WHERE run.profile = 'integrations' AND run.outcome <> 'running' AND run.target_id = t.target_id "
        "  AND run.extractor_version = $luna_hash "
        "RETURN run.extractor_version AS ev, max(run.completed_at) AS latest "
        "ORDER BY latest DESC",
        vendor=VENDOR,
        luna_hash=LUNA_INTEGRATIONS_HASH,
    )
    INTEGRATIONS_PRIMARY_EXTRACTOR_VERSION = timestamp_rows[0]["ev"]
    integrations_primary_variant = next(
        v
        for v in integrations_profile["extractor_versions"]
        if v["extractor_version"] == INTEGRATIONS_PRIMARY_EXTRACTOR_VERSION
    )

    INTEGRATIONS_LUNA_HASH = LUNA_INTEGRATIONS_HASH[:8]
    INTEGRATIONS_PRIMARY_MODEL = (
        "openai/gpt-5.6-luna"
        if integrations_primary_variant["extractor_version_label"] == INTEGRATIONS_LUNA_HASH
        else "unknown"
    )

    print(f"Model: {INTEGRATIONS_PRIMARY_MODEL}")
    print(
        f"Extraction passes on record: {len(integrations_profile['extractor_versions'])} "
        f"(picked {INTEGRATIONS_PRIMARY_EXTRACTOR_VERSION[:8]}, most recent luna pass by completed_at -- page count ties)"
    )

    integrations_match = (
        integrations_primary_variant["sources_attempted"]
        == fetch_metrics["dispatched_for_extraction"]
    )
    print(
        f"Pages with an extraction run: {integrations_primary_variant['sources_attempted']} "
        f"(matches Stage 1.1's dispatched-for-extraction count: {integrations_match})"
    )
    print(
        f"Of those, {integrations_primary_variant['products_extracted']} distinct products found "
        f"across {integrations_primary_variant['relations_extracted']} pre-grounding relation candidates"
    )

    integrations_rows, _, _ = await driver.execute_query(vey._VARIANT_QUERY, vendor=VENDOR)
    integrations_rows = [
        dict(r)
        for r in integrations_rows
        if r["profile"] == "integrations"
        and r["extractor_version"] == INTEGRATIONS_PRIMARY_EXTRACTOR_VERSION
    ]

    integrations_succeeded_ids = [
        r["extraction_id"] for r in integrations_rows if r["outcome"] == "succeeded"
    ]
    integrations_edges_by_extraction_id = {}
    if integrations_succeeded_ids:
        integrations_edge_rows, _, _ = await driver.execute_query(
            vey._EDGES_QUERY, extraction_ids=integrations_succeeded_ids
        )
        for r in integrations_edge_rows:
            integrations_edges_by_extraction_id.setdefault(r["extraction_id"], []).append(dict(r))

    integrations_accepted_count = sum(
        len(integrations_edges_by_extraction_id.get(r["extraction_id"], []))
        for r in integrations_rows
        if r["outcome"] == "succeeded"
    )
    integrations_rejected_count = sum(
        len(json.loads(r["grounding_rejections_json"] or "[]")) for r in integrations_rows
    )

    from collections import Counter

    def integrated_product_name(subject, obj):
        """Correction #10: the integrations schema (enrichment/profiles.py::
        _to_assertions) always maps item.product -> subject, item.integrated_product
        -> object, by FIXED FIELD POSITION -- so `object` is always "the thing
        being integrated with," regardless of what name `subject` says."""
        return obj

    # Correction #12: integrations-flavored rejection wording -- the 3 old regex
    # codes (target_not_endpoint/subject_not_in_evidence/object_not_in_evidence)
    # don't exist for this profile anymore (ELI-1195), and the customers wording
    # ("the page never said this org uses X") doesn't fit a product-to-product
    # relationship.
    REASON_MEANING = {
        "relationship_not_actually_happened": f"the page never actually said {VENDOR} integrates with this product",
        "relationship_claim_not_supported": "the page only hinted at an integration, never stated one outright",
        "quote_not_in_source": "the quote grounding checked against isn't in the page at all",
        "incomplete_relationship": "the extracted relationship was missing a required field",
        "unnamed_subject": f'the "{VENDOR}-side" product wasn\'t a real, named product',
        "unnamed_object": 'the "integrated-with" product wasn\'t a real, named product',
        "meta_commentary_quote": "the quote was describing the extraction task itself, not the page's actual content",
        "self_edge": f"{VENDOR} was extracted as integrating with itself",
        "profile_shape_mismatch": "the subject/object types didn't match what an integration relationship should look like",
        "target_as_integration_partner": f"the page named a compound brand joining {VENDOR}'s own name to this product, not a real third party",
        "integration_partner_not_third_party": f"the model judged this to be {VENDOR}'s own product or sub-brand, not an independent third party",
        "integration_partner_is_backend_infrastructure": f"the model judged this to be backend infrastructure {VENDOR} built its own product on top of, not a customer-facing integration",
        "integration_partner_not_specific_entity": 'the model judged this to be a generic category (e.g. "EHR", "practice management system"), not a specific named product',
    }

    integrations_golden_norm_set = {_normalize(n) for n in integrations_golden_raw}

    integrations_candidates = []
    for row in integrations_rows:
        if row["outcome"] == "succeeded":
            for edge in integrations_edges_by_extraction_id.get(row["extraction_id"], []):
                prod = integrated_product_name(edge["subject_name"], edge["object_name"])
                integrations_candidates.append(
                    {
                        "kept": True,
                        "subject": edge["subject_name"],
                        "object": edge["object_name"],
                        "reason": None,
                        "product": prod,
                        "url": row["url"],
                    }
                )
        for rejection in json.loads(row["grounding_rejections_json"] or "[]"):
            a = rejection.get("assertion", {})
            prod = integrated_product_name(a.get("subject"), a.get("object"))
            integrations_candidates.append(
                {
                    "kept": False,
                    "subject": a.get("subject"),
                    "object": a.get("object"),
                    "reason": rejection.get("code"),
                    "product": prod,
                    "url": row["url"],
                }
            )

    integrations_reason_counts = Counter(
        c["reason"] for c in integrations_candidates if c["reason"]
    )
    integrations_reason_caption = "; ".join(
        f"{n} {REASON_MEANING.get(r, r)}" for r, n in integrations_reason_counts.most_common()
    )

    integrations_groups = {}
    for c in integrations_candidates:
        key = _normalize(c["product"]) if c["product"] else f"__none__{c['subject']}|{c['object']}"
        integrations_groups.setdefault(key, []).append(c)

    integrations_same_kept, integrations_same_rejected, integrations_mixed = [], [], []
    for group in integrations_groups.values():
        kepts = [c["kept"] for c in group]
        if all(kepts):
            integrations_same_kept.append(group)
        elif not any(kepts):
            integrations_same_rejected.append(group)
        else:
            integrations_mixed.append(group)

    def integrations_raw_counts(groups_list):
        kept_n = sum(1 for g in groups_list for c in g if c["kept"])
        rej_n = sum(1 for g in groups_list for c in g if not c["kept"])
        return kept_n, rej_n

    isk_kept, isk_rej = integrations_raw_counts(integrations_same_kept)
    isr_kept, isr_rej = integrations_raw_counts(integrations_same_rejected)
    imx_kept, imx_rej = integrations_raw_counts(integrations_mixed)

    integrations_unique_kept = [
        {
            "product": g[0]["product"],
            "subject": g[0]["subject"],
            "object": g[0]["object"],
            "reason": None,
            "url": g[0]["url"],
            "raw_count": len(g),
            "kept": True,
            "is_golden": bool(g[0]["product"])
            and _normalize(g[0]["product"]) in integrations_golden_norm_set,
        }
        for g in integrations_same_kept + integrations_mixed
    ]
    integrations_unique_rejected = [
        {
            "product": g[0]["product"],
            "subject": g[0]["subject"],
            "object": g[0]["object"],
            # Pick the reason and URL from the same rejected check, not
            # just whichever check happened to be first in the group.
            "reason": next((c["reason"] for c in g if c["reason"]), None),
            "url": next((c["url"] for c in g if c["reason"]), g[0]["url"]),
            "raw_count": len(g),
            "kept": False,
            "is_golden": bool(g[0]["product"])
            and _normalize(g[0]["product"]) in integrations_golden_norm_set,
        }
        for g in integrations_same_rejected
    ]
    integrations_unique_candidates = integrations_unique_kept + integrations_unique_rejected

    def integrations_relation_list(items):
        rows_html = "".join(
            f'<div style="padding:3px 0; border-bottom:{ROW_BORDER}; font-family:ui-monospace,monospace; font-size:12px;">'
            f"{c['subject']} &rarr; {c['object']}"
            + (f" &times;{c['raw_count']}" if c["raw_count"] > 1 else "")
            + (
                f' <span style="color:{MUTED};">[{REASON_MEANING.get(c["reason"], c["reason"])}]</span>'
                f' &mdash; <a href="{c["url"]}" target="_blank" rel="noopener noreferrer" style="color:{ACCENT};">source</a>'
                if c["reason"]
                else ""
            )
            + "</div>"
            for c in items
        )
        return rows_html or "<i>none</i>"

    sequence_html = f"""
    <div style="font-family: system-ui, sans-serif; max-width:640px;">
      {
        section_label(
            "Raw checks &mdash; NOT deduplicated (one page mention = one check, same product can appear more than once)",
            ACCENT,
        )
    }
      {
        box(
            "candidate checks",
            len(integrations_candidates),
            ACCENT,
            "a page seems to mention an integration &mdash; one check per mention",
            width="100%",
        )
    }
      {arrow("each check either passes or fails")}
      <div style="display:flex; gap:10px;">
        {
        box(
            "checks that passed",
            integrations_accepted_count,
            GOOD,
            "the page text actually backs up the claim",
        )
    }
        {
        box(
            "checks that failed",
            integrations_rejected_count,
            BAD,
            f"the claim wasn't backed up: {integrations_reason_caption}",
        )
    }
      </div>

      {
        section_label(
            "Deduplicated &mdash; grouped by product name, regardless of pass/fail", ACCENT
        )
    }
      {
        arrow(
            f"group ALL {len(integrations_candidates)} checks above (passed AND failed together) by product name"
        )
    }
      {
        box(
            "distinct products checked",
            len(integrations_groups),
            ACCENT,
            "same product name counted once, no matter how many pages mentioned it",
            width="100%",
        )
    }
      {
        arrow(
            f"a product counts as kept if it passed on at least one page ({len(integrations_mixed)} products passed on one page, failed on another, and still count as kept)"
        )
    }
      <div style="display:flex; gap:10px;">
        {
        box(
            "kept",
            len(integrations_unique_kept),
            GOOD,
            "this product now has a real edge in the graph",
        )
    }
        {
        box(
            "rejected",
            len(integrations_unique_rejected),
            BAD,
            "never passed a single check, on any page",
        )
    }
      </div>
    </div>
    """
    display(HTML(sequence_html))

    detail_html = f"""
    <div style="font-family: system-ui, sans-serif; max-width:720px; margin-top:14px;">
      <div style="font-size:11px; color:{MUTED}; margin-bottom:6px;">Deduplicated products, broken down by consistency across their checks:</div>
      <div style="display:flex; gap:10px;">
        <div style="border:{BORDER}; border-top:4px solid {GOOD}; border-radius:6px; padding:12px 14px; flex:1;">
          <div style="font-size:11px; font-weight:600; color:{GOOD};">PASSED EVERY CHECK</div>
          <div style="font-size:22px; font-weight:700; margin:2px 0;">{len(integrations_same_kept)}</div>
          <div style="font-size:12px; color:{MUTED};">{isk_kept} raw checks, all passed</div>
        </div>
        <div style="border:{BORDER}; border-top:4px solid {BAD}; border-radius:6px; padding:12px 14px; flex:1;">
          <div style="font-size:11px; font-weight:600; color:{BAD};">FAILED EVERY CHECK</div>
          <div style="font-size:22px; font-weight:700; margin:2px 0;">{len(integrations_same_rejected)}</div>
          <div style="font-size:12px; color:{MUTED};">{isr_rej} raw checks, all failed</div>
        </div>
        <div style="border:{BORDER}; border-top:4px solid {ACCENT}; border-radius:6px; padding:12px 14px; flex:1;">
          <div style="font-size:11px; font-weight:600; color:{ACCENT};">PASSED SOME, FAILED OTHERS</div>
          <div style="font-size:22px; font-weight:700; margin:2px 0;">{len(integrations_mixed)}</div>
          <div style="font-size:12px; color:{MUTED};">{imx_kept} raw checks passed, {imx_rej} failed &rarr; still counts as kept</div>
        </div>
      </div>
    </div>
    """
    display(HTML(detail_html))

    list_html = f"""
    <div style="font-family: system-ui, sans-serif; display:flex; gap:10px; max-width:760px; margin-top:10px;">
      <div style="border:{BORDER}; border-top:4px solid {GOOD}; border-radius:6px; padding:12px 14px; flex:1;">
        <div style="font-size:12px; font-weight:600; color:{GOOD};">KEPT &mdash; deduplicated products ({len(integrations_unique_kept)})</div>
        <div style="max-height:220px; overflow-y:auto; margin-top:6px;">{integrations_relation_list(integrations_unique_kept)}</div>
      </div>
      <div style="border:{BORDER}; border-top:4px solid {BAD}; border-radius:6px; padding:12px 14px; flex:1;">
        <div style="font-size:12px; font-weight:600; color:{BAD};">REJECTED &mdash; deduplicated products ({len(integrations_unique_rejected)})</div>
        <div style="max-height:220px; overflow-y:auto; margin-top:6px;">{integrations_relation_list(integrations_unique_rejected)}</div>
      </div>
    </div>
    """
    display(HTML(list_html))

    integrations_tp = [c for c in integrations_unique_candidates if c["kept"] and c["is_golden"]]
    integrations_fp = [
        c for c in integrations_unique_candidates if c["kept"] and not c["is_golden"]
    ]
    integrations_fn = [
        c for c in integrations_unique_candidates if not c["kept"] and c["is_golden"]
    ]
    integrations_tn = [
        c for c in integrations_unique_candidates if not c["kept"] and not c["is_golden"]
    ]

    grid_html = f"""
    <div style="font-family: system-ui, sans-serif;">
      <div style="font-size:11px; color:{MUTED}; margin-bottom:8px;">All 4 counts below are deduplicated products (from the {len(integrations_unique_candidates)} in the previous step), not raw checks.</div>
      <div style="display:grid; grid-template-columns: 90px 1fr 1fr; gap:10px; align-items:stretch; max-width:760px;">
        <div></div>
        <div style="text-align:center; font-size:12px; font-weight:600; color:{MUTED};">KEPT</div>
        <div style="text-align:center; font-size:12px; font-weight:600; color:{MUTED};">REJECTED</div>

        <div style="display:flex; align-items:center; justify-content:flex-end; font-size:12px; font-weight:600; color:{MUTED}; padding-right:6px;">GOLDEN MATCH</div>
        {cell("TP", "&#10003;", GOOD, "properly accepted", integrations_tp, show_reason=False)}
        {cell("FN", "&#10007;", BAD, "wrongly rejected", integrations_fn, show_reason=True)}

        <div style="display:flex; align-items:center; justify-content:flex-end; font-size:12px; font-weight:600; color:{MUTED}; padding-right:6px;">NO GOLDEN MATCH</div>
        {cell("FP", "&#10007;", BAD, "wrongly accepted (unconfirmed, not proven fake)", integrations_fp, show_reason=False)}
        {cell("TN", "&#10003;", GOOD, "properly rejected", integrations_tn, show_reason=True)}
      </div>
    </div>
    """
    display(HTML(grid_html))

    # No F1/precision/recall here on purpose (correction #2) -- any score built
    # from these 4 counts would be measured against "products the model happened
    # to propose," not the real golden set, so it's gameable. Stage 1.10 gives
    # the real, ground-truth F1/precision/recall.

    integrations_extracted_raw = {c["product"] for c in integrations_unique_kept if c["product"]}

    # Plain name match at product level; no brand/product-family alias resolution.
    integrations_extracted_norm = {_normalize(n) for n in integrations_extracted_raw}
    integrations_raw_golden_by_norm = {_normalize(n): n for n in integrations_golden_raw}
    integrations_raw_extracted_by_norm = {_normalize(n): n for n in integrations_extracted_raw}

    integrations_raw_recall = _multilabel_recall(
        list(integrations_golden_norm_set), list(integrations_extracted_norm)
    )
    integrations_raw_precision = _multilabel_precision(
        list(integrations_golden_norm_set), list(integrations_extracted_norm)
    )
    integrations_raw_f1 = (
        0.0
        if (integrations_raw_precision + integrations_raw_recall) == 0
        else 2
        * integrations_raw_precision
        * integrations_raw_recall
        / (integrations_raw_precision + integrations_raw_recall)
    )

    integrations_matched = integrations_golden_norm_set & integrations_extracted_norm
    integrations_missed = integrations_golden_norm_set - integrations_extracted_norm
    integrations_extra = integrations_extracted_norm - integrations_golden_norm_set

    html = f"""
    <div style="font-family: system-ui, sans-serif; max-width:760px;">
      <div style="display:flex; gap:10px; margin-bottom:14px;">
        {stat("F1", f"{integrations_raw_f1:.1%}", ACCENT)}
        {stat("Precision", f"{integrations_raw_precision:.1%}", ACCENT)}
        {stat("Recall", f"{integrations_raw_recall:.1%}", ACCENT)}
      </div>
      <div style="display:flex; gap:10px;">
        {bucket("MATCHED", GOOD, integrations_matched, integrations_raw_golden_by_norm)}
        {bucket("MISSED", BAD, integrations_missed, integrations_raw_golden_by_norm)}
        {bucket("EXTRA (not golden)", NEUTRAL, integrations_extra, integrations_raw_extracted_by_norm)}
      </div>
    </div>
    """
    display(HTML(html))

    # fetched_sources is vendor-level (Stage 1.5's already-fetched pages) -- same
    # pages regardless of profile, so reused as-is, not refetched.
    integrations_verified = []
    integrations_unverifiable = []
    for product in integrations_golden_evidenced:
        needle = product.lower()
        hit_url = next(
            (url for _, url, content in fetched_sources if needle in content.lower()), None
        )
        if hit_url:
            integrations_verified.append({"product": product, "url": hit_url})
        else:
            integrations_unverifiable.append(product)

    integrations_verified_rows = "".join(
        f'<div style="padding:4px 0; border-bottom:{ROW_BORDER}; font-size:12px;">'
        f'<a href="{v["url"]}" target="_blank" rel="noopener noreferrer" style="color:{ACCENT}; text-decoration:none;">{v["product"]}</a></div>'
        for v in integrations_verified
    )
    integrations_unverifiable_rows = "".join(
        f'<div style="padding:4px 0; border-bottom:{ROW_BORDER}; font-family:ui-monospace,monospace; font-size:12px;">{c}</div>'
        for c in integrations_unverifiable
    )

    html = f"""
    <div style="font-family: system-ui, sans-serif; max-width:640px;">
      <div style="border:{BORDER}; border-top:4px solid {GOOD}; border-radius:6px; padding:16px 18px;">
        <div style="font-size:36px; font-weight:700; color:{GOOD};">{len(integrations_golden_evidenced)} <span style="font-size:16px; color:{MUTED}; font-weight:400;">of {len(integrations_golden_raw)} golden</span></div>
        <div style="font-size:13px; color:{MUTED}; margin-top:2px;">genuinely appear on {VENDOR}'s own website, backed by real evidence</div>
      </div>

      <div style="border:{BORDER}; border-top:4px solid {NEUTRAL}; border-radius:6px; padding:12px 16px; margin-top:12px;">
        <div style="font-size:11px; font-weight:600; color:{NEUTRAL};">OF THOSE {len(integrations_golden_evidenced)} &mdash; WHAT OUR OWN FETCH COULD RE-CONFIRM</div>
        <div style="display:flex; gap:20px; margin-top:8px;">
          <div><span style="font-size:20px; font-weight:700;">{len(integrations_verified)}</span> <span style="font-size:12px; color:{MUTED};">found in a fetched page</span></div>
          <div><span style="font-size:20px; font-weight:700;">{len(integrations_unverifiable)}</span> <span style="font-size:12px; color:{MUTED};">not found &mdash; fetch/search limitation, not proof they're absent</span></div>
        </div>
      </div>

      <div style="display:flex; gap:10px; margin-top:12px;">
        <div style="border:{BORDER}; border-radius:6px; padding:12px 14px; flex:1;">
          <div style="font-size:12px; font-weight:600; color:{MUTED};">FOUND ({len(integrations_verified)})</div>
          <div style="max-height:200px; overflow-y:auto; margin-top:6px;">{integrations_verified_rows}</div>
        </div>
        <div style="border:{BORDER}; border-radius:6px; padding:12px 14px; flex:1;">
          <div style="font-size:12px; font-weight:600; color:{MUTED};">NOT FOUND ({len(integrations_unverifiable)})</div>
          <div style="max-height:200px; overflow-y:auto; margin-top:6px;">{integrations_unverifiable_rows or "<i>none</i>"}</div>
        </div>
      </div>
    </div>
    """
    display(HTML(html))

    # Correction #9: ground truth is ALL evidenced golden integrations (all
    # cited, including any Stage 1.11 couldn't re-confirm in the fetched
    # content) -- not a narrower "verified" subset.
    integrations_ceiling_truth_norm = {_normalize(n) for n in integrations_golden_evidenced}
    integrations_raw_ceiling_by_norm = {_normalize(n): n for n in integrations_golden_evidenced}

    integrations_ceiling_matched = integrations_ceiling_truth_norm & integrations_extracted_norm
    integrations_ceiling_missed = integrations_ceiling_truth_norm - integrations_extracted_norm
    integrations_ceiling_extra = integrations_extracted_norm - integrations_ceiling_truth_norm

    integrations_ceiling_recall = _multilabel_recall(
        list(integrations_ceiling_truth_norm), list(integrations_extracted_norm)
    )
    integrations_ceiling_precision = _multilabel_precision(
        list(integrations_ceiling_truth_norm), list(integrations_extracted_norm)
    )
    integrations_ceiling_f1 = (
        0.0
        if (integrations_ceiling_precision + integrations_ceiling_recall) == 0
        else 2
        * integrations_ceiling_precision
        * integrations_ceiling_recall
        / (integrations_ceiling_precision + integrations_ceiling_recall)
    )

    html = f"""
    <div style="font-family: system-ui, sans-serif; max-width:760px;">
      <p style="font-size:12px; color:{MUTED}; margin-bottom:10px;">
        Scored against all {len(integrations_golden_evidenced)} cited golden integrations -- including any
        Stage 1.11 couldn't re-confirm in the fetched content. Only the {len(integrations_golden_raw) - len(integrations_golden_evidenced)}
        golden entries with no citation at all are excluded.
      </p>
      <div style="display:flex; gap:10px; margin-bottom:14px;">
        {stat("F1", f"{integrations_ceiling_f1:.1%}", ACCENT)}
        {stat("Precision", f"{integrations_ceiling_precision:.1%}", ACCENT)}
        {stat("Recall", f"{integrations_ceiling_recall:.1%}", ACCENT)}
      </div>
      <div style="display:flex; gap:10px;">
        {bucket("MATCHED", GOOD, integrations_ceiling_matched, integrations_raw_ceiling_by_norm)}
        {bucket("MISSED", BAD, integrations_ceiling_missed, integrations_raw_ceiling_by_norm)}
        {bucket("EXTRA (not in golden)", NEUTRAL, integrations_ceiling_extra, integrations_raw_extracted_by_norm)}
      </div>
    </div>
    """
    display(HTML(html))

    def integrations_summary_card(label, color, value, caption):
        return f"""
        <div style="border:{BORDER}; border-top:4px solid {color}; border-radius:6px; padding:14px 16px;">
          <div style="font-size:11px; font-weight:600; color:{color}; letter-spacing:0.02em;">{label}</div>
          <div style="font-size:22px; font-weight:700; margin:4px 0 2px;">{value}</div>
          <div style="font-size:12px; color:{MUTED};">{caption}</div>
        </div>"""

    integrations_summary_html = f"""
    <div style="font-family: system-ui, sans-serif; max-width:820px;">
      <div style="display:grid; grid-template-columns: repeat(3, 1fr); gap:10px;">
        {
        integrations_summary_card(
            "1 &middot; GOLDEN SET",
            MUTED,
            len(integrations_golden_raw),
            f"{len(integrations_golden_evidenced)} cited, {len(integrations_golden_raw) - len(integrations_golden_evidenced)} uncited",
        )
    }
        {
        integrations_summary_card(
            "2 &middot; FETCH &amp; USE (shared, Stage 1.1)",
            ACCENT,
            f"{fetch_metrics['used']}/{fetch_metrics['total_urls']} used ({fetch_metrics['used_pct']}%)",
            "same fetch stage as the customers profile -- not re-run per profile",
        )
    }
        {
        integrations_summary_card(
            "3 &middot; GROUNDING (ELI-1136)",
            ACCENT,
            f"{len(integrations_unique_kept)}/{len(integrations_unique_candidates)} products kept (deduplicated)",
            f"TP {len(integrations_tp)} &middot; FP {len(integrations_fp)} &middot; FN {len(integrations_fn)} &middot; TN {len(integrations_tn)} (all deduplicated products)",
        )
    }
        {
        integrations_summary_card(
            "4 &middot; REAL F1 / P / R",
            GOOD,
            f"{integrations_raw_f1:.0%} / {integrations_raw_precision:.0%} / {integrations_raw_recall:.0%}",
            "plain name match against the full golden set, no brand/product-family resolution",
        )
    }
        {
        integrations_summary_card(
            "5 &middot; EVIDENCE-VERIFIED",
            GOOD,
            f"{len(integrations_golden_evidenced)}/{len(integrations_golden_raw)} on-site",
            f"{len(integrations_verified)} of those {len(integrations_golden_evidenced)} confirmed by us, {len(integrations_unverifiable)} unconfirmed (fetch limit)",
        )
    }
        {
        integrations_summary_card(
            "6 &middot; CEILING F1 / P / R",
            GOOD,
            f"{integrations_ceiling_f1:.0%} / {integrations_ceiling_precision:.0%} / {integrations_ceiling_recall:.0%}",
            f"vs. all {len(integrations_golden_evidenced)} cited golden",
        )
    }
      </div>
      <p style="font-size:12px; color:{MUTED}; margin-top:10px;">Model: {
        INTEGRATIONS_PRIMARY_MODEL
    }.</p>
    </div>
    """
    display(HTML(integrations_summary_html))
    return locals()


async def run_model_comparison(vendor, customers_ctx, integrations_ctx, driver):
    from IPython.display import HTML, display

    VENDOR = vendor
    ctx = {**customers_ctx, **integrations_ctx}
    golden_raw = ctx["golden_raw"]
    golden_norm = ctx["golden_norm"]
    golden_evidenced = ctx["golden_evidenced"]
    real_f1 = ctx["real_f1"]
    real_precision = ctx["real_precision"]
    real_recall = ctx["real_recall"]
    ceiling_f1 = ctx["ceiling_f1"]
    ceiling_precision = ctx["ceiling_precision"]
    ceiling_recall = ctx["ceiling_recall"]
    ceiling_truth_norm = ctx["ceiling_truth_norm"]
    extracted_norm = ctx["extracted_norm"]
    raw_extracted_by_norm = ctx["raw_extracted_by_norm"]
    integrations_golden_raw = ctx["integrations_golden_raw"]
    integrations_golden_evidenced = ctx["integrations_golden_evidenced"]
    integrations_extracted_raw = ctx["integrations_extracted_raw"]
    integrations_raw_f1 = ctx["integrations_raw_f1"]
    integrations_raw_precision = ctx["integrations_raw_precision"]
    integrations_raw_recall = ctx["integrations_raw_recall"]
    integrations_ceiling_f1 = ctx["integrations_ceiling_f1"]
    integrations_ceiling_precision = ctx["integrations_ceiling_precision"]
    integrations_ceiling_recall = ctx["integrations_ceiling_recall"]
    integrations_ceiling_truth_norm = ctx["integrations_ceiling_truth_norm"]
    integrations_extracted_norm = ctx["integrations_extracted_norm"]
    integrations_raw_extracted_by_norm = ctx["integrations_raw_extracted_by_norm"]
    integrations_golden_norm_set = ctx["integrations_golden_norm_set"]
    compute_extractor_version = ctx["compute_extractor_version"]
    tp = ctx["tp"]
    fp = ctx["fp"]
    fn = ctx["fn"]
    tn = ctx["tn"]
    integrations_tp = ctx["integrations_tp"]
    integrations_fp = ctx["integrations_fp"]
    integrations_fn = ctx["integrations_fn"]
    integrations_tn = ctx["integrations_tn"]
    MINI_MODEL = "openai/gpt-5.4-mini"
    MINI_CUSTOMERS_HASH = await compute_extractor_version("customers", model=MINI_MODEL)

    mini_rows, _, _ = await driver.execute_query(vey._VARIANT_QUERY, vendor=VENDOR)
    mini_customer_rows = [
        dict(r)
        for r in mini_rows
        if r["profile"] == "customers" and r["extractor_version"] == MINI_CUSTOMERS_HASH
    ]
    mini_succeeded_ids = [
        r["extraction_id"] for r in mini_customer_rows if r["outcome"] == "succeeded"
    ]
    mini_edges_by_extraction_id = {}
    if mini_succeeded_ids:
        mini_edge_rows, _, _ = await driver.execute_query(
            vey._EDGES_QUERY, extraction_ids=mini_succeeded_ids
        )
        for r in mini_edge_rows:
            mini_edges_by_extraction_id.setdefault(r["extraction_id"], []).append(dict(r))

    mini_kept_customers = set()
    for row in mini_customer_rows:
        if row["outcome"] == "succeeded":
            for edge in mini_edges_by_extraction_id.get(row["extraction_id"], []):
                cust = customer_name(
                    edge["subject_name"],
                    edge["subject_type"],
                    edge["object_name"],
                    edge["object_type"],
                )
                if cust:
                    mini_kept_customers.add(cust)

    mini_extracted_norm = {_normalize(n) for n in mini_kept_customers}
    mini_raw_extracted_by_norm = {_normalize(n): n for n in mini_kept_customers}

    mini_real_recall = _multilabel_recall(list(golden_norm), list(mini_extracted_norm))
    mini_real_precision = _multilabel_precision(list(golden_norm), list(mini_extracted_norm))
    mini_real_f1 = (
        0.0
        if (mini_real_precision + mini_real_recall) == 0
        else 2 * mini_real_precision * mini_real_recall / (mini_real_precision + mini_real_recall)
    )

    # Ceiling: same golden_evidenced denominator as luna's ceiling stage, for
    # an apples-to-apples comparison.
    mini_ceiling_recall = _multilabel_recall(list(ceiling_truth_norm), list(mini_extracted_norm))
    mini_ceiling_precision = _multilabel_precision(
        list(ceiling_truth_norm), list(mini_extracted_norm)
    )
    mini_ceiling_f1 = (
        0.0
        if (mini_ceiling_precision + mini_ceiling_recall) == 0
        else 2
        * mini_ceiling_precision
        * mini_ceiling_recall
        / (mini_ceiling_precision + mini_ceiling_recall)
    )

    print(f"Model: {MINI_MODEL}")
    print(
        f"Customers -- Mini: {len(mini_kept_customers)} kept, real F1 {mini_real_f1:.1%}, ceiling F1 {mini_ceiling_f1:.1%}"
    )
    print(
        f"Customers -- Luna: {len(extracted_norm)} kept, real F1 {real_f1:.1%}, ceiling F1 {ceiling_f1:.1%}"
    )

    def compare_stat(label, luna_value, mini_value, higher_is_better=True):
        luna_num = float(luna_value.rstrip("%"))
        mini_num = float(mini_value.rstrip("%"))
        luna_wins = luna_num > mini_num if higher_is_better else luna_num < mini_num
        mini_wins = mini_num > luna_num if higher_is_better else mini_num < luna_num
        luna_color = GOOD if luna_wins else (BAD if mini_wins else MUTED)
        mini_color = GOOD if mini_wins else (BAD if luna_wins else MUTED)
        return f"""
        <div style="border:{BORDER}; border-radius:6px; padding:10px 14px; flex:1;">
          <div style="font-size:11px; color:{MUTED}; font-weight:600; text-align:center;">{label}</div>
          <div style="display:flex; justify-content:space-around; margin-top:6px;">
            <div style="text-align:center;">
              <div style="font-size:22px; font-weight:700; color:{luna_color};">{luna_value}</div>
              <div style="font-size:10px; color:{MUTED};">LUNA</div>
            </div>
            <div style="text-align:center;">
              <div style="font-size:22px; font-weight:700; color:{mini_color};">{mini_value}</div>
              <div style="font-size:10px; color:{MUTED};">MINI</div>
            </div>
          </div>
        </div>"""

    customers_stats_html = f"""
    <div style="font-family: system-ui, sans-serif; max-width:700px;">
      <div style="font-size:11px; font-weight:700; color:{ACCENT}; text-transform:uppercase; letter-spacing:0.05em; margin-bottom:8px;">Customers &mdash; Real F1/P/R (vs full {len(golden_raw)}-name golden set)</div>
      <div style="display:flex; gap:10px; margin-bottom:14px;">
        {compare_stat("F1", f"{real_f1:.1%}", f"{mini_real_f1:.1%}")}
        {compare_stat("Precision", f"{real_precision:.1%}", f"{mini_real_precision:.1%}")}
        {compare_stat("Recall", f"{real_recall:.1%}", f"{mini_real_recall:.1%}")}
      </div>
      <div style="font-size:11px; font-weight:700; color:{ACCENT}; text-transform:uppercase; letter-spacing:0.05em; margin-bottom:8px;">Customers &mdash; Ceiling F1/P/R (vs {len(golden_evidenced)} evidence-verified golden)</div>
      <div style="display:flex; gap:10px;">
        {compare_stat("F1", f"{ceiling_f1:.1%}", f"{mini_ceiling_f1:.1%}")}
        {compare_stat("Precision", f"{ceiling_precision:.1%}", f"{mini_ceiling_precision:.1%}")}
        {compare_stat("Recall", f"{ceiling_recall:.1%}", f"{mini_ceiling_recall:.1%}")}
      </div>
    </div>
    """
    display(HTML(customers_stats_html))

    # Disagreement: not accuracy against golden -- this is model vs model, what
    # one found that the other didn't, regardless of whether it's actually a
    # real customer. A human reviewing this list is the check on whether each
    # disagreement is a real miss or a real mistake.
    both_norm = extracted_norm & mini_extracted_norm
    luna_only_norm = extracted_norm - mini_extracted_norm
    mini_only_norm = mini_extracted_norm - extracted_norm

    customers_disagreement_html = f"""
    <div style="font-family: system-ui, sans-serif; max-width:760px; margin-top:14px;">
      <div style="font-size:11px; color:{MUTED}; margin-bottom:6px;">Where the two models actually disagree (not accuracy -- just what each one extracted):</div>
      <div style="display:flex; gap:10px;">
        <div style="border:{BORDER}; border-top:4px solid {MUTED}; border-radius:6px; padding:12px 14px; flex:1;">
          <div style="font-size:12px; font-weight:600; color:{MUTED};">FOUND BY BOTH ({len(both_norm)})</div>
          <div style="max-height:180px; overflow-y:auto; margin-top:6px;">{"".join(f'<div style="padding:3px 0; border-bottom:{ROW_BORDER}; font-family:ui-monospace,monospace; font-size:12px;">{raw_extracted_by_norm[n]}</div>' for n in sorted(both_norm)) or "<i>none</i>"}</div>
        </div>
        <div style="border:{BORDER}; border-top:4px solid {ACCENT}; border-radius:6px; padding:12px 14px; flex:1;">
          <div style="font-size:12px; font-weight:600; color:{ACCENT};">LUNA ONLY ({len(luna_only_norm)})</div>
          <div style="max-height:180px; overflow-y:auto; margin-top:6px;">{"".join(f'<div style="padding:3px 0; border-bottom:{ROW_BORDER}; font-family:ui-monospace,monospace; font-size:12px;">{raw_extracted_by_norm[n]}</div>' for n in sorted(luna_only_norm)) or "<i>none</i>"}</div>
        </div>
        <div style="border:{BORDER}; border-top:4px solid {BAD}; border-radius:6px; padding:12px 14px; flex:1;">
          <div style="font-size:12px; font-weight:600; color:{BAD};">MINI ONLY ({len(mini_only_norm)})</div>
          <div style="max-height:180px; overflow-y:auto; margin-top:6px;">{"".join(f'<div style="padding:3px 0; border-bottom:{ROW_BORDER}; font-family:ui-monospace,monospace; font-size:12px;">{mini_raw_extracted_by_norm[n]}</div>' for n in sorted(mini_only_norm)) or "<i>none</i>"}</div>
        </div>
      </div>
      <div style="font-size:11px; color:{MUTED}; margin-top:8px;">"Mini only" names worth checking by eye -- a weaker model mislabeling a product as a customer would show up here, not in the score alone.</div>
    </div>
    """
    display(HTML(customers_disagreement_html))
    # Mini's own confusion matrix -- same construction as luna's (1.4), not a
    # lighter substitute: succeeded edges + grounding_rejections_json, deduped
    # by customer name, golden-checked against the identical `golden_norm` set.
    mini_candidates = []
    for row in mini_customer_rows:
        if row["outcome"] == "succeeded":
            for edge in mini_edges_by_extraction_id.get(row["extraction_id"], []):
                cust = customer_name(
                    edge["subject_name"],
                    edge["subject_type"],
                    edge["object_name"],
                    edge["object_type"],
                )
                mini_candidates.append(
                    {
                        "kept": True,
                        "subject": edge["subject_name"],
                        "object": edge["object_name"],
                        "customer": cust,
                        "url": row["url"],
                    }
                )
        for rejection in json.loads(row["grounding_rejections_json"] or "[]"):
            a = rejection.get("assertion", {})
            cust = customer_name(
                a.get("subject"), a.get("subject_type"), a.get("object"), a.get("object_type")
            )
            mini_candidates.append(
                {
                    "kept": False,
                    "subject": a.get("subject"),
                    "object": a.get("object"),
                    "customer": cust,
                    "url": row["url"],
                }
            )

    mini_groups = {}
    for c in mini_candidates:
        key = (
            _normalize(c["customer"]) if c["customer"] else f"__none__{c['subject']}|{c['object']}"
        )
        mini_groups.setdefault(key, []).append(c)

    mini_unique_kept, mini_unique_rejected = [], []
    for group in mini_groups.values():
        rep = group[0]
        is_golden = bool(rep["customer"]) and _normalize(rep["customer"]) in golden_norm
        kept = any(c["kept"] for c in group)
        # Same representative-pick as luna's unique_rejected: prefer the
        # check that was actually rejected, since that's the URL that
        # backs the "wrongly rejected"/"properly rejected" quadrant.
        url = rep["url"] if kept else next((c["url"] for c in group if not c["kept"]), rep["url"])
        entry = {
            "customer": rep["customer"],
            "subject": rep["subject"],
            "object": rep["object"],
            "is_golden": is_golden,
            "url": url,
        }
        (mini_unique_kept if kept else mini_unique_rejected).append(entry)

    mini_tp = [c for c in mini_unique_kept if c["is_golden"]]
    mini_fp = [c for c in mini_unique_kept if not c["is_golden"]]
    mini_fn = [c for c in mini_unique_rejected if c["is_golden"]]
    mini_tn = [c for c in mini_unique_rejected if not c["is_golden"]]

    def confmatrix_pair(tag, icon, color, title, luna_items, mini_items, show_url=False):
        def rel_rows(items):
            return (
                "".join(
                    f'<div style="padding:3px 0; border-bottom:{ROW_BORDER}; font-family:ui-monospace,monospace; font-size:11px;">'
                    f"{c['subject']} &rarr; {c['object']}"
                    + (
                        f' &mdash; <a href="{c["url"]}" target="_blank" rel="noopener noreferrer" style="color:{ACCENT};">source</a>'
                        if show_url
                        else ""
                    )
                    + "</div>"
                    for c in items
                )
                or "<i>none</i>"
            )

        return f"""
        <div style="border:{BORDER}; border-top:4px solid {color}; border-radius:6px; padding:10px 12px;">
          <div style="font-size:11px; font-weight:600; color:{color};">{icon} {tag}</div>
          <div style="display:flex; gap:16px; margin:4px 0;">
            <div style="text-align:center;"><div style="font-size:22px; font-weight:700;">{len(luna_items)}</div><div style="font-size:9px; color:{MUTED};">LUNA</div></div>
            <div style="text-align:center;"><div style="font-size:22px; font-weight:700;">{len(mini_items)}</div><div style="font-size:9px; color:{MUTED};">MINI</div></div>
          </div>
          <div style="font-size:11px; color:{MUTED}; margin-bottom:6px;">{title}</div>
          <details><summary style="cursor:pointer; font-size:11px; color:{ACCENT};">show relations</summary>
            <div style="display:flex; gap:8px; margin-top:6px;">
              <div style="flex:1;"><div style="font-size:9px; color:{MUTED}; font-weight:600;">LUNA</div><div style="max-height:160px; overflow-y:auto;">{rel_rows(luna_items)}</div></div>
              <div style="flex:1;"><div style="font-size:9px; color:{MUTED}; font-weight:600;">MINI</div><div style="max-height:160px; overflow-y:auto;">{rel_rows(mini_items)}</div></div>
            </div>
          </details>
        </div>"""

    customers_confmatrix_html = f"""
    <div style="font-family: system-ui, sans-serif; margin-top:20px;">
      <div style="font-size:11px; font-weight:700; color:{ACCENT}; text-transform:uppercase; letter-spacing:0.05em; margin-bottom:8px;">Customers &mdash; Confusion Matrix, Luna vs Mini (deduplicated candidates)</div>
      <div style="display:grid; grid-template-columns: 90px 1fr 1fr; gap:10px; max-width:820px;">
        <div></div>
        <div style="text-align:center; font-size:12px; font-weight:600; color:{MUTED};">KEPT</div>
        <div style="text-align:center; font-size:12px; font-weight:600; color:{MUTED};">REJECTED</div>
        <div style="display:flex; align-items:center; justify-content:flex-end; font-size:12px; font-weight:600; color:{MUTED}; padding-right:6px;">GOLDEN MATCH</div>
        {confmatrix_pair("TP", "&#10003;", GOOD, "properly accepted", tp, mini_tp)}
        {confmatrix_pair("FN", "&#10007;", BAD, "wrongly rejected", fn, mini_fn, show_url=True)}
        <div style="display:flex; align-items:center; justify-content:flex-end; font-size:12px; font-weight:600; color:{MUTED}; padding-right:6px;">NO GOLDEN MATCH</div>
        {confmatrix_pair("FP", "&#10007;", BAD, "wrongly accepted", fp, mini_fp)}
        {confmatrix_pair("TN", "&#10003;", GOOD, "properly rejected", tn, mini_tn, show_url=True)}
      </div>
    </div>
    """
    display(HTML(customers_confmatrix_html))

    MINI_INTEGRATIONS_HASH = await compute_extractor_version("integrations", model=MINI_MODEL)

    mini_int_rows, _, _ = await driver.execute_query(vey._VARIANT_QUERY, vendor=VENDOR)
    mini_int_rows = [
        dict(r)
        for r in mini_int_rows
        if r["profile"] == "integrations" and r["extractor_version"] == MINI_INTEGRATIONS_HASH
    ]
    mini_int_succeeded_ids = [
        r["extraction_id"] for r in mini_int_rows if r["outcome"] == "succeeded"
    ]
    mini_int_edges = {}
    if mini_int_succeeded_ids:
        mini_int_edge_rows, _, _ = await driver.execute_query(
            vey._EDGES_QUERY, extraction_ids=mini_int_succeeded_ids
        )
        for r in mini_int_edge_rows:
            mini_int_edges.setdefault(r["extraction_id"], []).append(dict(r))

    mini_extracted_products_raw = set()
    for row in mini_int_rows:
        if row["outcome"] == "succeeded":
            for edge in mini_int_edges.get(row["extraction_id"], []):
                mini_extracted_products_raw.add(
                    edge["object_name"]
                )  # correction #10: object is always the integrated product

    # Plain name match -- no brand/product-family alias resolution (same
    # scope decision as Luna's own integrations F1 above).
    mini_int_extracted_norm = {_normalize(n) for n in mini_extracted_products_raw}
    mini_int_raw_extracted_by_norm = {_normalize(n): n for n in mini_extracted_products_raw}

    mini_int_recall = _multilabel_recall(
        list(integrations_golden_norm_set), list(mini_int_extracted_norm)
    )
    mini_int_precision = _multilabel_precision(
        list(integrations_golden_norm_set), list(mini_int_extracted_norm)
    )
    mini_int_f1 = (
        0.0
        if (mini_int_precision + mini_int_recall) == 0
        else 2 * mini_int_precision * mini_int_recall / (mini_int_precision + mini_int_recall)
    )

    mini_int_ceiling_recall = _multilabel_recall(
        list(integrations_ceiling_truth_norm), list(mini_int_extracted_norm)
    )
    mini_int_ceiling_precision = _multilabel_precision(
        list(integrations_ceiling_truth_norm), list(mini_int_extracted_norm)
    )
    mini_int_ceiling_f1 = (
        0.0
        if (mini_int_ceiling_precision + mini_int_ceiling_recall) == 0
        else 2
        * mini_int_ceiling_precision
        * mini_int_ceiling_recall
        / (mini_int_ceiling_precision + mini_int_ceiling_recall)
    )

    print(
        f"Integrations -- Mini: {len(mini_extracted_products_raw)} kept, real F1 {mini_int_f1:.1%}, ceiling F1 {mini_int_ceiling_f1:.1%}"
    )
    print(
        f"Integrations -- Luna: {len(integrations_extracted_raw)} kept, real F1 {integrations_raw_f1:.1%}, ceiling F1 {integrations_ceiling_f1:.1%}"
    )

    integrations_stats_html = f"""
    <div style="font-family: system-ui, sans-serif; max-width:700px; margin-top:20px;">
      <div style="font-size:11px; font-weight:700; color:{ACCENT}; text-transform:uppercase; letter-spacing:0.05em; margin-bottom:8px;">Integrations &mdash; Real F1/P/R (vs full {len(integrations_golden_raw)}-name golden set)</div>
      <div style="display:flex; gap:10px; margin-bottom:14px;">
        {compare_stat("F1", f"{integrations_raw_f1:.1%}", f"{mini_int_f1:.1%}")}
        {compare_stat("Precision", f"{integrations_raw_precision:.1%}", f"{mini_int_precision:.1%}")}
        {compare_stat("Recall", f"{integrations_raw_recall:.1%}", f"{mini_int_recall:.1%}")}
      </div>
      <div style="font-size:11px; font-weight:700; color:{ACCENT}; text-transform:uppercase; letter-spacing:0.05em; margin-bottom:8px;">Integrations &mdash; Ceiling F1/P/R (vs {len(integrations_golden_evidenced)} evidence-verified golden)</div>
      <div style="display:flex; gap:10px;">
        {compare_stat("F1", f"{integrations_ceiling_f1:.1%}", f"{mini_int_ceiling_f1:.1%}")}
        {compare_stat("Precision", f"{integrations_ceiling_precision:.1%}", f"{mini_int_ceiling_precision:.1%}")}
        {compare_stat("Recall", f"{integrations_ceiling_recall:.1%}", f"{mini_int_ceiling_recall:.1%}")}
      </div>
    </div>
    """
    display(HTML(integrations_stats_html))

    int_both_norm = integrations_extracted_norm & mini_int_extracted_norm
    int_luna_only_norm = integrations_extracted_norm - mini_int_extracted_norm
    int_mini_only_norm = mini_int_extracted_norm - integrations_extracted_norm

    integrations_disagreement_html = f"""
    <div style="font-family: system-ui, sans-serif; max-width:760px; margin-top:14px;">
      <div style="font-size:11px; color:{MUTED}; margin-bottom:6px;">Where the two models disagree on integrations:</div>
      <div style="display:flex; gap:10px;">
        <div style="border:{BORDER}; border-top:4px solid {MUTED}; border-radius:6px; padding:12px 14px; flex:1;">
          <div style="font-size:12px; font-weight:600; color:{MUTED};">FOUND BY BOTH ({len(int_both_norm)})</div>
          <div style="max-height:180px; overflow-y:auto; margin-top:6px;">{"".join(f'<div style="padding:3px 0; border-bottom:{ROW_BORDER}; font-family:ui-monospace,monospace; font-size:12px;">{integrations_raw_extracted_by_norm[n]}</div>' for n in sorted(int_both_norm)) or "<i>none</i>"}</div>
        </div>
        <div style="border:{BORDER}; border-top:4px solid {ACCENT}; border-radius:6px; padding:12px 14px; flex:1;">
          <div style="font-size:12px; font-weight:600; color:{ACCENT};">LUNA ONLY ({len(int_luna_only_norm)})</div>
          <div style="max-height:180px; overflow-y:auto; margin-top:6px;">{"".join(f'<div style="padding:3px 0; border-bottom:{ROW_BORDER}; font-family:ui-monospace,monospace; font-size:12px;">{integrations_raw_extracted_by_norm[n]}</div>' for n in sorted(int_luna_only_norm)) or "<i>none</i>"}</div>
        </div>
        <div style="border:{BORDER}; border-top:4px solid {BAD}; border-radius:6px; padding:12px 14px; flex:1;">
          <div style="font-size:12px; font-weight:600; color:{BAD};">MINI ONLY ({len(int_mini_only_norm)})</div>
          <div style="max-height:180px; overflow-y:auto; margin-top:6px;">{"".join(f'<div style="padding:3px 0; border-bottom:{ROW_BORDER}; font-family:ui-monospace,monospace; font-size:12px;">{mini_int_raw_extracted_by_norm[n]}</div>' for n in sorted(int_mini_only_norm)) or "<i>none</i>"}</div>
        </div>
      </div>
    </div>
    """
    display(HTML(integrations_disagreement_html))

    def integrated_product_name(subject, obj):
        return obj  # correction #10: object is always the integrated product

    integrations_golden_norm_set = {_normalize(n) for n in integrations_golden_raw}

    mini_int_candidates = []
    for row in mini_int_rows:
        if row["outcome"] == "succeeded":
            for edge in mini_int_edges.get(row["extraction_id"], []):
                prod = integrated_product_name(edge["subject_name"], edge["object_name"])
                mini_int_candidates.append(
                    {
                        "kept": True,
                        "subject": edge["subject_name"],
                        "object": edge["object_name"],
                        "product": prod,
                        "url": row["url"],
                    }
                )
        for rejection in json.loads(row["grounding_rejections_json"] or "[]"):
            a = rejection.get("assertion", {})
            prod = integrated_product_name(a.get("subject"), a.get("object"))
            mini_int_candidates.append(
                {
                    "kept": False,
                    "subject": a.get("subject"),
                    "object": a.get("object"),
                    "product": prod,
                    "url": row["url"],
                }
            )

    mini_int_groups = {}
    for c in mini_int_candidates:
        key = _normalize(c["product"]) if c["product"] else f"__none__{c['subject']}|{c['object']}"
        mini_int_groups.setdefault(key, []).append(c)

    mini_int_unique_kept, mini_int_unique_rejected = [], []
    for group in mini_int_groups.values():
        rep = group[0]
        is_golden = (
            bool(rep["product"]) and _normalize(rep["product"]) in integrations_golden_norm_set
        )
        kept = any(c["kept"] for c in group)
        # Same representative-pick as luna's unique_rejected: prefer the
        # check that was actually rejected for the URL behind this quadrant.
        url = rep["url"] if kept else next((c["url"] for c in group if not c["kept"]), rep["url"])
        entry = {
            "product": rep["product"],
            "subject": rep["subject"],
            "object": rep["object"],
            "is_golden": is_golden,
            "url": url,
        }
        (mini_int_unique_kept if kept else mini_int_unique_rejected).append(entry)

    mini_integrations_tp = [c for c in mini_int_unique_kept if c["is_golden"]]
    mini_integrations_fp = [c for c in mini_int_unique_kept if not c["is_golden"]]
    mini_integrations_fn = [c for c in mini_int_unique_rejected if c["is_golden"]]
    mini_integrations_tn = [c for c in mini_int_unique_rejected if not c["is_golden"]]

    integrations_confmatrix_html = f"""
    <div style="font-family: system-ui, sans-serif; margin-top:20px;">
      <div style="font-size:11px; font-weight:700; color:{ACCENT}; text-transform:uppercase; letter-spacing:0.05em; margin-bottom:8px;">Integrations &mdash; Confusion Matrix, Luna vs Mini (deduplicated candidates)</div>
      <div style="display:grid; grid-template-columns: 90px 1fr 1fr; gap:10px; max-width:820px;">
        <div></div>
        <div style="text-align:center; font-size:12px; font-weight:600; color:{MUTED};">KEPT</div>
        <div style="text-align:center; font-size:12px; font-weight:600; color:{MUTED};">REJECTED</div>
        <div style="display:flex; align-items:center; justify-content:flex-end; font-size:12px; font-weight:600; color:{MUTED}; padding-right:6px;">GOLDEN MATCH</div>
        {confmatrix_pair("TP", "&#10003;", GOOD, "properly accepted", integrations_tp, mini_integrations_tp)}
        {confmatrix_pair("FN", "&#10007;", BAD, "wrongly rejected", integrations_fn, mini_integrations_fn, show_url=True)}
        <div style="display:flex; align-items:center; justify-content:flex-end; font-size:12px; font-weight:600; color:{MUTED}; padding-right:6px;">NO GOLDEN MATCH</div>
        {confmatrix_pair("FP", "&#10007;", BAD, "wrongly accepted", integrations_fp, mini_integrations_fp)}
        {confmatrix_pair("TN", "&#10003;", GOOD, "properly rejected", integrations_tn, mini_integrations_tn, show_url=True)}
      </div>
    </div>
    """
    display(HTML(integrations_confmatrix_html))


async def run_prompt_comparison(
    vendor,
    customers_ctx,
    integrations_ctx,
    driver,
    previous_customers_hash,
    previous_integrations_hash,
):
    """ELI-1228: same shape as run_model_comparison, but the two passes being compared
    share one model (openai/gpt-5.6-luna) and differ only in which Braintrust prompt
    version was live when each ran. Since both passes used the identical model string,
    they can't be told apart by re-querying compute_extractor_version(model=...) the
    way run_model_comparison distinguishes luna vs mini -- the "new prompts" hash is
    whatever's live right now (already picked up by customers_ctx/integrations_ctx),
    but the "previous prompts" hash no longer resolves live once the prompt has been
    edited in Braintrust, so it's passed in explicitly, captured before the edit."""
    from IPython.display import HTML, display

    VENDOR = vendor
    ctx = {**customers_ctx, **integrations_ctx}
    golden_raw = ctx["golden_raw"]
    golden_norm = ctx["golden_norm"]
    golden_evidenced = ctx["golden_evidenced"]
    real_f1 = ctx["real_f1"]
    real_precision = ctx["real_precision"]
    real_recall = ctx["real_recall"]
    ceiling_f1 = ctx["ceiling_f1"]
    ceiling_precision = ctx["ceiling_precision"]
    ceiling_recall = ctx["ceiling_recall"]
    ceiling_truth_norm = ctx["ceiling_truth_norm"]
    extracted_norm = ctx["extracted_norm"]
    raw_extracted_by_norm = ctx["raw_extracted_by_norm"]
    integrations_golden_raw = ctx["integrations_golden_raw"]
    integrations_golden_evidenced = ctx["integrations_golden_evidenced"]
    integrations_extracted_raw = ctx["integrations_extracted_raw"]
    integrations_raw_f1 = ctx["integrations_raw_f1"]
    integrations_raw_precision = ctx["integrations_raw_precision"]
    integrations_raw_recall = ctx["integrations_raw_recall"]
    integrations_ceiling_f1 = ctx["integrations_ceiling_f1"]
    integrations_ceiling_precision = ctx["integrations_ceiling_precision"]
    integrations_ceiling_recall = ctx["integrations_ceiling_recall"]
    integrations_ceiling_truth_norm = ctx["integrations_ceiling_truth_norm"]
    integrations_extracted_norm = ctx["integrations_extracted_norm"]
    integrations_raw_extracted_by_norm = ctx["integrations_raw_extracted_by_norm"]
    integrations_golden_norm_set = ctx["integrations_golden_norm_set"]
    tp = ctx["tp"]
    fp = ctx["fp"]
    fn = ctx["fn"]
    tn = ctx["tn"]
    integrations_tp = ctx["integrations_tp"]
    integrations_fp = ctx["integrations_fp"]
    integrations_fn = ctx["integrations_fn"]
    integrations_tn = ctx["integrations_tn"]

    previous_rows, _, _ = await driver.execute_query(vey._VARIANT_QUERY, vendor=VENDOR)
    previous_customer_rows = [
        dict(r)
        for r in previous_rows
        if r["profile"] == "customers" and r["extractor_version"] == previous_customers_hash
    ]
    previous_succeeded_ids = [
        r["extraction_id"] for r in previous_customer_rows if r["outcome"] == "succeeded"
    ]
    previous_edges_by_extraction_id = {}
    if previous_succeeded_ids:
        previous_edge_rows, _, _ = await driver.execute_query(
            vey._EDGES_QUERY, extraction_ids=previous_succeeded_ids
        )
        for r in previous_edge_rows:
            previous_edges_by_extraction_id.setdefault(r["extraction_id"], []).append(dict(r))

    previous_kept_customers = set()
    for row in previous_customer_rows:
        if row["outcome"] == "succeeded":
            for edge in previous_edges_by_extraction_id.get(row["extraction_id"], []):
                cust = customer_name(
                    edge["subject_name"],
                    edge["subject_type"],
                    edge["object_name"],
                    edge["object_type"],
                )
                if cust:
                    previous_kept_customers.add(cust)

    previous_extracted_norm = {_normalize(n) for n in previous_kept_customers}
    previous_raw_extracted_by_norm = {_normalize(n): n for n in previous_kept_customers}

    previous_real_recall = _multilabel_recall(list(golden_norm), list(previous_extracted_norm))
    previous_real_precision = _multilabel_precision(
        list(golden_norm), list(previous_extracted_norm)
    )
    previous_real_f1 = (
        0.0
        if (previous_real_precision + previous_real_recall) == 0
        else 2
        * previous_real_precision
        * previous_real_recall
        / (previous_real_precision + previous_real_recall)
    )

    # Ceiling: same golden_evidenced denominator as the new pass's ceiling stage, for
    # an apples-to-apples comparison.
    previous_ceiling_recall = _multilabel_recall(
        list(ceiling_truth_norm), list(previous_extracted_norm)
    )
    previous_ceiling_precision = _multilabel_precision(
        list(ceiling_truth_norm), list(previous_extracted_norm)
    )
    previous_ceiling_f1 = (
        0.0
        if (previous_ceiling_precision + previous_ceiling_recall) == 0
        else 2
        * previous_ceiling_precision
        * previous_ceiling_recall
        / (previous_ceiling_precision + previous_ceiling_recall)
    )

    print("Comparing: previous prompts vs new prompts (same model: openai/gpt-5.6-luna)")
    print(
        f"Customers -- Previous: {len(previous_kept_customers)} kept, real F1 {previous_real_f1:.1%}, ceiling F1 {previous_ceiling_f1:.1%}"
    )
    print(
        f"Customers -- New: {len(extracted_norm)} kept, real F1 {real_f1:.1%}, ceiling F1 {ceiling_f1:.1%}"
    )

    def compare_stat(label, new_value, previous_value, higher_is_better=True):
        new_num = float(new_value.rstrip("%"))
        previous_num = float(previous_value.rstrip("%"))
        new_wins = new_num > previous_num if higher_is_better else new_num < previous_num
        previous_wins = previous_num > new_num if higher_is_better else previous_num < new_num
        new_color = GOOD if new_wins else (BAD if previous_wins else MUTED)
        previous_color = GOOD if previous_wins else (BAD if new_wins else MUTED)
        return f"""
        <div style="border:{BORDER}; border-radius:6px; padding:10px 14px; flex:1;">
          <div style="font-size:11px; color:{MUTED}; font-weight:600; text-align:center;">{label}</div>
          <div style="display:flex; justify-content:space-around; margin-top:6px;">
            <div style="text-align:center;">
              <div style="font-size:22px; font-weight:700; color:{new_color};">{new_value}</div>
              <div style="font-size:10px; color:{MUTED};">NEW</div>
            </div>
            <div style="text-align:center;">
              <div style="font-size:22px; font-weight:700; color:{previous_color};">{previous_value}</div>
              <div style="font-size:10px; color:{MUTED};">PREVIOUS</div>
            </div>
          </div>
        </div>"""

    customers_stats_html = f"""
    <div style="font-family: system-ui, sans-serif; max-width:700px;">
      <div style="font-size:11px; font-weight:700; color:{ACCENT}; text-transform:uppercase; letter-spacing:0.05em; margin-bottom:8px;">Customers &mdash; Real F1/P/R (vs full {len(golden_raw)}-name golden set)</div>
      <div style="display:flex; gap:10px; margin-bottom:14px;">
        {compare_stat("F1", f"{real_f1:.1%}", f"{previous_real_f1:.1%}")}
        {compare_stat("Precision", f"{real_precision:.1%}", f"{previous_real_precision:.1%}")}
        {compare_stat("Recall", f"{real_recall:.1%}", f"{previous_real_recall:.1%}")}
      </div>
      <div style="font-size:11px; font-weight:700; color:{ACCENT}; text-transform:uppercase; letter-spacing:0.05em; margin-bottom:8px;">Customers &mdash; Ceiling F1/P/R (vs {len(golden_evidenced)} evidence-verified golden)</div>
      <div style="display:flex; gap:10px;">
        {compare_stat("F1", f"{ceiling_f1:.1%}", f"{previous_ceiling_f1:.1%}")}
        {compare_stat("Precision", f"{ceiling_precision:.1%}", f"{previous_ceiling_precision:.1%}")}
        {compare_stat("Recall", f"{ceiling_recall:.1%}", f"{previous_ceiling_recall:.1%}")}
      </div>
    </div>
    """
    display(HTML(customers_stats_html))

    # Disagreement: not accuracy against golden -- this is prompt-version vs
    # prompt-version, what one found that the other didn't, regardless of whether
    # it's actually a real customer. A human reviewing this list is the check on
    # whether each disagreement is a real gain, a real regression, or a wash.
    both_norm = extracted_norm & previous_extracted_norm
    new_only_norm = extracted_norm - previous_extracted_norm
    previous_only_norm = previous_extracted_norm - extracted_norm

    customers_disagreement_html = f"""
    <div style="font-family: system-ui, sans-serif; max-width:760px; margin-top:14px;">
      <div style="font-size:11px; color:{MUTED}; margin-bottom:6px;">Where the two prompt versions actually disagree (not accuracy -- just what each one extracted):</div>
      <div style="display:flex; gap:10px;">
        <div style="border:{BORDER}; border-top:4px solid {MUTED}; border-radius:6px; padding:12px 14px; flex:1;">
          <div style="font-size:12px; font-weight:600; color:{MUTED};">FOUND BY BOTH ({len(both_norm)})</div>
          <div style="max-height:180px; overflow-y:auto; margin-top:6px;">{"".join(f'<div style="padding:3px 0; border-bottom:{ROW_BORDER}; font-family:ui-monospace,monospace; font-size:12px;">{raw_extracted_by_norm[n]}</div>' for n in sorted(both_norm)) or "<i>none</i>"}</div>
        </div>
        <div style="border:{BORDER}; border-top:4px solid {ACCENT}; border-radius:6px; padding:12px 14px; flex:1;">
          <div style="font-size:12px; font-weight:600; color:{ACCENT};">NEW ONLY ({len(new_only_norm)})</div>
          <div style="max-height:180px; overflow-y:auto; margin-top:6px;">{"".join(f'<div style="padding:3px 0; border-bottom:{ROW_BORDER}; font-family:ui-monospace,monospace; font-size:12px;">{raw_extracted_by_norm[n]}</div>' for n in sorted(new_only_norm)) or "<i>none</i>"}</div>
        </div>
        <div style="border:{BORDER}; border-top:4px solid {BAD}; border-radius:6px; padding:12px 14px; flex:1;">
          <div style="font-size:12px; font-weight:600; color:{BAD};">PREVIOUS ONLY ({len(previous_only_norm)})</div>
          <div style="max-height:180px; overflow-y:auto; margin-top:6px;">{"".join(f'<div style="padding:3px 0; border-bottom:{ROW_BORDER}; font-family:ui-monospace,monospace; font-size:12px;">{previous_raw_extracted_by_norm[n]}</div>' for n in sorted(previous_only_norm)) or "<i>none</i>"}</div>
        </div>
      </div>
      <div style="font-size:11px; color:{MUTED}; margin-top:8px;">"Previous only" names worth checking by eye -- a prompt edit that starts abstaining on a real customer would show up here, not in the score alone.</div>
    </div>
    """
    display(HTML(customers_disagreement_html))
    # Previous pass's own confusion matrix -- same construction as the new pass's
    # (1.4), not a lighter substitute: succeeded edges + grounding_rejections_json,
    # deduped by customer name, golden-checked against the identical `golden_norm` set.
    previous_candidates = []
    for row in previous_customer_rows:
        if row["outcome"] == "succeeded":
            for edge in previous_edges_by_extraction_id.get(row["extraction_id"], []):
                cust = customer_name(
                    edge["subject_name"],
                    edge["subject_type"],
                    edge["object_name"],
                    edge["object_type"],
                )
                previous_candidates.append(
                    {
                        "kept": True,
                        "subject": edge["subject_name"],
                        "object": edge["object_name"],
                        "customer": cust,
                        "url": row["url"],
                    }
                )
        for rejection in json.loads(row["grounding_rejections_json"] or "[]"):
            a = rejection.get("assertion", {})
            cust = customer_name(
                a.get("subject"), a.get("subject_type"), a.get("object"), a.get("object_type")
            )
            previous_candidates.append(
                {
                    "kept": False,
                    "subject": a.get("subject"),
                    "object": a.get("object"),
                    "customer": cust,
                    "url": row["url"],
                }
            )

    previous_groups = {}
    for c in previous_candidates:
        key = (
            _normalize(c["customer"]) if c["customer"] else f"__none__{c['subject']}|{c['object']}"
        )
        previous_groups.setdefault(key, []).append(c)

    previous_unique_kept, previous_unique_rejected = [], []
    for group in previous_groups.values():
        rep = group[0]
        is_golden = bool(rep["customer"]) and _normalize(rep["customer"]) in golden_norm
        kept = any(c["kept"] for c in group)
        # Same representative-pick as the new pass's unique_rejected: prefer the
        # check that was actually rejected, since that's the URL that
        # backs the "wrongly rejected"/"properly rejected" quadrant.
        url = rep["url"] if kept else next((c["url"] for c in group if not c["kept"]), rep["url"])
        entry = {
            "customer": rep["customer"],
            "subject": rep["subject"],
            "object": rep["object"],
            "is_golden": is_golden,
            "url": url,
        }
        (previous_unique_kept if kept else previous_unique_rejected).append(entry)

    previous_tp = [c for c in previous_unique_kept if c["is_golden"]]
    previous_fp = [c for c in previous_unique_kept if not c["is_golden"]]
    previous_fn = [c for c in previous_unique_rejected if c["is_golden"]]
    previous_tn = [c for c in previous_unique_rejected if not c["is_golden"]]

    def confmatrix_pair(tag, icon, color, title, new_items, previous_items, show_url=False):
        def rel_rows(items):
            return (
                "".join(
                    f'<div style="padding:3px 0; border-bottom:{ROW_BORDER}; font-family:ui-monospace,monospace; font-size:11px;">'
                    f"{c['subject']} &rarr; {c['object']}"
                    + (
                        f' &mdash; <a href="{c["url"]}" target="_blank" style="color:{ACCENT};">source</a>'
                        if show_url
                        else ""
                    )
                    + "</div>"
                    for c in items
                )
                or "<i>none</i>"
            )

        return f"""
        <div style="border:{BORDER}; border-top:4px solid {color}; border-radius:6px; padding:10px 12px;">
          <div style="font-size:11px; font-weight:600; color:{color};">{icon} {tag}</div>
          <div style="display:flex; gap:16px; margin:4px 0;">
            <div style="text-align:center;"><div style="font-size:22px; font-weight:700;">{len(new_items)}</div><div style="font-size:9px; color:{MUTED};">NEW</div></div>
            <div style="text-align:center;"><div style="font-size:22px; font-weight:700;">{len(previous_items)}</div><div style="font-size:9px; color:{MUTED};">PREVIOUS</div></div>
          </div>
          <div style="font-size:11px; color:{MUTED}; margin-bottom:6px;">{title}</div>
          <details><summary style="cursor:pointer; font-size:11px; color:{ACCENT};">show relations</summary>
            <div style="display:flex; gap:8px; margin-top:6px;">
              <div style="flex:1;"><div style="font-size:9px; color:{MUTED}; font-weight:600;">NEW</div><div style="max-height:160px; overflow-y:auto;">{rel_rows(new_items)}</div></div>
              <div style="flex:1;"><div style="font-size:9px; color:{MUTED}; font-weight:600;">PREVIOUS</div><div style="max-height:160px; overflow-y:auto;">{rel_rows(previous_items)}</div></div>
            </div>
          </details>
        </div>"""

    customers_confmatrix_html = f"""
    <div style="font-family: system-ui, sans-serif; margin-top:20px;">
      <div style="font-size:11px; font-weight:700; color:{ACCENT}; text-transform:uppercase; letter-spacing:0.05em; margin-bottom:8px;">Customers &mdash; Confusion Matrix, New vs Previous Prompts (deduplicated candidates)</div>
      <div style="display:grid; grid-template-columns: 90px 1fr 1fr; gap:10px; max-width:820px;">
        <div></div>
        <div style="text-align:center; font-size:12px; font-weight:600; color:{MUTED};">KEPT</div>
        <div style="text-align:center; font-size:12px; font-weight:600; color:{MUTED};">REJECTED</div>
        <div style="display:flex; align-items:center; justify-content:flex-end; font-size:12px; font-weight:600; color:{MUTED}; padding-right:6px;">GOLDEN MATCH</div>
        {confmatrix_pair("TP", "&#10003;", GOOD, "properly accepted", tp, previous_tp)}
        {confmatrix_pair("FN", "&#10007;", BAD, "wrongly rejected", fn, previous_fn, show_url=True)}
        <div style="display:flex; align-items:center; justify-content:flex-end; font-size:12px; font-weight:600; color:{MUTED}; padding-right:6px;">NO GOLDEN MATCH</div>
        {confmatrix_pair("FP", "&#10007;", BAD, "wrongly accepted", fp, previous_fp)}
        {confmatrix_pair("TN", "&#10003;", GOOD, "properly rejected", tn, previous_tn, show_url=True)}
      </div>
    </div>
    """
    display(HTML(customers_confmatrix_html))

    previous_int_rows, _, _ = await driver.execute_query(vey._VARIANT_QUERY, vendor=VENDOR)
    previous_int_rows = [
        dict(r)
        for r in previous_int_rows
        if r["profile"] == "integrations" and r["extractor_version"] == previous_integrations_hash
    ]
    previous_int_succeeded_ids = [
        r["extraction_id"] for r in previous_int_rows if r["outcome"] == "succeeded"
    ]
    previous_int_edges = {}
    if previous_int_succeeded_ids:
        previous_int_edge_rows, _, _ = await driver.execute_query(
            vey._EDGES_QUERY, extraction_ids=previous_int_succeeded_ids
        )
        for r in previous_int_edge_rows:
            previous_int_edges.setdefault(r["extraction_id"], []).append(dict(r))

    previous_extracted_products_raw = set()
    for row in previous_int_rows:
        if row["outcome"] == "succeeded":
            for edge in previous_int_edges.get(row["extraction_id"], []):
                previous_extracted_products_raw.add(
                    edge["object_name"]
                )  # correction #10: object is always the integrated product

    # Plain name match -- no brand/product-family alias resolution (same scope
    # decision as the new pass's integrations F1 above).
    previous_int_extracted_norm = {_normalize(n) for n in previous_extracted_products_raw}
    previous_int_raw_extracted_by_norm = {_normalize(n): n for n in previous_extracted_products_raw}

    previous_int_recall = _multilabel_recall(
        list(integrations_golden_norm_set), list(previous_int_extracted_norm)
    )
    previous_int_precision = _multilabel_precision(
        list(integrations_golden_norm_set), list(previous_int_extracted_norm)
    )
    previous_int_f1 = (
        0.0
        if (previous_int_precision + previous_int_recall) == 0
        else 2
        * previous_int_precision
        * previous_int_recall
        / (previous_int_precision + previous_int_recall)
    )

    previous_int_ceiling_recall = _multilabel_recall(
        list(integrations_ceiling_truth_norm), list(previous_int_extracted_norm)
    )
    previous_int_ceiling_precision = _multilabel_precision(
        list(integrations_ceiling_truth_norm), list(previous_int_extracted_norm)
    )
    previous_int_ceiling_f1 = (
        0.0
        if (previous_int_ceiling_precision + previous_int_ceiling_recall) == 0
        else 2
        * previous_int_ceiling_precision
        * previous_int_ceiling_recall
        / (previous_int_ceiling_precision + previous_int_ceiling_recall)
    )

    print(
        f"Integrations -- Previous: {len(previous_extracted_products_raw)} kept, real F1 {previous_int_f1:.1%}, ceiling F1 {previous_int_ceiling_f1:.1%}"
    )
    print(
        f"Integrations -- New: {len(integrations_extracted_raw)} kept, real F1 {integrations_raw_f1:.1%}, ceiling F1 {integrations_ceiling_f1:.1%}"
    )

    integrations_stats_html = f"""
    <div style="font-family: system-ui, sans-serif; max-width:700px; margin-top:20px;">
      <div style="font-size:11px; font-weight:700; color:{ACCENT}; text-transform:uppercase; letter-spacing:0.05em; margin-bottom:8px;">Integrations &mdash; Real F1/P/R (vs full {len(integrations_golden_raw)}-name golden set)</div>
      <div style="display:flex; gap:10px; margin-bottom:14px;">
        {compare_stat("F1", f"{integrations_raw_f1:.1%}", f"{previous_int_f1:.1%}")}
        {compare_stat("Precision", f"{integrations_raw_precision:.1%}", f"{previous_int_precision:.1%}")}
        {compare_stat("Recall", f"{integrations_raw_recall:.1%}", f"{previous_int_recall:.1%}")}
      </div>
      <div style="font-size:11px; font-weight:700; color:{ACCENT}; text-transform:uppercase; letter-spacing:0.05em; margin-bottom:8px;">Integrations &mdash; Ceiling F1/P/R (vs {len(integrations_golden_evidenced)} evidence-verified golden)</div>
      <div style="display:flex; gap:10px;">
        {compare_stat("F1", f"{integrations_ceiling_f1:.1%}", f"{previous_int_ceiling_f1:.1%}")}
        {compare_stat("Precision", f"{integrations_ceiling_precision:.1%}", f"{previous_int_ceiling_precision:.1%}")}
        {compare_stat("Recall", f"{integrations_ceiling_recall:.1%}", f"{previous_int_ceiling_recall:.1%}")}
      </div>
    </div>
    """
    display(HTML(integrations_stats_html))

    int_both_norm = integrations_extracted_norm & previous_int_extracted_norm
    int_new_only_norm = integrations_extracted_norm - previous_int_extracted_norm
    int_previous_only_norm = previous_int_extracted_norm - integrations_extracted_norm

    integrations_disagreement_html = f"""
    <div style="font-family: system-ui, sans-serif; max-width:760px; margin-top:14px;">
      <div style="font-size:11px; color:{MUTED}; margin-bottom:6px;">Where the two prompt versions disagree on integrations:</div>
      <div style="display:flex; gap:10px;">
        <div style="border:{BORDER}; border-top:4px solid {MUTED}; border-radius:6px; padding:12px 14px; flex:1;">
          <div style="font-size:12px; font-weight:600; color:{MUTED};">FOUND BY BOTH ({len(int_both_norm)})</div>
          <div style="max-height:180px; overflow-y:auto; margin-top:6px;">{"".join(f'<div style="padding:3px 0; border-bottom:{ROW_BORDER}; font-family:ui-monospace,monospace; font-size:12px;">{integrations_raw_extracted_by_norm[n]}</div>' for n in sorted(int_both_norm)) or "<i>none</i>"}</div>
        </div>
        <div style="border:{BORDER}; border-top:4px solid {ACCENT}; border-radius:6px; padding:12px 14px; flex:1;">
          <div style="font-size:12px; font-weight:600; color:{ACCENT};">NEW ONLY ({len(int_new_only_norm)})</div>
          <div style="max-height:180px; overflow-y:auto; margin-top:6px;">{"".join(f'<div style="padding:3px 0; border-bottom:{ROW_BORDER}; font-family:ui-monospace,monospace; font-size:12px;">{integrations_raw_extracted_by_norm[n]}</div>' for n in sorted(int_new_only_norm)) or "<i>none</i>"}</div>
        </div>
        <div style="border:{BORDER}; border-top:4px solid {BAD}; border-radius:6px; padding:12px 14px; flex:1;">
          <div style="font-size:12px; font-weight:600; color:{BAD};">PREVIOUS ONLY ({len(int_previous_only_norm)})</div>
          <div style="max-height:180px; overflow-y:auto; margin-top:6px;">{"".join(f'<div style="padding:3px 0; border-bottom:{ROW_BORDER}; font-family:ui-monospace,monospace; font-size:12px;">{previous_int_raw_extracted_by_norm[n]}</div>' for n in sorted(int_previous_only_norm)) or "<i>none</i>"}</div>
        </div>
      </div>
    </div>
    """
    display(HTML(integrations_disagreement_html))

    def integrated_product_name(subject, obj):
        return obj  # correction #10: object is always the integrated product

    integrations_golden_norm_set = {_normalize(n) for n in integrations_golden_raw}

    previous_int_candidates = []
    for row in previous_int_rows:
        if row["outcome"] == "succeeded":
            for edge in previous_int_edges.get(row["extraction_id"], []):
                prod = integrated_product_name(edge["subject_name"], edge["object_name"])
                previous_int_candidates.append(
                    {
                        "kept": True,
                        "subject": edge["subject_name"],
                        "object": edge["object_name"],
                        "product": prod,
                        "url": row["url"],
                    }
                )
        for rejection in json.loads(row["grounding_rejections_json"] or "[]"):
            a = rejection.get("assertion", {})
            prod = integrated_product_name(a.get("subject"), a.get("object"))
            previous_int_candidates.append(
                {
                    "kept": False,
                    "subject": a.get("subject"),
                    "object": a.get("object"),
                    "product": prod,
                    "url": row["url"],
                }
            )

    previous_int_groups = {}
    for c in previous_int_candidates:
        key = _normalize(c["product"]) if c["product"] else f"__none__{c['subject']}|{c['object']}"
        previous_int_groups.setdefault(key, []).append(c)

    previous_int_unique_kept, previous_int_unique_rejected = [], []
    for group in previous_int_groups.values():
        rep = group[0]
        is_golden = (
            bool(rep["product"]) and _normalize(rep["product"]) in integrations_golden_norm_set
        )
        kept = any(c["kept"] for c in group)
        # Same representative-pick as the new pass's unique_rejected: prefer the
        # check that was actually rejected for the URL behind this quadrant.
        url = rep["url"] if kept else next((c["url"] for c in group if not c["kept"]), rep["url"])
        entry = {
            "product": rep["product"],
            "subject": rep["subject"],
            "object": rep["object"],
            "is_golden": is_golden,
            "url": url,
        }
        (previous_int_unique_kept if kept else previous_int_unique_rejected).append(entry)

    previous_integrations_tp = [c for c in previous_int_unique_kept if c["is_golden"]]
    previous_integrations_fp = [c for c in previous_int_unique_kept if not c["is_golden"]]
    previous_integrations_fn = [c for c in previous_int_unique_rejected if c["is_golden"]]
    previous_integrations_tn = [c for c in previous_int_unique_rejected if not c["is_golden"]]

    integrations_confmatrix_html = f"""
    <div style="font-family: system-ui, sans-serif; margin-top:20px;">
      <div style="font-size:11px; font-weight:700; color:{ACCENT}; text-transform:uppercase; letter-spacing:0.05em; margin-bottom:8px;">Integrations &mdash; Confusion Matrix, New vs Previous Prompts (deduplicated candidates)</div>
      <div style="display:grid; grid-template-columns: 90px 1fr 1fr; gap:10px; max-width:820px;">
        <div></div>
        <div style="text-align:center; font-size:12px; font-weight:600; color:{MUTED};">KEPT</div>
        <div style="text-align:center; font-size:12px; font-weight:600; color:{MUTED};">REJECTED</div>
        <div style="display:flex; align-items:center; justify-content:flex-end; font-size:12px; font-weight:600; color:{MUTED}; padding-right:6px;">GOLDEN MATCH</div>
        {confmatrix_pair("TP", "&#10003;", GOOD, "properly accepted", integrations_tp, previous_integrations_tp)}
        {confmatrix_pair("FN", "&#10007;", BAD, "wrongly rejected", integrations_fn, previous_integrations_fn, show_url=True)}
        <div style="display:flex; align-items:center; justify-content:flex-end; font-size:12px; font-weight:600; color:{MUTED}; padding-right:6px;">NO GOLDEN MATCH</div>
        {confmatrix_pair("FP", "&#10007;", BAD, "wrongly accepted", integrations_fp, previous_integrations_fp)}
        {confmatrix_pair("TN", "&#10003;", GOOD, "properly rejected", integrations_tn, previous_integrations_tn, show_url=True)}
      </div>
    </div>
    """
    display(HTML(integrations_confmatrix_html))
