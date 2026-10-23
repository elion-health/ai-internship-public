"""Local cost accounting for the standalone internship experiments."""

from threading import Lock

from langchain_core.callbacks import BaseCallbackHandler


class CostTrackingBraintrustCallbackHandler(BaseCallbackHandler):
    """Compatibility name; this callback only counts provider-reported cost.

    Trace delivery is configured separately. Missing prices are not estimated.
    """

    def __init__(self, logger=None):
        self.openrouter_total_cost = 0.0
        self.openrouter_generation_count = 0
        self._lock = Lock()

    def on_llm_end(self, response, **kwargs):
        for generations in getattr(response, "generations", []):
            for generation in generations:
                metadata = (
                    getattr(getattr(generation, "message", None), "response_metadata", {}) or {}
                )
                cost = metadata.get("cost")
                if cost is None:
                    cost = (metadata.get("token_usage") or {}).get("cost")
                if isinstance(cost, (int, float)):
                    with self._lock:
                        self.openrouter_total_cost += cost
                        self.openrouter_generation_count += 1
