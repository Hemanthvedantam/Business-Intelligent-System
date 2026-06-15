# This service runs automatically after every file upload.
# It checks the data for problems before any agent touches it.
# It also generates a natural-language intelligence layer via LLM.

import json
from app.services.duckdb_service import (
    get_basic_info,
    get_missing_values_report,
    get_outliers_report,
    get_statistics,
    get_correlations,
    load_file_as_dataframe,
    get_quality_root_causes
)


from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


# ─────────────────────────────────────────
#  QUALITY CHECK  (unchanged contract)
# ─────────────────────────────────────────
def run_data_quality_check(filename: str) -> dict:
    """
    Run structural quality checks on an uploaded file.
    Returns a quality report dict consumed by the dashboard and profile panel.

    Now includes `root_cause_explanations`: plain-English sentences explaining
    WHY missing values / outliers are concentrated where they are, derived
    dynamically from correlations with other columns in the same file.
    """
    logger.info("running data quality check", filename=filename)

    basic_info = get_basic_info(filename)
    missing    = get_missing_values_report(filename)
    outliers   = get_outliers_report(filename)
    df         = load_file_as_dataframe(filename)
    dup_count  = int(df.duplicated().sum())

    score  = 100
    issues = []
    root_cause_explanations = []

    # Run root-cause correlation analysis only if there's something to explain
    causes = {"missing_value_causes": {}, "outlier_causes": {}}
    if missing or outliers:
        try:
            causes = get_quality_root_causes(filename, missing, outliers)
        except Exception as e:
            logger.error("quality root cause analysis failed", error=str(e), filename=filename)

    if missing:
        score -= len(missing) * 5
        issues.append(f"{len(missing)} columns have missing values")
        for col, explanations in causes.get("missing_value_causes", {}).items():
            root_cause_explanations.extend(explanations)

    if dup_count > 0:
        score -= 10
        issues.append(f"{dup_count} duplicate rows found")

    if outliers:
        score -= len(outliers) * 3
        issues.append(f"{len(outliers)} columns have outliers")
        for col, explanations in causes.get("outlier_causes", {}).items():
            root_cause_explanations.extend(explanations)

    score = max(0, min(100, score))

    report = {
        "filename":                filename,
        "quality_score":           score,
        "can_proceed":             score >= 50,
        "total_rows":              basic_info["total_rows"],
        "total_columns":           basic_info["total_columns"],
        "duplicate_rows":          dup_count,
        "missing_values":          missing,
        "outliers":                outliers,
        "issues":                  issues,
        "root_cause_explanations": root_cause_explanations,   # NEW
        "columns":                 basic_info["columns"],
        "preview":                 basic_info["preview"],
    }

    logger.info("data quality check done", filename=filename, score=score, root_causes=len(root_cause_explanations))
    return report

