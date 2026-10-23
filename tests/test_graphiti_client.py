from ai_internship.config import Settings
from ai_internship.graph import graphiti_client


def test_graphiti_uses_openrouter_for_1536_dimension_embeddings(monkeypatch):
    captured = {}

    class FakeGraphiti:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(graphiti_client, "Graphiti", FakeGraphiti)

    graphiti_client.build_graphiti_client(Settings(openrouter_api_key="router-key"))

    assert captured["embedder"].config.api_key == "router-key"
    assert captured["embedder"].config.base_url == graphiti_client.OPENROUTER_BASE_URL
    assert captured["embedder"].config.embedding_model == "openai/text-embedding-3-small"
    assert captured["embedder"].config.embedding_dim == 1536
    assert captured["cross_encoder"].config.api_key == "router-key"
    assert captured["cross_encoder"].config.base_url == graphiti_client.OPENROUTER_BASE_URL
