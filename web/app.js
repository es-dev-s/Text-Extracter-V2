const API_BASE = (() => {
  if (location.protocol === "http:" || location.protocol === "https:") {
    return location.origin;
  }
  return "http://127.0.0.1:8000";
})();

const drop = document.getElementById("drop");
const fileInput = document.getElementById("file");
const statusEl = document.getElementById("status");
const resultEl = document.getElementById("result");
const titleEl = document.getElementById("title");
const metaEl = document.getElementById("meta");
const badgeEl = document.getElementById("source-badge");
const headersEl = document.getElementById("headers");

function setStatus(text, kind) {
  statusEl.hidden = !text;
  statusEl.textContent = text || "";
  statusEl.className = `status${kind ? " " + kind : ""}`;
}

function openPicker() {
  fileInput.click();
}

drop.addEventListener("click", openPicker);
drop.addEventListener("keydown", (event) => {
  if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    openPicker();
  }
});

["dragenter", "dragover"].forEach((name) => {
  drop.addEventListener(name, (event) => {
    event.preventDefault();
    drop.classList.add("drag");
  });
});

["dragleave", "drop"].forEach((name) => {
  drop.addEventListener(name, (event) => {
    event.preventDefault();
    drop.classList.remove("drag");
  });
});

drop.addEventListener("drop", (event) => {
  const file = event.dataTransfer?.files?.[0];
  if (file) extract(file);
});

fileInput.addEventListener("change", () => {
  const file = fileInput.files?.[0];
  if (file) extract(file);
  fileInput.value = "";
});

async function extract(file) {
  if (!file.name.toLowerCase().endsWith(".pdf")) {
    setStatus("Please choose a PDF file.", "err");
    resultEl.hidden = true;
    return;
  }

  resultEl.hidden = true;
  headersEl.replaceChildren();
  setStatus(`Reading ${file.name}…`, "busy");

  const body = new FormData();
  body.append("file", file);

  const started = performance.now();
  let data;
  try {
    const response = await fetch(`${API_BASE}/v1/ocr`, {
      method: "POST",
      body,
    });
    data = await response.json();
    if (!response.ok) {
      throw new Error(data.detail || `Request failed (${response.status})`);
    }
  } catch (error) {
    setStatus(error.message || "Could not reach the extract API.", "err");
    return;
  }

  if (!data.ok) {
    setStatus(data.message || "No extractable text in this PDF.", "err");
    return;
  }

  const waitMs = Math.round(performance.now() - started);
  const source = data.title_source === "groq" ? "AI title" : "Native title";
  badgeEl.textContent = source;
  titleEl.textContent = data.title || "Untitled document";
  metaEl.textContent = `${data.page_count} page${data.page_count === 1 ? "" : "s"} · ${data.elapsed_ms || waitMs} ms`;

  const items = Array.isArray(data.headers) && data.headers.length
    ? data.headers
    : (data.headers_listed || []).map((text, i) => ({ index: i + 1, text, page: 1, level: 2 }));

  const frag = document.createDocumentFragment();
  for (const item of items) {
    const li = document.createElement("li");
    li.className = `lvl-${item.level || 2}`;
    const text = document.createElement("span");
    text.className = "text";
    text.textContent = item.text || String(item);
    const page = document.createElement("span");
    page.className = "page";
    page.textContent = item.page ? `p. ${item.page}` : "";
    li.append(text, page);
    frag.append(li);
  }
  headersEl.append(frag);
  resultEl.hidden = false;
  setStatus("");
}
