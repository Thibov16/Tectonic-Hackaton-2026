"""FastAPI entrypoint for trust-aware knowledge search."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_config
from app.ingest import store
from app.ingest.loader import ingest
from app.pipeline import SearchService, get_service
from app.schemas import DocumentDetail, SearchRequest, SearchResponse

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"

app = FastAPI(title="SD Worx Trust-Aware Knowledge Search")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup() -> None:
    svc = get_service()
    try:
        svc.ensure_ready()
    except Exception:
        logger.exception("Startup ingest/index failed")


@app.get("/health")
@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/search", response_model=SearchResponse)
def api_search(payload: SearchRequest) -> SearchResponse:
    details = payload.details.strip()
    if not details:
        raise HTTPException(status_code=400, detail="details must be non-empty")
    if len(details) > 4000:
        raise HTTPException(status_code=400, detail="details max length is 4000 characters")
    svc = get_service()
    try:
        result = svc.search(details, today=payload.today)
    except Exception as exc:  # noqa: BLE001
        logger.exception("search failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return SearchResponse(**result)


@app.get("/api/documents/{doc_id}", response_model=DocumentDetail)
def api_document(doc_id: int) -> DocumentDetail:
    config = get_config()
    conn = store.connect(config.db_path)
    try:
        doc = store.get_document(conn, doc_id)
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")
        claims = store.list_claims(conn, doc_id)
        links = store.list_links(conn, doc_id)
        meta = doc.get("metadata_json")
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except json.JSONDecodeError:
                meta = {}
        return DocumentDetail(
            id=doc["id"],
            path=doc["path"],
            title=doc["title"],
            doc_type=doc["doc_type"],
            doc_id=doc.get("doc_id"),
            version=doc.get("version"),
            status=doc.get("status"),
            country=doc.get("country"),
            customer=doc.get("customer"),
            effective_date=doc.get("effective_date"),
            raw_text=doc.get("raw_text") or "",
            metadata=meta or {},
            claims=claims,
            links=links,
            retracted=bool(doc.get("retracted")),
        )
    finally:
        conn.close()


@app.post("/api/ingest/rebuild")
def api_rebuild() -> dict:
    config = get_config()
    stats = ingest(config, rebuild=True)
    svc = get_service(config)
    svc.ensure_ready(rebuild=False)
    svc.index.build(store.connect(config.db_path))
    return {"status": "ok", "stats": stats}


# --- Compatibility: keep old client-insight portal working via adapter ---
@app.post("/api/client-insight")
def client_insight_adapter(payload: SearchRequest) -> dict:
    """Map trust search into the legacy portal shape + trust fields."""
    result = get_service().search(payload.details, today=payload.today)
    sources = result.get("sources") or []
    documents = []
    for s in sources:
        documents.append(
            {
                "id": s["id"],
                "client_id": None,
                "client_name": s.get("customer") or result["query_context"].get("customer"),
                "client_status": "resolved" if s.get("customer") else "unresolved",
                "source_type": s.get("doc_type"),
                "file_path": s.get("path"),
                "title": s.get("title"),
                "summary": s.get("evidence") or "",
                "tags": [s.get("role"), s.get("status")] if s.get("status") else [s.get("role")],
                "entities": {},
                "classified_at": s.get("date") or "",
                "body": s.get("evidence"),
                "score": int(round((s.get("trust") or 0) * 100)),
                "excerpts": [s.get("evidence")] if s.get("evidence") else [],
                "relevance": "top" if s.get("role") == "primary" else "other",
                "source_date": s.get("date"),
                "doc_status": s.get("status"),
                "age_days": None,
                "age_label": None,
                "trust": s.get("trust"),
                "role": s.get("role"),
                "signals": s.get("signals"),
                "reasons": s.get("reasons"),
            }
        )
    customer = result["query_context"].get("customer") or "General knowledge"
    return {
        "client_name": customer,
        "industry": result["query_context"].get("country") or "HR / Payroll",
        "headline": result.get("headline") or "",
        "summary": result.get("answer") or "",
        "highlights": [f.get("message") for f in result.get("flags") or []][:8],
        "contacts": [e["name"] for e in result.get("experts") or []],
        "next_steps": result.get("gaps") or [],
        "extracted_keywords": {
            "client": [customer] if customer != "General knowledge" else [],
            "location": [],
            "timeframe": [],
            "industry": [],
            "employee_count": None,
            "topics": [result["query_context"].get("topic") or ""],
            "contacts": [],
            "other": [],
            "all": [],
        },
        "documents": documents,
        "top_documents": documents[:5],
        "other_documents": documents[5:],
        "client_profile": {
            "name": customer,
            "industry": result["query_context"].get("country"),
            "people": [e["name"] for e in result.get("experts") or []],
            "emails": [],
            "phones": [],
            "locations": [],
        },
        # Trust payload for the upgraded UI
        "verdict": result.get("verdict"),
        "confidence": result.get("confidence"),
        "query_context": result.get("query_context"),
        "answer": result.get("answer"),
        "sources": result.get("sources"),
        "flags": result.get("flags"),
        "gaps": result.get("gaps"),
        "experts": result.get("experts"),
        "meta": result.get("meta"),
    }


@app.get("/")
def root() -> RedirectResponse:
    return RedirectResponse(url="/app/")


@app.get("/clients")
def clients_page() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "clients.html")


if FRONTEND_DIR.is_dir():
    app.mount("/app", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="app")
