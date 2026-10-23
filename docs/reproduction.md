# Private review and reproduction

This proposed research release is **private pending final Elion review**. Test it with Juan using his own accounts before approval to publish.

## Illustrative data

The bundle contains **ten illustrative product samples**, split across customer and integration files: Suki, FirstHx, Fold Health, Flatiron OncoEMR, Infermedica, AgileMD Clinical Pathways, Cadence, New Lantern, Arrive Health, and Abridge. These are ten products, not twenty separate examples: the two files contain the corresponding profiles for the same ten products.

There are **139 customer labels and 21 integration labels**. These are selected labels, not complete production records. Production product IDs are replaced with deterministic public fixture UUIDs derived from product names; production environment names and administrative metadata are removed. The sample is illustrative, not representative or a benchmark of general accuracy.

The broader datasets, catalog, row-level audits, saved notebook outputs, and outside-sample vendor-level historical results are excluded. The bundle contains **20 prompts with one public adaptation of the latest source version each**. Earlier versions, hosted version identifiers, source-version mappings, private canonical-name tables, customer-frequency tiers and organization-specific marketplace policy wording are excluded. Prompt IDs use content-derived `public-*` identifiers so changed prompts invalidate extraction caches. Fictional teaching fixtures illustrate relationship rules. These adaptations change behavior and are not exact historical replicas. `src/ai_internship/assets/manifest.json` lists products, versions, counts and checksums. Historical aggregate findings are in [research-findings.md](research-findings.md). The private source repository retains the full research artifacts.

## Historical experiment reports

The [experiment index](experiments/EXPERIMENTS.md) links the reviewed public editions of the original reports. They retain methods, quantitative findings and limitations where those can be shared without disclosing the broader dataset. Dataset rows, evidence inventories, reviewer commentary, private annotation instructions, raw outputs and review sheets are omitted. Some data-heavy reports are abridged; implementation comparisons outside the LangGraph-only scope are not republished.

Historical commands and file references describe the original experimental environment. A report being present does not mean its full-corpus runner, traces, private labels or original model are available here. Use this guide for the supported standalone workflow. New artifacts under `docs/experiments/` remain gitignored; only reviewed report files are tracked explicitly.

## Setup and graph run

Runtime model routing uses the OpenRouter client configured by this repository. The public prompt bundle contains no original hosted endpoint selection or version history.

Requires Python 3.14, uv and Docker. Use your own credentials:

```sh
uv sync --frozen
uv run playwright install chromium
cp .env.example .env
docker compose up -d
uv run python scripts/seed_catalog.py --dry-run
uv run python scripts/seed_catalog.py
uv run python scripts/run_stateful_research.py \
  --name Suki --url https://www.suki.ai \
  --profile customers --profile integrations
```

Set `OPENROUTER_API_KEY` and `FIRECRAWL_API_KEY`. Exa is optional. Prompts load locally by default. Seeding uses only identities from the ten records; it inserts no answer relationships. This smaller catalog changes entity resolution compared with the private experiments. The first-party default is up to 1,000 pages; adjust the budget in `.env` before a paid run.

Set `DISCOVERY_EXCLUDED_HOSTS` to comma-separated reference-label publisher and mirror hostnames for your evaluation (for example, `reference.example,mirror.example`). External discovery adds them to search exclusions and rejects their URLs, including subdomains, before fetching. The default contains no organization-specific reference publishers. Configure this explicitly to prevent circular evaluation; do not feed expected labels into discovery. Historical agent variants have separate tool paths, so enforce the same policy in their provider configuration when using them.

Use `--skip-external --first-party-only` together for vendor-only research. `--mode resume` reuses stored state; it does not guarantee source freshness. `--force-profile customers` reruns extraction on stored sources. `scripts/reset_target_state.py --help` describes deletion of target-specific state; use an isolated local database for experiments.

### Cache pages without extraction

```sh
uv run python scripts/cache_first_party_pages.py --product Suki --page-limit 50
```

