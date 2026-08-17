# OCR-V2 Engine API (Next.js contract)

Use this file as the source of truth when wiring a Next.js app. Verified against Engine `1.0.0`.

**The Engine already returns a real printed title for scanned files such as `3.Our.ME_Project_Study_2.pdf`.** If the website shows `3.Our.ME_Project_Study_2.pdf`, the app is displaying the **file name**, not Engine `title`.

---

## The title rule (read this first)

| JSON field | What it is | Show in the UI as the document name? |
|---|---|---|
| **`title`** | Printed title from the PDF | **Yes. Always this.** |
| `filename` | Original upload name (`3.Our.ME_Project_Study_2.pdf`) | **Never.** Storage / debug only. |
| `file.name` (browser) | Local file name before extract finishes | Temporary placeholder only. Replace it when extract returns. |

```ts
function documentName(data: ExtractResponse): string {
  const title = (data.title || "").trim();
  if (title && !title.toLowerCase().endsWith(".pdf") && title !== "Untitled document") {
    return title;
  }
  if (data.ok === false && data.message === "No OCR") {
    return "Title not readable (scanned PDF)";
  }
  return "Untitled document";
}
```

Do **not**:

- `data.filename` as the card heading
- `file.name` after extract has finished
- `data.ok === false` → fall back to the PDF name
- `data.method === "ocr"` → treat as failure (scanned PDFs succeed with `method: "ocr"` **and** a `title`)
- save the upload name into the database as `title`

Worked example — `3.Our.ME_Project_Study_2.pdf` (8-page scan, no embedded text):

```json
{
  "ok": true,
  "method": "ocr",
  "message": null,
  "filename": "3.Our.ME_Project_Study_2.pdf",
  "page_count": 8,
  "title": "Design and Fabrication of River Cleaning Machine",
  "title_source": "groq",
  "headers": [],
  "headers_listed": [],
  "content": "…",
  "pages": [{ "page": 1, "text": "…", "char_count": 1234 }],
  "elapsed_ms": 1676
}
```

- UI heading must be **Design and Fabrication of River Cleaning Machine**
- `filename` staying `3.Our.ME_Project_Study_2.pdf` is correct — that is the file, not the title
- HTTP status is **200**. This is a success.

---

## Base URL

No trailing slash. No API key in the browser. Groq stays on the Engine.

| Environment | Example |
|---|---|
| Local | `http://127.0.0.1:8000` |
| Railway | `https://YOUR-SERVICE.up.railway.app` |

Set Engine `CORS_ORIGINS` to the Next.js origin (or `*` for testing). `fetch` does not need `credentials: "include"`.

---

## Endpoints

| Method | Path | Body | Success |
|---|---|---|---|
| `GET` | `/health` | — | `{ "ok": true, "version": "1.0.0" }` |
| `GET` | `/ready` | — | same, or `503` while starting |
| `GET` | `/v1/engine/status` | — | limits + Groq flags |
| `POST` | `/v1/ocr` | `multipart/form-data`, field **`file`** | `ExtractResponse` |
| `POST` | `/v1/extract` | same | same handler |

Prefer **`POST /v1/ocr`**. There is no title-only endpoint. Ignore extra fields in the UI if you only need the title.

OpenAPI: `GET /docs` when `ENGINE_DOCS` is not `false`.

---

## Extract one PDF

One request = one PDF. Field name is **`file`** (singular).

```ts
const ENGINE = process.env.NEXT_PUBLIC_ENGINE_URL!; // no trailing slash

export async function extractPdf(file: File): Promise<ExtractResponse> {
  const body = new FormData();
  body.append("file", file); // do not rename the field

  const response = await fetch(`${ENGINE}/v1/ocr`, {
    method: "POST",
    body, // do not set Content-Type — browser sets the multipart boundary
    signal: AbortSignal.timeout(120_000), // scanned PDFs need this
  });

  const data = (await response.json()) as ExtractResponse | EngineErrorBody;
  if (!response.ok) {
    throw new Error(errorMessage(data, response.status));
  }
  return data as ExtractResponse;
}
```

Client timeout: **120 seconds**. Native extract is often < 1s. Scanned / custom-font PDFs can take 1–5s (OCR + Groq). A 5–10s Next.js timeout will abort those requests; the UI then keeps `file.name` and you will think the Engine returned the PDF name.

The bytes must start with `%PDF-`. A non-PDF returns HTTP `400`.

---

## What to show after extract

```ts
export function applyExtractToUi(file: File, data: ExtractResponse) {
  return {
    storedFileName: data.filename || file.name, // keep for downloads only
    heading: documentName(data),               // printed title
    outline: data.headers_listed,              // 1. 2. 3. …
    body: data.content,                        // full text
    pageCount: data.page_count,
    extractOk: data.ok,
  };
}
```

