"""Classify scrambled knowledge-base files into a searchable client knowledge DB.

Default input folder is ../testset (emails, chats, policies, contracts, …).

Usage:
    python classify.py
    python classify.py --data-dir ../testset --db knowledge.db
"""

from __future__ import annotations

import argparse
import csv
import io
import re
from datetime import datetime, timezone
from pathlib import Path

from db import (
    DEFAULT_DATA_DIR,
    DEFAULT_DB_PATH,
    PROJECT_ROOT,
    connect,
    init_db,
    upsert_client,
    upsert_document,
)
from keywords import LEGAL_SUFFIXES, extract_keywords

ALLOWED_TAGS = (
    "instructions",
    "client_profile",
    "contacts",
    "payroll",
    "hr_process",
    "meeting_notes",
    "next_steps",
    "policy",
    "contract",
    "procedure",
    "adversarial",
    "other",
)

SUPPORTED_SUFFIXES = {".txt", ".md", ".eml", ".csv"}
SKIP_DIR_NAMES = {"ground_truth", ".git"}
SKIP_FILE_NAMES = {"readme.md"}

# Folder name under testset/ → source_type
FOLDER_SOURCE_TYPES = {
    "emails": "email",
    "email": "email",
    "chats": "teams",
    "chat": "teams",
    "meetings": "meeting",
    "meeting": "meeting",
    "policies": "policy",
    "policy": "policy",
    "procedures": "procedure",
    "procedure": "procedure",
    "contracts": "contract",
    "contract": "contract",
    "adversarial": "adversarial",
    "other": "other",
    "manuals": "manual",
    "manual": "manual",
    "onenote": "onenote",
}

SOURCE_PREFIXES = (
    "email",
    "manual",
    "teams",
    "onenote",
    "policy",
    "procedure",
    "contract",
    "meeting",
    "adversarial",
)

# Fictional customers from the SD Worx test set README
KNOWN_CLIENTS: tuple[tuple[str, tuple[str, ...], str | None], ...] = (
    ("Janssens Logistics NV", ("janssens logistics", "janssens-logistics"), "Logistics"),
    ("Van Dijk Retail BV", ("van dijk retail", "vandijk retail", "van dijk"), "Retail"),
    ("Mueller Bau GmbH", ("mueller bau", "mueller-bau"), "Construction"),
)

TAG_PATTERNS: dict[str, tuple[str, ...]] = {
    "instructions": (
        r"\binstruction(?:s| list)?\b",
        r"\bchecklist\b",
        r"\bsop\b",
        r"\bstep\s*\d+\b",
        r"^\s*\d+\.\s+\S",
    ),
    "client_profile": (
        r"\bclient profile\b",
        r"\bclient details\b",
        r"\bcustomer\b",
        r"\bheadcount\b",
        r"\bindustry\b",
        r"\bemployees?\b",
    ),
    "contacts": (
        r"\bcontacts?\b",
        r"\bhr director\b",
        r"\bhr manager\b",
        r"\bpayroll lead\b",
        r"\bstakeholders?\b",
        r"\bexpert\b",
    ),
    "payroll": (
        r"\bpayroll\b",
        r"\bwage(?:s)?\b",
        r"\bsalary\b",
        r"\bpay input\b",
        r"\bmeal voucher",
        r"\bremote(?: work)? allowance\b",
    ),
    "hr_process": (
        r"\bhr process(?:es)?\b",
        r"\babsence\b",
        r"\bsick leave\b",
        r"\bleave\b",
        r"\bonboarding\b",
        r"\bremote work\b",
        r"\btelework\b",
    ),
    "meeting_notes": (
        r"\bmeeting notes\b",
        r"\bteams (?:channel|thread)\b",
        r"\bsteering\b",
        r"\bknowledge sync\b",
        r"^\[\d{1,2}:\d{2}\]",
        r"^\d{4}-\d{2}-\d{2}\s+\d{1,2}:\d{2}\b",
    ),
    "next_steps": (
        r"\bnext steps?\b",
        r"\baction list\b",
        r"\bfollow-?up\b",
        r"\bschedule\b",
    ),
    "policy": (
        r"\bpolicy\b",
        r"\bPOL-[A-Z]+-\d+",
        r"^---\s*$",
    ),
    "contract": (
        r"\bcontract\b",
        r"\bservice agreement\b",
        r"\bsigned\b",
        r"\bcustomer:\b",
    ),
    "procedure": (
        r"\bprocedure\b",
        r"\bPROC-[A-Z]+-\d+",
        r"\bKB-\d+",
        r"\bincident handling\b",
    ),
    "adversarial": (
        r"\bignore previous\b",
        r"\bprompt injection\b",
        r"\bceo fraud\b",
        r"\bspoof",
    ),
}

