import os
import json
import requests
from utils.logging import logger
from services.cosmos_tracker import update_indexing_status_in_cosmos, get_request_from_cosmos

DEMO_ALREADY_INDEXED_REQUEST_ID = "sample-meridian-foods-2026"


def _escape_odata_literal(value: str) -> str:
    return (value or "").replace("'", "''")


def _count_indexed_documents_for_request(request_id: str) -> int:
    search_endpoint = os.environ.get("AZURE_SEARCH_ENDPOINT")
    index_name = os.environ.get("AZURE_SEARCH_INDEX_NAME", "loan-documents-index")
    admin_key = os.environ.get("AZURE_SEARCH_ADMIN_KEY")

    if not search_endpoint or not admin_key or not request_id:
        return 0

    url = f"{search_endpoint}/indexes/{index_name}/docs/search?api-version=2024-07-01"
    safe_request_id = _escape_odata_literal(request_id)
    scoped_filter = f"requestId eq '{safe_request_id}'"
    payload = {
        "search": "*",
        "filter": scoped_filter,
        "top": 0,
        "count": True,
        "select": "id",
    }

    try:
        r = requests.post(url, json=payload, headers=_get_headers(), timeout=20)
        if r.status_code == 200:
            data = r.json()
            return int(data.get("@search.count", data.get("@odata.count", 0)) or 0)
        logger.warning(f"Could not count indexed docs for request {request_id}: {r.status_code} (filter={scoped_filter})")
    except Exception as e:
        logger.warning(f"Exception while counting indexed docs for request {request_id}: {e}")

    # Fallback path when filter expression is unsupported or field mappings are inconsistent.
    # Pull a bounded window and count in-memory using requestId/blobName heuristics.
    fallback_payload = {
        "search": "*",
        "top": 1000,
        "count": True,
        "select": "id,requestId,blobName",
    }
    try:
        r = requests.post(url, json=fallback_payload, headers=_get_headers(), timeout=20)
        if r.status_code == 200:
            value = r.json().get("value", []) or []
            count = 0
            for doc in value:
                doc_request_id = doc.get("requestId")
                blob_name = doc.get("blobName") or ""
                if doc_request_id == request_id or (isinstance(blob_name, str) and blob_name.startswith(f"{request_id}/")):
                    count += 1
            return count
    except Exception:
        pass

    return 0

def _get_headers():
    admin_key = os.environ.get("AZURE_SEARCH_ADMIN_KEY")
    return {
        "Content-Type": "application/json",
        "api-key": admin_key
    }

def trigger_indexer_run(request_id: str = None) -> bool:
    """
    Triggers an Azure AI Search indexer run.
    """
    if request_id == DEMO_ALREADY_INDEXED_REQUEST_ID:
        logger.info(f"Skipping indexer trigger for already-indexed demo request {request_id}")
        return True

    search_endpoint = os.environ.get("AZURE_SEARCH_ENDPOINT")
    indexer_name = os.environ.get("AZURE_SEARCH_INDEXER_NAME", "loan-documents-indexer")

    if not search_endpoint or not os.environ.get("AZURE_SEARCH_ADMIN_KEY"):
        logger.warning("Azure AI Search keys not configured. Cannot trigger indexer run.")
        return False

    url = f"{search_endpoint}/indexers/{indexer_name}/run?api-version=2024-07-01"
    try:
        r = requests.post(url, headers=_get_headers())
        if r.status_code in [202, 200]:
            logger.info(f"Successfully triggered AI Search indexer run for request {request_id}")
            if request_id:
                update_indexing_status_in_cosmos(request_id=request_id, status="running")
            return True
        else:
            logger.error(f"Failed to trigger indexer run: {r.status_code} - {r.text}")
            return False
    except Exception as e:
        logger.error(f"Error triggering indexer run: {str(e)}", exc_info=True)
        return False

