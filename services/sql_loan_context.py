import json
import os
import struct
from datetime import datetime, timezone

from azure.identity import DefaultAzureCredential

from utils.logging import logger

try:
    import pyodbc
except ImportError:
    pyodbc = None


SQL_ACCESS_TOKEN_ATTRIBUTE = 1256
SQL_CITATION_ID = 900


def _database_connection():
    if pyodbc is None:
        raise RuntimeError("pyodbc is not installed")

    server = os.environ.get("SQL_SERVER", "").strip()
    database = os.environ.get("SQL_DATABASE", "").strip()
    driver = os.environ.get("SQL_ODBC_DRIVER", "ODBC Driver 18 for SQL Server").strip()
    if not server or not database:
        raise RuntimeError("SQL_SERVER and SQL_DATABASE must be configured")

    credential = DefaultAzureCredential(exclude_interactive_browser_credential=True)
    access_token = credential.get_token("https://database.windows.net/.default").token
    encoded_token = access_token.encode("utf-16-le")
    token_struct = struct.pack("<I", len(encoded_token)) + encoded_token
    connection_string = (
        f"Driver={{{driver}}};"
        f"Server=tcp:{server};"
        f"Database={database};"
        "Encrypt=yes;"
        "TrustServerCertificate=no;"
        "Connection Timeout=30;"
    )
    return pyodbc.connect(
        connection_string,
        attrs_before={SQL_ACCESS_TOKEN_ATTRIBUTE: token_struct},
    )


def get_loan_context(request_id: str) -> str:
    """Return only the current SQL operational context mapped to one request."""
    try:
        with _database_connection() as connection:
            cursor = connection.cursor()
            cursor.execute("EXEC dbo.usp_get_agent_loan_context @request_id = ?", request_id)
            row = cursor.fetchone()
            context_json = str(row[0]) if row and row[0] else ""
    except Exception as exc:
        logger.warning("SQL loan context is unavailable for request %s: %s", request_id, exc)
        return "Current SQL operational context is unavailable. Continue with request-scoped document evidence only."

    if not context_json:
        return "No SQL operational context is linked to this request. Continue with request-scoped document evidence only."

    try:
        context = json.loads(context_json)
    except json.JSONDecodeError:
        logger.error("SQL loan context returned invalid JSON for request %s", request_id)
        return "Current SQL operational context could not be read. Continue with request-scoped document evidence only."

    if not isinstance(context, dict):
        return "No SQL operational context is linked to this request. Continue with request-scoped document evidence only."

    context["retrievedAt"] = datetime.now(timezone.utc).isoformat()
    return (
        f"[{SQL_CITATION_ID}] SQL Server current operational context. "
        "Use this source for internal workflow, relationship, monitoring, closing-condition, and collateral-control facts.\n"
        f"{json.dumps(context, default=str)}"
    )


def build_loan_context_tool(request_id: str) -> dict:
    def _get_loan_context() -> str:
        return get_loan_context(request_id)

    return {
        "definition": {
            "type": "function",
            "name": "get_loan_context",
            "description": (
                "Retrieve the request-scoped current operational context from SQL Server. "
                "Use for internal workflow, bank relationship, monitoring, closing conditions, and collateral controls. "
                "Do not use it to replace submitted document evidence."
            ),
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {},
                "required": [],
            },
            "strict": True,
        },
        "handler": _get_loan_context,
    }
