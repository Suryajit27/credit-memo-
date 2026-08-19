import os
import json
import re
from typing import Iterator, Callable, Any

from openai import OpenAI

from utils.logging import logger


def _load_local_settings_into_env() -> None:
    if not os.path.exists("local.settings.json"):
        return
    try:
        with open("local.settings.json", "r", encoding="utf-8") as f:
            settings = json.load(f)
        for key, value in settings.get("Values", {}).items():
            if key not in os.environ:
                os.environ[key] = str(value)
    except Exception as e:
        logger.warning(f"Unable to load local.settings.json for agent config: {e}")


def _chunk_for_stream(text: str) -> list[str]:
    if not text:
        return []
    pieces = re.findall(r"\S+\s*", text)
    if not pieces:
        return [text]
    final_chunks: list[str] = []
    for piece in pieces:
        if len(piece) <= 24:
            final_chunks.append(piece)
            continue
        for i in range(0, len(piece), 12):
            final_chunks.append(piece[i:i + 12])
    return final_chunks


class HostedAgentRunner:
    def __init__(
        self,
        instructions: str,
        agent_name: str = "",
        agent_id: str = "",
        tools: list[dict] | None = None,
        tool_handlers: dict[str, Callable[..., Any]] | None = None,
    ):
        if not (
            os.environ.get("FOUNDRY_PROJECT_ENDPOINT")
            or os.environ.get("AZURE_FOUNDRY_PROJECT_ENDPOINT")
            or os.environ.get("AZURE_OPENAI_ENDPOINT")
        ):
            _load_local_settings_into_env()

        endpoint = (
            os.environ.get("FOUNDRY_PROJECT_ENDPOINT")
            or os.environ.get("AZURE_FOUNDRY_PROJECT_ENDPOINT")
            or os.environ.get("AZURE_OPENAI_ENDPOINT")
            or ""
        ).rstrip("/")
        api_key = os.environ.get("FOUNDRY_API_KEY") or os.environ.get("AZURE_OPENAI_API_KEY") or os.environ.get("AZURE_OPENAI_KEY")
        deployment = os.environ.get("AZURE_OPENAI_DEPLOYMENT_NAME", "gpt-5.4-mini")

        if not endpoint:
            raise RuntimeError("FOUNDRY_PROJECT_ENDPOINT not set. Cannot create Foundry agent client.")
        if not api_key:
            raise RuntimeError("FOUNDRY_API_KEY (or AZURE_OPENAI_API_KEY/AZURE_OPENAI_KEY) not set.")
        if not agent_name and not agent_id:
            raise RuntimeError("Either Foundry agent name or id must be configured.")

        self.instructions = instructions
        self.agent_name = agent_name
        self.agent_id = agent_id
        self.deployment = deployment
        self.tools = tools or []
        self.tool_handlers = tool_handlers or {}
        self.client = OpenAI(api_key=api_key, base_url=f"{endpoint}/openai/v1")

    def _run_tool(self, name: str, arguments_json: str) -> str:
        handler = self.tool_handlers.get(name)
        if not handler:
            return json.dumps({"error": f"No handler registered for tool '{name}'"})

        try:
            args = json.loads(arguments_json or "{}") if isinstance(arguments_json, str) else {}
        except json.JSONDecodeError as exc:
            return json.dumps({"error": f"Invalid tool arguments for '{name}': {exc}"})

        try:
            result = handler(**args)
            if isinstance(result, str):
                return result
            return json.dumps(result)
        except Exception as exc:
            logger.error(f"Tool '{name}' failed: {exc}", exc_info=True)
            return json.dumps({"error": f"Tool '{name}' execution failed"})

    def run_text(self, user_prompt: str) -> str:
        body = {
            "agent_reference": {
                "type": "agent_reference",
            }
        }
        if self.agent_name:
            body["agent_reference"]["name"] = self.agent_name
        if self.agent_id:
            body["agent_reference"]["id"] = self.agent_id

        conversation = self.client.conversations.create()
        conversation_id = getattr(conversation, "id", None)
        if not conversation_id:
            raise RuntimeError("Failed to create Foundry conversation for tool-calling run.")

        if self.instructions.strip():
            response_input: Any = f"System instructions:\n{self.instructions}\n\nUser request:\n{user_prompt}"
        else:
            response_input = user_prompt
        try:
            for _ in range(20):
                create_kwargs = {
                    "model": self.deployment,
                    "input": response_input,
                    "conversation": conversation_id,
                    "extra_body": body,
                }
                response = self.client.responses.create(**create_kwargs)
                output_items = list(getattr(response, "output", []) or [])
                tool_calls = [item for item in output_items if getattr(item, "type", "") == "function_call"]

                if not tool_calls:
                    output_text = getattr(response, "output_text", None)
                    if output_text:
                        return output_text
                    return str(response)

                response_input = []
                for call in tool_calls:
                    call_id = getattr(call, "call_id", "")
                    name = getattr(call, "name", "")
                    arguments = getattr(call, "arguments", "{}")
                    output = self._run_tool(name, arguments)
                    response_input.append(
                        {
                            "type": "function_call_output",
                            "call_id": call_id,
                            "output": output,
                        }
                    )

            return "I could not complete the tool-calling loop within limits. Please retry with a narrower request."
        finally:
            try:
                self.client.conversations.delete(conversation_id=conversation_id)
            except Exception:
                pass

    def run_stream(self, user_prompt: str) -> Iterator[str]:
        body = {
            "agent_reference": {
                "type": "agent_reference",
            }
        }
        if self.agent_name:
            body["agent_reference"]["name"] = self.agent_name
        if self.agent_id:
            body["agent_reference"]["id"] = self.agent_id

        conversation = self.client.conversations.create()
        conversation_id = getattr(conversation, "id", None)
        if not conversation_id:
            raise RuntimeError("Failed to create Foundry conversation for streaming run.")

        if self.instructions.strip():
            response_input: Any = f"System instructions:\n{self.instructions}\n\nUser request:\n{user_prompt}"
        else:
            response_input = user_prompt
        try:
            for _ in range(20):
                create_kwargs = {
                    "model": self.deployment,
                    "input": response_input,
                    "conversation": conversation_id,
                    "extra_body": body,
                }

                response = None
                streamed_text = ""
                stream_api = getattr(self.client.responses, "stream", None)

                if callable(stream_api):
                    try:
                        with stream_api(**create_kwargs) as stream:
                            for event in stream:
                                event_type = getattr(event, "type", "")
                                if event_type == "response.output_text.delta":
                                    delta = getattr(event, "delta", "")
                                    if delta:
                                        piece = str(delta)
                                        streamed_text += piece
                                        yield piece
                        response = stream.get_final_response()
                    except Exception as exc:
                        logger.warning(f"Streaming response fallback to non-stream mode: {exc}")
                        response = None
                        streamed_text = ""

                if response is None:
                    response = self.client.responses.create(**create_kwargs)

                output_items = list(getattr(response, "output", []) or [])
                tool_calls = [item for item in output_items if getattr(item, "type", "") == "function_call"]

                if not tool_calls:
                    output_text = getattr(response, "output_text", None)
                    if output_text:
                        text = str(output_text)
                        if streamed_text:
                            if text.startswith(streamed_text):
                                remainder = text[len(streamed_text):]
                                if remainder:
                                    yield remainder
                        else:
                            for chunk in _chunk_for_stream(text):
                                yield chunk
                        return

                    if streamed_text:
                        return

                    for chunk in _chunk_for_stream(str(response)):
                        yield chunk
                    return

                response_input = []
                for call in tool_calls:
                    call_id = getattr(call, "call_id", "")
                    name = getattr(call, "name", "")
                    arguments = getattr(call, "arguments", "{}")
                    output = self._run_tool(name, arguments)
                    response_input.append(
                        {
                            "type": "function_call_output",
                            "call_id": call_id,
                            "output": output,
                        }
                    )

            for chunk in _chunk_for_stream(
                "I could not complete the tool-calling loop within limits. Please retry with a narrower request."
            ):
                yield chunk
        finally:
            try:
                self.client.conversations.delete(conversation_id=conversation_id)
            except Exception:
                pass