def reset_and_rerun_indexer() -> dict:
    """
    Resets the Azure AI Search indexer (clears its high-water mark), then triggers
    a fresh run so ALL blobs are re-processed with current field mappings.
    This fixes null requestId/documentType when field mappings were added after initial indexing.
    """
    search_endpoint = os.environ.get("AZURE_SEARCH_ENDPOINT")
    indexer_name = os.environ.get("AZURE_SEARCH_INDEXER_NAME", "loan-documents-indexer")

    if not search_endpoint or not os.environ.get("AZURE_SEARCH_ADMIN_KEY"):
        return {"success": False, "error": "Azure AI Search not configured."}

    headers = _get_headers()

    # Step 1: Reset the indexer (clears change-tracking state)
    reset_url = f"{search_endpoint}/indexers/{indexer_name}/reset?api-version=2024-07-01"
    try:
        r = requests.post(reset_url, headers=headers)
        if r.status_code not in [200, 204]:
            msg = f"Indexer reset failed: {r.status_code} - {r.text}"
            logger.error(msg)
            return {"success": False, "error": msg}
        logger.info(f"Indexer '{indexer_name}' reset successfully.")
    except Exception as e:
        return {"success": False, "error": str(e)}

    # Step 2: Trigger a fresh indexer run
    run_url = f"{search_endpoint}/indexers/{indexer_name}/run?api-version=2024-07-01"
    try:
        r = requests.post(run_url, headers=headers)
        if r.status_code in [200, 202]:
            logger.info(f"Indexer '{indexer_name}' re-run triggered after reset.")
            return {"success": True, "message": f"Indexer '{indexer_name}' reset and re-run triggered. All documents will be re-processed with current field mappings."}
        else:
            msg = f"Indexer run trigger failed after reset: {r.status_code} - {r.text}"
            logger.error(msg)
            return {"success": False, "error": msg}
    except Exception as e:
        return {"success": False, "error": str(e)}

def get_indexer_status(request_id: str) -> dict:
    """
    Fetches the status of the AI Search indexer and calculates status metrics for a specific requestId.
    """
    search_endpoint = os.environ.get("AZURE_SEARCH_ENDPOINT")
    indexer_name = os.environ.get("AZURE_SEARCH_INDEXER_NAME", "loan-documents-indexer")

    # Read current state from Cosmos DB
    cosmos_doc = get_request_from_cosmos(request_id)
    docs_in_request = (cosmos_doc or {}).get("documents", []) or []
    total_docs = int((cosmos_doc or {}).get("totalCount") or len(docs_in_request) or 0)
    cosmos_status = (cosmos_doc or {}).get("indexingStatus", "pending")
    cosmos_indexed_count = int((cosmos_doc or {}).get("indexedCount") or 0)

    if request_id == DEMO_ALREADY_INDEXED_REQUEST_ID:
        update_indexing_status_in_cosmos(
            request_id=request_id,
            status="succeeded",
            indexed_count=total_docs,
            total_count=total_docs,
        )
        return {
            "requestId": request_id,
            "indexingStatus": "succeeded",
            "indexedCount": total_docs,
            "totalCount": total_docs,
            "indexingErrors": [],
        }

    if not search_endpoint or not os.environ.get("AZURE_SEARCH_ADMIN_KEY"):
        return {
            "requestId": request_id,
            "indexingStatus": cosmos_status,
            "indexedCount": cosmos_indexed_count,
            "totalCount": total_docs,
            "indexingErrors": ["Azure AI Search not configured."]
        }

    url = f"{search_endpoint}/indexers/{indexer_name}/status?api-version=2024-07-01"
    try:
        r = requests.get(url, headers=_get_headers())
        if r.status_code == 200:
            data = r.json()
            last_result = data.get("lastResult", {})
            status_str = last_result.get("status", "unknown").lower()
            
            # Map indexer status to simple values
            if status_str in ["inprogress", "running"]:
                current_status = "running"
            elif status_str == "success":
                current_status = "succeeded"
            elif status_str == "transientfailure":
                current_status = "failed"
            elif status_str == "persistentfailure":
                current_status = "failed"
            else:
                current_status = status_str

            indexed_count = _count_indexed_documents_for_request(request_id)
            if indexed_count <= 0:
                indexed_count = cosmos_indexed_count
            
            # Filter errors for current request if any
            errors_raw = last_result.get("errors", [])
            errors = [e.get("errorMessage", str(e)) for e in errors_raw]
            if last_result.get("errorMessage"):
                errors.insert(0, str(last_result.get("errorMessage")))

            # Derive request-scoped status from request-scoped indexed count.
            if total_docs > 0:
                if indexed_count >= total_docs:
                    current_status = "succeeded"
                    indexed_count = total_docs
                elif indexed_count > 0:
                    current_status = "running"
                elif current_status not in ["running", "failed"]:
                    current_status = "pending"
            else:
                # When no expected docs are known yet, avoid reporting global succeeded by default.
                if current_status == "succeeded":
                    current_status = cosmos_status if cosmos_status in ["pending", "running", "failed"] else "pending"

            # Update Cosmos DB with updated metrics
            update_indexing_status_in_cosmos(
                request_id=request_id,
                status=current_status,
                indexed_count=indexed_count,
                total_count=total_docs,
                errors=errors
            )

            return {
                "requestId": request_id,
                "indexingStatus": current_status,
                "indexedCount": indexed_count,
                "totalCount": total_docs,
                "indexingErrors": errors,
                "indexingStartedAt": last_result.get("startTime"),
                "indexingCompletedAt": last_result.get("endTime")
            }
        else:
            logger.error(f"Error fetching indexer status: {r.status_code} - {r.text}")
    except Exception as e:
        logger.error(f"Exception fetching indexer status: {str(e)}", exc_info=True)

    # Fallback to Cosmos DB record
    return {
        "requestId": request_id,
        "indexingStatus": cosmos_status,
        "indexedCount": cosmos_indexed_count,
        "totalCount": total_docs,
        "indexingErrors": cosmos_doc.get("indexingErrors", []) if cosmos_doc else []
    }


