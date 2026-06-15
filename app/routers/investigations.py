# Investigation router — handles starting and streaming investigations.
#
# Upgrades delivered here:
#   Query Classifier — simple questions go to DuckDB only (skip 8-agent pipeline)
#   Upgrade 3       — per-agent output_summary counts streamed to browser
#   Upgrade 1+2     — executive_summary, evidence_tree included in complete event
#   Upgrade 4       — chart data (trend, forecast, correlation) included in complete event
#   Upgrade 6       — domain object (not string) sent with domain_discovery event

import json
import asyncio
import re
from fastapi import APIRouter, Depends, BackgroundTasks
from fastapi.responses import StreamingResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from app.db.session import get_db
from app.core.security import get_current_user, decode_access_token
from app.models.investigation import Investigation, InvestigationStatus
from app.agents.graph import investigation_graph, extract_result
from app.core.logging import get_logger

logger = get_logger(__name__)
router = APIRouter()

# SSE queues: investigation_id → asyncio.Queue
active_streams: dict[int, asyncio.Queue] = {}


class StartInvestigationRequest(BaseModel):
    question: str
    filename: str


# ─────────────────────────────────────────────────────────────────────────────
#  Query Classifier
# ─────────────────────────────────────────────────────────────────────────────

# Patterns that indicate a SIMPLE factual data query → DuckDB only
SIMPLE_PATTERNS = [
    r"^how many (rows|records|columns|entries)",
    r"^(what is|what's|show) the (total|count|sum|average|mean|max|min|number)",
    r"^(list|show|display|give me) (all |the )?(columns|rows|fields|headers)",
    r"^(how many|what is the) (unique|distinct)",
    r"^(what are|show) the (top|bottom) \d+",
    r"^(count|sum|average|min|max)\b",
    r"^(describe|summary|preview) (the |this )?(dataset|data|file|table)",
    r"^(what columns|which columns|column names)",
    r"^(sample|first|last) \d+ rows",
]

COMPLEX_KEYWORDS = [
    "why", "cause", "reason", "drop", "decline", "increase", "churn",
    "forecast", "predict", "trend", "anomaly", "root", "factor",
    "what will", "next month", "next quarter", "what happened",
    "performance", "delay", "risk", "impact", "correlation",
]


def classify_query(question: str) -> str:
    """
    Returns 'simple' or 'complex'.
    Simple → DuckDB direct answer only.
    Complex → full 8-agent LangGraph pipeline.
    """
    q_lower = question.lower().strip()

    for pattern in SIMPLE_PATTERNS:
        if re.search(pattern, q_lower):
            return "simple"

    for kw in COMPLEX_KEYWORDS:
        if kw in q_lower:
            return "complex"

    # Default: short questions (< 8 words) that don't match complex → simple
    word_count = len(q_lower.split())
    if word_count <= 6:
        return "simple"

    return "complex"


async def answer_simple_query(question: str, filename: str) -> str:
    """
    Answer a simple factual question using DuckDB directly — no agents.
    """
    from app.services.duckdb_service import get_basic_info, query_data
    try:
        info    = get_basic_info(filename)
        columns = [c["name"] for c in info["columns"]]
        rows    = info.get("row_count", "unknown")

        # Try to answer common patterns directly
        q = question.lower()

        if "how many rows" in q or "how many records" in q:
            return f"The dataset **{filename}** contains **{rows:,} rows**."

        if "how many columns" in q:
            return f"The dataset has **{len(columns)} columns**: {', '.join(columns)}."

        if "column names" in q or "list columns" in q or "what columns" in q:
            return f"**Columns ({len(columns)}):** {', '.join(columns)}"

        if "describe" in q or "summary" in q or "preview" in q:
            preview = info.get("preview", [])[:3]
            return (
                f"**Dataset:** {filename}\n"
                f"**Rows:** {rows:,}  |  **Columns:** {len(columns)}\n\n"
                f"**Columns:** {', '.join(columns)}\n\n"
                f"**Sample rows:**\n```\n{json.dumps(preview, indent=2)}\n```"
            )

        # Generic fallback — use provider for simple SQL answer
        from app.providers.factory import get_provider
        provider = get_provider()
        answer = await provider.complete(
            system="You are a data analyst. Answer the question directly and concisely based on dataset metadata. No markdown headers.",
            messages=[{
                "role": "user",
                "content": (
                    f"Dataset: {filename}\n"
                    f"Rows: {rows}\n"
                    f"Columns: {columns}\n\n"
                    f"Question: {question}\n\n"
                    "Answer concisely."
                )
            }]
        )
        return answer

    except Exception as e:
        logger.error("simple query failed", error=str(e))
        return f"Could not answer: {str(e)}"


