"""Portable research assets, independent of the original hosted project."""

from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import braintrust
from braintrust.prompt import PromptSchema
from braintrust.util import LazyValue


def assets_dir() -> Path:
    return Path(os.getenv("RESEARCH_ASSETS_DIR", Path(__file__).parent / "assets"))


def project_name() -> str:
    return os.getenv("BRAINTRUST_PROJECT", "ai-internship-public")


def public_dataset_row(row: dict) -> dict:
    """Export labels with public fixture identities and allowlisted metadata."""
    allowed = {
        "product_name",
        "vendor_name",
        "customer_count",
        "integration_count",
    }
    inp = {k: row["input"][k] for k in ("name", "url") if k in row["input"]}
    inp["product_id"] = str(
        uuid5(NAMESPACE_URL, f"public-research:product:{inp['name'].casefold()}")
    )
    return {
        "input": inp,
        "expected": row.get("expected"),
        "metadata": {k: v for k, v in (row.get("metadata") or {}).items() if k in allowed},
    }


def load_prompt(*, slug: str, version: str | None = None, project=None, api_key=None, **kwargs):
    """Render bundled versions offline, or explicitly use the caller's hosted project.

    The project argument is accepted for migrated experiment compatibility. The
    environment selects the destination; original project identifiers are never used.
    Hosted version IDs differ after bootstrap. Bundled versions use public
    content-derived identifiers.
    """
    pins = json.loads(os.getenv("PROMPT_VERSIONS", "{}"))
    version = version or pins.get(slug)
    source = os.getenv("PROMPT_SOURCE", "local")
    if source == "braintrust":
        key = api_key or os.getenv("BRAINTRUST_API_KEY")
        if not key:
            raise ValueError("Hosted prompts require your own BRAINTRUST_API_KEY")
        return braintrust.load_prompt(
            project=project_name(), slug=slug, version=version, api_key=key, **kwargs
        )
    if source != "local":
        raise ValueError("PROMPT_SOURCE must be local or braintrust")
    if Path(slug).name != slug:
        raise ValueError("Invalid prompt slug")
    path = assets_dir() / "prompts" / f"{slug}.json"
    if not path.is_file():
        raise ValueError(f"Prompt {slug} is not bundled")
    document = json.loads(path.read_text())
    selected = str(version or document["latest"])
    record = next((v for v in document["versions"] if v["version"] == selected), None)
    if record is None:
        raise ValueError(f"Prompt {slug} version {selected} is not bundled")
    schema = PromptSchema.from_dict_deep(
        {
            "id": None,
            "project_id": None,
            "_xact_id": selected,
            "name": document["name"],
            "slug": slug,
            "description": None,
            "prompt_data": record["prompt_data"],
            "tags": [],
        }
    )
    return braintrust.Prompt(
        LazyValue(lambda: schema, use_mutex=True),
        defaults=kwargs.get("defaults") or {},
        no_trace=True,
    )


def load_dataset(name: str) -> list[dict]:
    for path in sorted((assets_dir() / "datasets").glob("*.json")):
        document = json.loads(path.read_text())
        if document["name"] == name:
            return document["rows"]
    raise ValueError(f"Dataset {name} is not bundled")


def init_dataset(*, name: str, project=None, api_key=None) -> list[dict]:
    """Read-only compatibility adapter for historical diagnostic notebooks."""
    return load_dataset(name)


def research_targets() -> dict:
    targets = {
        row["input"]["name"]: {
            "url": row["input"]["url"],
            "marketplace_id": row["input"].get("product_id") or row["metadata"]["product_id"],
        }
        for row in load_dataset("Marketplace Customers Dataset")
    }
    return targets
