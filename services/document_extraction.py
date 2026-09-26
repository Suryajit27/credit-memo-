import base64
import asyncio
from io import BytesIO
import json
import os
import re
import time
from pathlib import Path
from typing import Any

from azure.core.exceptions import HttpResponseError
from openai import APIStatusError, OpenAI, RateLimitError
from pypdf import PdfReader, PdfWriter

from classification.client import get_document_client
from services.blob_uploader import download_blob
from utils.logging import logger


CANONICAL_SCHEMA_VERSION = "1.0"
MAX_LAYOUT_CHARS = 90000
MAX_SELECTED_PAGES = 20
MAX_VISION_PAGES_PER_REQUEST = 5
MAX_LAYOUT_PAGES_PER_REQUEST = 5
MAX_LAYOUT_CONCURRENCY = 3
PROFILE_PATH = Path(__file__).resolve().parent.parent / "config" / "document-extraction-profiles.json"


class ExtractionJsonError(ValueError):
    def __init__(self, stage: str, raw_output: str, base_fields: dict[str, Any] | None = None):
        super().__init__(f"The {stage} extraction response was not valid JSON.")
        self.stage = stage
        self.raw_output = raw_output
        self.base_fields = base_fields or {}

BASE_INSTRUCTIONS = """You extract only the requested fields from the supplied Document Intelligence layout pages.
Do not return fields that are not requested. Return JSON only, using this shape:
{"fields":{"dynamicFieldName":{"value":any,"valueType":"string|number|date|currency|boolean|object|array","confidence":0.0,"page":1,"evidence":"exact source text"}}}
Use nested values and arrays when present. Do not infer missing values. Every field needs exact evidence and a page number."""

VISION_INSTRUCTIONS = """You are a document extraction enrichment agent with visual access to selected document pages.
Review only the supplied selected pages, their Document Intelligence layout text, and base extraction candidates. Find missed fields,
correct OCR/layout mistakes, and report visual conflicts. Return JSON only with this shape:
{"fields":{"dynamicFieldName":{"value":any,"valueType":"string|number|date|currency|boolean|object|array","confidence":0.0,"page":1,"evidence":"exact visual or textual evidence"}},"findings":["short issue or observation"]}
Do not invent values or return fields that are not requested. Include only values supported by the document. Evidence and page are mandatory for every field."""


def _openai_client() -> tuple[OpenAI, str]:
    endpoint = (os.environ.get("DOCUMENT_EXTRACTION_FOUNDRY_ENDPOINT") or os.environ.get("FOUNDRY_PROJECT_ENDPOINT") or "").rstrip("/")
    api_key = os.environ.get("DOCUMENT_EXTRACTION_FOUNDRY_API_KEY") or os.environ.get("FOUNDRY_API_KEY")
    deployment = os.environ.get("DOCUMENT_EXTRACTION_MODEL", "gpt-5.4-mini")
    if not endpoint or not api_key:
        raise ValueError("Missing Foundry endpoint or API key configuration for document extraction.")
    return OpenAI(api_key=api_key, base_url=f"{endpoint}/openai/v1"), deployment


def _layout_pages(result: Any) -> list[dict[str, Any]]:
    pages: dict[int, dict[str, Any]] = {}
    for page in getattr(result, "pages", []) or []:
        page_number = getattr(page, "page_number", len(pages) + 1)
        lines = [getattr(line, "content", "") for line in getattr(page, "lines", []) or []]
        pages[page_number] = {"pageNumber": page_number, "text": "\n".join(lines), "tables": []}
    for index, table in enumerate(getattr(result, "tables", []) or [], start=1):
        rows: dict[int, list[str]] = {}
        for cell in getattr(table, "cells", []) or []:
            rows.setdefault(getattr(cell, "row_index", 0), []).append(getattr(cell, "content", ""))
        if rows:
            table_text = f"[Table {index}]\n" + "\n".join(" | ".join(row) for _, row in sorted(rows.items()))
            regions = getattr(table, "bounding_regions", []) or []
            page_number = getattr(regions[0], "page_number", None) if regions else None
            if page_number in pages:
                pages[page_number]["tables"].append(table_text)
    return [pages[number] for number in sorted(pages)]


