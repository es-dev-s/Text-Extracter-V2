FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app \
    # Tesseract must stay single-threaded: the engine runs several of them at
    # once and its own OpenMP pool would oversubscribe every core.
    OMP_THREAD_LIMIT=1 \
    # glibc opens an arena per thread; capping them keeps RSS flat under load.
    MALLOC_ARENA_MAX=2

WORKDIR /app

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin engine \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=engine:engine app ./app
COPY --chown=engine:engine web ./web
COPY --chown=engine:engine start.sh ./start.sh
RUN chmod +x start.sh && chown engine:engine start.sh

USER engine
EXPOSE 8000

CMD ["./start.sh"]
