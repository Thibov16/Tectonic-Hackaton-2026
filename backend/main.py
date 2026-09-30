from pathlib import Path
import re
from datetime import date

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from db import (
    connect,
    find_client_by_name,
    get_client,
    init_db,
    list_clients,
    list_documents_for_client,
    list_unresolved_documents,
    search_documents,
    search_documents_by_terms,
)
from keywords import ExtractedKeywords, extract_keywords

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

app = FastAPI(title="SD Worx Client Insight API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class ClientQuery(BaseModel):
    details: str = Field(min_length=1, description="Everything the user knows about the client")


class SearchQuery(BaseModel):
    query: str = Field(default="", description="Free-text search over titles, summaries, bodies, tags")
    client: str | None = Field(default=None, description="Optional client name filter")
    tag: str | None = Field(default=None, description="Optional content tag filter")


class ExtractedKeywordsOut(BaseModel):
    """Meaningful terms pulled from the user's free-text request."""

    client: list[str] = Field(default_factory=list, description="Company / account names")
    location: list[str] = Field(default_factory=list, description="Cities, regions, countries")
    timeframe: list[str] = Field(default_factory=list, description="Dates, quarters, relative periods")
    industry: list[str] = Field(default_factory=list, description="Sector / industry signals")
    employee_count: str | None = Field(default=None, description="Headcount if mentioned")
    topics: list[str] = Field(default_factory=list, description="HR/payroll themes of interest")
    contacts: list[str] = Field(default_factory=list, description="Roles or stakeholders mentioned")
    other: list[str] = Field(default_factory=list, description="Other useful leftover terms")
    all: list[str] = Field(default_factory=list, description="Flat deduplicated keyword list")


class DocumentOut(BaseModel):
    id: int
    client_id: int | None = None
    client_name: str | None = None
    client_status: str
    source_type: str
    file_path: str
    title: str
    summary: str
    tags: list[str] = Field(default_factory=list)
    entities: dict = Field(default_factory=dict)
    classified_at: str
    body: str | None = None
    score: int = 0
    excerpts: list[str] = Field(default_factory=list)
    relevance: str = "other"
    source_date: str | None = None
    doc_status: str | None = None
    age_days: int | None = None
    age_label: str | None = None


class ClientOut(BaseModel):
    id: int
    name: str
    aliases: list[str] = Field(default_factory=list)
    industry: str | None = None


class ClientProfileOut(BaseModel):
    name: str
    industry: str | None = None
    people: list[str] = Field(default_factory=list)
    emails: list[str] = Field(default_factory=list)
    phones: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)


class ClientSummary(BaseModel):
    client_name: str
    industry: str
    headline: str = ""
    summary: str = ""
    highlights: list[str] = Field(default_factory=list)
    contacts: list[str] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)
    extracted_keywords: ExtractedKeywordsOut
    client_profile: ClientProfileOut | None = None
    documents: list[DocumentOut] = Field(default_factory=list)
    top_documents: list[DocumentOut] = Field(default_factory=list)
    other_documents: list[DocumentOut] = Field(default_factory=list)


def _keywords_out(extracted: ExtractedKeywords) -> ExtractedKeywordsOut:
    payload = extracted.to_dict()
    payload["all"] = extracted.all_terms()
    return ExtractedKeywordsOut(**payload)


def _name_tokens(name: str) -> list[str]:
    return [part for part in name.replace("-", " ").split() if len(part) > 1]


def _open_db():
    conn = connect()
    init_db(conn)
    return conn


def _match_client_from_query(details: str, extracted: ExtractedKeywords) -> dict | None:
    """Resolve a client row from free-text using DB names/aliases, then keyword fallback."""
    conn = _open_db()
    try:
        # Strongest: explicit extracted company names
        for candidate in extracted.client:
            match = find_client_by_name(conn, candidate)
            if match:
                return match

        # Score all clients against query terms + raw text
        search_terms = {term.casefold() for term in extracted.all_terms()}
        for name in extracted.client:
            search_terms.update(part.casefold() for part in _name_tokens(name))
        lowered = details.casefold()

        best: dict | None = None
        best_score = 0
        for client in list_clients(conn):
            score = 0
            names = [client["name"], *(client.get("aliases") or [])]
            for label in names:
                label_cf = label.casefold()
                if label_cf in lowered:
                    score += 3
                for token in _name_tokens(label):
                    token_cf = token.casefold()
                    if token_cf in search_terms or token_cf in lowered:
                        score += 1
            if score > best_score:
                best_score = score
                best = client
        return best if best_score > 0 else None
    finally:
        conn.close()


