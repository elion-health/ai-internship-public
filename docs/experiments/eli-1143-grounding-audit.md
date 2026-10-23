# ELI-1143 — Grounding-Rejection and Accepted-Edge Audit

> Public research edition of the historical report. Dataset rows, internal review material and private artifacts are omitted. Commands describe the historical experiment; see ../reproduction.md for the supported standalone setup.

## Objective

Determine whether grounding was too strict, too permissive or failing in specific ways. Checks included quote similarity at 0.75, whole-word endpoint presence, relationship cues, self-edge rejection and fabricated-commentary detection.

Rather than sweep a threshold using only roughly four to six known customers per product, the experiment examined real rejection buckets and separately audited accepted edges. A previously observed partially fabricated quote showed that a permissive failure could coexist with false rejections.

## Method and corpus controls

A historical read-only audit queried the latest revision per `(source, profile, extractor_version)`, scoped by target. It parsed rejected assertions from grounding-failure records and separately read accepted edges and their quotes.

The first pass was discarded because it predated ELI-1130's seed-candidate fix and contained fabricated-name contamination. The replacement corpus forced fresh first-party extraction, using resume mode, forced profiles and `--skip-external`. FirstHx ran customers and integrations; Flatiron OncoEMR and Fold Health ran customers only. This avoided mixing fresh first-party results with stale external-source extractions.

All 71 cue rejections and all 80 accepted edges were manually judged. Small buckets were reviewed in full. Subject-presence and quote-presence buckets were reviewed at 58/61 and 53/58 respectively. A duplicate/set-difference issue left a few cases unreviewed, so those audits were not exhaustive.

The corpus snapshot followed the 2026-07-31 reruns. Private audit sheets, row-level judgments and evidence are omitted; the audit script's original data is unavailable.

## Results

There were **258 grounding-failure rejections and 80 accepted edges**:

| Product | Rejections | Accepted |
|---|---:|---:|
| FirstHx | 232 | 65 |
| Flatiron OncoEMR | 22 | 13 |
| Fold Health | 4 | 2 |

| Rejection code | Count | Share | Reviewed | False rejections found |
|---|---:|---:|---|---:|
| `relationship_cue_missing` | 71 | 27.5% | 71/71 | 17, representing 9 distinct relationships |
| `subject_not_in_evidence` | 61 | 23.6% | 58/61 | 17 |
| `quote_not_in_source` | 58 | 22.5% | 53/58 | 17 |
| `object_not_in_evidence` | 22 | 8.5% | 22/22 | 3 |
| `self_edge` | 20 | 7.8% | 20/20 | 0 |
| `unnamed_object` | 9 | 3.5% | 9/9 | 0 |
| `target_not_endpoint` | 8 | 3.1% | 8/8 | 2 |
| `unnamed_subject` | 7 | 2.7% | 7/7 | 0 |
| `target_as_customer_subject` | 2 | 0.8% | 2/2 | 0 |

These are assertion attempts, not counts of unique real-world relationships. Duplicate revisions and page mirrors can repeat both valid and invalid assertions.

## Recommendations by check

**Target endpoint: keep.** Two of eight cases were false rejections involving parent-company/product naming and a weak-quote name variant. The volume did not justify removing the check.

**Quote similarity: keep 0.75; no blanket sweep.** Many false rejections came from teaser/index pages truncating text with ellipses while the full article already yielded the fact. One apparent genuine similarity near-miss was insufficient evidence for changing the threshold globally.

**Endpoint presence: keep at this stage, investigate narrow gaps.** False rejections clustered around distant referents, abbreviations and integration-logo/widget lists that did not repeat the target's name nearby. This did not establish a general threshold problem. Later reports describe subsequent changes based on broader diagnostics.

**Relationship cues: widen.** Seventeen false rejections collapsed to nine relationships, concentrated in partnership phrasing and missing integration implementation cues. This directly motivated ELI-1120.

## Accepted-edge audit

**59/80 (74%) were judged correct; 21/80 (26%) had a confirmed defect.**

- **13/80 (16%): fabricated names accepted through referent retry.** The retry inserted a resolved name into a copy of the page, then grounded against that modified copy. A guessed name could therefore validate itself and the inserted annotation could become stored evidence. This was a systematic pipeline defect, not merely an occasional similarity-threshold accident.
- **Approximately 8/80 (10%): real text, wrong semantics.** These included hypothetical or derailed plans treated as completed facts, generic care settings treated as named organizations, and persons or places assigned the HealthSystem type.

No named relationship rows are reproduced. Illustrative fictional example: “Fictional Cedar Clinic planned to deploy Fictional Willow EHR, but cancelled” contains both endpoint names and a deployment verb, yet does not support a completed adoption relationship.

## Additional observations

The recurring fabricated-name behavior was not fully explained by seed hints. Direct seed lookup on affected pages returned only the relevant target, showing the model could invent names independently. Grounding normally caught those fabrications; modified-source retry was the route around that protection.

Shared press-release boilerplate also encouraged wrong-subject extraction. These low-volume cases were correctly rejected and did not require a new threshold.

Duplication materially inflated bucket counts. One testimonial produced seven cue-rejection rows across mirrors and revisions; unrelated affiliation content repeated nine and eight times in other cases. The nine-distinct-relationships count was therefore more useful for prioritizing cue changes than raw rejection percentages alone.

## Decisions and limitations

The highest-priority fix was immutable-source referent verification (ELI-1146), followed by semantic checks for accepted-edge defects (ELI-1147) and cue coverage (ELI-1120). A model-based reviewer was an exploratory alternative to adding a new deterministic exception for every phrasing failure.

No global quote-threshold sweep or separate diff-based fabrication detector was supported by the findings. Fixing the self-validation mechanism addressed the observed fabrication route more directly.

Results apply to this first-party snapshot only. External behavior, roughly half the rejection volume in the discarded pass, was not audited here. The two largest partially reviewed buckets support estimates rather than exhaustive classifications. The discarded pre-fix corpus was fully replaced, not merged into these counts.