def _layout_text(pages: list[dict[str, Any]]) -> str:
    text = "\n\n".join(
        f"[Page {page['pageNumber']}]\n{page['text']}\n" + "\n".join(page["tables"])
        for page in pages
        if page["text"] or page["tables"]
    ).strip()
    if not text:
        raise ValueError("Document Intelligence did not return readable document content.")
    return text[:MAX_LAYOUT_CHARS]


def _normalized_document_type(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _document_profile(document_type: str) -> dict[str, Any] | None:
    with PROFILE_PATH.open(encoding="utf-8") as profile_file:
        profiles = json.load(profile_file).get("profiles", [])
    normalized = _normalized_document_type(document_type)
    for profile in profiles:
        aliases = [profile.get("name", ""), *profile.get("aliases", [])]
        if normalized in {_normalized_document_type(alias) for alias in aliases}:
            return profile
    return None


def _page_score(page_text: str, terms: list[str]) -> tuple[int, list[str]]:
    normalized = page_text.casefold()
    matched = [term for term in terms if term.casefold() in normalized]
    score = sum(len(term) + (10 * len(term.split())) for term in matched)
    return score, matched


def _select_pages(pages: list[dict[str, Any]], profile: dict[str, Any] | None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not profile:
        return pages, {"mode": "unfiltered", "selectedPages": [page["pageNumber"] for page in pages], "unmatchedFields": []}

    selected: dict[int, dict[str, Any]] = {}
    unmatched_fields: list[str] = []
    for field in profile.get("extractionFields", []):
        field_name = field["fieldName"]
        matches = []
        for page in pages:
            score, terms = _page_score(f"{page['text']}\n{' '.join(page['tables'])}", field.get("searchTerms", []))
            if score:
                matches.append((score, page["pageNumber"], terms))
        if not matches:
            unmatched_fields.append(field_name)
            continue
        for score, page_number, terms in sorted(matches, reverse=True)[:2]:
            entry = selected.setdefault(page_number, {"score": 0, "fields": [], "matchedTerms": []})
            entry["score"] += score
            entry["fields"].append(field_name)
            entry["matchedTerms"].extend(terms)

    ranked = sorted(selected.items(), key=lambda item: (-item[1]["score"], item[0]))[:MAX_SELECTED_PAGES]
    selected_numbers = {page_number for page_number, _ in ranked}
    available_numbers = {page["pageNumber"] for page in pages}
    for page_number, _ in ranked:
        for neighbor in (page_number - 1, page_number + 1):
            if neighbor in available_numbers and len(selected_numbers) < MAX_SELECTED_PAGES:
                selected_numbers.add(neighbor)
    selected_pages = [page for page in pages if page["pageNumber"] in selected_numbers]
    return selected_pages, {
        "mode": "profile-filtered",
        "profile": profile["name"],
        "selectedPages": [page["pageNumber"] for page in selected_pages],
        "selectionReasons": [
            {"page": page_number, "fields": details["fields"], "matchedTerms": sorted(set(details["matchedTerms"]))}
            for page_number, details in ranked
        ],
        "unmatchedFields": unmatched_fields,
    }


def _selected_pdf(file_bytes: bytes, selected_pages: list[dict[str, Any]]) -> bytes:
    reader = PdfReader(BytesIO(file_bytes))
    writer = PdfWriter()
    for page in selected_pages:
        writer.add_page(reader.pages[page["pageNumber"] - 1])
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def _page_batches(pages: list[dict[str, Any]], batch_size: int = MAX_VISION_PAGES_PER_REQUEST) -> list[list[dict[str, Any]]]:
    return [pages[index:index + batch_size] for index in range(0, len(pages), batch_size)]


def _remap_batch_pages(batch_pages: list[dict[str, Any]], original_pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    remapped: list[dict[str, Any]] = []
    for page in batch_pages:
        batch_page_number = page["pageNumber"]
        if not 1 <= batch_page_number <= len(original_pages):
            continue
        remapped.append({**page, "pageNumber": original_pages[batch_page_number - 1]["pageNumber"]})
    return remapped


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


def _candidate(field_name: str, raw: Any, provider: str, model: str, candidate_suffix: str = "") -> dict[str, Any] | None:
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
        "id": f"{provider}:{field_name}{candidate_suffix}",
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


def _merge_fields(base: dict[str, Any], vision: dict[str, Any] | list[dict[str, Any]], deployment: str) -> dict[str, Any]:
    vision_results = vision if isinstance(vision, list) else [vision]
    names = set(base)
    for result in vision_results:
        names.update(result)
    merged: dict[str, Any] = {}
    for name in sorted(names):
        candidates = [_candidate(name, base.get(name), "document_intelligence_layout", "prebuilt-layout")]
        candidates.extend(
            _candidate(name, result.get(name), "vision_extraction_agent", deployment, f":{index}")
            for index, result in enumerate(vision_results)
        )
        candidates = [candidate for candidate in candidates if candidate]
        if candidates:
            merged[name] = _field_record(candidates)
    return merged


def _vision_input(content_type: str, file_bytes: bytes, prompt: str) -> list[dict[str, Any]]:
    data_url = f"data:{content_type};base64,{base64.b64encode(file_bytes).decode('ascii')}"
    document_input: dict[str, Any]
    if content_type == "application/pdf":
        document_input = {"type": "input_file", "filename": "selected-pages.pdf", "file_data": data_url}
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


def _is_oversized_vision_input(error: Exception) -> bool:
    details = f"{error} {getattr(error, 'body', '')}".casefold()
    return "invalidcontentlength" in details or "input image is too large" in details


def _prefilter_pdf_pages(file_bytes: bytes, profile: dict[str, Any] | None) -> list[dict[str, Any]]:
    reader = PdfReader(BytesIO(file_bytes))
    source_pages = [{"pageNumber": number} for number in range(1, len(reader.pages) + 1)]
    if not profile:
        return source_pages
    text_pages = []
    for number, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        text_pages.append({"pageNumber": number, "text": text, "tables": []})
    selected_pages, selection = _select_pages(text_pages, profile)
    if selected_pages:
        logger.info("PDF text prefilter selected %s of %s pages for profile '%s'.", len(selected_pages), len(source_pages), profile["name"])
        return [{"pageNumber": page["pageNumber"]} for page in selected_pages]
    logger.info("PDF text prefilter found no matching pages for profile '%s'; using all %s pages.", profile["name"], len(source_pages))
    return source_pages


async def _analyze_layout_pages(file_bytes: bytes, content_type: str, profile: dict[str, Any] | None) -> tuple[list[dict[str, Any]], list[str]]:
    client = get_document_client()
    try:
        if content_type != "application/pdf":
            poller = await client.begin_analyze_document(model_id="prebuilt-layout", analyze_request={"base64Source": file_bytes})
            return _layout_pages(await poller.result()), []

        source_pages = _prefilter_pdf_pages(file_bytes, profile)
        semaphore = asyncio.Semaphore(MAX_LAYOUT_CONCURRENCY)

        async def analyze_batch(batch: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
            try:
                async with semaphore:
                    batch_bytes = _selected_pdf(file_bytes, batch)
                    poller = await client.begin_analyze_document(model_id="prebuilt-layout", analyze_request={"base64Source": batch_bytes})
                    return _remap_batch_pages(_layout_pages(await poller.result()), batch), []
            except HttpResponseError as exc:
                if not _is_oversized_vision_input(exc):
                    raise
                batch_pages: list[dict[str, Any]] = []
                batch_findings: list[str] = []
                for page in batch:
                    try:
                        async with semaphore:
                            page_bytes = _selected_pdf(file_bytes, [page])
                            poller = await client.begin_analyze_document(model_id="prebuilt-layout", analyze_request={"base64Source": page_bytes})
                            batch_pages.extend(_remap_batch_pages(_layout_pages(await poller.result()), [page]))
                    except HttpResponseError as retry_exc:
                        if not _is_oversized_vision_input(retry_exc):
                            raise
                        batch_findings.append(f"Layout extraction skipped for page {page['pageNumber']} because it exceeded the Document Intelligence input limit.")
                return batch_pages, batch_findings

        results = await asyncio.gather(*(analyze_batch(batch) for batch in _page_batches(source_pages, MAX_LAYOUT_PAGES_PER_REQUEST)))
        pages = [page for batch_pages, _ in results for page in batch_pages]
        findings = [finding for _, batch_findings in results for finding in batch_findings]
        if not pages:
            raise ValueError("Document Intelligence could not process any pages because the source pages exceeded its input limit.")
        return pages, findings
    finally:
        await client.close()


async def extract_document_fields(blob_name: str, original_path: str = "", document_type: str = "") -> dict[str, Any]:
    file_bytes, content_type = download_blob(blob_name)
    if not file_bytes:
        raise ValueError("Source document could not be downloaded from Blob Storage.")
    content_type = _source_content_type(blob_name, content_type)

    profile = _document_profile(document_type)
    layout_pages, layout_findings = await _analyze_layout_pages(file_bytes, content_type, profile)
    selected_pages, page_selection = _select_pages(layout_pages, profile)
    if not selected_pages:
        raise ValueError("No relevant pages were found for the configured extraction fields.")
    document_text = _layout_text(selected_pages)
    requested_fields = [field["fieldName"] for field in (profile or {}).get("extractionFields", [])]
    target_prompt = f"\n\nRequested fields: {json.dumps(requested_fields, ensure_ascii=True)}" if requested_fields else ""
    llm_client, deployment = _openai_client()
    base_result = _response_json(llm_client, deployment, f"{BASE_INSTRUCTIONS}{target_prompt}\n\nSelected document layout:\n{document_text}", "layout")
    base_fields = base_result.get("fields") if isinstance(base_result.get("fields"), dict) else {}
    vision_results: list[dict[str, Any]] = []
    findings: list[str] = list(layout_findings)
    vision_pages = selected_pages if content_type == "application/pdf" else selected_pages[:1]

    def enrich_vision_batch(batch: list[dict[str, Any]]) -> dict[str, Any]:
        page_numbers = [page["pageNumber"] for page in batch]
        vision_prompt = (
            f"{VISION_INSTRUCTIONS}{target_prompt}\n\nDocument name: {original_path or blob_name}\n"
            f"The attached source contains only original document pages {page_numbers}. Return original document page numbers in every field's page property.\n\n"
            f"Selected Document Intelligence layout:\n{_layout_text(batch)}\n\n"
            f"Base extraction candidates:\n{json.dumps(base_fields, ensure_ascii=True)}"
        )
        vision_bytes = _selected_pdf(file_bytes, batch) if content_type == "application/pdf" else file_bytes
        return _response_json(llm_client, deployment, _vision_input(content_type, vision_bytes, vision_prompt), "vision")

    for batch in _page_batches(vision_pages):
        try:
            batch_results = [enrich_vision_batch(batch)]
        except ExtractionJsonError as exc:
            exc.base_fields = base_fields
            raise
        except APIStatusError as exc:
            if not _is_oversized_vision_input(exc):
                raise
            batch_results = []
            for page in batch:
                try:
                    batch_results.append(enrich_vision_batch([page]))
                except ExtractionJsonError as retry_exc:
                    retry_exc.base_fields = base_fields
                    raise
                except APIStatusError as retry_exc:
                    if not _is_oversized_vision_input(retry_exc):
                        raise
                    page_number = page["pageNumber"]
                    logger.warning("Vision enrichment skipped for page %s of '%s': %s", page_number, blob_name, retry_exc)
                    findings.append(f"Vision enrichment skipped for page {page_number} because it exceeded the model input limit; Layout extraction remains available.")
        for vision_result in batch_results:
            vision_results.append(vision_result.get("fields") if isinstance(vision_result.get("fields"), dict) else {})
            findings.extend(str(finding) for finding in vision_result.get("findings", []) if isinstance(finding, str))
    fields = _merge_fields(base_fields, vision_results, deployment)
    logger.info("Extracted %s canonical fields from blob '%s'.", len(fields), blob_name)
    return {
        "schemaVersion": CANONICAL_SCHEMA_VERSION,
        "document": {"blobName": blob_name, "name": original_path, "contentType": content_type},
        "pageSelection": page_selection,
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
