const API_URL = `${window.location.origin}/api/client-insight`;
const DOC_URL = `${window.location.origin}/api/documents`;
const INITIAL_VISIBLE = 4;
const LOAD_MORE_COUNT = 4;

const form = document.getElementById("client-form");
const details = document.getElementById("details");
const submitButton = document.getElementById("submit");
const statusEl = document.getElementById("status");
const results = document.getElementById("results");
const collapseToggle = document.getElementById("collapse-toggle");
const moreResults = document.getElementById("more-results");
const moreResultsLabel = document.getElementById("more-results-label");
const loadMoreButton = document.getElementById("load-more");
const docPanel = document.getElementById("doc-panel");
const docPanelTitle = document.getElementById("doc-panel-title");
const docPanelBody = document.getElementById("doc-panel-body");

let remainingDocuments = [];
let visibleCount = 0;

function setStatus(message, isError = false) {
  statusEl.textContent = message;
  statusEl.classList.toggle("error", isError);
}

function renderSidebarList(id, items, emptyText) {
  const list = document.getElementById(id);
  if (!items?.length) {
    const li = document.createElement("li");
    li.className = "empty";
    li.textContent = emptyText;
    list.replaceChildren(li);
    return;
  }
  list.replaceChildren(
    ...items.map((item) => {
      const li = document.createElement("li");
      if (String(item).includes("@")) {
        const link = document.createElement("a");
        link.href = `mailto:${item}`;
        link.textContent = item;
        li.appendChild(link);
      } else if (/^\+?[\d\s./()-]{8,}$/.test(String(item))) {
        const link = document.createElement("a");
        link.href = `tel:${String(item).replace(/[^\d+]/g, "")}`;
        link.textContent = item;
        li.appendChild(link);
      } else {
        li.textContent = item;
      }
      return li;
    }),
  );
}

function createTrustCard(source, { open = false } = {}) {
  const detailsEl = document.createElement("details");
  detailsEl.className = "top-source trust-source";
  detailsEl.open = open;
  detailsEl.dataset.role = source.role || "";

  const summaryEl = document.createElement("summary");
  const title = document.createElement("span");
  title.className = "top-source-title";
  title.textContent = source.title || source.path || "Source";

  const badges = document.createElement("span");
  badges.className = "source-badges";

  const typeBadge = document.createElement("span");
  typeBadge.className = "chip";
  typeBadge.textContent = source.doc_type || "doc";
  badges.appendChild(typeBadge);

  const roleBadge = document.createElement("span");
  roleBadge.className = `chip chip-role chip-role--${source.role || "supporting"}`;
  roleBadge.textContent = source.role || "supporting";
  badges.appendChild(roleBadge);

  summaryEl.append(title, badges);

  const meterLabel = document.createElement("label");
  meterLabel.className = "trust-meter-label";
  meterLabel.textContent = `Trust ${(Number(source.trust) || 0).toFixed(2)}`;
  const meter = document.createElement("meter");
  meter.className = "trust-meter";
  meter.min = 0;
  meter.max = 1;
  meter.low = 0.45;
  meter.high = 0.7;
  meter.optimum = 1;
  meter.value = Number(source.trust) || 0;

  const reasons = document.createElement("ul");
  reasons.className = "reason-list";
  for (const reason of (source.reasons || []).slice(0, 3)) {
    const li = document.createElement("li");
    li.textContent = reason;
    reasons.appendChild(li);
  }

  const signals = document.createElement("details");
  signals.className = "signal-details";
  const sigSum = document.createElement("summary");
  sigSum.textContent = "Signal breakdown";
  const sigList = document.createElement("ul");
  const sig = source.signals || {};
  for (const key of ["authority", "currency", "applicability", "corroboration", "integrity"]) {
    const li = document.createElement("li");
    li.textContent = `${key}: ${(Number(sig[key]) || 0).toFixed(2)}`;
    sigList.appendChild(li);
  }
  for (const reason of source.reasons || []) {
    const li = document.createElement("li");
    li.textContent = reason;
    sigList.appendChild(li);
  }
  signals.append(sigSum, sigList);

  const evidence = document.createElement("pre");
  evidence.className = "source-body";
  evidence.textContent = source.evidence || "";

  const viewBtn = document.createElement("button");
  viewBtn.type = "button";
  viewBtn.className = "linkish";
  viewBtn.textContent = "View source";
  viewBtn.addEventListener("click", () => loadDocument(source.id, source.title));

  const meta = document.createElement("p");
  meta.className = "source-meta";
  meta.textContent = [source.path, source.date, source.status, source.country, source.customer]
    .filter(Boolean)
    .join(" · ");

  detailsEl.append(summaryEl, meterLabel, meter, reasons, signals, meta, evidence, viewBtn);
  return detailsEl;
}

