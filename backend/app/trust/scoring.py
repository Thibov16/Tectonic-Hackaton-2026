"""Composite trust scoring and sensitivity-aware reranking."""

from __future__ import annotations

from typing import Any

from app.config import AppConfig
from app.trust.gates import apply_gates
from app.trust.integrity import check_integrity
from app.trust.signals import compute_signals


def sensitivity_rerank(
    trust: float,
    doc: dict[str, Any],
    query_ctx: dict[str, Any],
    integrity_findings: list[dict[str, Any]],
) -> tuple[float, list[str]]:
    """Boost official security guidance / demote adversarial hits on sensitive topics."""
    reasons: list[str] = []
    topic = query_ctx.get("topic") or ""
    doc_id = (doc.get("doc_id") or "").upper()
    path = (doc.get("path") or "").casefold()
    doc_type = (doc.get("doc_type") or "").casefold()

    if topic in {"bank_details", "data_handling"}:
        if doc_type == "policy" and ("SEC" in doc_id or "data_handling" in path or "sec-002" in path):
            trust = min(1.0, trust + 0.12)
            reasons.append("sensitivity_boost_security_policy")
        if any(f.get("severity") == "critical" for f in integrity_findings):
            trust = min(trust, 0.05)
            reasons.append("sensitivity_cap_critical_email")
        if path.startswith("adversarial/") or "/adversarial/" in path:
            trust = min(trust, 0.08)
            reasons.append("sensitivity_cap_adversarial")

    # Keep adversarial docs out of the primary answer set unless the query is about them
    if path.startswith("adversarial/") or "/adversarial/" in path:
        if topic not in {"bank_details", "data_handling"}:
            trust = min(trust, 0.35)
            reasons.append("sensitivity_demote_adversarial")

    if topic == "company_car" and doc_type in {"kb", "release_notes", "email", "meeting"}:
        trust = min(1.0, trust + 0.05)
        reasons.append("sensitivity_boost_expert_channel")

    return trust, reasons


def score_document(
    doc: dict[str, Any],
    query_ctx: dict[str, Any],
    config: AppConfig,
    *,
    peer_docs: list[dict[str, Any]] | None = None,
    links: list[dict[str, Any]] | None = None,
    integrity_findings: list[dict[str, Any]] | None = None,
    evidence: str = "",
    retrieval_score: float = 0.0,
) -> dict[str, Any]:
    """
    Compute gated trust score.

    trust = gate * (0.30A + 0.25C + 0.25App + 0.10Cor + 0.10I)
    """
    findings = integrity_findings if integrity_findings is not None else check_integrity(doc, config)
    signals = compute_signals(
        doc,
        query_ctx,
        config,
        integrity_findings=findings,
        peer_docs=peer_docs,
    )
    gate, role, gate_reasons = apply_gates(
        doc, signals, findings, config, links=links
    )

    tc = config.trust
    composite = (
        tc.w_authority * signals["authority"]
        + tc.w_currency * signals["currency"]
        + tc.w_applicability * signals["applicability"]
        + tc.w_corroboration * signals["corroboration"]
        + tc.w_integrity * signals["integrity"]
    )
    trust = gate * composite
    trust, sens_reasons = sensitivity_rerank(trust, doc, query_ctx, findings)

    flat_reasons: list[str] = []
    for key in ("authority", "currency", "applicability", "corroboration", "integrity"):
        flat_reasons.extend(signals["reasons"].get(key, []))
    flat_reasons.extend(gate_reasons)
    flat_reasons.extend(sens_reasons)

    return {
        "id": doc.get("id") or doc.get("document_id"),
        "title": doc.get("title") or "",
        "doc_type": doc.get("doc_type") or "",
        "path": doc.get("path") or "",
        "status": doc.get("status"),
        "country": doc.get("country"),
        "customer": doc.get("customer"),
        "date": doc.get("effective_date") or doc.get("created_date"),
        "role": role,
        "trust": round(float(trust), 4),
        "gate": gate,
        "composite": round(float(composite), 4),
        "signals": {
            "authority": round(signals["authority"], 4),
            "currency": round(signals["currency"], 4),
            "applicability": round(signals["applicability"], 4),
            "corroboration": round(signals["corroboration"], 4),
            "integrity": round(signals["integrity"], 4),
        },
        "reasons": flat_reasons,
        "evidence": evidence or (doc.get("raw_text") or "")[:400],
        "integrity_findings": findings,
        "retrieval_score": retrieval_score,
        "retracted": bool(doc.get("retracted")),
    }
