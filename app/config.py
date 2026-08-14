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


def _groq_keys_from_env() -> tuple[str, ...]:
    """GROQ_API_KEY, optional GROQ_API_KEYS (csv), and GROQ_API_KEY_2..8. Same Qwen model."""
    ordered: list[str] = []
    seen: set[str] = set()

    def add(raw: str) -> None:
        for part in _csv(raw) if "," in raw else [raw.strip()]:
            key = part.strip()
            if not key or key in seen:
                continue
            seen.add(key)
            ordered.append(key)

    add(os.environ.get("GROQ_API_KEY", ""))
    add(os.environ.get("GROQ_API_KEYS", ""))
    for index in range(2, 9):
        add(os.environ.get(f"GROQ_API_KEY_{index}", ""))
    return tuple(ordered)


@dataclass(frozen=True)
class Settings:
    environment: str
    log_level: str
    groq_api_key: str
    groq_api_keys: tuple[str, ...]
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
        keys = _groq_keys_from_env()
        return cls(
            environment=env,
            log_level=(os.environ.get("LOG_LEVEL", "INFO").strip().upper() or "INFO"),
            groq_api_key=keys[0] if keys else "",
            groq_api_keys=keys,
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
    keys = groq_api_keys()
    return keys[0] if keys else ""


def groq_api_keys() -> tuple[str, ...]:
    return get_settings().groq_api_keys


def groq_model() -> str:
    return get_settings().groq_model


def groq_enabled() -> bool:
    settings = get_settings()
    return settings.groq_title_verify and bool(settings.groq_api_keys)
