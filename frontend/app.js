const API_URL = `${window.location.origin}/api/client-insight`;
const INITIAL_VISIBLE = 3;
const LOAD_MORE_COUNT = 3;

const form = document.getElementById("client-form");
const details = document.getElementById("details");
const submitButton = document.getElementById("submit");
const statusEl = document.getElementById("status");
const results = document.getElementById("results");
const collapseToggle = document.getElementById("collapse-toggle");
const moreResults = document.getElementById("more-results");
const moreResultsLabel = document.getElementById("more-results-label");
const loadMoreButton = document.getElementById("load-more");

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

function docMeta(doc) {
  const parts = [];
  if (doc.client_name) parts.push(doc.client_name);
  if (doc.source_type) parts.push(doc.source_type);
  if (doc.source_date) {
    parts.push(doc.source_date);
    if (doc.age_label) parts.push(doc.age_label);
  } else if (doc.age_label) {
    parts.push(doc.age_label);
  }
  if (doc.doc_status) parts.push(doc.doc_status);
  return parts.join(" · ");
}

function createResultCard(doc, { open = false } = {}) {
  const detailsEl = document.createElement("details");
  detailsEl.className = "top-source";
  detailsEl.open = open;

  const summaryEl = document.createElement("summary");
  const title = document.createElement("span");
  title.className = "top-source-title";
  title.textContent = doc.title;

  const badge = document.createElement("span");
  badge.className = "chip";
  badge.textContent = doc.age_label || doc.source_type;

  summaryEl.append(title, badge);

  const meta = document.createElement("p");
  meta.className = "source-meta";
  meta.textContent = docMeta(doc);

  const body = document.createElement("pre");
  body.className = "source-body";
  body.textContent =
    doc.body ||
    (doc.excerpts?.length ? doc.excerpts.join("\n") : doc.summary || "No excerpt available.");

  detailsEl.append(summaryEl, meta, body);
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
  loadMoreButton.textContent = remaining <= LOAD_MORE_COUNT ? "Load more" : `Load ${LOAD_MORE_COUNT} more`;
}

function renderInitialResults(documents) {
  const wrap = document.getElementById("top-sources");
  if (!documents?.length) {
    wrap.replaceChildren();
    remainingDocuments = [];
    updateMoreResultsUI();
    const empty = document.createElement("p");
    empty.className = "source-summary";
    empty.textContent = "No strong matches found.";
    wrap.appendChild(empty);
    return;
  }

  const initial = documents.slice(0, INITIAL_VISIBLE);
  remainingDocuments = documents.slice(INITIAL_VISIBLE);
  visibleCount = initial.length;

  wrap.replaceChildren(
    ...initial.map((doc, index) => createResultCard(doc, { open: index === 0 })),
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
  for (const doc of batch) {
    wrap.appendChild(createResultCard(doc, { open: false }));
  }
  visibleCount += batch.length;
  updateMoreResultsUI();
}

function renderSidebar(data) {
  const profile = data.client_profile || {
    name: data.client_name,
    industry: data.industry,
    people: data.contacts || [],
    emails: [],
    phones: [],
    locations: [],
  };

  document.getElementById("client-name").textContent = profile.name || data.client_name;
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

  if (expanded) {
    details.focus();
  } else {
    details.blur();
  }
}

function showSummary(data) {
  renderSidebar(data);

  const ranked = [
    ...(data.top_documents || []),
    ...(data.other_documents || []),
  ];
  const fallback = ranked.length ? ranked : data.documents || [];
  renderInitialResults(fallback);

  results.hidden = false;
  document.body.classList.add("has-results");
  setFormExpanded(false);
}

collapseToggle.addEventListener("click", () => {
  const expanded = !document.body.classList.contains("form-expanded");
  setFormExpanded(expanded);
});

loadMoreButton.addEventListener("click", () => {
  loadMoreResults();
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const value = details.value.trim();

  if (!value) {
    setStatus("Enter what you know about the client first.", true);
    return;
  }

  submitButton.disabled = true;
  results.hidden = true;
  document.body.classList.remove("has-results", "form-expanded");
  collapseToggle.hidden = true;
  setStatus("Searching the knowledge base...");

  try {
    const response = await fetch(API_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ details: value }),
    });

    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.detail || "The server could not retrieve a summary.");
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
