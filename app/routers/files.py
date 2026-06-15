import os
import math
from pathlib import Path
from fastapi import APIRouter, UploadFile, File, HTTPException, Depends, BackgroundTasks
from typing import Optional
from app.core.config import settings
from app.core.security import get_current_user
from app.services.data_quality import run_data_quality_check, generate_nlp_intelligence
from app.services.duckdb_service import load_file_as_dataframe, run_query

# ── Upgrade 6 ────────────────────────────────────────────
from app.services.pipeline_service import (
    run_diagnosis_pipeline,
    get_diagnosis_report,
    update_recommendation_status,
)
# ─────────────────────────────────────────────────────────

from app.core.logging import get_logger
from fastapi.responses import JSONResponse
from pydantic import BaseModel

logger = get_logger(__name__)
router = APIRouter()

ALLOWED_EXTENSIONS = {".csv", ".xlsx", ".xls", ".parquet"}


def clean_for_json(obj):
    """Recursively remove nan/inf values before sending JSON response."""
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    elif isinstance(obj, dict):
        return {k: clean_for_json(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [clean_for_json(v) for v in obj]
    return obj


# ─────────────────────────────────────────────────────────
#  Request bodies
# ─────────────────────────────────────────────────────────
class ResolveRequest(BaseModel):
    rec_index: int
    status:    str          # "open" | "in_progress" | "resolved"
    note:      Optional[str] = None


# ─────────────────────────────────────────────────────────
#  UPLOAD  (Upgrade 6: fires diagnosis pipeline in background)
# ─────────────────────────────────────────────────────────
@router.post("/upload")
async def upload_file(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
):
    extension = Path(file.filename).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"File type not allowed. Accepted: {', '.join(ALLOWED_EXTENSIONS)}"
        )

    contents = await file.read()
    if len(contents) > settings.MAX_FILE_SIZE_BYTES:
        raise HTTPException(status_code=400, detail="File too large. Max size is 100MB.")

    upload_path = Path(settings.UPLOAD_DIR) / file.filename
    with open(upload_path, "wb") as f:
        f.write(contents)

    logger.info("file uploaded", filename=file.filename, size=len(contents))

    # Synchronous quality check — fast, returned immediately
    quality_report = run_data_quality_check(file.filename)

    # ── Upgrade 6: full diagnosis runs in background ──────
    # Does not block the upload response.
    # Result available at GET /files/diagnosis/{filename} once complete.
    background_tasks.add_task(run_diagnosis_pipeline, file.filename)
    # ─────────────────────────────────────────────────────

    return JSONResponse(content=clean_for_json({
        "message":          "File uploaded successfully",
        "filename":         file.filename,
        "quality_report":   quality_report,
        "diagnosis_status": "running",   # lets the frontend poll /diagnosis
    }))


# ─────────────────────────────────────────────────────────
#  LIST
# ─────────────────────────────────────────────────────────
@router.get("/list")
async def list_files(current_user: dict = Depends(get_current_user)):
    upload_dir = Path(settings.UPLOAD_DIR)
    files = []

    for f in sorted(upload_dir.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        if f.is_file() and f.suffix.lower() in ALLOWED_EXTENSIONS:
            files.append({
                "filename":    f.name,
                "size_kb":     round(f.stat().st_size / 1024, 2),
                "extension":   f.suffix.lower(),
                "uploaded_at": f.stat().st_mtime,
            })

    return {"files": files}


# ─────────────────────────────────────────────────────────
#  PROFILE
# ─────────────────────────────────────────────────────────
@router.get("/profile/{filename}")
async def get_file_profile(
    filename: str,
    current_user: dict = Depends(get_current_user),
):
    """Return quality report for an already-uploaded file."""
    file_path = Path(settings.UPLOAD_DIR) / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"File '{filename}' not found.")
    if file_path.suffix.lower() not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Unsupported file type.")

    logger.info("profile requested", filename=filename)
    quality_report = run_data_quality_check(filename)
    return JSONResponse(content=clean_for_json(quality_report))


# ─────────────────────────────────────────────────────────
#  PREVIEW  ← data explorer table
# ─────────────────────────────────────────────────────────
@router.get("/preview/{filename}")
async def preview_file(
    filename: str,
    page: int = 1,
    page_size: int = 50,
    current_user: dict = Depends(get_current_user),
):
    file_path = Path(settings.UPLOAD_DIR) / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"File '{filename}' not found.")

    try:
        df     = load_file_as_dataframe(filename)
        total  = len(df)
        offset = (page - 1) * page_size
        chunk  = df.iloc[offset: offset + page_size]

        columns = [{"name": col, "type": str(df[col].dtype)} for col in df.columns]
        rows    = chunk.where(chunk.notna(), None).to_dict(orient="records")

        return JSONResponse(content=clean_for_json({
            "filename":    filename,
            "total_rows":  total,
            "page":        page,
            "page_size":   page_size,
            "total_pages": math.ceil(total / page_size),
            "columns":     columns,
            "rows":        rows,
        }))
    except Exception as e:
        logger.error("preview failed", filename=filename, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


# ─────────────────────────────────────────────────────────
#  NLP INSIGHTS  ← intelligence layer
# ─────────────────────────────────────────────────────────
@router.get("/nlp-insights/{filename}")
async def get_nlp_insights(
    filename: str,
    current_user: dict = Depends(get_current_user),
):
    file_path = Path(settings.UPLOAD_DIR) / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"File '{filename}' not found.")
    if file_path.suffix.lower() not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Unsupported file type.")

    logger.info("NLP insights requested", filename=filename)
    intelligence = generate_nlp_intelligence(filename)
    return JSONResponse(content=clean_for_json(intelligence))


