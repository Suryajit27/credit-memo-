# Project Notes

## Overview
This project is a loan document classifier and credit memo generator.
It combines Azure Functions, Azure AI Search, Azure Cosmos DB, Azure Blob Storage, Azure OpenAI, Azure SQL Database, and a React UI.

The main flow is:
1. Upload and classify loan documents.
2. Index document content into Azure AI Search.
3. Use an agent to draft a credit memo with retrieved evidence.
4. Review, approve, regenerate, and finalize memo sections in the UI.
5. Combine request-scoped document retrieval with SQL operational context when answering or drafting.
6. Let portfolio users run constrained, read-only operational reporting queries across approved Azure SQL views.

## Main Structure

### Backend
- `function_app.py`
  - Azure Functions entry point.
  - Hosts upload, search, memo, stream, finalize, indexer-status, and portfolio-reporting endpoints.
  - Streams SSE responses from the drafting agent.
- `services/`
  - `credit_memo_agent.py`: analysis phase, drafting, regeneration, and SSE stream flow.
  - `agent_provider.py`: creates the Azure OpenAI agent via Microsoft Agent Framework.
  - `search_indexer.py`: indexer trigger/status logic and hybrid search.
  - `sql_loan_context.py`: managed-identity Azure SQL access and the request-scoped loan-context function handler.
  - `admin_reporting_agent.py`: portfolio-wide reporting chat and SSE stream flow.
  - `reporting_schema.py`: loads and exposes the approved reporting view and column catalog.
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
  - `src/pages/portfolio-reporting.tsx`: portfolio reporting page using the shared streaming chat widget.
  - `src/components/workspace-shell.tsx`: layout and sidebar status widgets.
  - `src/components/request-chat-widget.tsx`: shared request-scoped and portfolio-reporting streaming chat UI.
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
9. Foundry can use persistent document-search and SQL loan-context tool schemas; the Function App executes their request-scoped handlers.

### Request Chat
1. User opens request chat in the memo screen.
2. UI calls `/api/chat/stream` with `requestId`, prompt, and history.
3. Azure Functions performs request-scoped retrieval and answer synthesis.
4. SSE tokens and tool activity are streamed back to the chat widget.

### Portfolio Reporting
1. User opens the Portfolio reporting workspace tab at `/portfolio-reporting`.
2. The shared chat widget calls `/api/portfolio-reporting/stream` without a `requestId`.
3. The Express proxy forwards the stream to the Function endpoint `/api/portfolio-reporting/stream`.
4. `admin_reporting_agent.py` embeds `config/reporting-schema.json` in the agent request and registers `get_reporting_schema` plus `run_admin_report`.
5. The agent writes one constrained `SELECT` query against the approved reporting views.
6. The backend streams the generated SQL as an SSE `tool_call` event before the answer tokens, so the UI thinking trace shows the executed query.
7. The SQL result is summarized with `[910]` citations.

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
- Do not use an Azure Functions route beginning with `/admin/`; the Functions host reserves that path. Use `/portfolio-reporting/stream` for the portfolio reporting backend endpoint.
- `config/reporting-schema.json` is the agent's versioned SQL contract. Keep its approved views and exact columns aligned with `database/migrations/002_poc_admin_reporting.sql` and later reporting migrations.
- `run_admin_report` allows only a single read-only `SELECT` over approved `reporting.*` views, with a 10-second connection timeout and a 100-row result limit.
- Generic `status` is a domain-specific alias in the reporting views. Prefer `loan_stage`, `monitoring_status`, `condition_status`, or `control_status` when the question calls for that specific meaning.