function updateMoreResultsUI() {
  const remaining = remainingDocuments.length;
  if (remaining <= 0) {
    moreResults.hidden = true;
    return;
  }
  moreResults.hidden = false;
  moreResultsLabel.textContent =
    remaining === 1
      ? "1 more related result available"
      : `${remaining} more related results available`;
  loadMoreButton.textContent =
    remaining <= LOAD_MORE_COUNT ? "Load more" : `Load ${LOAD_MORE_COUNT} more`;
}

function renderInitialResults(sources) {
  const wrap = document.getElementById("top-sources");
  if (!sources?.length) {
    wrap.replaceChildren();
    remainingDocuments = [];
    updateMoreResultsUI();
    const empty = document.createElement("p");
    empty.className = "source-summary";
    empty.textContent = "No sources retrieved.";
    wrap.appendChild(empty);
    return;
  }

  const initial = sources.slice(0, INITIAL_VISIBLE);
  remainingDocuments = sources.slice(INITIAL_VISIBLE);
  visibleCount = initial.length;
  wrap.replaceChildren(
    ...initial.map((src, index) => createTrustCard(src, { open: index === 0 })),
  );
  updateMoreResultsUI();
}

function loadMoreResults() {
  if (!remainingDocuments.length) {
    updateMoreResultsUI();
    return;
  }
  const wrap = document.getElementById("top-sources");
  const batch = remainingDocuments.splice(0, LOAD_MORE_COUNT);
  for (const src of batch) {
    wrap.appendChild(createTrustCard(src, { open: false }));
  }
  visibleCount += batch.length;
  updateMoreResultsUI();
}

async function loadDocument(id, title) {
  try {
    const response = await fetch(`${DOC_URL}/${id}`);
    if (!response.ok) throw new Error("Could not load document");
    const data = await response.json();
    docPanel.hidden = false;
    docPanelTitle.textContent = data.title || title || `Document ${id}`;
    docPanelBody.textContent = data.raw_text || "";
  } catch (error) {
    setStatus(error.message || "Document load failed", true);
  }
}

function renderVerdict(data) {
  const verdict = data.verdict || "caution";
  const chip = document.getElementById("verdict-chip");
  chip.textContent = verdict;
  chip.className = `chip chip--${verdict}`;

  document.getElementById("confidence").textContent = data.confidence
    ? `confidence ${(Number(data.confidence) * 100).toFixed(0)}%`
    : "";
  document.getElementById("headline").textContent = data.headline || "";
  document.getElementById("answer").textContent = data.answer || data.summary || "";

  const ctx = data.query_context || {};
  document.getElementById("query-context").textContent = [
    ctx.customer || "no customer",
    ctx.country || "no country",
    ctx.topic || "other",
    ctx.question_type || "lookup",
  ].join(" · ");

  const flags = document.getElementById("flag-list");
  flags.replaceChildren(
    ...(data.flags || []).slice(0, 10).map((flag) => {
      const li = document.createElement("li");
      li.className = `flag flag--${flag.severity || "info"}`;
      li.textContent = `${flag.code}: ${flag.message}`;
      return li;
    }),
  );

  const gaps = document.getElementById("gap-list");
  gaps.replaceChildren(
    ...(data.gaps || []).map((gap) => {
      const li = document.createElement("li");
      li.className = "gap-item";
      li.textContent = gap;
      return li;
    }),
  );

  const expertsBlock = document.getElementById("experts-block");
  const experts = document.getElementById("experts-list");
  const expertItems = data.experts || [];
  if (!expertItems.length) {
    expertsBlock.hidden = true;
    experts.replaceChildren();
  } else {
    expertsBlock.hidden = false;
    experts.replaceChildren(
      ...expertItems.map((ex) => {
        const li = document.createElement("li");
        li.className = ex.available ? "expert" : "expert expert--unavailable";
        li.textContent = `${ex.name}${ex.role ? ` — ${ex.role}` : ""}${
          ex.available ? "" : " (unavailable)"
        }${ex.why ? `: ${ex.why}` : ""}`;
        return li;
      }),
    );
  }
}

