"""Normalise parsed documents: doc_type, country, customer, source_location."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from rapidfuzz import fuzz

from app.config import AppConfig


FOLDER_DOC_TYPES = {
    "policies": "policy",
    "procedures": "procedure",
    "contracts": "contract",
    "emails": "email",
    "chats": "chat",
    "meetings": "meeting",
    "adversarial": "other",
    "other": "other",
}


def _folder_of(path: str) -> str:
    parts = Path(path).parts
    for part in parts:
        if part.casefold() in FOLDER_DOC_TYPES:
            return part.casefold()
    return "other"


def infer_doc_type(parsed: dict[str, Any], rel_path: str) -> str:
    if parsed.get("doc_type_hint"):
        return str(parsed["doc_type_hint"])
    folder = _folder_of(rel_path)
    base = FOLDER_DOC_TYPES.get(folder, "other")
    name = Path(rel_path).name.casefold()
    text = (parsed.get("raw_text") or "").casefold()
    if "kb-" in name or "knowledge base" in text[:200]:
        return "kb"
    if "wiki" in name:
        return "wiki"
    if "release_notes" in name or "release notes" in text[:120]:
        return "release_notes"
    if "post_incident" in name or name.startswith("pia-"):
        return "analysis"
    if "ai_summary" in name or "summary:" in text[:80]:
        return "ai_summary"
    if "directory" in name:
        return "directory"
    if folder == "adversarial":
        if name.endswith(".eml"):
            return "email"
        if "chat" in name:
            return "chat"
        if "policy" in name or name.startswith("a03") or name.startswith("a06"):
            return "policy"
        if "wiki" in name:
            return "wiki"
        if "summary" in name:
            return "ai_summary"
    return base


def infer_source_location(rel_path: str, parsed: dict[str, Any]) -> str:
    name = Path(rel_path).name.casefold()
    text = (parsed.get("raw_text") or "").casefold()
    folder = _folder_of(rel_path)
    if "personal" in text or "personal drive" in text or "onedrive" in text:
        return "personal_drive"
    if folder == "emails" or parsed.get("parser") == "eml":
        return "mailbox"
    if folder == "chats" or parsed.get("parser") == "chat":
        return "teams"
    if "wiki" in name:
        return "wiki"
    if "ai_summary" in name or parsed.get("doc_type_hint") == "ai_summary":
        return "ai_generated"
    if folder in {"policies", "procedures", "contracts"}:
        return "official_repo"
    if "unsigned" in name or "near_duplicate" in name:
        return "personal_drive"
    return "unknown"


def infer_country(parsed: dict[str, Any], text: str, config: AppConfig) -> tuple[str, float]:
    raw_country = parsed.get("country")
    if raw_country:
        c = str(raw_country).split("(")[0].strip().upper()
        if c in {"BE", "NL", "DE", "ALL"}:
            return c, 1.0
        if c.startswith("ALL"):
            return "ALL", 1.0
        if c in {"", "MISSING", "UNKNOWN"} or "MISSING" in str(raw_country).upper():
            pass  # fall through to inference
        elif "/" in c:
            return c.split("/")[0], 0.6
        else:
            return c, 1.0
    lowered = text.casefold()
    if "belgium" in lowered or "belgian" in lowered or re.search(r"\bBE\b", text):
        return "BE", 0.6
    if "netherlands" in lowered or "dutch" in lowered or re.search(r"\bNL\b", text):
        return "NL", 0.6
    if "germany" in lowered or "german" in lowered or "eAU" in text or re.search(r"\bDE\b", text):
        return "DE", 0.6
    if "EUR" in text and "per day" in lowered:
        return "NL", 0.3
    return "UNKNOWN", 0.3


def infer_customer(text: str, config: AppConfig) -> tuple[str | None, float]:
    lowered = text.casefold()
    best_name = None
    best_score = 0
    for name, _country, aliases in config.known_customers:
        candidates = (name.casefold(), *aliases)
        for alias in candidates:
            if alias in lowered:
                return name, 0.9
            score = fuzz.partial_ratio(alias, lowered[:500])
            if score > best_score:
                best_score = score
                best_name = name
    if best_score >= 85:
        return best_name, 0.6
    return None, 0.3


def normalise_document(
    parsed: dict[str, Any],
    *,
    data_root: Path,
    config: AppConfig,
) -> dict[str, Any] | None:
    if parsed.get("kind") == "expert":
        return None

    abs_path = Path(parsed["path"])
    try:
        rel = str(abs_path.resolve().relative_to(data_root.resolve()))
    except ValueError:
        rel = str(abs_path)

    raw = parsed.get("raw_text") or ""
    fm = parsed.get("front_matter") or {}
    confidence = 1.0 if fm else 0.6

    doc_type = infer_doc_type(parsed, rel)
    country, c_conf = infer_country(parsed, raw, config)
    customer = parsed.get("customer")
    cust_conf = 1.0 if customer else 0.0
    if not customer:
        customer, cust_conf = infer_customer(raw + " " + rel, config)
    confidence = min(confidence, c_conf if country != "UNKNOWN" else confidence)
    if customer:
        confidence = max(confidence, cust_conf * 0.8)

    status = parsed.get("status")
    if status:
        status = str(status).split("(")[0].strip().upper()
    if not status:
        if doc_type == "contract":
            status = "SIGNED"
        elif "DRAFT" in Path(rel).name.upper():
            status = "DRAFT"
        else:
            status = "UNKNOWN"
            confidence = min(confidence, 0.3)

    source_location = infer_source_location(rel, parsed)

    return {
        "path": rel.replace("\\", "/"),
        "doc_type": doc_type,
        "title": parsed.get("title") or Path(rel).stem,
        "doc_id": parsed.get("doc_id") or fm.get("doc_id"),
        "version": str(parsed.get("version") or fm.get("version") or "") or None,
        "status": status,
        "country": country,
        "customer": customer,
        "effective_date": parsed.get("effective_date") or parsed.get("created_date"),
        "created_date": parsed.get("created_date"),
        "last_modified": parsed.get("last_modified"),
        "author": parsed.get("author"),
        "owner": parsed.get("owner") or fm.get("owner"),
        "approved_by": parsed.get("approved_by") or fm.get("approved_by"),
        "approval_date": parsed.get("approval_date") or _str_or_none(fm.get("approval_date")),
        "next_review": parsed.get("next_review") or _str_or_none(fm.get("next_review")),
        "source_location": source_location,
        "raw_text": raw,
        "metadata_confidence": round(confidence, 2),
        "retracted": False,
        "metadata": {
            "front_matter": fm,
            "parser": parsed.get("parser"),
            "from_header": parsed.get("from_header"),
            "to_header": parsed.get("to_header"),
            "reply_to": parsed.get("reply_to"),
            "subject": parsed.get("subject"),
            "messages": parsed.get("messages"),
            "decisions": parsed.get("decisions"),
            "actions": parsed.get("actions"),
            "attendees": parsed.get("attendees"),
        },
    }


def _str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
