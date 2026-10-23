# ELI-1315 — Rejecting relationships under the wrong profile

Public research edition of the historical report. Dataset rows, internal review material and private artifacts are omitted. Commands describe the historical experiment; see ../reproduction.md for the supported standalone setup.

## Problem and corrected scope

A real entity and grounded quote can still be assigned the wrong relationship type: adoption by an organization is different from interoperability between independent software products.

Rechecking ELI-1308's 127 automated wrong-profile candidates produced a much smaller definite bug population:

| Outcome | Rows |
|---|---:|
| Definite wrong-profile error | 11 |
| Already correctly profiled | 7 |
| Grounded relationship, ambiguous profile | 107 |
| Reclassified as other error types | 2 |

The eleven definite rows collapsed to nine distinct entity cases because one appeared three times. Ambiguous labels were not treated as confirmed extraction bugs.

## Implementation

Add `relationship_shape_matches_profile` to the semantic verdict and apply it to both `Adopts` and `Integrates`. Unlike preceding fields restricted to a single relationship type, this was checked for every assertion. Both profile prompts required corresponding changes.

The technical distinction is between an organization deploying a product for its own use and a connection between independent software products. Technical vocabulary alone does not determine the profile: implementing an API can still describe adoption.

## Historical validation and instability

Using real evidence with `openai/gpt-5.4-mini`, each of nine cases was tested five times. One initially failed consistently enough to expose a boundary problem: it was rejected only twice in five runs. Testing the actual source-language passage confirmed that this was model behavior rather than paraphrase or harness error. API/implementation wording biased the model toward integration even though the deployment was for the organization's own staff.

A targeted worked example clarified that distinction. Two additional repetitions of the difficult case yielded ten rejections in ten runs, followed by 45/45 rejections across the full nine-case set. Two genuine relationship controls tested five times each yielded 10/10 correct acceptances. The historical suite reported 345 passing tests with no regressions.

## Limitation

The 107 ambiguous candidates required a consistent profile definition before they could be used as error labels. This fix did not resolve that specification question, and case-level validation is not a full-corpus performance estimate. Authentic quotes and relationship identities are omitted.
