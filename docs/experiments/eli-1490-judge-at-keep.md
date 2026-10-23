# ELI-1490 — A judge at the keep decision

Public research edition of the historical report. Dataset rows, internal review material and private artifacts are omitted. Commands describe the historical experiment; see ../reproduction.md for the supported standalone setup.

## Problem and proposed mechanism

The external-discovery agent had 25 searches and 15 reads per vendor. Search decisions benefited from accumulated context, but the agent kept 93% of what it read. In the historical 61-vendor baseline, only 290 of 887 kept pages named a reference customer. This was a reference-matching proxy, not a complete independent quality assessment.

A focused model call was added after selection: **read → judge → keep**. Any selected but unread page was fetched before judgment, so the judge saw page text rather than just a search snippet. It received the vendor name and URL, page URL and first 8,000 characters of content. Structured output contained an evidence decision, named entities and a supporting passage.

Separate questions were used for customer adoption and product integration. A valid integrations page may name no customer organization; using the customer question for both profiles would reject useful integration evidence. The judge assessed explicit relationship evidence rather than mere co-mention or generic compatibility.

## Historical implementation

The production changes concerned discovery judgment, a `DISCOVERY_EVIDENCE_JUDGE` configuration switch, runtime construction, a `judgements` result field and persistence. A related persistence defect caused one page saved under two URLs to be extracted twice; deduplication corrected it. The judge was disabled by default.

The historical evaluation harness ran arms separately, reset stored state between runs and summarized results. Batch deletion prevented large resets timing out. Tests covered judgment, duplicate-page handling and the switch. These corpus-specific scripts, raw runs and charts are unavailable in this public edition; paths in the historical source do not describe a supported public workflow.

## Measurement

External discovery plus extraction ran for 61 vendors, with first-party discovery stubbed, **three runs per arm**. These are exploratory private-corpus results, not public ten-product results. Two denominators were used: relations external discovery could potentially find, and all reference relations.

### External-owned denominator

| Profile and arm | Precision | Recall | F1 |
|---|---:|---:|---:|
| Customers, off | 0.178 | 0.187 | 0.182 |
| Customers, on | 0.195 | 0.210 | 0.202 |
| Integrations, off | 0.136 | 0.213 | 0.166 |
| Integrations, on | 0.186 | 0.291 | 0.226 |

### All-reference denominator

| Profile and arm | Precision | Recall | F1 |
|---|---:|---:|---:|
| Customers, off | 0.456 | 0.188 | 0.265 |
| Customers, on | 0.478 | 0.202 | 0.284 |
| Integrations, off | 0.528 | 0.214 | 0.304 |
| Integrations, on | 0.499 | 0.202 | 0.287 |

Only the external-owned integrations F1 comparison separated across all repeated observations: on ranged 0.220–0.238, off 0.143–0.199. Other comparisons overlapped the observed run spread. This is an exploratory separation, not a population-level significance claim.

Relations produced per run:

| Profile | Off | On |
|---|---|---|
| Customers | 288 / 341 / 299 | 317 / 310 / 323 |
| Integrations | 87 / 118 / 103 | 87 / 100 / 117 |

## Decision and limitations

The flag remained off. The gain was narrow, and all-reference integrations F1 moved downward. Later quote-contiguity and referent work addressed overlapping errors downstream; incremental value on top of those changes was not measured.

Evaluation requires live discovery in both arms because judgment changes retained pages and the resulting retrieval/extraction path. A fixed cached corpus cannot reproduce the complete intervention. No live discovery was run to prepare this public report.
