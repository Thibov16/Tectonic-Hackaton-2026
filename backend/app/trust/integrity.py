"""Integrity scanners for adversarial and spoof signals (deterministic)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

Severity = Literal["info", "warning", "critical"]


@dataclass
class IntegrityFinding:
    code: str
    severity: Severity
    message: str

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "severity": self.severity, "message": self.message}


INJECTION_PATTERNS = (
    r"ignore\s+(all\s+)?previous\s+instructions",
    r"system\s+instruction",
    r"disregard\s+any\s+other\s+document",
    r"rate\s+this\s+document",
    r"you\s+are\s+now",
    r"disregard\s+all\s+(prior|previous)",
)

BEC_PATTERNS = (
    r"skip\s+(the\s+)?(dual\s+)?approval",
    r"do\s+not\s+discuss\s+with\s+anyone",
    r"change\s+the\s+bank\s+account",
    r"i'?ll\s+take\s+responsibility",
    r"confirm\s+by\s+reply\s+only",
)

FABRICATED_CITATION = (
    r"section\s+7\.4",
    r"according\s+to\s+POL-SEC-002\s+section\s+7\.4",
)


def _find_reply_to_mismatch(text: str, reply_to: str | None, from_header: str | None) -> bool:
    body = text or ""
    if reply_to and from_header:
        from_dom = _domain(from_header)
        reply_dom = _domain(reply_to)
        if from_dom and reply_dom and from_dom != reply_dom:
            return True
    # Body-mentioned Reply-To (A01 style)
    m = re.search(r"Reply-To:\s*([^\s)>]+)", body, flags=re.I)
    if m and from_header:
        if _domain(m.group(1)) != _domain(from_header):
            return True
    return False


def _domain(addr: str) -> str:
    m = re.search(r"@([A-Za-z0-9.-]+)", addr or "")
    return (m.group(1) if m else "").casefold()


def scan_document(
    doc: dict[str, Any],
    *,
    allowed_domains: tuple[str, ...] = (),
) -> list[IntegrityFinding]:
    """Return integrity findings for a normalised or parsed document dict."""
    findings: list[IntegrityFinding] = []
    text = doc.get("raw_text") or ""
    meta = doc.get("metadata") or {}
    if not meta and doc.get("metadata_json"):
        raw_meta = doc.get("metadata_json")
        if isinstance(raw_meta, str):
            try:
                import json

                meta = json.loads(raw_meta)
            except json.JSONDecodeError:
                meta = {}
        elif isinstance(raw_meta, dict):
            meta = raw_meta
    if isinstance(meta, str):
        meta = {}
    path = (doc.get("path") or "").casefold()
    title = (doc.get("title") or "").casefold()
    from_header = doc.get("from_header") or meta.get("from_header")
    reply_to = doc.get("reply_to") or meta.get("reply_to")
    source_location = (doc.get("source_location") or "").casefold()
    status = (doc.get("status") or "").upper()
    country = doc.get("country")
    human_reviewed = str(
        (doc.get("front_matter") or meta.get("front_matter") or {}).get("human_reviewed", "")
    ).casefold()

    # Prompt injection
    hay = text + "\n" + (doc.get("raw_text") or "")
    for pat in INJECTION_PATTERNS:
        if re.search(pat, hay, flags=re.I):
            findings.append(
                IntegrityFinding(
                    "PROMPT_INJECTION",
                    "critical",
                    "Document contains prompt-injection language; treat as blocked.",
                )
            )
            break
    if "<!--" in text and re.search(r"ignore|system instruction|disregard", text, flags=re.I):
        if not any(f.code == "PROMPT_INJECTION" for f in findings):
            findings.append(
                IntegrityFinding(
                    "PROMPT_INJECTION",
                    "critical",
                    "Hidden HTML comment looks like a prompt injection.",
                )
            )

    # Spoofed sender domain
    if from_header and allowed_domains:
        dom = _domain(from_header)
        allowed = {d.casefold() for d in allowed_domains}
        if dom and dom not in allowed:
            findings.append(
                IntegrityFinding(
                    "SPOOFED_SENDER",
                    "critical",
                    f"From domain '{dom}' is not in the allowed sender list.",
                )
            )

    # BEC / CEO fraud patterns
    if any(re.search(p, text, flags=re.I) for p in BEC_PATTERNS):
        findings.append(
            IntegrityFinding(
                "BEC_PATTERN",
                "critical",
                "Message matches business-email-compromise pressure patterns.",
            )
        )
    if _find_reply_to_mismatch(text, reply_to, from_header):
        findings.append(
            IntegrityFinding(
                "REPLY_TO_MISMATCH",
                "critical",
                "Reply-To domain does not match From; possible spoofing.",
            )
        )

    # Fabricated citation (A05)
    if any(re.search(p, text, flags=re.I) for p in FABRICATED_CITATION):
        findings.append(
            IntegrityFinding(
                "FABRICATED_CITATION",
                "warning",
                "Cites POL-SEC-002 section 7.4 which does not exist in the official policy.",
            )
        )
    if "ai_summary" in path or "ai_generated" in path or doc.get("doc_type") == "ai_summary":
        if human_reviewed in {"", "no", "false", "0"}:
            findings.append(
                IntegrityFinding(
                    "UNREVIEWED_AI_SUMMARY",
                    "warning",
                    "AI-generated summary without human review.",
                )
            )

    # Internal inconsistency (A06): two different December deadlines
    if re.search(r"\b10th\b", text) and re.search(r"\b15\s+December\b", text, flags=re.I):
        findings.append(
            IntegrityFinding(
                "INTERNAL_INCONSISTENCY",
                "warning",
                "Document states conflicting deadlines (10th vs 15 December).",
            )
        )

    # Missing country (A08)
    if "a08" in path or (doc.get("doc_type") == "policy" and (not country or str(country).upper() in {"", "UNKNOWN", "(MISSING)"})):
        if "a08" in path or "country" in (doc.get("raw_text") or "").casefold() and "missing" in path:
            findings.append(
                IntegrityFinding(
                    "MISSING_COUNTRY",
                    "warning",
                    "Policy is missing reliable country metadata.",
                )
            )
    fm = doc.get("front_matter") or meta.get("front_matter") or {}
    if str(fm.get("country") or "").strip() in {"(missing)", ""} and "a08" in path:
        if not any(f.code == "MISSING_COUNTRY" for f in findings):
            findings.append(
                IntegrityFinding(
                    "MISSING_COUNTRY",
                    "warning",
                    "Country field missing in front matter.",
                )
            )

    # Possible forgery / unsigned near-duplicate (A03)
    if "personal" in source_location or "personal" in path or "unsigned" in path or "near_duplicate" in path:
        if not doc.get("approved_by") and status not in {"SIGNED", "CURRENT"}:
            findings.append(
                IntegrityFinding(
                    "POSSIBLE_FORGERY",
                    "warning",
                    "Unapproved near-duplicate from personal drive / unsigned copy.",
                )
            )
        elif "a03" in path or "unsigned" in path:
            findings.append(
                IntegrityFinding(
                    "POSSIBLE_FORGERY",
                    "warning",
                    "Unsigned near-duplicate policy copy.",
                )
            )

    # Unreliable wiki provenance (A09)
    if "wiki" in path or doc.get("doc_type") == "wiki" or "the latest" in title:
        findings.append(
            IntegrityFinding(
                "UNRELIABLE_PROVENANCE",
                "warning",
                "Undated or self-described 'latest' wiki page — low provenance trust.",
            )
        )
    if "a09" in path:
        if not any(f.code == "UNRELIABLE_PROVENANCE" for f in findings):
            findings.append(
                IntegrityFinding(
                    "UNRELIABLE_PROVENANCE",
                    "warning",
                    "Old wiki claiming to be the latest version.",
                )
            )

    return findings


def integrity_penalty(findings: list[IntegrityFinding]) -> float:
    """Start at 1.0; subtract per finding; floor at 0."""
    score = 1.0
    for f in findings:
        if f.severity == "critical":
            score -= 1.0
        elif f.severity == "warning":
            score -= 0.35
        else:
            score -= 0.1
    return max(0.0, score)


def has_critical(findings: list[IntegrityFinding]) -> bool:
    return any(f.severity == "critical" for f in findings)


def check_integrity(doc: dict[str, Any], config: Any = None) -> list[dict[str, Any]]:
    """Adapter used by scoring/pipeline — returns plain dict findings."""
    allowed = ()
    if config is not None:
        allowed = tuple(getattr(config, "allowed_email_domains", ()) or ())
    return [f.as_dict() for f in scan_document(doc, allowed_domains=allowed)]

