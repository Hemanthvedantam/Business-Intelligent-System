# services/pipeline_service.py
#
# Upgrade 6 — Validation Pipeline Extension
# ------------------------------------------
# Bridges the gap between data_quality.py (detection) and the agent layer
# (diagnosis + resolution). Called automatically after every file upload via
# BackgroundTasks so the user gets a full diagnosis report without waiting.
#
# Flow:
#   run_data_quality_check()
#       → _build_anomaly_context()      ← formats outlier/missing data for RCA
#       → root_cause_agent(state)       ← existing agent, zero changes
#       → recommend_agent(state)        ← upgraded agent (structured JSON output)
#       → returns DiagnosisReport dict
#
# The result is stored in a module-level dict under key:
#   "diagnosis:{filename}"
# and retrieved by GET /files/diagnosis/{filename}
# To add persistence later (Redis, DB), swap only _store() and _load().

import asyncio
import json
from datetime import datetime, timezone
from typing import Optional

from app.services.data_quality import run_data_quality_check
from app.services.duckdb_service import get_statistics, get_correlations
from app.agents.root_cause import root_cause_agent
from app.agents.recommend import recommend_agent
from app.core.logging import get_logger

logger = get_logger(__name__)


# ─────────────────────────────────────────────────────────
#  Public entry point — called from files.py BackgroundTasks
# ─────────────────────────────────────────────────────────
async def run_diagnosis_pipeline(filename: str) -> dict:
    """
    Full automated diagnosis pipeline for an uploaded file.

    Returns a DiagnosisReport dict and stores it in the module-level cache.
    Safe to call multiple times — always overwrites the previous result.
    """
    logger.info("diagnosis pipeline starting", filename=filename)

    try:
        # ── Step 1: Quality check (already done in upload, re-use result) ──
        quality_report = run_data_quality_check(filename)

        # Skip deep diagnosis if the file is clean — nothing to explain
        if quality_report["quality_score"] >= 95 and not quality_report["issues"]:
            report = _clean_report(filename, quality_report)
            _store(filename, report)
            return report

        # ── Step 2: Build anomaly context for RCA ────────────────────────
        anomaly_context = _build_anomaly_context(filename, quality_report)

        # ── Step 3: Construct minimal agent state ────────────────────────
        # We use the same InvestigationState shape the graph uses,
        # but bypass the full graph (planner/rag/forecast) for speed.
        question = _auto_question(quality_report)

        state = {
            "question":        question,
            "filename":        filename,
            "investigation_id": 0,           # not a user investigation
            "domain":          "data_quality",
            "plan":            "Automated quality diagnosis",
            "data_findings": {
                "analysis": anomaly_context["summary_for_rca"],
            },
            "rag_context":     "No documents available — automated pipeline.",
            "forecast":        {"predictions": "No forecast — automated pipeline."},
            "anomaly_context": anomaly_context,
            "quality_issues":  quality_report["issues"],
            "current_step":    "pipeline_start",
        }

        # ── Step 4: Root cause analysis ──────────────────────────────────
        state = await root_cause_agent(state)

        # ── Step 5: Structured recommendations ──────────────────────────
        state = await recommend_agent(state)

        # ── Step 6: Package the diagnosis report ────────────────────────
        report = {
            "filename":               filename,
            "generated_at":           datetime.now(timezone.utc).isoformat(),
            "pipeline_version":       "v2",
            "quality_score":          quality_report["quality_score"],
            "can_proceed":            quality_report["can_proceed"],
            "issues":                 quality_report["issues"],
            "anomaly_context":        anomaly_context,
            "root_causes":            state.get("root_causes", []),
            "structured_causes":      state.get("structured_causes", []),
            "confidence":             state.get("confidence", 70),
            "causes_found":           state.get("causes_found", 0),
            "recommendations":        state.get("recommendations", []),
            "structured_recommendations": state.get("structured_recommendations", []),
            "resolution_summary":     _resolution_summary(
                                          state.get("structured_recommendations", [])
                                      ),
            "status":                 "complete",
        }

        _store(filename, report)
        logger.info("diagnosis pipeline complete",
                    filename=filename,
                    causes=report["causes_found"],
                    recs=len(report["structured_recommendations"]))
        return report

    except Exception as e:
        logger.error("diagnosis pipeline failed", filename=filename, error=str(e))
        error_report = {
            "filename":     filename,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "status":       "error",
            "error":        str(e),
        }
        _store(filename, error_report)
        return error_report


# ─────────────────────────────────────────────────────────
#  Resolution tracking
# ─────────────────────────────────────────────────────────
def update_recommendation_status(
    filename: str,
    rec_index: int,
    status: str,           # "open" | "in_progress" | "resolved"
    note: Optional[str] = None,
) -> dict:
    """
    Update the status of one recommendation in a stored diagnosis report.
    Returns the updated report, or raises ValueError if not found.
    """
    valid_statuses = {"open", "in_progress", "resolved"}
    if status not in valid_statuses:
        raise ValueError(f"status must be one of {valid_statuses}")

    report = _load(filename)
    if not report:
        raise ValueError(f"No diagnosis report found for '{filename}'")

    recs = report.get("structured_recommendations", [])
    if rec_index < 0 or rec_index >= len(recs):
        raise ValueError(f"rec_index {rec_index} out of range (0–{len(recs)-1})")

    recs[rec_index]["status"] = status
    recs[rec_index]["resolved_at"] = (
        datetime.now(timezone.utc).isoformat() if status == "resolved" else None
    )
    if note:
        recs[rec_index]["note"] = note

    report["structured_recommendations"] = recs
    report["resolution_summary"] = _resolution_summary(recs)
    _store(filename, report)
    return report