# ─────────────────────────────────────────
#  NLP INTELLIGENCE LAYER
# ─────────────────────────────────────────
def generate_nlp_intelligence(filename: str) -> dict:
    """
    Generate a full natural-language intelligence report for a dataset.

    Sections returned:
      - summary          : one-paragraph plain-English description of the dataset
      - business_context : what business domain this data likely represents
      - key_insights     : list[str] — 4-6 specific, numbered findings
      - trend_narrative  : plain-English explanation of the main trend
      - anomaly_narrative: plain-English explanation of any anomalies / outliers
      - risk_signals     : list[str] — concrete risk flags with business impact
      - opportunities    : list[str] — growth or optimisation opportunities
      - column_stories   : list[dict] — per-column natural language meaning
      - data_quality_narrative : plain-English quality assessment
    """
    logger.info("generating NLP intelligence", filename=filename)

    # ── Gather raw inputs ──────────────────────────────────────────────
    basic_info   = get_basic_info(filename)
    stats        = get_statistics(filename)
    correlations = get_correlations(filename)
    missing      = get_missing_values_report(filename)
    outliers     = get_outliers_report(filename)
    df           = load_file_as_dataframe(filename)

    # Build a compact text snapshot for the LLM
    col_types = {c["name"]: c["type"] for c in basic_info["columns"]}
    numeric_cols = [c["name"] for c in basic_info["columns"]
                    if c["type"] in ("float64", "int64", "int32", "float32")]
    cat_cols     = [c["name"] for c in basic_info["columns"] if c["type"] == "object"]

    # Compute top correlations for context
    top_corrs = []
    if isinstance(correlations, dict):
        for col_a, pairs in correlations.items():
            if not isinstance(pairs, dict):
                continue
            for col_b, val in pairs.items():
                if col_a != col_b and isinstance(val, (int, float)):
                    top_corrs.append((col_a, col_b, round(val, 3)))
        top_corrs = sorted(top_corrs, key=lambda x: abs(x[2]), reverse=True)[:6]

    data_snapshot = f"""
DATASET: {filename}
Rows: {basic_info['total_rows']} | Columns: {basic_info['total_columns']}
Numeric columns ({len(numeric_cols)}): {numeric_cols}
Categorical columns ({len(cat_cols)}): {cat_cols}
Missing values: {json.dumps(missing) if missing else 'None'}
Outlier columns: {list(outliers.keys()) if outliers else 'None'}
Duplicate rows: {int(df.duplicated().sum())}
Top correlations: {top_corrs}
Sample data (first 5 rows): {basic_info['preview']}
Statistics summary: {json.dumps({k: {sk: sv for sk, sv in v.items() if sk in ('mean','min','max','std')} for k, v in (stats if isinstance(stats, dict) else {}).items()}) if isinstance(stats, dict) else 'N/A'}
""".strip()

    prompt = f"""You are an expert business data analyst. Analyze the dataset below and produce a structured JSON intelligence report. Be SPECIFIC — use actual column names and real numbers from the data.

{data_snapshot}

Return ONLY a valid JSON object (no markdown, no commentary) with this exact schema:
{{
  "summary": "One clear paragraph describing what this dataset contains and what it measures.",
  "business_context": "What business domain or industry this data represents and what decisions it supports.",
  "key_insights": [
    "Insight 1 with specific numbers and column names",
    "Insight 2 with specific numbers and column names",
    "Insight 3 with specific numbers and column names",
    "Insight 4 with specific numbers and column names"
  ],
  "trend_narrative": "Plain English explanation of the main trend visible in the data — mention direction, magnitude and which columns drive it.",
  "anomaly_narrative": "Specific description of any anomalies, outliers or unusual patterns found. If none, say what is normal.",
  "risk_signals": [
    "Risk 1: description with business impact",
    "Risk 2: description with business impact"
  ],
  "opportunities": [
    "Opportunity 1: specific actionable suggestion",
    "Opportunity 2: specific actionable suggestion"
  ],
  "column_stories": [
    {{"column": "col_name", "meaning": "What this column represents in business terms", "notable": "Any notable value or pattern"}}
  ],
  "data_quality_narrative": "Plain English assessment of data reliability, completeness and fitness for analysis."
}}"""

    try:
        import httpx, os

        api_key  = settings.GROQ_API_KEY
        provider = settings.LLM_PROVIDER  # "groq" by default

        if provider == "groq" and api_key:
            response = httpx.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type":  "application/json",
                },
                json={
                    "model":       "llama-3.3-70b-versatile",
                    "messages":    [{"role": "user", "content": prompt}],
                    "temperature": 0.3,
                    "max_tokens":  2000,
                },
                timeout=45.0,
            )
            response.raise_for_status()
            raw = response.json()["choices"][0]["message"]["content"].strip()

            # Strip any accidental markdown fences
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            raw = raw.strip()

            intelligence = json.loads(raw)
            intelligence["generated"] = True
            logger.info("NLP intelligence generated", filename=filename)
            return intelligence

    except Exception as e:
        logger.error("NLP intelligence generation failed", error=str(e), filename=filename)

    # ── Fallback: rule-based intelligence (no LLM required) ───────────
    return _rule_based_intelligence(filename, basic_info, stats, missing, outliers, top_corrs, df)


