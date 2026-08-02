import uuid
import json
import re
from datetime import datetime, timezone
from utils.logging import logger
from services.agent_provider import create_agent
from services.search_indexer import perform_hybrid_search
from services.cosmos_tracker import get_request_from_cosmos
from services.memo_tracker import (
    init_memo_record,
    save_section_draft,
    get_memo_record,
    increment_section_regen_count,
    update_section_status
)

TAXONOMY = [
    "Borrower Profile",
    "Financial Analysis",
    "Collateral & Valuation",
    "Repayment / Payment History",
    "Risk Assessment",
    "Covenants",
    "Regulatory / Compliance Notes",
    "Recommendation",
    "Other Considerations"
]


def _sse(event: str, payload: dict) -> str:
    return f"data: {json.dumps({'event': event, **payload})}\n\n"

ANALYSIS_SYSTEM_PROMPT = """You are a senior loan underwriting analyst. Your task is to determine which sections of a commercial credit memo taxonomy are relevant given the available documents for a loan request.

For each of the 9 taxonomy sections, decide whether it should be included or excluded and provide a brief rationale.

Return a JSON array of objects with fields:
- "name": section name
- "status": "drafted" or "excluded"
- "rationale": brief explanation

IMPORTANT: The "Recommendation" section should always be included at the end."""

DRAFTING_SYSTEM_PROMPT = """You are an expert commercial loan underwriter drafting a formal Credit Memo.

RULES:
1. Use the provided evidence context from indexed documents as your factual source.
2. Every factual statement or financial metric must include inline numeric citations like [1], [2].
3. Never output bracketed file citations such as [CreditReport..., p.1] or any non-numeric bracket format.
4. Citation markers must be numeric only and must map to evidence excerpts returned by the tool.
5. At the end of the section, append a 'Footnotes' block with one line per citation in the exact form: [n] <document name>, p.<page>.
6. Keep citations tightly attached to the referenced text (no extra words inside citation brackets).
7. Maintain rigorous professional tone with clear subheadings.
8. Use no more than 6 tool calls before producing your final section draft."""


def _search_documents_for_tool(request_id: str, query: str, top: int = 6) -> str:
    result = perform_hybrid_search(query=query, request_id=request_id, top=min(top, 10), use_vector=True)
    hits = result.get("results", [])
    if not hits:
        return "No relevant evidence found in indexed documents."
    items = []
    for idx, hit in enumerate(hits, start=1):
        items.append(
            f"[{idx}] {hit.get('doc_name', 'Unknown')} (p.{hit.get('page', 1)}, type: {hit.get('documentType', 'Unknown')}, score: {hit.get('score', 'N/A')}):\n{hit.get('content', '')}"
        )
    return "\n\n".join(items)


def _memo_tools(request_id: str) -> list[dict]:
    def _search_documents(query: str, top: int = 6) -> str:
        return _search_documents_for_tool(request_id=request_id, query=query, top=top)

    return [
        {
            "definition": {
                "type": "function",
                "name": "search_documents",
                "description": "Search indexed loan documents for this request and return cited evidence excerpts.",
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "What evidence to find (borrower details, metrics, risks, covenants, etc.).",
                        },
                        "top": {
                            "type": "integer",
                            "description": "How many excerpts to retrieve (1-10).",
                            "minimum": 1,
                            "maximum": 10,
                        },
                    },
                    "required": ["query", "top"],
                },
                "strict": True,
            },
            "handler": _search_documents,
        }
    ]


def _parse_section_selection(text: str) -> list:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1]
        cleaned = cleaned.rsplit("```", 1)[0]
    cleaned = cleaned.strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\[.*?\]", cleaned, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
    logger.error("Failed to parse section selection JSON, including all sections.")
    return [{"name": s, "status": "drafted", "rationale": "Default inclusion."} for s in TAXONOMY]


