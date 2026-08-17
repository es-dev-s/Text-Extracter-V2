# OCR-V2 Engine

Production FastAPI service: native PDF text extract, numbered headers, and one exact document title. Lightweight Tesseract OCR runs only when native text cannot yield a title (garbled fonts or scans). The JSON API is stable for Next.js.

## Local run

```bash
cd Engine
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then set GROQ_API_KEY
# optional, for garbled/scanned titles locally:
# sudo apt install tesseract-ocr
./run.sh
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). API docs: `/docs`.

Without Tesseract locally, digital PDFs still work. Garbled/scanned titles fall back to Groq vision on a small page-1 JPEG.

## Railway (production)

1. Push this `Engine` folder as the Git repo, **or** push the parent `OCR-V2` repo and set **Root Directory** to `Engine`.
2. New Railway project → Deploy from GitHub.
3. Railway reads `Dockerfile` + `railway.toml`. It binds `PORT` automatically.
4. The image is `python:3.12-slim` plus **Tesseract English only** — no PyTorch, no EasyOCR. Everything the Engine needs is in the image; there is nothing to install or configure in Railway.
5. In **Variables**, set only these:

| Variable | Example |
|---|---|
| `ENVIRONMENT` | `production` |
| `GROQ_API_KEY` | first Groq key |
| `GROQ_API_KEY_2` | optional second key (same Qwen model; used if the first is limited) |
| `GROQ_API_KEY_3` | optional third key |
| `CORS_ORIGINS` | `*` for public testing; later your Next.js origin |

`GROQ_MODEL` and `GROQ_TITLE_VERIFY` already default to `qwen/qwen3.6-27b` and `true`.

6. Generate a public domain in Railway. Health check is `GET /health`.

### Concurrency — leave it alone

Do **not** set `WEB_CONCURRENCY`. `start.sh` reads the container's vCPU limit and
starts one uvicorn process per two vCPUs (2–8). Each process then runs a fixed
pool of heavy extraction slots, so all processes together match the CPU budget:

```
total concurrent extractions = WEB_CONCURRENCY × EXTRACT_WORKERS ≈ vCPUs
```

Uploads beyond that queue for up to `EXTRACT_QUEUE_TIMEOUT` seconds and then get
`503` + `Retry-After: 5` rather than exhausting container memory. Confirm the
live sizing any time with `GET /v1/engine/status` (`extract_workers`) or the
`engine ready … extract_workers=N` line in the deploy logs.

Measured on a 16-vCPU box: 200 uploads at 64-way concurrency finished at ~15
requests/second with zero failures and ~1.6 GB total RSS, so a 25 GB container
has a very wide margin. RAM is not the limit here — vCPUs are. If you want more
throughput, raise the plan's CPU rather than tuning these variables.

Optional overrides: `OCR_MAX_UPLOAD_MB` (default 50), `OCR_MAX_PAGES` (500),
`RATE_LIMIT_PER_MINUTE` (240 per IP **per worker**, `0` disables — set `0` when
Next.js proxies uploads server-side, since every request then shares one IP),
`EXTRACT_WORKERS`, `EXTRACT_QUEUE_TIMEOUT`, `ENGINE_DOCS`, `LOG_LEVEL`.

Do **not** commit `.env`. The Groq key lives only in Railway Variables.

Full client contract (endpoints, multi-file, TypeScript, errors): **[API.md](./API.md)**.

## API (unchanged for Next.js)

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Liveness |
| `GET` | `/ready` | Readiness |
| `GET` | `/v1/engine/status` | Feature flags and limits |
| `POST` | `/v1/ocr` | Extract (multipart `file`) |
| `POST` | `/v1/extract` | Same handler as `/v1/ocr` |

`POST` expects `multipart/form-data` with field `file` (PDF). Success body still includes `ok`, `method`, `title`, `title_source`, `headers`, `headers_listed`, `content`, `pages`, `elapsed_ms`.

Example:

```bash
curl -sS -F "file=@document.pdf" https://YOUR-SERVICE.up.railway.app/v1/ocr
```

When you add Next.js, point it at this origin and set `CORS_ORIGINS` to that frontend URL instead of `*`.
