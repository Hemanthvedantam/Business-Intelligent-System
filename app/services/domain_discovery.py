# This service detects what kind of business data the user uploaded.
# Returns a structured domain object — not just a string —
# so the frontend Domain Intelligence card (Upgrade 6) can show
# confidence %, key entities, and business objective.

from app.services.duckdb_service import get_basic_info
from app.providers.factory import get_provider
from app.core.logging import get_logger
import json, re

logger = get_logger(__name__)

# Maps a detected domain tag → typical key entities
DOMAIN_ENTITIES = {
    "sales":           ["Revenue", "Orders", "Customers", "Products", "Regions"],
    "logistics":       ["Shipments", "Delivery", "Routes", "Carriers", "Delays"],
    "healthcare":      ["Patients", "Providers", "Claims", "Diagnoses", "Treatments"],
    "finance":         ["Transactions", "Accounts", "Risk", "Returns", "Portfolios"],
    "hr":              ["Employees", "Departments", "Attrition", "Salaries", "Performance"],
    "marketing":       ["Campaigns", "Channels", "Leads", "Conversions", "Spend"],
    "operations":      ["Processes", "Efficiency", "Costs", "Output", "Quality"],
    "retail":          ["Products", "Sales", "Inventory", "Stores", "Margins"],
    "manufacturing":   ["Production", "Defects", "Downtime", "Suppliers", "Output"],
    "other":           ["Records", "Metrics", "Categories", "Values", "Dates"],
}

DOMAIN_OBJECTIVES = {
    "sales":         "Revenue & Sales Performance Analysis",
    "logistics":     "Delivery & Supply Chain Optimisation",
    "healthcare":    "Patient Outcomes & Claims Analysis",
    "finance":       "Financial Risk & Portfolio Analysis",
    "hr":            "Workforce & Attrition Analysis",
    "marketing":     "Campaign & Attribution Analysis",
    "operations":    "Operational Efficiency Analysis",
    "retail":        "Retail Sales & Inventory Analysis",
    "manufacturing": "Production Quality & Yield Analysis",
    "other":         "General Business Analysis",
}


async def detect_domain(filename: str) -> dict:
    """
    Detect business domain from dataset columns and preview rows.

    Returns a dict:
    {
        "name": "Healthcare",
        "tag":  "healthcare",        # lowercase key used by agents
        "confidence": 94,
        "entities": ["Patients", ...],
        "objective": "Patient Outcomes & Claims Analysis"
    }
    """
    try:
        info     = get_basic_info(filename)
        columns  = [c["name"] for c in info["columns"]]
        preview  = info["preview"][:3]

        provider = get_provider()

        system = """You are a business domain classifier.
Analyse column names and sample data then respond with ONLY valid JSON — no markdown, no explanation.

JSON format:
{
  "domain": "one of: sales | logistics | healthcare | finance | hr | marketing | operations | retail | manufacturing | other",
  "confidence": 0-100,
  "reasoning": "one sentence why"
}"""

        messages = [
            {
                "role": "user",
                "content": (
                    f"Column names: {columns}\n"
                    f"Sample rows: {preview}\n\n"
                    "Classify the business domain. Respond ONLY with JSON."
                )
            }
        ]

        raw = await provider.complete(system=system, messages=messages)

        # Strip any markdown fences if model added them
        clean = re.sub(r"```(?:json)?|```", "", raw).strip()
        parsed = json.loads(clean)

        tag        = parsed.get("domain", "other").strip().lower()
        confidence = int(parsed.get("confidence", 80))

        valid_domains = list(DOMAIN_ENTITIES.keys())
        if tag not in valid_domains:
            tag = "other"

        domain_obj = {
            "name":       tag.capitalize(),
            "tag":        tag,
            "confidence": confidence,
            "entities":   DOMAIN_ENTITIES.get(tag, DOMAIN_ENTITIES["other"]),
            "objective":  DOMAIN_OBJECTIVES.get(tag, DOMAIN_OBJECTIVES["other"]),
        }

        logger.info("domain detected", filename=filename, domain=tag, confidence=confidence)
        return domain_obj

    except Exception as e:
        logger.error("domain detection failed", error=str(e))
        return {
            "name":       "Other",
            "tag":        "other",
            "confidence": 70,
            "entities":   DOMAIN_ENTITIES["other"],
            "objective":  DOMAIN_OBJECTIVES["other"],
        }