def _normalize_selected_sections(raw_sections: list) -> list[dict]:
    normalized: list[dict] = []
    taxonomy_lookup = {name.lower(): name for name in TAXONOMY}

    def _canonical_name(value: str) -> str:
        key = value.strip().lower()
        if key in taxonomy_lookup:
            return taxonomy_lookup[key]
        for tax_key, tax_name in taxonomy_lookup.items():
            if key in tax_key or tax_key in key:
                return tax_name
        return value.strip()

    for item in raw_sections or []:
        if isinstance(item, dict):
            name = _canonical_name(str(item.get("name", "")).strip())
            status = str(item.get("status", "drafted")).strip().lower() or "drafted"
            rationale = str(item.get("rationale", "")).strip() or "Included based on available request evidence."
            if not name:
                continue
            normalized.append(
                {
                    "name": name,
                    "status": "excluded" if status == "excluded" else "drafted",
                    "rationale": rationale,
                }
            )
            continue

        if isinstance(item, str):
            raw = item.strip()
            if not raw:
                continue
            if " - " in raw:
                left, right = raw.split(" - ", 1)
                name = _canonical_name(left)
                rationale = right.strip() or "Included based on available request evidence."
            elif ":" in raw:
                left, right = raw.split(":", 1)
                name = _canonical_name(left)
                rationale = right.strip() or "Included based on available request evidence."
            else:
                name = _canonical_name(raw)
                rationale = "Included based on available request evidence."
            if name:
                normalized.append({"name": name, "status": "drafted", "rationale": rationale})

    if not normalized:
        return [{"name": s, "status": "drafted", "rationale": "Default inclusion."} for s in TAXONOMY]

    # Ensure recommendation is always included.
    if not any(s.get("name") == "Recommendation" for s in normalized):
        normalized.append({"name": "Recommendation", "status": "drafted", "rationale": "Always included."})

    return normalized


async def run_analysis_phase(request_id: str) -> tuple:
    thread_id = f"thread_{request_id}_{uuid.uuid4().hex[:8]}"
    logger.info(f"Starting AI-driven Analysis Phase for request {request_id} (thread: {thread_id})")

    cosmos_doc = get_request_from_cosmos(request_id)
    doc_list = cosmos_doc.get("documents", []) if cosmos_doc else []
    doc_types = list({d.get("documentType", "Unknown") for d in doc_list})

    user_prompt = (
        f"Available documents for request {request_id}:\n"
        f"{json.dumps(doc_list, indent=2)}\n\n"
        f"Document types present: {doc_types}\n\n"
        f"Full taxonomy:\n{json.dumps(TAXONOMY, indent=2)}\n\n"
        "Decide which sections are relevant and return a JSON array."
    )

    agent = create_agent(ANALYSIS_SYSTEM_PROMPT, tools=_memo_tools(request_id), agent_kind="memo")
    response = agent.run_text(user_prompt)

    if not response:
        logger.warning("LLM returned no response for analysis phase; including all sections.")
        selected_sections = [{"name": s, "status": "drafted", "rationale": "Default inclusion."} for s in TAXONOMY]
    else:
        selected_sections = _normalize_selected_sections(_parse_section_selection(response))

    active_sections = [s for s in selected_sections if s.get("status") == "drafted"]
    init_memo_record(request_id=request_id, thread_id=thread_id, sections_data=active_sections)

    logger.info(f"AI analysis complete for {request_id}. Selected {len(active_sections)}/{len(TAXONOMY)} sections.")
    return thread_id, active_sections


async def draft_section(request_id: str, section_name: str) -> dict:
    logger.info(f"Drafting section '{section_name}' for request {request_id}")

    memo_rec = get_memo_record(request_id)
    doc_types = list({d.get("documentType", "Unknown") for d in (get_request_from_cosmos(request_id) or {}).get("documents", [])})
    thread_context = ""
    if memo_rec:
        for s in memo_rec.get("sections", []):
            if s.get("content") and s.get("name") != section_name:
                thread_context += f"\n--- Prior Drafted Section: {s['name']} ---\n{s['content']}\n"

    user_prompt = (
        f"Section to draft: {section_name}\n"
        f"Available document types: {doc_types}\n"
        f"Thread context from prior sections:\n{thread_context[:2000]}\n\n"
        "Use the search_documents tool to gather evidence for this section before drafting.\n"
        "Use no more than 6 tool calls.\n"
        "Write Markdown with strict numeric inline citations only (for example [1], [2]).\n"
        "Do not use document-name citations inside brackets.\n"
        "End with a Footnotes block in the exact format: [n] <document name>, p.<page>."
    )

    system_prompt = DRAFTING_SYSTEM_PROMPT + f"\nCurrent section: {section_name}"
    agent = create_agent(system_prompt, tools=_memo_tools(request_id), agent_kind="memo")
    response = agent.run_text(user_prompt)

    if response:
        save_section_draft(request_id=request_id, section_name=section_name, content=response, citations=[])
    else:
        logger.error(f"LLM returned no content for section '{section_name}'")

    return {"section_name": section_name, "content": response, "citations": []}


