import importlib.util
import re
from pathlib import Path

import pytest

_EVAL_SPEC = importlib.util.spec_from_file_location(
    "eval_stateful_research", Path(__file__).parents[1] / "scripts" / "eval_stateful_research.py"
)
assert _EVAL_SPEC and _EVAL_SPEC.loader
_EVAL_MODULE = importlib.util.module_from_spec(_EVAL_SPEC)
_EVAL_SPEC.loader.exec_module(_EVAL_MODULE)


class RecordingDriver:
    def __init__(self, results=None):
        self.calls = []
        self.results = results if results is not None else []

    async def execute_query(self, query, **params):
        self.calls.append({"query": query, "params": params})
        return self.results, None, None


@pytest.mark.asyncio
async def test_fetch_relationship_names_matches_by_name_when_no_marketplace_id():
    """Regression test for the 2026-08-20 finding: marketplace_id values in
    TARGETS don't reliably match what's stamped on the Neo4j node (or nothing
    is stamped at all), so the query must be able to key off name alone when
    marketplace_id is None."""
    driver = RecordingDriver(results=[{"name": "Acme"}])

    names = await _EVAL_MODULE.fetch_relationship_names(
        driver, "Suki", None, "Integrates", "Product", "Product"
    )

    assert names == ["Acme"]
    assert len(driver.calls) == 1
    call = driver.calls[0]
    assert call["params"]["name"] == "Suki"
    assert call["params"]["mid"] is None
    # Query text should match on (trimmed, case-insensitive) name AND marketplace_id --
    # either is sufficient (Codex review, PR #49: name-only broke vendors whose
    # canonical node name diverges from TARGETS).
    assert "toLower(trim(a.name))" in call["query"]
    assert "toLower(trim(b.name))" in call["query"]
    assert "marketplace_id" in call["query"]
    # Label restriction is preserved -- this is what keeps name-matching safe
    # (a :Vendor-labeled node can never match a :Product-restricted pattern).
    assert "MATCH (a:Product)" in call["query"]
    assert "(b:Product)" in call["query"]


@pytest.mark.asyncio
async def test_fetch_relationship_names_passes_relation_labels_and_marketplace_id_through():
    driver = RecordingDriver(results=[])

    await _EVAL_MODULE.fetch_relationship_names(
        driver,
        "ExampleSuite",
        "00000000-0000-4000-8000-000000000002",
        "Adopts",
        "HealthSystem",
        "Product",
    )

    call = driver.calls[0]
    assert call["params"]["relation"] == "Adopts"
    assert call["params"]["name"] == "ExampleSuite"
    assert call["params"]["mid"] == "00000000-0000-4000-8000-000000000002"
    assert "MATCH (a:HealthSystem)" in call["query"]
    assert "(b:Product)" in call["query"]


@pytest.mark.asyncio
async def test_fetch_relationship_names_empty_result_returns_empty_list():
    driver = RecordingDriver(results=[])

    names = await _EVAL_MODULE.fetch_relationship_names(
        driver, "Some Vendor With No Edges", None, "Adopts", "HealthSystem", "Product"
    )

    assert names == []


class GraphSimDriver:
    """Fake driver that actually interprets the generated Cypher's WHERE/RETURN
    clauses against an in-memory node/edge graph, instead of just returning
    canned results. This lets tests exercise the real matching behavior the
    query text implies (which side is matched by name/marketplace_id, whether
    self-loops are excluded by identity) rather than merely asserting on
    substrings.

    `edges` is a list of (a, b) pairs, each a dict {"label", "name", "id",
    "mid"} representing one endpoint of a RELATES_TO edge (undirected for our
    purposes here, matching the query's `-[r]-` pattern). `id` is the fake
    elementId -- two dicts with the same `id` are the same node; two dicts
    with different `id`s but the same `name` are distinct same-named nodes
    (the exact scenario the self-loop-by-identity fix has to get right)."""

    def __init__(self, edges):
        self.edges = edges

    async def execute_query(self, query, **params):
        name = params["name"].strip().lower() if params.get("name") else None
        mid = params.get("mid")
        match_a_name = "toLower(trim(a.name)) = toLower(trim($name))" in query
        match_b_name = "toLower(trim(b.name)) = toLower(trim($name))" in query
        match_a_mid = "a.marketplace_id = $mid" in query
        match_b_mid = "b.marketplace_id = $mid" in query
        from_label = re.search(r"MATCH \(a:(\w+)\)", query).group(1)
        to_label = re.search(r"\(b:(\w+)\)", query).group(1)

        results = []
        seen = set()
        for a, b in self.edges:
            if a["label"] != from_label or b["label"] != to_label:
                continue
            if a["id"] == b["id"]:  # elementId(a) <> elementId(b)
                continue
            a_matches = (match_a_name and a["name"].strip().lower() == name) or (
                match_a_mid and mid is not None and a.get("mid") == mid
            )
            b_matches = (match_b_name and b["name"].strip().lower() == name) or (
                match_b_mid and mid is not None and b.get("mid") == mid
            )
            if a_matches:
                out = b["name"]
            elif b_matches:
                out = a["name"]
            else:
                continue
            if out not in seen:
                seen.add(out)
                results.append({"name": out})
        return results, None, None


def _node(label, name, node_id, mid=None):
    return {"label": label, "name": name, "id": node_id, "mid": mid}


