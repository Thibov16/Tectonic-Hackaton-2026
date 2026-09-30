"""Pydantic schemas for API and internal models."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    details: str = Field(min_length=1, max_length=4000)
    today: str | None = None


class QueryContext(BaseModel):
    customer: str | None = None
    country: str | None = None
    topic: str = "other"
    question_type: str = "lookup"
    claim_to_verify: str | None = None


class SignalBreakdown(BaseModel):
    authority: float
    currency: float
    applicability: float
    corroboration: float
    integrity: float


class SourceOut(BaseModel):
    id: int
    title: str
    doc_type: str
    role: Literal["primary", "supporting", "historical", "context_only", "blocked"]
    trust: float
    signals: SignalBreakdown
    reasons: list[str] = Field(default_factory=list)
    evidence: str = ""
    date: str | None = None
    path: str = ""
    status: str | None = None
    country: str | None = None
    customer: str | None = None


class FlagOut(BaseModel):
    severity: Literal["warning", "danger", "info"]
    source_id: int | None = None
    code: str
    message: str


class ExpertOut(BaseModel):
    name: str
    role: str = ""
    why: str = ""
    available: bool = True


class SearchResponse(BaseModel):
    query_context: QueryContext
    verdict: Literal["trusted", "caution", "conflict", "gap"]
    headline: str
    answer: str
    confidence: float
    sources: list[SourceOut] = Field(default_factory=list)
    flags: list[FlagOut] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    experts: list[ExpertOut] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)


class DocumentDetail(BaseModel):
    id: int
    path: str
    title: str
    doc_type: str
    doc_id: str | None = None
    version: str | None = None
    status: str | None = None
    country: str | None = None
    customer: str | None = None
    effective_date: str | None = None
    raw_text: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    claims: list[dict[str, Any]] = Field(default_factory=list)
    links: list[dict[str, Any]] = Field(default_factory=list)
    retracted: bool = False
