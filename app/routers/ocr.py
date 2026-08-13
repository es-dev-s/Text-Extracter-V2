"""Try native PDF text extract; fall back to OCR."""

from __future__ import annotations

import os

from fastapi import APIRouter, File, HTTPException, Request, UploadFile

from .. import __version__
from ..config import groq_enabled, groq_model
from ..schemas import EngineStatus, ExtractResponse
from ..services.pipeline import EncryptedPdfError, NativePdfError, extract_document

router = APIRouter()

_MAX_UPLOAD_MB = float(os.environ.get("OCR_MAX_UPLOAD_MB", "50"))
_MAX_BYTES = int(_MAX_UPLOAD_MB * 1024 * 1024)


@router.get("/engine/status", response_model=EngineStatus)
async def engine_status() -> EngineStatus:
    enabled = groq_enabled()
    return EngineStatus(
        version=__version__,
        groq_model=groq_model() if enabled else None,
        groq_title_verify=enabled,
    )


@router.post("/ocr", response_model=ExtractResponse)
@router.post("/extract", response_model=ExtractResponse)
async def extract_pdf_route(
    request: Request,
    file: UploadFile = File(...),
) -> ExtractResponse:
    filename = file.filename or "document.pdf"
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(data) > _MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds {_MAX_UPLOAD_MB:g} MB upload limit",
        )

    try:
        return await extract_document(
            data,
            filename,
            http=getattr(request.app.state, "http", None),
        )
    except EncryptedPdfError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except NativePdfError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
