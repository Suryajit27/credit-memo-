import os
from datetime import datetime, timezone
from azure.cosmos import CosmosClient, PartitionKey
from utils.logging import logger


def _deduplicate_documents(documents: list) -> list:
    """Keep one request document per deterministic Blob path."""
    unique: dict[str, dict] = {}
    for index, document in enumerate(documents or []):
        blob_name = document.get("blobName")
        key = blob_name or f"_unkeyed_{index}"
        if key in unique:
            # Preserve previously reviewed extraction data when an upload refreshes metadata.
            unique[key] = {**unique[key], **document}
        else:
            unique[key] = document
    return list(unique.values())

def _get_cosmos_container():
    endpoint = os.environ.get("COSMOS_DB_ENDPOINT")
    key = os.environ.get("COSMOS_DB_KEY")
    db_name = os.environ.get("COSMOS_DB_DATABASE", "docintellidb")
    container_name = os.environ.get("COSMOS_DB_CONTAINER", "loan-documents-index")

    if not endpoint or not key:
        logger.warning("COSMOS_DB_ENDPOINT or COSMOS_DB_KEY not configured. Skipping Cosmos DB.")
        return None

    client = CosmosClient(endpoint, credential=key)
    db = client.create_database_if_not_exists(id=db_name)
    return db.create_container_if_not_exists(
        id=container_name,
        partition_key=PartitionKey(path="/requestId")
    )

def record_upload_in_cosmos(request_id: str, doc_info: dict) -> None:
    """
    Records or updates document upload entry in Azure Cosmos DB under the request_id.
    """
    try:
        container = _get_cosmos_container()
        if not container:
            return

        now = datetime.now(timezone.utc).isoformat()

        try:
            item = container.read_item(item=request_id, partition_key=request_id)
        except Exception:
            item = {
                "id": request_id,
                "requestId": request_id,
                "createdAt": now,
                "documents": [],
                "indexingStatus": "pending",
                "indexedCount": 0,
                "totalCount": 0,
                "indexingErrors": []
            }

        documents = _deduplicate_documents(item.get("documents", []))
        existing_document = next((document for document in documents if document.get("blobName") == doc_info.get("blobName")), None)
        if existing_document and existing_document.get("extraction"):
            doc_info = {**doc_info, "extraction": existing_document["extraction"]}
        documents = [document for document in documents if document.get("blobName") != doc_info.get("blobName")]
        documents.append(doc_info)
        item["updatedAt"] = now
        item["documents"] = documents
        item["totalCount"] = len(documents)

        container.upsert_item(item)
        logger.info(f"Updated Cosmos DB request record for requestId: {request_id}")

    except Exception as e:
        logger.error(f"Failed to record upload in Cosmos DB: {str(e)}", exc_info=True)

def update_indexing_status_in_cosmos(
    request_id: str,
    status: str,
    indexed_count: int = 0,
    total_count: int = 0,
    errors: list = None
) -> None:
    """
    Updates the indexing status metrics for a request_id in Cosmos DB.
    """
    try:
        container = _get_cosmos_container()
        if not container:
            return

        now = datetime.now(timezone.utc).isoformat()

        try:
            item = container.read_item(item=request_id, partition_key=request_id)
        except Exception:
            item = {
                "id": request_id,
                "requestId": request_id,
                "createdAt": now,
                "documents": []
            }

        item["documents"] = _deduplicate_documents(item.get("documents", []))
        item["indexingStatus"] = status
        item["indexedCount"] = indexed_count
        if total_count > 0:
            item["totalCount"] = total_count
        else:
            item["totalCount"] = len(item["documents"])
        item["indexingErrors"] = errors or []
        item["updatedAt"] = now

        if status == "running" and "indexingStartedAt" not in item:
            item["indexingStartedAt"] = now
        elif status in ["succeeded", "failed"]:
            item["indexingCompletedAt"] = now

        container.upsert_item(item)
        logger.info(f"Updated Cosmos DB indexing status for {request_id} -> {status}")

    except Exception as e:
        logger.error(f"Failed to update indexing status in Cosmos DB: {str(e)}", exc_info=True)

def get_request_from_cosmos(request_id: str) -> dict:
    """
    Fetches the request record from Cosmos DB.
    """
    try:
        container = _get_cosmos_container()
        if not container:
            return None
        item = container.read_item(item=request_id, partition_key=request_id)
        documents = _deduplicate_documents(item.get("documents", []))
        if len(documents) != len(item.get("documents", [])) or item.get("totalCount") != len(documents):
            item["documents"] = documents
            item["totalCount"] = len(documents)
            item["updatedAt"] = datetime.now(timezone.utc).isoformat()
            container.upsert_item(item)
            logger.info(f"Removed duplicate document records for requestId: {request_id}")
        return item
    except Exception as e:
        logger.warning(f"Could not read request {request_id} from Cosmos DB: {str(e)}")
        return None


def save_document_extraction(request_id: str, blob_name: str, extraction_data: dict) -> dict:
    """Persists the reviewer-finalized canonical extraction on the matching document."""
    container = _get_cosmos_container()
    if not container:
        raise ValueError("Cosmos DB is not configured.")

    item = container.read_item(item=request_id, partition_key=request_id)
    now = datetime.now(timezone.utc).isoformat()
    for document in item.get("documents", []):
        if document.get("blobName") == blob_name:
            document["extraction"] = {
                "status": "finalized",
                **extraction_data,
                "finalizedAt": now,
                "updatedAt": now,
            }
            item["updatedAt"] = now
            container.upsert_item(item)
            return document["extraction"]

    raise ValueError("The selected document does not belong to this request.")
