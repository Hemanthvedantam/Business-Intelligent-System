# The RAG agent searches through any supporting documents the user uploaded
# (SOPs, annual reports, policy documents).
# For now, if no documents are in Qdrant it passes through gracefully.
#
# Upgrade 3: returns docs_retrieved count so the UI shows
#            "Retrieved N documents" under the RAG node.

from app.core.logging import get_logger

logger = get_logger(__name__)

# Phase 2: import qdrant_client and perform real vector search here.
# For now the agent signals that analysis is based on data only.


async def rag_agent(state: dict) -> dict:
    logger.info("rag agent starting")

    # ── Phase 2 hook ───────────────────────────────────────────────────────
    # qdrant = QdrantClient(...)
    # results = qdrant.search(collection_name="documents",
    #                         query_vector=embed(state["question"]), limit=8)
    # docs_retrieved = len(results)
    # rag_context = "\n".join(r.payload["text"] for r in results)
    # ──────────────────────────────────────────────────────────────────────

    docs_retrieved = 0
    rag_context    = "No supporting documents uploaded. Analysis based on dataset only."

    logger.info("rag agent done", docs_retrieved=docs_retrieved)
    return {
        **state,
        "rag_context":    rag_context,
        "docs_retrieved": docs_retrieved,   # consumed by router → output_summary
        "current_step":   "rag",
    }