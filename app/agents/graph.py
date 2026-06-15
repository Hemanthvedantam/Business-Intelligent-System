"""
Investigation Graph
-------------------
LangGraph connects all 7 agents into one pipeline.
Each agent does its job and passes results to the next one.
The state object carries all data through the entire investigation.

Chart data keys surfaced to the final result:
  trend_data, correlation_data, distribution_data, forecast_data

Upgrade 6 additions to InvestigationState:
  quality_issues            — list of plain-text issue strings from data_quality.py
  anomaly_context           — structured anomaly dict from pipeline_service._build_anomaly_context()
  structured_recommendations — list of rich recommendation dicts from upgraded recommend_agent
"""

from typing import TypedDict, Optional
from langgraph.graph import StateGraph, END
from app.core.logging import get_logger

logger = get_logger(__name__)


# ─────────────────────────────────────────────────────────
#  Shared state — flows through ALL agents
# ─────────────────────────────────────────────────────────
class InvestigationState(TypedDict):
    # ── Input ────────────────────────────────────────────
    question:         str
    filename:         str
    investigation_id: int

    # ── Domain discovery ─────────────────────────────────
    domain: Optional[str]

    # ── Per-agent outputs ────────────────────────────────
    plan:              Optional[str]
    data_findings:     Optional[dict]
    rag_context:       Optional[str]
    forecast:          Optional[dict]
    root_causes:       Optional[list]
    recommendations:   Optional[list]
    final_summary:     Optional[str]

    # ── Chart-ready payloads (set by data_analyst + forecast) ──
    trend_data:        Optional[dict]
    correlation_data:  Optional[dict]
    distribution_data: Optional[dict]
    forecast_data:     Optional[dict]

    # ── Agent output summaries (for Upgrade 3 UI) ────────
    analyst_summary:   Optional[str]

    # ── Upgrade 6: pipeline diagnosis keys ───────────────
    quality_issues:               Optional[list]   # from data_quality.run_data_quality_check()
    anomaly_context:              Optional[dict]   # from pipeline_service._build_anomaly_context()
    structured_recommendations:   Optional[list]   # from upgraded recommend_agent

    # ── Tracking ─────────────────────────────────────────
    current_step: Optional[str]
    error:        Optional[str]


# ─────────────────────────────────────────────────────────
#  Import agents
# ─────────────────────────────────────────────────────────
from app.agents.planner        import planner_agent
from app.agents.data_analyst   import data_analyst_agent
from app.agents.rag_agent      import rag_agent
from app.agents.forecast_agent import forecast_agent
from app.agents.root_cause     import root_cause_agent
from app.agents.recommend      import recommend_agent
from app.agents.executive      import executive_agent


# ─────────────────────────────────────────────────────────
#  Graph builder
# ─────────────────────────────────────────────────────────
def build_investigation_graph():
    graph = StateGraph(InvestigationState)

    # Nodes
    graph.add_node("planner",      planner_agent)
    graph.add_node("data_analyst", data_analyst_agent)
    graph.add_node("rag",          rag_agent)
    graph.add_node("forecast",     forecast_agent)
    graph.add_node("root_cause",   root_cause_agent)
    graph.add_node("recommend",    recommend_agent)
    graph.add_node("executive",    executive_agent)

    # Edges — sequential pipeline (unchanged)
    graph.add_edge("planner",      "data_analyst")
    graph.add_edge("data_analyst", "rag")
    graph.add_edge("rag",          "forecast")
    graph.add_edge("forecast",     "root_cause")
    graph.add_edge("root_cause",   "recommend")
    graph.add_edge("recommend",    "executive")
    graph.add_edge("executive",    END)

    graph.set_entry_point("planner")
    return graph.compile()


investigation_graph = build_investigation_graph()


# ─────────────────────────────────────────────────────────
#  Helper: extract final result dict from completed state
#  Called by investigations router after graph finishes.
# ─────────────────────────────────────────────────────────
def extract_result(state: dict) -> dict:
    """
    Flatten the state into the result dict that the frontend expects.
    All chart data keys are included so renderCharts() has what it needs.
    Upgrade 6: structured_recommendations included for resolution tracking.
    """
    return {
        # Core findings
        "root_causes":      state.get("root_causes", []),
        "recommendations":  state.get("recommendations", []),
        "final_summary":    state.get("final_summary", ""),
        "forecast":         state.get("forecast", {}),
        "domain":           state.get("domain", ""),

        # Executive summary (built by executive agent)
        "executive_summary": state.get("executive_summary", {}),
        "confidence":        state.get("confidence", None),

        # ── Chart payloads ───────────────────────────────
        "trend_data":        state.get("trend_data"),
        "correlation_data":  state.get("correlation_data"),
        "distribution_data": state.get("distribution_data"),
        "forecast_data":     state.get("forecast_data"),

        # Evidence tree (if executive agent produced one)
        "evidence_tree":     state.get("evidence_tree"),

        # ── Upgrade 6 ────────────────────────────────────
        "structured_recommendations": state.get("structured_recommendations", []),
    }