# ELI-1550 — Customer referent resolution on first-party pages

Public research edition of the historical report. Dataset rows, internal review material and private artifacts are omitted. Commands describe the historical experiment; see ../reproduction.md for the supported standalone setup.

## Question and context

Does a customer referent tuned mostly on external pages work on a vendor's own site? The earlier tuning mix is reported here as 239 pages, 171 external and 68 first-party. A first-party-specific failure could be diluted in the overall score. This report's 239-page count differs from ELI-1549's 240 cached pages; the historical reports do not reconcile that difference.

When a proposed relation's quote names only the organization or only the product, the referent reads the whole page to assess use. The experiment tested whether first-party framing improved that step without changing external-page behavior.

## Measurement and answer-key review

The corpus contained 266 first-party pages from 21 products, capped at 40 pages per vendor. Extraction produced 418 customer relations; 216 one-sided-quote relations formed the referent case set, with 176 accepts and 40 rejects in the corrected key.

`gpt-5.6-sol` first judged source pages. Manual review then examined 18 cases where at least half of 17 prompt runs disagreed with the model, and swept two error classes across the whole set. Seven answers were corrected and one case dropped. Corrections were applied by the historical case builder to support reproducible labels. Review rows and private artifacts are omitted.

A second scoreboard used the reference customer dataset restricted to 18 vendors. The source reports 83 page-supported relations with no reference row. The 21-product page sample and 18-vendor reference restriction are distinct denominators.

Eleven prompt versions were written. The shipped version ran three times; the finalists ran six and seven times because a three-case swing moved F1 by approximately 1.5 points. These are broader private-corpus experiments, not measurements of the public ten-product dataset.

## Findings

- **Baseline first-party performance was already strong:** 162/176 accepts, mean 38.7/40 rejects, F1 0.955.
- **A small first-party addition improved the corrected-key score:** v26 recognized a vendor's own case study or spotlight as customer framing even without repeated product naming. Across seven runs it averaged 170.4 accepts and 36.7 rejects, F1 0.975. The report gives t = 5.5 and paired-case Wilcoxon p = 0.014 versus baseline.
- **Correcting the key changed the conclusion:** six corrected rejects were cases all versions already rejected. Against the uncorrected key, the apparent improvement was inside run variance.
- **Showing the weak triggering quote hurt:** 141 accepts versus 162. This variant had not been deployed.
- **Removing the third-party warning hurt even on first-party input:** six accepts were lost in the reported comparison. Replacing it with first-party framing performed worse than retaining it plus the focused addition.
- **Additional rules were not consistently useful:** loose product-name guidance lost nine rejects; anonymized-subject guidance lost six accepts.
- **Reference matching barely changed:** v26 F1 0.673 versus 0.668. Most newly accepted relations had no reference row.

## Routing decision and uncertainty

The historical implementation routed first-party customers to a separate prompt. Source origin traveled through grounding and confirmation to `referent_slug(relation, origin)`. External pages and integration paths retained their existing routing. Two historical tests exercised origin propagation, not only prompt selection.

v26 was not statistically separated from v20 in the reported comparison: Wilcoxon p = 0.098, McNemar 7–6. The choice favored higher mean at equal rejects, but v26 was noisier (sd 0.0077 versus 0.0036). Repeated prompt selection on the same case set and model-assisted labels limit generalization; the reported significance is exploratory.

## End-to-end measurement

The full extraction → referent → semantic pipeline ran over the same 266 pages. The source introduces this as three arms but tabulates four configurations; all four reported rows are preserved.

| Arm | Proposed | Kept | SOL P | SOL R | SOL F1 | Reference F1 |
|---|---:|---:|---:|---:|---:|---:|
| No referent, 1 vote | 437 | 133 | 0.887 | 0.361 | 0.513 | 0.578 |
| v16, 1 vote | 448 | 276 | 0.932 | 0.761 | 0.838 | 0.582 |
| v26, 1 vote | 461 | 304 | 0.934 | 0.801 | 0.862 | 0.625 |
| v26, 5 votes | 438 | 291 | 0.932 | 0.808 | 0.866 | 0.585 |

Adding the baseline referent more than doubled retained edges (133 → 276) while raising evaluator precision (0.887 → 0.932). At matched one-vote settings, v26 retained 28 more edges than v16 at similar precision.

Vote-count effects cannot be isolated: the one- and five-vote v26 runs proposed 461 and 438 relations. A 13-edge retained difference is confounded by stochastic extraction. A related integration experiment returned 1, 39 and 2 relations from three identical extraction calls on one page. Repeated matched arms would be needed to attribute voting effects.

The no-referent, v16 and v26-five-vote reference F1 values were 0.578, 0.582 and 0.585 despite substantially different edge counts. The v26-one-vote row was 0.625, so the claim of flat reference scoring should not be extended to every row. Reference precision fell from 0.578 to 0.462 in the comparison highlighted by the source, consistent with recovering unlisted relations but not proof that every nonmatch was correct.

## Corrected-key version comparison

| Version | Runs | Accept /176 | Reject /40 | Key F1 | sd | Reference F1 |
|---|---:|---:|---:|---:|---:|---:|
| v16 baseline | 3 | 162.0 | 38.7 | 0.9548 | 0.0037 | 0.668 |
| v20 structural | 6 | 168.0 | 36.7 | 0.9674 | 0.0036 | 0.674 |
| v26 baseline + structural | 7 | 170.4 | 36.7 | 0.9747 | 0.0077 | 0.673 |
| v28 page-subject | 2 | 169.0 | 36.0 | 0.9685 | 0.0057 | 0.688 |
| v27 generic + title | 1 | 168 | 34 | 0.9600 | — | 0.686 |
| v22 subproduct | 1 | 170 | 31 | 0.9577 | — | 0.653 |
| v24 combined | 1 | 170 | 30 | 0.9551 | — | 0.678 |
| v23 named-subject | 1 | 164 | 36 | 0.9535 | — | 0.671 |
| v19 first-party framing | 1 | 156 | 40 | 0.9398 | — | 0.672 |
| v18 quote | 1 | 141 | 37 | 0.8812 | — | 0.648 |

The original prompt files, corrected cases, review records, leaderboards and pipeline scripts remain private and unavailable here. Uncorrected model-only scores were superseded. Integration benchmarking is discussed separately in the preserved ELI-1550 methodology and local integrations reports.
