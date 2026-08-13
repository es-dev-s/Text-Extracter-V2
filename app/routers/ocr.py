"""Try native PDF text extract; fall back to OCR."""

from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, Request, UploadFile

from .. import __version__
from ..config import get_settings, groq_enabled, groq_model
from ..files import read_upload, safe_filename
from ..schemas import EngineStatus, ExtractResponse
from ..services.pipeline import EncryptedPdfError, NativePdfError, extract_document

router = APIRouter()


@router.get("/engine/status", response_model=EngineStatus)
async def engine_status() -> EngineStatus:
    settings = get_settings()
    enabled = groq_enabled()
    return EngineStatus(
        version=__version__,
        groq_model=groq_model() if enabled else None,
        groq_title_verify=enabled,
        max_upload_mb=settings.max_upload_mb,
        max_pages=settings.max_pages,
    )


@router.post("/ocr", response_model=ExtractResponse)
@router.post("/extract", response_model=ExtractResponse)
async def extract_pdf_route(
    request: Request,
    file: UploadFile = File(...),
) -> ExtractResponse:
    settings = get_settings()
    filename = safe_filename(file.filename)
    data = await read_upload(
        file,
        settings.max_upload_bytes,
        max_mb=settings.max_upload_mb,
    )

    try:
        return await extract_document(
            data,
            filename,
            http=getattr(request.app.state, "http", None),
            max_pages=settings.max_pages,
        )
    except EncryptedPdfError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except NativePdfError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
