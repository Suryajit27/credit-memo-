import azure.functions as func
import json
import asyncio
import time
from datetime import datetime, timezone
from utils.validation import validate_request, is_supported_file
from utils.logging import logger
from services.zip_processor import process_zip_to_temp
from services.file_discovery import discover_files
from classification.classifier import process_documents_concurrently, classify_single_file_bytes

from azurefunctions.extensions.http.fastapi import Request, StreamingResponse
from fastapi.responses import JSONResponse

app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)

@app.route(route="classify", methods=["POST"])
async def classify_documents(req: Request):
    start_time = time.time()
    logger.info("Received request to classify documents (batch ZIP mode).")

    try:
        form = await req.form()
        file = form.get("file")
        if not file:
            return JSONResponse(status_code=400, content={"error": "No file uploaded in the request. Must be multipart/form-data."})
        
        file_bytes = await file.read()
        is_valid, error_msg, zip_bytes = validate_request(file_bytes)
        if not is_valid:
            logger.warning(f"Validation failed: {error_msg}")
            return JSONResponse(status_code=400, content={"error": error_msg})

        with process_zip_to_temp(zip_bytes) as extract_dir:
            supported_files = discover_files(extract_dir)
            logger.info(f"Discovered {len(supported_files)} supported documents for classification.")

            if not supported_files:
                return JSONResponse(status_code=400, content={"error": "No supported documents found in the ZIP file."})

            logger.info("Starting classification process...")
            classification_response = await process_documents_concurrently(
                file_paths=supported_files,
                base_dir=extract_dir,
                concurrency_limit=5
            )

            response_dict = json.loads(classification_response.model_dump_json())

            execution_time = time.time() - start_time
            logger.info(f"Classification completed in {execution_time:.2f} seconds.")

            return JSONResponse(status_code=200, content=response_dict)

    except Exception as e:
        logger.error(f"An unexpected error occurred during processing: {str(e)}", exc_info=True)
        return JSONResponse(status_code=500, content={"error": "An internal server error occurred while processing the request."})


@app.route(route="classify-file", methods=["POST"])
async def classify_single_document(req: Request):
    start_time = time.time()
    logger.info("Received per-file classification request.")

    try:
        form = await req.form()
        file = form.get("file")
        if not file:
            return JSONResponse(status_code=400, content={"error": "No file uploaded. Expected multipart/form-data with a 'file' field."})

        file_bytes = await file.read()
        if len(file_bytes) == 0:
            return JSONResponse(status_code=400, content={"error": "Uploaded file is empty."})

        file_name = form.get("fileName") or (file.filename if hasattr(file, 'filename') else "unknown")

        if not is_supported_file(file_name):
            return JSONResponse(status_code=400, content={"error": f"Unsupported file type: {file_name}"})

        logger.info(f"Classifying single file: {file_name}")
        result = await classify_single_file_bytes(file_bytes, file_name)

        execution_time = time.time() - start_time
        logger.info(f"Per-file classification done in {execution_time:.2f}s for {file_name}")

        return JSONResponse(status_code=200, content=json.loads(result.model_dump_json()))

    except Exception as e:
        logger.error(f"Unexpected error in classify-file: {str(e)}", exc_info=True)
        return JSONResponse(status_code=500, content={"error": "An internal server error occurred."})


