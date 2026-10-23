# A confidence score for the extraction pipeline

Public research edition of the historical report. Dataset rows, internal review material and private artifacts are omitted. Commands describe the historical experiment; see ../reproduction.md for the supported standalone setup.

## Purpose and scope

A fixed keep/drop rule selects one precision–recall trade-off for every consumer. A score between zero and one permits later threshold selection. This experiment asked whether recorded pipeline signals could support that choice and which small feature sets were most informative.

All numerical results below are historical exploratory measurements on the private 6,429-judgment corpus, not the public ten-product dataset. They are not a deployed calibration guarantee. Page-level repetition, extensive feature selection and threshold tuning limit interpretation of nominal out-of-fold performance.

## 1. Candidate signals

Twenty-two features were recorded when the pipeline assessed each candidate. Names below describe technical outputs, not exact private reviewer guidelines or prompt text.

### Semantic-panel votes: eight features

Five model calls assessed each relation. Each criterion became the fraction voting yes: 1.0 meant five of five and 0.6 meant three of five.

| Feature | Signal represented |
|---|---|
| `is_named_organization` | Specific organization identity |
| `relationship_actually_happened` | Occurred rather than merely proposed relationship |
| `relationship_claim_supported` | Support for the asserted claim |
| `is_third_party_vendor` | Independent counterparty identity |
| `is_customer_facing_integration` | Customer-facing rather than internal infrastructure connection |
| `is_specific_named_entity` | Specific entity rather than a category |
| `page_frames_them_as_users` | User/adopter framing on the page |
| `subject_is_the_claimant` | Attribution of the claim to the correct subject |

`relationship_shape_matches_profile` was present in recorded votes but omitted from the feature set by oversight. The source identified adding it as follow-up work.

### Processing flags: eleven features

| Feature | Meaning |
|---|---|
| Panel ran | Semantic stage reached |
| Referent asked | Whole-page resolution invoked |
| Referent confirmed | Whole-page resolution accepted |
| Quote names both | Both endpoints appear in the quote |
| Quote contiguous | Quote matches one page span |
| Subject in URL | Subject name appears in the address |
| Object in URL | Object name appears in the address |
| First-party page | Vendor-owned source |
| Customers profile | Customer rather than integration relation |
| Edge pair allowed | Valid endpoint-type combination |
| Cheap check rejected | Deterministic validation rejected the candidate |

### Evidence counts: three features

- Log quote length.
- Log number of relations reusing the quote on the page.
- Log number of relations emitted by the extraction call.

`Customers profile` encodes a corpus base rate rather than evidence and may not transfer to a new profile mix. Quote contiguity and cheap-check rejection encode historical pipeline policy; at least one relevant rule changed after collection.

## 2. Labels, fitting and search

### Labels and scoring units

The report records manual review of all 6,429 page-relation judgments, correcting 911 initial model labels. This reviewed key asks whether a particular page supports a claim.

The separate reference scoreboard asks whether an entity-level relationship appears in the reference dataset, collapsing repeated pages first. It can miss genuine claims not listed in that dataset and contains relations absent from the crawled first-party evidence. Its precision and recall are reference-overlap measures, not interchangeable with page-support accuracy.

### Model and folds

Logistic regression was fitted by Newton steps with light ridge regularization. The motivation was an interpretable continuous score suitable for thresholding. Logistic regression can produce probabilities, but calibration still requires measurement rather than following automatically from model choice.

Five-fold cross-validation scored each relation with a model that had not trained on that row. Feature standardization used training-fold statistics only. The historical report calls these predictions out of sample. It does not establish a vendor- or page-grouped split, nested feature selection or an independent calibration set; those stronger generalization claims are not made here.

### Feature search

- Exhaustive subsets through eight features: **603,500** combinations of sizes one through eight.
- Beam search beyond eight: extend the forty best eight-feature subsets one feature at a time through all 22.
- Reported runtime: approximately two hours, with no model-provider calls.
- A separate tree search considered subsets through five features and depths through four: **141,724** shallow trees.

These counts are retained as reported. Reusing the same folds to search many subsets and choose operating points can introduce selection optimism even when individual predictions are out of fold.

### Operating points

For every subset, thresholds were swept from one to zero to record:

1. Best F1.
2. Best precision subject to a recall floor.
3. Best recall subject to a precision floor.

The reviewed-key floors were recall ≥0.6 and precision ≥0.9. The reference floors were recall ≥0.3 and precision ≥0.5. Floors prevent trivial high precision from keeping almost nothing, or trivial high recall from accepting everything.

## 3. Results

The fixed pipeline scored **P 0.944 / R 0.634 / F1 0.758** against the reviewed key.

### Threshold trade-off

