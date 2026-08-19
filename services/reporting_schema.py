import json
from pathlib import Path


_SCHEMA_PATH = Path(__file__).resolve().parent.parent / "config" / "reporting-schema.json"


def get_reporting_schema_catalog() -> dict:
    with _SCHEMA_PATH.open("r", encoding="utf-8") as schema_file:
        catalog = json.load(schema_file)

    if not isinstance(catalog, dict) or not isinstance(catalog.get("views"), dict):
        raise RuntimeError("Reporting schema catalog is invalid")
    return catalog


def get_reporting_schema() -> str:
    """Return the agent-safe reporting views and exact queryable columns."""
    return json.dumps(get_reporting_schema_catalog(), separators=(",", ":"))


def build_reporting_schema_tool() -> dict:
    return {
        "definition": {
            "type": "function",
            "name": "get_reporting_schema",
            "description": "Return the approved reporting views, exact column names, and column meanings for portfolio SQL queries.",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {},
                "required": [],
            },
            "strict": True,
        },
        "handler": get_reporting_schema,
    }
