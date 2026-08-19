# Loan Document Classification API

This is a production-ready HTTP API built with Azure Functions (Python v2) that accepts a ZIP file containing loan documents and classifies them using Azure AI Document Intelligence.

## Local Setup

1. **Prerequisites**:
   - Python 3.11
   - Azure Functions Core Tools
   - An Azure AI Document Intelligence resource with a trained Custom Classification Model.

2. **Install Dependencies**:
   ```bash
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   ```

3. **Configuration**:
   Copy `local.settings.example.json` to `local.settings.json` and fill all required values.

   Required groups:
   - Document Intelligence (`DOCUMENT_INTELLIGENCE_ENDPOINT`, `DOCUMENT_INTELLIGENCE_KEY`, `CLASSIFIER_ID`)
   - Storage/Cosmos/Search (`AZURE_STORAGE_CONNECTION_STRING`, `COSMOS_*`, `AZURE_SEARCH_*`)
   - OpenAI + Foundry (`AZURE_OPENAI_*`, `FOUNDRY_*`)

   Do not commit `local.settings.json`.

4. **Run Backend Locally**:
   ```bash
   func start
   ```

## API Usage

**Endpoint**: `POST /api/classify`

**Content-Type**: `multipart/form-data`

The request must contain a ZIP file field (any name is fine, e.g., `file=@loan_documents.zip`).

### Example Request

```bash
curl -X POST http://localhost:7071/api/classify \
  -F "file=@sample_documents.zip"
```

### Example Response

```json
{
  "documents": [
    {
      "fileName": "Applicant/DriverLicense.pdf",
      "documentType": "DriverLicense",
      "confidence": 0.998
    }
  ],
  "summary": {
    "totalDocuments": 1,
    "successful": 1,
    "failed": 0
  }
}
```

## One-Command Azure Deployment (IaC)

This repo includes a full Infrastructure as Code path that provisions and deploys the complete system in one run:

- Bicep template: `infra/main.bicep`
- Orchestrator script: `scripts/deploy-full.ps1`
- Azure SQL deployment and migration script: `scripts/deploy-sql.ps1`
- Foundry agent configs: `config/foundry-agents/memo-agent.json`, `config/foundry-agents/chat-agent.json`, `config/foundry-agents/admin-reporting-agent.json`

Quickstart:

```powershell
./scripts/deploy-full.ps1 `
  -SubscriptionId "<subscription-id>" `
  -ClassifierId "<classifier-id>" `
  -OpenAIEndpoint "https://<resource>.openai.azure.com/" `
  -OpenAIApiKey "<openai-key>" `
  -FoundryProjectEndpoint "https://<resource>.services.ai.azure.com/api/projects/<project-name>" `
  -FoundryApiKey "<foundry-key>"
```

Full guide:

- `deployment/ONE_CLICK_DEPLOY.md`

### Azure SQL Only

`deploy-sql.ps1` provisions the POC Azure SQL logical server/database, applies versioned migrations from `database/migrations`, and applies configured seed loaders from `database/seeds/seed-manifest.json`. It uses Microsoft Entra authentication and requires the `sqlcmd` command-line tool.

```powershell
./scripts/deploy-sql.ps1 `
  -SubscriptionId "<subscription-id>" `
  -FunctionAppName "<existing-function-app-name>"
```

When deploying through a service principal rather than an interactive Entra user, also supply `-SqlEntraAdministratorObjectId` and `-SqlEntraAdministratorLogin`.

The seeded Meridian demonstration case is linked to request ID `sample-meridian-foods-2026`. Use that request ID when uploading the matching RAG documents to demonstrate combined document and SQL context.

## Portfolio Reporting Agent (POC)

`POST /api/portfolio-reporting/stream` streams portfolio-wide reporting answers. Its `portfolio-reporting-agent` uses the `run_admin_report` tool, which accepts a single read-only `SELECT` statement over these curated Azure SQL views:

- `reporting.vw_loan_cases`
- `reporting.vw_relationship_profiles`
- `reporting.vw_loan_monitoring`
- `reporting.vw_credit_conditions`
- `reporting.vw_collateral_controls`

Example request through the proxy:

```bash
curl -N -X POST http://localhost:3000/api/portfolio-reporting/stream \
  -H "Content-Type: application/json" \
  -d "{\"message\":\"How many cases are in each loan stage?\"}"
```

This is intentionally a POC implementation. The endpoint has no application-level admin role check, and the Function App managed identity is shared by all backend agent flows. The tool restricts statements to `SELECT`, approved reporting views, a 10-second timeout, and at most 100 returned rows, but its regular-expression validation is not a production-grade SQL authorization boundary. Before production use, add authenticated role enforcement, a separate runtime identity and database role, a SQL AST validator, row-level/tenant controls, query auditing, result-size controls enforced in SQL, and data classification or masking for sensitive fields.

## Git And Reproducibility Checklist

Before pushing:

1. Ensure secrets are not tracked:
   - `local.settings.json` must stay local
   - use `local.settings.example.json` for template values
2. Confirm `.gitignore` includes local/secrets and virtual environments.
3. Validate from a clean clone:
   - install backend deps (`pip install -r requirements.txt`)
   - install UI/proxy deps (`pnpm install` in `actual_ui/Code-Generation-UI`)
   - start backend (`func start`)
   - run proxy/UI builds

Example push commands:

```bash
git init
git add .
git commit -m "Add reproducible Azure POC setup and templates"
git remote add origin <your-repo-url>
git push -u origin main
```

The active UI for this project is the React app under `actual_ui/Code-Generation-UI/artifacts/doculender-ai` and its proxy under `actual_ui/Code-Generation-UI/artifacts/api-server`.