| Situation | HTTP | `ok` | `method` | `title` | What to show |
|---|---|---|---|---|---|
| Digital PDF | 200 | `true` | `native` | printed title | `title` |
| Scanned / image PDF (title recovered) | 200 | `true` | `ocr` | printed title | `title` |
| Unreadable scan | 200 | `false` | `ocr` | `null` | “Title not readable”, **not** the `.pdf` name |
| Bad / encrypted / too large PDF | 400 / 413 | — | — | — | `detail` error |

`title_source` is `"visual"` (layout) or `"groq"` (model confirmed). Both are the official title. Do not hide Groq titles.

---

## Digital PDF example

```json
{
  "ok": true,
  "method": "native",
  "filename": "3.Our.Project.pdf",
  "page_count": 10,
  "title": "Performance Analysis of Vapors Compression Refrigeration System with Hydrocarbon Blend Mixture of Different Refrigerants",
  "title_source": "groq",
  "headers": [],
  "headers_listed": [],
  "content": "--- Page 1 ---\n…",
  "pages": [{ "page": 1, "text": "…", "char_count": 800 }],
  "elapsed_ms": 849
}
```

Show the long `title`, not `3.Our.Project.pdf`.

---

## Unreadable scan (`200` + `ok: false`)

Only when OCR/vision also cannot read a title:

```json
{
  "ok": false,
  "method": "ocr",
  "message": "No OCR",
  "filename": "scan.pdf",
  "page_count": 4,
  "title": null,
  "title_source": null,
  "headers": [],
  "headers_listed": [],
  "content": "",
  "pages": [],
  "elapsed_ms": 40
}
```

HTTP is still 200. Do not use `filename` as the heading.

---

## Multiple files

No batch endpoint. N PDFs → N `POST`s, one after another (or 2–3 in parallel). Default **12 extracts per IP per minute**. On `429`, wait `Retry-After` (60s) and retry **that** file. Do not concatenate PDFs.

---

## Process (Engine)

```
PDF bytes
  → reject empty / not PDF / password / too many pages / too large
  → native text (PyMuPDF)
  → layout title + ~1000 characters from page 1, then 2, then 3
  → Groq (same Qwen model) confirms or completes the title
  → if still unreadable: Tesseract on the top of page 1, else Groq vision on that JPEG
  → JSON with title + headers + content
```

The whole PDF is never sent to Groq. Extra Groq keys (`GROQ_API_KEY_2`, `GROQ_API_KEY_3`) rotate on 429/timeout. Next.js does not send those keys.

Groq is an accelerator, not a dependency: with Groq switched off entirely, the
native layout reader plus Tesseract still return the same titles, so a rate
limit or outage degrades latency rather than accuracy.

### Measured on the 228-PDF `Mechanical Sources` corpus

| Check | Result |
|---|---|
| PDFs returning a real title | 227 / 228 (the last one has no title page — it opens at "Chapter 1") |
| Titles confirmed printed in the PDF, read back by OCR from the page image | 227 / 227 |
| Exact match against independent Groq-vision transcription | 82 / 83 (the one difference is the transcription reading a cover masthead) |
| Titles equal to the upload filename | 0 |
| Same bytes → same title over 5 repeat runs | 12 / 12 sampled |
| Latency | median 0.5 s, p95 1.1 s |

---

## Status — `GET /v1/engine/status`

```json
{
  "ok": true,
  "version": "1.0.0",
  "native": "pymupdf",
  "ocr": "tesseract",
  "groq_model": "qwen/qwen3.6-27b",
  "groq_title_verify": true,
  "max_upload_mb": 50,
  "max_pages": 500,
  "extract_workers": 2,
  "rate_limit_per_minute": 240
}
```

`ocr` is `"tesseract"` on Railway (Docker). Locally without Tesseract it may be `"disabled"`; Groq vision still recovers scanned titles. `groq_model` is `null` only if no Groq key is configured.

`extract_workers` is the heavy-extraction slots **per worker process**; multiply
by `WEB_CONCURRENCY` for the container total. Use it to size how many uploads the
Next.js app sends in parallel before it starts seeing `503`.

---

## Errors

Shape: `{ "detail": "…" }` or a FastAPI 422 array. CORS is applied so the browser can read them.

| Status | When | `detail` |
|---|---|---|
| `400` | Empty | `"Empty file"` |
| `400` | Not a PDF | `"File is not a PDF"` |
| `400` | Unreadable | `"Cannot open PDF"` / `"Cannot read PDF"` |
| `400` | Password | `"PDF is password-protected"` |
| `400` | Too many pages | `"PDF exceeds {n} page limit"` |
| `413` | Too large | `"File exceeds {n} MB upload limit"` |
| `422` | Missing field `file` | validation array |
| `429` | Rate limit | `"Too many requests. Try again in a minute."` + `Retry-After: 60` |
| `500` | Unhandled | `"Internal server error"` |
| `503` | Every extraction slot busy | `"Engine is busy. Retry shortly."` + `Retry-After: 5` |
| `503` | Not ready | `"Engine is starting"` |

