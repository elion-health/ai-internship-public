# ELI-1487 — Which discovery calls produce useful pages?

Public research edition of the historical report. Dataset rows, internal review material and private artifacts are omitted. Commands describe the historical experiment; see ../reproduction.md for the supported standalone setup.

## Measurement

The external-discovery agent was measured on ten historical study vendors at a reported cost of $0.44. This was a broader-corpus selection, not the public ten-product dataset. Per-vendor budgets were 25 searches and 15 page reads.

“Useful” meant a call led to a kept page naming a customer already in the reference set. It did not mean independent confirmation of every relation or discovery of a new customer.

| Call type | Calls | Useful |
|---|---:|---:|
| All searches | 211 | 40% |
| Agent-selected, customer-name search | 41 | 56% |
| Agent-selected, site-restricted | 38 | 42% |
| Agent-selected, generic keywords | 112 | 37% |
| Two hardcoded opening searches | 20 | 20% |
| Read a page | 87 | 61% |
| List site pages | 19 | 37% |
| Crawl a site | 1 | 0% |

## Findings

The agent kept 93% of pages it read, suggesting final selection supplied little filtering. Name searches selected in context outperformed fixed opening searches in this sample. Customer-owned websites scored 100% on the reference-based usefulness measure but represented only 9% of kept pages; press wires scored 40% and represented about one quarter. The single crawl call provides little basis for a general claim about crawling.

The report recommended removing fixed opening searches and improving page-read selection. These comparisons are observational, with different query contexts and small category samples.

## Budget race

The experiment found caps exceeded: 28 searches against 25, 17 reads against 15 for Cadence, and 6 maps against 5 in another study run. Concurrent LangChain tool execution allowed multiple calls through a check → call → increment sequence. A lock was added to reserve a slot before a provider call and release it on failure. Earlier budget reports therefore understated actual use. The measurement scripts and action logs are private and unavailable here.
