# OCR-V2 Engine

Production FastAPI service: native PDF text extract, numbered headers, and one exact document title. OCR is still a stub. The JSON API is stable for a future Next.js app.

## Local run

```bash
cd Engine
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then set GROQ_API_KEY
./run.sh
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). API docs: `/docs`.

## Railway (public testing)

1. Push this `Engine` folder as the Git repo, **or** push the parent `OCR-V2` repo and set **Root Directory** to `Engine`.
2. New Railway project → Deploy from GitHub.
3. Railway reads `Dockerfile` + `railway.toml`. It binds `PORT` automatically.
4. In **Variables**, set at least:

| Variable | Example |
|---|---|
| `ENVIRONMENT` | `production` |
| `GROQ_API_KEY` | your Groq key |
| `GROQ_MODEL` | `qwen/qwen3.6-27b` |
| `GROQ_TITLE_VERIFY` | `true` |
| `CORS_ORIGINS` | `*` for public testing; later your Next.js origin |

Optional: `OCR_MAX_UPLOAD_MB` (default 50), `OCR_MAX_PAGES` (80), `RATE_LIMIT_PER_MINUTE` (12), `ENGINE_DOCS` (`true`), `LOG_LEVEL` (`INFO`), `WEB_CONCURRENCY` (`1`).

5. Generate a public domain in Railway. Health check is `GET /health`.

Do **not** commit `.env`. The Groq key lives only in Railway Variables.

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
