"""
agent_utils.py
--------------
Shared utilities for all investigation agents.

Provides:
  retry_llm()           — exponential-backoff wrapper around any provider.complete() call
  safe_json_parse()     — strips markdown fences, repairs common LLM JSON errors, returns dict
  truncate_for_context()— hard-limits a string to N tokens (approx.) preserving start + end
  anomaly_scores()      — z-score anomaly detection on a numeric list
  trend_classifier()    — classifies a numeric series as improving / declining / stable / volatile
  moving_average()      — simple N-period moving average
  build_context_block() — formats a dict into a compact LLM-readable text block
"""

from __future__ import annotations

import asyncio
import json
import math
import re
import time
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
#  Constants
# ─────────────────────────────────────────────────────────────────────────────
_APPROX_CHARS_PER_TOKEN = 4          # conservative estimate
_MAX_RETRIES             = 3
_RETRY_BASE_DELAY        = 1.5       # seconds (doubles each retry)


# ─────────────────────────────────────────────────────────────────────────────
#  retry_llm
# ─────────────────────────────────────────────────────────────────────────────
async def retry_llm(
    provider,
    *,
    system: str,
    messages: list[dict],
    tag: str = "llm",
    max_retries: int = _MAX_RETRIES,
) -> str:
    """
    Call provider.complete() with exponential-backoff retry.

    On final failure raises the last exception so the calling agent can
    handle it gracefully (usually by returning a fallback).

    Usage:
        text = await retry_llm(provider, system=SYSTEM, messages=msgs, tag="root_cause")
    """
    last_exc: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            result = await provider.complete(system=system, messages=messages)
            if attempt > 1:
                logger.info(f"{tag}: succeeded on retry {attempt}")
            return result
        except Exception as exc:
            last_exc = exc
            if attempt < max_retries:
                delay = _RETRY_BASE_DELAY * (2 ** (attempt - 1))
                logger.warning(
                    f"{tag}: LLM call failed (attempt {attempt}/{max_retries}), "
                    f"retrying in {delay:.1f}s",
                    error=str(exc),
                )
                await asyncio.sleep(delay)
            else:
                logger.error(f"{tag}: all {max_retries} attempts failed", error=str(exc))

    raise last_exc  # type: ignore[misc]


# ─────────────────────────────────────────────────────────────────────────────
#  safe_json_parse
# ─────────────────────────────────────────────────────────────────────────────
def safe_json_parse(raw: str, fallback: Any = None, tag: str = "json") -> Any:
    """
    Parse JSON from an LLM response that may contain:
      - ```json ... ``` fences
      - leading/trailing prose
      - trailing commas (common LLM mistake)
      - single-quoted strings

    Returns `fallback` on all parse failures.
    """
    if not raw:
        return fallback

    # 1. Strip markdown fences
    text = re.sub(r"```(?:json)?", "", raw).replace("```", "").strip()

    # 2. Extract first JSON object or array
    match = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", text)
    if match:
        text = match.group(1)

    # 3. Remove trailing commas before } or ]
    text = re.sub(r",\s*([}\]])", r"\1", text)

    # 4. Replace single-quoted strings with double-quoted (naïve but catches most cases)
    # Only do this if no double-quotes at all — avoid mangling valid JSON
    if '"' not in text and "'" in text:
        text = text.replace("'", '"')

    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        logger.warning(f"{tag}: JSON parse failed: {exc}")
        return fallback


# ─────────────────────────────────────────────────────────────────────────────
#  truncate_for_context
# ─────────────────────────────────────────────────────────────────────────────
def truncate_for_context(text: str, max_tokens: int = 3000, preserve_end: bool = True) -> str:
    """
    Hard-limit a string to approximately `max_tokens` tokens.

    When `preserve_end=True` the function keeps the first 70 % and last 30 %
    of the budget so that both the schema header and the most recent data rows
    are visible to the LLM.
    """
    if not text:
        return ""

    max_chars = max_tokens * _APPROX_CHARS_PER_TOKEN
    if len(text) <= max_chars:
        return text

    if not preserve_end:
        return text[:max_chars] + "\n…[truncated]"

    head = int(max_chars * 0.70)
    tail = max_chars - head
    return (
        text[:head]
        + f"\n…[{len(text) - max_chars} chars truncated]…\n"
        + text[-tail:]
    )