## Environment Portability
- Primary deployment path is now infrastructure-as-code plus one orchestrator script: `infra/main.bicep` + `scripts/deploy-full.ps1`.
- SQL-only provisioning, schema migration, seed loading, and Function App SQL configuration are handled by `scripts/deploy-sql.ps1`.
- SQL migrations are in `database/migrations`; versioned seed definitions are in `database/seeds/seed-manifest.json`.
- Foundry agent definitions are stored in repo (`config/foundry-agents/*.json`) and bootstrapped automatically via `scripts/bootstrap_foundry_agents.py`, including `admin-reporting-agent.json`.
- Portfolio reporting uses migrations `002_poc_admin_reporting.sql` and `003_poc_admin_reporting_status_aliases.sql` to create the approved reporting views and generic status aliases.
- `search_documents` / `search_request_documents` and `get_loan_context` are persistent Foundry function schemas. Keep the matching request-scoped Python handlers registered in `credit_memo_agent.py` and `document_chat_agent.py`; do not re-inject these schemas in the runtime Responses API payload.
- Deploy flow is parameterized, so moving to a new environment is mostly updating subscription/resource/env values and rerunning the same command.
- Deploy flow supports both creating new resources and reusing existing resources (`-ReuseExistingResources`) when quota blocks new provisioning.
- `scripts/deploy-full.ps1` can reuse all existing app resources while provisioning SQL for the first time. Supply `ExistingSqlServerName` and `ExistingSqlDatabaseName` together only after SQL already exists.
- Search artifacts are applied as create/update operations in deployment flow (no implicit delete/recreate path).
- Deployment requires Azure CLI, Azure Functions Core Tools, Python, Node.js, pnpm, npx, and `sqlcmd`. Install Microsoft Sqlcmd Tools and ensure its installation directory is on `PATH` before running the scripts.
- The SQL deployment creates a temporary SQL administrator only during logical-server creation, then enables Microsoft Entra-only authentication. The temporary password is generated in memory and is not stored in repo configuration.
- Azure SQL server creation can be blocked in a region even when other resources can be created there. Check the desired serverless SKU availability first. If needed, run `deploy-sql.ps1` in a supported region, then rerun the full deployment with that server/database supplied as existing resources.
- `CLASSIFIER_ID` must exist in the configured Document Intelligence account/endpoint for that environment. Custom classifiers are account-specific and are not recreated by Bicep. When reusing a classifier from another resource group, pass both `DocumentIntelligenceResourceGroup` and `ExistingDocumentIntelligenceName` to the deployment script.
- For the POC, use request ID `sample-meridian-foods-2026` when uploading the Meridian source package; it joins the seeded SQL case to Blob, Cosmos, Search, memo, and chat data.

## Practical Notes For Future Changes
- Do not put secrets into this file.
- If the agent stream breaks, check the Azure OpenAI env values first.
- If UI API calls fail with `Unexpected end of JSON input`, verify proxy forwarding of `x-functions-key`.
- If search skillset provisioning fails with `Provided key is not a valid CognitiveServices type key`, use an Azure AI Services multi-service key in the same region as the Search service.
- If index status stays stale, check Cosmos request telemetry and AI Search indexer state.
- If a memo record is missing, the UI should see a `not_started` or empty response, not a hard failure.
- Keep the memo status and indexer status endpoints separate.
- Prefer the smallest fix that preserves the current UI flow.
- The Function App must have a system-assigned managed identity before SQL deployment. `deploy-full.ps1` assigns it idempotently.
- Azure SQL service-principal users must be created with the Function App managed identity **client/application ID**, not its object/principal ID. `deploy-sql.ps1` resolves the client ID through Microsoft Entra and corrects an existing mismatched user mapping.
- If SQL reports `18456` for `<token-identified principal>` after a correct identity mapping, run `DBCC FLUSHAUTHCACHE` as the Entra SQL administrator and restart the Function App before retrying.
- `SQL_SERVER`, `SQL_DATABASE`, `SQL_ODBC_DRIVER`, and `SQL_AUTHENTICATION=managed_identity` must be set on the Function App. The SQL context implementation requires the Microsoft ODBC Driver for SQL Server on the Function host and `pyodbc` in `requirements.txt`.
- If portfolio reporting returns an invalid column error, update the reporting schema catalog and the reporting views together, then redeploy the Function App and bootstrap the Foundry reporting agent.
- The Container App proxy may intentionally return `401` to direct unauthenticated health checks when Azure Static Web Apps authentication is enabled. Treat that as an expected auth gate; the Static Web App root should still return `200`.
