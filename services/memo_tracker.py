import os
from datetime import datetime, timezone
from azure.cosmos import CosmosClient, PartitionKey
from utils.logging import logger

_COSMOS_CONTAINER_CACHE = {}

def _get_memo_container():
    if "memo_container" in _COSMOS_CONTAINER_CACHE:
        return _COSMOS_CONTAINER_CACHE["memo_container"]

    if not os.environ.get("COSMOS_DB_ENDPOINT") and os.path.exists("local.settings.json"):
        try:
            import json as _json
            with open("local.settings.json", "r") as f:
                settings = _json.load(f)
                for k, v in settings.get("Values", {}).items():
                    if k not in os.environ:
                        os.environ[k] = v
        except Exception:
            pass

    endpoint = os.environ.get("COSMOS_DB_ENDPOINT")
    key = os.environ.get("COSMOS_DB_KEY")
    db_name = os.environ.get("COSMOS_DB_DATABASE", "docintellidb")
    container_name = os.environ.get("COSMOS_DB_MEMO_CONTAINER", "credit-memos")

    if not endpoint or not key:
        logger.warning("COSMOS_DB_ENDPOINT or COSMOS_DB_KEY not configured. Memory tracker operating in local/mock mode.")
        return None

    try:
        client = CosmosClient(endpoint, credential=key)
        db = client.create_database_if_not_exists(id=db_name)
        container = db.create_container_if_not_exists(
            id=container_name,
            partition_key=PartitionKey(path="/requestId")
        )
        _COSMOS_CONTAINER_CACHE["memo_container"] = container
        return container
    except Exception as e:
        logger.error(f"Error accessing Cosmos DB container {container_name}: {str(e)}")
        return None

# In-memory store fallback when Cosmos DB is unavailable
_IN_MEMORY_MEMO_STORE = {}

def get_memo_record(request_id: str) -> dict:
    """
    Fetches the Credit Memo record for request_id from Cosmos DB or in-memory fallback.
    """
    try:
        container = _get_memo_container()
        if container:
            try:
                return container.read_item(item=request_id, partition_key=request_id)
            except Exception:
                pass
    except Exception as e:
        logger.warning(f"Failed reading memo from Cosmos DB: {str(e)}")

    return _IN_MEMORY_MEMO_STORE.get(request_id)

def init_memo_record(request_id: str, thread_id: str, sections_data: list) -> dict:
    """
    Creates or initializes a new CreditMemoRecord for a given request_id.
    sections_data: list of dicts with keys: name, rationale, status ('drafted')
    """
    now = datetime.now(timezone.utc).isoformat()
    record = {
        "id": request_id,
        "requestId": request_id,
        "threadId": thread_id,
        "version": 1,
        "status": "in_progress",
        "sections": [
            {
                "name": sec["name"],
                "status": sec.get("status", "drafted"),
                "content": sec.get("content", ""),
                "citations": sec.get("citations", []),
                "rationale": sec.get("rationale", ""),
                "regen_count": sec.get("regen_count", 0),
                "reviewer_notes": None
            }
            for sec in sections_data
        ],
        "createdBy": "OrchestratorAgent",
        "approvedBy": None,
        "approvedAt": None,
        "blobPath": None,
        "createdAt": now,
        "updatedAt": now
    }

    _IN_MEMORY_MEMO_STORE[request_id] = record

    container = _get_memo_container()
    if container:
        try:
            container.upsert_item(record)
            logger.info(f"Initialized Credit Memo record in Cosmos DB for requestId: {request_id}")
        except Exception as e:
            logger.error(f"Failed to upsert memo record to Cosmos DB: {str(e)}")

    return record

