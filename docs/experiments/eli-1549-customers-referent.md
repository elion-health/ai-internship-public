# ELI-1549 — A second look for customer relations

Public research edition of the historical report. Dataset rows, internal review material and private artifacts are omitted. Commands describe the historical experiment; see ../reproduction.md for the supported standalone setup.

## Motivation

Following integration referent work, the experiment added whole-page resolution for customer claims whose quotes named only one endpoint. The historical customer corpus contained 240 cached pages versus 113 integration pages; an earlier audit had found 323 customer claims versus 59 integration claims. These counts describe private research inputs, not the public ten-product dataset.

## Method and pipeline

The extractor continued proposing an organization, product and evidence quote, including uncertain candidates for downstream adjudication. Deterministic sorting checked quote contiguity with a 95% single-match requirement. Quotes naming both endpoints went directly to semantic assessment; one-sided quotes went to the referent.

For customers, the referent path was common: approximately 250–270 relations per run, versus 120–130 with both endpoints named. The referent received the organization, product, URL and full page, then returned a decision and proof sentence. The page's own matching sentence was stored. Three unsuccessful attempts led to dropping the unresolved relation.

The weak triggering quote was deliberately excluded from the customer referent input. Showing it cost fourteen correct relations in the reported prompt comparison, consistent with anchoring on insufficient evidence. At this stage customer semantic checks used one vote and integrations used five with majority voting.

Implementation selected prompts by relation type (`Adopts` versus `Integrates`), returned no resolution for unknown types, and moved sorting into grounding rather than relying on a disabled rejection code. Referent errors did not fail the whole page, and absent model configuration skipped resolution. Tests covered profile routing, one-sided third-party claims, declines, unknown relations and error isolation.

## Results

The old and new pipelines each ran twice over 240 cached customer pages. Page-based correctness was judged by `gpt-5.6-sol`, a stronger model than the pipeline model, while reference matching provided a separate scoreboard.

| Page-based judgment | Kept | Correct | Wrong | Precision |
|---|---:|---:|---:|---:|
| Old, run 1 | 271 | 232 | 39 | 86% |
| Old, run 2 | 272 | 238 | 34 | 88% |
| New, run 1 | 265 | 249 | 16 | 94% |
| New, run 2 | 257 | 240 | 17 | 93% |

| Reference-set judgment | Matches | Nonmatches | Precision |
|---|---:|---:|---:|
| Old, run 1 | 106 | 111 | 49% |
| Old, run 2 | 104 | 112 | 48% |
| New, run 1 | 103 | 108 | 49% |
| New, run 2 | 102 | 108 | 49% |

Page-judged errors fell by more than half. The original narrative also stated a cost of about ten correct relations; the aggregate page-based table does not show that reduction. The tables are retained without inventing a reconciliation. Nonmatches against an incomplete reference set cannot all be treated as false claims, and the two scoreboards use different units.

## Prompt development

The referent benchmark had 188 cases: 157 accept and 31 reject. Fifteen versions improved the reported correct-case count from 162 to 174. Allowing explicit go-live announcements gained eight; recognizing specific user assertions in third-party directories gained three. A warning distinguishing investor descriptions of a vendor from customer-use evidence helped. A stricter requirement for use already in place reached 25/31 rejects but lost ten accepts.

Remaining false accepts mostly involved marketing pages where the referent and evaluator disagreed about whether a case-study mention established use. Rejection coverage remained around 21/31 across versions. These are tuning-set observations with a model-generated evaluator, not a generalization guarantee. The cached corpus, labels and private prompt artifacts are unavailable here.