VENDOR_BLOCKLIST = {
    "sd worx",
    "share sd worx",
    "freeze excel",
    "internal manual",
    "payroll service desk",
    "payroll operations",
    "payroll operations be",
    "payroll be",
    "payroll be all",
    "payroll be faq",
    "payroll tool",
    "payroll deadlines",
    "payroll input deadlines",
    "meeting transcript",
    "draft for discussion",
    "team lead",
    "customer onboarding team",
    "senior payroll consultant",
    "home office compensation",
    "home office expense compensation",
    "remote work allowance",
    "electronic meal vouchers",
    "sick leave notification",
    "customer payroll data handling",
    "company car",
}


def looks_like_company(name: str) -> bool:
    """True for known customers or names that end with a legal entity suffix."""
    if not name:
        return False
    if any(name.casefold() == canonical.casefold() for canonical, *_ in KNOWN_CLIENTS):
        return True
    tokens = name.split()
    if not tokens:
        return False
    # Reject document/procedure codes mistaken for companies (PROC-INC, POL-HR, …)
    head = tokens[0].casefold()
    if head in {"pol", "proc", "kb", "pia", "faq", "sop"} or re.match(r"^[a-z]{2,5}-\d", head):
        return False
    if not tokens[-1].casefold() in {s.casefold() for s in LEGAL_SUFFIXES}:
        return False
    # Need at least one real word before the suffix
    return len(tokens) >= 2 and any(len(t) > 2 and t.casefold() not in LEGAL_SUFFIXES for t in tokens[:-1])


def parse_metadata(text: str) -> dict[str, str]:
    """Parse YAML-like --- frontmatter or leading key: value metadata headers."""
    meta: dict[str, str] = {}
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            block = text[3:end].strip()
            for line in block.splitlines():
                if ":" in line:
                    key, value = line.split(":", 1)
                    meta[key.strip().casefold()] = value.strip()
            return meta

    # Leading key: value lines until a blank line (common in .eml-adjacent notes)
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            break
        if re.match(r"^(From|To|Date|Subject):", stripped, flags=re.IGNORECASE):
            continue
        match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.+)$", stripped)
        if not match:
            break
        meta[match.group(1).casefold()] = match.group(2).strip()
    return meta


def strip_frontmatter(text: str) -> str:
    if not text.startswith("---"):
        return text
    end = text.find("\n---", 3)
    if end == -1:
        return text
    return text[end + 4 :].lstrip("\n")


def read_file_text(path: Path) -> str:
    raw = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix.casefold() != ".csv":
        return raw.strip()

    # Flatten CSV into searchable text
    reader = csv.reader(io.StringIO(raw))
    rows = list(reader)
    if not rows:
        return ""
    lines = [" | ".join(rows[0])]
    for row in rows[1:]:
        lines.append(" | ".join(row))
    return "\n".join(lines).strip()


