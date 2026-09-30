"""Extract structured keywords from free-text client notes."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

# Canonical label -> match aliases (lowercase)
LOCATIONS: dict[str, tuple[str, ...]] = {
    "Antwerp": ("antwerp", "antwerpen"),
    "Brussels": ("brussels", "brussel", "bruxelles"),
    "Ghent": ("ghent", "gent"),
    "Liège": ("liège", "liege", "luik"),
    "Leuven": ("leuven", "louvain"),
    "Mechelen": ("mechelen"),
    "Bruges": ("bruges", "brugge"),
    "Charleroi": ("charleroi"),
    "Namur": ("namur", "namen"),
    "Hasselt": ("hasselt"),
    "Wallonia": ("wallonia", "wallonie", "wallonië"),
    "Flanders": ("flanders", "vlaanderen"),
    "Belgium": ("belgium", "belgië", "belgie", "belgian"),
    "Netherlands": ("netherlands", "holland", "dutch", "nederland"),
    "France": ("france", "french", "frankrijk"),
    "Germany": ("germany", "german", "duitsland"),
    "Luxembourg": ("luxembourg", "luxemburg"),
}

INDUSTRIES: dict[str, tuple[str, ...]] = {
    "Manufacturing": ("manufacturing", "manufacturer", "industrial", "factory", "plant"),
    "Retail": ("retail", "retailer", "store", "stores", "shop", "shops"),
    "Healthcare": ("healthcare", "hospital", "medical", "pharma", "pharmaceutical"),
    "Logistics": ("logistics", "warehouse", "warehousing", "transport", "distribution"),
    "Finance": ("finance", "banking", "insurance", "fintech"),
    "Technology": ("technology", "tech", "software", "it services", "saas"),
    "Hospitality": ("hospitality", "hotel", "hotels", "restaurant", "catering"),
    "Public sector": ("public sector", "government", "municipality", "commune"),
    "Construction": ("construction", "building", "contractor"),
    "Education": ("education", "school", "university", "academy"),
}

TOPICS: dict[str, tuple[str, ...]] = {
    "payroll": ("payroll", "payrolling", "wage", "wages", "salary", "salaries"),
    "HR": ("hr", "human resources", "people ops", "people operations"),
    "absence": ("absence", "sick leave", "leave management", "vacation"),
    "time & attendance": ("time & attendance", "time and attendance", "time tracking", "clocking"),
    "benefits": ("benefits", "fringe benefits", "extralegal"),
    "workforce management": ("workforce management", "workforce planning", "staffing"),
    "social secretariat": ("social secretariat", "sociaal secretariaat"),
    "self-service": ("self-service", "self service", "employee portal"),
    "onboarding": ("onboarding", "hire", "hiring", "recruitment"),
}

CONTACT_ROLES: dict[str, tuple[str, ...]] = {
    "HR Director": ("hr director", "director of hr", "head of hr"),
    "HR Manager": ("hr manager", "people manager", "regional hr"),
    "Payroll lead": ("payroll lead", "payroll manager", "payroll specialist"),
    "CFO": ("cfo", "chief financial"),
    "COO": ("coo", "chief operating"),
    "CHRO": ("chro", "chief people", "chief human"),
    "Store operations": ("store operations", "operations lead", "ops lead"),
}

LEGAL_SUFFIXES = (
    "nv",
    "sa",
    "bv",
    "bvba",
    "cvba",
    "vzw",
    "asbl",
    "gmbh",
    "ltd",
    "llc",
    "inc",
    "plc",
    "group",
    "holding",
    "partners",
)

STOPWORDS = {
    "a",
    "an",
    "the",
    "and",
    "or",
    "but",
    "in",
    "on",
    "at",
    "to",
    "for",
    "of",
    "with",
    "about",
    "from",
    "by",
    "as",
    "is",
    "are",
    "was",
    "were",
    "be",
    "been",
    "being",
    "have",
    "has",
    "had",
    "do",
    "does",
    "did",
    "will",
    "would",
    "could",
    "should",
    "may",
    "might",
    "must",
    "can",
    "this",
    "that",
    "these",
    "those",
    "they",
    "them",
    "their",
    "we",
    "our",
    "you",
    "your",
    "i",
    "me",
    "my",
    "it",
    "its",
    "looking",
    "look",
    "need",
    "needs",
    "needed",
    "want",
    "wants",
    "interested",
    "interest",
    "currently",
    "current",
    "run",
    "runs",
    "running",
    "using",
    "use",
    "used",
    "known",
    "know",
    "knows",
    "everything",
    "something",
    "also",
    "just",
    "like",
    "into",
    "across",
    "around",
    "over",
    "under",
    "between",
    "very",
    "quite",
    "rather",
    "more",
    "most",
    "some",
    "any",
    "all",
    "few",
    "many",
    "much",
    "several",
    "please",
    "thanks",
    "thank",
    "hello",
    "hi",
    "regarding",
    "client",
    "company",
    "organisation",
    "organization",
    "firm",
    "business",
    "involved",
    "sector",
    "review",
    "reviews",
    "looking",
    "notes",
    "info",
    "information",
    "prospect",
    "based",
    "located",
    "fte",
    "headcount",
}

MONTHS = (
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
    "jan",
    "feb",
    "mar",
    "apr",
    "jun",
    "jul",
    "aug",
    "sep",
    "sept",
    "oct",
    "nov",
    "dec",
)


@dataclass
class ExtractedKeywords:
    client: list[str] = field(default_factory=list)
    location: list[str] = field(default_factory=list)
    timeframe: list[str] = field(default_factory=list)
    industry: list[str] = field(default_factory=list)
    employee_count: str | None = None
    topics: list[str] = field(default_factory=list)
    contacts: list[str] = field(default_factory=list)
    other: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    def all_terms(self) -> list[str]:
        """Flat list of meaningful terms useful for search / matching."""
        terms: list[str] = []
        terms.extend(self.client)
        terms.extend(self.location)
        terms.extend(self.timeframe)
        terms.extend(self.industry)
        if self.employee_count:
            terms.append(self.employee_count)
        terms.extend(self.topics)
        terms.extend(self.contacts)
        terms.extend(self.other)
        # Deduplicate while preserving order
        seen: set[str] = set()
        unique: list[str] = []
        for term in terms:
            key = term.casefold()
            if key not in seen:
                seen.add(key)
                unique.append(term)
        return unique


CLIENT_FILLERS = {
    "prospect",
    "client",
    "company",
    "organisation",
    "organization",
    "account",
    "customer",
    "partner",
    "vendor",
    "supplier",
}


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        key = value.casefold()
        if key and key not in seen:
            seen.add(key)
            out.append(value)
    return out


def _prefer_specific(values: list[str]) -> list[str]:
    """Drop shorter terms that are fully contained in a longer one (e.g. Q2 inside Q2 2026)."""
    ordered = sorted(_unique(values), key=lambda v: (-len(v), v.casefold()))
    kept: list[str] = []
    for value in ordered:
        key = value.casefold()
        if any(key != other.casefold() and key in other.casefold() for other in kept):
            continue
        kept.append(value)
    return kept


def _match_catalog(text: str, catalog: dict[str, tuple[str, ...]]) -> list[str]:
    lowered = text.casefold()
    found: list[str] = []
    for label, aliases in catalog.items():
        if any(re.search(rf"\b{re.escape(alias)}\b", lowered) for alias in aliases):
            found.append(label)
    return found


def _extract_timeframes(text: str) -> list[str]:
    patterns = [
        r"\bQ[1-4]\s*(?:20)?\d{2}\b",
        r"\bQ[1-4]\b",
        r"\bH[12]\s*(?:20)?\d{2}\b",
        r"\bH[12]\b",
        r"\b(?:20)\d{2}\b",
        r"\b(?:next|this|last|coming)\s+(?:week|month|quarter|year|semester)\b",
        r"\b(?:by|before|after|during|since|until|till)\s+(?:"
        + "|".join(MONTHS)
        + r")(?:\s+(?:20)?\d{2})?\b",
        r"\b(?:"
        + "|".join(MONTHS)
        + r")\s+(?:20)?\d{2}\b",
        r"\bend of (?:year|month|quarter|"
        + "|".join(MONTHS)
        + r")\b",
        r"\bfiscal year(?:\s+(?:20)?\d{2})?\b",
        r"\bFY\s*(?:20)?\d{2}\b",
        r"\b(?:asap|immediately|urgent)\b",
        r"\bwithin\s+\d+\s+(?:days?|weeks?|months?|quarters?|years?)\b",
        r"\bin\s+\d+\s+(?:days?|weeks?|months?|quarters?|years?)\b",
    ]
    matches: list[str] = []
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            matches.append(re.sub(r"\s+", " ", match.group(0)).strip())
    return _prefer_specific(matches)


def _extract_employee_count(text: str) -> str | None:
    patterns = [
        r"\b(?:about|around|approx(?:imately)?|~|roughly)?\s*(\d{1,3}(?:[.,]\d{3})*|\d+)\+?\s*"
        r"(?:employees?|fte|staff|people|headcount|workers?)\b",
        r"\b(?:employees?|fte|staff|headcount)\s*(?:of|is|are|:)?\s*"
        r"(?:about|around|approx(?:imately)?|~|roughly)?\s*(\d{1,3}(?:[.,]\d{3})*|\d+)\+?\b",
        r"\b(\d{1,3}(?:[.,]\d{3})*|\d+)\+?\s*(?:-person|-employee)\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            raw = match.group(1).replace(".", "").replace(",", "")
            return f"{raw} employees"
    return None


def _clean_client_name(name: str) -> str | None:
    cleaned = re.sub(r"\s+", " ", name).strip(" ,.;:-")
    # Trim trailing location cues accidentally captured
    cleaned = re.sub(
        r"\s+(?:based|located|in|at|from|near)\b.*$",
        "",
        cleaned,
        flags=re.IGNORECASE,
    ).strip()
    if not cleaned:
        return None
    if cleaned.casefold() in STOPWORDS or cleaned.casefold() in CLIENT_FILLERS:
        return None
    # Drop single filler words or names that are only catalog labels
    if cleaned in LOCATIONS or cleaned in INDUSTRIES:
        return None
    return cleaned


def _extract_clients(text: str) -> list[str]:
    clients: list[str] = []
    suffix_alt = "|".join(LEGAL_SUFFIXES)

    # Case-sensitive patterns so we latch onto proper nouns, not mid-sentence words
    patterns = [
        # "client: Acme Manufacturing" / "company name - Northwind"
        (
            r"(?:[Cc]lient|[Cc]ompany|[Oo]rganisation|[Oo]rganization|[Aa]ccount|[Pp]rospect)\s*(?:name)?\s*[:\-]\s*"
            r"([A-Z][\w&.'-]*(?:\s+[A-Z][\w&.'-]*){0,5})",
            1,
            0,
        ),
        # "called Northwind Retail"
        (r"(?:called|named)\s+([A-Z][\w&.'-]*(?:\s+[A-Z][\w&.'-]*){0,4})", 1, 0),
        # "Acme Manufacturing NV" / "Contoso bv" / "Northwind Retail Group"
        (
            rf"\b([A-Za-z][\w&.'-]*(?:\s+[A-Za-z][\w&.'-]*){{0,4}}\s+(?:{suffix_alt}))\b",
            1,
            re.IGNORECASE,
        ),
        # "Acme Manufacturing in Antwerp" / "Contoso BV based in Leuven"
        (
            r"\b([A-Z][\w&.'-]*(?:\s+[A-Z][\w&.'-]*){0,4})\s+(?:based\s+in|located\s+in|in|at|from|near)\b",
            1,
            0,
        ),
    ]

    for pattern, group, flags in patterns:
        for match in re.finditer(pattern, text, flags=flags):
            cleaned = _clean_client_name(match.group(group))
            if cleaned:
                clients.append(cleaned)

    # Title-case multi-word names (case-sensitive pass for proper nouns)
    for match in re.finditer(r"\b([A-Z][a-zA-Z&.'-]+(?:\s+[A-Z][a-zA-Z&.'-]+){1,3})\b", text):
        phrase = match.group(1)
        words = phrase.split()
        if all(w.casefold() in STOPWORDS or w.casefold() in MONTHS or w.casefold() in CLIENT_FILLERS for w in words):
            continue
        if phrase in LOCATIONS or phrase in INDUSTRIES:
            continue
        cleaned = _clean_client_name(phrase)
        if cleaned:
            clients.append(cleaned)

    # Prefer the most specific name when overlaps exist
    return _prefer_specific(clients)


def _extract_other(text: str, already: set[str]) -> list[str]:
    """Collect leftover content words that were not classified."""
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9&'/.-]{1,}", text)
    other: list[str] = []
    for token in tokens:
        key = token.casefold()
        if key in STOPWORDS or len(key) < 3:
            continue
        if key in already:
            continue
        if key.isdigit():
            continue
        other.append(token)
    return _unique(other)[:12]


def extract_keywords(details: str) -> ExtractedKeywords:
    """Parse free-text notes into structured keyword buckets."""
    text = details.strip()
    if not text:
        return ExtractedKeywords()

    location = _match_catalog(text, LOCATIONS)
    industry = _match_catalog(text, INDUSTRIES)
    topics = _match_catalog(text, TOPICS)
    contacts = _match_catalog(text, CONTACT_ROLES)
    timeframe = _extract_timeframes(text)
    employee_count = _extract_employee_count(text)
    client = _extract_clients(text)

    # Drop client candidates that are really locations/industries/topics
    blocked = {v.casefold() for v in location + industry + topics + contacts + timeframe}
    for aliases in list(LOCATIONS.values()) + list(INDUSTRIES.values()) + list(TOPICS.values()):
        blocked.update(aliases)
    client = [c for c in client if c.casefold() not in blocked]

    already = set(blocked)
    for c in client:
        already.update(w.casefold() for w in c.split())
    if employee_count:
        already.update(w.casefold() for w in employee_count.split())
    for bucket in (location, industry, topics, contacts, timeframe):
        for item in bucket:
            already.update(w.casefold() for w in re.findall(r"[a-z0-9]+", item.casefold()))

    other = _extract_other(text, already)

    return ExtractedKeywords(
        client=client,
        location=location,
        timeframe=timeframe,
        industry=industry,
        employee_count=employee_count,
        topics=topics,
        contacts=contacts,
        other=other,
    )
