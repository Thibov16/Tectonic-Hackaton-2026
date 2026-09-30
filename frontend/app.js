const API_URL = "http://127.0.0.1:8000/api/client-insight";

const form = document.getElementById("client-form");
const details = document.getElementById("details");
const submitButton = document.getElementById("submit");
const statusEl = document.getElementById("status");
const results = document.getElementById("results");

function setStatus(message, isError = false) {
  statusEl.textContent = message;
  statusEl.classList.toggle("error", isError);
}

function renderList(id, items) {
  const list = document.getElementById(id);
  list.replaceChildren(
    ...items.map((item) => {
      const li = document.createElement("li");
      li.textContent = item;
      return li;
    }),
  );
}

function showSummary(data) {
  document.getElementById("client-name").textContent = data.client_name;
  document.getElementById("headline").textContent = data.headline;
  document.getElementById("industry").textContent = data.industry;
  document.getElementById("summary").textContent = data.summary;
  renderList("highlights", data.highlights);
  renderList("contacts", data.contacts);
  renderList("next-steps", data.next_steps);
  results.hidden = false;
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const value = details.value.trim();

  if (!value) {
    setStatus("Enter what you know about the client first.", true);
    return;
  }

  submitButton.disabled = true;
  results.hidden = true;
  setStatus("Retrieving client information...");

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
    setStatus("Summary ready.");
  } catch (error) {
    setStatus(error.message || "Could not reach the API. Is the backend running?", true);
  } finally {
    submitButton.disabled = false;
  }
});
