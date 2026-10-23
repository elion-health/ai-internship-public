# ELI-1480 — External-discovery trace analysis

Public research edition of the historical report. Dataset rows, internal review material and private artifacts are omitted. Commands describe the historical experiment; see ../reproduction.md for the supported standalone setup.

## Method

The LangGraph discovery agent was run end to end three times on one vendor, recording every tool call. Follow-up compared three agent configurations across six vendors. The underlying traces and broader corpus are private, not part of the public ten-product dataset.

## Findings and changes

- `crawl_site` always returned unavailable; its implementation was repaired.
- `map_site` lacked a budget cap; a cap was added.
- Neither tool was used even after repair because the prompt named only two of four tools; tool instructions were corrected.
- Social-network and job-board visits consumed budget without useful evidence; host exclusions were added.
- External discovery reread the vendor's own domain; external-domain filtering was corrected.
- The model exposed zero reasoning tokens. Tool calls were changed to require a logged `reason`, providing an observable action rationale rather than claiming access to hidden reasoning.

The production configuration scored best in the six-vendor comparison. Plain ReAct was close and 30% cheaper; encouraging more agent-framework features worsened results. The source does not provide arm scores or uncertainty estimates.

Two more consequential failures appeared: 17 of 277 kept pages were the project's own marketplace, creating circular evidence, and one uncaught provider error terminated an entire run. Self-evidence exclusion is discussed in ELI-1483; provider-error handling was separate work.