def _unique_keep_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        key = value.casefold().strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(value.strip())
    return out


TOPIC_TO_TAGS = {
    "payroll": ("payroll",),
    "HR": ("hr_process", "contacts"),
    "absence": ("hr_process",),
    "time & attendance": ("hr_process",),
    "workforce management": ("hr_process",),
    "social secretariat": ("payroll",),
    "self-service": ("hr_process",),
    "onboarding": ("hr_process", "procedure"),
    "benefits": ("hr_process", "payroll"),
}


def _search_terms(extracted: ExtractedKeywords) -> list[str]:
    """Build retrieval terms from extracted keywords + related document tags."""
    terms: list[str] = []
    terms.extend(extracted.client)
    terms.extend(extracted.topics)
    terms.extend(extracted.location)
    terms.extend(extracted.contacts)
    terms.extend(extracted.industry)
    terms.extend(extracted.timeframe)
    if extracted.employee_count:
        terms.append(extracted.employee_count)
    # Keep a few leftover content words for free-text search
    terms.extend(extracted.other[:8])
    for topic in extracted.topics:
        terms.extend(TOPIC_TO_TAGS.get(topic, ()))
    # Common query phrases from topics
    for topic in extracted.topics:
        if topic.casefold() == "payroll":
            terms.extend(["deadline", "input", "allowance"])
        if "absence" in topic.casefold() or "leave" in topic.casefold():
            terms.extend(["sick leave", "absence"])
    return _unique_keep_order([t for t in terms if len(t.strip()) >= 2])


# Fictional "today" used by the SD Worx test set (see testset/README.md)
REFERENCE_TODAY = date(2026, 9, 30)


def _parse_source_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _age_days(source_date: str | None) -> int | None:
    parsed = _parse_source_date(source_date)
    if not parsed:
        return None
    return max(0, (REFERENCE_TODAY - parsed).days)


def _age_label(source_date: str | None) -> str | None:
    days = _age_days(source_date)
    if days is None:
        return None
    if days == 0:
        return "today"
    if days == 1:
        return "1 day ago"
    if days < 30:
        return f"{days} days ago"
    months = days // 30
    if months == 1:
        return "1 month ago"
    if months < 12:
        return f"{months} months ago"
    years = days // 365
    if years <= 1:
        return "about 1 year ago"
    return f"about {years} years ago"


def _recency_boost(source_date: str | None) -> int:
    days = _age_days(source_date)
    if days is None:
        return 0
    if days <= 30:
        return 12
    if days <= 90:
        return 8
    if days <= 180:
        return 5
    if days <= 365:
        return 2
    return -2


def _status_score_delta(doc_status: str | None) -> int:
    if not doc_status:
        return 0
    status = doc_status.upper()
    if status in {"CURRENT", "SIGNED"}:
        return 3
    if status in {"DRAFT", "SUPERSEDED", "UNSIGNED"}:
        return -6
    return 0


def _score_document(doc: dict, terms: list[str], preferred_client_id: int | None) -> int:
    blob = " ".join(
        [
            doc.get("title") or "",
            doc.get("summary") or "",
            doc.get("body") or "",
            " ".join(doc.get("tags") or []),
            doc.get("client_name") or "",
        ]
    ).casefold()
    score = 0
    for term in terms:
        needle = term.casefold()
        if len(needle) < 2:
            continue
        if needle in blob:
            score += 3
        if any(needle == tag.casefold() or needle in tag.casefold() for tag in (doc.get("tags") or [])):
            score += 2
    if preferred_client_id is not None and doc.get("client_id") == preferred_client_id:
        score += 8
    if doc.get("source_type") == "adversarial" and "adversarial" not in {t.casefold() for t in terms}:
        score -= 4
    score += _recency_boost(doc.get("source_date"))
    score += _status_score_delta(doc.get("doc_status"))
    return score


