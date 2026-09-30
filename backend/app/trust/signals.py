"""Trust signal scorers: authority, currency, applicability, corroboration, integrity."""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from app.config import AppConfig


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def resolve_authority_key(doc: dict[str, Any]) -> str:
    """Map document fields onto TrustConfig.authority_table keys."""
    doc_type = (doc.get("doc_type") or "").casefold()
    status = (doc.get("status") or "").upper()
    source = (doc.get("source_location") or "").casefold()
    path = (doc.get("path") or "").casefold()
    title = (doc.get("title") or "").casefold()
    text_head = (doc.get("raw_text") or "")[:300].casefold()

    if source == "personal_drive":
        return "personal_drive"
    if doc_type == "ai_summary" or source == "ai_generated":
        return "ai_summary"
    if doc_type == "wiki" or "wiki" in path:
        return "wiki"
    if "rumour" in path or "rumor" in title or "rumour" in text_head:
        return "rumour"
    if doc_type == "contract" or status == "SIGNED":
        return "contract_signed"
    if doc_type == "policy" and (doc.get("approved_by") or status in {"CURRENT", "APPROVED"}):
        return "policy_approved"
    if doc_type == "policy":
        return "policy_approved" if doc.get("approved_by") else "unknown"
    if doc_type == "procedure":
        return "procedure"
    if doc_type == "kb":
        return "kb"
    if doc_type in {"analysis", "analysis_final"} or "post_incident" in path:
        return "analysis_final"
    if doc_type == "release_notes":
        return "release_notes"
    if doc_type == "meeting":
        return "meeting_decision"
    if doc_type == "ticket":
        return "ticket"
    if doc_type == "email":
        author = (doc.get("author") or "").casefold()
        if any(n in author for n in ("sara claes", "director", "policy")):
            return "email_policy_owner"
        return "email_expert"
    if doc_type == "chat":
        return "chat"
    return "unknown"


def score_authority(doc: dict[str, Any], config: AppConfig) -> tuple[float, list[str]]:
    table = config.trust.authority_table
    key = resolve_authority_key(doc)
    score = float(table.get(key, table.get("unknown", 0.15)))
    reasons = [f"authority:{key}={score:.2f}"]
    if doc.get("approved_by"):
        reasons.append(f"approved_by={doc['approved_by']}")
    return score, reasons


def score_currency(doc: dict[str, Any], config: AppConfig) -> tuple[float, list[str]]:
    reasons: list[str] = []
    status = (doc.get("status") or "").upper()
    today = config.today
    score = 0.7

    if status == "SUPERSEDED":
        score = 0.15
        reasons.append("status=SUPERSEDED")
    elif status == "DRAFT":
        score = 0.35
        reasons.append("status=DRAFT")
    elif status in {"CURRENT", "SIGNED", "FINAL"}:
        score = 0.9
        reasons.append(f"status={status}")

    eff = _parse_date(doc.get("effective_date") or doc.get("created_date"))
    if eff:
        age_days = (today - eff).days
        if age_days < 0:
            score = min(score, 0.4)
            reasons.append("effective_date_in_future")
        elif age_days <= 180:
            score = max(score, 0.85)
            reasons.append(f"recent_effective={eff.isoformat()}")
        elif age_days <= 730:
            score = min(score, 0.7)
            reasons.append(f"effective={eff.isoformat()}")
        else:
            score = min(score, 0.35)
            reasons.append(f"stale_effective={eff.isoformat()}")
    else:
        score = min(score, 0.4)
        reasons.append("no_effective_date")

    next_review = _parse_date(doc.get("next_review"))
    if next_review and next_review < today:
        score = min(score, 0.25)
        reasons.append(f"review_overdue={next_review.isoformat()}")

    if doc.get("retracted"):
        score = min(score, 0.1)
        reasons.append("retracted")

    return max(0.0, min(1.0, score)), reasons


