"""The narrow, configurable P0 profile registry."""

from __future__ import annotations

import asyncio
import functools
import hashlib
import json

from pydantic import BaseModel

from ai_internship.discovery.contracts import RetainedSource
from ai_internship.enrichment.grounding import SEMANTIC_CHECK_PROMPT_SLUGS
from ai_internship.enrichment.referent_resolution import (
    FIRST_PARTY_REFERENT_PROMPT_SLUGS,
    REFERENT_PROMPT_SLUGS,
)
from ai_internship.public_assets import load_prompt, project_name

from .contracts import Assertion, SeededEntity

BRAINTRUST_PROJECT = project_name()

# Profiles whose extraction prompt is loaded from Braintrust instead of the
# hardcoded PROFILE_INSTRUCTIONS/EVIDENCE_INSTRUCTIONS below.
PROMPT_SLUGS = {
    "customers": "customer-extraction-8f90",
    "integrations": "integration-extraction-4de1",
}


async def _load_prompt(slug: str):
    return await asyncio.get_running_loop().run_in_executor(
        None,
        functools.partial(load_prompt, slug=slug),
    )


class Profile(BaseModel):
    version: str
    labels: set[str]
    fields: set[str]
    relationships: set[str]


PROFILES = {
    "customers": Profile(
        version="v1",
        labels={"HealthSystem", "Product"},
        fields={"name", "website", "summary"},
        relationships={"Adopts"},
    ),
    "integrations": Profile(
        version="v1",
        labels={"Product"},
        fields={"name", "summary"},
        relationships={"Integrates"},
    ),
}


class CustomerRelationship(BaseModel):
    health_system: str
    product: str
    quote: str


class CustomersResult(BaseModel):
    relationships: list[CustomerRelationship]


class IntegrationRelationship(BaseModel):
    product: str
    integrated_product: str
    quote: str


class IntegrationsResult(BaseModel):
    relationships: list[IntegrationRelationship]


PROFILE_SCHEMAS = {
    "customers": CustomersResult,
    "integrations": IntegrationsResult,
}


PROFILE_INSTRUCTIONS = {
    "customers": (
        "Extract only explicit HealthSystem -> Adopts -> Product relationships supported by "
        "use, implementation, deployment, integration, launch, or a direct partnership. Do not "
        "treat an investment, award, or network membership as customer adoption."
    ),
    "integrations": (
        "Extract only explicit Product -> Integrates -> Product relationships supported by named "
        "systems being integrated, connected, embedded, interoperable, or combined. Generic system "
        "types and compatibility claims are not product endpoints."
    ),
}

EVIDENCE_INSTRUCTIONS = (
    "Every relationship must include one contiguous evidence span copied from the source's visible "
    "text. Include enough adjacent heading or sentences for both named endpoints and the explicit "
    "relationship cue to appear in that span."
)


