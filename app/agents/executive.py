# The executive agent is the last to run.
# It takes all technical findings and produces TWO things:
#
#   1. final_summary  — plain-English prose for the report (3 paragraphs)
#   2. executive_summary — structured dict consumed by the frontend:
#          { confidence, metrics[], evidence[], evidence_tree{}, forecast_text }
#
# Upgrades delivered here:
#   Upgrade 1 — structured executive summary with Impact + Forecast rows
#   Upgrade 2 — evidence_tree built from structured_causes

import re
import json
from app.providers.factory import get_provider
from app.core.logging import get_logger

logger = get_logger(__name__)


async def executive_agent(state: dict) -> dict:
    logger.info("executive agent starting")

    provider = get_provider()

    # ── 1. Generate prose summary ─────────────────────────────────────────
    prose_system = """You are a senior business executive writing for the CEO.
Write a clear executive summary in plain English — no markdown headers, no bullet points.
Exactly 3 paragraphs:
  Paragraph 1: What the data shows — the main finding with a key number if available.
  Paragraph 2: Why it is happening — the root causes in simple terms.
  Paragraph 3: What to do about it — top recommendations and expected outcome.
Keep it concise and direct. No jargon."""

    root_causes_text = "\n".join(state.get("root_causes", []))
    recs_text        = "\n".join(state.get("recommendations", []))
    forecast_text    = state.get("forecast", {}).get("predictions", "") if isinstance(state.get("forecast"), dict) else ""

    prose_messages = [
        {
            "role": "user",
            "content": (
                f"Original question: {state['question']}\n\n"
                f"Data analysis: {state.get('data_findings', {}).get('analysis', '')}\n\n"
                f"Root causes:\n{root_causes_text}\n\n"
                f"Recommendations:\n{recs_text}\n\n"
                f"Forecast: {forecast_text}\n\n"
                "Write the executive summary."
            )
        }
    ]

    # ── 2. Generate structured metrics for KPI cards ──────────────────────
    metrics_system = """You are a data analyst extracting KPI metrics from an investigation.
Return ONLY valid JSON — no markdown, no fences, no explanation.

Format:
{
  "metrics": [
    { "label": "<metric name>",   "value": "<formatted value>", "direction": "up|down|neutral" }
  ],
  "impact_summary":   "<one short phrase, e.g. Revenue ↓ 18%>",
  "forecast_summary": "<one short phrase, e.g. Revenue ↓ 8% next month>"
}

Rules:
- 3–5 metrics maximum
- direction "down" = negative/bad, "up" = positive/good
- values must include units or % where relevant
- impact_summary and forecast_summary must each be under 8 words"""

    metrics_messages = [
        {
            "role": "user",
            "content": (
                f"Question: {state['question']}\n\n"
                f"Root causes:\n{root_causes_text}\n\n"
                f"Forecast: {forecast_text}\n\n"
                f"Data findings: {state.get('data_findings', {}).get('analysis', '')[:800]}\n\n"
                "Extract the KPI metrics. Respond ONLY with JSON."
            )
        }
    ]

    try:
        # Run both LLM calls
        prose_summary, metrics_raw = await _gather(
            provider.complete(system=prose_system,  messages=prose_messages),
            provider.complete(system=metrics_system, messages=metrics_messages),
        )

        # Parse metrics JSON
        metrics_clean  = re.sub(r"```(?:json)?|```", "", metrics_raw).strip()
        metrics_parsed = json.loads(metrics_clean)
        metrics        = metrics_parsed.get("metrics", [])
        impact_summary  = metrics_parsed.get("impact_summary", "")
        forecast_summary = metrics_parsed.get("forecast_summary", "")

        # Add impact + forecast as explicit metric cards with special keys
        if impact_summary:
            metrics.insert(0, {
                "label":     "Impact",
                "value":     impact_summary,
                "direction": "down",
                "highlight": True,
            })
        if forecast_summary:
            metrics.append({
                "label":     "Forecast",
                "value":     forecast_summary,
                "direction": "down",
                "highlight": True,
            })

    except Exception as e:
        logger.warning("metrics extraction failed, using fallback", error=str(e))
        prose_summary = ""
        metrics       = _fallback_metrics(state)

    # If prose generation failed separately, run it alone
    if not prose_summary:
        try:
            prose_summary = await provider.complete(system=prose_system, messages=prose_messages)
        except Exception as e:
            logger.error("prose summary failed", error=str(e))
            prose_summary = "Investigation complete. See root causes and recommendations below."

    # ── 3. Build Evidence Tree from structured_causes ─────────────────────
    structured_causes = state.get("structured_causes", [])
    evidence_tree     = _build_evidence_tree(state["question"], structured_causes)

    # ── 4. Build evidence checklist (top items for exec summary) ──────────
    evidence_items = []
    for c in structured_causes[:5]:
        label = c.get("title", "")
        value = c.get("value")
        evidence_items.append(f"{label}{' ' + value if value else ''}")

    # Fallback to plain text causes
    if not evidence_items:
        for rc in state.get("root_causes", [])[:5]:
            clean = re.sub(r"\*\*(.*?)\*\*", r"\1", rc)[:70]
            evidence_items.append(clean)

    # ── 5. Assemble executive_summary dict ────────────────────────────────
    executive_summary = {
        "confidence":       state.get("confidence", 80),
        "metrics":          metrics,
        "evidence":         evidence_items,
        "impact_summary":   impact_summary if 'impact_summary' in dir() else "",
        "forecast_summary": forecast_summary if 'forecast_summary' in dir() else "",
    }

    logger.info("executive agent done",
                metrics_count=len(metrics),
                evidence_count=len(evidence_items))

    return {
        **state,
        "final_summary":    prose_summary,
        "executive_summary": executive_summary,
        "evidence_tree":     evidence_tree,
        "current_step":      "executive",
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────────────────────

async def _gather(*coros):
    """Run multiple coroutines concurrently."""
    import asyncio
    return await asyncio.gather(*coros)


def _build_evidence_tree(question: str, structured_causes: list) -> dict:
    """
    Build the tree dict that renderEvidenceTree() expects:
    {
        "root": "<question>",
        "branches": [
            { "label": "...", "value": "-18%", "direction": "negative", "depth": 0 }
        ]
    }
    """
    if not structured_causes:
        return None

    branches = []
    for c in structured_causes[:6]:
        branches.append({
            "label":     c.get("title", "Unknown cause"),
            "value":     c.get("value"),
            "direction": c.get("direction", "negative"),
            "depth":     0,
        })

    # Truncate question for display
    root_label = question.strip().rstrip("?")
    if len(root_label) > 80:
        root_label = root_label[:77] + "..."

    return {
        "root":     root_label,
        "branches": branches,
    }


def _fallback_metrics(state: dict) -> list:
    """Build minimal metrics when LLM extraction fails."""
    metrics = []
    causes  = state.get("root_causes", [])
    recs    = state.get("recommendations", [])
    conf    = state.get("confidence", 80)

    if causes:
        metrics.append({"label": "Root Causes Found", "value": str(len(causes)), "direction": "neutral"})
    if recs:
        metrics.append({"label": "Recommendations",   "value": str(len(recs)),   "direction": "up"})
    if conf:
        metrics.append({"label": "Confidence",        "value": f"{conf}%",        "direction": "neutral"})

    return metrics