def _build_regeneration_prompts(section_name: str, doc_types: list[str], reviewer_notes: str, regen_count: int) -> tuple[str, str]:
    system = (
        f"You are an expert commercial loan underwriter revising the '{section_name}' section based on reviewer feedback.\n"
        f"Use the search_documents tool to find any additional evidence needed.\n"
        f"Regeneration attempt #{regen_count}/2.\n"
        f"Reviewer feedback: {reviewer_notes or 'Please refine detail and clarity.'}\n"
        "Write Markdown with strict numeric inline citations only (for example [1], [2])."
    )
    user = f"Available document types: {doc_types}\n\nRegenerate the '{section_name}' section addressing the feedback above."
    user += "\nUse no more than 6 tool calls."
    user += "\nEvery factual statement or financial metric must include inline numeric citations only (e.g., [1], [2])."
    user += "\nDo not use document-name citations inside brackets."
    user += "\nEnd with a Footnotes block in the exact format: [n] <document name>, p.<page>."
    return system, user


async def regenerate_section(request_id: str, section_name: str, reviewer_notes: str = None) -> dict:
    logger.info(f"Regenerating section '{section_name}' for request {request_id}")

    new_count, can_regen = increment_section_regen_count(request_id, section_name)
    if not can_regen:
        logger.warning(f"Section '{section_name}' exceeded max regenerations.")
        return {"status": "manual_escalation", "message": "Max 2 regenerations reached.", "regen_count": new_count}

    doc_types = list({d.get("documentType", "Unknown") for d in (get_request_from_cosmos(request_id) or {}).get("documents", [])})

    system, user = _build_regeneration_prompts(
        section_name=section_name,
        doc_types=doc_types,
        reviewer_notes=reviewer_notes,
        regen_count=new_count,
    )
    agent = create_agent(system, tools=_memo_tools(request_id), agent_kind="memo")
    response = agent.run_text(user)

    if response:
        save_section_draft(request_id=request_id, section_name=section_name, content=response, citations=[])
    else:
        logger.error(f"Regeneration returned no content for '{section_name}'")

    update_section_status(request_id, section_name, "drafted", reviewer_notes=reviewer_notes)
    return {"status": "drafted", "section_name": section_name, "regen_count": new_count, "content": response, "citations": []}


async def stream_regenerate_section(request_id: str, section_name: str, reviewer_notes: str = None):
    logger.info(f"Streaming regeneration for section '{section_name}' on request {request_id}")
    yield _sse("regen_started", {"request_id": request_id, "section_name": section_name})

    try:
        new_count, can_regen = increment_section_regen_count(request_id, section_name)
        if not can_regen:
            logger.warning(f"Section '{section_name}' exceeded max regenerations.")
            yield _sse(
                "manual_escalation",
                {
                    "status": "manual_escalation",
                    "message": "Max 2 regenerations reached.",
                    "section_name": section_name,
                    "regen_count": new_count,
                },
            )
            return

        doc_types = list(
            {
                d.get("documentType", "Unknown")
                for d in (get_request_from_cosmos(request_id) or {}).get("documents", [])
            }
        )
        system, user = _build_regeneration_prompts(
            section_name=section_name,
            doc_types=doc_types,
            reviewer_notes=reviewer_notes,
            regen_count=new_count,
        )

        yield _sse(
            "status",
            {
                "phase": "drafting",
                "message": f"Regenerating '{section_name}' with reviewer feedback.",
                "section_name": section_name,
                "regen_count": new_count,
            },
        )

        agent = create_agent(system, tools=_memo_tools(request_id), agent_kind="memo")
        accumulated_text = ""
        for chunk in agent.run_stream(user):
            if chunk:
                clean = str(chunk).replace("\u258B", "")
                accumulated_text += clean
                yield _sse("token_delta", {"section_name": section_name, "delta": clean})
                yield _sse("terminal_token", {"section_name": section_name, "token": clean})

        if not accumulated_text.strip():
            logger.error(f"Regeneration stream returned no content for '{section_name}'")
            update_section_status(request_id, section_name, "drafted", reviewer_notes=reviewer_notes)
            yield _sse(
                "regen_error",
                {
                    "code": "empty_response",
                    "status": "drafted",
                    "message": "No regenerated content was produced. Please retry.",
                    "section_name": section_name,
                    "regen_count": new_count,
                },
            )
            return

        save_section_draft(
            request_id=request_id,
            section_name=section_name,
            content=accumulated_text,
            citations=[],
        )
        update_section_status(
            request_id=request_id,
            section_name=section_name,
            status="drafted",
            reviewer_notes=reviewer_notes,
        )

        yield _sse(
            "regen_complete",
            {
                "status": "drafted",
                "section_name": section_name,
                "regen_count": new_count,
                "content": accumulated_text,
                "citations": [],
            },
        )
    except Exception as exc:
        logger.error(f"Error while streaming regenerate for {request_id}/{section_name}: {exc}", exc_info=True)
        yield _sse(
            "regen_error",
            {
                "code": "regenerate_stream_failed",
                "status": "error",
                "message": f"Unable to stream regenerated draft for '{section_name}'.",
                "section_name": section_name,
            },
        )
    finally:
        yield _sse("done", {"section_name": section_name})


