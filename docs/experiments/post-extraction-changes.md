# Two post-extraction changes and their measured effect

Public research edition of the historical report. Dataset rows, internal review material and private artifacts are omitted. Commands describe the historical experiment; see ../reproduction.md for the supported standalone setup.

## Context and method

The first full cached first-party run recorded 6,429 distinct `(page, relation)` candidates. Its corrected-key precision was high but recall low. Two subsequent changes targeted rejected candidates without changing extraction itself.

The report records review of all 6,429 model labels in 33 shuffled batches of up to 200. There were 911 corrections: 663 false accepts and 248 false rejects. Each change was then measured over affected claims using the actual referent and semantic stages, scored against the reviewed page-based key. These are private broader-corpus measurements, not public ten-product results.

## Change 1: repair spliced evidence quotes

Approximately one in eleven extractor quotes combined genuine fragments while omitting intervening text. The deterministic contiguity gate rejected them before whole-page evaluation. It blocked 570 claims, 447 supported by the reviewed key.

Instead of accepting the model's assembled quotation, repair recovered the page's own substring spanning the matched pieces. That text was contiguous by construction and was sent to referent resolution. This distinguishes repairing evidence from inventing or silently rewriting a quotation.

Of the 447 supported claims, 245 were recovered, with 28 unsupported claims retained as well. Repair also exposed non-idempotent text normalization: a second normalization pass removed additional Markdown. That defect was fixed. The old contiguity rejection path no longer fired after the repair change described here.

## Change 2: first-party customer-list interpretation

The referent had rejected names under a vendor's explicit customer-list heading as mere grid entries. Seven of ten organizations rejected on that basis existed in the reference customer data. The revised first-party prompt recognized the vendor's own adoption claim; external-page treatment remained stricter. Actual names and evidence quotations are omitted.

This affected more than spliced quotes. On 306 claims dropped by the referent alone, the revised prompt confirmed 120 supported and eight unsupported claims.

## Overall results

| Historical configuration | P | R | F1 |
|---|---:|---:|---:|
| First full run | 0.944 | 0.634 | 0.758 |
| Plus splice repair | 0.940 | 0.685 | 0.792 |
| Plus first-party referent change | 0.940 | 0.710 | 0.809 |

Precision was approximately unchanged when rounded to two decimals, not exactly unchanged. The measured benefit was recall: 0.634 → 0.710. These are targeted re-evaluations of recorded candidates, not repeated independent end-to-end extraction runs.

Outcome of the 570 previously contiguity-blocked claims:

| Outcome | Supported | Unsupported |
|---|---:|---:|
| Kept | 245 | 28 |
| Referent rejects | 127 | 79 |
| Semantic check: page does not frame entities as users | 58 | 6 |
| Other semantic rejection | 17 | 10 |

## Remaining limits

The largest remaining group contained 594 relations confirmed by the referent and overruled by the semantic check, including 391 supported claims. This does not justify bypassing the semantic stage wholesale: the group also contains unsupported claims.

The semantic check read a local section capped at 6,000 characters. Some supporting headings, case-study links and testimonial attributions lay outside that window. Widening context was untested.

The scoring unit also inflates improvements on repeated site navigation. One claim appeared on 179 pages, so recovering its repeated teaser counts many times. Relation-level deduplication and repeated runs would provide a stronger estimate of practical benefit. The private corpus, review sheets, corrections and run artifacts are unavailable here.
