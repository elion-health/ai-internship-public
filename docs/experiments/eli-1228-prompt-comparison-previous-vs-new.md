# ELI-1228 — Previous versus revised extraction and semantic-check prompts

Public research edition of the historical report. Dataset rows, internal review material and private artifacts are omitted. Commands describe the historical experiment; see ../reproduction.md for the supported standalone setup.

## Question and design

Measure whether revised relationship interpretation improves extraction with the same model, the same seven products, and the same first-party corpus. The historical model identifier was `openai/gpt-5.6-luna`; this is a historical configuration, not a claim of current model availability.

Four prompt roles were examined: customer extraction, integration extraction, and the two semantic checks. Changes addressed relationship-type boundaries and the distinction between an announced arrangement and established deployment. The integration extraction prompt itself remained unchanged; an alias-resolution gap required code/data changes rather than wording. Exact private policy mappings and prompt text are omitted.

Both passes reused retained first-party sources, without fresh discovery or fetching. Historical run settings included `--first-party-only --skip-external --mode resume --force-profile customers --force-profile integrations`. Explicit extractor-version hashes separated old and new prompt runs despite their shared model.

## Prerequisites for a valid comparison

- The extractor-version fingerprint originally included only the extraction prompt, not its semantic-check prompt. The fingerprint was extended so a verifier-only edit produced a distinct version.
- Initial runs also consumed previously retained external sources. For Suki, these added 172 sources. Contaminated experiment records were identified by version and source origin, removed within the experiment's scope, and the intended first-party comparison was rerun.
- Source-origin and revision counts were checked for equality across both passes for all seven products.

## Results

| Scope | Customer F1, previous → revised | Integration F1, previous → revised |
|---|---:|---:|
| Suki | 29.4% → 31.4% | 25.0% → 27.8% |
| Infermedica | 36.0% → 38.5% | 10.0% → 14.0% |
| Seven-product mean | 24.4% → 25.9% | 17.4% → 16.4% |
| Mean against evidence-verified reference subset | 24.2% → 25.9% | 17.6% → 16.5% |

The named rows are the public-sample products represented in this experiment; the means retain the full historical seven-product denominator. Revised prompts improved both profiles for three products, previous prompts won both for two, and two were split or unchanged.

## Interpretation

There was no universal winner. The integration mean was particularly sensitive to one product with only five first-party sources: its sole retained integration disappeared, moving its F1 from 14.3% to zero. This small-sample change should be examined before attributing a broad integration regression to the prompt revision.

The comparison analyzed real and evidence-subset precision, recall, F1, confusion matrices, and version-specific disagreements. The read-only analysis did not perform new model calls. No additional automated tests were introduced for that analysis; the versioning infrastructure was tested separately. Historical notebooks and disagreement rows are omitted.
