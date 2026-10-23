"""ELI-1432 -- scale ELI-1252's error_taxonomy_extract.py from 7 vendors to
all 61 marketplace vendors in the 61-product-runner run.

error_taxonomy_extract.py's VENDORS list is built from
build_frozen_case_set.MARKETPLACE_IDS, a 9-vendor name->marketplace_id
lookup -- but confirmed by reading run_customers_pipeline/
run_integrations_pipeline/get_fetched_sources (scripts/pipeline_functions.py,
scripts/build_frozen_case_set.py): none of them actually use marketplace_id,
every Neo4j/Braintrust lookup in that chain matches by vendor `name` alone.
So swapping the module's VENDORS global to the 61 names in
eval_stateful_research.TARGETS (61-product-runner branch/worktree) is safe
and doesn't require touching anything else in error_taxonomy_extract.py.

TARGETS is read via ast.literal_eval directly from the worktree file rather
than `import`ed, so this script never puts the worktree's scripts/ directory
on sys.path -- that would shadow this checkout's own eval_stateful_research.py
(38-entry TARGETS, no --score-only/--concurrency flags) and
error_taxonomy_extract.py copies with the worktree's, silently swapping in
the wrong module for anything imported after it.

Usage: uv run python scripts/error_taxonomy_extract_eli1432.py
Output: data/eli-1432-error-taxonomy/raw_candidates.json
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import error_taxonomy_extract as ete
import vendor_fetch_baseline as vfb

from ai_internship.public_assets import research_targets

TARGETS = research_targets()
assert TARGETS, "No illustrative products bundled"

ete.VENDORS = list(TARGETS)
ete.DATA_DIR = Path(__file__).parent.parent / "data" / "eli-1432-error-taxonomy"
ete.OUTPUT_PATH = ete.DATA_DIR / "raw_candidates.json"

# run_fetch_stage (pipeline_functions.py) looks vendor URLs up in
# vendor_fetch_baseline.VENDORS -- a name->url map that comment there says is
# meant to be "kept in sync with eval_stateful_research.py's TARGETS" but
# only actually covers ~25 of the 61. Extend it with every TARGETS URL
# (dict union, not replace, so vfb's own entries -- some of which differ
# subtly from a raw TARGETS url after vfb-specific fixes -- still win for
# any vendor already present there).
vfb.VENDORS = {**{name: t["url"] for name, t in TARGETS.items()}, **vfb.VENDORS}

if __name__ == "__main__":
    asyncio.run(ete.main())
