"""Graphiti client setup for ELI-1057 (moving entity matching onto graphiti's
built-in fuzzy matching instead of our own hand-rolled exact-match lookup in
resolve.py). Reuses credentials already configured for this project: the
same Neo4j instance, OpenRouter for the LLM tier (same pattern as
research/runtime.py). Graphiti's OpenAI-compatible embedder is pointed at
OpenRouter for embeddings too."""

from __future__ import annotations

from graphiti_core import Graphiti
from graphiti_core.cross_encoder import OpenAIRerankerClient
from graphiti_core.embedder import OpenAIEmbedder, OpenAIEmbedderConfig
from graphiti_core.llm_client import LLMConfig, OpenAIClient

from ai_internship.config import Settings

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
EMBEDDING_MODEL = "openai/text-embedding-3-small"
EMBEDDING_DIMENSIONS = 1536


def build_graphiti_client(settings: Settings) -> Graphiti:
    llm_client = OpenAIClient(
        config=LLMConfig(
            api_key=settings.openrouter_api_key,
            base_url=OPENROUTER_BASE_URL,
            model=settings.ingestion_llm_model,
        )
    )
    embedder = OpenAIEmbedder(
        config=OpenAIEmbedderConfig(
            api_key=settings.openrouter_api_key,
            base_url=OPENROUTER_BASE_URL,
            embedding_model=EMBEDDING_MODEL,
            embedding_dim=EMBEDDING_DIMENSIONS,
        )
    )
    cross_encoder = OpenAIRerankerClient(
        config=LLMConfig(
            api_key=settings.openrouter_api_key,
            base_url=OPENROUTER_BASE_URL,
            model=settings.ingestion_llm_model,
        )
    )
    return Graphiti(
        uri=settings.neo4j_uri,
        user=settings.neo4j_user,
        password=settings.neo4j_password,
        llm_client=llm_client,
        embedder=embedder,
        cross_encoder=cross_encoder,
    )