This uses Firecrawl and Neo4j but makes no model calls. Without `--product`, it visits only the ten bundled targets. It reuses completed inventory and retries pending/failed frontier entries. The root domain is mapped and the product's own URL section is prioritized. Set `RESEARCH_FETCH_CONCURRENCY` to bound parallel fetches (default 8). Cached website content and newly generated outputs remain local/private, not part of the ten-record release.

The graph now uses a five-call semantic panel and whole-page referent checks, including a forced check after supported quote-splice repair. Budget for those model calls. See [pipeline.md](pipeline.md) for routing and failure behavior.

The mapper itself is capped at the page limit before product-path sorting, so pages outside its returned subset cannot be prioritized. The cache command continues to later targets after a target failure, but reports failed targets separately and exits nonzero on target exceptions or recorded page-fetch failures.

## Fresh Braintrust project

Set your own `BRAINTRUST_API_KEY`, then:

```sh
uv run python scripts/bootstrap_braintrust.py --project juan-internship-examples
```

This imports only the ten-product sample and bundled prompts into a **new** project. It refuses an existing project. No original logs, users or traces are imported. After a partial failure, choose a new project name for a retry.

Set `BRAINTRUST_PROJECT=juan-internship-examples`. Keep `PROMPT_SOURCE=local` for bundled prompts; use `braintrust` for hosted prompts. `PROMPT_VERSIONS` maps slugs to bundled version IDs in local mode, or new IDs in hosted mode. Bootstrap records their mapping in ignored `data/runs/braintrust-bootstrap.json`.

```sh
uv run python scripts/eval_stateful_research.py --product Suki --profile customers
```

Evaluation uses the illustrative labels and logs new results to your project. This sample cannot reproduce the original full-corpus metrics.

The evaluation CLI also accepts `--skip-external` to disable external discovery. It still runs extraction; use the cache-only command above when no model work is wanted.

## Research variants and evidence judge

Seven historical LangGraph Exa/Firecrawl agent variants remain accessible through:

```sh
uv run python scripts/run_experiment.py --help
```

All retained variants use `OPENROUTER_API_KEY` for model calls. Use OpenRouter model IDs such as `openai/gpt-5-mini` with `--model`. Search providers still require their own Exa or Firecrawl credentials. Model availability can change; record any override. Routing the historical variants through OpenRouter can differ from their original provider configuration.

The optional external-page judge is **off** by default, controlled by `DISCOVERY_EVIDENCE_JUDGE`. It reads unread selected pages before judging them with a profile-specific question. These reads are additional to the agent's scrape budget. Judge failures retain pages. Prompt-loading failures skip judging.

The adapted A/B harness runs on the ten examples, without historical leads by default:

```sh
for arm in judge-off judge-on; do
  for run in 1 2 3; do
    uv run python scripts/eval_external_arms.py --arm "$arm" --runs 1 \
      --concurrency 1 --out "data/runs/${arm}-run${run}.json"
  done
done
uv run python scripts/summarize_judge_ab.py data/runs
```

The harness resets each target before each run. Optional `--prior-run` accepts your own locally generated categorized audit for lead injection. Do not use expected labels as leads. Diagnostic scripts operate on your newly generated graph; their original full-corpus inputs are not included. Generated data and notebooks are gitignored.

`scripts/audit_extracted_claims.py --since <ISO-timestamp> --out data/runs/claims.md` audits your own graph against the example labels. An unmatched claim is not necessarily false: the shared reference set is intentionally small. Keep generated claim reports private.

## Limits and checks

This remains a research implementation: semantic errors can fail open, old relationships can remain active, and completion status does not always expose partial failures. Graph acceptance is not publication approval. The smaller catalog, current websites, prompt edits and model availability prevent exact historical replication.

```sh
uv run pytest -m 'not e2e and not local_neo4j'
uv run ruff check src tests scripts
uv build
```

Offline checks do not exercise paid providers or a real fresh-account import. Juan's live handoff test and Elion's final review are still required before changing repository visibility.
