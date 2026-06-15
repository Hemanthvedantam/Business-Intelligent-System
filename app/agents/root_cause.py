# The root cause agent finds WHY something is happening — not just what.
# It combines data findings, forecasts, and document context.
#
# Upgrades delivered here:
#   Upgrade 2 — produces structured_causes list for the Evidence Tree
#   Upgrade 1 — produces overall confidence score
#   Upgrade 3 — returns causes_found count for output_summary

import re
import json
from app.providers.factory import get_provider
from app.core.logging import get_logger

logger = get_logger(__name__)


async def root_cause_agent(state: dict) -> dict:
    logger.info("root cause agent starting")

    provider = get_provider()

    system = """You are an expert root cause analyst.
Find the specific root causes behind the business problem.

Return ONLY valid JSON — no markdown, no explanation, no fences.

Format:
{
  "confidence": <integer 0-100 reflecting overall confidence in findings>,
  "causes": [
    {
      "title":     "<short cause title, 3-6 words>",
      "detail":    "<one sentence explanation referencing data>",
      "value":     "<metric e.g. -18% or +12% — null if not applicable>",
      "direction": "negative | positive | neutral"
    }
  ]
}

Maximum 6 causes. Be specific — always say what the data shows."""

    messages = [
        {
            "role": "user",
            "content": (
                f"Question: {state['question']}\n"
                f"Domain:   {state.get('domain', {}).get('tag', 'unknown') if isinstance(state.get('domain'), dict) else state.get('domain', 'unknown')}\n\n"
                f"Data analysis findings:\n{state.get('data_findings', {}).get('analysis', 'No analysis available')}\n\n"
                f"Forecast:\n{state.get('forecast', {}).get('predictions', 'No forecast available')}\n\n"
                f"Document context:\n{state.get('rag_context', 'No documents available')}\n\n"
                "Identify the specific root causes. Respond ONLY with JSON."
            )
        }
    ]

    try:
        raw    = await provider.complete(system=system, messages=messages)
        clean  = re.sub(r"```(?:json)?|```", "", raw).strip()
        parsed = json.loads(clean)

        confidence       = int(parsed.get("confidence", 80))
        structured_causes = parsed.get("causes", [])

        # Build the plain-text list the rest of the pipeline expects
        root_causes_text = []
        for c in structured_causes:
            title  = c.get("title", "")
            detail = c.get("detail", "")
            value  = c.get("value")
            line   = f"**{title}**: {detail}"
            if value:
                line += f" ({value})"
            root_causes_text.append(line)

        logger.info("root cause agent done",
                    causes_found=len(structured_causes),
                    confidence=confidence)

        return {
            **state,
            "root_causes":       root_causes_text,   # plain text list used by downstream agents
            "structured_causes": structured_causes,   # rich dicts used by evidence tree
            "confidence":        confidence,
            "causes_found":      len(structured_causes),
            "current_step":      "root_cause",
        }

    except Exception as e:
        logger.error("root cause agent failed", error=str(e))
        # Graceful fallback: try to parse as plain text
        raw_fallback = ""
        try:
            raw_fallback = await provider.complete(
                system="List root causes as a numbered list only.",
                messages=[{"role": "user", "content": state.get("question", "")}]
            )
        except Exception:
            pass

        lines = [
            l.strip() for l in raw_fallback.strip().split("\n")
            if l.strip() and len(l.strip()) > 10
        ]
        return {
            **state,
            "root_causes":       lines,
            "structured_causes": [],
            "confidence":        70,
            "causes_found":      len(lines),
            "error":             str(e),
            "current_step":      "root_cause",
        }