def detect_source_type(path: Path, data_dir: Path) -> str:
    try:
        relative = path.resolve().relative_to(data_dir.resolve())
        if relative.parts:
            folder = relative.parts[0].casefold()
            if folder in FOLDER_SOURCE_TYPES:
                return FOLDER_SOURCE_TYPES[folder]
    except ValueError:
        pass

    stem = path.stem.casefold()
    for prefix in SOURCE_PREFIXES:
        if stem == prefix or stem.startswith(f"{prefix}_") or stem.startswith(f"{prefix}-"):
            return prefix

    # Filename heuristics used in the test set
    if stem.startswith("e") and path.suffix.casefold() == ".eml":
        return "email"
    if stem.startswith(("c0", "c1", "c2", "c3", "c4")):
        return "teams"
    if stem.startswith("m0") or stem.startswith("m1"):
        return "meeting"
    if stem.startswith("pol-"):
        return "policy"
    if stem.startswith(("proc-", "kb-")):
        return "procedure"
    if path.suffix.casefold() == ".eml":
        return "email"
    return "other"


def folder_tag_hints(source_type: str) -> list[str]:
    mapping = {
        "policy": ["policy"],
        "procedure": ["procedure", "instructions"],
        "contract": ["contract", "client_profile"],
        "meeting": ["meeting_notes"],
        "teams": ["meeting_notes"],
        "adversarial": ["adversarial"],
        "email": [],
    }
    return list(mapping.get(source_type, []))


def infer_tags(
    text: str,
    extracted_topics: list[str],
    extracted_contacts: list[str],
    source_type: str,
) -> list[str]:
    tags: list[str] = folder_tag_hints(source_type)
    for tag, patterns in TAG_PATTERNS.items():
        if any(re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE) for pattern in patterns):
            if tag not in tags:
                tags.append(tag)

    topic_map = {
        "payroll": "payroll",
        "HR": "hr_process",
        "absence": "hr_process",
        "time & attendance": "hr_process",
        "workforce management": "hr_process",
        "social secretariat": "payroll",
        "self-service": "hr_process",
        "onboarding": "hr_process",
        "benefits": "hr_process",
    }
    for topic in extracted_topics:
        mapped = topic_map.get(topic)
        if mapped and mapped not in tags:
            tags.append(mapped)

    if extracted_contacts and "contacts" not in tags:
        tags.append("contacts")

    if not tags:
        tags.append("other")

    allowed = {tag: index for index, tag in enumerate(ALLOWED_TAGS)}
    return sorted(set(tags), key=lambda t: allowed.get(t, len(ALLOWED_TAGS)))


def build_summary(text: str, tags: list[str], client_name: str | None) -> str:
    content = strip_frontmatter(text)
    first_line = next(
        (
            line.strip()
            for line in content.splitlines()
            if line.strip() and not line.strip().startswith("---")
        ),
        "",
    )
    subject = ""
    subject_match = re.search(r"^Subject:\s*(.+)$", text, flags=re.IGNORECASE | re.MULTILINE)
    if subject_match:
        subject = subject_match.group(1).strip()
    heading = ""
    heading_match = re.search(r"^#\s+(.+)$", content, flags=re.MULTILINE)
    if heading_match:
        heading = heading_match.group(1).strip()

    lead = subject or heading or first_line
    if len(lead) > 160:
        lead = lead[:157] + "..."

    parts: list[str] = []
    if client_name:
        parts.append(f"Client: {client_name}.")
    if tags:
        parts.append(f"Tags: {', '.join(tags)}.")
    if lead:
        parts.append(lead)
    return " ".join(parts)


