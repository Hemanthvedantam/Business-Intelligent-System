"""
Data Analyst Agent
------------------
Runs SQL / statistical analysis on the uploaded file and produces
structured chart-ready payloads consumed by the frontend.

Chart outputs:
  trend_data        → line chart  (time-series or ordered numeric)
  correlation_data  → horizontal bar chart (correlations with best target col)
  distribution_data → bar chart  (full categorical counts via DuckDB — not just preview)
  anomaly_data      → annotated list of outlier rows for the chart overlay

Production upgrades vs original:
  - Smart correlation target: picks the column most semantically relevant
    to the question keywords, not just the first column.
  - Anomaly detection (z-score) layered on top of trend data.
  - distribution_data uses a real DuckDB GROUP BY query (accurate counts).
  - Token budget: data_summary is hard-capped so huge datasets don't blow context.
  - trend_data includes moving-average series for smoother chart rendering.
  - trend_classifier() classification included in analyst_summary.
  - retry_llm() wrapper for resilience.
  - All chart payloads include a `chart_type` hint for the frontend.
"""

from __future__ import annotations
import asyncio

from app.providers.factory import get_provider
from app.services.duckdb_service import (
    get_basic_info,
    get_statistics,
    get_correlations,
    get_missing_values_report,
    run_query,                     # assumed to exist — runs arbitrary SQL
)
from app.core.logging import get_logger
from app.agents.agent_utils import (
    retry_llm,
    truncate_for_context,
    anomaly_scores,
    trend_classifier,
    moving_average,
    build_context_block,
)

logger = get_logger(__name__)

# Max tokens budgeted to the data summary sent to the LLM
_DATA_SUMMARY_MAX_TOKENS = 3200


