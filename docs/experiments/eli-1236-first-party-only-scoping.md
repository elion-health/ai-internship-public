# ELI-1236 — First-party-only extraction scoping

Public research edition of the historical report. Dataset rows, internal review material and private artifacts are omitted. Commands describe the historical experiment; see ../reproduction.md for the supported standalone setup.

## Problem and cause

The LangGraph pipeline extracted from every retained source associated with a target, including external material acquired in earlier runs. First-party-only comparisons therefore needed an extraction-input filter, not merely a switch disabling new external discovery.

During the prompt comparison, Suki was processed against 326 retained sources instead of the intended 154 first-party sources. The other 172 were external sources already in the graph. This consumed approximately 31 minutes and contaminated the intended comparison. Experiment-specific records were removed using bounded version/time/source criteria before rerunning.

`list_target_source_ids` had no origin filter. The correction used the stored `RetainedSource.entrypoint`, distinguishing `first_party_acquisition` from `external_acquisition`.

## Implementation

- Add `first_party_only=False` to the source-listing operation; when enabled, filter on the first-party entrypoint in the database query.
- Add `ResearchRequest.first_party_only: bool = False`.
- Pass that request field through the research graph's `fetch_first_party` node.
- Expose the historical CLI flag `--first-party-only`.

The default retained the previous all-origin behavior. The explicit filter made comparison scope reproducible even when the database already contained external sources.

## Historical verification

Eight new tests covered store filtering, default behavior, graph propagation, request defaults, and CLI parsing. The suite reported 254 passing tests and one pre-existing referent-resolution failure. This report records that historical result, not a fresh execution of private infrastructure.