def normalize_client_name(name: str) -> str | None:
    """Strip sentence crumbs and keep a clean company label."""
    text = re.sub(r"[ \t]+", " ", name).strip(" ,.;:-")
    if not text:
        return None
    if text.casefold() in VENDOR_BLOCKLIST:
        return None

    # Map known aliases onto canonical names
    lowered = text.casefold()
    for canonical, aliases, _industry in KNOWN_CLIENTS:
        if lowered == canonical.casefold() or any(alias in lowered for alias in aliases):
            return canonical

    suffix_alt = "|".join(LEGAL_SUFFIXES)
    matches = list(
        re.finditer(
            rf"\b([A-Z][\w&.'-]*(?:[ \t]+[A-Z][\w&.'-]*){{0,4}}[ \t]+(?i:{suffix_alt}))\b",
            text,
        )
    )
    if matches:
        raw = max((m.group(1) for m in matches), key=len)
        cleaned = " ".join(raw.split())
        if cleaned.casefold() in VENDOR_BLOCKLIST:
            return None
        return cleaned

    words = text.split()
    fillers = {
        "our",
        "call",
        "with",
        "about",
        "the",
        "for",
        "from",
        "notes",
        "during",
        "annotated",
        "captured",
        "quick",
        "email",
        "following",
        "this",
        "page",
        "mixes",
        "example",
        "customers",
    }
    while words and (words[0].casefold() in fillers or words[0][:1].islower()):
        words.pop(0)
    if len(words) < 2:
        return None
    cleaned = " ".join(words)
    if cleaned.casefold() in VENDOR_BLOCKLIST:
        return None
    if not any(token.casefold() in {s.casefold() for s in LEGAL_SUFFIXES} for token in words):
        # Without a legal suffix, only keep known-client aliases already mapped above
        return None
    return cleaned


def find_known_clients_in_text(text: str) -> list[str]:
    lowered = text.casefold()
    found: list[str] = []
    for canonical, aliases, _industry in KNOWN_CLIENTS:
        needles = (canonical.casefold(), *aliases)
        if any(needle in lowered for needle in needles):
            found.append(canonical)
    return found


def pick_primary_client(candidates: list[str], body: str = "") -> str | None:
    cleaned = [c for c in (normalize_client_name(name) for name in candidates) if c]
    cleaned.extend(find_known_clients_in_text(body))
    # Only keep real company names — drop policy titles / people / channel names
    cleaned = [c for c in cleaned if looks_like_company(c) and c.casefold() not in VENDOR_BLOCKLIST]
    if not cleaned:
        return None

    unique: list[str] = []
    seen: set[str] = set()
    for name in cleaned:
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        unique.append(name)

    lowered = body.casefold()

    def score(name: str) -> tuple:
        has_suffix = any(
            token.casefold() in {s.casefold() for s in LEGAL_SUFFIXES} for token in name.split()
        )
        mentions = lowered.count(name.casefold())
        first = name.split()[0].casefold()
        if len(first) >= 4:
            mentions += lowered.count(first)
        if any(name == canonical for canonical, *_ in KNOWN_CLIENTS):
            mentions += 5
        first_pos = lowered.find(name.casefold())
        if first_pos < 0:
            first_pos = lowered.find(first) if len(first) >= 4 else 10_000
        return (mentions, has_suffix, -first_pos if first_pos >= 0 else -10_000, len(name))

    return max(unique, key=score)


def path_client_hint(path: Path, data_dir: Path) -> str | None:
    """Hint from filename when it embeds a company name (e.g. Janssens_Logistics_NV_...)."""
    stem = path.stem.replace("-", " ").replace("_", " ")
    known = find_known_clients_in_text(stem)
    if known:
        return known[0]
    normalized = normalize_client_name(stem)
    return normalized


def industry_for_client(client_name: str | None, extracted_industry: list[str]) -> str | None:
    if client_name:
        for canonical, _aliases, industry in KNOWN_CLIENTS:
            if canonical.casefold() == client_name.casefold():
                return industry
    return extracted_industry[0] if extracted_industry else None