function renderSidebar(data) {
  const profile = data.client_profile || {
    name: data.client_name,
    industry: data.industry,
    people: (data.experts || []).map((e) => e.name),
    emails: [],
    phones: [],
    locations: [],
  };

  document.getElementById("client-name").textContent = profile.name || data.client_name || "—";
  document.getElementById("industry").textContent = profile.industry || data.industry || "";
  renderSidebarList("sidebar-people", profile.people, "No contacts found");
  renderSidebarList("sidebar-emails", profile.emails, "No email found");
  renderSidebarList("sidebar-phones", profile.phones, "No phone found");
  renderSidebarList("sidebar-locations", profile.locations, "No location found");
}

function setFormExpanded(expanded) {
  document.body.classList.toggle("form-expanded", expanded);
  collapseToggle.hidden = !document.body.classList.contains("has-results");
  collapseToggle.setAttribute("aria-expanded", String(expanded));
  collapseToggle.setAttribute(
    "aria-label",
    expanded ? "Collapse client details" : "Expand client details",
  );
  if (expanded) details.focus();
  else details.blur();
}

function showSummary(data) {
  renderVerdict(data);
  renderSidebar(data);
  const sources = data.sources?.length
    ? data.sources
    : (data.top_documents || []).map((doc) => ({
        id: doc.id,
        title: doc.title,
        doc_type: doc.source_type,
        role: doc.role || "supporting",
        trust: doc.trust ?? (doc.score || 0) / 100,
        signals: doc.signals || {},
        reasons: doc.reasons || [],
        evidence: doc.body || doc.summary || "",
        date: doc.source_date,
        path: doc.file_path,
        status: doc.doc_status,
      }));
  renderInitialResults(sources);
  results.hidden = false;
  document.body.classList.add("has-results");
  setFormExpanded(false);
}

document.getElementById("examples").addEventListener("click", (event) => {
  const btn = event.target.closest("[data-example]");
  if (!btn) return;
  details.value = btn.getAttribute("data-example") || "";
  setFormExpanded(true);
  details.focus();
});

document.getElementById("doc-panel-close").addEventListener("click", () => {
  docPanel.hidden = true;
});

collapseToggle.addEventListener("click", () => {
  setFormExpanded(!document.body.classList.contains("form-expanded"));
});

loadMoreButton.addEventListener("click", () => {
  loadMoreResults();
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const value = details.value.trim();
  if (!value) {
    setStatus("Enter a customer question first.", true);
    return;
  }

  submitButton.disabled = true;
  results.hidden = true;
  docPanel.hidden = true;
  document.body.classList.remove("has-results", "form-expanded");
  collapseToggle.hidden = true;
  setStatus("Scoring sources for this query...");

  try {
    const response = await fetch(API_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ details: value }),
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.detail || "The server could not complete the search.");
    }
    const data = await response.json();
    showSummary(data);
    setStatus("Results ready.");
  } catch (error) {
    setStatus(error.message || "Could not reach the API. Is the backend running?", true);
  } finally {
    submitButton.disabled = false;
  }
});
