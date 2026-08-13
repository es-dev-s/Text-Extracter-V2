"""Engine settings from environment. Railway env vars win over a local .env."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env", override=False)

GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"
DEFAULT_GROQ_MODEL = "qwen/qwen3.6-27b"


def _truthy(value: str | None, default: bool = False) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _csv(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name, str(default))
    try:
        return int(raw or default)
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    raw = os.environ.get(name, str(default))
    try:
        return float(raw or default)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    environment: str
    log_level: str
    groq_api_key: str
    groq_model: str
    groq_title_verify: bool
    groq_timeout: float
    cors_origins: list[str]
    max_upload_mb: float
    max_pages: int
    rate_limit_per_minute: int
    docs_enabled: bool

    @property
    def is_production(self) -> bool:
        return self.environment in {"production", "prod"}

    @property
    def max_upload_bytes(self) -> int:
        return int(self.max_upload_mb * 1024 * 1024)

    @property
    def cors_allow_all(self) -> bool:
        return not self.cors_origins or self.cors_origins == ["*"]

    @classmethod
    def from_env(cls) -> Settings:
        env = os.environ.get("ENVIRONMENT", "development").strip().lower() or "development"
        origins = _csv(os.environ.get("CORS_ORIGINS", "*"))
        return cls(
            environment=env,
            log_level=(os.environ.get("LOG_LEVEL", "INFO").strip().upper() or "INFO"),
            groq_api_key=os.environ.get("GROQ_API_KEY", "").strip(),
            groq_model=(
                os.environ.get("GROQ_MODEL", DEFAULT_GROQ_MODEL).strip()
                or DEFAULT_GROQ_MODEL
            ),
            groq_title_verify=_truthy(os.environ.get("GROQ_TITLE_VERIFY"), True),
            groq_timeout=max(5.0, _float("GROQ_TIMEOUT", 30.0)),
            cors_origins=origins,
            max_upload_mb=max(1.0, _float("OCR_MAX_UPLOAD_MB", 50.0)),
            max_pages=max(1, _int("OCR_MAX_PAGES", 80)),
            rate_limit_per_minute=max(1, _int("RATE_LIMIT_PER_MINUTE", 12)),
            docs_enabled=_truthy(os.environ.get("ENGINE_DOCS"), True),
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()


def groq_api_key() -> str:
    return get_settings().groq_api_key


def groq_model() -> str:
    return get_settings().groq_model


def groq_enabled() -> bool:
    settings = get_settings()
    return settings.groq_title_verify and bool(settings.groq_api_key)
