import os
from azure.core.credentials import AzureKeyCredential
from azure.ai.documentintelligence.aio import DocumentIntelligenceClient
from utils.logging import logger

def get_document_client() -> DocumentIntelligenceClient:
    """
    Initializes and returns an asynchronous Azure AI Document Intelligence client.
    Expects DOCUMENT_INTELLIGENCE_ENDPOINT and DOCUMENT_INTELLIGENCE_KEY to be set in environment variables.
    """
    endpoint = os.environ.get("DOCUMENT_INTELLIGENCE_ENDPOINT")
    key = os.environ.get("DOCUMENT_INTELLIGENCE_KEY")
    
    if not endpoint or not key:
        raise ValueError("Missing DOCUMENT_INTELLIGENCE_ENDPOINT or DOCUMENT_INTELLIGENCE_KEY environment variable.")

    logger.info(f"Initializing DocumentIntelligenceClient with endpoint: {endpoint}")
    # Explicitly set active api_version
    client = DocumentIntelligenceClient(
        endpoint=endpoint, 
        credential=AzureKeyCredential(key),
        api_version="2024-11-30"
    )
    return client
