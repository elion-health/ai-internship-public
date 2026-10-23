from types import SimpleNamespace


def test_counts_reported_costs_without_inventing_missing_cost():
    from ai_internship.agents.customers.costs import CostTrackingBraintrustCallbackHandler

    tracker = CostTrackingBraintrustCallbackHandler(logger=None)
    response = SimpleNamespace(
        generations=[
            [
                SimpleNamespace(
                    message=SimpleNamespace(response_metadata={"token_usage": {"cost": 0.12}})
                )
            ]
        ]
    )
    tracker.on_llm_end(response)
    tracker.on_llm_end(SimpleNamespace(generations=[]))
    assert tracker.openrouter_total_cost == 0.12
    assert tracker.openrouter_generation_count == 1
