from pathlib import Path

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


class ClientOut(BaseModel):
    id: int
    name: str
    aliases: list[str] = Field(default_factory=list)
    industry: str | None = None


class ClientSummary(BaseModel):
    client_name: str
    industry: str
    headline: str
    summary: str
    highlights: list[str]
    contacts: list[str]
    next_steps: list[str]
    extracted_keywords: ExtractedKeywordsOut
    documents: list[DocumentOut] = Field(default_factory=list)


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


def _build_insight_from_documents(
    client: dict,
    documents: list[dict],
    extracted: ExtractedKeywords,
) -> ClientSummary:
    industry = client.get("industry") or (extracted.industry[0] if extracted.industry else "To be confirmed")

    summaries = [doc["summary"] for doc in documents if doc.get("summary")]
    bodies_preview = []
    for doc in documents[:3]:
        bodies_preview.append(f"[{doc['source_type']}] {doc['title']}: {doc['summary']}")

    summary = (
        f"{client['name']} has {len(documents)} classified source document(s) "
        f"across {len({doc['source_type'] for doc in documents})} source type(s)."
    )
    if summaries:
        summary = f"{summary}\n\n" + "\n".join(bodies_preview)

    highlights: list[str] = []
    for doc in documents:
        tag_str = ", ".join(doc.get("tags") or []) or "untagged"
        highlights.append(f"{doc['source_type']}: {doc['title']} ({tag_str})")
        entities = doc.get("entities") or {}
        if entities.get("employee_count"):
            highlights.append(f"Headcount signal: {entities['employee_count']}")
        for location in entities.get("locations") or []:
            highlights.append(f"Location: {location}")
    highlights = _unique_keep_order(highlights)[:8]
    if not highlights:
        highlights = ["No highlights extracted yet — re-run classify.py after adding files."]

    contacts: list[str] = []
    for doc in documents:
        contacts.extend((doc.get("entities") or {}).get("people") or [])
    contacts = _unique_keep_order(contacts)
    if not contacts:
        contacts = ["Not yet identified in classified sources"]

    next_steps: list[str] = []
    for doc in documents:
        if "next_steps" in (doc.get("tags") or []):
            next_steps.append(doc["summary"] or doc["title"])
        # Pull numbered lines that look like actions when tagged
        if "instructions" in (doc.get("tags") or []) and "next_steps" in (doc.get("tags") or []):
            for line in (doc.get("body") or "").splitlines():
                stripped = line.strip()
                if stripped.lower().startswith(("next step", "action")):
                    next_steps.append(stripped)
    next_steps = _unique_keep_order(next_steps)[:6]
    if not next_steps:
        next_steps = [
            "Review classified documents for this client",
            "Confirm legal entities and key contacts",
        ]

    source_types = sorted({doc["source_type"] for doc in documents})
    headline = (
        f"Knowledge base brief from {', '.join(source_types)} sources."
        if source_types
        else "Client matched, but no classified documents yet."
    )

    doc_models = [
        DocumentOut(
            id=doc["id"],
            client_id=doc.get("client_id"),
            client_name=doc.get("client_name") or client["name"],
            client_status=doc.get("client_status", "resolved"),
            source_type=doc["source_type"],
            file_path=doc["file_path"],
            title=doc["title"],
            summary=doc["summary"],
            tags=doc.get("tags") or [],
            entities=doc.get("entities") or {},
            classified_at=doc["classified_at"],
            body=None,
        )
        for doc in documents
    ]

    return ClientSummary(
        client_name=client["name"],
        industry=industry,
        headline=headline,
        summary=summary,
        highlights=highlights,
        contacts=contacts,
        next_steps=next_steps,
        extracted_keywords=_keywords_out(extracted),
        documents=doc_models,
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
    keywords_out = _keywords_out(extracted)
    match = _match_client_from_query(details, extracted)

    if match:
        conn = _open_db()
        try:
            documents = list_documents_for_client(conn, match["id"], include_body=True)
        finally:
            conn.close()
        return _build_insight_from_documents(match, documents, extracted)

    # Unmatched: echo notes so the UI still works before classify.py has run / matched
    name = extracted.client[0] if extracted.client else "Unmatched client"
    industry = extracted.industry[0] if extracted.industry else "To be confirmed"
    return ClientSummary(
        client_name=name,
        industry=industry,
        headline="No classified documents matched yet.",
        summary=(
            "No client in the knowledge database matched your notes. "
            "Drop text files into data/, run `python classify.py`, then retry.\n\n"
            f"Your notes:\n{details}"
        ),
        highlights=[
            "Run backend/classify.py against the data/ folder",
            "Search again with a known company name from the corpus",
        ],
        contacts=extracted.contacts or ["Not yet identified"],
        next_steps=[
            "Add or update scrambled .txt sources under data/",
            "Re-run classification and query again",
        ],
        extracted_keywords=keywords_out,
        documents=[],
    )


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
