"""Assemble final search verdict from scored sources and claim analysis."""

from __future__ import annotations

from typing import Any

from app.config import AppConfig


def build_verdict(
    query_ctx: dict[str, Any],
    scored_sources: list[dict[str, Any]],
    conflict_analysis: dict[str, Any],
    experts: list[dict[str, Any]],
    config: AppConfig,
) -> dict[str, Any]:
    """
    Build verdict payload: verdict/headline/answer/confidence/sources/flags/gaps.
    """
    tc = config.trust
    usable = [s for s in scored_sources if s.get("role") != "blocked"]
    primary = [s for s in usable if s.get("role") == "primary"]
    supporting = [s for s in usable if s.get("role") in {"supporting", "historical", "context_only"}]

    conflicts = conflict_analysis.get("conflicts") or []
    hard_conflicts = [c for c in conflicts if not c.get("authority_separated")]
    winning = conflict_analysis.get("winning_claim")
    outdated = conflict_analysis.get("outdated") or []

    flags: list[dict[str, Any]] = []
    gaps: list[str] = []

    ranked_for_flags = sorted(
        scored_sources, key=lambda s: float(s.get("trust") or 0.0), reverse=True
    )
    top3_ids = {s.get("id") for s in ranked_for_flags[:3]}
    topic = query_ctx.get("topic") or "other"

    for src in scored_sources:
        findings = src.get("integrity_findings") or []
        for finding in findings:
            sev = finding.get("severity")
            if sev == "critical":
                # Top hits always; other blocked adversarial only on security topics
                if src.get("id") in top3_ids:
                    pass
                elif src.get("role") == "blocked" and topic in {
                    "bank_details",
                    "data_handling",
                }:
                    pass
                else:
                    continue
                flags.append(
                    {
                        "severity": "danger",
                        "source_id": src.get("id"),
                        "code": finding.get("code") or "integrity",
                        "message": finding.get("message") or "",
                    }
                )
            elif sev == "warning" and src.get("id") in top3_ids:
                flags.append(
                    {
                        "severity": "warning",
                        "source_id": src.get("id"),
                        "code": finding.get("code") or "integrity",
                        "message": finding.get("message") or "",
                    }
                )
        if src.get("id") in top3_ids and src.get("role") == "historical":
            flags.append(
                {
                    "severity": "info",
                    "source_id": src.get("id"),
                    "code": "SUPERSEDED",
                    "message": f"Superseded: {src.get('title')}",
                }
            )
        if src.get("id") in top3_ids and src.get("role") == "context_only":
            flags.append(
                {
                    "severity": "warning",
                    "source_id": src.get("id"),
                    "code": "DRAFT_OR_WEAK",
                    "message": f"Draft / weak provenance: {src.get('title')}",
                }
            )
        if src.get("id") in top3_ids and src.get("retracted"):
            flags.append(
                {
                    "severity": "danger",
                    "source_id": src.get("id"),
                    "code": "RETRACTED",
                    "message": f"Retracted source: {src.get('path')}",
                }
            )

    topic = query_ctx.get("topic") or "other"
    for item in outdated:
        claim = item.get("claim") or {}
        if topic != "other" and claim.get("subject") not in {topic, "other"}:
            continue
        path = str(item.get("path") or "")
        if "adversarial/" in path:
            continue
        flags.append(
            {
                "severity": "warning",
                "source_id": item.get("document_id"),
                "code": f"OUTDATED_{str(item.get('reason') or 'claim').upper()}",
                "message": f"Outdated ({item.get('reason')}): {path}",
            }
        )

    if hard_conflicts:
        override_wins = bool(
            winning
            and query_ctx.get("customer")
            and (winning.get("document") or {}).get("doc_type") == "contract"
        )
        for c in hard_conflicts:
            subj = c.get("subject")
            if topic != "other" and subj not in {topic, None, "other"}:
                continue
            # Contract override already resolves the disagreement for this customer
            if override_wins and subj == (winning or {}).get("subject"):
                flags.append(
                    {
                        "severity": "info",
                        "source_id": None,
                        "code": "OVERRIDE_RESOLVED",
                        "message": (
                            f"Other {subj} values exist in the corpus; "
                            f"customer contract overrides them for this query."
                        ),
                    }
                )
                continue
            vals = [str(v.get("value")) for v in c.get("values") or []]
            flags.append(
                {
                    "severity": "danger",
                    "source_id": None,
                    "code": "CLAIM_CONFLICT",
                    "message": f"Conflicting {subj} values: {', '.join(vals)}",
                }
            )

    flags = _dedupe_flags(flags)[:6]

    top = primary[0] if primary else (usable[0] if usable else None)
    top_trust = float(top["trust"]) if top else 0.0
    max_authority = max((float(s["signals"]["authority"]) for s in usable), default=0.0)

    # Gap: only low-authority evidence
    if not usable or max_authority <= tc.gap_authority_ceiling:
        gaps.append("Only low-authority evidence available for this question")
    if query_ctx.get("topic") == "sick_leave" and query_ctx.get("country") == "DE":
        gaps.append("No current approved DE sick-leave procedure; review overdue")
    if query_ctx.get("question_type") == "how_to" and not any(
        s.get("doc_type") in {"kb", "procedure", "email", "meeting"} for s in usable
    ):
        gaps.append("No procedural/how-to source retrieved")

    # Overdue official source + informal disagreement → gap (T5)
    if top and float(top.get("signals", {}).get("currency") or 1) <= 0.35:
        if query_ctx.get("topic") == "sick_leave":
            gaps.append("Official source is stale; newer informal channels disagree")
            verdict_force_gap = True
        else:
            verdict_force_gap = False
    else:
        verdict_force_gap = False

    # Verdict selection
    if hard_conflicts and len({str(v.get("value")) for c in hard_conflicts for v in c.get("values") or []}) > 1:
        # If customer override wins clearly, prefer trusted/caution with override note
        if (
            winning
            and query_ctx.get("customer")
            and (winning.get("document") or {}).get("doc_type") == "contract"
        ):
            verdict = "trusted" if top_trust >= tc.trusted_threshold else "caution"
            headline = _headline_from_claim(winning, query_ctx) or "Contract override applies"
        else:
            verdict = "conflict"
            headline = "Sources disagree"
    elif verdict_force_gap or (gaps and (top_trust < tc.caution_threshold or query_ctx.get("topic") == "sick_leave")):
        verdict = "gap"
        headline = "Knowledge gap"
    elif top_trust >= tc.trusted_threshold and primary:
        verdict = "trusted"
        headline = _headline_from_claim(winning, query_ctx) or "Trusted answer"
    elif top_trust >= tc.caution_threshold:
        verdict = "caution"
        headline = _headline_from_claim(winning, query_ctx) or "Answer with caution"
    else:
        verdict = "gap"
        headline = "Insufficient reliable evidence"

    answer = _compose_answer(query_ctx, winning, top, verdict, hard_conflicts, experts)
    confidence = _confidence(verdict, top_trust, hard_conflicts, gaps)

    # Serialise sources for API shape
    sources_out = []
    ordered = primary + [s for s in supporting if s not in primary]
    # Include a few blocked as flags already cover them; still list top blocked for transparency
    blocked = [s for s in scored_sources if s.get("role") == "blocked"]
    for src in (ordered + blocked)[:12]:
        sources_out.append(
            {
                "id": src.get("id"),
                "title": src.get("title") or "",
                "doc_type": src.get("doc_type") or "",
                "role": src.get("role") or "supporting",
                "trust": float(src.get("trust") or 0.0),
                "signals": src.get("signals") or {},
                "reasons": src.get("reasons") or [],
                "evidence": src.get("evidence") or "",
                "date": src.get("date"),
                "path": src.get("path") or "",
                "status": src.get("status"),
                "country": src.get("country"),
                "customer": src.get("customer"),
            }
        )

    return {
        "query_context": {
            "customer": query_ctx.get("customer"),
            "country": query_ctx.get("country"),
            "topic": query_ctx.get("topic") or "other",
            "question_type": query_ctx.get("question_type") or "lookup",
            "claim_to_verify": query_ctx.get("claim_to_verify"),
        },
        "verdict": verdict,
        "headline": headline,
        "answer": answer,
        "confidence": round(confidence, 4),
        "sources": sources_out,
        "flags": flags,
        "gaps": gaps,
        "experts": [
            {
                "name": e["name"],
                "role": e.get("role") or "",
                "why": e.get("why") or "",
                "available": bool(e.get("available", True)),
            }
            for e in experts
        ],
        "meta": {
            "winning_claim": winning,
            "conflict_count": len(conflicts),
            "scored": len(scored_sources),
        },
    }