def save_section_draft(request_id: str, section_name: str, content: str, citations: list, rationale: str = None) -> dict:
    """
    Updates the content and citations for a specific section in the memo record.
    """
    record = get_memo_record(request_id)
    if not record:
        logger.error(f"Cannot save section draft: No memo record found for {request_id}")
        return None

    now = datetime.now(timezone.utc).isoformat()
    record["updatedAt"] = now

    found = False
    for sec in record["sections"]:
        if sec["name"].lower() == section_name.lower():
            sec["content"] = content
            sec["citations"] = citations
            sec["status"] = "drafted"
            if rationale:
                sec["rationale"] = rationale
            found = True
            break

    if not found:
        record["sections"].append({
            "name": section_name,
            "status": "drafted",
            "content": content,
            "citations": citations,
            "rationale": rationale or "",
            "regen_count": 0,
            "reviewer_notes": None
        })

    _IN_MEMORY_MEMO_STORE[request_id] = record

    container = _get_memo_container()
    if container:
        try:
            container.upsert_item(record)
            logger.info(f"Saved section draft '{section_name}' in Cosmos DB for requestId: {request_id}")
        except Exception as e:
            logger.error(f"Failed saving section draft in Cosmos DB: {str(e)}")

    return record

def update_section_status(request_id: str, section_name: str, status: str, reviewer_notes: str = None) -> dict:
    """
    Updates the approval/review status of a specific section ('approved', 'regenerating', 'manual_escalation').
    """
    record = get_memo_record(request_id)
    if not record:
        return None

    now = datetime.now(timezone.utc).isoformat()
    record["updatedAt"] = now

    for sec in record["sections"]:
        if sec["name"].lower() == section_name.lower():
            sec["status"] = status
            if reviewer_notes is not None:
                sec["reviewer_notes"] = reviewer_notes
            break

    # Check if all sections are approved -> update overall status to in_review
    all_approved = all(s.get("status") == "approved" for s in record["sections"])
    if all_approved and record["status"] != "final":
        record["status"] = "in_review"

    _IN_MEMORY_MEMO_STORE[request_id] = record

    container = _get_memo_container()
    if container:
        try:
            container.upsert_item(record)
        except Exception as e:
            logger.error(f"Failed updating section status in Cosmos DB: {str(e)}")

    return record

def increment_section_regen_count(request_id: str, section_name: str) -> tuple:
    """
    Increments section regen count up to MAX 2. Returns (new_count, can_regenerate).
    """
    record = get_memo_record(request_id)
    if not record:
        return (0, False)

    for sec in record["sections"]:
        if sec["name"].lower() == section_name.lower():
            current_count = sec.get("regen_count", 0)
            if current_count >= 2:
                sec["status"] = "manual_escalation"
                _IN_MEMORY_MEMO_STORE[request_id] = record
                container = _get_memo_container()
                if container:
                    try:
                        container.upsert_item(record)
                    except Exception:
                        pass
                return (current_count, False)

            sec["regen_count"] = current_count + 1
            sec["status"] = "regenerating"
            _IN_MEMORY_MEMO_STORE[request_id] = record
            container = _get_memo_container()
            if container:
                try:
                    container.upsert_item(record)
                except Exception:
                    pass
            return (sec["regen_count"], True)

    return (0, False)

def finalize_memo_record(request_id: str, approver: str, blob_path: str) -> dict:
    """
    Marks the memo record as final, recording approval metadata and published blob path.
    """
    record = get_memo_record(request_id)
    if not record:
        return None

    now = datetime.now(timezone.utc).isoformat()
    record["status"] = "final"
    record["approvedBy"] = approver or "CreditReviewer"
    record["approvedAt"] = now
    record["blobPath"] = blob_path
    record["updatedAt"] = now

    _IN_MEMORY_MEMO_STORE[request_id] = record

    container = _get_memo_container()
    if container:
        try:
            container.upsert_item(record)
            logger.info(f"Finalized Credit Memo record in Cosmos DB for {request_id}")
        except Exception as e:
            logger.error(f"Failed finalizing memo record in Cosmos DB: {str(e)}")

    return record
