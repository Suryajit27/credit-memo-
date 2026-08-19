import json
import re
import uuid

from services.agent_provider import create_agent
from services.reporting_schema import build_reporting_schema_tool, get_reporting_schema_catalog
from services.sql_loan_context import build_admin_reporting_tool
from utils.logging import logger


ADMIN_REPORTING_SYSTEM_PROMPT = """You are a portfolio reporting assistant for commercial loan operations.

Rules:
1. Answer only from the run_admin_report tool results.
2. Use the tool for factual portfolio data; do not invent values, columns, or records.
3. The tool accepts only read-only SELECT queries over its approved reporting views and exact catalog columns.
4. Use concise Markdown. Cite each factual statement from reporting data as [910].
5. State when no matching data is returned or when the requested data is unavailable.
6. Use no more than 3 tool calls before producing a final answer.
7. Never invent a view or column. Call get_reporting_schema whenever the embedded catalog does not clearly answer which column to use.
"""


def _sse(event: str, payload: dict) -> str:
    return f"data: {json.dumps({'event': event, **payload})}\n\n"


def _chunk_for_stream(text: str) -> list[str]:
    pieces = re.findall(r"\S+\s*", text)
    return pieces or ([text] if text else [])


def _format_history(history: list[dict] | None) -> str:
    if not history:
        return ""

    lines: list[str] = []
    for message in history[-8:]:
        role = str(message.get("role", "user")).strip().lower()
        content = str(message.get("content", "")).strip()
        if role in {"user", "assistant"} and content:
            lines.append(f"{role.title()}: {content}")
    return "\n".join(lines)


async def stream_admin_reporting_chat(message: str, history: list[dict] | None = None):
    stream_id = f"admin_reporting_{uuid.uuid4().hex[:10]}"
    yield _sse("chat_start", {"streamId": stream_id})
    yield _sse("status", {"phase": "preparing", "message": "Preparing portfolio reporting tools..."})

    prompt = (
        f"Reporting schema catalog:\n{json.dumps(get_reporting_schema_catalog(), indent=2)}\n\n"
        f"Conversation history:\n{_format_history(history) or 'No prior conversation.'}\n\n"
        f"User question:\n{message.strip()}\n\n"
        "Use run_admin_report for portfolio facts. Do not use request-scoped document or SQL tools. "
        "If the result is empty or cannot answer the question, say so clearly."
    )
    tool_events: list[dict] = []

    def _record_query(payload: dict) -> None:
        tool_events.append(payload)

    agent = create_agent(
        ADMIN_REPORTING_SYSTEM_PROMPT,
        tools=[build_reporting_schema_tool(), build_admin_reporting_tool(on_query=_record_query)],
        agent_kind="reporting",
    )

    try:
        yield _sse("status", {"phase": "reporting", "message": "Querying portfolio reporting data..."})
        accumulated_text = ""
        for chunk in agent.run_stream(prompt):
            while tool_events:
                yield _sse("tool_call", tool_events.pop(0))
            if not chunk:
                continue
            clean = str(chunk).replace("\u258B", "")
            accumulated_text += clean
            for delta in _chunk_for_stream(clean):
                yield _sse("token_delta", {"delta": delta})

        while tool_events:
            yield _sse("tool_call", tool_events.pop(0))

        if not accumulated_text.strip():
            yield _sse(
                "error",
                {
                    "code": "empty_response",
                    "message": "The reporting assistant could not generate an answer from the available portfolio data.",
                },
            )
            return

        yield _sse("chat_complete", {"content": accumulated_text})
    except Exception as exc:
        logger.error("Error while streaming admin reporting chat: %s", exc, exc_info=True)
        yield _sse(
            "error",
            {
                "code": "admin_reporting_stream_failed",
                "message": "Unable to stream a portfolio reporting response.",
            },
        )
