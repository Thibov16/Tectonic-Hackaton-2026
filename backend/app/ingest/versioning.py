"""Version grouping, supersession links, duplicate/forgery detection."""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from app.ingest import store


def _version_key(version: str | None) -> tuple[int, ...]:
    if not version:
        return (0,)
    nums = [int(x) for x in re.findall(r"\d+", version)]
    return tuple(nums) if nums else (0,)


def _is_eligible_current(doc: dict[str, Any], today: date) -> bool:
    status = (doc.get("status") or "").upper()
    if status == "DRAFT":
        return False
    loc = (doc.get("source_location") or "").casefold()
    if loc in {"personal_drive", "ai_generated", "wiki"}:
        return False
    if not doc.get("approved_by") and loc != "official_repo" and status not in {
        "CURRENT",
        "SIGNED",
        "FINAL",
    }:
        # Unapproved adversarial copies must not win version races
        if "adversarial" in (doc.get("path") or "").casefold():
            return False
    eff = doc.get("effective_date")
    if eff:
        try:
            if date.fromisoformat(str(eff)[:10]) > today:
                return False
        except ValueError:
            pass
    return True


def _rank_key(doc: dict[str, Any]) -> tuple:
    loc = (doc.get("source_location") or "").casefold()
    official = 1 if loc == "official_repo" else 0
    approved = 1 if doc.get("approved_by") else 0
    status_bonus = 1 if (doc.get("status") or "").upper() == "CURRENT" else 0
    return (official, approved, status_bonus, _version_key(doc.get("version")))


def apply_versioning(conn: Any, today: date) -> list[dict[str, str]]:
    """Mark CURRENT/SUPERSEDED within doc_id groups and create supersedes links."""
    docs = store.list_documents(conn)
    by_id: dict[str, list[dict[str, Any]]] = {}
    for doc in docs:
        doc_id = doc.get("doc_id")
        if not doc_id:
            continue
        by_id.setdefault(str(doc_id), []).append(doc)

    actions: list[dict[str, str]] = []
    for doc_id, group in by_id.items():
        eligible = [d for d in group if _is_eligible_current(d, today)]
        if not eligible:
            # Fall back to best official/approved even if status odd
            eligible = [
                d
                for d in group
                if (d.get("status") or "").upper() != "DRAFT"
                and (d.get("source_location") or "") == "official_repo"
            ]
        if not eligible:
            continue

        eligible.sort(key=_rank_key, reverse=True)
        current = eligible[0]

        for doc in group:
            if doc["id"] == current["id"]:
                if (doc.get("status") or "").upper() != "DRAFT":
                    conn.execute(
                        "UPDATE documents SET status = ? WHERE id = ?",
                        ("CURRENT", doc["id"]),
                    )
                    actions.append({"path": doc["path"], "status": "CURRENT"})
            elif (doc.get("status") or "").upper() == "DRAFT":
                continue
            else:
                conn.execute(
                    "UPDATE documents SET status = ? WHERE id = ?",
                    ("SUPERSEDED", doc["id"]),
                )
                store.add_link(conn, current["id"], doc["id"], "supersedes")
                store.add_link(conn, doc["id"], current["id"], "superseded_by")
                actions.append({"path": doc["path"], "status": "SUPERSEDED"})

        # Near-duplicate without approval from personal drive
        for doc in group:
            if doc["id"] == current["id"]:
                continue
            if (doc.get("source_location") or "") == "personal_drive" and not doc.get("approved_by"):
                store.add_link(conn, doc["id"], current["id"], "duplicates")
                actions.append({"path": doc["path"], "status": "duplicates"})

    conn.commit()
    return actions