def score_applicability(
    doc: dict[str, Any],
    query_ctx: dict[str, Any],
    config: AppConfig,
) -> tuple[float, list[str]]:
    reasons: list[str] = []
    score = 0.55
    q_country = (query_ctx.get("country") or "").upper() or None
    q_customer = query_ctx.get("customer")
    d_country = (doc.get("country") or "").upper() or None
    if d_country in {"UNKNOWN", "NONE", ""}:
        d_country = None
    d_customer = doc.get("customer")

    if q_country and d_country:
        if q_country == d_country:
            score = 0.9
            reasons.append(f"country_match={d_country}")
        else:
            score = 0.1
            reasons.append(f"country_mismatch={d_country}!={q_country}")
    elif q_country and not d_country:
        score = 0.45
        reasons.append("doc_missing_country")
    elif not q_country:
        score = 0.7
        reasons.append("query_no_country")

    if q_customer and d_customer:
        if q_customer.casefold() == str(d_customer).casefold():
            score = max(score, 0.95)
            reasons.append(f"customer_match={d_customer}")
        else:
            # Customer-specific doc for someone else
            score = min(score, 0.15)
            reasons.append(f"customer_mismatch={d_customer}")
    elif q_customer and not d_customer:
        # Generic policy may still apply unless contract needed
        if (doc.get("doc_type") or "") == "contract":
            score = min(score, 0.2)
            reasons.append("contract_wrong_customer_scope")
        else:
            reasons.append("generic_doc_for_customer_query")

    if not q_customer and d_customer and (doc.get("doc_type") or "") == "contract":
        # Contract for a specific customer less applicable to generic query
        score = min(score, 0.5)
        reasons.append("customer_specific_contract")

    # Topic keyword soft match
    topic = query_ctx.get("topic") or "other"
    topic_hints = {
        "remote_allowance": ("allowance", "telework", "remote", "home office"),
        "payroll_deadline": ("deadline", "input", "payroll", "variable pay"),
        "meal_vouchers": ("meal voucher", "voucher"),
        "company_car": ("company car", "co2", "car benefit"),
        "sick_leave": ("sick", "eau", "certificate", "leave"),
        "data_handling": ("payroll export", "data handling", "confidential"),
        "bank_details": ("iban", "bank", "approval"),
        "year_end_bonus": ("bonus",),
    }
    if topic in topic_hints:
        blob = f"{doc.get('title') or ''} {(doc.get('raw_text') or '')[:600]}".casefold()
        matched = False
        for h in topic_hints[topic]:
            if " " in h:
                if h in blob:
                    matched = True
                    break
            else:
                if re.search(rf"\b{re.escape(h)}\b", blob):
                    matched = True
                    break
        if not matched:
            score = max(0.0, score - 0.5)
            reasons.append("topic_mismatch")
        else:
            reasons.append("topic_keywords_present")

    return max(0.0, min(1.0, score)), reasons


def score_corroboration(
    doc: dict[str, Any],
    peer_docs: list[dict[str, Any]],
    config: AppConfig,
) -> tuple[float, list[str]]:
    """Independent high-authority peers that share topic/country raise the score."""
    reasons: list[str] = []
    if not peer_docs:
        return 0.3, ["no_peers"]

    doc_id = doc.get("id") or doc.get("document_id")
    independent = 0
    chat_only = 0
    for peer in peer_docs:
        pid = peer.get("id") or peer.get("document_id")
        if pid == doc_id:
            continue
        ptype = (peer.get("doc_type") or "").casefold()
        auth_key = resolve_authority_key(peer)
        auth = config.trust.authority_table.get(auth_key, 0.15)
        if ptype in {"chat"} or auth < 0.35:
            chat_only += 1
            continue
        # Country compatibility
        dc = (doc.get("country") or "").upper()
        pc = (peer.get("country") or "").upper()
        if dc and pc and dc != pc and dc not in {"UNKNOWN"} and pc not in {"UNKNOWN"}:
            continue
        if auth >= 0.6:
            independent += 1

    if independent >= 2:
        score = 0.9
        reasons.append(f"independent_sources={independent}")
    elif independent == 1:
        score = 0.7
        reasons.append("one_independent_source")
    elif chat_only >= 2:
        score = 0.25
        reasons.append("chat_echo_only")
    else:
        score = 0.4
        reasons.append("weak_corroboration")
    return score, reasons


def score_integrity_signal(
    findings: list[dict[str, Any]],
    config: AppConfig,
) -> tuple[float, list[str]]:
    reasons: list[str] = []
    if not findings:
        return 1.0, ["integrity_clean"]

    score = 1.0
    for f in findings:
        sev = f.get("severity")
        code = f.get("code")
        reasons.append(f"{sev}:{code}")
        if sev == "critical":
            score = 0.0
        elif sev == "warning":
            score = min(score, 0.45)
        elif sev == "info":
            score = min(score, 0.85)
    return max(0.0, score), reasons


def compute_signals(
    doc: dict[str, Any],
    query_ctx: dict[str, Any],
    config: AppConfig,
    *,
    integrity_findings: list[dict[str, Any]] | None = None,
    peer_docs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return scores + per-signal reasons for all five trust dimensions."""
    a, ar = score_authority(doc, config)
    c, cr = score_currency(doc, config)
    ap, apr = score_applicability(doc, query_ctx, config)
    co, cor = score_corroboration(doc, peer_docs or [], config)
    i, ir = score_integrity_signal(integrity_findings or [], config)
    return {
        "authority": a,
        "currency": c,
        "applicability": ap,
        "corroboration": co,
        "integrity": i,
        "reasons": {
            "authority": ar,
            "currency": cr,
            "applicability": apr,
            "corroboration": cor,
            "integrity": ir,
        },
    }
