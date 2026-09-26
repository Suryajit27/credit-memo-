import json
import re
from datetime import datetime
from difflib import SequenceMatcher
from typing import Any

from services.document_extraction import _document_profile


def _source_field_name(field_name: str) -> str:
    normalized = field_name.replace("'", "").replace("/", " ")
    return re.sub(r"[^a-z0-9]+", "_", normalized.lower()).strip("_")


def _normalized_scalar(value: Any) -> Any:
    if isinstance(value, str):
        stripped = value.strip()
        for date_format in ("%m/%d/%Y", "%Y-%m-%d"):
            try:
                return ("date", datetime.strptime(stripped, date_format).date().isoformat())
            except ValueError:
                pass
        digits = re.sub(r"[^0-9]", "", stripped)
        if stripped.startswith("$") and digits:
            return ("currency", digits.lstrip("0") or "0")
        return ("text", re.sub(r"[^a-z0-9]+", "", stripped.casefold()))
    if isinstance(value, bool):
        return ("boolean", value)
    if isinstance(value, (int, float)):
        return ("number", str(value))
    return value


def _normalized_value(value: Any) -> Any:
    if isinstance(value, list):
        normalized_items = [_normalized_value(item) for item in value]
        return sorted(normalized_items, key=lambda item: json.dumps(item, sort_keys=True, default=str))
    if isinstance(value, dict):
        return {key: _normalized_value(item) for key, item in sorted(value.items())}
    return _normalized_scalar(value)


def _similarity_percent(expected: Any, actual: Any) -> int:
    left = json.dumps(_normalized_value(expected), sort_keys=True, default=str)
    right = json.dumps(_normalized_value(actual), sort_keys=True, default=str)
    return round(SequenceMatcher(None, left, right).ratio() * 100)


def _check(field_name: str, expected: Any, extracted: dict[str, Any] | None) -> dict[str, Any]:
    if expected is None:
        return {"fieldName": field_name, "status": "missing_expected_value"}
    if not extracted:
        return {"fieldName": field_name, "expectedValue": expected, "status": "missing_extracted_value"}
    if extracted.get("status") == "conflict":
        return {
            "fieldName": field_name,
            "expectedValue": expected,
            "status": "review_required",
            "reason": "Extraction candidates conflict.",
        }
    actual = extracted.get("value")
    if actual is None:
        return {"fieldName": field_name, "expectedValue": expected, "status": "missing_extracted_value"}
    source = (extracted.get("sources") or [{}])[0]
    similarity_percent = _similarity_percent(expected, actual)
    return {
        "fieldName": field_name,
        "expectedValue": expected,
        "extractedValue": actual,
        "comparison": "normalized_exact",
        "status": "passed" if similarity_percent == 100 else "failed",
        "similarityPercent": similarity_percent,
        "page": source.get("page"),
        "evidence": source.get("evidence"),
    }


def validate_document_extraction(extraction: dict[str, Any], document_type: str, request_record: dict[str, Any]) -> dict[str, Any]:
    """Compares configured extracted fields with request-level Cosmos source data."""
    profile = _document_profile(document_type)
    if not profile:
        return {"status": "not_configured", "checks": []}
    expected_fields = (request_record.get("extracted_data") or {}).get(profile["sourceGroup"])
    if not isinstance(expected_fields, dict):
        return {"status": "missing_source_data", "sourceGroup": profile["sourceGroup"], "checks": []}
    fields = extraction.get("fields") or {}
    checks = [
        _check(field["fieldName"], expected_fields.get(_source_field_name(field["fieldName"])), fields.get(field["fieldName"]))
        for field in profile.get("extractionFields", [])
    ]
    statuses = {check["status"] for check in checks}
    status = "passed" if statuses == {"passed"} else "review_required" if "review_required" in statuses else "failed"
    return {"status": status, "sourceGroup": profile["sourceGroup"], "checks": checks}
