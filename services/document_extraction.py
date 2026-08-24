import base64
import json
import os
import re
import time
from typing import Any

from openai import OpenAI, RateLimitError

from classification.client import get_document_client
from services.blob_uploader import download_blob
from utils.logging import logger


CANONICAL_SCHEMA_VERSION = "1.0"
MAX_LAYOUT_CHARS = 90000


class ExtractionJsonError(ValueError):
    def __init__(self, stage: str, raw_output: str, base_fields: dict[str, Any] | None = None):
        super().__init__(f"The {stage} extraction response was not valid JSON.")
        self.stage = stage
        self.raw_output = raw_output
        self.base_fields = base_fields or {}

BASE_INSTRUCTIONS = """You extract meaningful document fields from Document Intelligence layout text.
Document types and field names are unknown in advance. Return JSON only, using this shape:
{"fields":{"dynamicFieldName":{"value":any,"valueType":"string|number|date|currency|boolean|object|array","confidence":0.0,"page":1,"evidence":"exact source text"}}}
Use nested values and arrays when present. Do not infer missing values. Every field needs exact evidence and a page number."""

VISION_INSTRUCTIONS = """You are a document extraction enrichment agent with visual access to the original document.
Review the source document, Document Intelligence layout text, and base extraction candidates. Find missed fields,
correct OCR/layout mistakes, and report visual conflicts. Return JSON only with this shape:
{"fields":{"dynamicFieldName":{"value":any,"valueType":"string|number|date|currency|boolean|object|array","confidence":0.0,"page":1,"evidence":"exact visual or textual evidence"}},"findings":["short issue or observation"]}
Do not invent values. Include only values supported by the document. Evidence and page are mandatory for every field."""


def _openai_client() -> tuple[OpenAI, str]:
    endpoint = (os.environ.get("DOCUMENT_EXTRACTION_FOUNDRY_ENDPOINT") or os.environ.get("FOUNDRY_PROJECT_ENDPOINT") or "").rstrip("/")
    api_key = os.environ.get("DOCUMENT_EXTRACTION_FOUNDRY_API_KEY") or os.environ.get("FOUNDRY_API_KEY")
    deployment = os.environ.get("DOCUMENT_EXTRACTION_MODEL", "gpt-5.4-mini")
    if not endpoint or not api_key:
        raise ValueError("Missing Foundry endpoint or API key configuration for document extraction.")
    return OpenAI(api_key=api_key, base_url=f"{endpoint}/openai/v1"), deployment


def _layout_text(result: Any) -> str:
    pages: list[str] = []
    for page in getattr(result, "pages", []) or []:
        page_number = getattr(page, "page_number", len(pages) + 1)
        lines = [getattr(line, "content", "") for line in getattr(page, "lines", []) or []]
        if lines:
            pages.append(f"[Page {page_number}]\n" + "\n".join(lines))

    tables: list[str] = []
    for index, table in enumerate(getattr(result, "tables", []) or [], start=1):
        rows: dict[int, list[str]] = {}
        for cell in getattr(table, "cells", []) or []:
            rows.setdefault(getattr(cell, "row_index", 0), []).append(getattr(cell, "content", ""))
        if rows:
            tables.append(f"[Table {index}]\n" + "\n".join(" | ".join(row) for _, row in sorted(rows.items())))

    text = "\n\n".join(pages + tables).strip()
    if not text:
        raise ValueError("Document Intelligence did not return readable document content.")
    return text[:MAX_LAYOUT_CHARS]


def _parse_json_object(value: str) -> dict[str, Any]:
    cleaned = value.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE)
    parsed = json.loads(cleaned)
    if not isinstance(parsed, dict):
        raise ValueError("The extraction model returned JSON that was not an object.")
    return parsed


def _response_json(client: OpenAI, deployment: str, input_value: Any, stage: str) -> dict[str, Any]:
    for attempt in range(3):
        try:
            response = client.responses.create(model=deployment, input=input_value)
            raw_output = str(getattr(response, "output_text", ""))
            try:
                return _parse_json_object(raw_output)
            except ValueError as exc:
                raise ExtractionJsonError(stage, raw_output) from exc
        except RateLimitError:
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)
    raise RuntimeError("Unreachable extraction response state.")


def _value_type(value: Any, supplied: Any) -> str:
    if isinstance(supplied, str) and supplied:
        return supplied
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "string"


def _candidate(field_name: str, raw: Any, provider: str, model: str) -> dict[str, Any] | None:
    if not isinstance(raw, dict) or "value" not in raw:
        return None
    evidence = str(raw.get("evidence") or "").strip()
    try:
        page = int(raw.get("page") or 0)
    except (TypeError, ValueError):
        page = 0
    if not evidence or page <= 0:
        return None
    try:
        confidence = float(raw.get("confidence", 0))
    except (TypeError, ValueError):
        confidence = 0.0
    return {
        "id": f"{provider}:{field_name}",
        "value": raw["value"],
        "valueType": _value_type(raw["value"], raw.get("valueType")),
        "confidence": max(0.0, min(1.0, confidence)),
        "source": {
            "provider": provider,
            "model": model,
            "page": page,
            "evidence": evidence,
            "confidence": max(0.0, min(1.0, confidence)),
        },
    }


