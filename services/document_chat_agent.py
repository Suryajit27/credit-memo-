import json
import re
import time
import uuid
import asyncio

from services.agent_provider import create_agent
from services.cosmos_tracker import get_request_from_cosmos
from services.search_indexer import perform_hybrid_search
from utils.logging import logger


CHAT_SYSTEM_PROMPT = """You are a professional, request-scoped document assistant for a commercial loan underwriting workspace.

Rules:
1. Answer only from the documents indexed for the current requestId.
2. If the available evidence is incomplete, say so clearly and ask the user to retry after indexing completes.
3. Never answer from general knowledge or from unrelated requests.
4. Use Markdown for structure when helpful.
5. Every factual statement must use inline citation markers like [1], [2].
6. Keep the tone concise, professional, and evidence-driven.
7. If evidence is uncertain, prefer stating that it could not be verified in the indexed documents.
8. Use no more than 4 tool calls before producing your final answer.
"""


_STREAM_EVENT_QUEUES: dict[str, asyncio.Queue] = {}


def _sse(event: str, payload: dict) -> str:
    return f"data: {json.dumps({'event': event, **payload})}\n\n"


def _emit_stream_event(stream_id: str, event: str, payload: dict) -> None:
    q = _STREAM_EVENT_QUEUES.get(stream_id)
    if not q:
        return
    try:
        q.put_nowait((event, payload))
    except Exception:
        # Never fail tool execution because UI telemetry queue is unavailable.
        pass


def _chunk_for_stream(text: str) -> list[str]:
    if not text:
        return []

    # Keep whitespace attached so markdown/citations render correctly while streaming.
    pieces = re.findall(r"\S+\s*", text)
    if not pieces:
        return [text]

    # Prevent very large chunks from appearing as non-streaming bursts.
    final_chunks: list[str] = []
    for piece in pieces:
        if len(piece) <= 24:
            final_chunks.append(piece)
            continue
        for i in range(0, len(piece), 12):
            final_chunks.append(piece[i:i + 12])
    return final_chunks


def search_request_documents(request_id: str, query: str, top: int = 4, stream_id: str = "") -> str:
    """Search only the indexed documents for the active requestId and return formatted excerpts for citations."""
    stream_id = stream_id or request_id
    logger.info(f"Chat agent calling search_request_documents(query='{query}', top={top}, request_id='{request_id}')")

    start = time.perf_counter()
    _emit_stream_event(
        stream_id,
        "tool_call",
        {
            "tool": "search_request_documents",
            "query": query,
            "top": min(top, 10),
        },
    )

    result = perform_hybrid_search(
        query=query,
        request_id=request_id,
        top=min(top, 10),
        use_vector=True,
        allow_unscoped_fallback=False,
    )
    hits = result.get("results", [])

    _emit_stream_event(
        stream_id,
        "tool_result",
        {
            "tool": "search_request_documents",
            "query": query,
            "hitCount": len(hits),
            "latencyMs": int((time.perf_counter() - start) * 1000),
        },
    )

    if not hits:
        return "No relevant evidence found in the indexed documents for this request."

    items = []
    for idx, hit in enumerate(hits, start=1):
        items.append(
            f"[{idx}] {hit.get('doc_name', 'Unknown')} (p.{hit.get('page', 1)}, type: {hit.get('documentType', 'Unknown')}, score: {hit.get('score', 'N/A')}):\n{hit.get('content', '')}"
        )
    return "\n\n".join(items)


def _chat_tools(request_id: str, stream_id: str) -> list[dict]:
    def _search_request_documents(query: str, top: int = 4) -> str:
        return search_request_documents(request_id=request_id, query=query, top=top, stream_id=stream_id)

    return [
        {
            "definition": {
                "type": "function",
                "name": "search_request_documents",
                "description": "Search indexed documents for the current request and return citation-ready evidence excerpts.",
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "The evidence to retrieve for answering the user question.",
                        },
                        "top": {
                            "type": "integer",
                            "description": "How many excerpts to return (1-10).",
                            "minimum": 1,
                            "maximum": 10,
                        },
                    },
                    "required": ["query", "top"],
                },
                "strict": True,
            },
            "handler": _search_request_documents,
        }
    ]


def _format_history(history: list[dict] | None) -> str:
    if not history:
        return ""

    lines: list[str] = []
    for message in history[-8:]:
        role = str(message.get("role", "user")).strip().lower()
        content = str(message.get("content", "")).strip()
        if not content:
            continue
        label = "User" if role == "user" else "Assistant" if role == "assistant" else role.title()
        lines.append(f"{label}: {content}")

    return "\n".join(lines)


