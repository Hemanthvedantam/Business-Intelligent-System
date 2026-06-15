"""
Planner Agent
-------------
First agent in the pipeline. Responsibilities:

1. Classify the question as "simple" (single factual lookup) or
   "complex" (multi-step analytical investigation).  The classification
   is emitted as `query_type` so the investigations router can short-
   circuit the full pipeline when it isn't needed.

2. For complex questions: generate a structured, domain-aware
   investigation plan (numbered steps, 4-6 steps).

3. Emit `tasks_created` so the UI node chip shows
   "Created N investigation tasks".

Production upgrades vs original:
  - query_type classification with keyword + LLM hybrid logic
  - dynamic step count driven by domain (operational vs strategic)
  - domain context properly unpacked from dict or string
  - retry_llm() with exponential backoff
  - truncate_for_context() guards against huge filenames / question blobs
  - all LLM output is sanitised before being stored in state
"""

import re
from app.providers.factory import get_provider
from app.core.logging import get_logger
from app.agents.agent_utils import retry_llm, truncate_for_context

logger = get_logger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
#  Simple-query heuristics
#  Questions matching these patterns are routed to the fast path (1 LLM call,
#  no full 8-agent pipeline).
# ─────────────────────────────────────────────────────────────────────────────
_SIMPLE_PATTERNS = [
    r"^how many",
    r"^what (is|are|was|were) the (column|row|field|header)",
    r"^list (all )?(column|row|field|header)",
    r"^show (me )?(the )?(column|row|schema|preview|sample)",
    r"^count ",
    r"^total (number|count|rows|columns)",
    r"^(minimum|maximum|average|mean|median|sum) (of )?",
    r"\bcolumn names?\b",
    r"\bhow many rows?\b",
    r"\bhow many columns?\b",
    r"\bdata types?\b",
    r"\bschema\b",
    r"\bpreview\b",
    r"\bsample data\b",
]

_SIMPLE_RE = [re.compile(p, re.IGNORECASE) for p in _SIMPLE_PATTERNS]


def _is_simple_query(question: str) -> bool:
    """
    Return True if the question is a direct factual lookup that does not
    require root-cause analysis, forecasting, or recommendations.
    """
    q = question.strip().lower()
    return any(pattern.search(q) for pattern in _SIMPLE_RE)


# ─────────────────────────────────────────────────────────────────────────────
#  Domain → investigation depth mapping
# ─────────────────────────────────────────────────────────────────────────────
_DOMAIN_STEP_COUNT = {
    "sales":       6,
    "finance":     6,
    "operations":  5,
    "hr":          5,
    "marketing":   5,
    "supply_chain":6,
    "healthcare":  6,
    "education":   4,
    "general":     5,
}

_SYSTEM_PLAN = """You are an expert business analyst and investigation planner.
Create a clear, structured investigation plan based on the user's question and dataset domain.
Be specific — reference the actual domain and likely metrics involved.
Format: numbered list ONLY, one step per line, exactly {n_steps} steps.
No intro sentence, no conclusion, no markdown headers — just the numbered steps.
Each step must be an actionable analysis task (e.g. "2. Identify top 5 products by revenue decline")."""

_SYSTEM_CLASSIFY = """You are a query classifier for a business intelligence system.
Respond with ONLY one word: "simple" or "complex".
  simple  = factual lookup (row count, column names, single statistic)
  complex = requires root-cause analysis, trend investigation, forecasting, or recommendations"""


# ─────────────────────────────────────────────────────────────────────────────
#  Main agent
# ─────────────────────────────────────────────────────────────────────────────
async def planner_agent(state: dict) -> dict:
    logger.info("planner agent starting", question=state["question"])

    provider = get_provider()

    # ── 1. Unpack domain ─────────────────────────────────────────────────────
    domain_raw = state.get("domain", "unknown")
    if isinstance(domain_raw, dict):
        domain_tag = domain_raw.get("tag", "general")
        domain_name = domain_raw.get("name", domain_tag.title())
    else:
        domain_tag  = str(domain_raw) if domain_raw else "general"
        domain_name = domain_tag.title()

    question = truncate_for_context(state["question"], max_tokens=200, preserve_end=False)
    filename = state.get("filename", "dataset")

    # ── 2. Classify query (heuristic first, LLM fallback) ────────────────────
    query_type: str
    if _is_simple_query(question):
        query_type = "simple"
        logger.info("planner: simple query detected via heuristic")
    else:
        try:
            classify_resp = await retry_llm(
                provider,
                system=_SYSTEM_CLASSIFY,
                messages=[{"role": "user", "content": f"Question: {question}"}],
                tag="planner/classify",
                max_retries=2,
            )
            query_type = "simple" if "simple" in classify_resp.lower() else "complex"
        except Exception:
            query_type = "complex"   # safe default

    logger.info("planner: query_type=%s", query_type)

    # ── 3. For simple queries, skip the plan ─────────────────────────────────
    if query_type == "simple":
        return {
            **state,
            "plan":          "Direct data query — no multi-step plan required.",
            "query_type":    query_type,
            "tasks_created": 1,
            "current_step":  "planner",
        }

    # ── 4. Build investigation plan ──────────────────────────────────────────
    n_steps = _DOMAIN_STEP_COUNT.get(domain_tag, 5)

    try:
        plan = await retry_llm(
            provider,
            system=_SYSTEM_PLAN.format(n_steps=n_steps),
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Question: {question}\n"
                        f"Dataset:  {filename}\n"
                        f"Domain:   {domain_name} ({domain_tag})\n\n"
                        f"Create exactly {n_steps} investigation steps."
                    ),
                }
            ],
            tag="planner/plan",
        )

        # Count numbered steps produced
        steps = [l.strip() for l in plan.split("\n") if re.match(r"^\d+[\.\)]", l.strip())]
        tasks_created = len(steps) if steps else max(1, plan.count("\n") + 1)

        logger.info("planner agent done", tasks_created=tasks_created, query_type=query_type)
        return {
            **state,
            "plan":          plan,
            "query_type":    query_type,
            "tasks_created": tasks_created,
            "current_step":  "planner",
        }

    except Exception as exc:
        logger.error("planner agent failed", error=str(exc))
        return {
            **state,
            "plan":          "Investigate the question using available data.",
            "query_type":    "complex",
            "tasks_created": 1,
            "error":         str(exc),
            "current_step":  "planner",
        }