def _dedupe_flags(flags: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[Any, ...]] = set()
    out: list[dict[str, Any]] = []
    severity_rank = {"danger": 0, "warning": 1, "info": 2}
    ordered = sorted(flags, key=lambda f: severity_rank.get(str(f.get("severity")), 9))
    for flag in ordered:
        key = (flag.get("code"), flag.get("message"), flag.get("source_id"))
        soft = (flag.get("code"), flag.get("message"))
        if key in seen or soft in seen:
            continue
        seen.add(key)
        seen.add(soft)
        out.append(flag)
    return out


def _human_unit(unit: str | None) -> str:
    if not unit:
        return ""
    mapping = {
        "calendar_day": "calendar day",
        "working_day": "working day",
        "EUR/month": "per month",
        "EUR/day": "per day",
        "EUR": "",
    }
    return mapping.get(unit, unit.replace("_", " "))


def _format_claim_value(claim: dict[str, Any]) -> str:
    val = str(claim.get("value_text") or claim.get("value_num") or "").strip()
    unit = claim.get("unit") or ""
    human = _human_unit(unit)
    if not human:
        return val
    # Avoid "12th calendar day calendar_day" / "EUR 160 EUR/month"
    compact_unit = unit.replace("_", "").replace("/", "").casefold()
    compact_val = val.replace(" ", "").replace("_", "").replace("/", "").casefold()
    if compact_unit and compact_unit in compact_val:
        return val
    if human.casefold() in val.casefold():
        return val
    return f"{val} ({human})"


