# ELI-1307 — Excluding sitemap and robots content from extraction

Public research edition of the historical report. Dataset rows, internal review material and private artifacts are omitted. Commands describe the historical experiment; see ../reproduction.md for the supported standalone setup.

## Failure mode

Machine-readable sitemap URL lists were treated as prose. A grounding check could confirm that a proposed quote occurred in the file even when that quote was merely a URL, not evidence of a relationship. Twenty-five accepted candidates had this problem.

The original concern was recall loss: nine correct reference matches were supported only by sitemap-derived evidence, including two without another citation in the reference dataset. Before changing exclusion logic, the experiment rechecked current graph evidence after ELI-1308's larger crawl.

## Re-audit

All nine at-risk matches now had actual page evidence. The historical summary of the original 25 problematic candidates reported:

| Status | Count |
|---|---:|
| Now had actual page evidence | 22 |
| Not a reference customer; removal appropriate | 1 |
| No longer active in graph | 1 |
| Still sitemap-only, with a valid match lost | 0 |

The source's displayed breakdown totals 24 rather than 25; the omitted case cannot be reconciled from that table. The reported zero-loss conclusion is therefore a historical audit result, not a newly reconstructed exhaustive accounting. The separate nine-at-risk recheck did report all nine recovered.

## Implementation and verification

Use `technical_exclusion_reason()` at the crawl frontier. Match filenames ending in `.xml` and containing `sitemap`, or exactly `robots.txt`, rather than any occurrence of sitemap in a URL path. A prose page whose slug happens to contain that word remains eligible.

Tests covered seven observed sitemap filename shapes, robots exclusion, and a genuine page with sitemap in its slug. The historical suite reported 338 passing tests with no regressions. The crawl expansion had supplied replacement evidence before this exclusion, so the previously anticipated companion recrawl was no longer required for the tested population.

This experiment did not establish retroactive cleanup of already retained content. ELI-1432 later documented stale fetched sources still being extracted despite discovery-time exclusions.
