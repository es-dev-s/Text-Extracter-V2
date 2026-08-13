"""Request/response models for PDF extraction."""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class HeaderItem(BaseModel):
    index: int = Field(..., description="1-based position in the numbered list")
    text: str
    page: int = Field(..., description="1-based page where this heading appears")
    level: int = Field(1, description="Heading level, 1 is most prominent")
    source: Literal["font", "toc", "title"] = "font"


class PageText(BaseModel):
    page: int
    text: str
    char_count: int


class ExtractResponse(BaseModel):
    ok: bool
    method: Literal["native", "ocr"]
    message: Optional[str] = None
    filename: Optional[str] = None
    page_count: int = 0
    title: Optional[str] = None
    title_source: Optional[str] = None
    headers: List[HeaderItem] = Field(default_factory=list)
    headers_listed: List[str] = Field(
        default_factory=list,
        description="Headers and title numbered 1, 2, 3, ...",
    )
    content: str = ""
    pages: List[PageText] = Field(default_factory=list)
    elapsed_ms: int = 0


class EngineStatus(BaseModel):
    ok: bool = True
    version: str
    native: str = "pymupdf"
    ocr: str = "disabled"
    groq_model: Optional[str] = None
    groq_title_verify: bool = False
    max_upload_mb: float = 50
    max_pages: int = 80