# ─────────────────────────────────────────────────────────────────────────────
#  Trend data builder
# ─────────────────────────────────────────────────────────────────────────────
def _build_trend_data(basic_info: dict, _stats: dict) -> dict | None:
    """
    Build a trend chart payload from the dataset preview.
    Adds:
      - moving_average series (3-period)
      - anomaly_points list
      - trend classification metadata
    """
    columns = basic_info.get("columns", [])
    preview = basic_info.get("preview", [])

    if not preview:
        return None

    # Find date/time column
    date_col: str | None = None
    for c in columns:
        name_l = c["name"].lower()
        if c.get("type", "").startswith("datetime") or any(
            k in name_l for k in ("date", "time", "month", "year", "week", "period", "quarter")
        ):
            date_col = c["name"]
            break

    # Find first numeric column (skip date col)
    num_col: str | None = None
    for c in columns:
        if c.get("type", "") in ("float64", "int64", "double", "float32") and c["name"] != date_col:
            num_col = c["name"]
            break

    if not num_col:
        return None

    labels: list[str]  = []
    values: list[float] = []
    for row in preview:
        label = str(row.get(date_col, "")) if date_col else str(len(labels) + 1)
        val   = row.get(num_col)
        try:
            values.append(float(val))
            labels.append(label[:10])
        except (TypeError, ValueError):
            pass

    if len(values) < 2:
        return None

    ma_values    = moving_average(values, window=3)
    anomalies    = anomaly_scores(values)
    trend_meta   = trend_classifier(values)

    return {
        "labels":          labels,
        "values":          values,
        "moving_average":  ma_values,
        "series_label":    num_col,
        "y_label":         num_col,
        "anomaly_points":  anomalies,
        "trend":           trend_meta,
        "chart_type":      "line",
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Correlation data builder — smart target selection
# ─────────────────────────────────────────────────────────────────────────────
_TARGET_KEYWORDS = {
    "sales":    ["revenue", "sales", "amount", "price", "total", "income", "gmv"],
    "finance":  ["profit", "revenue", "cost", "expense", "margin", "income", "loss"],
    "hr":       ["salary", "tenure", "score", "rating", "performance", "attrition"],
    "ops":      ["delay", "time", "duration", "count", "quantity", "units", "defect"],
    "general":  ["value", "amount", "total", "count", "score"],
}

def _pick_correlation_target(correlations: dict, question: str, domain_tag: str) -> str | None:
    """
    Pick the best target column from correlations based on:
    1. Question keywords (highest priority — does a col name appear in the question?)
    2. Domain keyword list (medium priority)
    3. Column with highest average absolute correlation (fallback)
    """
    if not correlations:
        return None

    col_names = list(correlations.keys())
    q_lower   = question.lower()

    # 1. Question mention
    for col in col_names:
        if col.lower() in q_lower or any(word in q_lower for word in col.lower().split("_")):
            return col

    # 2. Domain keywords
    kw_list = _TARGET_KEYWORDS.get(domain_tag, _TARGET_KEYWORDS["general"])
    for col in col_names:
        if any(kw in col.lower() for kw in kw_list):
            return col

    # 3. Highest mean absolute correlation (most "central" column)
    best_col, best_score = None, -1.0
    for col, pairs in correlations.items():
        if not isinstance(pairs, dict):
            continue
        abs_vals = [abs(v) for v in pairs.values() if isinstance(v, (int, float)) and v != 1.0]
        if abs_vals:
            score = sum(abs_vals) / len(abs_vals)
            if score > best_score:
                best_score, best_col = score, col

    return best_col


def _build_correlation_data(correlations: dict, question: str, domain_tag: str) -> dict | None:
    """Build correlation chart payload with smart target column selection."""
    if not correlations or not isinstance(correlations, dict):
        return None

    target = _pick_correlation_target(correlations, question, domain_tag)
    if not target:
        return None

    pairs = correlations.get(target, {})
    if not isinstance(pairs, dict):
        return None

    sorted_pairs = sorted(
        [(k, v) for k, v in pairs.items() if k != target and isinstance(v, (int, float))],
        key=lambda x: abs(x[1]),
        reverse=True,
    )[:10]

    if not sorted_pairs:
        return None

    labels = [p[0] for p in sorted_pairs]
    values = [round(p[1], 3) for p in sorted_pairs]

    # Classify strength of top correlation
    top_abs = abs(values[0]) if values else 0
    strength = "strong" if top_abs > 0.7 else ("moderate" if top_abs > 0.4 else "weak")

    return {
        "labels":           labels,
        "values":           values,
        "target":           target,
        "top_correlation":  values[0] if values else 0,
        "strength":         strength,
        "chart_type":       "bar_horizontal",
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Distribution data builder — full DuckDB GROUP BY (not just preview)
# ─────────────────────────────────────────────────────────────────────────────
def _build_distribution_data(basic_info: dict, filename: str) -> dict | None:
    """
    Pick the first categorical column and run a real GROUP BY via DuckDB
    so that counts reflect the FULL dataset, not just the preview rows.
    Falls back to preview-based counting if DuckDB query fails.
    """
    columns = basic_info.get("columns", [])
    cat_col: str | None = None
    for c in columns:
        if c.get("type", "") in ("object", "string", "varchar", "text", "category"):
            cat_col = c["name"]
            break

    if not cat_col:
        return None

    # Try full DuckDB query first
    try:
        sql = (
            f'SELECT "{cat_col}", COUNT(*) AS cnt '
            f'FROM read_csv_auto(\'{filename}\') '   # DuckDB handles CSV/Parquet
            f'GROUP BY "{cat_col}" '
            f'ORDER BY cnt DESC '
            f'LIMIT 15'
        )
        rows = run_query(filename, sql)
        if rows:
            items = [(str(r[cat_col]), int(r["cnt"])) for r in rows if cat_col in r and "cnt" in r]
            if items:
                return {
                    "labels":       [i[0] for i in items],
                    "values":       [i[1] for i in items],
                    "series_label": cat_col,
                    "y_label":      "Count",
                    "title":        f"Distribution — {cat_col}",
                    "chart_type":   "bar",
                    "full_dataset": True,
                }
    except Exception as exc:
        logger.warning("distribution DuckDB query failed, falling back to preview", error=str(exc))

    # Fallback: preview-based
    preview = basic_info.get("preview", [])
    counts: dict[str, int] = {}
    for row in preview:
        val = str(row.get(cat_col, ""))
        counts[val] = counts.get(val, 0) + 1

    if not counts:
        return None

    sorted_items = sorted(counts.items(), key=lambda x: x[1], reverse=True)[:10]
    return {
        "labels":       [i[0] for i in sorted_items],
        "values":       [i[1] for i in sorted_items],
        "series_label": cat_col,
        "y_label":      "Count",
        "title":        f"Distribution — {cat_col}",
        "chart_type":   "bar",
        "full_dataset": False,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Main Agent
# ─────────────────────────────────────────────────────────────────────────────
async def data_analyst_agent(state: dict) -> dict:
    logger.info("data analyst agent starting", filename=state["filename"])

    provider   = get_provider()
    filename   = state["filename"]
    question   = state["question"]
    domain_raw = state.get("domain", {})
    domain_tag = domain_raw.get("tag", "general") if isinstance(domain_raw, dict) else str(domain_raw)

    try:
        # ── Gather raw data (all fast local calls) ───────────────────────────
        basic_info, stats, correlations, missing = await asyncio.gather(
            asyncio.to_thread(get_basic_info, filename),
            asyncio.to_thread(get_statistics, filename),
            asyncio.to_thread(get_correlations, filename),
            asyncio.to_thread(get_missing_values_report, filename),
        )

        # ── Build chart payloads (pure Python, no LLM) ───────────────────────
        trend_data        = _build_trend_data(basic_info, stats)
        correlation_data  = _build_correlation_data(correlations, question, domain_tag)
        distribution_data = _build_distribution_data(basic_info, filename)

        # ── Build anomaly summary for LLM context ────────────────────────────
        anomaly_summary = ""
        if trend_data and trend_data.get("anomaly_points"):
            pts = trend_data["anomaly_points"]
            anomaly_summary = (
                f"\nAnomalies detected (z-score ≥ 2.5): {len(pts)} point(s). "
                + "; ".join(
                    f"Index {p['index']}: {p['value']} (z={p['z_score']}, {p['direction']})"
                    for p in pts[:3]
                )
            )

        trend_summary = ""
        if trend_data and trend_data.get("trend"):
            t = trend_data["trend"]
            trend_summary = (
                f"\nTrend classification: {t['classification']} "
                f"(slope {t['slope_pct']:+.1f}%, volatility {t['volatility']:.2f})"
            )

        # ── Compose token-safe data summary ──────────────────────────────────
        col_names = [c["name"] for c in basic_info.get("columns", [])]
        num_cols  = [c for c in basic_info.get("columns", []) if c.get("type") in ("float64", "int64", "double")]

        raw_summary = (
            f"Dataset: {filename}\n"
            f"Rows: {basic_info.get('total_rows', '?')} | "
            f"Columns: {basic_info.get('total_columns', '?')}\n"
            f"Column names: {col_names}\n"
            f"Missing values: {missing}\n"
            f"{anomaly_summary}"
            f"{trend_summary}\n\n"
            + build_context_block("Statistics", stats, max_tokens=800)
            + "\n\n"
            + build_context_block("Correlations", correlations, max_tokens=600)
            + "\n\n"
            + build_context_block("Sample rows (first 10)", basic_info.get("preview", [])[:10], max_tokens=600)
        )
        data_summary = truncate_for_context(raw_summary, max_tokens=_DATA_SUMMARY_MAX_TOKENS)

        # ── LLM analysis ─────────────────────────────────────────────────────
        system = """You are an expert data analyst with deep knowledge of statistics and business intelligence.
Analyse the provided dataset information and answer the user's question with precise, evidence-backed findings.

Rules:
- Mention SPECIFIC column names, values, and percentages from the data summary.
- Highlight any anomalies or unexpected patterns you see.
- Note missing-data issues that might affect conclusions.
- Do NOT invent numbers — use only what the data summary contains.
- Structure your answer: (1) Key finding, (2) Supporting evidence, (3) Data limitations."""

        findings_text = await retry_llm(
            provider,
            system=system,
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Question: {question}\n"
                        f"Investigation plan: {truncate_for_context(state.get('plan', ''), 400, preserve_end=False)}\n\n"
                        f"Data Summary:\n{data_summary}\n\n"
                        "Provide your data analysis findings."
                    ),
                }
            ],
            tag="data_analyst",
        )

        # ── Build output summary for UI node chip ────────────────────────────
        trend_cls   = trend_data["trend"]["classification"] if trend_data and trend_data.get("trend") else "n/a"
        n_anomalies = len(trend_data.get("anomaly_points", [])) if trend_data else 0
        output_summary = (
            f"{len(num_cols)} numeric cols · "
            f"{basic_info.get('total_rows', '?')} rows · "
            f"trend={trend_cls}"
            + (f" · {n_anomalies} anomaly" if n_anomalies else "")
        )

        data_findings = {
            "analysis":          findings_text,
            "basic_info":        basic_info,
            "stats":             stats,
            "correlations":      correlations,
            "missing_values":    missing,
            "trend_data":        trend_data,
            "correlation_data":  correlation_data,
            "distribution_data": distribution_data,
        }

        logger.info("data analyst agent done", output_summary=output_summary)
        return {
            **state,
            "data_findings":     data_findings,
            "trend_data":        trend_data,
            "correlation_data":  correlation_data,
            "distribution_data": distribution_data,
            "analyst_summary":   output_summary,
            "current_step":      "data_analyst",
        }

    except Exception as exc:
        logger.error("data analyst agent failed", error=str(exc))
        return {**state, "error": str(exc), "current_step": "data_analyst"}