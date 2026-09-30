"""Rule-based claim extraction from document text."""

from __future__ import annotations

import re
from typing import Any


SUBJECT_KEYWORDS = {
    "remote_allowance": (
        "remote work allowance",
        "telework allowance",
        "home office",
        "remote allowance",
        "structural allowance",
        "teleworking",
    ),
    "payroll_deadline": (
        "input deadline",
        "payroll input",
        "variable pay input",
        "submit",
        "calendar day",
        "working day",
    ),
    "meal_vouchers": ("meal voucher", "meal vouchers", "face value"),
    "company_car": ("company car", "co2", "retroactive"),
    "sick_leave": ("sick leave", "eau", "medical certificate", "paper certificate"),
    "data_handling": ("payroll export", "bank detail", "dual approval", "data handling"),
}


def detect_subjects(text: str) -> list[str]:
    lowered = text.casefold()
    found = []
    for subject, keys in SUBJECT_KEYWORDS.items():
        if any(k in lowered for k in keys):
            found.append(subject)
    return found


def extract_claims_from_text(
    text: str,
    *,
    scope_country: str | None = None,
    scope_customer: str | None = None,
    valid_from: str | None = None,
) -> list[dict[str, Any]]:
    claims: list[dict[str, Any]] = []
    subjects = detect_subjects(text)
    if not subjects:
        subjects = ["other"]

    # EUR amounts
    for match in re.finditer(
        r"(EUR|€)\s?(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d+)?|\d+(?:[.,]\d+)?)",
        text,
        flags=re.I,
    ):
        raw = match.group(2).replace(",", ".")
        # handle 1.09 style
        try:
            value = float(raw)
        except ValueError:
            continue
        fragment = text[max(0, match.start() - 40) : match.end() + 40].strip()
        unit = "EUR"
        # unit hints
        window = text[match.end() : match.end() + 40].casefold()
        if "per month" in window or "/month" in window:
            unit = "EUR/month"
        elif "per day" in window or "/day" in window:
            unit = "EUR/day"
        subject = "remote_allowance"
        if "meal" in fragment.casefold():
            subject = "meal_vouchers"
        elif any(s == "company_car" for s in subjects):
            subject = "company_car"
        elif "remote_allowance" in subjects:
            subject = "remote_allowance"
        elif "meal_vouchers" in subjects:
            subject = "meal_vouchers"
        claims.append(
            {
                "subject": subject,
                "value_num": value,
                "value_text": match.group(0),
                "unit": unit,
                "scope_country": scope_country,
                "scope_customer": scope_customer,
                "valid_from": valid_from,
                "valid_to": None,
                "fragment": fragment,
                "extractor": "rule",
                "confidence": 0.8,
            }
        )

    # Ordinal calendar/working day deadlines
    for match in re.finditer(
        r"\b(?:the\s+)?(\d{1,2})(?:st|nd|rd|th)?\s+(calendar\s+day|working\s+day)\b",
        text,
        flags=re.I,
    ):
        day = int(match.group(1))
        kind = match.group(2).casefold()
        fragment = text[max(0, match.start() - 40) : match.end() + 40].strip()
        claims.append(
            {
                "subject": "payroll_deadline",
                "value_num": float(day),
                "value_text": match.group(0),
                "unit": "working_day" if "working" in kind else "calendar_day",
                "scope_country": scope_country,
                "scope_customer": scope_customer,
                "valid_from": valid_from,
                "valid_to": None,
                "fragment": fragment,
                "extractor": "rule",
                "confidence": 0.85,
            }
        )

    # "by the 12th" / "the 18th"
    for match in re.finditer(r"\b(?:by\s+)?(?:the\s+)?(\d{1,2})(?:st|nd|rd|th)\b", text, flags=re.I):
        day = int(match.group(1))
        if day > 31:
            continue
        fragment = text[max(0, match.start() - 50) : match.end() + 50].strip()
        if "deadline" not in fragment.casefold() and "input" not in fragment.casefold() and "submit" not in fragment.casefold():
            continue
        claims.append(
            {
                "subject": "payroll_deadline",
                "value_num": float(day),
                "value_text": match.group(0),
                "unit": "calendar_day",
                "scope_country": scope_country,
                "scope_customer": scope_customer,
                "valid_from": valid_from,
                "valid_to": None,
                "fragment": fragment,
                "extractor": "rule",
                "confidence": 0.7,
            }
        )

    return _dedupe_claims(claims)


def _dedupe_claims(claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[Any, ...]] = set()
    out: list[dict[str, Any]] = []
    for claim in claims:
        key = (
            claim["subject"],
            claim.get("value_num"),
            claim.get("unit"),
            claim.get("scope_customer"),
            claim.get("fragment", "")[:40],
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(claim)
    return out
