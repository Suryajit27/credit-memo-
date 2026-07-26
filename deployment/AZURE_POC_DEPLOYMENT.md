# Azure POC Deployment (East US)

This document matches the current app architecture:
- Azure Functions backend (`function_app.py`)
- Express proxy (`actual_ui/Code-Generation-UI/artifacts/api-server`)
- React frontend (`actual_ui/Code-Generation-UI/artifacts/doculender-ai`)

## 1) Prerequisites

- Azure CLI
- Azure Functions Core Tools
- Node 20+
- pnpm
- Python 3.11

Login:

```powershell
az login
```

## 2) Provision Azure resources

Run the provisioning script from repo root:

```powershell
.\deployment\azure-poc-deploy.ps1 `
  -SubscriptionId "<subscription-id>" `
  -Suffix "<unique4to6>" `
  -OpenAIDeploymentName "<chat-deployment-name>" `
  -OpenAIEmbeddingDeployment "<embedding-deployment-name>" `
  -ClassifierId "<document-intelligence-classifier-id>"
```

Notes:
- Region defaults to `eastus`.
- Static Web App defaults to `eastus2`.
- Script attempts free tiers first and falls back when unavailable.

## 3) Deploy the Azure Functions backend

From repo root:

```powershell
func azure functionapp publish <function-app-name>
python run_provision.py
```

`run_provision.py` creates/updates Azure AI Search datasource, index, skillset, and indexer.

Important for successful indexing:
- Attach a paid Cognitive Services key to the Search skillset (`cognitiveServices`) to avoid free skillset quota failures.
- Ensure the embedding skill (`AzureOpenAIEmbeddingSkill`) points to the same Azure OpenAI resource used by the environment.
- If these settings drift, run `POST /api/reset-indexer` after fixing them.

## 4) Deploy the proxy app

From `actual_ui/Code-Generation-UI`:

```powershell
pnpm install
pnpm --filter @workspace/api-server run build
```

Zip the folder `actual_ui/Code-Generation-UI/artifacts/api-server` and deploy:

```powershell
az webapp deploy --resource-group <rg> --name <proxy-webapp-name> --src-path <zip-path> --type zip
```

If App Service quota is unavailable, deploy the same proxy image to Container Apps and use that URL as your UI backend.

The proxy requires:
- `AZURE_FUNC_API_URL`
- `AZURE_FUNC_API_KEY`

The `AZURE_FUNC_API_KEY` value should be a Functions host/function key. The proxy forwards it as `x-functions-key`.

## 5) Deploy frontend (Static Web Apps)

From `actual_ui/Code-Generation-UI`:

```powershell
$env:PORT="5173"
$env:BASE_PATH="/"
pnpm --filter @workspace/doculender-ai run build
```

Deploy `actual_ui/Code-Generation-UI/artifacts/doculender-ai/dist/public` to Static Web Apps.

## 6) Validate

- `GET https://<proxy-host>/api/healthz`
- Upload/classify flow
- Indexer status updates
- `/api/memo/stream` emits incremental SSE tokens
- `/api/chat/stream` emits incremental SSE tokens
- Memo finalize writes to memo container

Indexer-status validation:
- Confirm `/api/indexer-status?requestId=<id>` progresses `pending/running -> succeeded`.
- If it remains `running` with zero indexed docs, inspect Search indexer execution history for quota/auth errors.

## 7) Security cleanup

- Rotate any secrets previously committed in `local.settings.json`.
- Move app settings to Key Vault references for non-POC environments.
