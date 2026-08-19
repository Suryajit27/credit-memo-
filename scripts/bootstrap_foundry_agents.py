import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import FunctionTool, PromptAgentDefinition
from azure.identity import AzureCliCredential


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _validate_config(config: dict[str, Any], file_path: Path) -> None:
    required = ["name", "instructions", "tools"]
    missing = [key for key in required if key not in config]
    if missing:
        raise ValueError(f"{file_path}: missing required keys: {', '.join(missing)}")

    if not isinstance(config["tools"], list) or not config["tools"]:
        raise ValueError(f"{file_path}: tools must be a non-empty list")


def _tool_from_config(tool_config: dict[str, Any], file_path: Path) -> FunctionTool:
    if tool_config.get("type") != "function":
        raise ValueError(f"{file_path}: only function tools are supported")

    name = str(tool_config.get("name") or "").strip()
    if not name:
        raise ValueError(f"{file_path}: tool is missing 'name'")

    parameters = tool_config.get("parameters")
    if not isinstance(parameters, dict):
        raise ValueError(f"{file_path}: tool '{name}' is missing object 'parameters'")

    return FunctionTool(
        name=name,
        description=tool_config.get("description"),
        parameters=parameters,
        strict=bool(tool_config.get("strict", True)),
    )


def _config_hash(config: dict[str, Any]) -> str:
    packed = json.dumps(config, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(packed.encode("utf-8")).hexdigest()[:16]


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]

    for attr in ("version", "id", "name"):
        if hasattr(value, attr):
            return str(getattr(value, attr))
    return str(value)


def _upsert_prompt_agent(
    client: AIProjectClient,
    config: dict[str, Any],
    config_path: Path,
    model_deployment: str,
) -> dict[str, Any]:
    tools = [_tool_from_config(tool, config_path) for tool in config["tools"]]
    definition = PromptAgentDefinition(
        model=model_deployment,
        instructions=str(config["instructions"]),
        temperature=float(config.get("temperature", 0.2)),
        top_p=float(config.get("top_p", 1.0)),
        tools=tools,
    )

    agent_name = str(config["name"]).strip()
    version = client.agents.create_version(
        agent_name=agent_name,
        definition=definition,
        description=str(config.get("description") or ""),
        draft=False,
        metadata={
            "managed-by": "credit-memo-bootstrap",
            "config-hash": _config_hash(config),
            "config-file": config_path.name,
        },
    )

    client.agents.enable(agent_name)
    details = client.agents.get(agent_name)

    versions_obj = getattr(details, "versions", None)
    latest_version_obj = getattr(versions_obj, "latest", None) if versions_obj else None
    latest_version = str(getattr(latest_version_obj, "version", latest_version_obj or ""))
    latest_version_id = str(getattr(latest_version_obj, "id", ""))

    return {
        "name": str(details.name),
        "id": str(details.id),
        "state": str(getattr(details, "state", "")),
        "latestVersion": latest_version,
        "latestVersionId": latest_version_id,
        "createdVersion": str(getattr(version, "version", "")),
        "createdVersionId": str(getattr(version, "id", "")),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Create/update Foundry prompt agents from repo config files.")
    parser.add_argument("--project-endpoint", default=os.environ.get("FOUNDRY_PROJECT_ENDPOINT", ""))
    parser.add_argument("--model-deployment", default=os.environ.get("AZURE_OPENAI_DEPLOYMENT_NAME", ""))
    parser.add_argument(
        "--memo-config",
        default="config/foundry-agents/memo-agent.json",
        help="Path to memo agent config JSON",
    )
    parser.add_argument(
        "--chat-config",
        default="config/foundry-agents/chat-agent.json",
        help="Path to chat agent config JSON",
    )
    parser.add_argument(
        "--reporting-config",
        default="config/foundry-agents/admin-reporting-agent.json",
        help="Path to portfolio reporting agent config JSON",
    )
    parser.add_argument(
        "--write-env-file",
        default="",
        help="Optional path to write env lines for agent names/ids",
    )
    parser.add_argument(
        "--json-out",
        default="",
        help="Optional path to write full JSON result",
    )
    args = parser.parse_args()

    endpoint = str(args.project_endpoint or "").strip()
    model_deployment = str(args.model_deployment or "").strip()

    if not endpoint:
        print("Missing --project-endpoint (or FOUNDRY_PROJECT_ENDPOINT).", file=sys.stderr)
        return 2
    if not model_deployment:
        print("Missing --model-deployment (or AZURE_OPENAI_DEPLOYMENT_NAME).", file=sys.stderr)
        return 2

    memo_path = Path(args.memo_config)
    chat_path = Path(args.chat_config)
    reporting_path = Path(args.reporting_config)
    if not memo_path.exists() or not chat_path.exists() or not reporting_path.exists():
        print("Agent config files not found. Check --memo-config / --chat-config paths.", file=sys.stderr)
        return 2

    memo_config = _load_json(memo_path)
    chat_config = _load_json(chat_path)
    reporting_config = _load_json(reporting_path)
    _validate_config(memo_config, memo_path)
    _validate_config(chat_config, chat_path)
    _validate_config(reporting_config, reporting_path)

    credential = AzureCliCredential()
    client = AIProjectClient(endpoint=endpoint, credential=credential)

    try:
        memo_result = _upsert_prompt_agent(client, memo_config, memo_path, model_deployment)
        chat_result = _upsert_prompt_agent(client, chat_config, chat_path, model_deployment)
        reporting_result = _upsert_prompt_agent(client, reporting_config, reporting_path, model_deployment)
    finally:
        client.close()

    result = {
        "memoAgent": memo_result,
        "chatAgent": chat_result,
        "reportingAgent": reporting_result,
    }

    if args.write_env_file:
        env_path = Path(args.write_env_file)
        env_path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            f"FOUNDRY_MEMO_AGENT_NAME={memo_result['name']}",
            f"FOUNDRY_MEMO_AGENT_ID={memo_result['id']}",
            f"FOUNDRY_CHAT_AGENT_NAME={chat_result['name']}",
            f"FOUNDRY_CHAT_AGENT_ID={chat_result['id']}",
            f"FOUNDRY_ADMIN_REPORTING_AGENT_NAME={reporting_result['name']}",
            f"FOUNDRY_ADMIN_REPORTING_AGENT_ID={reporting_result['id']}",
        ]
        env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    safe_result = _json_safe(result)

    if args.json_out:
        json_path = Path(args.json_out)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(safe_result, indent=2) + "\n", encoding="utf-8")

    print(json.dumps(safe_result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