def generate_embedding(text: str) -> list[float]:
    endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
    key = os.environ.get("AZURE_OPENAI_KEY")
    deployment = os.environ.get("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "text-embedding-3-small")
    if not endpoint or not key:
        logger.warning("Azure OpenAI not configured for embeddings. Skipping vector search.")
        return None
    try:
        url = f"{endpoint.rstrip('/')}/openai/deployments/{deployment}/embeddings?api-version=2024-06-01"
        headers = {"Content-Type": "application/json", "api-key": key}
        resp = requests.post(url, json={"input": text}, headers=headers, timeout=30)
        if resp.status_code == 200:
            return resp.json()["data"][0]["embedding"]
        logger.warning(f"Embedding API returned {resp.status_code}")
        return None
    except Exception as e:
        logger.error(f"Error generating embedding: {e}")
        return None


def perform_hybrid_search(query: str, request_id: str = None, document_type: str = None, top: int = 5, use_vector: bool = True, allow_unscoped_fallback: bool = True) -> dict:
    """
    Performs text + optional vector search against Azure AI Search index.
    Falls back to unscoped search if requestId-filtered search returns 0 results unless disabled.
    """
    # Load local.settings.json if env vars not yet in environment
    if not os.environ.get("AZURE_SEARCH_ENDPOINT") and os.path.exists("local.settings.json"):
        try:
            import json as _json
            with open("local.settings.json", "r") as f:
                settings = _json.load(f)
                for k, v in settings.get("Values", {}).items():
                    if k not in os.environ:
                        os.environ[k] = v
        except Exception:
            pass

    search_endpoint = os.environ.get("AZURE_SEARCH_ENDPOINT")
    index_name = os.environ.get("AZURE_SEARCH_INDEX_NAME", "loan-documents-index")
    admin_key = os.environ.get("AZURE_SEARCH_ADMIN_KEY")

    if not search_endpoint or not admin_key:
        logger.error("Azure Search endpoint or admin key not configured.")
        return {"results": [], "totalCount": 0, "searchMode": "keyword", "vectorUsed": False, "fallbackUsed": False}

    url = f"{search_endpoint}/indexes/{index_name}/docs/search?api-version=2024-07-01"

    query_text = (query or "").strip()
    embedding = generate_embedding(query) if use_vector else None
    if use_vector and embedding and query_text:
        search_mode = "hybrid"
    elif use_vector and embedding and not query_text:
        search_mode = "vector"
    else:
        search_mode = "keyword"
    vector_used = bool(use_vector and embedding)

    def _do_search(filter_expr: str = None) -> dict:
        payload = {
            "search": query,
            "select": "id,content,documentType,blobName,confidence,requestId",
            "top": top,
            "count": True,
            "queryType": "simple"
        }
        if filter_expr:
            payload["filter"] = filter_expr
        if embedding:
            payload["vectorQueries"] = [{"kind": "vector", "vector": embedding, "fields": "contentVector", "k": top}]
            if filter_expr:
                payload["vectorFilterMode"] = "preFilter"

        logger.info(f"Executing search | query='{query}' | filter='{filter_expr}' | top={top}")
        r = requests.post(url, json=payload, headers=_get_headers())

        if r.status_code == 200:
            data = r.json()
            raw_results = data.get("value", [])
            logger.info(f"Search returned {len(raw_results)} result(s) (filter='{filter_expr}')")
            formatted = []
            for doc in raw_results:
                blob_name = doc.get("blobName")
                doc_name_val = doc.get("doc_name") or (blob_name.split("/")[-1] if blob_name else "Document.pdf")
                doc_id_val = doc.get("doc_id") or doc.get("id") or blob_name or "doc_1"
                page_val = doc.get("page", 1)
                chunk_id_val = doc.get("chunk_id") or f"{doc_id_val}_c1"
                req_id_val = doc.get("requestId") or doc.get("metadata_requestid")
                if not req_id_val:
                    # Fallback 1: Extract from blobName path e.g. ".../req-1234/file.pdf"
                    if blob_name and "/" in blob_name:
                        parts = blob_name.split("/")
                        for p in parts:
                            if p.startswith("req-"):
                                req_id_val = p
                                break
                    # Fallback 2: Decode base64 key ID
                    elif doc.get("id"):
                        try:
                            import base64
                            b64_str = doc["id"]
                            b64_str += "=" * (-len(b64_str) % 4)
                            decoded = base64.b64decode(b64_str).decode("utf-8", errors="ignore")
                            parts = [p for p in decoded.split("/") if p]
                            for p_val in parts:
                                if p_val.startswith("req-"):
                                    req_id_val = p_val
                                    break
                            if not req_id_val and len(parts) >= 2:
                                req_id_val = parts[-2]
                        except Exception:
                            pass

                # documentType fallback: check documentType or extract from blobName
                doc_type_val = doc.get("documentType")
                if not doc_type_val and blob_name:
                    filename = blob_name.split("/")[-1] if "/" in blob_name else blob_name
                    if "_" in filename:
                        doc_type_val = filename.split("_")[0]
                        if doc_type_val == "UNCLASSIFIED":
                            doc_type_val = None

                formatted.append({
                    "content": doc.get("content", ""),
                    "documentType": doc_type_val,
                    "blobName": blob_name,
                    "confidence": doc.get("confidence"),
                    "score": doc.get("@search.score"),
                    "doc_id": doc_id_val,
                    "doc_name": doc_name_val,
                    "page": page_val,
                    "chunk_id": chunk_id_val,
                    "requestId": req_id_val or "unassigned"
                })
                return {
                    "results": formatted,
                    "totalCount": data.get("@search.count", len(formatted)),
                    "searchMode": search_mode,
                    "vectorUsed": vector_used,
                    "fallbackUsed": False,
                }
        else:
            logger.error(f"Search failed ({r.status_code}): {r.text}")
            return {"results": [], "totalCount": 0, "searchMode": search_mode, "vectorUsed": vector_used, "fallbackUsed": False}

    # Build filter - check requestId or blobName path match (*request_id*)
    filter_clauses = []
    if request_id:
        filter_clauses.append(f"requestId eq '{request_id}'")
    if document_type:
        filter_clauses.append(f"documentType eq '{document_type}'")
    filter_expr = " and ".join(filter_clauses) if filter_clauses else None

    try:
        result = _do_search(filter_expr)

        # If filtered search returns 0 results but a filter was applied,
        # fall back to unscoped search then filter in-memory using parsed requestId/blobName
        if allow_unscoped_fallback and result["totalCount"] == 0 and filter_expr:
            logger.warning(f"Filtered search returned 0 results. Falling back to unscoped search with in-memory filtering for {request_id}.")
            unscoped_result = _do_search(filter_expr=None)
            all_items = unscoped_result.get("results", [])
            
            # Post-filter in memory using our fallback-resolved requestId / blobName path
            if request_id:
                filtered_items = [
                    item for item in all_items 
                    if item.get("requestId") == request_id or (item.get("blobName") and item.get("blobName").startswith(f"{request_id}/"))
                ]
            else:
                filtered_items = all_items

            if filtered_items:
                result = {
                    "results": filtered_items,
                    "totalCount": len(filtered_items),
                    "searchMode": search_mode,
                    "vectorUsed": vector_used,
                    "fallbackUsed": True,
                }
            else:
                result = unscoped_result
                result["fallbackUsed"] = True
                result["warning"] = f"No documents found matching requestId '{request_id}'; showing all indexed documents."

        return result
    except Exception as e:
        logger.error(f"Exception during search: {str(e)}", exc_info=True)
        return {"results": [], "totalCount": 0, "searchMode": search_mode, "vectorUsed": vector_used, "fallbackUsed": False}
