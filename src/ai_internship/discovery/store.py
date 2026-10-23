"""Neo4j persistence for local discovery and ingestion run state."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Protocol

from ai_internship.enrichment.contracts import (
    TERMINAL_INGESTION_OUTCOMES,
    ExtractionWork,
    GroundingRejection,
)
from ai_internship.research.contracts import ResearchTarget

from .contracts import DiscoveryRun, FrontierRecord, RetainedSource, canonicalize_url
from .deep_agent import _is_excluded_host


class QueryDriver(Protocol):
    async def execute_query(self, query: str, **params: object) -> object: ...


class DiscoveryStore:
    def __init__(self, driver: QueryDriver):
        self._driver = driver

    async def start_run(self, run: DiscoveryRun) -> None:
        properties = run.model_dump(mode="json", exclude_none=True)
        properties["config"] = json.dumps(run.config, sort_keys=True)
        await self._driver.execute_query(
            "MERGE (run:DiscoveryRun {run_id: $run_id}) SET run += $properties",
            run_id=run.run_id,
            properties=properties,
        )

    async def finish_run(self, run_id: str, status: str, metrics: dict[str, object]) -> None:
        await self._driver.execute_query(
            "MATCH (run:DiscoveryRun {run_id: $run_id}) "
            "SET run.status = $status, run.completed_at = $completed_at, run.metrics = $metrics",
            run_id=run_id,
            status=status,
            completed_at=datetime.now(UTC).isoformat(),
            metrics=json.dumps(metrics, sort_keys=True),
        )

    async def start_provider_call(self, *, call_id: str, run_id: str, stage: str, url: str) -> None:
        await self._driver.execute_query(
            "MATCH (run:DiscoveryRun {run_id: $run_id}) "
            "MERGE (call:ProviderCall {call_id: $call_id}) "
            "SET call.run_id = $run_id, call.stage = $stage, call.url = $url, "
            "call.status = 'running', call.started_at = $started_at "
            "MERGE (run)-[:MADE_PROVIDER_CALL]->(call)",
            call_id=call_id,
            run_id=run_id,
            stage=stage,
            url=url,
            started_at=datetime.now(UTC).isoformat(),
        )

    async def finish_provider_call(
        self,
        call_id: str,
        *,
        status: str,
        elapsed_seconds: float,
        error: str | None = None,
    ) -> None:
        await self._driver.execute_query(
            "MATCH (call:ProviderCall {call_id: $call_id}) "
            "SET call.status = $status, call.elapsed_seconds = $elapsed_seconds, "
            "call.error = $error, call.completed_at = $completed_at",
            call_id=call_id,
            status=status,
            elapsed_seconds=elapsed_seconds,
            error=error,
            completed_at=datetime.now(UTC).isoformat(),
        )

    async def summarize_provider_calls(self, run_id: str) -> dict[str, dict[str, int]]:
        result = await self._driver.execute_query(
            "MATCH (:DiscoveryRun {run_id: $run_id})-[:MADE_PROVIDER_CALL]->(call:ProviderCall) "
            "RETURN call.stage AS stage, count(call) AS calls, "
            "count(CASE WHEN call.status = 'succeeded' THEN 1 END) AS succeeded, "
            "count(CASE WHEN call.status = 'failed' THEN 1 END) AS failed, "
            "count(CASE WHEN call.status = 'running' THEN 1 END) AS running",
            run_id=run_id,
        )
        records = result[0] if isinstance(result, tuple) else result
        return {
            record["stage"]: {
                "calls": int(record["calls"]),
                "succeeded": int(record["succeeded"]),
                "failed": int(record["failed"]),
                "running": int(record["running"]),
            }
            for record in records
        }

    async def load_or_create_target(self, target: ResearchTarget) -> ResearchTarget:
        result = await self._driver.execute_query(
            "MERGE (target:ResearchTarget {target_id: $target_id}) "
            "ON CREATE SET target += $properties "
            "ON MATCH SET target.name = $name "
            "RETURN properties(target) AS target",
            target_id=target.target_id,
            name=target.name,
            properties=target.model_dump(mode="json"),
        )
        records = result[0] if isinstance(result, tuple) else result
        return ResearchTarget.model_validate(records[0]["target"]) if records else target

    async def seed_marketplace_entity(
        self, *, marketplace_id: str, name: str, embedder, label: str = "Product"
    ) -> None:
        """Ensure the target's own entity node already exists, carrying its
        real marketplace_id, before crawling starts. Entity resolution
        (graphiti_resolve.py) matches against existing nodes first, so this
        is what makes extraction attach to the real catalog entity instead of
        minting an unlabeled placeholder that never gets a marketplace_id
        (ELI-1289).

        Matched by the marketplace_id property itself, not seed_id --
        graph_seed's bulk import (see graph_seed/seed.py) writes seed_id as
        the composite "{marketplace_id}@{source_id}", not the bare id, so
        matching on seed_id here would miss an already-imported node and
        create a disconnected duplicate instead of enriching the real one
        (Macroscope review, PR #49). seed_id is still set on first creation
        so find_seed_candidates (graph/local.py) can find it; an existing
        node's seed_id is left alone.

        Also writes name_embedding, same as graph_seed's bulk import does
        (see "Embed Graphiti seed entities on import", b5fc999) -- without it
        graphiti's node_similarity_search can never find this node, so any
        page that refers to the target by a name variant (e.g. "AgileMD"
        instead of the full "AgileMD Clinical Pathways") would fail to
        resolve to it and get dropped instead."""
        name_embedding = await embedder.create(input_data=[name.replace("\n", " ")])
        await self._driver.execute_query(
            "MERGE (n:Entity {marketplace_id: $marketplace_id}) "
            "ON CREATE SET n.name = $name, n.uuid = $marketplace_id, "
            "n.seed_id = $marketplace_id "
            "SET n.group_id = 'ai-internship', "
            "n.name_embedding = coalesce(n.name_embedding, $name_embedding) "
            "SET n:$($label)",
            marketplace_id=marketplace_id,
            name=name,
            name_embedding=name_embedding,
            label=label,
        )

    async def has_completed_inventory(
        self, target_id: str, *, map_limit: int | None = None
    ) -> bool:
        result = await self._driver.execute_query(
            "MATCH (target:ResearchTarget {target_id: $target_id}) "
            "RETURN coalesce(target.inventory_completed, false) AS completed, "
            "target.inventory_map_limit AS map_limit, "
            "target.inventory_may_be_truncated AS may_be_truncated",
            target_id=target_id,
        )
        records = result[0] if isinstance(result, tuple) else result
        if not records or not records[0].get("completed"):
            return False
        previous_limit = records[0].get("map_limit")
        if map_limit is not None and (
            previous_limit is None
            or records[0].get("may_be_truncated")
            and map_limit > previous_limit
        ):
            return False
        return True

    async def mark_inventory_completed(
        self,
        target_id: str,
        run_id: str,
        *,
        map_limit: int,
        mapped_count: int,
        inventory_may_be_truncated: bool,
    ) -> None:
        await self._driver.execute_query(
            "MATCH (target:ResearchTarget {target_id: $target_id}) "
            "SET target.inventory_completed = true, target.inventory_run_id = $run_id, "
            "target.inventory_map_limit = $map_limit, "
            "target.inventory_mapped_count = $mapped_count, "
            "target.inventory_may_be_truncated = $inventory_may_be_truncated",
            target_id=target_id,
            run_id=run_id,
            map_limit=map_limit,
            mapped_count=mapped_count,
            inventory_may_be_truncated=inventory_may_be_truncated,
        )

    async def upsert_frontier(self, record: FrontierRecord) -> None:
        await self._driver.execute_query(
            "MATCH (target:ResearchTarget {target_id: $target_id}) "
            "MATCH (run:DiscoveryRun {run_id: $run_id}) "
            "MERGE (frontier:FrontierUrl {target_id: $target_id, canonical_url: $canonical_url}) "
            "ON CREATE SET frontier += $properties "
            "ON MATCH SET frontier.requested_url = $requested_url, "
            "frontier.discovery_method = $discovery_method, "
            "frontier.discovery_run_id = $run_id, frontier.query = $frontier_query, "
            "frontier.queries = reduce(items = coalesce(frontier.queries, []), "
            "item IN $queries | CASE WHEN item IN items THEN items ELSE items + item END), "
            "frontier.discovery_methods = reduce(items = coalesce(frontier.discovery_methods, []), "
            "item IN $discovery_methods | CASE WHEN item IN items THEN items "
            "ELSE items + item END), "
            "frontier.provider_call_ids = reduce(items = coalesce(frontier.provider_call_ids, []), "
            "item IN $provider_call_ids | CASE WHEN item IN items THEN items "
            "ELSE items + item END), "
            "frontier.profiles = reduce(items = coalesce(frontier.profiles, []), "
            "item IN $profiles | CASE WHEN item IN items THEN items ELSE items + item END), "
            "frontier.status = CASE WHEN frontier.status = 'fetched' "
            "THEN 'fetched' ELSE $status END "
            "MERGE (target)-[:TRACKS]->(frontier) "
            "MERGE (run)-[discovered:DISCOVERED]->(frontier)",
            target_id=record.target_id,
            canonical_url=record.canonical_url,
            requested_url=record.requested_url,
            discovery_method=record.discovery_method,
            frontier_query=record.query,
            queries=record.queries,
            discovery_methods=record.discovery_methods,
            provider_call_ids=record.provider_call_ids,
            profiles=record.profiles,
            run_id=record.discovery_run_id,
            status=record.status.value,
            properties=record.model_dump(mode="json"),
        )

    async def list_pending_frontier(
        self, target_id: str, *, origin: str | None = None
    ) -> list[FrontierRecord]:
        result = await self._driver.execute_query(
            "MATCH (:ResearchTarget {target_id: $target_id})-[:TRACKS]->(frontier:FrontierUrl) "
            "WHERE frontier.status IN ['discovered', 'pending', 'failed'] "
            "AND ($origin IS NULL OR frontier.origin = $origin) "
            "RETURN properties(frontier) AS frontier ORDER BY frontier.canonical_url",
            target_id=target_id,
            origin=origin,
        )
        records = result[0] if isinstance(result, tuple) else result
        return [FrontierRecord.model_validate(record["frontier"]) for record in records]

    async def retain_for_target(self, target_id: str, source: RetainedSource) -> None:
        event_id = hashlib.sha256(
            f"{source.discovery_run_id}\0{source.source_id}\0{source.retrieved_at.isoformat()}".encode()
        ).hexdigest()
        source_properties = source.model_dump(mode="json")
        source_properties["provider_metadata"] = json.dumps(
            source.provider_metadata, sort_keys=True
        )
        await self._driver.execute_query(
            "MATCH (target:ResearchTarget {target_id: $target_id}) "
            "MATCH (run:DiscoveryRun {run_id: $run_id}) "
            "MATCH (frontier:FrontierUrl {target_id: $target_id, canonical_url: $canonical_url}) "
            "MERGE (source:RetainedSource {source_id: $source_id}) "
            "ON CREATE SET source += $source "
            "MERGE (target)-[:HAS_SOURCE]->(source) "
            "MERGE (frontier)-[:HAS_VERSION]->(source) "
            "MERGE (run)-[:RETRIEVED]->(source) "
            "MERGE (event:RetrievalEvent {event_id: $event_id}) SET event += $event "
            "MERGE (source)-[:LATEST_RETRIEVAL]->(event) "
            "SET frontier.status = 'fetched', frontier.latest_source_id = $source_id, "
            "frontier.latest_content_hash = $content_hash, frontier.last_error = null",
            target_id=target_id,
            run_id=source.discovery_run_id,
            canonical_url=canonicalize_url(source.final_url),
            source_id=source.source_id,
            source=source_properties,
            content_hash=source.content_hash,
            event_id=event_id,
            event={
                "event_id": event_id,
                "run_id": source.discovery_run_id,
                "requested_url": source.requested_url,
                "retrieved_at": source.retrieved_at.isoformat(),
                "status": "fetched",
            },
        )

    async def mark_frontier_failed(self, record: FrontierRecord, error: str) -> None:
        await self._driver.execute_query(
            "MATCH (frontier:FrontierUrl {target_id: $target_id, canonical_url: $canonical_url}) "
            "SET frontier.status = 'failed', frontier.last_error = $error, "
            "frontier.attempts = coalesce(frontier.attempts, 0) + 1",
            target_id=record.target_id,
            canonical_url=record.canonical_url,
            error=error,
        )

    async def plan_extraction(
        self,
        target_id: str,
        source_id: str,
        profile: str,
        extractor_version: str,
        *,
        target_name: str = "",
        force: bool = False,
    ) -> ExtractionWork | None:
        result = await self._driver.execute_query(
            "MATCH (:ResearchTarget {target_id: $target_id})-[:HAS_SOURCE]->"
            "(source:RetainedSource {source_id: $source_id}) "
            "MATCH (:ResearchTarget {target_id: $target_id})-[:HAS_SOURCE]->"
            "(equivalent:RetainedSource {content_hash: source.content_hash}) "
            "OPTIONAL MATCH (run:ExtractionRun)-[:EXTRACTED]->(equivalent) "
            "WHERE run IS NULL OR (run.profile = $profile "
            "AND run.extractor_version = $extractor_version) "
            "RETURN source.content_hash AS content_hash, run.revision AS revision, "
            "run.outcome AS outcome "
            "ORDER BY run.revision DESC LIMIT 1",
            target_id=target_id,
            source_id=source_id,
            profile=profile,
            extractor_version=extractor_version,
        )
        records = result[0] if isinstance(result, tuple) else result
        latest_record = records[0] if records else {}
        latest = latest_record if latest_record.get("revision") is not None else None
        if latest and latest["outcome"] in TERMINAL_INGESTION_OUTCOMES and not force:
            return None
        revision = (
            int(latest["revision"])
            if latest and not force
            else int(latest["revision"]) + 1
            if latest
            else 1
        )
        content_hash = latest_record.get("content_hash", "")
        return ExtractionWork.build(
            source_id,
            profile,
            extractor_version,
            revision=revision,
            target_id=target_id,
            target_name=target_name,
            content_hash=content_hash,
        )

    async def start_extraction(self, work: ExtractionWork) -> None:
        await self._driver.execute_query(
            "MATCH (source:RetainedSource {source_id: $source_id}) "
            "MERGE (run:ExtractionRun {extraction_id: $extraction_id}) "
            "SET run += $properties, run.outcome = 'running' "
            "MERGE (run)-[:EXTRACTED]->(source)",
            source_id=work.source_id,
            extraction_id=work.extraction_id,
            properties=work.model_dump(mode="json"),
        )

    async def finish_extraction(
        self,
        work: ExtractionWork,
        outcome: str,
        grounding_rejections: list[GroundingRejection],
    ) -> None:
        await self._driver.execute_query(
            "MATCH (run:ExtractionRun {extraction_id: $extraction_id}) "
            "SET run.outcome = $outcome, run.grounding_rejections = $grounding_rejections, "
            "run.completed_at = $completed_at",
            extraction_id=work.extraction_id,
            outcome=outcome,
            grounding_rejections=json.dumps(
                [item.model_dump(mode="json") for item in grounding_rejections], sort_keys=True
            ),
            completed_at=datetime.now(UTC).isoformat(),
        )

    async def load_source(self, source_id: str) -> RetainedSource | None:
        result = await self._driver.execute_query(
            "MATCH (source:RetainedSource {source_id: $source_id}) "
            "RETURN properties(source) AS source",
            source_id=source_id,
        )
        records = result[0] if isinstance(result, tuple) else result
        if not records:
            return None
        values = records[0]["source"]
        values["provider_metadata"] = json.loads(values.get("provider_metadata") or "{}")
        return RetainedSource.model_validate(values)

    async def list_target_source_ids(
        self, target_id: str, *, first_party_only: bool = False
    ) -> list[str]:
        # first_party_only restricts to sources acquired via the first-party fetch path
        # (RetainedSource.entrypoint == "first_party_acquisition", set in
        # research/acquisition.py), excluding sources acquired via external discovery --
        # ELI-1236, so a vendor comparison can be scoped to first-party sources only
        # instead of re-extracting from every external source already in Neo4j.
        result = await self._driver.execute_query(
            "MATCH (:ResearchTarget {target_id: $target_id})-[:HAS_SOURCE]->"
            "(source:RetainedSource) "
            "WHERE coalesce(source.quality_status, 'eligible') = 'eligible' "
            "AND ($first_party_only = false OR source.entrypoint = 'first_party_acquisition') "
            # final_url is collected, not carried bare: as a bare term it joins
            # content_hash as a grouping key, so one page retained under two
            # URLs comes back twice and gets extracted twice (PR #82 review).
            "WITH source.content_hash AS content_hash, min(source.source_id) AS source_id, "
            "collect(source.final_url) AS final_urls "
            "RETURN source_id, final_urls ORDER BY source_id",
            target_id=target_id,
            first_party_only=first_party_only,
        )
        records = result[0] if isinstance(result, tuple) else result
        # Applies EXCLUDED_HOSTS here too, not just at discovery time: a page
        # that redirected to an excluded host is only recognisable by its final
        # URL, which discovery never saw, so without this check a resumed run
        # still hands it to extraction.
        return [
            record["source_id"]
            for record in records
            if not any(_is_excluded_host(url) for url in record["final_urls"] if url)
        ]

    async def list_target_frontier_urls(self, target_id: str) -> list[str]:
        result = await self._driver.execute_query(
            "MATCH (:ResearchTarget {target_id: $target_id})-[:TRACKS]->(frontier:FrontierUrl) "
            "RETURN frontier.canonical_url AS url ORDER BY url",
            target_id=target_id,
        )
        records = result[0] if isinstance(result, tuple) else result
        return [record["url"] for record in records]
