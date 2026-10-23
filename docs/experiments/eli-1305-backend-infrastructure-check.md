# ELI-1305 — Backend infrastructure versus customer-facing integrations

Public research edition of the historical report. Dataset rows, internal review material and private artifacts are omitted. Commands describe the historical experiment; see ../reproduction.md for the supported standalone setup.

## Problem and implementation

Six documented false positives described hosting or AI components used to build a vendor's product as if they were customer-facing integrations. The semantic verdict gained `is_customer_facing_integration`, applied only to `Integrates` assertions and included in ELI-1304's existing majority vote.

Matching prompt examples distinguished backend dependencies from genuine interoperability. Words such as partnership or leveraging were not sufficient rejection signals: valid integrations can use the same language. The relevant distinction is whether the named external system's identity is the point of the interoperability claim or merely a swappable implementation dependency.

## Historical verification

The six error cases and three genuine integrations were tested against `openai/gpt-5.4-mini` through OpenRouter, five repeated runs per case, using real evidence.

| Outcome | Result |
|---|---:|
| Documented error cases rejected in every run | 6/6 |
| Genuine integrations accepted in every run | 3/3 |
| Error cases caught by new field in at least one run | 3/6 |
| Error cases caught only by pre-existing checks | 3/6 |

Two cases consistently triggered the new infrastructure reason. A third triggered it in four of five runs and an existing unsupported-claim check once. The other three consistently triggered existing semantic reasons. Final acceptance/rejection was stable even where the first rejection reason varied.

## Limitations and follow-up

The new field was not independently responsible for all six rejections. Its coverage should be rechecked if other semantic checks change. No full-corpus evaluation measured the earlier proposed F1 change from .279 to .282.

The field description was subsequently narrowed because consuming a third-party API can be either backend implementation or genuine customer-facing interoperability. The five-run validation predates that wording correction; the revised description was not revalidated live in this experiment. Historical hosted-prompt synchronization was also a dependency, rather than an artifact included with this public report.
