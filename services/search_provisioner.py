import os
import requests
from utils.logging import logger

def _get_headers():
    admin_key = os.environ.get("AZURE_SEARCH_ADMIN_KEY")
    return {
        "Content-Type": "application/json",
        "api-key": admin_key
    }

def provision_search_resources():
    """
    Provisions Data Source, Index, Skillset, and Indexer in Azure AI Search.
    Uses Azure REST API v2024-07-01.
    """
    # Load from local.settings.json if environment variables are missing
    if not os.environ.get("AZURE_SEARCH_ENDPOINT") and os.path.exists("local.settings.json"):
        try:
            import json
            with open("local.settings.json", "r") as f:
                settings = json.load(f)
                values = settings.get("Values", {})
                for k, v in values.items():
                    if k not in os.environ:
                        os.environ[k] = v
        except Exception:
            pass

    search_endpoint = os.environ.get("AZURE_SEARCH_ENDPOINT")
    admin_key = os.environ.get("AZURE_SEARCH_ADMIN_KEY")
    conn_string = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")

    if not search_endpoint or not admin_key or not conn_string:
        logger.warning("Missing Azure Search or Storage configuration. Skipping AI Search provisioning.")
        return False

    api_version = "2024-07-01"
    headers = _get_headers()

    datasource_name = os.environ.get("AZURE_SEARCH_DATASOURCE_NAME", "loan-documents-datasource")
    index_name = os.environ.get("AZURE_SEARCH_INDEX_NAME", "loan-documents-index")
    skillset_name = os.environ.get("AZURE_SEARCH_SKILLSET_NAME", "loan-documents-skillset")
    indexer_name = os.environ.get("AZURE_SEARCH_INDEXER_NAME", "loan-documents-indexer")
    blob_container = os.environ.get("BLOB_CONTAINER_NAME", "loan-documents")

    openai_endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
    openai_key = os.environ.get("AZURE_OPENAI_KEY")
    embedding_deployment = os.environ.get("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "text-embedding-3-small")
    cognitive_services_key = os.environ.get("AZURE_COGNITIVE_SERVICES_KEY")

    try:
        # 0. Delete existing resources to allow clean recreation (field type changes require this)
        for resource_type, resource_name in [
            ("indexers", indexer_name),
            ("skillsets", skillset_name),
            ("indexes", index_name),
        ]:
            del_r = requests.delete(f"{search_endpoint}/{resource_type}/{resource_name}?api-version={api_version}", headers=headers)
            if del_r.status_code == 204:
                logger.info(f"Deleted existing {resource_type[:-1]} '{resource_name}'.")
            elif del_r.status_code == 404:
                logger.info(f"{resource_type[:-1].capitalize()} '{resource_name}' not found, skipping delete.")
            else:
                logger.warning(f"Unexpected status deleting {resource_type[:-1]} '{resource_name}': {del_r.status_code}")

        # 1. Create/Update Datasource
        ds_payload = {
            "name": datasource_name,
            "type": "azureblob",
            "credentials": {"connectionString": conn_string},
            "container": {"name": blob_container}
        }
        r = requests.put(f"{search_endpoint}/datasources/{datasource_name}?api-version={api_version}", json=ds_payload, headers=headers)
        r.raise_for_status()
        logger.info(f"Data source '{datasource_name}' created/updated.")

        # 2. Create Index
        index_payload = {
            "name": index_name,
            "fields": [
                {"name": "id", "type": "Edm.String", "key": True, "searchable": True, "filterable": True, "analyzer": "keyword"},
                {"name": "parentId", "type": "Edm.String", "searchable": False, "filterable": True},
                {"name": "content", "type": "Edm.String", "searchable": True, "filterable": False},
                {"name": "contentVector", "type": "Collection(Edm.Single)", "searchable": True, "dimensions": 1536, "vectorSearchProfile": "myHnswProfile"},
                {"name": "requestId", "type": "Edm.String", "searchable": False, "filterable": True, "facetable": True},
                {"name": "documentType", "type": "Edm.String", "searchable": True, "filterable": True, "facetable": True},
                {"name": "confidence", "type": "Edm.Double", "searchable": False, "filterable": True},
                {"name": "blobName", "type": "Edm.String", "searchable": True, "filterable": True},
                {"name": "uploadedAt", "type": "Edm.String", "searchable": False, "filterable": True, "sortable": True},
                {"name": "chunkIndex", "type": "Edm.Int32", "searchable": False, "filterable": True, "sortable": True},
                {"name": "totalChunks", "type": "Edm.Int32", "searchable": False, "filterable": True}
            ],
            "vectorSearch": {
                "algorithms": [{"name": "myHnsw", "kind": "hnsw"}],
                "profiles": [{"name": "myHnswProfile", "algorithm": "myHnsw"}]
            }
        }
        r = requests.put(f"{search_endpoint}/indexes/{index_name}?api-version={api_version}", json=index_payload, headers=headers)
        if not r.ok:
            logger.error(f"Index creation failed ({r.status_code}): {r.text}")
        r.raise_for_status()
        logger.info(f"Index '{index_name}' created.")

        # 3. Create Skillset with OCR → Merge → Embed pipeline
        if openai_endpoint and openai_key:
            skills = [
                # OCR: extract text from images (PNG, JPEG, TIFF, scanned PDFs)
                {
                    "@odata.type": "#Microsoft.Skills.Vision.OcrSkill",
                    "name": "ocr-skill",
                    "context": "/document/normalized_images/*",
                    "defaultLanguageCode": "en",
                    "detectOrientation": True,
                    "inputs": [{"name": "image", "source": "/document/normalized_images/*"}],
                    "outputs": [{"name": "text", "targetName": "text"}]
                },
                # Merge: combine native text content + OCR text into one field
                # Uses expression evaluator for itemsToInsert so that docs without images
                # get null (treated as empty) instead of a fatal missing-path error
                {
                    "@odata.type": "#Microsoft.Skills.Text.MergeSkill",
                    "name": "merge-skill",
                    "context": "/document",
                    "insertPreTag": " ",
                    "insertPostTag": " ",
                    "inputs": [
                        {"name": "text", "source": "/document/content"},
                        {"name": "itemsToInsert", "source": "= $(/document/normalized_images/*/text)"}
                    ],
                    "outputs": [{"name": "mergedText", "targetName": "mergedContent"}]
                },
                # Split merged text before embedding so long reports never exceed model limits.
                {
                    "@odata.type": "#Microsoft.Skills.Text.SplitSkill",
                    "name": "split-skill",
                    "context": "/document",
                    "textSplitMode": "pages",
                    "maximumPageLength": 6000,
                    "pageOverlapLength": 300,
                    "defaultLanguageCode": "en",
                    "inputs": [{"name": "text", "source": "/document/mergedContent"}],
                    "outputs": [{"name": "textItems", "targetName": "pages"}],
                },
                # Embed each bounded text chunk instead of the full document.
                {
                    "@odata.type": "#Microsoft.Skills.Text.AzureOpenAIEmbeddingSkill",
                    "name": "embed-skill",
                    "context": "/document/pages/*",
                    "resourceUri": openai_endpoint,
                    "apiKey": openai_key,
                    "deploymentId": embedding_deployment,
                    "modelName": embedding_deployment,
                    "inputs": [{"name": "text", "source": "/document/pages/*"}],
                    "outputs": [{"name": "embedding", "targetName": "contentVector"}]
                }
            ]
            skillset_payload = {
                "name": skillset_name,
                "description": "OCR → Merge → Embed skillset for loan documents (images + PDFs)",
                "skills": skills,
                "indexProjections": {
                    "selectors": [{
                        "targetIndexName": index_name,
                        "parentKeyFieldName": "parentId",
                        "sourceContext": "/document/pages/*",
                        "mappings": [
                            {"name": "content", "source": "/document/pages/*"},
                            {"name": "contentVector", "source": "/document/pages/*/contentVector"},
                            {"name": "requestId", "source": "/document/requestid"},
                            {"name": "documentType", "source": "/document/documenttype"},
                            {"name": "blobName", "source": "/document/metadata_storage_path"},
                            {"name": "uploadedAt", "source": "/document/uploadtimestamp"},
                        ],
                    }],
                    "parameters": {"projectionMode": "skipIndexingParentDocuments"},
                },
                # Attach Azure AI Services key to remove the 20-document free enrichment cap
                "cognitiveServices": {
                    "@odata.type": "#Microsoft.Azure.Search.CognitiveServicesByKey",
                    "key": cognitive_services_key
                } if cognitive_services_key else {"@odata.type": "#Microsoft.Azure.Search.DefaultCognitiveServices"}
            }
            r = requests.put(f"{search_endpoint}/skillsets/{skillset_name}?api-version={api_version}", json=skillset_payload, headers=headers)
            if not r.ok:
                logger.error(f"Skillset creation failed ({r.status_code}): {r.text}")
            r.raise_for_status()
            logger.info(f"Skillset '{skillset_name}' created/updated.")

        # 4. Create Indexer with image action enabled
        indexer_payload = {
            "name": indexer_name,
            "dataSourceName": datasource_name,
            "targetIndexName": index_name,
            "skillsetName": skillset_name if (openai_endpoint and openai_key) else None,
            # Enable image normalization so OCR skill can process images
            "parameters": {
                "configuration": {
                    "dataToExtract": "contentAndMetadata",
                    "imageAction": "generateNormalizedImages",
                    "parsingMode": "default"
                }
            },
            # Map standard blob metadata → index fields
            # NOTE: Azure Blob Storage lowercases ALL metadata key names,
            # so requestId → requestid, documentType → documenttype
            "fieldMappings": [
                {"sourceFieldName": "metadata_storage_path", "targetFieldName": "blobName"},
                {"sourceFieldName": "requestid",    "targetFieldName": "requestId"},
                {"sourceFieldName": "documenttype", "targetFieldName": "documentType"},
                {"sourceFieldName": "uploadTimestamp", "targetFieldName": "uploadedAt"},
                {"sourceFieldName": "chunkindex",    "targetFieldName": "chunkIndex"},
                {"sourceFieldName": "totalchunks",   "targetFieldName": "totalChunks"}
            ],
            "outputFieldMappings": []
        }
        r = requests.put(f"{search_endpoint}/indexers/{indexer_name}?api-version={api_version}", json=indexer_payload, headers=headers)
        if not r.ok:
            logger.error(f"Indexer creation failed ({r.status_code}): {r.text}")
        r.raise_for_status()
        logger.info(f"Indexer '{indexer_name}' created/updated.")

        return True
    except Exception as e:
        logger.error(f"Failed to provision Azure AI Search resources: {str(e)}", exc_info=True)
        return False
