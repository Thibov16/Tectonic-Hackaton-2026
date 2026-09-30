"""Trust gates that multiply the composite score and assign a source role."""

from __future__ import annotations

from typing import Any

from app.config import AppConfig


def apply_gates(
    doc: dict[str, Any],
    signals: dict[str, Any],
    integrity_findings: list[dict[str, Any]],
    config: AppConfig,
    *,
    links: list[dict[str, Any]] | None = None,
) -> tuple[float, str, list[str]]:
    """
    Apply hard gates.

    Returns (multiplier, role, reasons).
    Roles: primary | supporting | historical | context_only | blocked
    """
    tc = config.trust
    multiplier = 1.0
    reasons: list[str] = []
    role = "primary"
    status = (doc.get("status") or "").upper()
    doc_id = doc.get("id") or doc.get("document_id")
    links = links or []

    # Critical integrity → blocked
    critical = [f for f in integrity_findings if f.get("severity") == "critical"]
    if critical:
        multiplier = tc.gate_critical_integrity
        role = "blocked"
        reasons.append(f"gate_critical_integrity:{critical[0].get('code')}")
        return multiplier, role, reasons

    # Retracted
    if doc.get("retracted"):
        multiplier *= tc.gate_retracted
        role = "historical"
        reasons.append("gate_retracted")

    # Superseded → historical
    if status == "SUPERSEDED":
        multiplier *= tc.gate_superseded
        role = "historical"
        reasons.append("gate_superseded")

    # Draft → context only
    if status == "DRAFT":
        multiplier *= tc.gate_draft
        role = "context_only"
        reasons.append("gate_draft")

    # Forgery / near-duplicate unsigned personal drive
    is_forgery = False
    source = (doc.get("source_location") or "").casefold()
    if source == "personal_drive" and not doc.get("approved_by"):
        for link in links:
            rel = link.get("relation")
            if rel == "duplicates" and (
                link.get("from_doc") == doc_id or link.get("to_doc") == doc_id
            ):
                is_forgery = True
                break
        path = (doc.get("path") or "").casefold()
        if "unsigned" in path or "near_duplicate" in path or "a03" in path:
            is_forgery = True
    if is_forgery:
        multiplier *= tc.gate_forgery
        reasons.append("gate_forgery")
        if role == "primary":
            role = "context_only"

    # Low applicability
    applicability = float(signals.get("applicability") or 0.0)
    if applicability < tc.applicability_mismatch_threshold:
        multiplier *= tc.gate_low_applicability
        reasons.append("gate_low_applicability")
        if role == "primary":
            role = "supporting"

    # Soft demotions for weak integrity warnings
    if any(f.get("code") == "fabricated_citation" for f in integrity_findings):
        multiplier *= 0.5
        reasons.append("gate_fabricated_citation")
        if role == "primary":
            role = "context_only"

    if multiplier <= 0.0:
        role = "blocked"
    elif role == "primary" and multiplier < 0.5:
        role = "supporting"

    return max(0.0, multiplier), role, reasons
