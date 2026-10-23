# ELI-1056 — Referent-Distance Name Injection

> Public research edition of the historical report. Dataset rows, internal review material and private artifacts are omitted. Commands describe the historical experiment; see ../reproduction.md for the supported standalone setup.

## Objective and historical outcome

Recover assertions whose real product or customer name appears on a first-party page but outside the quote's local evidence span. A pronoun or speaker reference can be clear at page level yet fail a local endpoint-name check.

| Product | Recall before | Recall after |
|---|---:|---:|
| Flatiron OncoEMR | 50% (3/6) | 66.67% (4/6) |
| FirstHx | 80% (4/5) | 100% (5/5) |

These were historical forced-extraction results repeated across multiple runs. They do not establish that the mechanism was safe: the later ELI-1143 audit found fabricated names passing through this design, and ELI-1146 replaced its injection step. Read the results as the evidence available at this stage of development.

## Mechanism

`enrichment/referent_resolution.py` supplied a new LangGraph `resolve_referents` node immediately after `verify_grounding`, gated to `entrypoint == "first_party_acquisition"`.

1. Detect rejection codes `unnamed_subject`, `unnamed_object`, `subject_not_in_evidence` or `object_not_in_evidence`.
2. Look for known graph names on the page; if none qualify, ask an LLM to resolve the missing name from the rest of the page.
3. Insert that name beside the quote and other cue-bearing paragraphs.
4. Re-extract and re-check grounding against the modified content.

The first-party gate limited the search for a referent to the same page. It did not establish that an inserted name was semantically correct.

## Implementation failures found during validation

Six issues prevented end-to-end recovery until corrected:

1. **Near-verbatim quote location:** markdown links became plain text in model quotes, so exact substring splicing failed. A fuzzy locator used the quote-grounding tolerance.
2. **Markdown corruption:** a fuzzy match could end inside link brackets. Placement skipped a trailing markdown-link closer.
3. **Resolution variance:** identical inputs sometimes returned null. Resolution retried up to three times.
4. **Re-extraction variance:** modified pages sometimes yielded no assertions. Re-extraction also retried up to three times.
5. **Missing adoption verb:** `CUSTOMER_CUE` did not recognize “utilize.”
6. **Placement:** appending a name after a long paragraph did not help quotes covering only an earlier clause. Insertion moved next to the cue word and swept other cue-bearing paragraphs.

Two more serious defects involved wrong-name recovery:

7. **Substring false matches:** graph names could match inside unrelated words. Whole-word verification was applied before accepting the candidate.
8. **Real name, wrong referent:** an LLM could select an organization genuinely present elsewhere on a multi-organization page but unrelated to the rejected quote. The resolver was changed to return a supporting sentence connecting the name and reference. That sentence had to occur in the source and contain the proposed name. Vague groups were grounds for abstention.

Illustrative fictional example: a page about **Fictional Cedar Clinic** and **Fictional Willow Clinic** may contain both names, but a quote about “our practice” cannot safely be assigned to either merely because its name occurs somewhere on the page. This example is not a dataset row.

## Validation and scope limits

Historical validation used forced fresh extraction through the evaluation script, with repeated tests after each fix. The script's private run state and case evidence are not bundled. Tests covered resolution, graph wiring, cue matching and the individual regressions.

The report left two Flatiron cases outside this change: one where extraction proposed no relationship at all, and another lacking a strong first-party adoption statement. A rejection-triggered retry cannot recover an assertion never proposed.

A separate provenance issue could mark a source `external_acquisition` when both paths discovered the same URL. One affected record was manually corrected for this experiment; that was not a general fix.

The remaining semantic limitation was explicit: verifying that a supporting sentence exists and contains a name does not prove that it resolves the original referent. A stricter model or independent verification was still needed. Later evidence showed an additional design failure: validating against modified text allowed the system to confirm its own insertion.
