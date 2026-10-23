# ELI-1550 — First-party integrations benchmark methodology

Public research edition of the historical report. Dataset rows, internal review material and private artifacts are omitted. Commands describe the historical experiment; see ../reproduction.md for the supported standalone setup.

## Research question

The filename is preserved, but this edition records experimental design and reproducibility lessons rather than employee handoff instructions. The question was whether an integration referent tuned mainly on external pages also worked on vendor-owned pages. Earlier tuning used 171 external and 68 first-party pages, which could conceal first-party-specific errors.

A referent resolves claims whose quote names only one endpoint by reading the whole page. Customer and integration relations require separate questions. This report describes an intermediate benchmark state, before the later integrations results; it is not a current deployment guide.

## Benchmark construction

| Historical stage | Count |
|---|---:|
| First-party pages | 340 across 32 vendors |
| Extracted relations | 325 |
| Initial model verdicts | 313: 184 connected, 129 not |
| Frozen referent cases | 180: 100 accept, 80 reject |

Pages were selected by integration-related URL shapes: `integrat`, `partner`, `marketplace`, `connect`, `interoperab`, `api`. Only 34 first-party pages had integration extraction records available to inherit, with 28 from one vendor. This sampling strategy favors pages advertising connections. Recall therefore means recall over integration-shaped pages, not over entire sites.

The judge differed from the earlier third-party integration judge by omitting its closing third-party-specific warning. Scores were not directly comparable to that earlier benchmark. All counts concern private historical inputs, not the public ten-product dataset.

## Answer-key validation design

The customer experiment had corrected seven of 216 model-labeled cases. That motivated four complementary checks for integration labels:

1. **Template reuse:** group quote text after removing subject names to identify one testimonial reused for different entities.
2. **Structural evidence:** distinguish prose assertions from headings, image markup and logo alt-text.
3. **Self-consistency:** find endpoint pairs judged differently on different pages, then examine context rather than assuming identical truth.
4. **Contested cases:** compare several prompt variants and inspect cases where repeated decisions disagree with the model key.

Corrections needed a recorded reason and a reproducible application step. Indeterminate cases could be removed rather than assigned invented certainty. Model and manually reviewed label provenance were distinguished. The public edition includes none of the review sheets or correction payloads.

Two confounders were identified. First-party integration directories may resemble third-party co-listing traps, making ownership and claim attribution important. Also, one vendor contributed 45 of 80 initial rejects; an aggregate improvement could reflect learning one site's format. Per-vendor reject performance was therefore an important diagnostic.

## Evaluation design

Integration assertions used `subject_type="Product"`, `relation="Integrates"` and `profile="integrations"`. Reference matching needed the pipeline's own integration-name normalization so results were comparable. A reusable referent runner could remain relation-agnostic while selecting the appropriate prompt template.

Two scoreboards were required: page-supported judgments after review, and reference-dataset matching. The customer comparison had 92 disagreements among 217 evaluated items, mostly page-supported positives without reference rows. This intermediate 217-item comparison is not the final 216-case customer benchmark. Reference incompleteness must not be conflated with extraction error.

Before baselining, the actual deployed prompt needed comparison with the local candidate: in the customer experiment, a newer-looking local file was not the version being served. Historical prompt IDs and private service instructions are omitted; the general requirement is to record the exact baseline content and source routing.

## Lessons available at this stage

- Run variation was approximately three cases, around 1.5 F1 points on the customer set. An apparent first-run win could disappear on repetition.
- Showing the triggering weak quote reduced customer accepts by about twenty in the comparison summarized here.
- One focused first-party addition helped, while subsequent rules could trade one accept for seven rejects.
- Removing a third-party warning cost eight accepts in the comparison recorded in this intermediate note. Later customer reporting describes a six-accept loss for its comparison; these are historical summaries, not a newly reconciled estimate.
- A referent-only benchmark cannot measure semantic vote-count changes. At this stage `SEMANTIC_CHECK_VOTES` had been extended to customers as well as integrations, requiring a separate full-pipeline experiment.

The original note's claim that the customer prompt remained unchanged was superseded by the final customer report, which records separate first-party routing. The initial integration label balance was likewise revised in the later integrations report. Historical builder scripts, cases, private reference snapshots and prompt artifacts are unavailable here.
