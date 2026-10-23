_EXPORT_MODULES = {
    "ReactExaCustomerAgent": ("ai_internship.agents.customers.experiments.customers_react_exa"),
    "ReactExaMemoryAgent": (
        "ai_internship.agents.customers.experiments.customers_react_exa_memory"
    ),
    "ReactExaMemorySynthAgent": (
        "ai_internship.agents.customers.experiments.customers_react_exa_memory_synth"
    ),
    "ReactExaMultiAgentCustomerAgent": (
        "ai_internship.agents.customers.experiments.customers_react_exa_multiagent"
    ),
    "ReactFirecrawlCustomerAgent": (
        "ai_internship.agents.customers.experiments.customers_react_firecrawl"
    ),
    "ExaOpenRouterCustomerAgent": (
        "ai_internship.agents.customers.experiments.customers_react_exa_openrouter"
    ),
    "ExaServerToolCustomerAgent": (
        "ai_internship.agents.customers.experiments.customers_exa_servertool"
    ),
}


def __getattr__(name: str):
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    module = __import__(module_name, fromlist=[name])
    return getattr(module, name)


__all__ = sorted(_EXPORT_MODULES)
