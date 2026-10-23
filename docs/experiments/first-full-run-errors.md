# Why the first full extraction run retained only six in ten supported relations

Public research edition of the historical report. Dataset rows, internal review material and private artifacts are omitted. Commands describe the historical experiment; see ../reproduction.md for the supported standalone setup.

## Context and question

The historical first-party corpus run recorded 6,429 distinct `(page, relation)` candidates across customers and integrations, whether retained or rejected. `gpt-5.6-sol` judged whether each source page supported its claim. The report records subsequent review of every verdict against its page, correcting 911 labels.

Against that corrected key, pipeline precision was 0.944, recall 0.634 and F1 0.758. The research question was why recall was low and which gates could recover supported claims. These are broader private-corpus measurements, not results on the public ten-product dataset.

## Method

Recorded rejection reasons partitioned 1,763 supported-but-dropped candidates. Each gate was evaluated by counting supported and unsupported rows it blocked and calculating a counterfactual score without that gate. This initial analysis used recorded data, with no new model calls. Removing a gate in a counterfactual table is not equivalent to rerunning downstream checks.

## Main failure mechanisms

The semantic check accounted for almost six in ten misses. The `quote_not_contiguous` rule accounted for about one quarter: it blocked 570 relations, 447 supported and 123 unsupported. Of the 447 supported claims, 377 were first-party customers.

The contiguity rule was intended to catch quotes stitched from disconnected page fragments. It also rejected testimonials where the statement and its attribution were separated by markup. The attribution often supplied the customer name, so a more complete evidence quote could paradoxically fail the exact-span check.

The report initially counted 117 referent confirmations overridden by contiguity and 488 overridden by the semantic check: 605 in total. A full-page judgment could be overturned by a narrower-context stage. These are stage-specific historical counts, not all confirmed true positives.

Two other findings were important:

- `subject_named_only_in_url` blocked 32 claims, with 30 correctly blocked and two supported after full review. The model-only key had made removing this rule appear harmless.
- 306 claims had no recorded rejection reason, including 269 supported claims. At this stage their path was not understood; a later report identified a referent-only group of that size.

## Answer-key audit progression

### Targeted sample: 50 cases

Twenty-five cases came from contiguity failures and twenty-five from referent-overruled claims. Approximately 23/25 contiguity judgments were correct. In the referent-overruled sample, about 14/25 were clearly correct, six clearly wrong and the rest uncertain.

The latter errors often treated general infrastructure, authentication or embedded media as customer-facing integration evidence. Across one related 614-row referent-overruled grouping, 36 objects were of this type. The source estimated that the initial 605 apparent supported overrides might be nearer 450 after correcting the sample's error rate. That was a preliminary projection, not the final full-review count; the 614 and 605 groups should not be assumed identical.

Repeated site furniture also inflated row counts. One relationship appeared on 179 pages and another on 169. In total, 894 rows reused a quote on at least three pages of one site. Collapsing to unique relations—retained if any page retained it, supported if any page supported it—gave 2,349 relations, P 0.931, R 0.680, F1 0.786. Duplication affected magnitude but did not remove the low-recall finding.

A separate false-positive pattern reused one testimonial while changing the company printed beneath it. Actual relationship names and source quotations are omitted.

### Uniform sample: 200 cases

The targeted sample selected disagreements and therefore could not estimate overall label accuracy. A uniform sample of 200 found eleven clearly wrong model labels (5.5%): ten false accepts and one false reject. Error types included general infrastructure, unnamed organizations, generic product categories, reused testimonials and company misidentification.

All eleven occurred on pipeline-dropped claims. In this sample, label errors inflated misses rather than changing retained-output precision. Projecting from it suggested about 15% of apparent misses were not misses, moving recall from 0.594 to approximately 0.633 and F1 from 0.735 to approximately 0.764. The later full-review values were 0.634 and 0.758. Sample conclusions about precision did not hold exactly in the full review.

### First 1,000 reviewed cases