@app.route(route="upload", methods=["POST"])
async def upload_document(req: Request):
    start_time = time.time()
    logger.info("Received request to upload document to Azure Blob.")

    try:
        from services.blob_uploader import upload_document_to_blob
        from services.cosmos_tracker import record_upload_in_cosmos
        from models.response_models import UploadResponse, UploadDocumentResult

        form = await req.form()
        file = form.get("file")
        if not file:
            return JSONResponse(status_code=400, content={"error": "No file uploaded. Expected multipart/form-data."})

        file_bytes = await file.read()
        if len(file_bytes) == 0:
            return JSONResponse(status_code=400, content={"error": "Empty file field."})

        original_path = form.get("fileName") or (file.filename if hasattr(file, 'filename') else "unknown")
        request_id = form.get("requestId")
        document_type = form.get("documentType", "UNCLASSIFIED")
        status = form.get("status", "Success")

        try:
            conf_val = form.get("confidence")
            confidence = float(conf_val) if conf_val else None
        except (ValueError, TypeError):
            confidence = None

        if not request_id:
            return JSONResponse(status_code=400, content={"error": "Missing required 'requestId' parameter."})

        doc_result = upload_document_to_blob(
            file_bytes=file_bytes,
            original_path=original_path,
            request_id=request_id,
            document_type=document_type,
            confidence=confidence,
            status=status
        )

        record_upload_in_cosmos(request_id=request_id, doc_info=doc_result)

        resp = UploadResponse(
            requestId=request_id,
            uploadedDocument=UploadDocumentResult(**doc_result),
            message="Document successfully uploaded to Azure Blob and tracked in Cosmos DB."
        )

        logger.info(f"Successfully processed upload in {time.time() - start_time:.2f}s for {original_path}")
        return JSONResponse(status_code=200, content=json.loads(resp.model_dump_json()))

    except Exception as e:
        logger.error(f"Unexpected error in upload: {str(e)}", exc_info=True)
        return JSONResponse(status_code=500, content={"error": f"Internal server error during upload: {str(e)}"})


