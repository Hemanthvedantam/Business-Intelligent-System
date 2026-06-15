"""
insights_api.py — Advanced Analytics API for ABIP Insights Hub.
Production-grade: TTL caching, richer stats, streaming narrative,
correlation scatter, export endpoint, structured error handling.
"""

import math
import json
import time
import asyncio
import hashlib
import numpy as np
import pandas as pd
from pathlib import Path
from functools import lru_cache
from typing import Optional, Dict, Any
from fastapi import APIRouter, HTTPException, Depends, Query
from fastapi.responses import JSONResponse, StreamingResponse

from app.core.config import settings
from app.core.security import get_current_user
from app.core.logging import get_logger
from app.services.duckdb_service import load_file_as_dataframe

logger = get_logger(__name__)
router = APIRouter()

ALLOWED_EXTENSIONS = {".csv", ".xlsx", ".xls", ".parquet"}

# ─── TTL Cache ────────────────────────────────────────────────────────────────
# Keyed by (filename, endpoint). Invalidated when file mtime changes or TTL expires.
_cache: Dict[str, Dict[str, Any]] = {}
CACHE_TTL_SECONDS = 300  # 5 minutes


def _cache_key(filename: str, endpoint: str) -> str:
    return f"{endpoint}:{filename}"


def _file_mtime(filename: str) -> float:
    try:
        return (Path(settings.UPLOAD_DIR) / filename).stat().st_mtime
    except Exception:
        return 0.0


def cache_get(filename: str, endpoint: str) -> Optional[Any]:
    key = _cache_key(filename, endpoint)
    entry = _cache.get(key)
    if not entry:
        return None
    if time.time() - entry["ts"] > CACHE_TTL_SECONDS:
        del _cache[key]
        return None
    if _file_mtime(filename) > entry["mtime"]:
        del _cache[key]
        return None
    return entry["data"]


def cache_set(filename: str, endpoint: str, data: Any) -> None:
    _cache[_cache_key(filename, endpoint)] = {
        "data":  data,
        "ts":    time.time(),
        "mtime": _file_mtime(filename),
    }


# ─── Helpers ──────────────────────────────────────────────────────────────────

