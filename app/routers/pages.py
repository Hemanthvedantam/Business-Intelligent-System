"""
pages.py — HTML page router for ABIP.
Each route returns the correct Jinja2 template.
Data is fetched client-side via JS after page load,
except for datasets which are injected at render time for the Insights page.
"""

import os
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

router = APIRouter()

# ── Absolute Path Resolution Fixes ───────────────────────────────────────────

# CURRENT_DIR resolves to: D:\Business Intelligent System\backend\app\routers
CURRENT_DIR = Path(__file__).resolve().parent

# TEMPLATES_DIR resolves to: D:\Business Intelligent System\backend\app\templates
TEMPLATES_DIR = CURRENT_DIR.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# WORKSPACE_ROOT steps back to the true base project directory: D:\Business Intelligent System
WORKSPACE_ROOT = CURRENT_DIR.parent.parent.parent

# Grounding the uploads directory explicitly to the absolute workspace root path
_UPLOAD_DIR    = Path(os.getenv("UPLOAD_DIR", str(WORKSPACE_ROOT / "data" / "uploads")))
_SUPPORTED_EXT = {".csv", ".xlsx", ".xls", ".parquet", ".json"}


def _list_datasets() -> list[str]:
    """Return sorted filenames from the absolute uploads directory."""
    if not _UPLOAD_DIR.exists():
        print(f"[ABIP WARNING] Upload directory not found at: {_UPLOAD_DIR.resolve()}")
        return []
    
    datasets = sorted(
        f.name
        for f in _UPLOAD_DIR.iterdir()
        if f.is_file() and f.suffix.lower() in _SUPPORTED_EXT
    )
    print(f"[ABIP INFO] Injected datasets for Insights page: {datasets}")
    return datasets


# ── Overview ──────────────────────────────────────────────────────────────────

@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    return templates.TemplateResponse(request=request, name="dashboard.html", context={
        "show_nav": True, "active_page": "dashboard",
    })


# ── Data ──────────────────────────────────────────────────────────────────────

@router.get("/datasets", response_class=HTMLResponse)
async def datasets(request: Request):
    return templates.TemplateResponse(request=request, name="datasets.html", context={
        "show_nav": True, "active_page": "datasets",
    })


@router.get("/explorer", response_class=HTMLResponse)
async def explorer(request: Request):
    return templates.TemplateResponse(request=request, name="explorer.html", context={
        "show_nav": True, "active_page": "explorer",
    })


# ── Intelligence ──────────────────────────────────────────────────────────────

@router.get("/insights", response_class=HTMLResponse)
async def insights(request: Request):
    """Insights hub — injects dataset list so chips render on first load."""
    return templates.TemplateResponse(request=request, name="insights.html", context={
        "show_nav":    True,
        "active_page": "insights",
        "datasets":    _list_datasets(),   # ← required by insights.html chip loop
    })


@router.get("/nlp", response_class=HTMLResponse)
async def nlp_analysis(request: Request):
    return templates.TemplateResponse(request=request, name="nlp.html", context={
        "show_nav": True, "active_page": "nlp",
    })


# ── Workflows ─────────────────────────────────────────────────────────────────

@router.get("/chat", response_class=HTMLResponse)
async def chat(request: Request):
    return templates.TemplateResponse(request=request, name="chat.html", context={
        "show_nav": True, "active_page": "chat",
    })


@router.get("/investigation/{id}", response_class=HTMLResponse)
async def investigation(request: Request, id: int):
    return templates.TemplateResponse(request=request, name="investigation.html", context={
        "show_nav": True, "active_page": "chat", "investigation_id": id,
    })


# ── Outputs ───────────────────────────────────────────────────────────────────

@router.get("/reports", response_class=HTMLResponse)
async def reports(request: Request):
    return templates.TemplateResponse(request=request, name="reports.html", context={
        "show_nav": True, "active_page": "reports",
    })


# ── System ────────────────────────────────────────────────────────────────────

@router.get("/agents", response_class=HTMLResponse)
async def agents(request: Request):
    return templates.TemplateResponse(request=request, name="agents.html", context={
        "show_nav": True, "active_page": "agents",
    })


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    return templates.TemplateResponse(request=request, name="settings.html", context={
        "show_nav": True, "active_page": "settings",
    })


# ── Root ──────────────────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
async def root(request: Request):
    return RedirectResponse(url="/auth/login")