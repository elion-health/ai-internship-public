# ELI-1146 — Referent-Resolution Retry Self-Validation Bug

> Public research edition of the historical report. Dataset rows, internal review material and private artifacts are omitted. Commands describe the historical experiment; see ../reproduction.md for the supported standalone setup.

## Finding

The ELI-1056 retry guessed a missing name, inserted it into a copy of source text, re-extracted, and grounded the result against that same modified copy. This allowed a fabricated endpoint to appear supported and persisted the synthetic “referring to” annotation inside evidence intended to represent source text.

The earlier audit counted 13 affected accepted edges. A direct historical query on 2026-08-03 found seven currently active tainted edges across five pages; newer extraction revisions had superseded some earlier edges. These are different snapshots, not a measured repair rate.

## Root cause

1. Local grounding rejects a missing endpoint name.
2. `resolve_missing_name` reads the rest of the page to identify a referent.
3. `inject_resolved_names` inserts that guess beside the ambiguous text.
4. Re-extraction produces a quote containing the inserted annotation.
5. Grounding compares it with the same altered page, so the insertion satisfies both quote and name checks.

The evidence boundary was wrong: model-derived text had become the reference used to validate model-derived assertions.

## Fix

`resolve_referents` stopped modifying source text and stopped re-extracting.

1. Call `resolve_missing_name`, which verifies a supporting quote against the real page and checks that the resolved name appears in that real span.
2. Substitute the verified name into the original rejected Assertion, preserving its quote, relationship and other endpoint.
3. Ground against unchanged `source.content`.
4. Use `assume_grounded` only for the specific resolved endpoint field. This bypasses the local-near-quote check that cannot handle a genuinely distant referent; all other checks still run.

Quote matching, self-edge detection, relationship cues and target identity remained active. The exception trusted separately verified whole-page evidence for one field rather than manufacturing local evidence.

## Verification

The ingestion regression test still recovered a real-but-distant referent with a mocked model. It now expected one extractor call rather than two and asserted that persisted evidence never contained “referring to.” The test for retrying an empty re-extraction was removed because that loop no longer existed.

**Historical full suite: 200 passed.** This was unit-level verification with mocked extractor/model behavior, not a fresh end-to-end rerun of every affected page. The previously observed live corruption established the root cause; it did not establish post-fix corpus-wide recovery.

## Remaining limitations

Resolver quote verification still used 75% similarity rather than exact matching. That near-verbatim tradeoff was not tightened here. Whole-page textual support also does not perfectly establish semantic referent identity. Private affected-edge listings and source content are omitted, and the historical graph snapshot is unavailable.