@app.route(route="trigger-indexing", methods=["POST"])
async def trigger_indexing_endpoint(req: Request):
    try:
        req_body = await req.json() if req.method == "POST" else {}
        request_id = req_body.get("requestId") or req.query_params.get("requestId")

        from services.search_indexer import trigger_indexer_run
        success = trigger_indexer_run(request_id)

        if success:
            return JSONResponse(status_code=200, content={"message": f"Indexer run triggered successfully for request {request_id}"})
        else:
            return JSONResponse(status_code=500, content={"error": "Failed to trigger AI Search indexer."})
    except Exception as e:
        logger.error(f"Error in trigger-indexing endpoint: {str(e)}", exc_info=True)
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.route(route="reset-indexer", methods=["POST"])
async def reset_indexer_endpoint(req: Request):
    try:
        from services.search_indexer import reset_and_rerun_indexer
        result = reset_and_rerun_indexer()

        status_code = 200 if result.get("success") else 500
        return JSONResponse(status_code=status_code, content=result)
    except Exception as e:
        logger.error(f"Error in reset-indexer endpoint: {str(e)}", exc_info=True)
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.route(route="indexer-status", methods=["GET"])
async def indexer_status_endpoint(req: Request):
    try:
        request_id = req.query_params.get("requestId")
        if not request_id:
            return JSONResponse(status_code=400, content={"error": "Missing required 'requestId' query parameter."})

        from services.search_indexer import get_indexer_status
        from models.response_models import IndexerStatusResponse

        status_data = get_indexer_status(request_id)
        resp = IndexerStatusResponse(**status_data)

        return JSONResponse(status_code=200, content=json.loads(resp.model_dump_json()))
    except Exception as e:
        logger.error(f"Error in indexer-status endpoint: {str(e)}", exc_info=True)
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.route(route="search", methods=["POST"])
async def search_endpoint(req: Request):
    try:
        req_body = await req.json()
        query = req_body.get("query")
        if not query:
            return JSONResponse(status_code=400, content={"error": "Missing required 'query' parameter in JSON body."})

        request_id = req_body.get("requestId")
        document_type = req_body.get("documentType")
        top = req_body.get("top", 5)

        from services.search_indexer import perform_hybrid_search
        from models.response_models import SearchResponse, SearchResultItem

        results_data = perform_hybrid_search(
            query=query,
            request_id=request_id,
            document_type=document_type,
            top=top
        )

        resp_items = [SearchResultItem(**item) for item in results_data.get("results", [])]
        resp = SearchResponse(
            results=resp_items,
            totalCount=results_data.get("totalCount", len(resp_items)),
            searchMode=results_data.get("searchMode", "keyword"),
            vectorUsed=bool(results_data.get("vectorUsed", False)),
            fallbackUsed=bool(results_data.get("fallbackUsed", False)),
            warning=results_data.get("warning"),
        )

        return JSONResponse(status_code=200, content=json.loads(resp.model_dump_json()))
    except Exception as e:
        logger.error(f"Error in search endpoint: {str(e)}", exc_info=True)
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.route(route="memo/stream", methods=["POST", "GET"])
async def memo_stream_endpoint(req: Request):
    try:
        if req.method == "POST":
            req_body = await req.json()
            request_id = req_body.get("requestId")
        else:
            request_id = req.query_params.get("requestId")

        if not request_id:
            return JSONResponse(status_code=400, content={"error": "Missing required 'requestId' parameter."})

        from services.credit_memo_agent import stream_full_drafting_flow
        
        return StreamingResponse(
            stream_full_drafting_flow(request_id),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    except Exception as e:
        logger.error(f"Error in memo/stream endpoint: {str(e)}", exc_info=True)
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.route(route="chat/stream", methods=["POST"])
async def chat_stream_endpoint(req: Request):
    try:
        req_body = await req.json()
        request_id = req_body.get("requestId")
        message = req_body.get("message")
        history = req_body.get("history") or []

        if not request_id or not message:
            return JSONResponse(status_code=400, content={"error": "Missing required 'requestId' or 'message' parameter."})

        from services.document_chat_agent import stream_document_chat

        return StreamingResponse(
            stream_document_chat(request_id, message, history),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    except Exception as e:
        logger.error(f"Error in chat/stream endpoint: {str(e)}", exc_info=True)
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.route(route="memo/start", methods=["POST"])
async def memo_start_endpoint(req: Request):
    try:
        req_body = await req.json()
        request_id = req_body.get("requestId") or req.query_params.get("requestId")
        if not request_id:
            return JSONResponse(status_code=400, content={"error": "Missing required 'requestId' parameter."})

        from services.credit_memo_agent import run_full_drafting_flow
        from models.response_models import CreditMemoRecord, MemoStartResponse

        memo_record_dict = await run_full_drafting_flow(request_id)
        memo_record = CreditMemoRecord(**memo_record_dict)

        resp = MemoStartResponse(
            requestId=request_id,
            memo=memo_record,
            message="Credit Memo initial analysis and section drafting completed successfully."
        )

        return JSONResponse(status_code=200, content=json.loads(resp.model_dump_json()))
    except Exception as e:
        logger.error(f"Error in memo/start endpoint: {str(e)}", exc_info=True)
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.route(route="memo/status", methods=["GET"])
async def memo_status_endpoint(req: Request):
    try:
        request_id = req.query_params.get("requestId")
        if not request_id:
            return JSONResponse(status_code=400, content={"error": "Missing required 'requestId' query parameter."})

        from services.memo_tracker import get_memo_record

        record_dict = get_memo_record(request_id)
        if not record_dict:
            return JSONResponse(
                status_code=200,
                content={
                    "requestId": request_id,
                    "status": "not_started",
                    "approvedCount": 0,
                    "totalSections": 0,
                    "sections": [],
                    "rationales": []
                }
            )

        return JSONResponse(status_code=200, content=record_dict)
    except Exception as e:
        logger.error(f"Error in memo/status endpoint: {str(e)}", exc_info=True)
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.route(route="memo/section/approve", methods=["POST"])
async def memo_section_approve_endpoint(req: Request):
    try:
        req_body = await req.json()
        request_id = req_body.get("requestId")
        section_name = req_body.get("sectionName")
        if not request_id or not section_name:
            return JSONResponse(status_code=400, content={"error": "Missing required 'requestId' or 'sectionName'."})

        from services.memo_tracker import update_section_status
        from models.response_models import CreditMemoRecord

        result = update_section_status(request_id=request_id, section_name=section_name, status="approved")
        if result:
            return JSONResponse(status_code=200, content=json.loads(CreditMemoRecord(**result).model_dump_json()))
        return JSONResponse(status_code=404, content={"error": "Memo record not found"})
    except Exception as e:
        logger.error(f"Error in memo/section/approve endpoint: {str(e)}", exc_info=True)
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.route(route="memo/section/regenerate", methods=["POST"])
async def memo_section_regenerate_endpoint(req: Request):
    try:
        req_body = await req.json()
        request_id = req_body.get("requestId")
        section_name = req_body.get("sectionName")
        reviewer_notes = req_body.get("reviewerNotes")

        if not request_id or not section_name:
            return JSONResponse(status_code=400, content={"error": "Missing required 'requestId' or 'sectionName'."})

        from services.credit_memo_agent import regenerate_section
        res = await regenerate_section(request_id=request_id, section_name=section_name, reviewer_notes=reviewer_notes)

        return JSONResponse(status_code=200, content=res)
    except Exception as e:
        logger.error(f"Error in memo/section/regenerate endpoint: {str(e)}", exc_info=True)
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.route(route="memo/finalize", methods=["POST"])
async def memo_finalize_endpoint(req: Request):
    try:
        req_body = await req.json()
        request_id = req_body.get("requestId")
        approver = req_body.get("approver", "CreditReviewer")

        if not request_id:
            return JSONResponse(status_code=400, content={"error": "Missing required 'requestId'."})

        from services.memo_tracker import get_memo_record, finalize_memo_record
        from services.blob_uploader import _get_blob_container_client
        from models.response_models import FinalizeMemoResponse

        record = get_memo_record(request_id)
        if not record:
            return JSONResponse(status_code=404, content={"error": f"No credit memo record found for {request_id}"})

        unapproved = [s["name"] for s in record.get("sections", []) if s.get("status") != "approved"]
        if unapproved:
            return JSONResponse(status_code=400, content={"error": f"Cannot finalize memo: The following sections are not yet approved: {', '.join(unapproved)}"})

        header = f"# COMMERCIAL CREDIT MEMORANDUM\n\n**Request ID:** `{request_id}`  \n**Version:** `v1.0`  \n**Approved By:** `{approver}`  \n**Date:** `{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}`\n\n---\n\n"
        sections_md = []
        for sec in record.get("sections", []):
            sections_md.append(sec.get("content", ""))

        full_markdown = header + "\n\n---\n\n".join(sections_md)

        # Chunk into ~6000 tokens each (~24000 chars) to stay under the 8000-token embedding limit
        MAX_CHUNK_CHARS = 24000
        chunks = [full_markdown[i:i+MAX_CHUNK_CHARS] for i in range(0, len(full_markdown), MAX_CHUNK_CHARS)]

        blob_path = f"final-credit-memos/{request_id}/v1.md"
        blob_client = _get_blob_container_client(
            container_name_env="BLOB_MEMO_CONTAINER_NAME",
            default_container_name="loan-credit-memos",
            ensure_exists=True,
        )
        if blob_client:
            try:
                for idx, chunk_text in enumerate(chunks):
                    chunk_name = f"v1_chunk_{idx}.md" if len(chunks) > 1 else "v1.md"
                    cp = f"final-credit-memos/{request_id}/{chunk_name}"
                    b_client = blob_client.get_blob_client(cp)
                    b_client.upload_blob(chunk_text.encode("utf-8"), overwrite=True,
                        metadata={
                            "requestid": str(request_id),
                            "documenttype": "CreditMemo",
                            "chunkindex": str(idx),
                            "totalchunks": str(len(chunks)),
                        })
                    logger.info(f"Uploaded credit memo chunk {idx+1}/{len(chunks)} to: {cp}")
            except Exception as e:
                logger.error(f"Failed uploading memo to Blob Storage: {str(e)}")

        updated_record = finalize_memo_record(request_id=request_id, approver=approver, blob_path=blob_path)

        resp = FinalizeMemoResponse(
            requestId=request_id,
            blobPath=blob_path,
            status="final",
            approvedBy=approver,
            approvedAt=updated_record.get("approvedAt", ""),
            message="Credit memo successfully finalized, signed, and published to Blob Storage."
        )

        return JSONResponse(status_code=200, content=json.loads(resp.model_dump_json()))
    except Exception as e:
        logger.error(f"Error in memo/finalize endpoint: {str(e)}", exc_info=True)
        return JSONResponse(status_code=500, content={"error": str(e)})
