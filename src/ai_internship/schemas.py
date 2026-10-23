"""Structured outputs for the customer-research experiments."""

from typing import List, Literal

from pydantic import BaseModel, ConfigDict, Field


class Evidence(BaseModel):
    """Source URL and snippet supporting a finding."""

    model_config = ConfigDict(extra="forbid")

    url: str | None = Field(description="Source URL for the evidence")
    snippet: str | None = Field(description="Exact copy-pasted snippet from the source")


class Customer(BaseModel):
    """A named customer with supporting evidence."""

    model_config = ConfigDict(extra="forbid")

    name: str
    website: str = Field(description="Website URL of the customer organization, or empty string")
    reasoning: str
    date: str | None = Field(description="Customer adoption year in YYYY format, or null")
    supporting_evidence: List[Evidence]
    confidence: Literal["high", "medium", "low"] = Field(
        description=(
            "Evidence quality: "
            "high = found on vendor's own website/case-study page OR mentioned by 2+ independent sources; "  # noqa: E501
            "medium = mentioned once with a specific source URL (press release, article); "
            "low = mentioned once with no URL or only inferred from generic language"
        ),
    )


class CustomerAnalysis(BaseModel):
    """Structured output for customer analysis."""

    model_config = ConfigDict(extra="forbid")

    customers: List[Customer] = Field(
        description="All identified customers with metadata and supporting evidence",
    )
