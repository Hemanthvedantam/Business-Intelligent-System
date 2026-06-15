"""
Forecast Agent
--------------
Predicts future values based on historical data patterns.
Now produces structured forecast_data for the frontend chart:
  {
    "labels":     [...],   # date/period labels for ALL points
    "historical": [...],   # values for past points (None for forecast range)
    "forecast":   [...],   # values for future points (None for historical range)
    "horizon":    "90 days",
  }
"""

import json
import re
from datetime import datetime, timedelta
from app.providers.factory import get_provider
from app.core.logging import get_logger

logger = get_logger(__name__)


# ─────────────────────────────────────────────────────────
#  Helper: build forecast_data from basic_info + LLM text
# ─────────────────────────────────────────────────────────
def _build_forecast_data(state: dict, forecast_text: str) -> dict | None:
    """
    Attempt to build a forecast chart payload.

    Strategy:
    1. Use historical trend_data already produced by data_analyst (if present).
    2. Extrapolate forward using simple linear trend or LLM-extracted numbers.
    3. Produce aligned historical / forecast arrays with None padding so
       Chart.js renders them as separate datasets on one x-axis.
    """
    trend = state.get("trend_data")

    # ── Fallback: no trend data at all ───────────────────
    if not trend or not trend.get("values"):
        return _synthetic_forecast(forecast_text)

    hist_labels = trend["labels"]
    hist_values = trend["values"]

    n_hist = len(hist_values)
    if n_hist < 2:
        return None

    # ── Simple linear projection ──────────────────────────
    # slope = average delta over last 3 points (or all if < 3)
    window = min(3, n_hist - 1)
    deltas = [hist_values[i + 1] - hist_values[i] for i in range(n_hist - window - 1, n_hist - 1)]
    avg_delta = sum(deltas) / len(deltas) if deltas else 0

    # Forecast 6 periods forward
    n_fore = 6
    last_val = hist_values[-1]
    fore_values = [round(last_val + avg_delta * (i + 1), 2) for i in range(n_fore)]

    # Build future labels
    # Try to detect if labels look like dates; otherwise just append numbers
    fore_labels = _extend_labels(hist_labels, n_fore)

    # Combine: historical array has None for forecast slots, and vice-versa
    # Overlap by 1 point so the lines connect visually
    all_labels = hist_labels + fore_labels
    historical = hist_values + [None] * n_fore
    forecast   = [None] * (n_hist - 1) + [hist_values[-1]] + fore_values

    return {
        "labels":     all_labels,
        "historical": historical,
        "forecast":   forecast,
        "horizon":    "next 6 periods",
        "series_label": trend.get("series_label", "Value"),
    }


def _extend_labels(labels: list, n: int) -> list:
    """Extend a label list by n steps, detecting date or numeric patterns."""
    if not labels:
        return [str(i) for i in range(1, n + 1)]

    last = labels[-1]

    # Try date formats
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%Y-%m", "%b %Y", "%Y"):
        try:
            dt = datetime.strptime(last[:len(fmt) + 2], fmt)
            # Guess step from previous label
            if len(labels) >= 2:
                try:
                    dt_prev = datetime.strptime(labels[-2][:len(fmt) + 2], fmt)
                    delta = (dt - dt_prev)
                except Exception:
                    delta = timedelta(days=30)
            else:
                delta = timedelta(days=30)

            result = []
            for i in range(1, n + 1):
                result.append((dt + delta * i).strftime(fmt))
            return result
        except ValueError:
            continue

    # Try integer
    try:
        base = int(last)
        return [str(base + i) for i in range(1, n + 1)]
    except ValueError:
        pass

    # Generic fallback
    return [f"{last}+{i}" for i in range(1, n + 1)]


def _synthetic_forecast(forecast_text: str) -> dict | None:
    """
    Last resort: create a plausible synthetic forecast curve when we have
    no real data to extrapolate from. Uses a simple up/down trend based
    on whether the LLM text is optimistic or pessimistic.
    """
    text_lower = forecast_text.lower()
    pessimistic = any(w in text_lower for w in ("declin", "drop", "fall", "decreas", "worse", "risk"))
    direction = -1 if pessimistic else 1

    base = 100
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    n_hist = 6
    n_fore = 6

    hist_values = [round(base + direction * i * 2 + (i % 2) * 1.5, 1) for i in range(n_hist)]
    fore_values = [round(hist_values[-1] + direction * (i + 1) * 3, 1) for i in range(n_fore)]

    all_labels  = months[:n_hist] + months[n_hist:n_hist + n_fore]
    historical  = hist_values + [None] * n_fore
    forecast    = [None] * (n_hist - 1) + [hist_values[-1]] + fore_values

    return {
        "labels":     all_labels,
        "historical": historical,
        "forecast":   forecast,
        "horizon":    "next 6 months (illustrative)",
        "series_label": "Index",
        "synthetic":  True,
    }


# ─────────────────────────────────────────────────────────
#  Main Agent
# ─────────────────────────────────────────────────────────
async def forecast_agent(state: dict) -> dict:
    logger.info("forecast agent starting")

    provider = get_provider()

    system = """You are an expert business forecaster.
Based on the data analysis findings provided, make realistic predictions about future trends.
Be specific about timeframes — next 30, 60, 90 days.
Base predictions only on the patterns found in the data.
Include percentage changes and direction where possible."""

    messages = [
        {
            "role": "user",
            "content": f"""
Question: {state['question']}
Domain: {state.get('domain', 'unknown')}

Data findings:
{state.get('data_findings', {}).get('analysis', 'No analysis available')}

Based on these findings, what are the forecast and future predictions?
""",
        }
    ]

    try:
        forecast_text = await provider.complete(system=system, messages=messages)

        # Build structured chart data
        forecast_data = _build_forecast_data(state, forecast_text)

        forecast = {
            "predictions": forecast_text,
            "timeframe":   "30-90 days",
            "data":        forecast_data,     # structured for chart
        }

        logger.info("forecast agent done")
        return {
            **state,
            "forecast":      forecast,
            "forecast_data": forecast_data,   # top-level so graph.py can pick it up easily
            "current_step":  "forecast",
        }

    except Exception as e:
        logger.error("forecast agent failed", error=str(e))
        return {**state, "error": str(e), "current_step": "forecast"}