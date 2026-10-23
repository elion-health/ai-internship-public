from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from scripts import cache_first_party_pages as cache


@pytest.mark.asyncio
async def test_target_exception_is_counted_and_recorded():
    store = SimpleNamespace(
        load_or_create_target=AsyncMock(return_value=SimpleNamespace(target_id="example")),
        start_run=AsyncMock(),
        finish_run=AsyncMock(),
    )
    acquisition = SimpleNamespace(map_first_party=AsyncMock(side_effect=RuntimeError("map failed")))
    result = await cache.cache_one(acquisition, store, "Example", "https://example.com")
    assert result["failed_targets"] == 1
    assert store.finish_run.await_args.kwargs["status"] == "failed"


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_targets,failed_pages,expected", [(1, 0, 1), (0, 1, 1), (0, 0, 0)])
async def test_batch_continues_but_exits_unsuccessfully_on_failures(
    monkeypatch, failed_targets, failed_pages, expected
):
    driver = SimpleNamespace(close=AsyncMock())
    monkeypatch.setattr(cache.AsyncGraphDatabase, "driver", lambda *a, **k: driver)
    monkeypatch.setattr(cache, "FirecrawlDiscoveryProvider", lambda **k: object())
    monkeypatch.setattr(cache, "DiscoveryStore", lambda *a: object())
    monkeypatch.setattr(cache, "FirstPartyAcquisition", lambda *a, **k: object())
    calls = []

    async def cache_one(acquisition, store, name, url):
        calls.append(name)
        return {
            "discovered": 0,
            "retained": 0,
            "provider_calls": 0,
            "failed": failed_pages if len(calls) == 1 else 0,
            "failed_targets": failed_targets if len(calls) == 1 else 0,
        }

    monkeypatch.setattr(cache, "cache_one", cache_one)
    args = SimpleNamespace(product=["Suki", "FirstHx"], page_limit=10, only_missing=False)
    assert await cache.main_async(args) == expected
    assert calls == ["Suki", "FirstHx"]
    driver.close.assert_awaited_once()
