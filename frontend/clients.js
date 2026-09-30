const API_CLIENTS = "http://127.0.0.1:8000/api/clients";
const API_DOCS = (id) => `http://127.0.0.1:8000/api/clients/${id}/documents`;

const statusEl = document.getElementById("status");
const panel = document.getElementById("clients-panel");
const list = document.getElementById("client-list");

function setStatus(message, isError = false) {
  statusEl.textContent = message;
  statusEl.classList.toggle("error", isError);
}

function renderClient(client, documents) {
  const li = document.createElement("li");
  li.className = "client-item";

  const title = document.createElement("h2");
  title.textContent = client.name;

  const meta = document.createElement("p");
  meta.className = "client-meta";
  const industry = client.industry || "Industry unknown";
  meta.textContent = `${industry} · ${documents.length} document(s)`;

  const tags = document.createElement("p");
  tags.className = "client-tags";
  const allTags = [...new Set(documents.flatMap((doc) => doc.tags || []))];
  tags.textContent = allTags.length ? `Tags: ${allTags.join(", ")}` : "No tags yet";

  const docs = document.createElement("ul");
  docs.className = "doc-list";
  for (const doc of documents) {
    const item = document.createElement("li");
    item.textContent = `[${doc.source_type}] ${doc.title}`;
    docs.appendChild(item);
  }
  if (!documents.length) {
    const empty = document.createElement("li");
    empty.textContent = "No linked documents.";
    docs.appendChild(empty);
  }

  li.append(title, meta, tags, docs);
  return li;
}

async function loadClients() {
  try {
    const response = await fetch(API_CLIENTS);
    if (!response.ok) {
      throw new Error("Could not load clients from the API.");
    }
    const clients = await response.json();
    if (!clients.length) {
      setStatus("No clients in the database yet. Run: python classify.py");
      return;
    }

    list.replaceChildren();
    for (const client of clients) {
      const docsResponse = await fetch(API_DOCS(client.id));
      const documents = docsResponse.ok ? await docsResponse.json() : [];
      list.appendChild(renderClient(client, documents));
    }

    panel.hidden = false;
    setStatus(`${clients.length} client(s) loaded.`);
  } catch (error) {
    setStatus(
      error.message || "Could not reach the API. Is the backend running on :8000?",
      true,
    );
  }
}

loadClients();
