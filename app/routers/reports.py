# Reports router — Upgrade 7
# Handles PDF and DOCX report download for a completed investigation.
# Also provides a shareable view endpoint and a list endpoint.

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response, HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db.session import get_db
from app.core.security import get_current_user
from app.models.investigation import Investigation, InvestigationStatus
from app.services.report_service import generate_pdf, generate_docx
from app.core.logging import get_logger

logger = get_logger(__name__)
router = APIRouter()


# ─────────────────────────────────────────────────────────────────────────────
#  List endpoint  — GET /reports/list
#  Returns all COMPLETED investigations for the current user so the reports
#  page can populate its table and KPI strip.
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/list")
async def list_reports(
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    result = await db.execute(
        select(Investigation)
        .where(
            Investigation.user_id == int(current_user["sub"]),
            Investigation.status == InvestigationStatus.COMPLETED,
        )
        .order_by(Investigation.created_at.desc())
    )
    investigations = result.scalars().all()

    reports = []
    for inv in investigations:
        # Expose both PDF and DOCX as separate virtual report entries
        # so the frontend can list them with their download links.
        base = {
            "id":               inv.id,
            "investigation_id": inv.id,
            "question":         inv.question,
            "filename":         inv.dataset_filename or f"Investigation #{inv.id}",
            "domain":           inv.domain or "other",
            "created_at":       str(inv.created_at),
            "status":           inv.status,   # will be "completed" (lowercase)
        }
        reports.append({**base, "format": "pdf"})
        reports.append({**base, "format": "docx"})

    return {"reports": reports}


# ─────────────────────────────────────────────────────────────────────────────
#  Download endpoint  — GET /reports/download/{investigation_id}?format=pdf|docx
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/download/{investigation_id}")
async def download_report(
    investigation_id: int,
    format: str = "pdf",
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    result = await db.execute(
        select(Investigation).where(
            Investigation.id == investigation_id,
            Investigation.user_id == int(current_user["sub"]),
        )
    )
    investigation = result.scalar_one_or_none()

    if not investigation:
        raise HTTPException(status_code=404, detail="Investigation not found")

    if investigation.status != InvestigationStatus.COMPLETED:
        raise HTTPException(
            status_code=400,
            detail="Investigation is not complete yet. Please wait for it to finish."
        )

    result_data = _build_result_dict(investigation)

    safe_name = (
        investigation.question[:40]
        .replace(" ", "_")
        .replace("/", "-")
        .replace("\\", "-")
    )

    if format.lower() == "docx":
        try:
            docx_bytes = generate_docx(investigation, result_data)
        except RuntimeError as e:
            raise HTTPException(status_code=500, detail=str(e))

        logger.info("docx report generated", id=investigation_id)
        return Response(
            content=docx_bytes,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={
                "Content-Disposition": f'attachment; filename="ABIP_{safe_name}.docx"'
            },
        )

    else:  # default: PDF
        try:
            pdf_bytes = generate_pdf(investigation, result_data)
        except RuntimeError as e:
            raise HTTPException(status_code=500, detail=str(e))

        logger.info("pdf report generated", id=investigation_id)
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="ABIP_{safe_name}.pdf"'
            },
        )


# ─────────────────────────────────────────────────────────────────────────────
#  Shareable view endpoint  — GET /reports/view/{investigation_id}
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/view/{investigation_id}", response_class=HTMLResponse)
async def view_report(
    investigation_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    result = await db.execute(
        select(Investigation).where(
            Investigation.id == investigation_id,
            Investigation.user_id == int(current_user["sub"]),
        )
    )
    investigation = result.scalar_one_or_none()

    if not investigation:
        raise HTTPException(status_code=404, detail="Investigation not found")

    result_data = _build_result_dict(investigation)
    html        = _render_html_report(investigation, result_data)
    return HTMLResponse(content=html)


# ─────────────────────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _build_result_dict(investigation) -> dict:
    return {
        "root_causes":       investigation.root_causes      or [],
        "recommendations":   investigation.recommendations   or [],
        "final_summary":     investigation.final_summary     or "",
        "executive_summary": {},
        "evidence_tree":     None,
        "forecast":          {},
    }


def _render_html_report(investigation, result_data: dict) -> str:
    from datetime import datetime
    import html as html_lib

    def esc(s):
        return html_lib.escape(str(s or ""))

    question  = esc(investigation.question)
    domain    = esc((investigation.domain or "other").capitalize())
    filename  = esc(investigation.dataset_filename or "")
    summary   = esc(investigation.final_summary or "No summary available.")
    generated = datetime.now().strftime("%d %b %Y  %H:%M")

    causes_html = "".join(
        f"<li>{esc(c)}</li>"
        for c in (investigation.root_causes or [])
    )
    recs_html = "".join(
        f"<li>{esc(r)}</li>"
        for r in (investigation.recommendations or [])
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ABIP Report — {question[:60]}</title>
<style>
  body {{ font-family: Inter, system-ui, sans-serif; background:#F8FAFC; color:#0F172A;
          margin:0; padding:0; }}
  .container {{ max-width: 780px; margin: 40px auto; padding: 0 24px 60px; }}
  .header {{ border-bottom: 2px solid #E2E8F0; padding-bottom: 20px; margin-bottom: 28px; }}
  .brand {{ font-size: 12px; font-weight: 700; color: #2563EB; text-transform: uppercase;
            letter-spacing: .08em; margin-bottom: 8px; }}
  h1 {{ font-size: 22px; font-weight: 800; margin: 0 0 8px; color: #0F172A; }}
  .meta {{ font-size: 13px; color: #64748B; display: flex; gap: 16px; flex-wrap: wrap; }}
  h2 {{ font-size: 15px; font-weight: 700; color: #2563EB; margin: 28px 0 10px;
        border-bottom: 1px solid #E2E8F0; padding-bottom: 6px; }}
  p  {{ font-size: 14px; line-height: 1.75; margin: 0 0 12px; }}
  ul, ol {{ padding-left: 20px; }}
  li {{ font-size: 14px; line-height: 1.7; margin-bottom: 6px; }}
  .footer {{ margin-top: 48px; font-size: 12px; color: #94A3B8;
             border-top: 1px solid #E2E8F0; padding-top: 16px; }}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <div class="brand">ABIP — AI Business Intelligence Platform</div>
    <h1>{question}</h1>
    <div class="meta">
      <span>📁 {filename}</span>
      <span>🏷 {domain}</span>
      <span>🕐 {generated}</span>
    </div>
  </div>

  <h2>Executive Summary</h2>
  {"".join(f"<p>{p}</p>" for p in summary.split("&#10;&#10;") if p.strip())}

  {"<h2>Root Causes</h2><ul>" + causes_html + "</ul>" if causes_html else ""}
  {"<h2>Recommendations</h2><ol>" + recs_html + "</ol>" if recs_html else ""}

  <div class="footer">
    Generated by ABIP — AI Business Intelligence Platform · {generated}
  </div>
</div>
</body>
</html>"""