def create_agent(instructions: str, tools: list = None, agent_kind: str = "memo") -> HostedAgentRunner:
    if agent_kind == "chat":
        agent_name = os.environ.get("FOUNDRY_CHAT_AGENT_NAME") or os.environ.get("AZURE_FOUNDRY_CHAT_AGENT_NAME")
        agent_id = os.environ.get("FOUNDRY_CHAT_AGENT_ID") or os.environ.get("AZURE_FOUNDRY_CHAT_AGENT_ID")
        if not agent_name and not agent_id:
            raise RuntimeError("Set FOUNDRY_CHAT_AGENT_NAME or FOUNDRY_CHAT_AGENT_ID.")
    elif agent_kind == "reporting":
        agent_name = os.environ.get("FOUNDRY_ADMIN_REPORTING_AGENT_NAME") or os.environ.get("AZURE_FOUNDRY_ADMIN_REPORTING_AGENT_NAME")
        agent_id = os.environ.get("FOUNDRY_ADMIN_REPORTING_AGENT_ID") or os.environ.get("AZURE_FOUNDRY_ADMIN_REPORTING_AGENT_ID")
        if not agent_name and not agent_id:
            raise RuntimeError("Set FOUNDRY_ADMIN_REPORTING_AGENT_NAME or FOUNDRY_ADMIN_REPORTING_AGENT_ID.")
    else:
        agent_name = os.environ.get("FOUNDRY_MEMO_AGENT_NAME") or os.environ.get("AZURE_FOUNDRY_MEMO_AGENT_NAME")
        agent_id = os.environ.get("FOUNDRY_MEMO_AGENT_ID") or os.environ.get("AZURE_FOUNDRY_MEMO_AGENT_ID")
        if not agent_name and not agent_id:
            raise RuntimeError("Set FOUNDRY_MEMO_AGENT_NAME or FOUNDRY_MEMO_AGENT_ID.")

    tool_definitions: list[dict] = []
    tool_handlers: dict[str, Callable[..., Any]] = {}
    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        definition = tool.get("definition")
        handler = tool.get("handler")
        if isinstance(definition, dict):
            tool_definitions.append(definition)
            name = str(definition.get("name") or "").strip()
            if name and callable(handler):
                tool_handlers[name] = handler

    logger.info(
        f"Creating Foundry hosted agent runner (kind={agent_kind}, agent_name={agent_name}, agent_id={agent_id}, tools={len(tool_definitions)})"
    )
    return HostedAgentRunner(
        instructions=instructions,
        agent_name=agent_name or "",
        agent_id=agent_id or "",
        tools=tool_definitions,
        tool_handlers=tool_handlers,
    )