async def extractor_version(profile: str, *, model: str) -> str:
    definition = PROFILES[profile]
    fields: dict[str, object] = {
        "profile": profile,
        "profile_version": definition.version,
        "schema": PROFILE_SCHEMAS[profile].model_json_schema(),
        "grounding_policy": "relationship_is_grounded:v11",
        "model": model,
    }
    if profile in PROMPT_SLUGS:
        # A cheap, cached metadata fetch -- no .build(), no real content -- so
        # editing the prompt in Braintrust changes this hash and invalidates
        # stale extractions on the next resume run.
        prompt = await _load_prompt(PROMPT_SLUGS[profile])
        fields["prompt_slug"] = PROMPT_SLUGS[profile]
        fields["prompt_version"] = prompt.version
    else:
        fields["instructions"] = PROFILE_INSTRUCTIONS[profile]
        fields["evidence_instructions"] = EVIDENCE_INSTRUCTIONS
    # The semantic grounding check (grounding.py's semantic_rejection_reason) is a
    # separate Braintrust prompt, keyed by relation rather than profile. Include its
    # version too -- otherwise editing only the semantic-check prompt wouldn't change
    # this hash, and before/after extraction runs would collide under the same
    # extractor_version.
    (relation,) = definition.relationships
    semantic_check_slug = SEMANTIC_CHECK_PROMPT_SLUGS.get(relation)
    if semantic_check_slug is not None:
        semantic_check_prompt = await _load_prompt(semantic_check_slug)
        fields["semantic_check_prompt_slug"] = semantic_check_slug
        fields["semantic_check_prompt_version"] = semantic_check_prompt.version
    # The referent is a third Braintrust prompt, also keyed by relation, and it
    # decides whether a relation whose quote names one side survives at all.
    # Without it here, adding or editing a referent prompt leaves this hash
    # unchanged, so plan_extraction skips every page that already has a
    # terminal run at the same version and the change reaches nothing.
    #
    # Both referent prompts for this relation go in, not whichever one this
    # page would route to: the version is computed per (profile, relation),
    # before any page is in hand, and a page's origin is not part of it.
    # Hashing only the routed one would leave the hash unchanged for every
    # page that does not take the new route, which is most of them.
    for field, slugs in (
        ("referent", REFERENT_PROMPT_SLUGS),
        ("first_party_referent", FIRST_PARTY_REFERENT_PROMPT_SLUGS),
    ):
        referent_slug = slugs.get(relation)
        if referent_slug is not None:
            referent_prompt = await _load_prompt(referent_slug)
            fields[f"{field}_prompt_slug"] = referent_slug
            fields[f"{field}_prompt_version"] = referent_prompt.version
    payload = json.dumps(fields, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


class LLMAssertionExtractor:
    """P0-schema extractor that requires source quotes for every proposed fact."""

    def __init__(self, model):
        self._model = model

    async def extract(
        self,
        source: RetainedSource,
        profiles: list[str],
        seeded_entities: list[SeededEntity] | None = None,
        target_name: str = "",
    ) -> list[Assertion]:
        candidates = "\n".join(
            f"- {entity.label}: {entity.name}" for entity in (seeded_entities or [])
        )
        assertions: list[Assertion] = []
        for profile in profiles:
            if profile not in PROFILE_SCHEMAS:
                continue
            if profile in PROMPT_SLUGS:
                prompt = await _load_prompt(PROMPT_SLUGS[profile])
                # target_product_name/target_product_aliases feed the
                # ELI-1192 fix -- the model no longer guesses which product
                # is involved, it's told directly. Aliases aren't wired up
                # to a real data source yet (no per-vendor alias list
                # exists), so it's passed empty for now -- follow-up work,
                # not blocking this change.
                built = prompt.build(
                    seeded_candidates=candidates or "(none)",
                    source_content=source.content,
                    target_product_name=target_name,
                    target_product_aliases="",
                )
                messages = built["messages"]
            else:
                system_prompt = (
                    f"{PROFILE_INSTRUCTIONS[profile]} "
                    f"{EVIDENCE_INSTRUCTIONS} "
                    "Every endpoint must be a proper-named organization or branded product, not a "
                    "generic cohort, capability, navigation label, or inferred entity. Every "
                    "relationship must be explicit. Abstain when either endpoint is unnamed. Reuse "
                    "the exact spelling and label of a seeded candidate when the source refers to "
                    "that entity; do not emit candidates merely "
                    f"because they are listed.\nSeeded candidates:\n{candidates or '(none)'}"
                )
                messages = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": source.content},
                ]
            structured_model = self._model.with_structured_output(
                PROFILE_SCHEMAS[profile], method="function_calling"
            )
            result = await structured_model.ainvoke(messages)
            assertions.extend(_to_assertions(profile, result))
        return assertions


def _to_assertions(profile: str, result) -> list[Assertion]:
    parsed = PROFILE_SCHEMAS[profile].model_validate(result)
    if profile == "customers":
        return [
            Assertion(
                subject=item.health_system,
                subject_type="HealthSystem",
                relation="Adopts",
                object=item.product,
                object_type="Product",
                quote=item.quote,
                profile=profile,
            )
            for item in parsed.relationships
        ]
    return [
        Assertion(
            subject=item.product,
            subject_type="Product",
            relation="Integrates",
            object=item.integrated_product,
            object_type="Product",
            quote=item.quote,
            profile=profile,
        )
        for item in parsed.relationships
    ]
