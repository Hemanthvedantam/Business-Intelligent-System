# This service handles all data reading and querying.
# When a user uploads a file, this is what reads it and runs analysis on it.
# DuckDB reads files directly from disk — no importing needed.
# Pandas handles statistics and correlations.

import duckdb
import pandas as pd
from pathlib import Path
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

import math

def clean_nan(obj):
    # Recursively replace nan and inf values with None
    # JSON cannot handle nan so we convert to null
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    elif isinstance(obj, dict):
        return {k: clean_nan(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [clean_nan(v) for v in obj]
    return obj

def get_connection():
    # Create a fresh DuckDB connection for each operation
    # DuckDB connections are not thread safe so we never share one
    return duckdb.connect()


def get_file_path(filename: str) -> str:
    # Build the full path to the uploaded file
    return str(Path(settings.UPLOAD_DIR) / filename)


def load_file_as_dataframe(filename: str) -> pd.DataFrame:
    # Load any supported file type into a Pandas dataframe
    # We use this for statistics that DuckDB can't do easily
    file_path = get_file_path(filename)
    extension = Path(filename).suffix.lower()

    if extension == ".csv":
        df = pd.read_csv(file_path)
    elif extension in [".xlsx", ".xls"]:
        df = pd.read_excel(file_path)
    elif extension == ".parquet":
        df = pd.read_parquet(file_path)
    else:
        raise ValueError(f"Unsupported file type: {extension}")

    logger.info("file loaded", filename=filename, rows=len(df), columns=len(df.columns))
    return df


def get_basic_info(filename: str) -> dict:
    # Get a quick summary of the file — shape, columns, types, sample rows
    # This is shown on the dashboard right after upload
    df = load_file_as_dataframe(filename)

    return {
        "filename": filename,
        "total_rows": len(df),
        "total_columns": len(df.columns),
        # Column names and their data types
        "columns": [
            {"name": col, "type": str(df[col].dtype)}
            for col in df.columns
        ],
        # First 5 rows as a preview
        "preview": df.head(5).to_dict(orient="records"),
    }


def get_statistics(filename: str) -> dict:
    # Get statistical summary for all numeric columns
    # mean, min, max, std deviation etc
    df = load_file_as_dataframe(filename)

    # Only run stats on numeric columns
    numeric_df = df.select_dtypes(include=["number"])

    if numeric_df.empty:
        return {"message": "No numeric columns found"}

    stats = numeric_df.describe().to_dict()
    return clean_nan(stats)


def get_correlations(filename: str) -> dict:
    # Find which columns move together
    # e.g. when sales go up, does profit also go up?
    df = load_file_as_dataframe(filename)
    numeric_df = df.select_dtypes(include=["number"])

    if numeric_df.empty or len(numeric_df.columns) < 2:
        return {"message": "Not enough numeric columns for correlation"}

    # Round to 2 decimal places for readability
    corr = numeric_df.corr().round(2).to_dict()
    return clean_nan(corr)


def run_query(filename: str, sql: str) -> list[dict]:
    # Run a custom SQL query on the uploaded file
    # The agents use this to answer specific questions about the data
    # Example: SELECT region, SUM(revenue) FROM file GROUP BY region
    file_path = get_file_path(filename)
    extension = Path(filename).suffix.lower()

    conn = get_connection()

    try:
        # Register the file so DuckDB can query it with SQL
        if extension == ".csv":
            conn.execute(f"CREATE VIEW data AS SELECT * FROM read_csv_auto('{file_path}')")
        elif extension in [".xlsx", ".xls"]:
            # DuckDB can't read Excel directly so we load via Pandas first
            df = pd.read_excel(file_path)
            conn.register("data", df)
        elif extension == ".parquet":
            conn.execute(f"CREATE VIEW data AS SELECT * FROM read_parquet('{file_path}')")

        # Replace 'file' with 'data' in the query so agents can use simple names
        clean_sql = sql.replace("file", "data")
        result = conn.execute(clean_sql).fetchdf()
        return result.to_dict(orient="records")

    except Exception as e:
        logger.error("query failed", sql=sql, error=str(e))
        raise
    finally:
        # Always close the connection when done
        conn.close()


def detect_date_columns(filename: str) -> list[str]:
    # Find which columns look like dates
    # The forecast agent needs this to build time series predictions
    df = load_file_as_dataframe(filename)
    date_columns = []

    for col in df.columns:
        # Check if the column name suggests it's a date
        col_lower = col.lower()
        if any(word in col_lower for word in ["date", "time", "month", "year", "day"]):
            date_columns.append(col)
            continue

        # Try to parse the first non-null value as a date
        sample = df[col].dropna().head(1)
        if not sample.empty:
            try:
                pd.to_datetime(sample.iloc[0])
                date_columns.append(col)
            except Exception:
                pass

    return date_columns


def get_missing_values_report(filename: str) -> dict:
    # Count missing values in each column
    # Used by the data quality layer
    df = load_file_as_dataframe(filename)

    missing = {}
    for col in df.columns:
        count = df[col].isna().sum()
        if count > 0:
            # Show count and percentage
            missing[col] = {
                "count": int(count),
                "percentage": round((count / len(df)) * 100, 2)
            }

    return missing


def get_outliers_report(filename: str) -> dict:
    # Find outliers using IQR method for each numeric column
    # Values that are unusually high or low compared to the rest
    df = load_file_as_dataframe(filename)
    numeric_df = df.select_dtypes(include=["number"])
    outliers = {}

    for col in numeric_df.columns:
        Q1 = numeric_df[col].quantile(0.25)
        Q3 = numeric_df[col].quantile(0.75)
        IQR = Q3 - Q1

        # Anything below Q1-1.5*IQR or above Q3+1.5*IQR is an outlier
        lower = Q1 - 1.5 * IQR
        upper = Q3 + 1.5 * IQR

        outlier_count = ((numeric_df[col] < lower) | (numeric_df[col] > upper)).sum()

        if outlier_count > 0:
            outliers[col] = clean_nan({
    "count": int(outlier_count),
    "lower_bound": round(float(lower), 2),
    "upper_bound": round(float(upper), 2),
})

    return outliers

def get_quality_root_causes(
    filename: str,
    missing_report: dict = None,
    outliers_report: dict = None,
    min_group_size: int = 5,
    min_lift: float = 1.5,
) -> dict:
    """
    Generic root-cause correlation analysis for data quality issues.

    For every column flagged with missing values or outliers, checks every
    OTHER column in the dataset (categorical groups, or date/time buckets
    such as hour-of-day and day-of-week) to find segments where the issue
    is disproportionately concentrated.

    Fully dynamic — no column names are hardcoded anywhere. Works on
    whatever schema the uploaded file happens to have. If a dataset has
    no categorical or date-like columns, it simply returns no findings.

    Returns:
        {
          "missing_value_causes": { "<col>": ["<explanation>", ...], ... },
          "outlier_causes":       { "<col>": ["<explanation>", ...], ... }
        }
    """
    df = load_file_as_dataframe(filename)
    total_rows = len(df)
    if total_rows == 0:
        return {"missing_value_causes": {}, "outlier_causes": {}}

    missing_report  = missing_report  if missing_report  is not None else get_missing_values_report(filename)
    outliers_report = outliers_report if outliers_report is not None else get_outliers_report(filename)

    # Candidate categorical columns: low-to-moderate cardinality object columns
    categorical_cols = [
        col for col in df.columns
        if df[col].dtype == "object" and 1 < df[col].nunique(dropna=True) <= 20
    ]

    # Candidate date/time-like columns (parsed once, reused for bucketing)
    datetime_cols = []
    for col in df.columns:
        if col in categorical_cols:
            continue
        col_lower = col.lower()
        if any(word in col_lower for word in ["date", "time", "month", "year", "day"]):
            try:
                parsed = pd.to_datetime(df[col], errors="coerce")
                if parsed.notna().sum() > 0:
                    datetime_cols.append((col, parsed))
            except Exception:
                pass

    def _best_segment_explanation(target_mask: pd.Series, target_col: str, label: str):
        """
        Find the single segment (across all candidate columns/buckets) where
        `target_mask` is most concentrated relative to its overall rate.
        Returns one explanation string, or None if nothing stands out.
        """
        baseline_count = int(target_mask.sum())
        if baseline_count == 0:
            return None

        baseline_rate = baseline_count / total_rows
        best = None  # (lift, explanation)

        # ── Categorical group breakdowns ──────────────────────────────
        for col in categorical_cols:
            if col == target_col:
                continue
            for group_value, group_idx in df.groupby(col, dropna=False).groups.items():
                group_size = len(group_idx)
                if group_size < min_group_size:
                    continue

                group_mask = target_mask.loc[group_idx]
                group_hits = int(group_mask.sum())
                if group_hits == 0:
                    continue

                group_rate = group_hits / group_size
                if group_rate <= baseline_rate:
                    continue

                lift = group_rate / baseline_rate
                if lift < min_lift:
                    continue

                pct_of_total = round((group_hits / baseline_count) * 100, 1)
                explanation = (
                    f"{pct_of_total}% of {label} in '{target_col}' occur where "
                    f"'{col}' = '{group_value}' "
                    f"({round(group_rate * 100, 1)}% rate vs {round(baseline_rate * 100, 1)}% overall)"
                )
                if best is None or lift > best[0]:
                    best = (lift, explanation)

        # ── Date/time bucket breakdowns (hour-of-day, day-of-week) ────
        for col, parsed in datetime_cols:
            if col == target_col:
                continue

            for bucket_name, bucket_series in [
                ("hour of day", parsed.dt.hour),
                ("day of week", parsed.dt.day_name()),
            ]:
                valid_idx = bucket_series.dropna().index
                if len(valid_idx) < min_group_size:
                    continue

                for bucket_value, bucket_idx in bucket_series.loc[valid_idx].groupby(
                    bucket_series.loc[valid_idx]
                ).groups.items():
                    group_size = len(bucket_idx)
                    if group_size < min_group_size:
                        continue

                    group_mask = target_mask.loc[bucket_idx]
                    group_hits = int(group_mask.sum())
                    if group_hits == 0:
                        continue

                    group_rate = group_hits / group_size
                    if group_rate <= baseline_rate:
                        continue

                    lift = group_rate / baseline_rate
                    if lift < min_lift:
                        continue

                    pct_of_total = round((group_hits / baseline_count) * 100, 1)
                    if bucket_name == "hour of day":
                        where = f"records timestamped around {int(bucket_value)}:00 (based on '{col}')"
                    else:
                        where = f"records falling on {bucket_value} (based on '{col}')"

                    explanation = (
                        f"{pct_of_total}% of {label} in '{target_col}' occur in "
                        f"{where} "
                        f"({round(group_rate * 100, 1)}% rate vs {round(baseline_rate * 100, 1)}% overall)"
                    )
                    if best is None or lift > best[0]:
                        best = (lift, explanation)

        return best[1] if best else None

    missing_value_causes = {}
    for col in missing_report.keys():
        target_mask = df[col].isna()
        explanation = _best_segment_explanation(target_mask, col, "missing values")
        if explanation:
            missing_value_causes[col] = [explanation]

    outlier_causes = {}
    for col, info in outliers_report.items():
        lower = info.get("lower_bound")
        upper = info.get("upper_bound")
        target_mask = (df[col] < lower) | (df[col] > upper)
        explanation = _best_segment_explanation(target_mask, col, "outliers")
        if explanation:
            outlier_causes[col] = [explanation]

    return clean_nan({
        "missing_value_causes": missing_value_causes,
        "outlier_causes":       outlier_causes,
    })

# Alias run_query so the agent's import statement works perfectly
query_data = run_query

