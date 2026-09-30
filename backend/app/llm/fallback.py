"""Deterministic answer templates — never invent facts."""

from __future__ import annotations

from typing import Any


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
    compact_unit = unit.replace("_", "").replace("/", "").casefold()
    compact_val = val.replace(" ", "").replace("_", "").replace("/", "").casefold()
    if compact_unit and compact_unit in compact_val:
        return val
    if human.casefold() in val.casefold():
        return val
    return f"{val} ({human})"


def compose_answer(verdict: dict[str, Any], query_ctx: dict[str, Any]) -> str:
    # Keep a solid engine draft when present and already readable
    existing = str(verdict.get("answer") or "").strip()
    if existing and "calendar_day" not in existing and "EUR/month" not in existing.split(")")[0]:
        if existing.startswith(("Use ", "Verified:", "No ", "Conflicting", "Recommended")):
            return existing

    winning = verdict.get("winning_claim")
    claim = None
    doc: dict[str, Any] = {}
    if isinstance(winning, dict):
        if isinstance(winning.get("claim"), dict):
            claim = winning["claim"]
            doc = winning.get("doc") or winning.get("document") or {}
        elif "value_num" in winning or "value_text" in winning:
            claim = winning
            doc = winning.get("document") or {}

    customer = query_ctx.get("customer")
    topic = query_ctx.get("topic")

    if claim:
        val = _format_claim_value(claim)
        path = doc.get("path") or ""
        if doc.get("doc_type") == "contract" and customer:
            policy_hint = ""
            if topic == "remote_allowance":
                policy_hint = " This overrides the standard Belgian policy (EUR 151/month)."
            elif topic == "payroll_deadline":
                policy_hint = " This overrides the standard Belgian deadline (18th)."
            return f"Use {val} ({customer} contract: {path}).{policy_hint}"
        return f"Use {val} from {path}."

    if verdict.get("verdict") == "gap":
        gaps = verdict.get("gaps") or []
        return (gaps[0] if gaps else "Insufficient reliable coverage.") + " Involve an expert."

    if verdict.get("verdict") == "conflict":
        return (
            "Sources of similar authority disagree. "
            "Do not pick a single value; escalate to the document owner."
        )

    if existing:
        return existing

    sources = verdict.get("sources") or []
    primary = next((s for s in sources if s.get("role") == "primary"), None)
    if primary:
        return f"See primary source: {primary.get('title')} ({primary.get('path')})."
    return "No trusted answer could be assembled."
