from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from keywords import ExtractedKeywords, extract_keywords

app = FastAPI(title="SD Worx Client Insight API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class ClientQuery(BaseModel):
    details: str = Field(min_length=1, description="Everything the user knows about the client")


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


class ClientSummary(BaseModel):
    client_name: str
    industry: str
    headline: str
    summary: str
    highlights: list[str]
    contacts: list[str]
    next_steps: list[str]
    extracted_keywords: ExtractedKeywordsOut


SAMPLE_CLIENTS = [
    {
        "keywords": ["acme", "manufacturing", "antwerp"],
        "client_name": "Acme Manufacturing NV",
        "industry": "Manufacturing",
        "headline": "Mid-size Belgian manufacturer with mixed payroll and benefits needs.",
        "summary": (
            "Acme Manufacturing NV operates plants in Antwerp and Ghent. "
            "They currently run payroll for about 420 employees and are reviewing "
            "a move to a single HR and payroll platform."
        ),
        "highlights": [
            "420 employees across two Belgian sites",
            "Current mix of Excel, local payroll, and a legacy HRIS",
            "Priority topics: payroll accuracy, absence, and social secretariat",
        ],
        "contacts": ["HR Director (known from briefing)", "Payroll lead at the Antwerp site"],
        "next_steps": [
            "Confirm employee count and legal entities",
            "Map current payroll calendar and closing dates",
            "Prepare a first-fit SD Worx payroll & HR offering",
        ],
    },
    {
        "keywords": ["northwind", "retail", "brussels"],
        "client_name": "Northwind Retail Group",
        "industry": "Retail",
        "headline": "Multi-brand retailer looking for consistent HR processes across stores.",
        "summary": (
            "Northwind Retail Group manages several store brands in Brussels and Wallonia. "
            "High part-time volume and seasonal hiring make workforce planning a pain point."
        ),
        "highlights": [
            "Store network with high part-time and student contracts",
            "Need for time & attendance plus payroll in one flow",
            "Interest in self-service for store managers",
        ],
        "contacts": ["Regional HR manager", "Store operations lead"],
        "next_steps": [
            "Collect contract types and peak-season volumes",
            "Review current time-tracking tools",
            "Share an SD Worx workforce management walkthrough",
        ],
    },
]


FALLBACK = {
    "client_name": "Unmatched client",
    "industry": "To be confirmed",
    "headline": "We captured your notes. Connect a real data source to enrich this brief.",
    "summary": (
        "No sample client matched the details you entered. "
        "This starter API echoes your input into a structured brief so the portal "
        "can already show a summary view."
    ),
    "highlights": [
        "Replace SAMPLE_CLIENTS with CRM, knowledge base, or LLM retrieval",
        "Keep the same response shape so the frontend does not need to change",
    ],
    "contacts": ["Not yet identified"],
    "next_steps": [
        "Clarify company name, sector, and country",
        "Add employee count and current HR/payroll setup",
    ],
}


def _keywords_out(extracted: ExtractedKeywords) -> ExtractedKeywordsOut:
    payload = extracted.to_dict()
    payload["all"] = extracted.all_terms()
    return ExtractedKeywordsOut(**payload)


def _name_tokens(name: str) -> list[str]:
    return [part for part in name.replace("-", " ").split() if len(part) > 1]


def _match_client(details: str, extracted: ExtractedKeywords) -> dict | None:
    """Score sample clients using extracted terms, then fall back to raw substring match."""
    search_terms = {term.casefold() for term in extracted.all_terms()}
    # Also include individual tokens from client names for partial hits ("Acme")
    for name in extracted.client:
        search_terms.update(part.casefold() for part in _name_tokens(name))

    lowered = details.casefold()
    best: dict | None = None
    best_score = 0
    for client in SAMPLE_CLIENTS:
        score = sum(1 for keyword in client["keywords"] if keyword in search_terms)
        # Raw details catch aliases the extractor might miss
        score += sum(1 for keyword in client["keywords"] if keyword in lowered)
        if score > best_score:
            best_score = score
            best = client

    return best if best_score > 0 else None


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/client-insight", response_model=ClientSummary)
def client_insight(payload: ClientQuery) -> ClientSummary:
    details = payload.details.strip()
    if not details:
        raise HTTPException(status_code=400, detail="Client details are required.")

    extracted = extract_keywords(details)
    keywords_out = _keywords_out(extracted)
    match = _match_client(details, extracted)

    if match:
        data = {key: value for key, value in match.items() if key != "keywords"}
        data["extracted_keywords"] = keywords_out
        return ClientSummary(**data)

    fallback = FALLBACK.copy()
    # Prefer an extracted company name over the generic unmatched label
    if extracted.client:
        fallback["client_name"] = extracted.client[0]
    if extracted.industry:
        fallback["industry"] = extracted.industry[0]
    fallback["summary"] = f"{FALLBACK['summary']}\n\nYour notes:\n{details}"
    fallback["extracted_keywords"] = keywords_out
    return ClientSummary(**fallback)


@app.post("/api/extract-keywords", response_model=ExtractedKeywordsOut)
def extract_keywords_endpoint(payload: ClientQuery) -> ExtractedKeywordsOut:
    """Standalone keyword extraction for the free-text client request."""
    details = payload.details.strip()
    if not details:
        raise HTTPException(status_code=400, detail="Client details are required.")
    return _keywords_out(extract_keywords(details))
