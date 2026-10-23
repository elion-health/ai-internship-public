"""Stable contracts shared by discovery entrypoints and ingestion."""

from __future__ import annotations

import hashlib
from datetime import datetime
from enum import StrEnum
from typing import Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

from pydantic import BaseModel, Field


class FrontierStatus(StrEnum):
    DISCOVERED = "discovered"
    PENDING = "pending"
    FETCHED = "fetched"
    FAILED = "failed"
    EXCLUDED = "excluded"
    UNCHANGED = "unchanged"
    REMOVED = "removed"


class DiscoveryStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    PROVIDER_FAILURE = "provider_failure"


TRACKING_KEYS = {"fbclid", "gclid", "mc_cid", "mc_eid"}
ASSET_SUFFIXES = {".css", ".gif", ".ico", ".jpeg", ".jpg", ".js", ".png", ".svg", ".webp"}


def canonicalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    hostname = (parts.hostname or "").lower()
    if hostname.startswith("www."):
        hostname = hostname.removeprefix("www.")
    default_port = 80 if scheme == "http" else 443 if scheme == "https" else None
    netloc = hostname if parts.port in {None, default_port} else f"{hostname}:{parts.port}"
    path = "/" + "/".join(segment for segment in parts.path.split("/") if segment)
    if path != "/":
        path = path.rstrip("/")
    query = urlencode(
        sorted(
            (key, value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
            if not key.lower().startswith("utm_") and key.lower() not in TRACKING_KEYS
        )
    )
    return urlunsplit((scheme, netloc, path, query, ""))


def technical_exclusion_reason(url: str) -> str | None:
    parts = urlsplit(canonicalize_url(url))
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        return "unsupported_url"
    if any(parts.path.lower().endswith(suffix) for suffix in ASSET_SUFFIXES):
        return "unsupported_asset"
    if any(token in parts.path.lower() for token in ("/login", "/wp-admin", "/sign-in")):
        return "authentication"
    query_keys = {key.lower() for key, _ in parse_qsl(parts.query)}
    if "calendar" in parts.path.lower() and query_keys & {"day", "month", "year"}:
        return "crawler_trap"
    # ELI-1318: a "developer." subdomain is API/SDK reference documentation --
    # a well-established industry convention (developer.suki.ai,
    # developer.imohealth.com) -- not a page that ever describes real
    # marketplace customers or integration partners. Mentions of AI coding
    # tools (Claude Code, Cursor, VS Code) on these pages describe developers
    # building against the vendor's API, not real business relationships.
    # Checked on the subdomain specifically, not any "developer" substring in
    # the path, so a real page like "suki.ai/careers/developer-relations" is
    # unaffected.
    if parts.hostname.startswith("developer."):
        return "developer_documentation"
    # ELI-1307: sitemap.xml/page-sitemap.xml/news-sitemap.xml etc. are machine-
    # readable URL lists, not prose -- but the grounding check only verifies a
    # string appears somewhere in the source, and a URL that's genuinely inside
    # a sitemap trivially passes that test, so these were getting extracted as
    # if they were real pages. Checked on the FILENAME specifically (last path
    # segment, must end in .xml and contain "sitemap"), not any "sitemap"
    # substring in the path, so a real page like "/resources/sitemap-of-
    # features" is unaffected. robots.txt is the other standard machine-
    # readable file crawlers should never treat as prose.
    filename = parts.path.rsplit("/", 1)[-1].lower()
    if filename == "robots.txt" or (
        filename.endswith((".xml", ".xml.gz")) and "sitemap" in filename
    ):
        return "sitemap_or_robots_file"
    return None


def _identity(final_url: str, content: str) -> tuple[str, str]:
    content_hash = hashlib.sha256(content.encode()).hexdigest()
    source_id = hashlib.sha256(
        f"{canonicalize_url(final_url)}\0{content_hash}".encode()
    ).hexdigest()
    return source_id, content_hash


class DiscoveryRun(BaseModel):
    run_id: str = Field(default_factory=lambda: str(uuid4()))
    thread_id: str | None = None
    entrypoint: str
    config: dict[str, object] = Field(default_factory=dict)
    started_at: datetime
    completed_at: datetime | None = None
    status: DiscoveryStatus = DiscoveryStatus.RUNNING


class RetainedSource(BaseModel):
    source_id: str
    requested_url: str
    final_url: str
    title: str | None = None
    retrieved_at: datetime
    discovery_run_id: str
    entrypoint: str
    content: str
    raw_content: str | None = None
    content_hash: str
    provider_metadata: dict[str, object] = Field(default_factory=dict)
    quality_status: str = "eligible"
    quality_reason: str | None = None
    parent_url: str | None = None
    depth: int = 0

    @classmethod
    def build(cls, **values: object) -> "RetainedSource":
        source_id, content_hash = _identity(str(values["final_url"]), str(values["content"]))
        return cls(**values, source_id=source_id, content_hash=content_hash)

    def with_recomputed_identity(self) -> "RetainedSource":
        source_id, content_hash = _identity(self.final_url, self.content)
        return self.model_copy(update={"source_id": source_id, "content_hash": content_hash})


class FrontierRecord(BaseModel):
    target_id: str
    canonical_url: str
    requested_url: str
    origin: Literal["first_party", "external"]
    status: FrontierStatus
    discovery_method: str
    discovery_run_id: str
    query: str | None = None
    queries: list[str] = Field(default_factory=list)
    discovery_methods: list[str] = Field(default_factory=list)
    provider_call_ids: list[str] = Field(default_factory=list)
    profiles: list[str] = Field(default_factory=list)
    parent_url: str | None = None
    depth: int = 0
    attempts: int = 0
    policy_reason: str | None = None
    last_error: str | None = None


class RetrievalEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid4()))
    run_id: str
    requested_url: str
    retrieved_at: datetime
    status: FrontierStatus
    error: str | None = None


class DiscoveryCandidate(BaseModel):
    url: str
    title: str | None = None
    snippet: str | None = None
    parent_url: str | None = None
    depth: int = 0
    queries: list[str] = Field(default_factory=list)
    discovery_methods: list[str] = Field(default_factory=list)
    provider_call_ids: list[str] = Field(default_factory=list)
    profiles: list[str] = Field(default_factory=list)


class ExternalDiscoveryResult(BaseModel):
    observed_candidates: list[DiscoveryCandidate] = Field(default_factory=list)
    selected_candidates: list[DiscoveryCandidate] = Field(default_factory=list)
    prefetched_pages: list[object] = Field(default_factory=list)
    failures: list[tuple[str, str]] = Field(default_factory=list)
    search_calls: int = 0
    scrape_calls: int = 0
    # Every tool call the agent made, with the `reason` it was required to
    # give: the audit trail behind the selected candidates.
    action_log: list[dict] = Field(default_factory=list)
    # ELI-1490: one record per page the final selection considered --
    # {url, basis, accepted, organisations, why} -- so the raw selection can
    # be reconstructed from a judged one and the judge's names reach extraction.
    judgements: list[dict] = Field(default_factory=list)
