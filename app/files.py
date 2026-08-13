"""Safe handling of uploaded filenames and request bodies."""

from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException, UploadFile


def safe_filename(name: str | None) -> str:
    raw = Path(name or "document.pdf").name.replace("\x00", "").strip()
    if not raw or raw in {".", ".."}:
        return "document.pdf"
    return raw[:255]


async def read_upload(file: UploadFile, max_bytes: int, *, max_mb: float) -> bytes:
    chunks: list[bytes] = []
    total = 0
    try:
        while True:
            piece = await file.read(1024 * 1024)
            if not piece:
                break
            total += len(piece)
            if total > max_bytes:
                raise HTTPException(
                    status_code=413,
                    detail=f"File exceeds {max_mb:g} MB upload limit",
                )
            chunks.append(piece)
    finally:
        await file.close()
    data = b"".join(chunks)
    if not data:
        raise HTTPException(status_code=400, detail="Empty file")
    return data
