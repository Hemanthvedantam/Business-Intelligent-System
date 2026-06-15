# agents/recommend.py
#
# Upgrade 6 — Structured Recommendations
# ----------------------------------------
# Changed from plain-text numbered list to structured JSON output.
# Each recommendation now carries:
#   action      : str   — what to do (imperative, specific)
#   detail      : str   — why + how, referencing actual data findings
#   severity    : str   — "critical" | "high" | "medium" | "low"
#   effort      : str   — "quick-win" | "moderate" | "significant"
#   type        : str   — "data_fix" | "process" | "monitoring" | "investigation"
#   status      : str   — always "open" on creation
#
# Backward compat: state["recommendations"] still populated as plain-text list
# so the existing executive_agent and frontend continue working unchanged.
# New key: state["structured_recommendations"] carries the rich dicts.

import re
import json
from app.providers.factory import get_provider
from app.core.logging import get_logger

logger = get_logger(__name__)


async def recommend_agent(state: dict) -> dict:
    logger.info("recommendation agent starting")

    provider = get_provider()

    system = """You are an expert data quality and business consultant.
Analyse the root causes provided and return ONLY valid JSON — no markdown, no fences, no explanation.

Return this exact schema:
{
  "recommendations": [
    {
      "action":   "<imperative verb phrase, 4-8 words>",
      "detail":   "<one sentence: what to do, why, which column or metric>",
      "severity": "critical | high | medium | low",
      "effort":   "quick-win | moderate | significant",
      "type":     "data_fix | process | monitoring | investigation"
    }
  ]
}

Rules:
- Maximum 6 recommendations, minimum 2.
- severity=critical: blocks analysis or production use.
- severity=high: materially degrades results.
- severity=medium: should be fixed before sharing.
- severity=low: nice to have.
- effort=quick-win: under 1 hour.
- effort=moderate: half a day.
- effort=significant: multi-day or systemic change needed.
- type=data_fix: directly repair the data (impute, deduplicate, clip).
- type=process: fix the upstream process that produced the problem.
- type=monitoring: add an alert or check to catch this class of problem earlier.
- type=investigation: dig deeper before acting.
- Always reference specific column names or metrics from the findings.
- Order by severity descending (critical first)."""

    messages = [
        {
            "role": "user",
            "content": (
                f"Question: {state['question']}\n"
                f"Domain: {state.get('domain', 'unknown') if not isinstance(state.get('domain'), dict) else state['domain'].get('tag', 'unknown')}\n\n"
                f"Root causes identified:\n"
                f"{chr(10).join(state.get('root_causes', ['No root causes identified']))}\n\n"
                f"Anomaly context:\n"
                f"{_format_anomaly_context(state.get('anomaly_context', {}))}\n\n"
                f"Forecast:\n"
                f"{state.get('forecast', {}).get('predictions', 'No forecast available')}\n\n"
                "Respond ONLY with JSON matching the schema. No other text."
            ),
        }
    ]

    try:
        raw   = await provider.complete(system=system, messages=messages)
        clean = re.sub(r"```(?:json)?|```", "", raw).strip()
        parsed = json.loads(clean)

        structured = parsed.get("recommendations", [])

        # Stamp every recommendation as open on creation
        for rec in structured:
            rec.setdefault("status", "open")
            rec.setdefault("resolved_at", None)
            rec.setdefault("note", None)

        # Build plain-text list for backward compat (executive_agent, frontend)
        plain_list = _to_plain_list(structured)

        logger.info("recommendation agent done",
                    count=len(structured),
                    critical=sum(1 for r in structured if r.get("severity") == "critical"))

        return {
            **state,
            "recommendations":            plain_list,       # backward compat
            "structured_recommendations": structured,        # new rich output
            "current_step":               "recommend",
        }

    except Exception as e:
        logger.error("recommendation agent failed", error=str(e))

        # Graceful fallback — plain text only, no structured output
        plain_fallback = await _plain_text_fallback(provider, state)

        return {
            **state,
            "recommendations":            plain_fallback,
            "structured_recommendations": [],
            "error":                      str(e),
            "current_step":               "recommend",
        }


# ─────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────
def _format_anomaly_context(ctx: dict) -> str:
    """Format the anomaly_context dict as a readable string for the LLM."""
    if not ctx:
        return "No anomaly context available."

    lines = []
    score = ctx.get("quality_score")
    if score is not None:
        lines.append(f"Quality score: {score}/100")

    missing = ctx.get("missing_columns", [])
    if missing:
        lines.append(f"Columns with missing values: {', '.join(missing)}")

    outlier_cols = ctx.get("outlier_columns", [])
    outlier_stats = ctx.get("outlier_stats", {})
    for col in outlier_cols:
        stat = outlier_stats.get(col, {})
        if stat:
            lines.append(
                f"Outlier column '{col}': mean={stat.get('mean')}, "
                f"std={stat.get('std')}, range={stat.get('min')}–{stat.get('max')}"
            )
        else:
            lines.append(f"Outlier column: {col}")

    dup = ctx.get("duplicate_count", 0)
    if dup:
        lines.append(f"Duplicate rows: {dup}")

    return "\n".join(lines) if lines else "No specific anomaly data."


def _to_plain_list(structured: list) -> list:
    """Convert structured recommendations to the plain-text format the existing
    executive_agent and frontend expect."""
    plain = []
    severity_icons = {
        "critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢"
    }
    effort_labels = {
        "quick-win": "(quick win)", "moderate": "(moderate effort)",
        "significant": "(significant effort)"
    }
    for r in structured:
        icon    = severity_icons.get(r.get("severity", "medium"), "•")
        effort  = effort_labels.get(r.get("effort", "moderate"), "")
        action  = r.get("action", "")
        detail  = r.get("detail", "")
        plain.append(f"{icon} **{action}** {effort}: {detail}")
    return plain


async def _plain_text_fallback(provider, state: dict) -> list:
    """Last-resort fallback: ask for a plain numbered list."""
    try:
        system = """You are an expert business consultant.
Return ONLY a numbered list of specific recommendations. Nothing else.
Each item: **Action title**: specific steps. Maximum 6."""

        messages = [
            {
                "role": "user",
                "content": (
                    f"Question: {state['question']}\n\n"
                    f"Root causes:\n"
                    f"{chr(10).join(state.get('root_causes', ['No root causes identified']))}\n\n"
                    "Provide specific recommendations."
                ),
            }
        ]

        text  = await provider.complete(system=system, messages=messages)
        lines = [l.strip() for l in text.strip().split("\n")
                 if l.strip() and len(l.strip()) > 10]
        return lines
    except Exception:
        return ["Unable to generate recommendations — check logs for details."]