async def stream_full_drafting_flow(request_id: str):
    doc_list = (get_request_from_cosmos(request_id) or {}).get("documents", [])
    doc_types = list({d.get("documentType", "Unknown") for d in doc_list})

    thread_id, active_sections = await run_analysis_phase(request_id)
    yield f"data: {json.dumps({'event': 'analysis_complete', 'thread_id': thread_id, 'active_sections': active_sections})}\n\n"

    for sec in active_sections:
        sec_name = sec["name"]
        yield f"data: {json.dumps({'event': 'section_start', 'section_name': sec_name})}\n\n"

        memo_rec = get_memo_record(request_id)
        thread_context = ""
        if memo_rec:
            for s in memo_rec.get("sections", []):
                if s.get("content") and s.get("name") != sec_name:
                    thread_context += f"\n--- Prior Drafted Section: {s['name']} ---\n{s['content']}\n"

        user_prompt = (
            f"Section to draft: {sec_name}\n"
            f"Available document types: {doc_types}\n"
            f"Thread context from prior sections:\n{thread_context[:2000]}\n\n"
            "Use the search_documents tool to gather evidence for this section before drafting.\n"
            "Use no more than 6 tool calls.\n"
            "Write Markdown with strict numeric inline citations only (for example [1], [2]).\n"
            "Do not use document-name citations inside brackets.\n"
            "End with a Footnotes block in the exact format: [n] <document name>, p.<page>."
        )
        system_prompt = DRAFTING_SYSTEM_PROMPT + f"\nCurrent section: {sec_name}"
        agent = create_agent(system_prompt, tools=_memo_tools(request_id), agent_kind="memo")

        accumulated_text = ""
        for chunk in agent.run_stream(user_prompt):
            if chunk:
                accumulated_text += chunk
                yield f"data: {json.dumps({'event': 'token_delta', 'section_name': sec_name, 'delta': chunk})}\n\n"
                yield f"data: {json.dumps({'event': 'terminal_token', 'section_name': sec_name, 'token': chunk})}\n\n"

        if not accumulated_text:
            logger.error(f"Streaming produced no content for '{sec_name}'")

        save_section_draft(request_id=request_id, section_name=sec_name, content=accumulated_text, citations=[])
        yield f"data: {json.dumps({'event': 'section_complete', 'section_name': sec_name, 'content': accumulated_text, 'citations': []})}\n\n"

    final_record = get_memo_record(request_id)
    yield f"data: {json.dumps({'event': 'flow_complete', 'memo': final_record})}\n\n"


async def run_full_drafting_flow(request_id: str) -> dict:
    thread_id, active_sections = await run_analysis_phase(request_id)
    for sec in active_sections:
        await draft_section(request_id, sec["name"])
    return get_memo_record(request_id)
