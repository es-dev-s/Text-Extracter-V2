const API_BASE = (() => {
  if (location.protocol === "http:" || location.protocol === "https:") {
    return location.origin;
  }
  return "http://127.0.0.1:8000";
})();

const REQUEST_MS = 120000;
const drop = document.getElementById("drop");
const fileInput = document.getElementById("file");
const statusEl = document.getElementById("status");
const resultEl = document.getElementById("result");
const titleEl = document.getElementById("title");
const metaEl = document.getElementById("meta");
const badgeEl = document.getElementById("source-badge");
const headersEl = document.getElementById("headers");

let inflight = false;
let limits = { max_upload_mb: 50, max_pages: 80 };

function setStatus(text, kind) {
  statusEl.hidden = !text;
  statusEl.textContent = text || "";
  statusEl.className = `status${kind ? " " + kind : ""}`;
}

function errorDetail(data, status) {
  const detail = data && data.detail;
  if (typeof detail === "string" && detail) return detail;
  if (Array.isArray(detail) && detail.length) {
    return detail
      .map((item) => (typeof item === "string" ? item : item.msg || item.detail))
      .filter(Boolean)
      .join("; ");
  }
  if (status === 413) return "File is too large for this engine.";
  if (status === 429) return "Too many requests. Wait a minute and try again.";
  if (status >= 500) return "Engine error. Try again shortly.";
  return `Request failed (${status})`;
}

function openPicker() {
  if (inflight) return;
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
    if (!inflight) drop.classList.add("drag");
  });
});

["dragleave", "drop"].forEach((name) => {
  drop.addEventListener(name, (event) => {
    event.preventDefault();
    drop.classList.remove("drag");
  });
});

drop.addEventListener("drop", (event) => {
  if (inflight) return;
  const file = event.dataTransfer?.files?.[0];
  if (file) extract(file);
});

fileInput.addEventListener("change", () => {
  const file = fileInput.files?.[0];
  if (file) extract(file);
  fileInput.value = "";
});

async function loadLimits() {
  try {
    const response = await fetch(`${API_BASE}/v1/engine/status`);
    if (!response.ok) return;
    const data = await response.json();
    if (data.max_upload_mb) limits.max_upload_mb = Number(data.max_upload_mb);
    if (data.max_pages) limits.max_pages = Number(data.max_pages);
  } catch {
    /* keep defaults */
  }
}

async function extract(file) {
  if (inflight) return;
  if (!file.name.toLowerCase().endsWith(".pdf")) {
    setStatus("Please choose a PDF file.", "err");
    resultEl.hidden = true;
    return;
  }
  const maxBytes = limits.max_upload_mb * 1024 * 1024;
  if (file.size > maxBytes) {
    setStatus(`File exceeds ${limits.max_upload_mb} MB upload limit.`, "err");
    resultEl.hidden = true;
    return;
  }

  inflight = true;
  drop.classList.add("busy");
  resultEl.hidden = true;
  headersEl.replaceChildren();
  setStatus(`Reading ${file.name}…`, "busy");

  const body = new FormData();
  body.append("file", file);
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REQUEST_MS);

  try {
    const response = await fetch(`${API_BASE}/v1/ocr`, {
      method: "POST",
      body,
      signal: controller.signal,
    });
    const raw = await response.text();
    let data = {};
    if (raw) {
      try {
        data = JSON.parse(raw);
      } catch {
        throw new Error(errorDetail({}, response.status));
      }
    }
    if (!response.ok) {
      throw new Error(errorDetail(data, response.status));
    }

    if (!data.ok) {
      setStatus(data.message || "No extractable text in this PDF.", "err");
      return;
    }

    const source = data.title_source === "groq" ? "AI title" : "Native title";
    badgeEl.textContent = source;
    titleEl.textContent = data.title || "Untitled document";
    metaEl.textContent = `${data.page_count} page${data.page_count === 1 ? "" : "s"} · ${data.elapsed_ms || 0} ms`;

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
  } catch (error) {
    const message = error.name === "AbortError"
      ? "Request timed out. Try a smaller PDF."
      : (error.message || "Could not reach the extract API.");
    setStatus(message, "err");
  } finally {
    clearTimeout(timer);
    inflight = false;
    drop.classList.remove("busy");
  }
}

loadLimits();
