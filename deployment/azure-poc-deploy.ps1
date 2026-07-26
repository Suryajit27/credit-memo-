Param(
  [Parameter(Mandatory = $true)] [string] $SubscriptionId,
  [Parameter(Mandatory = $true)] [string] $Suffix,
  [string] $ResourceGroup = "credit-memo-poc-rg",
  [string] $Location = "eastus",
  [string] $StaticWebAppLocation = "eastus2",
  [string] $OpenAIDeploymentName = "",
  [string] $OpenAIEmbeddingDeployment = "",
  [string] $ClassifierId = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($OpenAIDeploymentName) -or [string]::IsNullOrWhiteSpace($OpenAIEmbeddingDeployment) -or [string]::IsNullOrWhiteSpace($ClassifierId)) {
  throw "You must provide OpenAIDeploymentName, OpenAIEmbeddingDeployment, and ClassifierId."
}

$storage = ("cmstor{0}" -f $Suffix).ToLower()
$cosmos = ("cmcosmos-{0}" -f $Suffix).ToLower()
$search = ("cmsearch-{0}" -f $Suffix).ToLower()
$openai = ("cmopenai-{0}" -f $Suffix).ToLower()
$docintel = ("cmdocintel-{0}" -f $Suffix).ToLower()

$functionApp = ("cm-func-{0}" -f $Suffix).ToLower()
$appPlan = ("cm-proxy-plan-{0}" -f $Suffix).ToLower()
$webApp = ("cm-proxy-{0}" -f $Suffix).ToLower()
$staticWebApp = ("cm-ui-{0}" -f $Suffix).ToLower()

$cosmosDb = "docintellidb"
$cosmosDocsContainer = "loan-documents-index"
$cosmosMemoContainer = "credit-memos"
$blobDocsContainer = "loan-documents"
$blobMemoContainer = "loan-credit-memos"

az account set --subscription $SubscriptionId

az group create --name $ResourceGroup --location $Location

az storage account create --name $storage --resource-group $ResourceGroup --location $Location --sku Standard_LRS --kind StorageV2
$storageKey = az storage account keys list --resource-group $ResourceGroup --account-name $storage --query "[0].value" --output tsv
az storage container create --name $blobDocsContainer --account-name $storage --account-key $storageKey
az storage container create --name $blobMemoContainer --account-name $storage --account-key $storageKey

az cosmosdb create --name $cosmos --resource-group $ResourceGroup --locations "regionName=$Location failoverPriority=0 isZoneRedundant=False" --enable-free-tier true
az cosmosdb sql database create --account-name $cosmos --resource-group $ResourceGroup --name $cosmosDb
az cosmosdb sql container create --account-name $cosmos --resource-group $ResourceGroup --database-name $cosmosDb --name $cosmosDocsContainer --partition-key-path "/requestId"
az cosmosdb sql container create --account-name $cosmos --resource-group $ResourceGroup --database-name $cosmosDb --name $cosmosMemoContainer --partition-key-path "/requestId"

az search service create --name $search --resource-group $ResourceGroup --location $Location --sku free

az cognitiveservices account create --name $openai --resource-group $ResourceGroup --location $Location --kind OpenAI --sku S0 --yes
try {
  az cognitiveservices account create --name $docintel --resource-group $ResourceGroup --location $Location --kind FormRecognizer --sku F0 --yes
}
catch {
  az cognitiveservices account create --name $docintel --resource-group $ResourceGroup --location $Location --kind FormRecognizer --sku S0 --yes
}

az functionapp create --name $functionApp --resource-group $ResourceGroup --consumption-plan-location $Location --runtime python --runtime-version 3.11 --functions-version 4 --os-type Linux --storage-account $storage

