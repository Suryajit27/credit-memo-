import json
import os
import re
import struct
from datetime import datetime, timezone
from typing import Callable

from azure.identity import DefaultAzureCredential

from utils.logging import logger

try:
    import pyodbc
except ImportError:
    pyodbc = None


SQL_ACCESS_TOKEN_ATTRIBUTE = 1256
SQL_CITATION_ID = 900
ADMIN_REPORTING_CITATION_ID = 910
MAX_ADMIN_REPORTING_ROWS = 100
ALLOWED_ADMIN_REPORTING_VIEWS = {
    "reporting.vw_loan_cases",
    "reporting.vw_relationship_profiles",
    "reporting.vw_loan_monitoring",
    "reporting.vw_credit_conditions",
    "reporting.vw_collateral_controls",
}
DISALLOWED_SQL_KEYWORDS = {
    "ALTER", "BACKUP", "CREATE", "DELETE", "DROP", "EXEC", "EXECUTE", "GRANT",
    "INSERT", "INTO", "MERGE", "RECONFIGURE", "REVOKE", "TRUNCATE", "UPDATE", "USE",
}


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


def _validate_admin_reporting_sql(sql: str) -> tuple[bool, str]:
    statement = sql.strip()
    if not statement:
        return False, "A SQL statement is required."
    if len(statement) > 4000:
        return False, "The SQL statement exceeds the 4,000-character limit."
    if ";" in statement or "--" in statement or "/*" in statement or "*/" in statement:
        return False, "Multiple statements and SQL comments are not allowed."
    if not re.match(r"^SELECT\b", statement, flags=re.IGNORECASE):
        return False, "Only SELECT statements are allowed."

    keywords = set(re.findall(r"\b[A-Za-z_]+\b", statement.upper()))
    forbidden = sorted(DISALLOWED_SQL_KEYWORDS & keywords)
    if forbidden:
        return False, f"Disallowed SQL keyword: {forbidden[0]}."

    sources = re.findall(
        r"\b(?:FROM|JOIN)\s+([\[\]\w.]+)",
        statement,
        flags=re.IGNORECASE,
    )
    if not sources:
        return False, "The query must select from an approved reporting view."

    normalized_sources = {source.replace("[", "").replace("]", "").lower() for source in sources}
    disallowed_sources = normalized_sources - ALLOWED_ADMIN_REPORTING_VIEWS
    if disallowed_sources:
        return False, f"Source is not approved for admin reporting: {sorted(disallowed_sources)[0]}."

    return True, ""


def run_admin_report(sql: str, purpose: str, max_rows: int = 50) -> str:
    """Execute a bounded read-only portfolio query for the POC reporting agent."""
    valid, reason = _validate_admin_reporting_sql(sql)
    if not valid:
        logger.warning("Rejected admin reporting query: %s", reason)
        return f"Admin reporting query was rejected: {reason}"

    try:
        max_rows = max(1, min(int(max_rows), MAX_ADMIN_REPORTING_ROWS))
    except (TypeError, ValueError):
        max_rows = 50

    try:
        with _database_connection() as connection:
            connection.timeout = 10
            cursor = connection.cursor()
            cursor.execute(sql)
            columns = [column[0] for column in cursor.description or []]
            rows = cursor.fetchmany(max_rows + 1)
    except Exception as exc:
        logger.warning("Admin reporting query failed: %s", exc)
        return "Admin reporting data is currently unavailable."

    truncated = len(rows) > max_rows
    result_rows = [
        {column: value for column, value in zip(columns, row)}
        for row in rows[:max_rows]
    ]
    result = {
        "source": "Azure SQL portfolio reporting views",
        "citationId": ADMIN_REPORTING_CITATION_ID,
        "purpose": purpose.strip()[:500],
        "columns": columns,
        "rows": result_rows,
        "rowCount": len(result_rows),
        "truncated": truncated,
        "retrievedAt": datetime.now(timezone.utc).isoformat(),
    }
    return f"[{ADMIN_REPORTING_CITATION_ID}] Portfolio reporting data.\n{json.dumps(result, default=str)}"


def build_admin_reporting_tool(on_query: Callable[[dict], None] | None = None) -> dict:
    def _run_admin_report(sql: str, purpose: str, maxRows: int = 50) -> str:
        if on_query:
            on_query(
                {
                    "tool": "run_admin_report",
                    "query": sql,
                    "purpose": purpose,
                    "maxRows": maxRows,
                }
            )
        return run_admin_report(sql=sql, purpose=purpose, max_rows=maxRows)

    return {
        "definition": {
            "type": "function",
            "name": "run_admin_report",
            "description": (
                "Run a read-only portfolio reporting query. Only SELECT statements "
                "against approved reporting views are accepted. Never use request-scoped tools for this task."
            ),
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "sql": {
                        "type": "string",
                        "description": (
                            "One SELECT statement using only reporting.vw_loan_cases, "
                            "reporting.vw_relationship_profiles, reporting.vw_loan_monitoring, "
                            "reporting.vw_credit_conditions, and reporting.vw_collateral_controls. "
                            "Use vw_loan_cases.loan_stage or status for pipeline stage; use domain columns "
                            "such as monitoring_status, condition_status, and control_status when relevant."
                        ),
                    },
                    "purpose": {
                        "type": "string",
                        "description": "A concise description of the portfolio question being answered.",
                    },
                    "maxRows": {
                        "type": "integer",
                        "description": "Maximum result rows to return (1-100).",
                        "minimum": 1,
                        "maximum": MAX_ADMIN_REPORTING_ROWS,
                    },
                },
                "required": ["sql", "purpose", "maxRows"],
            },
            "strict": True,
        },
        "handler": _run_admin_report,
    }