`503` is backpressure, not a crash: the upload was never parsed. Retry the same
file after the `Retry-After` delay and it will succeed. Two or three retries with
a short backoff are enough for any realistic burst.

```ts
function errorMessage(data: unknown, status: number): string {
  const detail = data && typeof data === "object" ? (data as { detail?: unknown }).detail : undefined;
  if (typeof detail === "string" && detail) return detail;
  if (Array.isArray(detail) && detail[0] && typeof detail[0] === "object") {
    const first = detail[0] as { msg?: string };
    if (first.msg) return first.msg;
  }
  if (status === 413) return "File is too large.";
  if (status === 429) return "Too many requests. Wait a minute.";
  if (status >= 500) return "Engine error. Try again shortly.";
  return `Request failed (${status})`;
}
```

Log response header `X-Request-ID` on failures.

---

## Limits

| Limit | Default | Env |
|---|---|---|
| Upload | 50 MB | `OCR_MAX_UPLOAD_MB` |
| Pages | 500 | `OCR_MAX_PAGES` |
| Extract POSTs / IP / min | 240 per worker, `0` disables | `RATE_LIMIT_PER_MINUTE` |
| Concurrent extractions | sized from vCPUs | `WEB_CONCURRENCY` × `EXTRACT_WORKERS` |
| Queue wait before `503` | 20 s | `EXTRACT_QUEUE_TIMEOUT` |
| Groq wait | 30 s | `GROQ_TIMEOUT` |

Health/status routes are not rate-limited.

If Next.js proxies uploads server-side, every request arrives from one IP and
shares a single rate-limit bucket — set `RATE_LIMIT_PER_MINUTE=0` and rely on the
`503` backpressure instead. `GET /v1/engine/status` reports the live
`extract_workers` and `rate_limit_per_minute`.

---

## TypeScript (copy into the Next.js app)

```ts
export type TitleSource = "visual" | "groq";
export type ExtractMethod = "native" | "ocr";
export type HeaderSource = "font" | "toc" | "title";

export interface HeaderItem {
  index: number;
  text: string;
  page: number;
  level: number;
  source: HeaderSource;
}

export interface PageText {
  page: number;
  text: string;
  char_count: number;
}

export interface ExtractResponse {
  ok: boolean;
  method: ExtractMethod;
  message: string | null;
  filename: string | null;
  page_count: number;
  title: string | null;
  title_source: TitleSource | string | null;
  headers: HeaderItem[];
  headers_listed: string[];
  content: string;
  pages: PageText[];
  elapsed_ms: number;
}

export interface EngineStatus {
  ok: boolean;
  version: string;
  native: string;
  ocr: string;
  groq_model: string | null;
  groq_title_verify: boolean;
  max_upload_mb: number;
  max_pages: number;
  extract_workers: number;
  rate_limit_per_minute: number;
}

export interface EngineErrorBody {
  detail: string | Array<{ loc: unknown[]; msg: string; type: string }>;
}
```

---

## Optional Next.js proxy

`app/api/extract/route.ts` — hides the Engine URL. Keep `maxDuration = 120`.

```ts
export const runtime = "nodejs";
export const maxDuration = 120;

export async function POST(req: Request) {
  const incoming = await req.formData();
  const file = incoming.get("file");
  if (!(file instanceof Blob)) {
    return Response.json({ detail: "Missing file" }, { status: 400 });
  }

  const body = new FormData();
  body.append("file", file, (file as File).name || "document.pdf");

  const upstream = await fetch(`${process.env.ENGINE_URL}/v1/ocr`, {
    method: "POST",
    body,
  });

  const text = await upstream.text();
  return new Response(text, {
    status: upstream.status,
    headers: { "Content-Type": "application/json" },
  });
}
```

```
NEXT_PUBLIC_ENGINE_URL=http://127.0.0.1:8000
ENGINE_URL=http://127.0.0.1:8000
```

Engine: `CORS_ORIGINS=http://localhost:3000,https://your-app.vercel.app`

If the browser only talks to `/api/extract`, Engine CORS does not matter for that path.

---

## Why Next.js shows `name.pdf` (checklist)

1. Heading bound to `filename` or `file.name` instead of `title`.
2. Extract never called — list is built from the upload picker.
3. Timeout < ~15s — scanned extract aborted; UI kept the local name.
4. `if (!data.ok || data.method === "ocr")` treated as failure (wrong).
5. Database row created with `file.name` as title and never updated after `/v1/ocr`.
6. Talking to an old Engine deploy that did not yet recover scans (`ok: false`, `title: null`). Redeploy this Engine.

---

## What not to send

- No `Authorization` (ignored).
- No JSON body for extract (`422`).
- Do not send the Groq key from Next.js.
- Do not send the whole PDF to Groq from Next.js.