def classify_file(path: Path, data_dir: Path) -> dict:
    body = read_file_text(path)
    meta = parse_metadata(body)
    extracted = extract_keywords(body)
    source_type = detect_source_type(path, data_dir)

    client_candidates = list(extracted.client)
    for key in ("customer", "client", "client_name", "account"):
        if key in meta and meta[key]:
            client_candidates.insert(0, meta[key])

    hint = path_client_hint(path, data_dir)
    if hint:
        client_candidates.append(hint)

    client_name = pick_primary_client(client_candidates, body)
    tags = infer_tags(body, extracted.topics, extracted.contacts, source_type)
    summary = build_summary(body, tags, client_name)
    industry = industry_for_client(client_name, extracted.industry)

    entities = {
        "people": extracted.contacts,
        "locations": extracted.location,
        "topics": extracted.topics,
        "client_candidates": _unique(
            [c for c in (normalize_client_name(x) for x in extracted.client) if c]
            + find_known_clients_in_text(body)
        ),
        "employee_count": extracted.employee_count,
        "industry": industry,
        "metadata": meta,
    }

    title = path.stem.replace("_", " ")
    subject_match = re.search(r"^Subject:\s*(.+)$", body, flags=re.IGNORECASE | re.MULTILINE)
    heading_match = re.search(r"^#\s+(.+)$", strip_frontmatter(body), flags=re.MULTILINE)
    if subject_match:
        title = subject_match.group(1).strip()
    elif heading_match:
        title = heading_match.group(1).strip()
    elif source_type in {"teams", "meeting"} and body:
        title = next((line.strip() for line in body.splitlines() if line.strip()), title)

    aliases = [
        c
        for c in (normalize_client_name(name) for name in [*extracted.client, *meta.values()])
        if c
        and client_name
        and c.casefold() != client_name.casefold()
        and (
            c.casefold() in client_name.casefold()
            or client_name.casefold().startswith(c.split()[0].casefold())
        )
    ]

    return {
        "source_type": source_type,
        "client_name": client_name,
        "industry": industry,
        "tags": tags,
        "summary": summary,
        "entities": entities,
        "title": title,
        "body": body,
        "aliases": aliases,
    }


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(value)
    return out


def iter_source_files(data_dir: Path) -> list[Path]:
    files: list[Path] = []
    for path in sorted(data_dir.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.casefold() not in SUPPORTED_SUFFIXES:
            continue
        if path.name.casefold() in SKIP_FILE_NAMES:
            continue
        if any(part.casefold() in SKIP_DIR_NAMES for part in path.parts):
            continue
        files.append(path)
    return files


def run_classification(*, data_dir: Path, db_path: Path) -> dict[str, int]:
    data_dir = data_dir.resolve()
    if not data_dir.is_dir():
        raise FileNotFoundError(f"Data directory not found: {data_dir}")

    files = iter_source_files(data_dir)
    conn = connect(db_path)
    init_db(conn)

    # Seed known fictional customers so partial matches resolve cleanly
    for canonical, aliases, industry in KNOWN_CLIENTS:
        upsert_client(conn, canonical, aliases=list(aliases), industry=industry)

    stats = {"processed": 0, "resolved": 0, "unresolved": 0}
    now = datetime.now(timezone.utc).isoformat()

    for path in files:
        result = classify_file(path, data_dir)
        client_id = None
        client_status = "unresolved"

        if result["client_name"]:
            client = upsert_client(
                conn,
                result["client_name"],
                aliases=result["aliases"],
                industry=result["industry"],
            )
            client_id = client["id"]
            client_status = "resolved"
            stats["resolved"] += 1
        else:
            stats["unresolved"] += 1

        try:
            stored_path = str(path.resolve().relative_to(PROJECT_ROOT.resolve()))
        except ValueError:
            stored_path = str(path)

        upsert_document(
            conn,
            file_path=stored_path,
            source_type=result["source_type"],
            title=result["title"],
            body=result["body"],
            summary=result["summary"],
            tags=result["tags"],
            entities=result["entities"],
            classified_at=now,
            client_id=client_id,
            client_status=client_status,
        )
        stats["processed"] += 1
        print(
            f"{stored_path}: source={result['source_type']} "
            f"client={result['client_name'] or 'UNRESOLVED'} tags={result['tags']}"
        )

    conn.close()
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Classify scrambled knowledge-base files (default: testset/) into SQLite."
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help="Folder containing source files (default: ../testset)",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB_PATH,
        help="SQLite database path",
    )
    args = parser.parse_args()
    stats = run_classification(data_dir=args.data_dir, db_path=args.db)
    print(
        f"Done. processed={stats['processed']} "
        f"resolved={stats['resolved']} unresolved={stats['unresolved']}"
    )


if __name__ == "__main__":
    main()