$cosmosEndpoint = az cosmosdb show --resource-group $ResourceGroup --name $cosmos --query "documentEndpoint" --output tsv
$cosmosKey = az cosmosdb keys list --resource-group $ResourceGroup --name $cosmos --type keys --query "primaryMasterKey" --output tsv
$searchEndpoint = "https://$search.search.windows.net"
$searchKey = az search admin-key show --resource-group $ResourceGroup --service-name $search --query "primaryKey" --output tsv
$docintelEndpoint = az cognitiveservices account show --resource-group $ResourceGroup --name $docintel --query "properties.endpoint" --output tsv
$docintelKey = az cognitiveservices account keys list --resource-group $ResourceGroup --name $docintel --query "key1" --output tsv
$openaiEndpoint = az cognitiveservices account show --resource-group $ResourceGroup --name $openai --query "properties.endpoint" --output tsv
$openaiKey = az cognitiveservices account keys list --resource-group $ResourceGroup --name $openai --query "key1" --output tsv
$storageConnectionString = az storage account show-connection-string --resource-group $ResourceGroup --name $storage --query "connectionString" --output tsv

az functionapp config appsettings set --resource-group $ResourceGroup --name $functionApp --settings `
  "AzureWebJobsStorage=$storageConnectionString" `
  "FUNCTIONS_WORKER_RUNTIME=python" `
  "AZURE_STORAGE_CONNECTION_STRING=$storageConnectionString" `
  "BLOB_CONTAINER_NAME=$blobDocsContainer" `
  "BLOB_MEMO_CONTAINER_NAME=$blobMemoContainer" `
  "COSMOS_DB_ENDPOINT=$cosmosEndpoint" `
  "COSMOS_DB_KEY=$cosmosKey" `
  "COSMOS_DB_DATABASE=$cosmosDb" `
  "COSMOS_DB_CONTAINER=$cosmosDocsContainer" `
  "COSMOS_DB_MEMO_CONTAINER=$cosmosMemoContainer" `
  "AZURE_SEARCH_ENDPOINT=$searchEndpoint" `
  "AZURE_SEARCH_ADMIN_KEY=$searchKey" `
  "AZURE_OPENAI_ENDPOINT=$openaiEndpoint" `
  "AZURE_OPENAI_API_KEY=$openaiKey" `
  "AZURE_OPENAI_DEPLOYMENT_NAME=$OpenAIDeploymentName" `
  "AZURE_OPENAI_EMBEDDING_DEPLOYMENT=$OpenAIEmbeddingDeployment" `
  "DOCUMENT_INTELLIGENCE_ENDPOINT=$docintelEndpoint" `
  "DOCUMENT_INTELLIGENCE_KEY=$docintelKey" `
  "CLASSIFIER_ID=$ClassifierId"

$funcBaseUrl = "https://$functionApp.azurewebsites.net/api"
$funcHostKey = az functionapp keys list --resource-group $ResourceGroup --name $functionApp --query "functionKeys.default" --output tsv

try {
  az appservice plan create --name $appPlan --resource-group $ResourceGroup --sku F1
}
catch {
  az appservice plan create --name $appPlan --resource-group $ResourceGroup --sku B1
}
az webapp create --name $webApp --resource-group $ResourceGroup --plan $appPlan --runtime "NODE|20-lts"
az webapp config appsettings set --resource-group $ResourceGroup --name $webApp --settings `
  "NODE_ENV=production" `
  "LOG_LEVEL=info" `
  "PORT=8080" `
  "AZURE_FUNC_API_URL=$funcBaseUrl" `
  "AZURE_FUNC_API_KEY=$funcHostKey"

az staticwebapp create --name $staticWebApp --resource-group $ResourceGroup --location $StaticWebAppLocation --sku Free

""
"Provisioning complete. Next steps:"
"1) Deploy backend: func azure functionapp publish $functionApp"
"2) Run search setup from repo root: python run_provision.py"
"3) Build and deploy proxy (artifacts/api-server) to $webApp"
"4) Build and deploy UI (artifacts/doculender-ai/dist/public) to $staticWebApp"
