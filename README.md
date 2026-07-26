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

## Azure Deployment

Use the Azure CLI or VS Code Azure Functions extension to deploy the backend application:

```bash
func azure functionapp publish <FunctionAppName>
```

Remember to set the application settings (`DOCUMENT_INTELLIGENCE_ENDPOINT`, `DOCUMENT_INTELLIGENCE_KEY`, `CLASSIFIER_ID`) in your deployed Azure Function App.

For the full POC deployment (Functions + proxy + React UI + Azure dependencies), use:

- `deployment/AZURE_POC_DEPLOYMENT.md`
- `deployment/azure-poc-deploy.ps1`

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
