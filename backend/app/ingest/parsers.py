"""File parsers for the SD Worx synthetic testset."""

from __future__ import annotations

import csv
import email
import io
import re
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

import frontmatter


def _norm_topic(raw: str) -> str:
    key = raw.casefold().strip()
    mapping = {
        "company car": "company_car",
        "benefits": "company_car",
        "payroll tool": "company_car",
        "payroll policy": "payroll_deadline",
        "deadlines": "payroll_deadline",
        "german payroll": "sick_leave",
        "sick leave": "sick_leave",
        "eau": "sick_leave",
        "home office compensation": "remote_allowance",
        "nl payroll": "remote_allowance",
        "customer communication": "other",
        "pilots": "payroll_deadline",
        "general payroll": "payroll_deadline",
    }
    return mapping.get(key, key.replace(" ", "_"))


def _iso_date(value: str | None) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    m = re.match(r"^(\d{4}-\d{2}-\d{2})", text)
    if m:
        return m.group(1)
    try:
        return parsedate_to_datetime(text).date().isoformat()
    except (TypeError, ValueError, IndexError, OverflowError):
        return None


def parse_markdown(path: Path) -> dict[str, Any]:
    post = frontmatter.load(str(path))
    meta = {str(k).lower(): v for k, v in dict(post.metadata).items()}
    body = post.content.strip()
    title_match = re.search(r"^#\s+(.+)$", body, flags=re.MULTILINE)
    title = title_match.group(1).strip() if title_match else path.stem
    return {
        "path": str(path),
        "title": title,
        "raw_text": body,
        "front_matter": meta,
        "created_date": _iso_date(str(meta.get("effective") or meta.get("signed") or meta.get("date") or "")),
        "effective_date": _iso_date(str(meta.get("effective") or "")),
        "approval_date": _iso_date(str(meta.get("approval_date") or "")),
        "approved_by": str(meta.get("approved_by") or "") or None,
        "owner": str(meta.get("owner") or "") or None,
        "author": str(meta.get("author") or meta.get("owner") or "") or None,
        "doc_id": str(meta.get("doc_id") or "") or None,
        "version": str(meta.get("version") or "") or None,
        "status": str(meta.get("status") or "").split("(")[0].strip().upper() or None,
        "country": str(meta.get("country") or "").upper() or None,
        "customer": str(meta.get("customer") or "") or None,
        "next_review": _iso_date(str(meta.get("next_review") or "")),
        "parser": "markdown",
    }


def parse_eml(path: Path) -> dict[str, Any]:
    msg = email.message_from_bytes(path.read_bytes())
    body_parts: list[str] = []
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True)
                if isinstance(payload, bytes):
                    body_parts.append(payload.decode(errors="replace"))
    else:
        payload = msg.get_payload(decode=True)
        if isinstance(payload, bytes):
            body_parts.append(payload.decode(errors="replace"))
        elif isinstance(msg.get_payload(), str):
            body_parts.append(msg.get_payload())  # type: ignore[arg-type]

    body = "\n".join(body_parts).strip() or path.read_text(encoding="utf-8", errors="replace")
    date_hdr = msg.get("Date")
    return {
        "path": str(path),
        "title": (msg.get("Subject") or path.stem).strip(),
        "raw_text": body,
        "front_matter": {},
        "created_date": _iso_date(date_hdr),
        "effective_date": _iso_date(date_hdr),
        "author": (msg.get("From") or "").strip() or None,
        "from_header": (msg.get("From") or "").strip() or None,
        "to_header": (msg.get("To") or "").strip() or None,
        "reply_to": (msg.get("Reply-To") or "").strip() or None,
        "subject": (msg.get("Subject") or "").strip() or None,
        "parser": "eml",
        "doc_type_hint": "email",
    }


