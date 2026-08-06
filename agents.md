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
  - `src/pages/memo.tsx`: memo drafting/review screen, live agent stream, and section regeneration stream.
  - `src/components/workspace-shell.tsx`: layout and sidebar status widgets.
  - `src/components/request-chat-widget.tsx`: request-scoped streaming chat tied to indexed evidence.
  - `src/lib/request-id-context.tsx`: request ID sharing across UI areas.
- `actual_ui/Code-Generation-UI/artifacts/api-server/`
  - Express proxy that forwards UI requests to Azure Functions.
  - Handles `/api/*` routes, including streaming memo drafting/regeneration/chat.
  - Must forward `x-functions-key` on backend calls to avoid auth failures and empty-body JSON parse errors.
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
8. Section regeneration uses `/api/memo/section/regenerate/stream` and emits token deltas in real time.

### Request Chat
1. User opens request chat in the memo screen.
2. UI calls `/api/chat/stream` with `requestId`, prompt, and history.
3. Azure Functions performs request-scoped retrieval and answer synthesis.
4. SSE tokens and tool activity are streamed back to the chat widget.

### Finalization
1. All sections must be approved.
2. Final memo is written to Blob Storage.
3. Finalized memo state is stored in Cosmos DB.
4. The UI shows the published blob path.

## Important Points
- `host.json` must keep streaming enabled for SSE to work.
- The Express proxy uses manual stream piping for memo drafting, section regeneration, and chat.
- The agent uses Azure OpenAI directly through Agent Framework.
- `AZURE_OPENAI_ENDPOINT` and `AZURE_OPENAI_DEPLOYMENT_NAME` must be set.
- The agent currently defaults to `gpt-5.4-mini`.
- Hybrid search uses BM25 plus vector queries via Azure AI Search.
- Finalized memos are stored in a separate blob container from source documents.
- Source documents and memo documents should not share the same index target.
- Large memo documents are chunked before upload to avoid embedding token limits.
- `requestId` is the key join field across Cosmos, Blob, Search, and UI state.
- Memo page auto-loads the shared `requestId` after index status reaches `succeeded`.
- Streaming UI now renders markdown directly in the live terminal log.
- Citation badges are styled as small boxy markers and should remain tightly attached to the text.
- Current UX language intentionally emphasizes "credit memo narratives" across intake, drafting, and chat.
- Architecture diagram source is stored at `architecture/credit-memo-architecture.drawio`.

## Environment Portability
- Primary deployment path is now infrastructure-as-code plus one orchestrator script: `infra/main.bicep` + `scripts/deploy-full.ps1`.
- Foundry agent definitions are stored in repo (`config/foundry-agents/*.json`) and bootstrapped automatically via `scripts/bootstrap_foundry_agents.py`.
- Deploy flow is parameterized, so moving to a new environment is mostly updating subscription/resource/env values and rerunning the same command.
- Deploy flow supports both creating new resources and reusing existing resources (`-ReuseExistingResources`) when quota blocks new provisioning.
- Search artifacts are applied as create/update operations in deployment flow (no implicit delete/recreate path).
- `CLASSIFIER_ID` must exist in the configured Document Intelligence account/endpoint for that environment.

## Practical Notes For Future Changes
- Do not put secrets into this file.
- If the agent stream breaks, check the Azure OpenAI env values first.
- If UI API calls fail with `Unexpected end of JSON input`, verify proxy forwarding of `x-functions-key`.
- If search skillset provisioning fails with `Provided key is not a valid CognitiveServices type key`, use an Azure AI Services multi-service key in the same region as the Search service.
- If index status stays stale, check Cosmos request telemetry and AI Search indexer state.
- If a memo record is missing, the UI should see a `not_started` or empty response, not a hard failure.
- Keep the memo status and indexer status endpoints separate.
- Prefer the smallest fix that preserves the current UI flow.
