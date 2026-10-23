# ELI-1483 — Excluding the marketplace's own evidence

Public research edition of the historical report. Dataset rows, internal review material and private artifacts are omitted. Commands describe the historical experiment; see ../reproduction.md for the supported standalone setup.

External discovery selected pages from the same marketplace that published the reference customer lists. Treating those pages as independent evidence made the evaluation circular.

In a historical six-vendor, three-configuration comparison, 17 of 277 kept pages were self-evidence: 5, 4 and 8 across the arms. For three study products, one such page alone inflated the live recall proxy to 1.0. These are broader-corpus aggregate observations, not public-dataset results.

The marketplace host was added to `EXCLUDED_HOSTS`. Filtering ran before budget was spent in `scrape_url` and `crawl_site`, and again at final selection. This made exclusion a code-enforced boundary rather than a prompt preference. Provider-error handling initially considered with this change was implemented separately.