def get_diagnosis_report(filename: str) -> Optional[dict]:
    """Retrieve the stored diagnosis report for a file. None if not yet run."""
    return _load(filename)


# ─────────────────────────────────────────────────────────
#  Internal helpers
# ─────────────────────────────────────────────────────────
def _build_anomaly_context(filename: str, quality_report: dict) -> dict:
    """
    Formats raw quality findings into a rich context block for root_cause_agent.
    Adds statistical backing (mean/std) for numeric outlier columns.
    """
    missing   = quality_report.get("missing_values", {}) or {}
    outliers  = quality_report.get("outliers", {}) or {}
    dup_count = quality_report.get("duplicate_rows", 0)
    score     = quality_report.get("quality_score", 100)

    # Pull stats for outlier columns to give RCA numeric evidence
    column_stats = {}
    try:
        stats = get_statistics(filename)
        if isinstance(stats, dict):
            for col in outliers:
                if col in stats:
                    s = stats[col]
                    column_stats[col] = {
                        "mean": round(s.get("mean", 0), 3),
                        "std":  round(s.get("std",  0), 3),
                        "min":  round(s.get("min",  0), 3),
                        "max":  round(s.get("max",  0), 3),
                    }
    except Exception:
        pass  # stats are optional enrichment

    # Build a plain-English paragraph the RCA agent reads as data_findings
    lines = [f"Data quality score: {score}/100."]

    if missing:
        pcts = [f"{col} ({info.get('missing_pct', '?')}% missing)"
                for col, info in list(missing.items())[:5]]
        lines.append(f"Missing values in: {', '.join(pcts)}.")

    if outliers:
        for col, info in list(outliers.items())[:5]:
            stat = column_stats.get(col, {})
            stat_str = (f" [mean={stat['mean']}, std={stat['std']}, "
                        f"range={stat['min']}–{stat['max']}]") if stat else ""
            count = info.get("outlier_count", "?")
            lines.append(f"Outlier column '{col}': {count} anomalous values{stat_str}.")

    if dup_count > 0:
        lines.append(f"Duplicate rows: {dup_count} exact duplicates found.")

    summary = " ".join(lines)

    return {
        "summary_for_rca": summary,
        "missing_columns": list(missing.keys()),
        "outlier_columns": list(outliers.keys()),
        "outlier_stats":   column_stats,
        "duplicate_count": dup_count,
        "quality_score":   score,
    }


def _auto_question(quality_report: dict) -> str:
    """Synthesise a focused investigation question from the quality report."""
    issues = quality_report.get("issues", [])
    score  = quality_report.get("quality_score", 100)
    if not issues:
        return f"Why does this dataset have a quality score of {score}/100?"
    top = issues[0] if issues else "data quality issues"
    return (
        f"This dataset has a quality score of {score}/100. "
        f"The primary issue is: {top}. "
        f"What are the root causes of these data quality problems "
        f"and what corrective actions should be taken?"
    )


def _resolution_summary(structured_recommendations: list) -> dict:
    """Compute open/in_progress/resolved counts for the dashboard."""
    counts = {"open": 0, "in_progress": 0, "resolved": 0, "total": 0}
    for r in structured_recommendations:
        status = r.get("status", "open")
        if status in counts:
            counts[status] += 1
        counts["total"] += 1
    counts["completion_pct"] = (
        round(counts["resolved"] / counts["total"] * 100)
        if counts["total"] > 0 else 0
    )
    return counts


def _clean_report(filename: str, quality_report: dict) -> dict:
    """Minimal report for files with no issues — skips RCA."""
    return {
        "filename":               filename,
        "generated_at":           datetime.now(timezone.utc).isoformat(),
        "pipeline_version":       "v2",
        "quality_score":          quality_report["quality_score"],
        "can_proceed":            True,
        "issues":                 [],
        "anomaly_context":        {},
        "root_causes":            [],
        "structured_causes":      [],
        "confidence":             100,
        "causes_found":           0,
        "recommendations":        ["Dataset is clean — no corrective actions needed."],
        "structured_recommendations": [],
        "resolution_summary":     {"open": 0, "in_progress": 0,
                                   "resolved": 0, "total": 0, "completion_pct": 100},
        "status":                 "clean",
    }


def _store(filename: str, report: dict) -> None:
    _fallback_store[f"diagnosis:{filename}"] = report


def _load(filename: str) -> Optional[dict]:
    return _fallback_store.get(f"diagnosis:{filename}")


# Module-level store — process-lifetime cache for diagnosis reports.
# Survives across requests within the same uvicorn worker process.
# If you add Redis/DB persistence later, swap _store/_load only.
_fallback_store: dict = {}