def _rule_based_intelligence(filename, basic_info, stats, missing, outliers, top_corrs, df) -> dict:
    """
    Deterministic fallback when the LLM call fails.
    Produces reasonable intelligence from pure statistics.
    """
    rows    = basic_info["total_rows"]
    cols    = basic_info["total_columns"]
    columns = basic_info["columns"]

    numeric_cols = [c["name"] for c in columns if c["type"] in ("float64", "int64", "int32", "float32")]
    cat_cols     = [c["name"] for c in columns if c["type"] == "object"]

    # Key insights from stats
    insights = []
    if isinstance(stats, dict):
        for col, col_stats in list(stats.items())[:3]:
            mean = col_stats.get("mean")
            mn   = col_stats.get("min")
            mx   = col_stats.get("max")
            if mean is not None and mn is not None and mx is not None:
                insights.append(
                    f"{col}: mean={round(mean,2)}, range=[{round(mn,2)}, {round(mx,2)}]"
                )

    if missing:
        col_list = ", ".join(list(missing.keys())[:3])
        insights.append(f"Data gaps found in: {col_list} — review before analysis")

    if top_corrs:
        a, b, v = top_corrs[0]
        direction = "positively" if v > 0 else "negatively"
        insights.append(f"Strongest relationship: {a} is {direction} correlated with {b} (r={v})")

    if len(insights) < 4:
        insights.append(f"Dataset has {rows:,} rows across {cols} columns — adequate for statistical analysis")

    # Column stories
    column_stories = []
    for c in columns[:8]:
        col_stats = stats.get(c["name"], {}) if isinstance(stats, dict) else {}
        notable   = ""
        if col_stats:
            mean = col_stats.get("mean")
            if mean is not None:
                notable = f"Average value: {round(mean, 2)}"
        elif c["type"] == "object":
            try:
                unique = df[c["name"]].nunique()
                notable = f"{unique} unique values"
            except Exception:
                notable = "Categorical field"

        column_stories.append({
            "column":  c["name"],
            "meaning": f"Measures {c['name'].replace('_', ' ').lower()} — {c['type']} type",
            "notable": notable or "—",
        })

    risk_signals = []
    if missing:
        risk_signals.append(f"Missing data in {len(missing)} column(s) may skew analysis results")
    if outliers:
        risk_signals.append(f"Outliers detected in {len(outliers)} column(s) — validate before modelling")
    dup_count = int(df.duplicated().sum())
    if dup_count > 0:
        risk_signals.append(f"{dup_count} duplicate rows could inflate aggregated metrics")
    if not risk_signals:
        risk_signals.append("No critical data risks detected — dataset appears analysis-ready")

    return {
        "summary": (
            f"This dataset contains {rows:,} records across {cols} columns, "
            f"with {len(numeric_cols)} numeric and {len(cat_cols)} categorical fields. "
            f"It appears to track {', '.join(c['name'].replace('_',' ') for c in columns[:3])} "
            f"among other attributes."
        ),
        "business_context": (
            f"Based on column names and data types, this dataset likely supports "
            f"operational or analytical reporting. Key measurable fields include: "
            f"{', '.join(numeric_cols[:4]) or 'none identified'}."
        ),
        "key_insights":   insights[:6],
        "trend_narrative": (
            f"No clear time axis detected. Numeric distributions suggest "
            f"{'high variability' if len(outliers) > 2 else 'relatively stable values'} "
            f"across the dataset. Review outlier columns for trend drivers."
        ),
        "anomaly_narrative": (
            f"{len(outliers)} column(s) contain statistical outliers: "
            f"{', '.join(list(outliers.keys())[:4]) if outliers else 'none'}. "
            f"{'Investigate these before drawing conclusions.' if outliers else 'Data appears consistent.'}"
        ),
        "risk_signals":   risk_signals,
        "opportunities": [
            f"Cross-analyse {numeric_cols[0]} and {numeric_cols[1]} to find performance levers"
            if len(numeric_cols) >= 2 else "Add more numeric columns for deeper analysis",
            f"Segment by {cat_cols[0]} to identify group-level differences"
            if cat_cols else "Add categorical grouping columns for segmentation analysis",
        ],
        "column_stories":          column_stories,
        "data_quality_narrative": (
            f"Quality score is based on missing values ({len(missing)} columns affected), "
            f"duplicates ({dup_count} rows), and outliers ({len(outliers)} columns). "
            f"{'Dataset is fit for analysis.' if not missing and not outliers else 'Review flagged issues before production use.'}"
        ),
        "generated": False,
    }