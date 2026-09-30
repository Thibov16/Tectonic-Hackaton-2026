"""Central configuration for the trust-aware knowledge search system."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path


def _parse_today() -> date:
    raw = os.getenv("TODAY", "2026-09-30")
    return date.fromisoformat(raw)


@dataclass
class TrustConfig:
    """All trust weights and thresholds in one place."""

    w_authority: float = 0.30
    w_currency: float = 0.25
    w_applicability: float = 0.25
    w_corroboration: float = 0.10
    w_integrity: float = 0.10

    gate_superseded: float = 0.15
    gate_draft: float = 0.35
    gate_retracted: float = 0.05
    gate_critical_integrity: float = 0.0
    gate_low_applicability: float = 0.3
    gate_forgery: float = 0.1

    applicability_mismatch_threshold: float = 0.2
    conflict_authority_delta: float = 0.15
    trusted_threshold: float = 0.7
    caution_threshold: float = 0.45
    gap_authority_ceiling: float = 0.4

    authority_table: dict[str, float] = field(
        default_factory=lambda: {
            "policy_approved": 1.0,
            "contract_signed": 1.0,
            "procedure": 0.8,
            "kb": 0.8,
            "analysis_final": 0.7,
            "release_notes": 0.7,
            "email_policy_owner": 0.7,
            "meeting_decision": 0.65,
            "ticket": 0.6,
            "email_expert": 0.6,
            "chat": 0.3,
            "rumour": 0.1,
            "ai_summary": 0.2,
            "wiki": 0.2,
            "personal_drive": 0.15,
            "unknown": 0.15,
        }
    )


@dataclass
class AppConfig:
    today: date = field(default_factory=_parse_today)
    project_root: Path = field(
        default_factory=lambda: Path(__file__).resolve().parents[2]
    )
    data_dir: Path | None = None
    db_path: Path | None = None
    claude_model: str = field(
        default_factory=lambda: os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514")
    )
    anthropic_api_key: str | None = field(
        default_factory=lambda: os.getenv("ANTHROPIC_API_KEY") or None
    )
    trust: TrustConfig = field(default_factory=TrustConfig)
    chunk_size: int = 800
    chunk_overlap: int = 100
    retrieval_top_k: int = 25
    known_customers: tuple[tuple[str, str, tuple[str, ...]], ...] = (
        ("Janssens Logistics NV", "BE", ("janssens logistics", "janssens")),
        ("Van Dijk Retail BV", "NL", ("van dijk retail", "van dijk", "vandijk")),
        ("Mueller Bau GmbH", "DE", ("mueller bau", "mueller")),
    )
    allowed_email_domains: tuple[str, ...] = (
        "sdworx-example.com",
        "janssens-logistics-example.be",
        "vandijk-retail-example.nl",
        "mueller-bau-example.de",
    )

    def __post_init__(self) -> None:
        if self.data_dir is None or str(self.data_dir) in {"", "."}:
            candidate = self.project_root / "data" / "sdworx_testset"
            legacy = self.project_root / "testset"
            self.data_dir = candidate if candidate.exists() else legacy
        if self.db_path is None or str(self.db_path) in {"", "."}:
            self.db_path = self.project_root / "backend" / "trust.db"
        self.data_dir = Path(self.data_dir)
        self.db_path = Path(self.db_path)


def get_config() -> AppConfig:
    return AppConfig()
