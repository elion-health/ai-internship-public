from ai_internship.config import Settings


def test_local_graph_and_discovery_defaults(monkeypatch):
    monkeypatch.delenv("NEO4J_URI", raising=False)
    monkeypatch.delenv("NEO4J_USER", raising=False)
    monkeypatch.delenv("NEO4J_PASSWORD", raising=False)
    monkeypatch.delenv("DISCOVERY_MAX_SEARCH_CALLS", raising=False)
    monkeypatch.delenv("DISCOVERY_MAX_SCRAPE_CALLS", raising=False)
    monkeypatch.delenv("DISCOVERY_MAX_CRAWL_PAGES", raising=False)
    monkeypatch.delenv("DISCOVERY_MAX_CRAWL_CALLS", raising=False)
    monkeypatch.delenv("DISCOVERY_MAX_MAP_CALLS", raising=False)
    monkeypatch.delenv("RESEARCH_FIRST_PARTY_PAGE_LIMIT", raising=False)
    monkeypatch.delenv("RESEARCH_EXTERNAL_FETCH_LIMIT", raising=False)
    monkeypatch.delenv("RESEARCH_PROVIDER_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("RESEARCH_CRAWL_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("RESEARCH_MIN_CONTENT_CHARACTERS", raising=False)
    monkeypatch.delenv("RESEARCH_RENDER_RETRY_WAIT_MS", raising=False)
    monkeypatch.delenv("RESEARCH_INTERACTIVE_EVIDENCE_LIMIT", raising=False)
    monkeypatch.delenv("RESEARCH_FETCH_CONCURRENCY", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.delenv("DISCOVERY_LLM_MODEL", raising=False)
    monkeypatch.delenv("INGESTION_LLM_MODEL", raising=False)

    settings = Settings()

    assert settings.neo4j_uri == "bolt://localhost:7688"
    assert settings.neo4j_user == "neo4j"
    assert settings.neo4j_password == "local-research-password"
    # ELI-1462: one raised ceiling for every vendor, was 10/10.
    assert settings.discovery_max_search_calls == 25
    assert settings.discovery_max_scrape_calls == 15
    assert settings.discovery_max_crawl_pages == 50
    assert settings.discovery_max_crawl_calls == 5
    # map_site had no cap at all before this (found in the 2026-09-16 budget
    # review) -- one site, or repeated calls, could rack up unlimited calls.
    assert settings.discovery_max_map_calls == 5
    # Off by default (ELI-1490): lands as a flag so the decision stays open.
    assert settings.discovery_evidence_judge is False
    assert settings.research_first_party_page_limit == 1000
    assert settings.research_external_fetch_limit == 10
    assert settings.research_provider_timeout_seconds == 60.0
    assert settings.research_crawl_timeout_seconds == 600.0
    assert settings.research_min_content_characters == 200
    assert settings.research_render_retry_wait_ms == 5000
    assert settings.research_interactive_evidence_limit == 5
    assert settings.research_fetch_concurrency == 8
    assert settings.langsmith_project == "ai-internship"
    assert settings.llm_model == "openai/gpt-5.4-mini"
    assert settings.discovery_llm_model == "openai/gpt-5.4-mini"
    assert settings.ingestion_llm_model == "openai/gpt-5.4-mini"


def test_workflow_models_can_override_shared_model(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "shared/model")
    monkeypatch.setenv("DISCOVERY_LLM_MODEL", "discovery/model")
    monkeypatch.setenv("INGESTION_LLM_MODEL", "ingestion/model")

    settings = Settings()

    assert settings.llm_model == "shared/model"
    assert settings.discovery_llm_model == "discovery/model"
    assert settings.ingestion_llm_model == "ingestion/model"


def test_blank_workflow_model_overrides_use_shared_model(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "shared/model")
    monkeypatch.setenv("DISCOVERY_LLM_MODEL", "")
    monkeypatch.setenv("INGESTION_LLM_MODEL", "")

    settings = Settings()

    assert settings.discovery_llm_model == "shared/model"
    assert settings.ingestion_llm_model == "shared/model"


def test_research_acquisition_settings_can_be_overridden(monkeypatch):
    monkeypatch.setenv("RESEARCH_FIRST_PARTY_PAGE_LIMIT", "25")
    monkeypatch.setenv("RESEARCH_EXTERNAL_FETCH_LIMIT", "6")
    monkeypatch.setenv("RESEARCH_PROVIDER_TIMEOUT_SECONDS", "12.5")
    monkeypatch.setenv("RESEARCH_CRAWL_TIMEOUT_SECONDS", "300")
    monkeypatch.setenv("RESEARCH_MIN_CONTENT_CHARACTERS", "400")
    monkeypatch.setenv("RESEARCH_RENDER_RETRY_WAIT_MS", "1500")
    monkeypatch.setenv("RESEARCH_INTERACTIVE_EVIDENCE_LIMIT", "3")

    settings = Settings()

    assert settings.research_first_party_page_limit == 25
    assert settings.research_external_fetch_limit == 6
    assert settings.research_provider_timeout_seconds == 12.5
    assert settings.research_crawl_timeout_seconds == 300.0
    assert settings.research_min_content_characters == 400
    assert settings.research_render_retry_wait_ms == 1500
    assert settings.research_interactive_evidence_limit == 3