| Target precision | Reported recall | Relations kept |
|---|---:|---:|
| 0.99 | 0.002 | 12 |
| 0.98 | 0.27 | 1,305 |
| 0.97 | 0.40 | 2,002 |
| 0.96 | 0.45 | 2,258 |
| 0.95 | 0.72 | 3,623 |
| 0.93 | 0.84 | 4,359 |
| 0.90 | 0.90 | 4,792 |

At roughly the fixed pipeline's precision, the source reports score-based recall near 0.8. It also describes perfect observed precision among the top twelve claims; the table labels that operating point 0.99. With twelve retained observations, neither wording supports a production guarantee of error-free output. Identical recorded signals can occur on both supported and unsupported claims.

### Reviewed-key results by feature count

Each cell is precision / recall / F1.

| Features | Best F1 | Best precision, recall ≥0.6 | Best recall, precision ≥0.9 |
|---|---|---|---|
| 1 | 0.84 / 0.89 / 0.86 | 0.92 / 0.64 / 0.76 | 0.90 / 0.70 / 0.79 |
| 2 | 0.84 / 0.93 / 0.88 | 0.93 / 0.64 / 0.76 | 0.90 / 0.74 / 0.81 |
| 3 | 0.84 / 0.94 / 0.89 | 0.93 / 0.60 / 0.73 | 0.90 / 0.79 / 0.84 |
| 4 | 0.85 / 0.95 / 0.89 | 0.94 / 0.60 / 0.74 | 0.90 / 0.83 / 0.86 |
| 5 | 0.87 / 0.93 / 0.90 | 0.95 / 0.60 / 0.74 | 0.90 / 0.87 / 0.88 |

### Reference-set results by feature count

| Features | Best F1 | Best precision, recall ≥0.3 | Best recall, precision ≥0.5 |
|---|---|---|---|
| 1 | 0.34 / 0.49 / 0.40 | 0.39 / 0.30 / 0.34 | 0.50 / 0.14 / 0.22 |
| 2 | 0.39 / 0.46 / 0.42 | 0.52 / 0.30 / 0.38 | 0.50 / 0.34 / 0.41 |
| 3 | 0.40 / 0.47 / 0.43 | 0.53 / 0.30 / 0.38 | 0.50 / 0.36 / 0.42 |
| 4 | 0.41 / 0.47 / 0.44 | 0.55 / 0.30 / 0.39 | 0.50 / 0.36 / 0.42 |
| 5 | 0.43 / 0.46 / 0.45 | 0.56 / 0.30 / 0.39 | 0.50 / 0.36 / 0.42 |

Searching beyond five features raised best reviewed-key F1 from 0.898 to 0.907 at thirteen features, with lower scores beyond that. Eight extra features bought less than one F1 point.

### Decision trees

The best shallow tree reached F1 0.900 using five signals and four levels, compared with logistic F1 0.907. Its leaves suggested approximately 0.89–0.97 supported-claim rates for unanimous panels. With split panels, referent confirmation corresponded to about 0.89, while an asked-and-refused referent corresponded to about 0.33.

Discrete leaf scores limited operating-point control: at the 0.9 precision floor, the source reports tree recall 0.60 versus logistic 0.91. This is a search-specific threshold comparison, not evidence that trees cannot be calibrated in general.

## 4. Fitted-model interpretation

Inputs were standardized, making coefficient magnitudes comparable within each fitted model. Exact weights, preprocessing statistics, fitting inputs and validation artifacts remain private. Aggregate operating-point comparisons above describe the historical fits, not standalone deployable models.

Referent routing can mark difficult claims, so correlation with an invoked check does not establish that the check causes worse outcomes. These fits require independent validation before reuse.

## 5. Conclusions and remaining work

One panel feature—agreement that the claim is supported—already reached F1 0.864. Adding 21 more candidate signals improved the best score by about 0.04. Five-feature models were within one F1 point of the thirteen-feature optimum, favoring simpler models for interpretation.

The referent was more prominent in reference-set models than reviewed-key models. The useful distinction was whether an invoked referent confirmed or refused a claim. The strongest recurring coefficient, however, was the profile base rate, which says little about individual evidence. The historical recommendation was to drop it and rerun selection before deployment.

The feature set contained no direct page-type representation: case study, logo wall, catalogue or repeated navigation teaser. Those structures accounted for important remaining failures. Adding evidence-structure signals, testing grouped held-out data, and independently validating selected thresholds would be needed before interpreting the score as transferable confidence.

## Historical reproduction

The source recorded these offline commands:

```text
PYTHONPATH=src uv run python scripts/fit_confidence_score.py
PYTHONPATH=src uv run python scripts/search_confidence_features.py
PYTHONPATH=src uv run python scripts/search_confidence_trees.py
```

These scripts and their private corpus inputs are **unavailable here**; the commands are historical documentation, not a standalone public workflow. Reported runtimes were seconds, about two hours and about half an hour respectively, with no model calls. No fitting or provider calls were run for this restoration.
