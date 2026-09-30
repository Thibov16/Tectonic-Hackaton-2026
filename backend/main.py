from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

app = FastAPI(title="SD Worx Client Insight API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class ClientQuery(BaseModel):
    details: str = Field(min_length=1, description="Everything the user knows about the client")


class ClientSummary(BaseModel):
    client_name: str
    industry: str
    headline: str
    summary: str
    highlights: list[str]
    contacts: list[str]
    next_steps: list[str]


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


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/client-insight", response_model=ClientSummary)
def client_insight(payload: ClientQuery) -> ClientSummary:
    details = payload.details.strip()
    if not details:
        raise HTTPException(status_code=400, detail="Client details are required.")

    lowered = details.lower()
    match = next(
        (client for client in SAMPLE_CLIENTS if any(word in lowered for word in client["keywords"])),
        None,
    )

    if match:
        data = {key: value for key, value in match.items() if key != "keywords"}
        return ClientSummary(**data)

    fallback = FALLBACK.copy()
    fallback["summary"] = f"{FALLBACK['summary']}\n\nYour notes:\n{details}"
    return ClientSummary(**fallback)
