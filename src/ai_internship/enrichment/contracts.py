"""Pydantic contracts for extracted, grounded graph facts."""

from __future__ import annotations

import hashlib
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class IngestionOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    NO_PROFILE = "no_profile"
    NO_FACTS = "no_facts"
    GROUNDING_FAILURE = "grounding_failure"
    RESOLUTION_AMBIGUITY = "resolution_ambiguity"
    PERSISTENCE_FAILURE = "persistence_failure"


TERMINAL_INGESTION_OUTCOMES = frozenset(
    {
        IngestionOutcome.SUCCEEDED.value,
        IngestionOutcome.NO_PROFILE.value,
        IngestionOutcome.NO_FACTS.value,
        IngestionOutcome.GROUNDING_FAILURE.value,
        IngestionOutcome.RESOLUTION_AMBIGUITY.value,
    }
)

AUTHORITATIVE_INGESTION_OUTCOMES = TERMINAL_INGESTION_OUTCOMES - {IngestionOutcome.NO_PROFILE.value}


class ExtractionWork(BaseModel):
    source_id: str
    target_id: str = ""
    target_name: str = ""
    content_hash: str = ""
    profile: str
    extractor_version: str
    revision: int
    extraction_id: str

    @classmethod
    def build(
        cls,
        source_id: str,
        profile: str,
        extractor_version: str,
        *,
        revision: int,
        target_id: str = "",
        target_name: str = "",
        content_hash: str = "",
    ) -> "ExtractionWork":
        payload = (
            f"{target_id}\0{content_hash or source_id}\0{profile}\0{extractor_version}\0{revision}"
        )
        return cls(
            source_id=source_id,
            target_id=target_id,
            target_name=target_name,
            content_hash=content_hash,
            profile=profile,
            extractor_version=extractor_version,
            revision=revision,
            extraction_id=hashlib.sha256(payload.encode()).hexdigest(),
        )


class Assertion(BaseModel):
    subject: str
    subject_type: str
    relation: str | None = None
    object: str | None = None
    object_type: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)
    quote: str
    profile: str = ""


class GroundingRejection(BaseModel):
    code: str
    assertion: Assertion


class CandidateEntity(BaseModel):
    label: str
    name: str
    attributes: dict[str, Any] = Field(default_factory=dict)
    quote: str


class SeededEntity(BaseModel):
    label: str
    name: str
    entity_uuid: str


class ResolvedEntity(CandidateEntity):
    entity_uuid: str | None = None
    status: str = "new"


class IngestionState(BaseModel):
    source_id: str
    profiles: list[str] = Field(default_factory=list)
    assertions: list[Assertion] = Field(default_factory=list)
    outcome: IngestionOutcome | None = None
