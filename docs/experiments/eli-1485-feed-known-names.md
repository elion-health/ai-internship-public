# ELI-1485 — Confirmed customers as external-discovery leads

Public research edition of the historical report. Dataset rows, internal review material and private artifacts are omitted. Commands describe the historical experiment; see ../reproduction.md for the supported standalone setup.

## Question and setup

Would confirmed first-party customer names help external discovery find additional customers? Arm A used the production agent. Arm B supplied confirmed names as search leads and instructed it to avoid reproving them.

Ten vendors from the broader private corpus were selected for at least three first-party-provable customers and at least three customers with only third-party proof. This historical ten-vendor selection is **not** the public ten-product dataset. It contained 151 simulated known names and 89 external-only targets. The known names came from reference evidence, modeling a perfect first-party run and therefore an upper bound rather than realistic first-party extraction.

Both arms had budgets of 25 searches, 15 scrapes, 5 maps and 50 crawl pages. The model was `openai/gpt-5.6-luna`, with live Firecrawl, one run per cell. A target counted as found when fetched text on a kept page named it; this was a discovery proxy, not final relation validation. The optional `known_entities` input defaulted off.

## Reported results

Broader-corpus products are anonymized below; allowed public-product names are retained only for metrics, without relationship rows.

| Study product | Targets | A found | B found | Δ | Known re-proved A → B | B lead searches |
|---|---:|---:|---:|---:|---|---|
| A | 26 | 3 | 4 | +1 | 4 → 1 | 2/14 |
| B | 14 | 0 | 8 | +8 | 3 → 2 | 5/29 |
| C | 10 | 0 | 0 | 0 | 4 → 3 | 0/22 |
| Cadence | 8 | 3 | 7 | +4 | 5 → 3 | 1/23 |
| D | 7 | 1 | 1 | 0 | 7 → 6 | 0/18 |
| E | 6 | 3 | 3 | 0 | 5 → 3 | 8/24 |
| F | 5 | 1 | 1 | 0 | 0 → 0 | 0/11 |
| G | 5 | 2 | 1 | −1 | 1 → 1 | 0/19 |
| H | 4 | 2 | 1 | −1 | 5 → 4 | 2/28 |
| ArriveHealth | 4 | 0 | 0 | 0 | 6 → 7 | 4/22 |
| **Total** | **89** | **15** | **26** | **+11** | **40 → 30** | **22/210 (10%)** |

Reported model cost was $0.41 for A and $0.45 for B across all ten vendors. Each arm kept 15 pages per run. B found 73% more targets in this single comparison, won on three vendors, tied on five and lost one target each on two.

## Behavior and interpretation

The gains came from press-release roundups reached through searches combining a known customer's name with the vendor. The agent also independently used known names as negative search filters. Actual named queries and discovered relationship lists are omitted.

Long lists were often ignored: lists of 31, 22, 19 and 13 names produced no positive-lead searches for four study products. Only vendors supplied at most 14 names produced such searches. The proposed implementation was to pass approximately 5–8 distinctive confirmed names, remove generic descriptions, and test whether explicit negative-filter guidance helped.

This was a single-run exploratory result. ELI-1488 subsequently observed enough run-to-run variation to weaken the original interpretation of the +11 gain. A cap and repeated evaluation were recommendations, not measured outcomes in this report. Historical scratch scripts, traces and per-run results remain private and unavailable here.
