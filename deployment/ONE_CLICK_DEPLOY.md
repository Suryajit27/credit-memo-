# One-Command Deployment (IaC)

This repo now includes an Infrastructure as Code setup (Bicep) and a single orchestrator script that deploys:

- Azure Functions backend
- Proxy API (Express) on Azure Container Apps
- React UI on Azure Static Web Apps
- Storage, Cosmos DB, Azure AI Search, ACR, monitoring, and Document Intelligence
- Foundry agents (memo + chat) from repo-managed JSON definitions

## 1) Prerequisites

- Azure CLI (`az`)
- Azure Functions Core Tools (`func`)
- Python 3.11+
- Node 20+
- pnpm
- An Azure subscription with permission to create resource groups/resources
- A Foundry project endpoint and deployed model

Login first:

```powershell
az login
```

## 2) Required inputs

Copy `.env.deploy.template` and fill values (or pass directly as script args):

- `SUBSCRIPTION_ID`
- `CLASSIFIER_ID`
- `OPENAI_ENDPOINT`
- `OPENAI_API_KEY`
- `OPENAI_DEPLOYMENT_NAME`
- `OPENAI_EMBEDDING_DEPLOYMENT`
- `FOUNDRY_PROJECT_ENDPOINT`
- `FOUNDRY_API_KEY` (can be same as OpenAI key)

## 3) Run deployment

From repo root:

```powershell
./scripts/deploy-full.ps1 `
  -SubscriptionId "<subscription-id>" `
  -EnvironmentName "dev" `
  -Location "eastus" `
  -StaticWebAppLocation "eastus2" `
  -ClassifierId "<classifier-id>" `
  -OpenAIEndpoint "https://<resource>.openai.azure.com/" `
  -OpenAIApiKey "<openai-key>" `
  -OpenAIDeploymentName "gpt-5.4-mini" `
  -OpenAIEmbeddingDeployment "text-embedding-3-small" `
  -FoundryProjectEndpoint "https://<resource>.services.ai.azure.com/api/projects/<project-name>" `
  -FoundryApiKey "<foundry-key>"
```

The script is idempotent:

- Re-running updates existing resources and redeploys app code.
- Search artifacts are applied as create/update (PUT), not delete/recreate.
- Foundry agents are create-or-update by stable names from `config/foundry-agents/*.json`.

## 4) What the script does

1. Validates prerequisites.
2. Deploys/updates `infra/main.bicep` in the target resource group.
3. Configures Function App settings (Cosmos/Search/Blob/OpenAI/Foundry/Classifier).
4. Publishes Function code.
5. Creates/updates Search data source/index/skillset/indexer from `infra/main.bicep` using a deployment script resource.
6. Builds proxy image in ACR and updates Container App revision.
7. Builds UI and deploys Static Web App with deployment token.
8. Bootstraps Foundry agents and writes IDs back to Function App settings.
9. Runs smoke tests (`/api/healthz`, memo/indexer status endpoints).

## 5) Agent definitions in repo

Foundry agent specs are versioned here:

- `config/foundry-agents/memo-agent.json`
- `config/foundry-agents/chat-agent.json`

Bootstrap script:

- `scripts/bootstrap_foundry_agents.py`

If you edit tool schemas or instructions in these JSON files, rerun `deploy-full.ps1` (or run the bootstrap script directly) to apply changes.

## 6) Optional switches

- `-SkipFoundryBootstrap` to skip agent create/update for a run.
- `-SkipSmokeTest` to skip post-deploy endpoint validation.
- `-ReuseExistingResources` to skip infra creation and deploy into existing Azure resources (useful when your subscription has zero App Service quota in the target region).
- `-DocumentIntelligenceResourceGroup` when your classifier lives in a different resource group than the rest of the stack.

When `-ReuseExistingResources` is set, provide:

- `-ExistingFunctionAppName`
- `-ExistingProxyContainerAppName`
- `-ExistingStaticWebAppName`
- `-ExistingContainerRegistryName`
- `-ExistingSearchServiceName`
- `-ExistingCosmosAccountName`
- `-ExistingStorageAccountName`
- `-ExistingDocumentIntelligenceName`

## 7) Notes

- Azure AI Search `free` SKU can fail in subscriptions that already have a free Search service; `infra/main.bicep` defaults to `basic` to keep deployment reliable.
- Document Intelligence `F0` can also be quota-limited; deployment defaults to `S0`.
- Custom domain setup remains separate from this script.