# ─────────────────────────────────────────────────────────
#  SQL QUERY  ← data explorer
# ─────────────────────────────────────────────────────────
@router.post("/query/{filename}")
async def query_file(
    filename: str,
    body: dict,
    current_user: dict = Depends(get_current_user),
):
    file_path = Path(settings.UPLOAD_DIR) / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"File '{filename}' not found.")

    sql = body.get("sql", "").strip()
    if not sql:
        raise HTTPException(status_code=400, detail="SQL query is required.")

    forbidden = ("insert", "update", "delete", "drop", "alter", "create", "truncate")
    if any(kw in sql.lower() for kw in forbidden):
        raise HTTPException(status_code=400, detail="Only SELECT queries are allowed.")

    try:
        results = run_query(filename, sql)
        return JSONResponse(content=clean_for_json({
            "rows":  results,
            "count": len(results),
        }))
    except Exception as e:
        logger.error("SQL query failed", filename=filename, sql=sql, error=str(e))
        raise HTTPException(status_code=400, detail=f"Query error: {str(e)}")


# ─────────────────────────────────────────────────────────
#  DIAGNOSIS  (Upgrade 6) ← full pipeline report
# ─────────────────────────────────────────────────────────
@router.get("/diagnosis/{filename}")
async def get_diagnosis(
    filename: str,
    current_user: dict = Depends(get_current_user),
):
    """
    Return the full diagnosis report for a file.

    The report is generated in the background after upload.
    Poll this endpoint; when status != "running" it's ready.

    Response shape:
    {
        "status":                   "complete" | "clean" | "error" | "pending",
        "quality_score":            int,
        "issues":                   [str, ...],
        "root_causes":              [str, ...],       ← plain text
        "structured_causes":        [{title, detail, value, direction}, ...],
        "confidence":               int,
        "recommendations":          [str, ...],       ← plain text (backward compat)
        "structured_recommendations": [{action, detail, severity, effort, type, status}, ...],
        "resolution_summary":       {open, in_progress, resolved, total, completion_pct},
        "anomaly_context":          {...},
        "generated_at":             ISO timestamp,
    }
    """
    file_path = Path(settings.UPLOAD_DIR) / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"File '{filename}' not found.")

    report = get_diagnosis_report(filename)

    if report is None:
        # Still running (background task not yet finished)
        return JSONResponse(content={"status": "pending", "filename": filename})

    return JSONResponse(content=clean_for_json(report))


# ─────────────────────────────────────────────────────────
#  RESOLVE  (Upgrade 6) ← resolution tracking
# ─────────────────────────────────────────────────────────
@router.post("/resolve/{filename}")
async def resolve_recommendation(
    filename: str,
    body: ResolveRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    Update the status of one recommendation in the diagnosis report.

    Body:
        rec_index : int   — 0-based index into structured_recommendations
        status    : str   — "open" | "in_progress" | "resolved"
        note      : str?  — optional free-text note

    Returns the full updated diagnosis report.
    """
    file_path = Path(settings.UPLOAD_DIR) / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"File '{filename}' not found.")

    try:
        updated = update_recommendation_status(
            filename=filename,
            rec_index=body.rec_index,
            status=body.status,
            note=body.note,
        )
        logger.info("recommendation resolved",
                    filename=filename,
                    rec_index=body.rec_index,
                    status=body.status)
        return JSONResponse(content=clean_for_json(updated))

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("resolve failed", filename=filename, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


# ─────────────────────────────────────────────────────────
#  DELETE
# ─────────────────────────────────────────────────────────
@router.delete("/{filename}")
async def delete_file(
    filename: str,
    current_user: dict = Depends(get_current_user),
):
    file_path = Path(settings.UPLOAD_DIR) / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found.")

    os.remove(file_path)
    logger.info("file deleted", filename=filename)
    return {"message": f"{filename} deleted successfully"}