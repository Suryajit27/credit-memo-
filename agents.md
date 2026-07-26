# Project Notes

## Overview
This project is a loan document classifier and credit memo generator.
It combines Azure Functions, Azure AI Search, Azure Cosmos DB, Azure Blob Storage, Azure OpenAI, and a React UI.

The main flow is:
1. Upload and classify loan documents.
2. Index document content into Azure AI Search.
3. Use an agent to draft a credit memo with retrieved evidence.
4. Review, approve, regenerate, and finalize memo sections in the UI.

## Main Structure

### Backend
- `function_app.py`
  - Azure Functions entry point.
  - Hosts upload, search, memo, stream, finalize, and indexer-status endpoints.
  - Streams SSE responses from the drafting agent.
- `services/`
  - `credit_memo_agent.py`: analysis phase, drafting, regeneration, and SSE stream flow.
  - `agent_provider.py`: creates the Azure OpenAI agent via Microsoft Agent Framework.
  - `search_indexer.py`: indexer trigger/status logic and hybrid search.
  - `search_provisioner.py`: provisions AI Search index, skillset, and indexer.
  - `memo_tracker.py`: stores memo state in Cosmos DB.
  - `cosmos_tracker.py`: stores indexing/request telemetry in Cosmos DB.
  - `blob_uploader.py`: uploads source files and finalized memo blobs.
  - `zip_processor.py`: safely extracts ZIP uploads.
- `models/response_models.py`
  - Pydantic response schemas for the API.
- `utils/logging.py`
  - Shared logger setup.

### Frontend
- `actual_ui/Code-Generation-UI/artifacts/doculender-ai/`
  - Main React app used for the current UI.
  - `src/pages/home.tsx`: document intake, search, and indexer status.
  - `src/pages/memo.tsx`: memo drafting/review screen and live agent stream.
  - `src/components/workspace-shell.tsx`: layout and sidebar status widgets.
  - `src/lib/request-id-context.tsx`: request ID sharing across UI areas.
- `actual_ui/Code-Generation-UI/artifacts/api-server/`
  - Express proxy that forwards UI requests to Azure Functions.
  - Handles `/api/*` routes, including streaming memo drafting.
- `actual_ui/Code-Generation-UI/lib/api-client-react/`
  - Generated React Query client used by the UI.

## Runtime Flow

### Upload and Index
1. User uploads a file or ZIP in the home page.
2. UI sends it through the Express proxy.
3. Azure Functions classifies the file and stores metadata in Cosmos DB.
4. Files are uploaded to Blob Storage.
5. The AI Search indexer is triggered or re-run.
6. Index status is read from the indexer and Cosmos telemetry.

### Memo Drafting
1. User enters or loads a `requestId` in the memo page.
2. UI calls `/api/memo/stream`.
3. Azure Functions starts the analysis phase.
4. The agent selects relevant memo sections.
5. For each section, the agent runs hybrid search for evidence.
6. Draft text is streamed back as SSE events.
7. UI renders the draft and lets the user approve or regenerate sections.

### Finalization
1. All sections must be approved.
2. Final memo is written to Blob Storage.
3. Finalized memo state is stored in Cosmos DB.
4. The UI shows the published blob path.

## Important Points
- `host.json` must keep streaming enabled for SSE to work.
- The Express proxy uses manual stream piping for memo drafting.
- The agent uses Azure OpenAI directly through Agent Framework.
- `AZURE_OPENAI_ENDPOINT` and `AZURE_OPENAI_DEPLOYMENT_NAME` must be set.
- The agent currently defaults to `gpt-5.4-mini`.
- Hybrid search uses BM25 plus vector queries via Azure AI Search.
- Finalized memos are stored in a separate blob container from source documents.
- Source documents and memo documents should not share the same index target.
- Large memo documents are chunked before upload to avoid embedding token limits.
- `requestId` is the key join field across Cosmos, Blob, Search, and UI state.
- Streaming UI now renders markdown directly in the live terminal log.
- Citation badges are styled as small boxy markers and should remain tightly attached to the text.

## Practical Notes For Future Changes
- Do not put secrets into this file.
- If the agent stream breaks, check the Azure OpenAI env values first.
- If index status stays stale, check Cosmos request telemetry and AI Search indexer state.
- If a memo record is missing, the UI should see a `not_started` or empty response, not a hard failure.
- Keep the memo status and indexer status endpoints separate.
- Prefer the smallest fix that preserves the current UI flow.