def _relevant_excerpts(doc: dict, terms: list[str], *, max_lines: int = 3) -> list[str]:
    body = doc.get("body") or ""
    if not body:
        return [doc.get("summary") or doc.get("title") or ""]
    needles = [t.casefold() for t in terms if len(t) >= 3]
    hits: list[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        if len(stripped) < 8:
            continue
        lowered = stripped.casefold()
        if any(n in lowered for n in needles):
            hits.append(stripped[:220])
        if len(hits) >= max_lines:
            break
    if hits:
        return hits
    summary = (doc.get("summary") or "").strip()
    return [summary[:220]] if summary else [doc.get("title") or ""]


def _collect_relevant_documents(
    extracted: ExtractedKeywords,
    client: dict | None,
) -> list[dict]:
    terms = _search_terms(extracted)
    client_id = client["id"] if client else None
    conn = _open_db()
    try:
        docs = search_documents_by_terms(
            conn,
            terms,
            client_id=client_id,
            include_body=True,
            limit=50,
        )
        # Always include direct client docs even when keyword list is thin
        if client_id is not None:
            for doc in list_documents_for_client(conn, client_id, include_body=True):
                if all(existing["id"] != doc["id"] for existing in docs):
                    docs.append(doc)
    finally:
        conn.close()

    ranked = sorted(
        docs,
        key=lambda doc: (
            _score_document(doc, terms, client_id),
            doc.get("source_date") or "",
        ),
        reverse=True,
    )
    # Drop zero-score noise unless they belong to the matched client
    filtered = [
        doc
        for doc in ranked
        if _score_document(doc, terms, client_id) > 0
        or (client_id is not None and doc.get("client_id") == client_id)
    ]
    return filtered[:20]


TOP_RESULT_COUNT = 3
TOP_SCORE_FLOOR = 8


def _document_out(
    doc: dict,
    *,
    terms: list[str],
    preferred_client_id: int | None,
    relevance: str,
    include_body_preview: bool = False,
) -> DocumentOut:
    score = _score_document(doc, terms, preferred_client_id)
    excerpts = _relevant_excerpts(doc, terms, max_lines=4 if relevance == "top" else 2)
    body_preview = None
    if include_body_preview and doc.get("body"):
        body_preview = "\n".join(excerpts) if excerpts else (doc.get("summary") or "")[:600]
        needles = [t.casefold() for t in terms if len(t) >= 3]
        lines = [line.rstrip() for line in (doc.get("body") or "").splitlines()]
        hit_idx = next(
            (
                i
                for i, line in enumerate(lines)
                if any(n in line.casefold() for n in needles) and len(line.strip()) >= 8
            ),
            None,
        )
        if hit_idx is not None:
            start = max(0, hit_idx - 1)
            end = min(len(lines), hit_idx + 6)
            chunk = "\n".join(line for line in lines[start:end] if line.strip())
            if chunk:
                body_preview = chunk[:900]

    return DocumentOut(
        id=doc["id"],
        client_id=doc.get("client_id"),
        client_name=doc.get("client_name"),
        client_status=doc.get("client_status", "resolved"),
        source_type=doc["source_type"],
        file_path=doc["file_path"],
        title=doc["title"],
        summary=doc["summary"],
        tags=doc.get("tags") or [],
        entities=doc.get("entities") or {},
        classified_at=doc["classified_at"],
        body=body_preview,
        score=score,
        excerpts=excerpts,
        relevance=relevance,
        source_date=doc.get("source_date"),
        doc_status=doc.get("doc_status"),
        age_days=_age_days(doc.get("source_date")),
        age_label=_age_label(doc.get("source_date")),
    )


def _split_top_and_other(
    documents: list[dict],
    terms: list[str],
    preferred_client_id: int | None,
) -> tuple[list[DocumentOut], list[DocumentOut]]:
    scored = [(doc, _score_document(doc, terms, preferred_client_id)) for doc in documents]
    scored.sort(
        key=lambda item: (item[1], item[0].get("source_date") or ""),
        reverse=True,
    )

    top_raw: list[dict] = []
    other_raw: list[dict] = []
    for index, (doc, score) in enumerate(scored):
        if len(top_raw) < TOP_RESULT_COUNT and (score >= TOP_SCORE_FLOOR or not top_raw):
            top_raw.append(doc)
        else:
            other_raw.append(doc)

    if not top_raw and scored:
        top_raw = [doc for doc, _score in scored[: min(TOP_RESULT_COUNT, len(scored))]]
        other_raw = [doc for doc, _score in scored[len(top_raw) :]]

    top_docs = [
        _document_out(
            doc,
            terms=terms,
            preferred_client_id=preferred_client_id,
            relevance="top",
            include_body_preview=True,
        )
        for doc in top_raw
    ]
    other_docs = [
        _document_out(
            doc,
            terms=terms,
            preferred_client_id=preferred_client_id,
            relevance="other",
            include_body_preview=False,
        )
        for doc in other_raw
    ]
    return top_docs, other_docs


def _extract_contact_details(documents: list[dict], extracted: ExtractedKeywords) -> dict[str, list[str]]:
    """Pull people, emails and phones from matched document bodies/entities."""
    people: list[str] = []
    emails: list[str] = []
    phones: list[str] = []
    locations: list[str] = []

    email_re = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
    phone_re = re.compile(
        r"(?:\+|00)\d{1,3}[\s./-]?\(?\d{1,4}\)?(?:[\s./-]?\d{2,4}){2,4}"
        r"|\b0\d{1,3}[\s./-]\d{2,4}(?:[\s./-]\d{2,4}){1,3}\b"
    )
    named_email_re = re.compile(
        r"(?:From|To|Cc)?\s*:?\s*([A-Z][A-Za-zÀ-öø-ÿ' .-]{1,40})\s*<([^>]+@[^>]+)>"
    )
    chat_name_re = re.compile(
        r"\b\d{1,2}:\d{2}\s+([A-Z][A-Za-zÀ-öø-ÿ]+(?:\s+[A-Z][A-Za-zÀ-öø-ÿ]*\.?)?)\s*:"
    )

    blocked_people = {
        "hr manager",
        "payroll lead",
        "hr director",
        "team lead",
        "payroll service desk",
        "payroll be all",
        "payroll be",
        "cfo",
        "coo",
        "chro",
    }

    for doc in documents:
        entities = doc.get("entities") or {}
        for person in entities.get("people") or []:
            if person.casefold() not in blocked_people:
                people.append(person)
        locations.extend(entities.get("locations") or [])
        text = "\n".join(
            [
                doc.get("title") or "",
                doc.get("summary") or "",
                doc.get("body") or "",
            ]
        )
        for match in named_email_re.finditer(text):
            name = match.group(1).strip(" -:")
            if name.casefold() not in blocked_people and (" " in name or "." in name):
                people.append(name)
            emails.append(match.group(2).strip())
        emails.extend(email_re.findall(text))
        for match in chat_name_re.finditer(text):
            people.append(match.group(1).strip())
        for match in phone_re.finditer(text):
            candidate = re.sub(r"\s+", " ", match.group(0)).strip(" .-")
            digits = re.sub(r"\D", "", candidate)
            if (
                9 <= len(digits) <= 15
                and not re.search(r"20\d{2}", candidate)
                and not set(digits) <= {"0"}
                and (candidate.startswith(("+", "00", "0")))
            ):
                phones.append(candidate)

    for person in extracted.contacts:
        if person.casefold() not in blocked_people:
            people.append(person)
    locations.extend(extracted.location)

    people = [
        p
        for p in _unique_keep_order(people)
        if len(p) > 2 and p.casefold() not in blocked_people and not p.casefold().endswith(" all")
    ]
    emails = _unique_keep_order(emails)
    non_internal = [e for e in emails if "sdworx" not in e.casefold()]
    emails = non_internal[:6] or emails[:3]
    phones = _unique_keep_order(phones)[:6]
    locations = _unique_keep_order(locations)[:6]
    return {
        "people": people[:8],
        "emails": emails,
        "phones": phones,
        "locations": locations,
    }


def _build_insight_from_documents(
    client: dict | None,
    documents: list[dict],
    extracted: ExtractedKeywords,
) -> ClientSummary:
    client_name = (
        client["name"]
        if client
        else (extracted.client[0] if extracted.client else "Knowledge search")
    )
    industry = (
        (client.get("industry") if client else None)
        or (extracted.industry[0] if extracted.industry else "To be confirmed")
    )
    terms = _search_terms(extracted)
    preferred_client_id = client["id"] if client else None
    top_documents, other_documents = _split_top_and_other(
        documents, terms, preferred_client_id
    )
    all_documents = [*top_documents, *other_documents]
    contact_details = _extract_contact_details(documents, extracted)
    profile = ClientProfileOut(
        name=client_name,
        industry=industry,
        people=contact_details["people"],
        emails=contact_details["emails"],
        phones=contact_details["phones"],
        locations=contact_details["locations"],
    )

    return ClientSummary(
        client_name=client_name,
        industry=industry,
        extracted_keywords=_keywords_out(extracted),
        client_profile=profile,
        contacts=contact_details["people"],
        documents=all_documents,
        top_documents=top_documents,
        other_documents=other_documents,
    )


@app.on_event("startup")
def _startup() -> None:
    conn = _open_db()
    conn.close()


@app.get("/")
def root() -> RedirectResponse:
    return RedirectResponse(url="/app/")


@app.get("/clients")
def clients_page() -> FileResponse:
    """Browser-friendly clients list (HTML). JSON API stays at /api/clients."""
    return FileResponse(FRONTEND_DIR / "clients.html")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/clients", response_model=list[ClientOut])
def api_list_clients() -> list[ClientOut]:
    conn = _open_db()
    try:
        return [ClientOut(**client) for client in list_clients(conn)]
    finally:
        conn.close()


@app.get("/api/clients/{client_id}/documents", response_model=list[DocumentOut])
def api_client_documents(
    client_id: int,
    tag: str | None = Query(default=None),
    include_body: bool = Query(default=False),
) -> list[DocumentOut]:
    conn = _open_db()
    try:
        client = get_client(conn, client_id)
        if not client:
            raise HTTPException(status_code=404, detail="Client not found.")
        docs = list_documents_for_client(conn, client_id, tag=tag, include_body=include_body)
        return [DocumentOut(**doc) for doc in docs]
    finally:
        conn.close()


@app.get("/api/documents/unresolved", response_model=list[DocumentOut])
def api_unresolved_documents(include_body: bool = Query(default=False)) -> list[DocumentOut]:
    conn = _open_db()
    try:
        docs = list_unresolved_documents(conn, include_body=include_body)
        return [DocumentOut(**doc) for doc in docs]
    finally:
        conn.close()


@app.post("/api/search", response_model=list[DocumentOut])
def api_search(payload: SearchQuery) -> list[DocumentOut]:
    conn = _open_db()
    try:
        docs = search_documents(
            conn,
            payload.query,
            client=payload.client,
            tag=payload.tag,
            include_body=False,
        )
        return [DocumentOut(**doc) for doc in docs]
    finally:
        conn.close()


@app.post("/api/client-insight", response_model=ClientSummary)
def client_insight(payload: ClientQuery) -> ClientSummary:
    details = payload.details.strip()
    if not details:
        raise HTTPException(status_code=400, detail="Client details are required.")

    extracted = extract_keywords(details)
    match = _match_client_from_query(details, extracted)
    documents = _collect_relevant_documents(extracted, match)
    return _build_insight_from_documents(match, documents, extracted)


@app.post("/api/extract-keywords", response_model=ExtractedKeywordsOut)
def extract_keywords_endpoint(payload: ClientQuery) -> ExtractedKeywordsOut:
    """Standalone keyword extraction for the free-text client request."""
    details = payload.details.strip()
    if not details:
        raise HTTPException(status_code=400, detail="Client details are required.")
    return _keywords_out(extract_keywords(details))


# Serve the portal UI after API routes so /api/* stays authoritative.
if FRONTEND_DIR.is_dir():
    app.mount("/app", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