def _same_value(left: Any, right: Any) -> bool:
    return json.dumps(left, sort_keys=True, default=str) == json.dumps(right, sort_keys=True, default=str)


def _field_record(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    primary = max(candidates, key=lambda candidate: candidate["confidence"])
    same_value = all(_same_value(primary["value"], candidate["value"]) for candidate in candidates)
    return {
        "value": primary["value"] if same_value else None,
        "valueType": primary["valueType"],
        "confidence": primary["confidence"],
        "status": "candidate" if same_value else "conflict",
        "sources": [candidate["source"] for candidate in candidates],
        "candidates": candidates,
    }


def _merge_fields(base: dict[str, Any], vision: dict[str, Any], deployment: str) -> dict[str, Any]:
    names = set(base) | set(vision)
    merged: dict[str, Any] = {}
    for name in sorted(names):
        candidates = [
            candidate
            for candidate in (
                _candidate(name, base.get(name), "document_intelligence_layout", "prebuilt-layout"),
                _candidate(name, vision.get(name), "vision_extraction_agent", deployment),
            )
            if candidate
        ]
        if candidates:
            merged[name] = _field_record(candidates)
    return merged


def _vision_input(content_type: str, file_bytes: bytes, prompt: str) -> list[dict[str, Any]]:
    data_url = f"data:{content_type};base64,{base64.b64encode(file_bytes).decode('ascii')}"
    document_input: dict[str, Any]
    if content_type == "application/pdf":
        document_input = {"type": "input_file", "filename": "source.pdf", "file_data": data_url}
    elif content_type.startswith("image/"):
        document_input = {"type": "input_image", "image_url": data_url}
    else:
        raise ValueError("Vision enrichment currently supports PDF and image source documents.")
    return [{"role": "user", "content": [{"type": "input_text", "text": prompt}, document_input]}]


def _source_content_type(blob_name: str, content_type: str | None) -> str:
    if content_type and content_type != "application/octet-stream":
        return content_type
    extension = os.path.splitext(blob_name.lower())[1]
    return {
        ".pdf": "application/pdf",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }.get(extension, content_type or "application/octet-stream")


async def extract_document_fields(blob_name: str, original_path: str = "") -> dict[str, Any]:
    file_bytes, content_type = download_blob(blob_name)
    if not file_bytes:
        raise ValueError("Source document could not be downloaded from Blob Storage.")
    content_type = _source_content_type(blob_name, content_type)

    client = get_document_client()
    try:
        poller = await client.begin_analyze_document(model_id="prebuilt-layout", analyze_request={"base64Source": file_bytes})
        layout_result = await poller.result()
    finally:
        await client.close()

    document_text = _layout_text(layout_result)
    llm_client, deployment = _openai_client()
    base_result = _response_json(llm_client, deployment, f"{BASE_INSTRUCTIONS}\n\nDocument layout:\n{document_text}", "layout")
    base_fields = base_result.get("fields") if isinstance(base_result.get("fields"), dict) else {}
    vision_prompt = (
        f"{VISION_INSTRUCTIONS}\n\nDocument name: {original_path or blob_name}\n\n"
        f"Document Intelligence layout:\n{document_text}\n\n"
        f"Base extraction candidates:\n{json.dumps(base_fields, ensure_ascii=True)}"
    )
    try:
        vision_result = _response_json(llm_client, deployment, _vision_input(content_type, file_bytes, vision_prompt), "vision")
    except ExtractionJsonError as exc:
        exc.base_fields = base_fields
        raise
    vision_fields = vision_result.get("fields") if isinstance(vision_result.get("fields"), dict) else {}
    fields = _merge_fields(base_fields, vision_fields, deployment)
    findings = vision_result.get("findings") if isinstance(vision_result.get("findings"), list) else []
    logger.info("Extracted %s canonical fields from blob '%s'.", len(fields), blob_name)
    return {
        "schemaVersion": CANONICAL_SCHEMA_VERSION,
        "document": {"blobName": blob_name, "name": original_path, "contentType": content_type},
        "fields": fields,
        "findings": [str(finding) for finding in findings],
    }


def repair_document_extraction(blob_name: str, original_path: str, stage: str, raw_output: str, base_fields: dict[str, Any] | None = None) -> dict[str, Any]:
    """Normalizes reviewer-corrected agent JSON without invoking either model again."""
    repaired = _parse_json_object(raw_output)
    repaired_fields = repaired.get("fields") if isinstance(repaired.get("fields"), dict) else {}
    base_fields = base_fields or {}
    if stage == "vision":
        fields = _merge_fields(base_fields, repaired_fields, os.environ.get("DOCUMENT_EXTRACTION_MODEL", "gpt-5.4-mini"))
        findings = repaired.get("findings") if isinstance(repaired.get("findings"), list) else []
    elif stage == "layout":
        fields = _merge_fields(repaired_fields, {}, os.environ.get("DOCUMENT_EXTRACTION_MODEL", "gpt-5.4-mini"))
        findings = ["Vision enrichment was skipped after manual repair of the Layout extraction response."]
    else:
        raise ValueError("Unknown extraction repair stage.")
    return {
        "schemaVersion": CANONICAL_SCHEMA_VERSION,
        "document": {"blobName": blob_name, "name": original_path, "contentType": _source_content_type(blob_name, None)},
        "fields": fields,
        "findings": [str(finding) for finding in findings],
    }
