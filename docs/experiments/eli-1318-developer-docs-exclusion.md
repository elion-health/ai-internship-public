# ELI-1318 — Developer documentation and build-time tools

Public research edition of the historical report. Dataset rows, internal review material and private artifacts are omitted. Commands describe the historical experiment; see ../reproduction.md for the supported standalone setup.

## Problem and two-part correction

Developer/API documentation produced 27 documented customer or integration errors involving build-time tooling rather than customer-facing relationships. URL filtering alone could not catch every case.

1. `technical_exclusion_reason()` excluded hostnames beginning with `developer.` before fetching. This caught 26 of 27 documented URLs.
2. The integration verifier's `is_customer_facing_integration` field was extended to distinguish tools used to write code against an API from customer-facing interoperability. Matching schema and semantic-prompt changes addressed the remaining main-domain case.

Named tool/relationship examples are omitted; this distinction concerns their role in the quoted claim, not whether a technology can ever be a valid integration endpoint.

## Historical verification

| Check | Result |
|---|---:|
| Documented URLs excluded by hostname rule | 26/27 |
| Content-based error controls, four cases × five runs | 20/20 rejected |
| Genuine integration control × five runs | 5/5 accepted |
| Existing edges on affected developer subdomains examined | 68 across four products |
| Valid relationships found in that existing-edge audit | 0 |

Together, the two mechanisms covered the 27 documented failures. The historical suite reported 361 passes and one pre-existing ingestion-graph failure, with no newly identified regressions.

## Resumable LangGraph behavior

Discovery-time screening was insufficient for a checkpointed workflow. Once inventory was marked complete, a resumed run could skip mapping while still having previously queued `PENDING` URLs. Those URLs would bypass a newly introduced exclusion.

The correction reapplied `technical_exclusion_reason()` inside `_fetch_pending`, immediately before scraping, updating excluded frontier records to `EXCLUDED`. A regression test reproduced a stale pending URL being scraped before the change and skipped afterward.

A second correction moved screening before the fetch-limit slice. If excluded records sorted first, slicing before exclusion let them consume slots and displaced valid URLs. The final order screened the entire pending list, removed excluded records, then applied `max_calls`. A regression test covered this ordering. The fake store was also updated to remove excluded entries from its pending list, matching the real store's status filtering.

## Tradeoff

Excluding an entire developer subdomain can remove genuine technical-integration documentation on a future product. The 68-edge audit found no such loss in the tested four-product population, but does not establish safety for unseen sites. A narrower content-aware policy would be warranted if genuine evidence is found there. The pending-URL correction also does not itself purge already fetched content; ELI-1432 later identified that distinct stale-source problem.
