import os
import re
from datetime import datetime, timezone
from azure.storage.blob import BlobServiceClient
from utils.logging import logger

def _get_blob_container_client(
    container_name_env: str = "BLOB_CONTAINER_NAME",
    default_container_name: str = "loan-documents",
    ensure_exists: bool = False,
):
    """Retrieves Azure Blob Container Client using connection string from environment."""
    # Load local.settings.json if env vars not yet populated
    if not os.environ.get("AZURE_STORAGE_CONNECTION_STRING") and os.path.exists("local.settings.json"):
        try:
            import json as _json
            with open("local.settings.json", "r") as f:
                settings = _json.load(f)
                for k, v in settings.get("Values", {}).items():
                    if k not in os.environ:
                        os.environ[k] = v
        except Exception:
            pass

    conn_string = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
    container_name = os.environ.get(container_name_env, default_container_name)
    if not conn_string:
        return None
    try:
        blob_service_client = BlobServiceClient.from_connection_string(conn_string)
        container_client = blob_service_client.get_container_client(container_name)
        if ensure_exists:
            try:
                container_client.create_container()
            except Exception:
                pass
        return container_client
    except Exception:
        return None

def sanitize_filename(name: str) -> str:
    """Sanitize filename to be safe for blob names."""
    return re.sub(r'[^a-zA-Z0-9_.\-]', '_', name)

def upload_document_to_blob(
    file_bytes: bytes,
    original_path: str,
    request_id: str,
    document_type: str,
    confidence: float = None,
    status: str = "Success"
) -> dict:
    """
    Uploads a document to Azure Blob Storage under:
    <request_id>/<applicant_folder>/<classified_doc_type>_<original_filename>

    Sets metadata on the blob including documentType, confidence, uploadTimestamp, and requestId.
    """
    conn_string = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
    container_name = os.environ.get("BLOB_CONTAINER_NAME", "loan-documents")

    if not conn_string:
        raise ValueError("Missing AZURE_STORAGE_CONNECTION_STRING environment variable.")

    blob_service_client = BlobServiceClient.from_connection_string(conn_string)
    container_client = blob_service_client.get_container_client(container_name)

    # Ensure container exists
    try:
        container_client.create_container()
    except Exception:
        pass # Container already exists or permissions handled

    # Parse path parts
    normalized_path = original_path.replace("\\", "/")
    path_parts = [p for p in normalized_path.split("/") if p]
    
    if len(path_parts) > 1:
        folder_prefix = "/".join(path_parts[:-1])
        orig_filename = path_parts[-1]
    else:
        folder_prefix = ""
        orig_filename = path_parts[0] if path_parts else "document.pdf"

    filename_base, ext = os.path.splitext(orig_filename)

    # Determine classified file name
    clean_doc_type = sanitize_filename(document_type) if document_type else "UNCLASSIFIED"
    clean_orig_name = sanitize_filename(orig_filename)

    if status != "Success" or clean_doc_type == "UNCLASSIFIED":
        new_filename = f"UNCLASSIFIED_{clean_orig_name}"
    else:
        new_filename = f"{clean_doc_type}_{clean_orig_name}"

    # Build blob path: <request_id>/<applicant_folder>/<classified_doc_type>_<original_filename>
    if folder_prefix:
        blob_name = f"{request_id}/{folder_prefix}/{new_filename}"
    else:
        blob_name = f"{request_id}/{new_filename}"

    upload_time = datetime.now(timezone.utc).isoformat()

    # Prepare metadata (lower-case key names explicitly for Azure Blob Storage metadata compatibility)
    metadata = {
        "requestid": str(request_id),
        "documenttype": str(clean_doc_type),
        "confidence": str(confidence) if confidence is not None else "N/A",
        "uploadtimestamp": str(upload_time),
        "originalpath": str(original_path),
        "status": str(status)
    }

    logger.info(f"Uploading blob '{blob_name}' to container '{container_name}'...")
    blob_client = container_client.get_blob_client(blob_name)
    blob_client.upload_blob(file_bytes, overwrite=True, metadata=metadata)
    logger.info(f"Successfully uploaded blob '{blob_name}'.")

    return {
        "originalPath": original_path,
        "blobName": blob_name,
        "documentType": clean_doc_type,
        "confidence": confidence,
        "status": status,
        "uploadedAt": upload_time
    }


def download_blob(blob_name: str, container_name_env: str = "BLOB_CONTAINER_NAME", default_container_name: str = "loan-documents") -> tuple[bytes | None, str | None]:
    """Download a blob by name. Returns (content_bytes, content_type) or (None, None)."""
    container_client = _get_blob_container_client(container_name_env, default_container_name)
    if not container_client:
        logger.warning(f"Cannot download blob '{blob_name}': blob storage not configured.")
        return None, None
    try:
        blob_client = container_client.get_blob_client(blob_name)
        stream = blob_client.download_blob()
        content_type = blob_client.get_blob_properties().content_settings.content_type or "application/octet-stream"
        return stream.readall(), content_type
    except Exception as e:
        logger.error(f"Failed to download blob '{blob_name}': {e}")
        return None, None
