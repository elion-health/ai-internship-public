from typing import Any, Protocol


class OpenRouterCostTracker(Protocol):
    openrouter_total_cost: float
    openrouter_generation_count: int


def log_customer_root_span(
    span,
    *,
    input: dict[str, Any],
    metadata: dict[str, Any],
    output: Any,
    cost_tracker: OpenRouterCostTracker | None = None,
) -> None:
    openrouter_total_cost = cost_tracker.openrouter_total_cost if cost_tracker is not None else 0.0
    openrouter_generation_count = (
        cost_tracker.openrouter_generation_count if cost_tracker is not None else 0
    )

    span.log(
        input=input,
        metadata={
            **metadata,
            "openrouter_total_cost": openrouter_total_cost,
            "openrouter_generation_count": openrouter_generation_count,
        },
        metrics={
            "openrouter_total_cost": openrouter_total_cost,
            "estimated_cost": openrouter_total_cost,
        },
        output=output,
    )
