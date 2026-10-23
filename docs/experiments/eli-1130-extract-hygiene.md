# ELI-1130 — Extraction Input and Output Hygiene

> Public research edition of the historical report. Dataset rows, internal review material and private artifacts are omitted. Commands describe the historical experiment; see ../reproduction.md for the supported standalone setup.

## Scope and result

Two defects affected the extraction step: irrelevant seed candidates supplied to the model and fabricated abstention text emitted as evidence. Neither was shown to have corrupted persisted Flatiron OncoEMR data in this investigation because grounding rejected the observed cases. A related substring issue had previously caused incorrect referent resolution, making prevention at the shared lookup important.

## Seed-candidate matching

`find_seed_candidates` used a case-insensitive Cypher `CONTAINS` comparison to supply known entity names found on a page. Substring matching could treat a short entity name embedded within an unrelated longer word as a relevant hint.

An earlier caller in referent resolution applied a word-boundary guard, but the extraction caller did not. The fix moved `_contains_endpoint` verification into `find_seed_candidates` itself. Cypher remained a coarse prefilter; every returned candidate received the same boundary-aware check. The redundant caller-specific guard was removed.

A direct call against a historical Flatiron source then returned only the relevant product candidate, rather than the unrelated substring match. Source evidence and named relationship examples are omitted.

## Fabricated abstention quotes

The extraction schema already allowed `relationships=[]`. Nonetheless, the model sometimes populated an assertion using a borrowed seed name and put its own refusal text into `quote`, such as “No relevant HealthSystem -> Adopts -> Product relationship is supported.” That is model commentary, not source evidence.

The existing similarity check usually rejected such text; one observed case scored 0.62 against a 0.75 threshold. Relying on incidental dissimilarity was not a dedicated safeguard.

A `META_COMMENTARY_RE` check was added early in `grounding_rejection_reason`, returning `meta_commentary_quote`. Its first version also matched general phrases such as “not supported” and “does not state.” That was too broad: real evidence could contain those phrases about another subject while clearly describing a valid relationship.

The final check was narrowed to schema-triple arrow notation (`Type -> Relation -> Type`), the distinctive signal in the confirmed fabricated abstention. Verification checked that this refusal still rejected and that ordinary prose containing an unrelated negative phrase no longer did.

## Technical decisions and limitations

The historical customers prompt was remotely hosted, so editing a local fallback prompt would not change the live extraction behavior. A grounding-side guard applied regardless of prompt source.

The final regex was intentionally narrow and did not detect every possible fabricated refusal. Other grounding checks remained necessary. The report documents prevention of a proven failure class, not a measured recall or precision improvement.

Changes affected `graph/local.py`, `enrichment/referent_resolution.py`, `enrichment/grounding.py`, graph-primitive tests and grounding tests. Private page content and run artifacts are unavailable in this edition.
