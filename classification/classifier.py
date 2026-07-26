import os
import asyncio
from typing import List
from azure.ai.documentintelligence.aio import DocumentIntelligenceClient
from classification.client import get_document_client
from models.response_models import DocumentResult, ClassificationResponse, Summary
from utils.logging import logger

async def classify_document_async(client: DocumentIntelligenceClient, classifier_id: str, file_path: str, base_dir: str) -> DocumentResult:
    """
    Asynchronously classifies a single document using the Document Intelligence client.
    """
    # Calculate the relative path to use as the fileName in the response
    relative_file_name = os.path.relpath(file_path, base_dir)
    # Convert Windows backslashes to forward slashes for cross-platform consistency in API response
    relative_file_name = relative_file_name.replace("\\", "/")

    try:
        logger.info(f"Starting classification for {relative_file_name}")
        with open(file_path, "rb") as f:
            file_bytes = f.read()

        poller = await client.begin_classify_document(
            classifier_id=classifier_id,
            classify_request={"base64Source": file_bytes}
        )
        result = await poller.result()

        if result.documents:
            # Assuming the first document classification result is the most relevant for the whole file
            doc = result.documents[0]
            doc_type = doc.doc_type
            confidence = doc.confidence
            logger.info(f"Successfully classified {relative_file_name} as {doc_type} with confidence {confidence}")
            return DocumentResult(
                fileName=relative_file_name,
                documentType=doc_type,
                confidence=confidence,
                status="Success"
            )
        else:
            logger.warning(f"No document classification results returned for {relative_file_name}")
            return DocumentResult(
                fileName=relative_file_name,
                status="Failed",
                error="No classification results returned by the model."
            )

    except Exception as e:
        logger.error(f"Failed to classify {relative_file_name}: {str(e)}")
        return DocumentResult(
            fileName=relative_file_name,
            status="Failed",
            error=str(e)
        )

async def classify_single_file_bytes(file_bytes: bytes, file_name: str) -> DocumentResult:
    """
    Classifies a single file provided as raw bytes.
    Used by the per-file endpoint so the UI can stream results one-by-one.

    Args:
        file_bytes: The raw bytes of the file to classify.
        file_name: The display name of the file (e.g. "Applicant/DriverLicense.pdf").

    Returns:
        A DocumentResult with the classification outcome.
    """
    classifier_id = os.environ.get("CLASSIFIER_ID")
    if not classifier_id:
        raise ValueError("Missing CLASSIFIER_ID environment variable.")

    client = get_document_client()
    try:
        logger.info(f"Starting per-file classification for: {file_name}")
        poller = await client.begin_classify_document(
            classifier_id=classifier_id,
            classify_request={"base64Source": file_bytes}
        )
        result = await poller.result()

        if result.documents:
            doc = result.documents[0]
            logger.info(f"Classified {file_name} as {doc.doc_type} (confidence={doc.confidence})")
            return DocumentResult(
                fileName=file_name,
                documentType=doc.doc_type,
                confidence=doc.confidence,
                status="Success"
            )
        else:
            logger.warning(f"No results returned for {file_name}")
            return DocumentResult(
                fileName=file_name,
                status="Failed",
                error="No classification results returned by the model."
            )
    except Exception as e:
        logger.error(f"Failed to classify {file_name}: {str(e)}")
        return DocumentResult(
            fileName=file_name,
            status="Failed",
            error=str(e)
        )
    finally:
        await client.close()

async def process_documents_concurrently(file_paths: List[str], base_dir: str, concurrency_limit: int = 5) -> ClassificationResponse:
    """
    Processes a list of files concurrently using a semaphore to limit parallel requests.
    """
    classifier_id = os.environ.get("CLASSIFIER_ID")
    if not classifier_id:
        raise ValueError("Missing CLASSIFIER_ID environment variable.")

    client = get_document_client()
    semaphore = asyncio.Semaphore(concurrency_limit)

    async def bounded_classify(file_path: str) -> DocumentResult:
        async with semaphore:
            return await classify_document_async(client, classifier_id, file_path, base_dir)

    try:
        # Create tasks for all files
        tasks = [bounded_classify(fp) for fp in file_paths]
        # Run them concurrently
        results: List[DocumentResult] = await asyncio.gather(*tasks)

        # Aggregate results
        successful = sum(1 for r in results if r.status == "Success")
        failed = len(results) - successful

        summary = Summary(
            totalDocuments=len(results),
            successful=successful,
            failed=failed
        )

        return ClassificationResponse(
            documents=results,
            summary=summary
        )
    finally:
        await client.close()