# ─────────────────────────────────────────────────────────────────────────────
#  Start investigation
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/start")
async def start_investigation(
    data: StartInvestigationRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    query_type = classify_query(data.question)

    investigation = Investigation(
        user_id=int(current_user["sub"]),
        question=data.question,
        dataset_filename=data.filename,
        status=InvestigationStatus.PENDING,
    )
    db.add(investigation)
    await db.flush()
    investigation_id = investigation.id
    await db.commit()

    queue = asyncio.Queue()
    active_streams[investigation_id] = queue

    if query_type == "simple":
        background_tasks.add_task(
            run_simple_query,
            investigation_id=investigation_id,
            question=data.question,
            filename=data.filename,
            queue=queue,
        )
    else:
        background_tasks.add_task(
            run_investigation,
            investigation_id=investigation_id,
            question=data.question,
            filename=data.filename,
            user_id=int(current_user["sub"]),
            queue=queue,
        )

    logger.info("investigation started",
                id=investigation_id,
                type=query_type,
                question=data.question)

    return {
        "investigation_id": investigation_id,
        "status":           "started",
        "query_type":       query_type,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Simple query runner (DuckDB only — skips all 8 agents)
# ─────────────────────────────────────────────────────────────────────────────

async def run_simple_query(
    investigation_id: int,
    question: str,
    filename: str,
    queue: asyncio.Queue,
):
    from app.db.session import SessionLocal

    async with SessionLocal() as db:
        try:
            result_obj = await db.execute(
                select(Investigation).where(Investigation.id == investigation_id)
            )
            investigation = result_obj.scalar_one()
            investigation.status = InvestigationStatus.RUNNING
            await db.commit()

            await queue.put({
                "type":    "agent_step",
                "agent":   "data_analyst",
                "message": "Running direct data query...",
                "status":  "running",
            })

            answer = await answer_simple_query(question, filename)

            await queue.put({
                "type":           "agent_step",
                "agent":          "data_analyst",
                "message":        "Query complete",
                "status":         "done",
                "output_summary": "Direct DuckDB answer — full pipeline skipped",
            })

            investigation.status        = InvestigationStatus.COMPLETED
            investigation.final_summary = answer
            investigation.root_causes   = []
            investigation.recommendations = []
            await db.commit()

            await queue.put({
                "type":   "complete",
                "result": {
                    "summary":           answer,
                    "root_causes":       [],
                    "recommendations":   [],
                    "executive_summary": {
                        "confidence": None,
                        "metrics":    [],
                        "evidence":   [],
                    },
                },
            })

        except Exception as e:
            logger.error("simple query failed", id=investigation_id, error=str(e))
            await queue.put({"type": "error", "message": str(e)})
        finally:
            active_streams.pop(investigation_id, None)


# ─────────────────────────────────────────────────────────────────────────────
#  Full investigation runner (all 8 agents)
# ─────────────────────────────────────────────────────────────────────────────

async def run_investigation(
    investigation_id: int,
    question: str,
    filename: str,
    user_id: int,
    queue: asyncio.Queue,
):
    from app.db.session import SessionLocal
    from app.services.domain_discovery import detect_domain

    async with SessionLocal() as db:
        try:
            result_obj = await db.execute(
                select(Investigation).where(Investigation.id == investigation_id)
            )
            investigation = result_obj.scalar_one()
            investigation.status = InvestigationStatus.RUNNING
            await db.commit()

            # ── Domain Discovery ──────────────────────────────────────────
            await queue.put({
                "type":    "agent_step",
                "agent":   "domain_discovery",
                "message": "Detecting business domain...",
                "status":  "running",
            })

            domain = await detect_domain(filename)   # now returns a dict

            await queue.put({
                "type":           "agent_step",
                "agent":          "domain_discovery",
                "message":        f"Domain detected: {domain['name']} ({domain['confidence']}% confidence)",
                "status":         "done",
                "output_summary": f"{domain['confidence']}% confidence · {len(domain['entities'])} key entities",
                "domain":         domain,   # full dict → showDomainCard()
            })

            # ── Planner ───────────────────────────────────────────────────
            await queue.put({
                "type":    "agent_step",
                "agent":   "planner",
                "message": "Creating investigation plan...",
                "status":  "running",
            })

            # Build initial state
            initial_state = {
                "question":         question,
                "filename":         filename,
                "investigation_id": investigation_id,
                "domain":           domain,
                "plan":             None,
                "data_findings":    None,
                "rag_context":      None,
                "forecast":         None,
                "root_causes":      None,
                "recommendations":  None,
                "final_summary":    None,
                "trend_data":       None,
                "correlation_data": None,
                "distribution_data":None,
                "forecast_data":    None,
                "analyst_summary":  None,
                "current_step":     None,
                "error":            None,
            }

            # ── Run the LangGraph pipeline ────────────────────────────────
            # We intercept after each node using astream_events so we can
            # send live per-agent progress to the browser.
            final_state = {}

            try:
                # Use astream_events if available (langgraph ≥ 0.1)
                async for event in investigation_graph.astream(
                    initial_state,
                    stream_mode="updates",
                ):
                    for node_name, node_output in event.items():
                        final_state.update(node_output)
                        await _emit_agent_done(queue, node_name, node_output)

            except AttributeError:
                # Fallback: ainvoke (no streaming)
                final_state = await investigation_graph.ainvoke(initial_state)
                # Emit done events for all nodes
                for agent in ["planner", "data_analyst", "rag", "forecast",
                               "root_cause", "recommend", "executive"]:
                    await _emit_agent_done(queue, agent, final_state)
                    await asyncio.sleep(0.08)

            # ── Save to DB ────────────────────────────────────────────────
            investigation.status          = InvestigationStatus.COMPLETED
            investigation.domain          = domain.get("tag", "other")
            investigation.agent_steps     = final_state.get("plan")
            investigation.root_causes     = final_state.get("root_causes")
            investigation.recommendations = final_state.get("recommendations")
            investigation.final_summary   = final_state.get("final_summary")
            await db.commit()

            # ── Build complete result payload ─────────────────────────────
            result = extract_result(final_state)

            await queue.put({
                "type":   "complete",
                "result": result,
            })

        except Exception as e:
            logger.error("investigation failed", id=investigation_id, error=str(e))

            result_obj = await db.execute(
                select(Investigation).where(Investigation.id == investigation_id)
            )
            inv = result_obj.scalar_one_or_none()
            if inv:
                inv.status        = InvestigationStatus.FAILED
                inv.error_message = str(e)
                await db.commit()

            await queue.put({"type": "error", "message": str(e)})

        finally:
            active_streams.pop(investigation_id, None)


async def _emit_agent_done(queue: asyncio.Queue, node_name: str, node_output: dict):
    """
    Emit an agent_step 'done' event for a completed LangGraph node,
    including per-agent output_summary counts (Upgrade 3).
    """
    label_map = {
        "planner":      "Investigation plan created",
        "data_analyst": "Data analysis complete",
        "rag":          "Document search complete",
        "forecast":     "Forecast generated",
        "root_cause":   "Root causes identified",
        "recommend":    "Recommendations ready",
        "executive":    "Executive summary written",
    }

    message = label_map.get(node_name, f"{node_name} complete")

    # Build output_summary string based on what each agent produced
    output_summary = _build_output_summary(node_name, node_output)

    await queue.put({
        "type":           "agent_step",
        "agent":          node_name,
        "message":        message,
        "status":         "done",
        "output_summary": output_summary,
    })


def _build_output_summary(node_name: str, state: dict) -> str:
    """Return a short human-readable count string for Upgrade 3 display."""
    if node_name == "planner":
        n = state.get("tasks_created", 0)
        return f"Created {n} investigation tasks" if n else "Plan created"

    if node_name == "data_analyst":
        summary = state.get("analyst_summary", "")
        cols = state.get("data_findings", {}).get("columns_analysed", 0) if isinstance(state.get("data_findings"), dict) else 0
        if cols:
            return f"Analysed {cols} columns"
        return summary or "Dataset analysed"

    if node_name == "rag":
        n = state.get("docs_retrieved", 0)
        return f"Retrieved {n} documents" if n else "No documents found"

    if node_name == "forecast":
        horizon = ""
        fc = state.get("forecast", {})
        if isinstance(fc, dict):
            horizon = fc.get("horizon", fc.get("forecast_horizon", ""))
        return f"Generated {horizon} prediction" if horizon else "Forecast generated"

    if node_name == "root_cause":
        n = state.get("causes_found", len(state.get("root_causes", [])))
        conf = state.get("confidence", "")
        if n and conf:
            return f"Found {n} factors · {conf}% confidence"
        return f"Found {n} root causes" if n else "Root causes identified"

    if node_name == "recommend":
        n = len(state.get("recommendations", []))
        return f"Generated {n} recommendations" if n else "Recommendations ready"

    if node_name == "executive":
        return "Summary and evidence tree built"

    return ""


# ─────────────────────────────────────────────────────────────────────────────
#  SSE stream endpoint
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/{investigation_id}/stream")
async def stream_investigation(
    investigation_id: int,
    token: str,
):
    payload = decode_access_token(token)
    if not payload:
        return Response(status_code=401)

    async def event_generator():
        queue = active_streams.get(investigation_id)
        if not queue:
            yield f"data: {json.dumps({'type': 'error', 'message': 'Investigation not found or already complete'})}\n\n"
            return

        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=30.0)
                yield f"data: {json.dumps(event)}\n\n"
                if event["type"] in ["complete", "error"]:
                    break
            except asyncio.TimeoutError:
                yield f"data: {json.dumps({'type': 'heartbeat'})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":    "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
#  List investigations
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/list")
async def list_investigations(
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    result = await db.execute(
        select(Investigation)
        .where(Investigation.user_id == int(current_user["sub"]))
        .order_by(Investigation.created_at.desc())
    )
    investigations = result.scalars().all()

    return {
        "investigations": [
            {
                "id":         inv.id,
                "question":   inv.question,
                "domain":     inv.domain,
                "status":     inv.status,
                "filename":   inv.dataset_filename,
                "created_at": str(inv.created_at),
            }
            for inv in investigations
        ]
    }