@pytest.mark.asyncio
async def test_fetch_relationship_names_adopts_does_not_match_wrong_side_health_system():
    """Regression test for the 2026-08-20 finding: a real live :HealthSystem
    node named "Gifthealth" adopting some unrelated product ("TJM Labs") must
    not leak into GiftHealth-the-vendor's own customer list just because the
    HealthSystem happens to share the vendor's name. Only the Product-side
    ("b") should be matched by name for Adopts."""
    edges = [
        # Wrong-side name collision: a HealthSystem named "GiftHealth" adopts
        # some other product entirely. Must NOT show up as GiftHealth's own
        # customer.
        (_node("HealthSystem", "GiftHealth", "n1"), _node("Product", "TJM Labs", "n2")),
        # The real edge: an actual hospital adopting the GiftHealth product.
        (_node("HealthSystem", "Some Real Hospital", "n3"), _node("Product", "GiftHealth", "n4")),
    ]
    driver = GraphSimDriver(edges)

    names = await _EVAL_MODULE.fetch_relationship_names(
        driver, "GiftHealth", None, "Adopts", "HealthSystem", "Product"
    )

    assert names == ["Some Real Hospital"]
    assert "TJM Labs" not in names


@pytest.mark.asyncio
async def test_fetch_relationship_names_integrates_still_matches_either_side():
    """Regression check for the reason either-side matching was added for
    Integrates in the first place: Suki's real integrations edges sit with
    Suki as the subject ("a") side, e.g. "Suki -> Acme". This must keep
    working after scoping either-side matching down to from_label == to_label."""
    edges = [
        (_node("Product", "Suki", "n1"), _node("Product", "Acme", "n2")),
        (_node("Product", "Elation", "n3"), _node("Product", "Suki", "n4")),
    ]
    driver = GraphSimDriver(edges)

    names = await _EVAL_MODULE.fetch_relationship_names(
        driver, "Suki", None, "Integrates", "Product", "Product"
    )

    assert set(names) == {"Acme", "Elation"}


@pytest.mark.asyncio
async def test_fetch_relationship_names_excludes_self_loop_integrates():
    """Regression test for the 2026-08-20 finding: real self-loop edges exist
    in the graph (e.g. "Acme Sepsis Model" and "TJM Labs" both integrate with
    themselves, confirmed not name-twin nodes -- the SAME node on both ends)."""
    same_node = _node("Product", "Acme Sepsis Model", "n1")
    edges = [
        (same_node, same_node),
        (same_node, _node("Product", "Cerner", "n2")),
    ]
    driver = GraphSimDriver(edges)

    names = await _EVAL_MODULE.fetch_relationship_names(
        driver, "Acme Sepsis Model", None, "Integrates", "Product", "Product"
    )

    assert names == ["Cerner"]
    assert "Acme Sepsis Model" not in names


@pytest.mark.asyncio
async def test_fetch_relationship_names_excludes_self_loop_adopts():
    """Same self-loop exclusion, but on the Adopts (differing-label) branch --
    the WHERE clause should filter true self-loops there too (same node on
    both ends), even though a HealthSystem/Product true self-loop is rarer
    than the Integrates case."""
    same_node_dual_labeled = _node("Product", "TJM Labs", "n1")
    edges = [
        # Same underlying node appearing on both sides -- a genuine self-loop.
        ({**same_node_dual_labeled, "label": "HealthSystem"}, same_node_dual_labeled),
        (_node("HealthSystem", "Real Hospital", "n2"), _node("Product", "TJM Labs", "n3")),
    ]
    driver = GraphSimDriver(edges)

    names = await _EVAL_MODULE.fetch_relationship_names(
        driver, "TJM Labs", None, "Adopts", "HealthSystem", "Product"
    )

    assert names == ["Real Hospital"]


@pytest.mark.asyncio
async def test_fetch_relationship_names_does_not_drop_real_edge_between_distinct_same_named_nodes():
    """Codex review (PR #49), P2: excluding self-loops by NAME instead of
    identity would incorrectly drop a real edge between two DIFFERENT nodes
    that just happen to share a display name -- confirmed live, :HealthSystem
    and :Product nodes both named "ExampleCare" exist. A genuine adoption
    between two such distinct nodes must survive."""
    health_system = _node("HealthSystem", "ExampleCare", "hs-1")
    product = _node("Product", "ExampleCare", "prod-1")  # different node, same name
    edges = [(health_system, product)]
    driver = GraphSimDriver(edges)

    names = await _EVAL_MODULE.fetch_relationship_names(
        driver, "ExampleCare", None, "Adopts", "HealthSystem", "Product"
    )

    # The real HealthSystem "ExampleCare" adopting the real Product "ExampleCare"
    # must be reported, not dropped as a false self-loop.
    assert names == ["ExampleCare"]


@pytest.mark.asyncio
async def test_fetch_relationship_names_falls_back_to_marketplace_id_when_name_diverges():
    """Codex review (PR #49), P1: entity resolution can canonicalize a node
    to a display name that differs from TARGETS (e.g. "Flatiron OncoEMR" in
    TARGETS vs. the real node canonically named "OncoEMR®" --
    tests/test_graphiti_resolve.py:95-121). Matching by marketplace_id must
    still find the node's edges when the name doesn't match."""
    edges = [
        (
            _node("HealthSystem", "Some Cancer Center", "hs-1"),
            _node("Product", "OncoEMR®", "prod-1", mid="00000000-0000-4000-8000-000000000001"),
        ),
    ]
    driver = GraphSimDriver(edges)

    names = await _EVAL_MODULE.fetch_relationship_names(
        driver,
        "Flatiron OncoEMR",  # TARGETS' name -- does NOT match the node's real name
        "00000000-0000-4000-8000-000000000001",  # but marketplace_id does match
        "Adopts",
        "HealthSystem",
        "Product",
    )

    assert names == ["Some Cancer Center"]
