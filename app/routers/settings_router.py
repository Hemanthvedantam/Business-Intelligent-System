"""
backend/app/routers/settings.py
Production-grade settings router — reads/writes .env, manages app config.
"""
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.db.session import get_db
from app.models.user import User

router = APIRouter(prefix="/settings", tags=["settings"])

# ── .env path resolution ─────────────────────────────────────────────────────
def _env_path() -> Path:
    """Resolve .env relative to the project root (backend/)."""
    here = Path(__file__).resolve()
    # Walk up until we find the .env or hit the filesystem root
    for parent in [here.parent.parent.parent, here.parent.parent, Path.cwd()]:
        candidate = parent / ".env"
        if candidate.exists():
            return candidate
    # Fall back to backend/.env whether it exists or not
    return here.parent.parent.parent / ".env"


def _read_env() -> Dict[str, str]:
    """Parse .env into a dict. Lines starting with # are ignored."""
    env: Dict[str, str] = {}
    path = _env_path()
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, val = line.partition("=")
            env[key.strip()] = val.strip().strip('"').strip("'")
    return env


def _write_env(updates: Dict[str, str]) -> None:
    """
    Update or append key=value pairs in .env.
    Preserves comments, blank lines, and existing keys.
    Backs up the original to .env.bak before writing.
    """
    path = _env_path()
    existing_lines: list[str] = []
    if path.exists():
        shutil.copy2(path, path.with_suffix(".bak"))
        existing_lines = path.read_text(encoding="utf-8").splitlines()

    updated_keys: set[str] = set()
    new_lines: list[str] = []

    for line in existing_lines:
        stripped = line.strip()
        if stripped.startswith("#") or not stripped:
            new_lines.append(line)
            continue
        if "=" in stripped:
            key = stripped.partition("=")[0].strip()
            if key in updates:
                new_lines.append(f'{key}="{updates[key]}"')
                updated_keys.add(key)
                continue
        new_lines.append(line)

    # Append any keys that were not already in the file
    for key, val in updates.items():
        if key not in updated_keys:
            new_lines.append(f'{key}="{val}"')

    path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


# ── App config stored in-process (non-secret settings) ───────────────────────
# In production you'd persist these to your DB. This is a clean in-memory
# approach that reads from env on first load and saves back to .env.
_APP_CONFIG_KEYS = {
    "primary_provider": "gemini",
    "gemini_model":     "gemini-2.0-flash",
    "groq_model":       "llama-3.3-70b-versatile",
    "max_tokens":       "4096",
    "temperature":      "0.3",
    "streaming":        "true",
    "autodelete":       "false",
    "rag":              "true",
    "domain":           "true",
    "max_upload_mb":    "100",
    "inv_timeout_min":  "10",
}

API_KEY_NAMES = [
    "GEMINI_API_KEY",
    "GROQ_API_KEY",
    "OPENROUTER_API_KEY",
    "QDRANT_API_KEY",
]


# ── Schemas ───────────────────────────────────────────────────────────────────
class AccountUpdate(BaseModel):
    username: Optional[str] = None
    email:    Optional[str] = None
    password: Optional[str] = None


class SaveSettingsRequest(BaseModel):
    primary_provider: str = "gemini"
    gemini_model:     str = "gemini-2.0-flash"
    groq_model:       str = "llama-3.3-70b-versatile"
    max_tokens:       int = 4096
    temperature:      float = Field(0.3, ge=0.0, le=1.0)
    streaming:        bool = True
    autodelete:       bool = False
    rag:              bool = True
    domain:           bool = True
    max_upload_mb:    int = 100
    inv_timeout_min:  int = 10
    api_keys:         Dict[str, str] = {}
    account:          Optional[AccountUpdate] = None


class ClearKeyRequest(BaseModel):
    key: str


