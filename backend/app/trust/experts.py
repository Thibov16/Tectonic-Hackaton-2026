"""Expert recommendation from the expert directory."""

from __future__ import annotations

from datetime import date
from typing import Any

from app.config import AppConfig
from app.ingest import store


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _topic_overlap(expert_topics: list[Any], topic: str) -> float:
    if not topic or topic == "other":
        return 0.3
    tokens = topic.replace("_", " ").casefold()
    parts = topic.casefold().split("_")
    score = 0.0
    for t in expert_topics or []:
        tt = str(t).casefold()
        if topic.casefold() == tt or tokens == tt:
            return 1.0
        if any(p in tt for p in parts if len(p) > 2):
            score = max(score, 0.7)
        if any(w in tt for w in tokens.split()):
            score = max(score, 0.5)
    return score


def find_experts(
    conn: Any,
    *,
    topic: str,
    country: str | None,
    config: AppConfig,
    limit: int = 3,
) -> list[dict[str, Any]]:
    """
    Rank experts: topic 0.4 + country 0.2 + recency 0.2 + evidence/availability 0.2.
    """
    experts = store.list_experts(conn)
    today = config.today
    ranked: list[dict[str, Any]] = []

    for expert in experts:
        topics = expert.get("topics") or []
        countries = [str(c).upper() for c in (expert.get("countries") or [])]
        topic_s = _topic_overlap(topics, topic)

        if country and countries:
            country_s = 1.0 if country.upper() in countries else 0.0
        elif country and not countries:
            country_s = 0.3
        else:
            country_s = 0.5

        last = _parse_date(expert.get("last_active"))
        if last:
            age = (today - last).days
            if age <= 30:
                recency_s = 1.0
            elif age <= 180:
                recency_s = 0.7
            elif age <= 365:
                recency_s = 0.4
            else:
                recency_s = 0.1
        else:
            recency_s = 0.2

        available = bool(expert.get("available", True))
        evidence_s = 0.9 if available else 0.2
        notes = (expert.get("notes") or "").casefold()
        if "left" in notes or "inactive" in (expert.get("role") or "").casefold():
            evidence_s = 0.1
            available = False

        score = 0.4 * topic_s + 0.2 * country_s + 0.2 * recency_s + 0.2 * evidence_s
        why_parts = []
        if topic_s >= 0.5:
            why_parts.append(f"covers {topic}")
        if country and country_s >= 1.0:
            why_parts.append(f"country {country}")
        if last:
            why_parts.append(f"last active {last.isoformat()}")
        if not available:
            why_parts.append("marked unavailable")

        ranked.append(
            {
                "name": expert["name"],
                "role": expert.get("role") or "",
                "why": "; ".join(why_parts) or "directory match",
                "available": available,
                "score": round(score, 4),
            }
        )

    ranked.sort(key=lambda e: e["score"], reverse=True)
    return ranked[:limit]