def clean(obj):
    """Recursively replace nan/inf with None for safe JSON serialisation."""
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        v = float(obj)
        return None if (math.isnan(v) or math.isinf(v)) else v
    if isinstance(obj, np.ndarray):
        return clean(obj.tolist())
    if isinstance(obj, dict):
        return {k: clean(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [clean(v) for v in obj]
    return obj


def guard(filename: str) -> pd.DataFrame:
    """Validate file exists, load and return DataFrame."""
    path = Path(settings.UPLOAD_DIR) / filename
    if not path.exists():
        raise HTTPException(404, detail=f"File '{filename}' not found.")
    if path.suffix.lower() not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, detail="Unsupported file type.")
    try:
        return load_file_as_dataframe(filename)
    except Exception as e:
        logger.error("Failed to load dataframe", filename=filename, error=str(e))
        raise HTTPException(500, detail=f"Could not load file: {e}")


def detect_date_col(df: pd.DataFrame) -> Optional[str]:
    """Find first column that looks like a date series."""
    for col in df.columns:
        if df[col].dtype in ["datetime64[ns]", "datetime64[ns, UTC]"]:
            return col
        try:
            parsed = pd.to_datetime(df[col], infer_datetime_format=True, errors="coerce")
            if parsed.notna().sum() / max(len(df), 1) > 0.5:
                return col
        except Exception:
            pass
    return None


def numeric_summary(series: pd.Series) -> dict:
    """Full numeric summary for a single column."""
    s = series.dropna()
    if s.empty:
        return {}
    counts, edges = np.histogram(s, bins=min(20, s.nunique()))
    return {
        "hist_counts": counts.tolist(),
        "hist_edges":  [round(float(e), 4) for e in edges.tolist()],
        "mean":        round(float(s.mean()), 4),
        "median":      round(float(s.median()), 4),
        "std":         round(float(s.std()), 4),
        "min":         round(float(s.min()), 4),
        "max":         round(float(s.max()), 4),
        "q1":          round(float(s.quantile(0.25)), 4),
        "q3":          round(float(s.quantile(0.75)), 4),
        "p5":          round(float(s.quantile(0.05)), 4),
        "p95":         round(float(s.quantile(0.95)), 4),
        "skewness":    round(float(s.skew()), 4),
        "kurtosis":    round(float(s.kurt()), 4),
        "count":       int(s.count()),
        "missing":     int(series.isnull().sum()),
        "cv":          round(float(s.std() / s.mean()), 4) if s.mean() != 0 else None,
    }


# ─── 1. Dataset Overview ──────────────────────────────────────────────────────

@router.get("/overview/{filename}")
async def dataset_overview(
    filename: str,
    current_user: dict = Depends(get_current_user),
):
    cached = cache_get(filename, "overview")
    if cached:
        return JSONResponse(content=cached)

    df = guard(filename)
    numeric_cols     = df.select_dtypes(include="number").columns.tolist()
    categorical_cols = df.select_dtypes(include=["object", "category", "bool"]).columns.tolist()
    date_cols        = df.select_dtypes(include=["datetime", "datetimetz"]).columns.tolist()

    for col in categorical_cols:
        try:
            parsed = pd.to_datetime(df[col], infer_datetime_format=True, errors="coerce")
            if parsed.notna().sum() / max(len(df), 1) > 0.7:
                date_cols.append(col)
        except Exception:
            pass

    missing_total  = int(df.isnull().sum().sum())
    duplicate_rows = int(df.duplicated().sum())
    completeness   = round(100 * (1 - missing_total / max(df.size, 1)), 1)

    columns_meta = []
    for col in df.columns:
        columns_meta.append({
            "name":        col,
            "dtype":       str(df[col].dtype),
            "kind":        ("numeric"     if col in numeric_cols
                            else "date"   if col in date_cols
                            else "categorical"),
            "missing":     int(df[col].isnull().sum()),
            "missing_pct": round(100 * df[col].isnull().sum() / max(len(df), 1), 1),
            "unique":      int(df[col].nunique()),
            "sample":      [str(v) for v in df[col].dropna().head(3).tolist()],
        })

    result = clean({
        "filename":           filename,
        "rows":               len(df),
        "columns":            len(df.columns),
        "numeric_count":      len(numeric_cols),
        "categorical_count":  len(categorical_cols),
        "date_count":         len(date_cols),
        "missing_total":      missing_total,
        "duplicate_rows":     duplicate_rows,
        "completeness":       completeness,
        "memory_kb":          round(df.memory_usage(deep=True).sum() / 1024, 1),
        "columns_meta":       columns_meta,
        "has_date_col":       bool(date_cols or detect_date_col(df)),
    })
    cache_set(filename, "overview", result)
    return JSONResponse(content=result)


# ─── 2. Distribution Analysis ─────────────────────────────────────────────────

@router.get("/distribution/{filename}")
async def distribution_analysis(
    filename: str,
    current_user: dict = Depends(get_current_user),
):
    cached = cache_get(filename, "distribution")
    if cached:
        return JSONResponse(content=cached)

    df = guard(filename)
    result = {"numeric": {}, "categorical": {}}

    for col in df.select_dtypes(include="number").columns:
        result["numeric"][col] = numeric_summary(df[col])

    for col in df.select_dtypes(include=["object", "category", "bool"]).columns:
        vc = df[col].value_counts().head(10)
        result["categorical"][col] = {
            "labels": [str(v) for v in vc.index.tolist()],
            "counts": vc.values.tolist(),
            "unique": int(df[col].nunique()),
            "top_value": str(vc.index[0]) if len(vc) else None,
            "top_pct":   round(100 * vc.iloc[0] / max(len(df), 1), 1) if len(vc) else None,
        }

    result = clean(result)
    cache_set(filename, "distribution", result)
    return JSONResponse(content=result)


# ─── 3. Correlation Analysis ──────────────────────────────────────────────────

@router.get("/correlation/{filename}")
async def correlation_analysis(
    filename: str,
    current_user: dict = Depends(get_current_user),
):
    cached = cache_get(filename, "correlation")
    if cached:
        return JSONResponse(content=cached)

    df = guard(filename)
    num = df.select_dtypes(include="number")

    if len(num.columns) < 2:
        return JSONResponse(content={"message": "Need at least 2 numeric columns."})

    corr    = num.corr(method="pearson").round(3)
    columns = corr.columns.tolist()
    matrix  = corr.values.tolist()

    pairs = []
    for i in range(len(columns)):
        for j in range(i + 1, len(columns)):
            val = corr.iloc[i, j]
            if not math.isnan(val):
                # Build scatter sample for drill-down (max 300 pts)
                sub = num[[columns[i], columns[j]]].dropna().head(300)
                pairs.append({
                    "col_a": columns[i],
                    "col_b": columns[j],
                    "r":     round(val, 3),
                    "r2":    round(val ** 2, 3),
                    "abs_r": round(abs(val), 3),
                    "scatter_x": [round(float(v), 4) for v in sub[columns[i]].tolist()],
                    "scatter_y": [round(float(v), 4) for v in sub[columns[j]].tolist()],
                })

    pairs.sort(key=lambda x: x["abs_r"], reverse=True)
    # Don't include scatter in top_pairs list (too large), reference by col_a/col_b
    top_pairs = [
        {k: v for k, v in p.items() if k not in ("scatter_x", "scatter_y")}
        for p in pairs[:12]
    ]

    result = clean({
        "columns":        columns,
        "matrix":         matrix,
        "top_pairs":      top_pairs,
        "pairs_with_scatter": pairs[:20],   # Full pairs including scatter for drill-down
    })
    cache_set(filename, "correlation", result)
    return JSONResponse(content=result)


# ─── 4. Outlier Detection ─────────────────────────────────────────────────────

@router.get("/outliers/{filename}")
async def outlier_detection(
    filename: str,
    current_user: dict = Depends(get_current_user),
):
    cached = cache_get(filename, "outliers")
    if cached:
        return JSONResponse(content=cached)

    df = guard(filename)
    num = df.select_dtypes(include="number")
    result = {}

    for col in num.columns:
        series = num[col].dropna()
        if series.empty:
            continue
        Q1  = series.quantile(0.25)
        Q3  = series.quantile(0.75)
        IQR = Q3 - Q1
        if IQR == 0:
            continue
        lower = Q1 - 1.5 * IQR
        upper = Q3 + 1.5 * IQR
        mask  = (series < lower) | (series > upper)
        outlier_vals = series[mask]

        if len(outlier_vals) > 0:
            # Z-score for context
            z_scores = ((series - series.mean()) / series.std()).abs()
            severe   = int((z_scores > 3).sum())
            result[col] = {
                "count":        int(len(outlier_vals)),
                "pct":          round(100 * len(outlier_vals) / len(series), 2),
                "severe_count": severe,   # |z| > 3
                "lower_bound":  round(float(lower), 4),
                "upper_bound":  round(float(upper), 4),
                "min_outlier":  round(float(outlier_vals.min()), 4),
                "max_outlier":  round(float(outlier_vals.max()), 4),
                "samples":      [round(float(v), 4) for v in outlier_vals.head(5).tolist()],
                # Histogram of outliers only for spark visual
                "outlier_hist": np.histogram(outlier_vals, bins=min(8, len(outlier_vals)))[0].tolist(),
            }

    result = clean(result)
    cache_set(filename, "outliers", result)
    return JSONResponse(content=result)


# ─── 5. Time-Series Analysis ──────────────────────────────────────────────────

@router.get("/timeseries/{filename}")
async def timeseries_analysis(
    filename: str,
    current_user: dict = Depends(get_current_user),
):
    cached = cache_get(filename, "timeseries")
    if cached:
        return JSONResponse(content=cached)

    df        = guard(filename)
    date_col  = detect_date_col(df)

    if not date_col:
        return JSONResponse(content={"message": "No date column detected in this dataset."})

    df[date_col] = pd.to_datetime(df[date_col], infer_datetime_format=True, errors="coerce")
    value_cols   = df.select_dtypes(include="number").columns.tolist()

    if not value_cols:
        return JSONResponse(content={"message": "No numeric columns for time-series analysis."})

    df_ts = df[[date_col] + value_cols].dropna(subset=[date_col]).sort_values(date_col)
    df_ts.set_index(date_col, inplace=True)

    try:
        monthly = df_ts[value_cols].resample("ME").mean()
    except Exception:
        monthly = df_ts[value_cols].resample("M").mean()

    series_data = {}
    for col in value_cols[:5]:
        vals = monthly[col].dropna()
        if len(vals) < 2:
            continue
        rolling  = vals.rolling(window=3, min_periods=1).mean()
        mom_chg  = vals.pct_change().mul(100).round(2)
        trend    = "up" if vals.iloc[-1] > vals.iloc[0] else "down"
        peak_idx = int(vals.idxmax().strftime("%s")) if hasattr(vals.idxmax(), "strftime") else None

        series_data[col] = {
            "labels":      [str(d.date()) for d in vals.index],
            "values":      [round(float(v), 4) for v in vals.tolist()],
            "rolling_avg": [round(float(v), 4) for v in rolling.tolist()],
            "mom_change":  [clean(v) for v in mom_chg.tolist()],
            "trend":       trend,
            "first_val":   round(float(vals.iloc[0]), 4),
            "last_val":    round(float(vals.iloc[-1]), 4),
            "total_change_pct": round(
                100 * (vals.iloc[-1] - vals.iloc[0]) / abs(vals.iloc[0]), 2
            ) if vals.iloc[0] != 0 else None,
            "peak_label":  str(vals.idxmax().date()),
            "peak_value":  round(float(vals.max()), 4),
            "trough_label": str(vals.idxmin().date()),
            "trough_value": round(float(vals.min()), 4),
        }

    result = clean({
        "date_column":   date_col,
        "value_columns": value_cols[:5],
        "series":        series_data,
        "date_range":    {
            "start": str(df_ts.index.min().date()),
            "end":   str(df_ts.index.max().date()),
            "months": len(monthly),
        },
    })
    cache_set(filename, "timeseries", result)
    return JSONResponse(content=result)


# ─── 6. Clustering (K-Means) ─────────────────────────────────────────────────

@router.get("/clustering/{filename}")
async def clustering_analysis(
    filename: str,
    current_user: dict = Depends(get_current_user),
):
    cached = cache_get(filename, "clustering")
    if cached:
        return JSONResponse(content=cached)

    try:
        from sklearn.cluster import KMeans
        from sklearn.preprocessing import StandardScaler
        from sklearn.metrics import silhouette_score
    except ImportError:
        return JSONResponse(content={"message": "scikit-learn not installed."})

    df  = guard(filename)
    num = df.select_dtypes(include="number").dropna(axis=1, how="all")

    if len(num.columns) < 2:
        return JSONResponse(content={"message": "Need at least 2 numeric columns."})

    cols   = num.columns[:2].tolist()
    sample = num[cols].dropna().head(500)

    if len(sample) < 6:
        return JSONResponse(content={"message": "Not enough data rows for clustering."})

    scaler = StandardScaler()
    scaled = scaler.fit_transform(sample)

    # Auto-select k via silhouette score (try k=2..6)
    best_k, best_score, best_labels = 2, -1, None
    scores = []
    for k in range(2, min(7, len(sample) // 10 + 2)):
        km     = KMeans(n_clusters=k, random_state=42, n_init=10)
        labels = km.fit_predict(scaled)
        try:
            score = silhouette_score(scaled, labels)
        except Exception:
            score = -1
        scores.append({"k": k, "silhouette": round(float(score), 4)})
        if score > best_score:
            best_k, best_score, best_labels = k, score, labels

    km_final = KMeans(n_clusters=best_k, random_state=42, n_init=10)
    km_final.fit(scaled)
    centroids = scaler.inverse_transform(km_final.cluster_centers_)

    # Cluster sizes & centroid stats
    cluster_stats = []
    for i in range(best_k):
        mask = best_labels == i
        cluster_stats.append({
            "id":    i,
            "size":  int(mask.sum()),
            "pct":   round(100 * mask.sum() / len(sample), 1),
        })

    scatter = [
        {
            "x":       round(float(sample.iloc[i][cols[0]]), 4),
            "y":       round(float(sample.iloc[i][cols[1]]), 4),
            "cluster": int(best_labels[i]),
        }
        for i in range(len(sample))
    ]

    result = clean({
        "col_x":        cols[0],
        "col_y":        cols[1],
        "k":            best_k,
        "silhouette":   round(float(best_score), 4),
        "k_scores":     scores,
        "scatter":      scatter,
        "cluster_stats": cluster_stats,
        "centroids":    [
            {"x": round(float(c[0]), 4), "y": round(float(c[1]), 4)}
            for c in centroids
        ],
    })
    cache_set(filename, "clustering", result)
    return JSONResponse(content=result)


# ─── 7. Feature Importance ────────────────────────────────────────────────────

@router.get("/feature-importance/{filename}")
async def feature_importance(
    filename: str,
    target: str = Query(default=""),
    current_user: dict = Depends(get_current_user),
):
    cache_key = f"fi:{target}"
    cached = cache_get(filename, cache_key)
    if cached:
        return JSONResponse(content=cached)

    try:
        from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
        from sklearn.model_selection import cross_val_score
    except ImportError:
        return JSONResponse(content={"message": "scikit-learn not installed."})

    df  = guard(filename)
    num = df.select_dtypes(include="number").dropna()

    if len(num.columns) < 2:
        return JSONResponse(content={"message": "Need at least 2 numeric columns."})

    target_col   = target if (target and target in num.columns) else num.columns[-1]
    feature_cols = [c for c in num.columns if c != target_col]
    X = num[feature_cols].fillna(0).head(1000)
    y = num[target_col].fillna(0).head(1000)

    is_clf = y.nunique() <= 10 and y.nunique() >= 2
    model  = (RandomForestClassifier(n_estimators=50, random_state=42, max_depth=8)
              if is_clf else
              RandomForestRegressor(n_estimators=50, random_state=42, max_depth=8))

    model.fit(X, y)

    # Cross-val score for model quality indicator
    try:
        scoring = "accuracy" if is_clf else "r2"
        cv_scores = cross_val_score(model, X, y, cv=3, scoring=scoring)
        model_score = round(float(cv_scores.mean()), 4)
    except Exception:
        model_score = None

    importances = sorted(
        zip(feature_cols, model.feature_importances_),
        key=lambda x: x[1], reverse=True
    )

    result = clean({
        "target":        target_col,
        "task_type":     "classification" if is_clf else "regression",
        "model_score":   model_score,
        "scoring":       scoring if model_score is not None else None,
        "n_classes":     int(y.nunique()) if is_clf else None,
        "features":      [
            {"name": name, "importance": round(float(imp), 4)}
            for name, imp in importances
        ],
    })
    cache_set(filename, cache_key, result)
    return JSONResponse(content=result)


# ─── 8. Correlation Scatter Drill-down ────────────────────────────────────────

@router.get("/correlation-scatter/{filename}")
async def correlation_scatter(
    filename: str,
    col_a: str = Query(...),
    col_b: str = Query(...),
    current_user: dict = Depends(get_current_user),
):
    """Return scatter + linear regression line for a pair of columns."""
    df  = guard(filename)
    num = df.select_dtypes(include="number")

    for col in [col_a, col_b]:
        if col not in num.columns:
            raise HTTPException(400, detail=f"Column '{col}' not found or not numeric.")

    sub = num[[col_a, col_b]].dropna().head(400)
    x   = sub[col_a].values
    y   = sub[col_b].values

    # Linear regression
    try:
        coeffs = np.polyfit(x, y, 1)
        x_line = [float(x.min()), float(x.max())]
        y_line = [float(np.polyval(coeffs, v)) for v in x_line]
        slope  = round(float(coeffs[0]), 4)
    except Exception:
        x_line, y_line, slope = [], [], None

    r = float(np.corrcoef(x, y)[0, 1])

    return JSONResponse(content=clean({
        "col_a":   col_a,
        "col_b":   col_b,
        "r":       round(r, 3),
        "r2":      round(r ** 2, 3),
        "slope":   slope,
        "points":  [{"x": round(float(a), 4), "y": round(float(b), 4)}
                    for a, b in zip(x.tolist(), y.tolist())],
        "line_x":  [round(v, 4) for v in x_line],
        "line_y":  [round(v, 4) for v in y_line],
        "n":       len(sub),
    }))


# ─── 9. Full Export ───────────────────────────────────────────────────────────

@router.get("/export-summary/{filename}")
async def export_summary(
    filename: str,
    current_user: dict = Depends(get_current_user),
):
    """
    Returns a single JSON combining all cached analytics for download.
    Only returns what's already been computed — no on-demand recompute.
    """
    sections = ["overview", "distribution", "correlation", "outliers", "timeseries", "clustering"]
    result   = {"filename": filename, "exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    for section in sections:
        cached = cache_get(filename, section)
        if cached:
            result[section] = cached

    if len(result) == 2:   # only filename + exported_at = nothing computed yet
        raise HTTPException(400, detail="No cached analytics found. Load the Insights page first.")

    return JSONResponse(content=result)


# ─── 10. AI Narrative (with optional streaming) ───────────────────────────────

@router.get("/ai-narrative/{filename}")
async def ai_narrative(
    filename: str,
    stream: bool = Query(default=False),
    current_user: dict = Depends(get_current_user),
):
    df  = guard(filename)
    num = df.select_dtypes(include="number")

    # Build richer digest
    digest: Dict[str, Any] = {
        "filename":       filename,
        "rows":           len(df),
        "columns":        len(df.columns),
        "numeric_columns": num.columns.tolist(),
        "categorical_columns": df.select_dtypes(include=["object", "category"]).columns.tolist(),
        "missing_pct":    round(100 * df.isnull().sum().sum() / max(df.size, 1), 1),
        "duplicate_rows": int(df.duplicated().sum()),
        "stats":          {},
        "top_correlations": [],
    }

    for col in num.columns[:8]:
        s = num[col].dropna()
        if s.empty:
            continue
        digest["stats"][col] = {
            "mean":     round(float(s.mean()), 3),
            "std":      round(float(s.std()), 3),
            "skewness": round(float(s.skew()), 3),
            "kurtosis": round(float(s.kurt()), 3),
            "min":      round(float(s.min()), 3),
            "max":      round(float(s.max()), 3),
            "q1":       round(float(s.quantile(0.25)), 3),
            "q3":       round(float(s.quantile(0.75)), 3),
        }

    # Add correlation highlights to digest
    if len(num.columns) >= 2:
        corr = num.corr().round(3)
        cols = corr.columns.tolist()
        for i in range(min(len(cols), 6)):
            for j in range(i + 1, min(len(cols), 6)):
                val = corr.iloc[i, j]
                if not math.isnan(val) and abs(val) > 0.4:
                    digest["top_correlations"].append(
                        {"cols": f"{cols[i]} × {cols[j]}", "r": round(float(val), 3)}
                    )

    prompt = f"""You are a senior data analyst producing an executive-level data briefing.
Analyse the dataset digest below and produce precise, actionable, non-generic insights.

Dataset Digest:
{json.dumps(digest, indent=2)}

Rules:
- Be specific: reference actual column names and numbers from the digest.
- Flag real anomalies: high skewness (|skew|>1), high CV (>0.5), extreme kurtosis, high missing%, strong correlations.
- Recommendations must be concrete, not vague.
- Do NOT say "the data appears well-distributed" unless it actually does.

Return ONLY a valid JSON object with exactly these keys:
{{
  "summary": "2-sentence plain-English summary of what this dataset is and its key characteristic",
  "key_insights": ["emoji + specific insight with numbers...", ...],  // 4-6 items
  "data_quality": ["emoji + specific quality observation...", ...],   // 2-4 items
  "recommendations": ["emoji + specific actionable next step...", ...] // 3-5 items
}}

No markdown. No code fences. No extra text outside the JSON."""

    # ── Streaming response ────────────────────────────────────────────────────
    if stream:
        async def event_stream():
            try:
                from app.providers.factory import get_provider
                provider = get_provider()
                # Some providers support streaming; if not, we simulate it
                raw = await provider.complete(prompt, max_tokens=1000)
                clean_raw = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
                narrative = json.loads(clean_raw)
                # Stream section by section
                for section, key in [
                    ("summary", "summary"),
                    ("key_insights", "key_insights"),
                    ("data_quality", "data_quality"),
                    ("recommendations", "recommendations"),
                ]:
                    data = {key: narrative.get(key, "")}
                    yield f"data: {json.dumps(data)}\n\n"
                    await asyncio.sleep(0.05)
                yield "data: [DONE]\n\n"
            except Exception as e:
                logger.warning("Streaming narrative failed", error=str(e))
                fallback = _rule_based_narrative(df, digest)
                yield f"data: {json.dumps(fallback)}\n\n"
                yield "data: [DONE]\n\n"

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    # ── Regular JSON response ─────────────────────────────────────────────────
    narrative = None
    try:
        from app.providers.factory import get_provider
        provider  = get_provider()
        raw       = await provider.complete(prompt, max_tokens=1000)
        clean_raw = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        narrative = json.loads(clean_raw)
    except Exception as e:
        logger.warning("LLM narrative failed, using rule-based fallback", error=str(e))

    if not narrative:
        narrative = _rule_based_narrative(df, digest)

    return JSONResponse(content=narrative)


def _rule_based_narrative(df: pd.DataFrame, digest: dict) -> dict:
    """Produce a smart rule-based narrative when the LLM is unavailable."""
    num      = df.select_dtypes(include="number")
    insights = []
    quality  = []
    recs     = []

    for col, stat in digest.get("stats", {}).items():
        skew = stat.get("skewness", 0) or 0
        cv   = (abs(stat.get("std", 0)) / abs(stat.get("mean", 1))
                if stat.get("mean") else 0)
        kurt = stat.get("kurtosis", 0) or 0

        if abs(skew) > 1.5:
            direction = "right (positive)" if skew > 0 else "left (negative)"
            insights.append(
                f"📊 **{col}** is strongly {direction}-skewed (skew={skew}), "
                f"suggesting the presence of outliers or a non-normal distribution."
            )
        if cv > 0.8:
            insights.append(
                f"⚡ **{col}** shows very high variability (CV={round(cv, 2)}), "
                f"ranging from {stat.get('min')} to {stat.get('max')}."
            )
        if kurt > 3:
            insights.append(
                f"🔺 **{col}** has heavy tails (kurtosis={kurt}), indicating frequent extreme values."
            )

    for corr in digest.get("top_correlations", [])[:3]:
        direction = "positive" if corr["r"] > 0 else "negative"
        insights.append(
            f"🔗 Strong {direction} correlation (r={corr['r']}) between {corr['cols']}."
        )

    if not insights:
        insights.append("📋 No strong statistical anomalies detected across numeric columns.")

    missing_pct = digest.get("missing_pct", 0)
    quality.append(
        f"{'⚠️' if missing_pct > 5 else '✅'} Missing data: {missing_pct}% of all values are null."
    )
    dups = digest.get("duplicate_rows", 0)
    quality.append(
        f"{'⚠️' if dups > 0 else '✅'} {dups} duplicate row(s) found."
    )
    if missing_pct > 20:
        quality.append("🚨 High missing rate — imputation or column exclusion is strongly recommended before modelling.")

    recs = [
        "🔍 Investigate columns with >20% missing values before feature engineering.",
        "📈 Use the Correlation tab to identify strongly predictive feature pairs.",
        "🤖 Open an Investigation in the Chat panel for deep root-cause analysis.",
        "📤 Export the full summary JSON for offline analysis or report generation.",
    ]

    return {
        "summary": (
            f"Dataset '{digest['filename']}' contains {digest['rows']:,} rows × "
            f"{digest['columns']} columns with {missing_pct}% missing values and "
            f"{len(digest.get('numeric_columns', []))} numeric feature(s)."
        ),
        "key_insights":    insights[:6],
        "data_quality":    quality,
        "recommendations": recs,
    }