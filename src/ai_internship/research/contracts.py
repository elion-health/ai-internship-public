"""Contracts for target-centric stateful research."""

from __future__ import annotations

import hashlib
from enum import StrEnum

from pydantic import BaseModel, Field, model_validator

from ai_internship.discovery.contracts import canonicalize_url
from ai_internship.enrichment.profiles import PROFILES


class ResearchMode(StrEnum):
    INITIAL = "initial"
    RESUME = "resume"


class ResearchRequest(BaseModel):
    name: str
    url: str
    profiles: list[str]
    mode: ResearchMode = ResearchMode.INITIAL
    force_profiles: list[str] = Field(default_factory=list)
    skip_external: bool = False
    first_party_only: bool = False
    # Set only when the caller already knows this target's real marketplace_id
    # (e.g. it's a row in the marketplace dataset). When set, the target's own
    # Product/Vendor node is seeded with this id before crawling starts, so
    # extraction resolves to it instead of ever minting a placeholder node.
    marketplace_id: str | None = None

    @model_validator(mode="after")
    def validate_profiles(self) -> "ResearchRequest":
        if not self.profiles:
            raise ValueError("at least one profile is required")
        if unknown := set(self.profiles) - PROFILES.keys():
            raise ValueError(f"unknown profiles: {', '.join(sorted(unknown))}")
        if len(self.profiles) != len(set(self.profiles)):
            raise ValueError("profiles must be unique")
        if not set(self.force_profiles).issubset(self.profiles):
            raise ValueError("force_profiles must be a subset of profiles")
        return self


class ResearchTarget(BaseModel):
    target_id: str
    name: str
    canonical_url: str

    @classmethod
    def build(cls, name: str, url: str) -> "ResearchTarget":
        canonical_url = canonicalize_url(url)
        target_id = hashlib.sha256(canonical_url.encode()).hexdigest()
        return cls(
            target_id=target_id,
            name=name.strip(),
            canonical_url=canonical_url,
        )