# ── GET /settings/get ─────────────────────────────────────────────────────────
@router.get("/get")
async def get_settings(
    current_user: User = Depends(get_current_user),
):
    env = _read_env()

    # Determine which API keys are present (don't expose values)
    api_keys_present = {k: bool(env.get(k, "").strip()) for k in API_KEY_NAMES}

    # Read app-level config keys
    def _boolenv(key: str, default: bool) -> bool:
        v = env.get(key, str(default)).lower()
        return v in ("1", "true", "yes", "on")

    return {
        # AI models
        "primary_provider": env.get("PRIMARY_PROVIDER", _APP_CONFIG_KEYS["primary_provider"]),
        "gemini_model":     env.get("GEMINI_MODEL",     _APP_CONFIG_KEYS["gemini_model"]),
        "groq_model":       env.get("GROQ_MODEL",       _APP_CONFIG_KEYS["groq_model"]),
        "max_tokens":       int(env.get("MAX_TOKENS",   _APP_CONFIG_KEYS["max_tokens"])),
        "temperature":      float(env.get("TEMPERATURE", _APP_CONFIG_KEYS["temperature"])),
        "streaming":        _boolenv("STREAMING", True),
        # Workspace
        "autodelete":       _boolenv("AUTODELETE_UPLOADS", False),
        "rag":              _boolenv("ENABLE_RAG", True),
        "domain":           _boolenv("ENABLE_DOMAIN_DETECTION", True),
        "max_upload_mb":    int(env.get("MAX_UPLOAD_MB",   _APP_CONFIG_KEYS["max_upload_mb"])),
        "inv_timeout_min":  int(env.get("INV_TIMEOUT_MIN", _APP_CONFIG_KEYS["inv_timeout_min"])),
        # Keys presence only
        "api_keys_present": api_keys_present,
        # Current user
        "user": {
            "id":       current_user.id,
            "username": current_user.username,
            "email":    getattr(current_user, "email", ""),
            "role":     getattr(current_user, "role",  "Analyst"),
        },
    }


# ── POST /settings/save ───────────────────────────────────────────────────────
@router.post("/save")
async def save_settings(
    req: SaveSettingsRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    updates: Dict[str, str] = {
        "PRIMARY_PROVIDER":         req.primary_provider,
        "GEMINI_MODEL":             req.gemini_model,
        "GROQ_MODEL":               req.groq_model,
        "MAX_TOKENS":               str(req.max_tokens),
        "TEMPERATURE":              str(round(req.temperature, 4)),
        "STREAMING":                "true" if req.streaming else "false",
        "AUTODELETE_UPLOADS":       "true" if req.autodelete else "false",
        "ENABLE_RAG":               "true" if req.rag else "false",
        "ENABLE_DOMAIN_DETECTION":  "true" if req.domain else "false",
        "MAX_UPLOAD_MB":            str(req.max_upload_mb),
        "INV_TIMEOUT_MIN":          str(req.inv_timeout_min),
    }

    # Only update keys the user actually pasted (non-empty)
    for key, val in req.api_keys.items():
        if key in API_KEY_NAMES and val.strip():
            updates[key] = val.strip()

    _write_env(updates)

    # Update account if provided
    if req.account:
        if req.account.username:
            current_user.username = req.account.username
        if req.account.email and hasattr(current_user, "email"):
            current_user.email = req.account.email
        if req.account.password:
            from app.core.security import get_password_hash
            current_user.hashed_password = get_password_hash(req.account.password)
        db.commit()

    needs_restart = bool(req.api_keys)
    msg = "Settings saved successfully."
    if needs_restart:
        msg += " Restart the server for new API keys to take effect."

    return {"status": "ok", "message": msg, "needs_restart": needs_restart}


# ── POST /settings/clear-key ──────────────────────────────────────────────────
@router.post("/clear-key")
async def clear_api_key(
    req: ClearKeyRequest,
    current_user: User = Depends(get_current_user),
):
    if req.key not in API_KEY_NAMES:
        raise HTTPException(status_code=400, detail="Invalid key name")

    env = _read_env()
    env.pop(req.key, None)

    # Rewrite env without that key
    path = _env_path()
    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines()
        shutil.copy2(path, path.with_suffix(".bak"))
        new_lines = [l for l in lines if not l.strip().startswith(req.key + "=")]
        path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

    return {"status": "ok", "message": f"{req.key} cleared."}


# ── POST /settings/danger/{action} ───────────────────────────────────────────
@router.post("/danger/{action}")
async def danger_action(
    action: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if action == "clear-investigations":
        from app.models.investigation import Investigation
        db.query(Investigation).delete()
        db.commit()
        return {"status": "ok", "message": "All investigations deleted."}

    elif action == "clear-uploads":
        uploads_dir = Path(__file__).resolve().parent.parent.parent.parent / "data" / "uploads"
        deleted = 0
        if uploads_dir.exists():
            for f in uploads_dir.iterdir():
                if f.is_file() and f.name != ".gitkeep":
                    f.unlink()
                    deleted += 1
        return {"status": "ok", "message": f"Deleted {deleted} file(s) from uploads."}

    elif action == "purge-memory":
        try:
            from app.services.memory_service import MemoryService
            svc = MemoryService()
            await svc.purge_all()
            return {"status": "ok", "message": "Vector memory purged."}
        except Exception as e:
            return {"status": "partial", "message": f"Memory purge attempted: {e}"}

    else:
        raise HTTPException(status_code=400, detail=f"Unknown action: {action}")