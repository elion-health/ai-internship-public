"""Deterministic eligibility checks for fetched research content."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ContentAssessment:
    eligible: bool
    reason: str | None = None
    retryable: bool = False


@dataclass(frozen=True)
class PageRelevance:
    eligible: bool
    method: str = "normalized_target_name_v1"
    reason: str | None = None


def assess_content(content: str, *, minimum_characters: int) -> ContentAssessment:
    normalized = content.strip()
    if normalized.lower() in {"loading ...", "loading...", "loading"}:
        return ContentAssessment(False, "loading_placeholder", True)
    if len(normalized) < minimum_characters:
        return ContentAssessment(False, "too_short", True)
    lines = [line.strip() for line in normalized.splitlines() if line.strip()]
    if lines and all(re.fullmatch(r"(?:[-*]\s*)?\[[^]]+\]\([^)]*\)", line) for line in lines):
        return ContentAssessment(False, "navigation_only")
    return ContentAssessment(True)


def assess_page_relevance(content: str, target_name: str) -> PageRelevance:
    """Require every significant word of `target_name` to appear in `content` as a
    whole word, not necessarily contiguous -- so a paraphrase like "Flatiron
    Health OncoEMR" still counts as mentioning the target "Flatiron OncoEMR"."""

    def normalize(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()

    target_tokens = [token for token in normalize(target_name).split() if token]
    body = f" {normalize(content)} "
    if target_tokens and all(f" {token} " in body for token in target_tokens):
        return PageRelevance(True)
    return PageRelevance(False, reason="target_not_mentioned")