def parse_chat(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    channel = None
    messages: list[dict[str, str]] = []
    for line in lines:
        if "Teams channel" in line or line.startswith("["):
            channel = line.strip()
            continue
        m = re.match(
            r"^(\d{4}-\d{2}-\d{2})\s+(\d{1,2}:\d{2})\s+([^:]+):\s*(.*)$",
            line.strip(),
        )
        if m:
            messages.append(
                {
                    "date": m.group(1),
                    "time": m.group(2),
                    "author": m.group(3).strip(),
                    "text": m.group(4).strip(),
                }
            )
    first_date = messages[0]["date"] if messages else None
    return {
        "path": str(path),
        "title": channel or path.stem,
        "raw_text": text.strip(),
        "front_matter": {},
        "created_date": first_date,
        "effective_date": first_date,
        "messages": messages,
        "parser": "chat",
        "doc_type_hint": "chat",
    }


def parse_meeting(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace")
    date_m = re.search(r"Date:\s*(\d{4}-\d{2}-\d{2})", text)
    title_m = re.search(r"Title:\s*(.+)", text)
    attendees_m = re.search(r"Attendees:\s*(.+)", text)
    decisions = re.findall(r"^Decision:\s*(.+)$", text, flags=re.MULTILINE | re.IGNORECASE)
    actions = re.findall(r"^Action:\s*(.+)$", text, flags=re.MULTILINE | re.IGNORECASE)
    # Also capture inline Decision: lines without start anchor sometimes
    if not decisions:
        decisions = re.findall(r"Decision:\s*(.+)", text)
    return {
        "path": str(path),
        "title": (title_m.group(1).strip() if title_m else path.stem),
        "raw_text": text.strip(),
        "front_matter": {},
        "created_date": date_m.group(1) if date_m else None,
        "effective_date": date_m.group(1) if date_m else None,
        "attendees": [a.strip() for a in (attendees_m.group(1).split(",") if attendees_m else [])],
        "decisions": decisions,
        "actions": actions,
        "parser": "meeting",
        "doc_type_hint": "meeting",
    }


def parse_csv(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    rows = list(reader)
    stem = path.stem.casefold()
    if "expert" in stem:
        return [
            {
                "kind": "expert",
                "path": str(path),
                "name": (row.get("name") or row.get("Name") or "").strip(),
                "role": (row.get("role") or row.get("Role") or "").strip(),
        "topics": [
                    _norm_topic(t.strip())
                    for t in re.split(r"[;,|/]", row.get("topics") or row.get("Topics") or "")
                    if t.strip()
                ],
                "countries": [
                    t.strip().upper()
                    for t in re.split(r"[;,|/]", row.get("countries") or row.get("Countries") or "")
                    if t.strip()
                ],
                "last_active": (
                    row.get("last_active")
                    or row.get("last_active_date")
                    or row.get("last_active_in_channels")
                    or ""
                ).strip()
                or None,
                "available": (
                    str(row.get("available") or "true").strip().lower()
                    not in {"0", "false", "no", "left"}
                    and "left" not in (row.get("role") or "").casefold()
                    and "left" not in (row.get("notes") or "").casefold()
                ),
                "notes": (row.get("notes") or row.get("role") or "").strip() or None,
                "raw": row,
            }
            for row in rows
            if (row.get("name") or row.get("Name"))
        ]

    # ticket export → one document per row
    docs: list[dict[str, Any]] = []
    for i, row in enumerate(rows, start=1):
        ticket_id = (row.get("ticket_id") or row.get("id") or f"T-{i}").strip()
        customer = (row.get("customer") or "").strip() or None
        topic = (row.get("topic") or row.get("subject") or "").strip()
        resolution = (row.get("resolution") or row.get("notes") or "").strip()
        created = _iso_date(row.get("date") or row.get("created") or "")
        body = " | ".join(f"{k}: {v}" for k, v in row.items() if v)
        docs.append(
            {
                "path": f"{path}#{ticket_id}",
                "title": f"Ticket {ticket_id}: {topic or 'ticket'}",
                "raw_text": body,
                "front_matter": {},
                "created_date": created,
                "effective_date": created,
                "customer": customer,
                "author": (row.get("assignee") or row.get("owner") or "").strip() or None,
                "parser": "ticket_csv",
                "doc_type_hint": "ticket",
                "status": "FINAL",
            }
        )
    return docs


def parse_file(path: Path) -> list[dict[str, Any]]:
    suffix = path.suffix.casefold()
    if suffix == ".md":
        return [parse_markdown(path)]
    if suffix == ".eml":
        return [parse_eml(path)]
    if suffix == ".csv":
        return parse_csv(path)
    if suffix == ".txt":
        text = path.read_text(encoding="utf-8", errors="replace")
        if "MEETING TRANSCRIPT" in text or re.search(r"^Title:\s*", text, flags=re.M):
            return [parse_meeting(path)]
        if re.search(r"\d{4}-\d{2}-\d{2}\s+\d{1,2}:\d{2}\s+[^:]+:", text):
            return [parse_chat(path)]
        return [
            {
                "path": str(path),
                "title": path.stem,
                "raw_text": text.strip(),
                "front_matter": {},
                "parser": "text",
            }
        ]
    return []
