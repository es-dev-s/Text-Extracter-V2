"""Load Engine settings from .env without blocking imports."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"

def groq_api_key() -> str:
    return os.environ.get("GROQ_API_KEY", "").strip()


def groq_model() -> str:
    return os.environ.get("GROQ_MODEL", "qwen/qwen3.6-27b").strip() or "qwen/qwen3.6-27b"


def groq_enabled() -> bool:
    verify = os.environ.get("GROQ_TITLE_VERIFY", "true").strip().lower() in {
        "1", "true", "yes", "on",
    }
    return verify and bool(groq_api_key())