The corpus was shuffled into 200-case review batches. The first five contained 94 incorrect labels (9.4%), with batch error rates 12.5%, 7.5%, 6.5%, 10.0% and 10.5%.

About two thirds were false accepts. Reported categories included fifteen infrastructure claims, eleven instances of a reused testimonial, eight unnamed organizations, eight same-vendor component pairs and thirteen inconsistent judgments of identical pages. False rejects often missed explicit selection announcements, with fifteen in that class.

On these 1,000 cases, corrected scoring changed precision 0.964 → 0.966 and recall 0.621 → 0.649. This preliminary result understated the full review's impact on precision.

### Full review: 6,429 cases

All 33 batches were reviewed. Of 911 corrections (14.2%), 663 changed accept to reject and 248 changed reject to accept. The largest reported groups were:

| Label-error group | Count |
|---|---:|
| Same page judged inconsistently | 254 |
| Quote did not support claim | 148 |
| Vendor paired with own component | 93 |
| No organization named | 79 |
| General-purpose infrastructure | 72 |
| One reused testimonial pattern | 61 |

Corrected overall scoring was P 0.944 / R 0.634 / F1 0.758, versus 0.962 / 0.594 / 0.735 on model labels alone. Unlike the smaller random sample, the full review found incorrectly retained claims and therefore reduced measured precision. Review payloads and reviewer details remain private.

## Subsequent change and remaining hypotheses

The contiguity gate was replaced by repairing a spliced quote using the page's own text spanning matched fragments, then sending it to the referent. The extractor spliced approximately one quote in eleven. Of 570 previously blocked claims, 245 of 447 supported claims returned, along with 28 unsupported ones. Overall recall rose 0.634 → 0.685 and F1 0.758 → 0.792; precise reported precision was 0.944 → 0.940, approximately unchanged at two decimals.

The historical follow-up list included:

1. Evaluate semantic overrides of referent confirmations; a later state counted 594 such relations, plus 119 contiguity-related cases already addressed by splice repair.
2. Trace 306 unrecorded-reason drops, 269 supported in the original accounting.
3. Sweep the semantic acceptance threshold offline using the five recorded votes. This report described an all-five requirement at that stage; earlier reports used majority voting.
4. Test referent resolution even for quotes naming both endpoints, requiring additional model calls.

The source recommended retaining `subject_named_only_in_url`: it blocked 30 unsupported claims at a cost of two supported ones. Semantic checks saw one local section capped at 6,000 characters plus referent evidence; relevant headings, links or testimonial attributions could lie outside that window. Widening context remained untested.

## Score tables

Corrected page-relation key:

| Profile | P | R | F1 |
|---|---:|---:|---:|
| Customers | 0.973 | 0.601 | 0.743 |
| Integrations | 0.906 | 0.686 | 0.781 |
| All | 0.944 | 0.634 | 0.758 |

Before review, corresponding model-only scores were 0.967 / 0.577 / 0.722; 0.956 / 0.619 / 0.752; and 0.962 / 0.594 / 0.735.

Reference matching, with pages collapsed:

| Profile | P | R | F1 |
|---|---:|---:|---:|
| Customers | 0.455 | 0.391 | 0.421 |
| Integrations | 0.205 | 0.386 | 0.268 |
| All | 0.350 | 0.390 | 0.369 |

Reference membership and page-supported evidence use different units and answer different questions.

| Gate | Blocks supported | Blocks unsupported | Counterfactual F1 without gate |
|---|---:|---:|---:|
| Semantic check | 1,031 | 1,196 | 0.795 |
| `quote_not_contiguous` | 447 | 123 | 0.812 |
| No reason recorded | 269 | 37 | 0.795 |
| `subject_named_only_in_url` | 2 | 30 | 0.756 |
| Everything else | 14 | 48 | No movement |

The historical command was `PYTHONPATH=src uv run python scripts/score_corpus_global.py`. The script, recorded corpus and reviewed key are private and unavailable here; this is not a supported standalone command. Reported audit counts are preserved historical claims, not independently re-audited in this public restoration.