def _build_request_context(request_id: str) -> tuple[list[dict], list[dict]]:
    cosmos_doc = get_request_from_cosmos(request_id) or {}
    doc_list = cosmos_doc.get("documents", []) or []
    doc_summary = []
    for doc in doc_list:
        doc_summary.append({
            "fileName": doc.get("fileName") or doc.get("originalPath") or doc.get("blobName") or "Unknown",
            "documentType": doc.get("documentType") or "Unknown",
            "status": doc.get("status") or "Unknown",
            "blobName": doc.get("blobName") or "",
        })
    return doc_list, doc_summary


async def stream_document_chat(request_id: str, message: str, history: list[dict] | None = None):
    stream_id = f"chat_{uuid.uuid4().hex[:10]}"
    yield _sse("chat_start", {"requestId": request_id, "streamId": stream_id})
    yield _sse("status", {"phase": "validating", "message": "Validating request context..."})

    doc_list, doc_summary = _build_request_context(request_id)
    if not doc_list:
        yield _sse(
            "error",
            {
                "code": "request_not_ready",
                "message": f"No indexed documents are available yet for request {request_id}. Please upload and index the documents, then retry.",
            },
        )
        return

    search_probe = perform_hybrid_search(
        query=message.strip() or "loan request summary",
        request_id=request_id,
        top=1,
        use_vector=False,
        allow_unscoped_fallback=False,
    )
    if int(search_probe.get("totalCount", 0) or 0) <= 0:
        yield _sse(
            "error",
            {
                "code": "indexing_not_ready",
                "message": f"Documents for request {request_id} are still indexing. Please retry once indexing completes.",
                "indexingStatus": "running",
                "indexedCount": 0,
                "totalCount": len(doc_list),
            },
        )
        return

    yield _sse("status", {"phase": "retrieving_evidence", "message": "Preparing grounded retrieval tools..."})

    history_text = _format_history(history)
    doc_manifest = json.dumps(doc_summary, indent=2)
    user_prompt = (
        f"Request ID: {request_id}\n\n"
        f"Available documents for this request:\n{doc_manifest}\n\n"
        f"Conversation history:\n{history_text or 'No prior conversation.'}\n\n"
        f"User question:\n{message.strip()}\n\n"
        "Use the search_request_documents tool to retrieve evidence before answering. "
        "Use no more than 4 tool calls. "
        "Answer using only the indexed documents for this request. If the evidence is insufficient, say that it is not yet verifiable from the indexed documents and ask the user to retry after indexing completes."
    )

    agent = create_agent(CHAT_SYSTEM_PROMPT, tools=_chat_tools(request_id, stream_id), agent_kind="chat")
    event_queue: asyncio.Queue = asyncio.Queue()
    _STREAM_EVENT_QUEUES[stream_id] = event_queue

    async def _produce_agent_events() -> None:
        accumulated_text = ""
        try:
            _emit_stream_event(stream_id, "status", {"phase": "drafting", "message": "Generating answer..."})
            for chunk in agent.run_stream(user_prompt):
                if chunk:
                    clean = str(chunk).replace('\u258B', '')
                    accumulated_text += clean
                    for delta in _chunk_for_stream(clean):
                        _emit_stream_event(stream_id, "token_delta", {"delta": delta})

            if not accumulated_text.strip():
                _emit_stream_event(
                    stream_id,
                    "error",
                    {
                        "code": "empty_response",
                        "message": "The assistant could not generate a grounded answer from the indexed documents. Please retry after indexing completes.",
                    },
                )
                return

            _emit_stream_event(stream_id, "chat_complete", {"content": accumulated_text})
        except Exception as exc:
            logger.error(f"Error while streaming document chat for {request_id}: {exc}", exc_info=True)
            _emit_stream_event(
                stream_id,
                "error",
                {
                    "code": "chat_stream_failed",
                    "message": f"Unable to stream a grounded answer for request {request_id}.",
                },
            )
        finally:
            _emit_stream_event(stream_id, "done", {})

    try:
        producer_task = asyncio.create_task(_produce_agent_events())
        while True:
            event, payload = await event_queue.get()
            if event == "done":
                break
            yield _sse(event, payload)
        await producer_task
    finally:
        _STREAM_EVENT_QUEUES.pop(stream_id, None)
