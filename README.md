# AI internship research - Agents for product enrichment

Research on evidence-backed customer and integration enrichment, developed during Juan's internship at Elion.

This repository contains the stateful research graph, research variants, one public adaptation of the latest version of each prompt, and ten illustrative product samples. The broader datasets remain private.

## Setup

Requires Python 3.14, [uv](https://docs.astral.sh/uv/), and Docker for local Neo4j.

```sh
uv sync --frozen
uv run playwright install chromium
cp .env.example .env
docker compose up -d
```

Supply your own OpenRouter and Firecrawl keys. Exa is optional. Bundled prompts load locally by default; Braintrust is optional for graph runs. To log runs and run hosted evaluations, create a new project in your own Braintrust account using the bootstrap command documented in `docs/reproduction.md`.

## Scope

The runtime follows upstream main at `93586bb` (PRs #90, #91, #93–98), with account-independent asset loading and the restricted sample. Full-corpus benchmark builders, confidence fitting/search scripts and review artifacts remain in the private source repository; confidence scoring is not deployed in the runtime.

The graph maps a vendor website, retains fetched sources, extracts customer/integration assertions, resolves catalog entities, checks supporting evidence, and then searches external sources. Retained sources and extraction versions support reuse.

The ten products are Suki, FirstHx, Fold Health, Flatiron OncoEMR, Infermedica, AgileMD Clinical Pathways, Cadence, New Lantern, Arrive Health, and Abridge: 139 customer labels and 21 integration labels in total. Product IDs are synthetic public fixture UUIDs; production metadata is removed. These are selected labels, not complete production records. See [reproduction](docs/reproduction.md) and [aggregate findings](docs/research-findings.md). These examples and adapted prompts cannot reproduce the private full-corpus benchmark.

Code is MIT licensed; see `LICENSE`. Third-party source quotations remain attributable to their original publishers; the code license does not grant new rights in those materials.

## Start here

- [Pipeline walkthrough](docs/pipeline.md): follow one source through acquisition, extraction, evidence checks and persistence.
- [Reproduction guide](docs/reproduction.md): seed the example catalog, run research, cache pages, and bootstrap your own Braintrust project.
- [Aggregate findings](docs/research-findings.md): measured results and their limits, including quote repair and confidence-score research.
- [Experiment reports](docs/experiments/EXPERIMENTS.md): reviewed public editions of the research write-ups, with dataset rows and private review material removed.
- [`tests/test_ingestion_graph.py`](tests/test_ingestion_graph.py): executable examples of acceptance and rejection rules.

To explore without credentials or services:

```sh
uv run pytest -m 'not e2e and not local_neo4j'
```

## How it works

**Two ways to find pages, one shared validation workflow.** The first-party path maps a vendor's domain, prioritizes its product section and caches pages. The external agent searches under a budget. Both feed the same extraction and validation code.

Extraction proposes customer and integration relationships with quoted evidence. Entity resolution maps names to catalog identities. Grounding checks the source text, repairs supported spliced quotes, and asks a whole-page referent check when needed. Five semantic model calls vote on each criterion before accepted relationships are persisted with source provenance. Failures can still fail open; this is research code, not a publication gate.

| Code | Responsibility |
|---|---|
| [`research/graph.py`](src/ai_internship/research/graph.py) | Acquisition and extraction orchestration |
| [`research/acquisition.py`](src/ai_internship/research/acquisition.py) | First-party mapping, bounded concurrent fetching and reuse |
| [`discovery/deep_agent.py`](src/ai_internship/discovery/deep_agent.py) | External search tools and budgets |
| [`discovery/store.py`](src/ai_internship/discovery/store.py) | Frontier, retained pages, run state and extraction bookkeeping |
| [`enrichment/graph.py`](src/ai_internship/enrichment/graph.py) | Shared relationship-validation workflow |
| [`enrichment/grounding.py`](src/ai_internship/enrichment/grounding.py) | Text checks and semantic voting |
| [`enrichment/referent_resolution.py`](src/ai_internship/enrichment/referent_resolution.py) | Whole-page confirmation, with separate first-party prompts |
| [`enrichment/profiles.py`](src/ai_internship/enrichment/profiles.py) | Profile contracts and prompt-version hashing |

The seven agents under `agents/customers/experiments/` are historical LangGraph variants, not the main graph's execution path. All retained research-agent approaches use LangGraph, including the main graph's DeepAgents-based discovery. This release bundles prompts locally and requires no Elion account or private graph dump.