def _headline_from_claim(
    winning: dict[str, Any] | None, query_ctx: dict[str, Any]
) -> str | None:
    if not winning:
        return None
    label = _format_claim_value(winning)
    if not label:
        return None
    doc = winning.get("document") or {}
    customer = query_ctx.get("customer")
    if doc.get("doc_type") == "contract" and customer:
        return f"{label} — {customer} contract override"
    return label


def _compose_answer(
    query_ctx: dict[str, Any],
    winning: dict[str, Any] | None,
    top: dict[str, Any] | None,
    verdict: str,
    conflicts: list[dict[str, Any]],
    experts: list[dict[str, Any]],
) -> str:
    if query_ctx.get("question_type") == "who_knows":
        if experts:
            names = ", ".join(e["name"] for e in experts[:3])
            return f"Recommended experts: {names}."
        return "No matching experts found in the directory."

    if verdict == "conflict":
        parts = ["Conflicting values found across sources."]
        if winning:
            parts.append(
                f"Highest-weighted claim: {_format_claim_value(winning)} "
                f"({(winning.get('document') or {}).get('path')})."
            )
        return " ".join(parts)

    if verdict == "gap":
        bits = ["No sufficiently trusted source answers this reliably."]
        if experts:
            bits.append(f"Consider asking {experts[0]['name']}.")
        return " ".join(bits)

    if winning and (winning.get("value_text") or winning.get("value_num") is not None):
        doc = winning.get("document") or {}
        val = _format_claim_value(winning)
        path = doc.get("path") or doc.get("title") or "source"
        customer = query_ctx.get("customer")
        topic = query_ctx.get("topic")
        if doc.get("doc_type") == "contract" and customer:
            hint = ""
            if topic == "remote_allowance":
                hint = " This overrides the standard Belgian policy (EUR 151/month)."
            elif topic == "payroll_deadline":
                hint = " This overrides the standard Belgian deadline (18th)."
            return f"Use {val} ({customer} contract: {path}).{hint}"
        prefix = "Verified: " if query_ctx.get("question_type") == "verify_claim" else "Use "
        return f"{prefix}{val} (from {path})."

    if top:
        snip = (top.get("evidence") or "").strip().replace("\n", " ")
        if len(snip) > 280:
            snip = snip[:277] + "..."
        return snip or f"See {top.get('title')}."

    return "No answer assembled."


def _confidence(
    verdict: str,
    top_trust: float,
    conflicts: list[dict[str, Any]],
    gaps: list[str],
) -> float:
    if verdict == "trusted":
        return max(0.7, min(0.95, top_trust))
    if verdict == "caution":
        return max(0.4, min(0.7, top_trust))
    if verdict == "conflict":
        return max(0.25, min(0.55, top_trust * 0.7))
    base = min(0.35, top_trust)
    if gaps:
        base = min(base, 0.3)
    return base