# ─────────────────────────────────────────────────────────────────────────────
#  anomaly_scores
# ─────────────────────────────────────────────────────────────────────────────
def anomaly_scores(values: list[float | None], threshold: float = 2.5) -> list[dict]:
    """
    Z-score anomaly detection on a numeric series.

    Returns a list of dicts for each anomalous point:
      { "index": int, "value": float, "z_score": float, "direction": "high"|"low" }

    Points with |z| >= threshold are flagged. Skips None values.
    Requires at least 4 non-None values; returns [] otherwise.
    """
    clean = [(i, v) for i, v in enumerate(values) if v is not None]
    if len(clean) < 4:
        return []

    vals  = [v for _, v in clean]
    mean  = sum(vals) / len(vals)
    var   = sum((v - mean) ** 2 for v in vals) / len(vals)
    stdev = math.sqrt(var) if var > 0 else 0

    if stdev == 0:
        return []

    anomalies = []
    for orig_idx, v in clean:
        z = (v - mean) / stdev
        if abs(z) >= threshold:
            anomalies.append({
                "index":     orig_idx,
                "value":     round(v, 4),
                "z_score":   round(z, 2),
                "direction": "high" if z > 0 else "low",
            })
    return anomalies


# ─────────────────────────────────────────────────────────────────────────────
#  trend_classifier
# ─────────────────────────────────────────────────────────────────────────────
def trend_classifier(values: list[float | None]) -> dict:
    """
    Classify a numeric series into one of:
      improving | declining | stable | volatile | insufficient_data

    Also returns:
      slope_pct   — overall % change from first to last non-None value
      volatility  — coefficient of variation (stdev/mean)
      direction   — "up" | "down" | "flat"

    Example:
        >>> trend_classifier([10, 12, 11, 14, 16])
        {"classification": "improving", "slope_pct": 60.0, "volatility": 0.18, "direction": "up"}
    """
    clean = [v for v in values if v is not None]
    if len(clean) < 3:
        return {"classification": "insufficient_data", "slope_pct": 0.0, "volatility": 0.0, "direction": "flat"}

    first, last = clean[0], clean[-1]
    slope_pct   = round(((last - first) / abs(first)) * 100, 1) if first != 0 else 0.0

    mean  = sum(clean) / len(clean)
    stdev = math.sqrt(sum((v - mean) ** 2 for v in clean) / len(clean))
    cv    = round(stdev / abs(mean), 3) if mean != 0 else 0.0

    # High volatility overrides trend label
    if cv > 0.25:
        classification = "volatile"
    elif slope_pct > 5:
        classification = "improving"
    elif slope_pct < -5:
        classification = "declining"
    else:
        classification = "stable"

    direction = "up" if slope_pct > 2 else ("down" if slope_pct < -2 else "flat")

    return {
        "classification": classification,
        "slope_pct":      slope_pct,
        "volatility":     cv,
        "direction":      direction,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  moving_average
# ─────────────────────────────────────────────────────────────────────────────
def moving_average(values: list[float | None], window: int = 3) -> list[float | None]:
    """
    Simple N-period moving average. None values are skipped in the window.
    Output list is same length as input; first (window-1) entries are None.
    """
    result: list[float | None] = [None] * len(values)
    for i in range(len(values)):
        window_vals = [v for v in values[max(0, i - window + 1): i + 1] if v is not None]
        if len(window_vals) >= max(1, window // 2):
            result[i] = round(sum(window_vals) / len(window_vals), 4)
    return result


# ─────────────────────────────────────────────────────────────────────────────
#  build_context_block
# ─────────────────────────────────────────────────────────────────────────────
def build_context_block(label: str, data: Any, max_tokens: int = 800) -> str:
    """
    Format an arbitrary value (str, dict, list) into a compact, labelled
    LLM context block, truncated to max_tokens.

    Usage:
        block = build_context_block("Root causes", state.get("root_causes", []))
    """
    if data is None:
        return f"[{label}: not available]"

    if isinstance(data, str):
        text = data
    elif isinstance(data, list):
        text = "\n".join(f"- {item}" for item in data)
    else:
        try:
            text = json.dumps(data, indent=2)
        except Exception:
            text = str(data)

    truncated = truncate_for_context(text, max_tokens=max_tokens, preserve_end=False)
    return f"=== {label} ===\n{truncated}"