Param(
  [Parameter(Mandatory = $true)] [string] $SubscriptionId,
  [string] $ResourceGroup = "",
  [string] $EnvironmentName = "dev",
  [string] $Location = "eastus",
  [string] $StaticWebAppLocation = "eastus2",
  [string] $DocumentIntelligenceResourceGroup = "",
  [string] $CognitiveServicesResourceGroup = "",
  [string] $CognitiveServicesAccountName = "",
  [string] $CognitiveServicesKey = "",
  [string] $SearchSku = "basic",
  [string] $DocumentIntelligenceSku = "S0",
  [Parameter(Mandatory = $true)] [string] $ClassifierId,
  [Parameter(Mandatory = $true)] [string] $OpenAIEndpoint,
  [Parameter(Mandatory = $true)] [string] $OpenAIApiKey,
  [string] $OpenAIDeploymentName = "gpt-5.4-mini",
  [string] $OpenAIEmbeddingDeployment = "text-embedding-3-small",
  [Parameter(Mandatory = $true)] [string] $FoundryProjectEndpoint,
  [string] $FoundryApiKey = "",
  [string] $FoundryMemoAgentName = "credit-memo-agent",
  [string] $FoundryChatAgentName = "request-chat-agent",
  [switch] $ReuseExistingResources,
  [string] $ExistingFunctionAppName = "",
  [string] $ExistingProxyContainerAppName = "",
  [string] $ExistingStaticWebAppName = "",
  [string] $ExistingContainerRegistryName = "",
  [string] $ExistingSearchServiceName = "",
  [string] $ExistingCosmosAccountName = "",
  [string] $ExistingStorageAccountName = "",
  [string] $ExistingDocumentIntelligenceName = "",
  [string] $ExistingSqlServerName = "",
  [string] $ExistingSqlDatabaseName = "",
  [string] $SqlEntraAdministratorObjectId = "",
  [string] $SqlEntraAdministratorLogin = "",
  [string] $SqlEntraAdministratorTenantId = "",
  [switch] $SkipFoundryBootstrap,
  [switch] $SkipSmokeTest
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($ResourceGroup)) {
  $ResourceGroup = ("credit-memo-{0}-rg" -f $EnvironmentName).ToLower()
}

if ([string]::IsNullOrWhiteSpace($DocumentIntelligenceResourceGroup)) {
  $DocumentIntelligenceResourceGroup = $ResourceGroup
}

if ([string]::IsNullOrWhiteSpace($CognitiveServicesResourceGroup)) {
  $CognitiveServicesResourceGroup = $ResourceGroup
}

if ([string]::IsNullOrWhiteSpace($FoundryApiKey)) {
  $FoundryApiKey = $OpenAIApiKey
}

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Resolve-Path (Join-Path $scriptRoot "..")

function Get-OutputValue {
  Param(
    [Parameter(Mandatory = $true)] [object] $Outputs,
    [Parameter(Mandatory = $true)] [string] $Name
  )

  $node = $Outputs.PSObject.Properties[$Name]
  if (-not $node) {
    throw "Missing deployment output '$Name'."
  }
  return [string]$node.Value.value
}

function Require-ExistingValue {
  Param(
    [Parameter(Mandatory = $true)] [string] $Value,
    [Parameter(Mandatory = $true)] [string] $Name
  )

  if ([string]::IsNullOrWhiteSpace($Value)) {
    throw "Missing required parameter when -ReuseExistingResources is set: $Name"
  }

  return $Value
}

function Assert-LastExitCode {
  Param(
    [Parameter(Mandatory = $true)] [string] $Step
  )

  if ($LASTEXITCODE -ne 0) {
    throw "$Step failed with exit code $LASTEXITCODE"
  }
}

function Resolve-SqlEntraAdministrator {
  if ([string]::IsNullOrWhiteSpace($script:SqlEntraAdministratorTenantId)) {
    $script:SqlEntraAdministratorTenantId = az account show --query tenantId --output tsv
    Assert-LastExitCode -Step "Resolving Microsoft Entra tenant ID"
  }

  if ([string]::IsNullOrWhiteSpace($script:SqlEntraAdministratorObjectId)) {
    $script:SqlEntraAdministratorObjectId = az ad signed-in-user show --query id --output tsv 2>$null
    if ($LASTEXITCODE -ne 0) {
      throw "Unable to resolve the signed-in Microsoft Entra user. Supply -SqlEntraAdministratorObjectId and -SqlEntraAdministratorLogin when deploying with a service principal."
    }
  }

  if ([string]::IsNullOrWhiteSpace($script:SqlEntraAdministratorLogin)) {
    $script:SqlEntraAdministratorLogin = az account show --query user.name --output tsv
    Assert-LastExitCode -Step "Resolving Microsoft Entra administrator login"
  }

  Require-ExistingValue -Value $script:SqlEntraAdministratorObjectId -Name "SqlEntraAdministratorObjectId" | Out-Null
  Require-ExistingValue -Value $script:SqlEntraAdministratorLogin -Name "SqlEntraAdministratorLogin" | Out-Null
  Require-ExistingValue -Value $script:SqlEntraAdministratorTenantId -Name "SqlEntraAdministratorTenantId" | Out-Null
}

function New-SqlAdministratorPassword {
  $characters = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
  $suffix = -join (1..29 | ForEach-Object { $characters[(Get-Random -Minimum 0 -Maximum $characters.Length)] })
  return "Aa1!$suffix"
}

Push-Location $repoRoot
try {
"Checking prerequisites..."
& (Join-Path $scriptRoot "check-prereqs.ps1")

""
"Using subscription: $SubscriptionId"
az account set --subscription $SubscriptionId | Out-Null
Assert-LastExitCode -Step "Setting Azure subscription"

"Ensuring resource group '$ResourceGroup' in '$Location'..."
az group create --name $ResourceGroup --location $Location | Out-Null
Assert-LastExitCode -Step "Creating/updating resource group"

if ([string]::IsNullOrWhiteSpace($CognitiveServicesKey) -and -not [string]::IsNullOrWhiteSpace($CognitiveServicesAccountName)) {
  "Resolving Azure AI Services key from account '$CognitiveServicesAccountName'..."
  $CognitiveServicesKey = az cognitiveservices account keys list --resource-group $CognitiveServicesResourceGroup --name $CognitiveServicesAccountName --query "key1" --output tsv
  Assert-LastExitCode -Step "Reading Azure AI Services key"
}

if ([string]::IsNullOrWhiteSpace($CognitiveServicesKey)) {
  "Warning: no Azure AI Services key was provided. Search skillset will use DefaultCognitiveServices (limited enrichment quota)."
}

if ($ReuseExistingResources) {
  "Reusing existing resources (skipping infra provisioning)..."
  $functionAppName = Require-ExistingValue -Value $ExistingFunctionAppName -Name "ExistingFunctionAppName"
  $proxyContainerAppName = Require-ExistingValue -Value $ExistingProxyContainerAppName -Name "ExistingProxyContainerAppName"
  $staticWebAppName = Require-ExistingValue -Value $ExistingStaticWebAppName -Name "ExistingStaticWebAppName"
  $containerRegistryName = Require-ExistingValue -Value $ExistingContainerRegistryName -Name "ExistingContainerRegistryName"
  $searchServiceName = Require-ExistingValue -Value $ExistingSearchServiceName -Name "ExistingSearchServiceName"
  $cosmosAccountName = Require-ExistingValue -Value $ExistingCosmosAccountName -Name "ExistingCosmosAccountName"
  $storageAccountName = Require-ExistingValue -Value $ExistingStorageAccountName -Name "ExistingStorageAccountName"
  $documentIntelligenceName = Require-ExistingValue -Value $ExistingDocumentIntelligenceName -Name "ExistingDocumentIntelligenceName"
  $sqlServerName = $ExistingSqlServerName.Trim()
  $sqlDatabaseName = $ExistingSqlDatabaseName.Trim()
  if ([string]::IsNullOrWhiteSpace($sqlServerName) -xor [string]::IsNullOrWhiteSpace($sqlDatabaseName)) {
    throw "ExistingSqlServerName and ExistingSqlDatabaseName must be supplied together when reusing an existing SQL deployment."
  }
  $functionApiBaseUrl = "https://$functionAppName.azurewebsites.net/api"
}
else {
  Resolve-SqlEntraAdministrator
  $temporarySqlAdministratorPassword = New-SqlAdministratorPassword
  $deploymentName = "cm-infra-{0}" -f (Get-Date -Format "yyyyMMddHHmmss")
  "Deploying infrastructure (Bicep)..."
  $outputsJson = az deployment group create `
    --name $deploymentName `
    --resource-group $ResourceGroup `
    --template-file (Join-Path $repoRoot "infra/main.bicep") `
    --parameters "environmentName=$EnvironmentName" "location=$Location" "staticWebAppLocation=$StaticWebAppLocation" "searchSku=$SearchSku" "documentIntelligenceSku=$DocumentIntelligenceSku" "azureOpenAIEndpoint=$OpenAIEndpoint" "azureOpenAIApiKey=$OpenAIApiKey" "azureOpenAIEmbeddingDeployment=$OpenAIEmbeddingDeployment" "azureCognitiveServicesKey=$CognitiveServicesKey" "sqlEntraAdministratorObjectId=$SqlEntraAdministratorObjectId" "sqlEntraAdministratorLogin=$SqlEntraAdministratorLogin" "sqlEntraAdministratorTenantId=$SqlEntraAdministratorTenantId" "sqlAdministratorPassword=$temporarySqlAdministratorPassword" `
    --query "properties.outputs" `
    --output json
  Assert-LastExitCode -Step "Deploying infrastructure template"

  $outputs = $outputsJson | ConvertFrom-Json

  $functionAppName = Get-OutputValue -Outputs $outputs -Name "functionAppName"
  $proxyContainerAppName = Get-OutputValue -Outputs $outputs -Name "proxyContainerAppName"
  $staticWebAppName = Get-OutputValue -Outputs $outputs -Name "staticWebAppName"
  $containerRegistryName = Get-OutputValue -Outputs $outputs -Name "containerRegistryName"
  $searchServiceName = Get-OutputValue -Outputs $outputs -Name "searchServiceName"
  $cosmosAccountName = Get-OutputValue -Outputs $outputs -Name "cosmosAccountName"
  $storageAccountName = Get-OutputValue -Outputs $outputs -Name "storageAccountName"
  $documentIntelligenceName = Get-OutputValue -Outputs $outputs -Name "documentIntelligenceName"
  $sqlServerName = Get-OutputValue -Outputs $outputs -Name "sqlServerName"
  $sqlDatabaseName = Get-OutputValue -Outputs $outputs -Name "sqlDatabaseName"
  $functionApiBaseUrl = Get-OutputValue -Outputs $outputs -Name "functionApiBaseUrl"
}

"Ensuring the Function App has a system-assigned managed identity..."
az functionapp identity assign --resource-group $ResourceGroup --name $functionAppName | Out-Null
Assert-LastExitCode -Step "Assigning Function App managed identity"

"Deploying Azure SQL schema and seed data..."
$sqlDeploymentArguments = @{
  SubscriptionId = $SubscriptionId
  ResourceGroup = $ResourceGroup
  EnvironmentName = $EnvironmentName
  Location = $Location
  FunctionAppName = $functionAppName
}
if ($sqlServerName -and $sqlDatabaseName) {
  $sqlDeploymentArguments.SqlServerName = $sqlServerName
  $sqlDeploymentArguments.SqlDatabaseName = $sqlDatabaseName
  $sqlDeploymentArguments.SkipInfrastructure = $true
}
else {
  Resolve-SqlEntraAdministrator
  $sqlDeploymentArguments.SqlEntraAdministratorObjectId = $SqlEntraAdministratorObjectId
  $sqlDeploymentArguments.SqlEntraAdministratorLogin = $SqlEntraAdministratorLogin
  $sqlDeploymentArguments.SqlEntraAdministratorTenantId = $SqlEntraAdministratorTenantId
}
& (Join-Path $scriptRoot "deploy-sql.ps1") @sqlDeploymentArguments
Assert-LastExitCode -Step "Deploying Azure SQL schema and seed data"

"Collecting infra connection details..."
$storageConnectionString = az storage account show-connection-string --resource-group $ResourceGroup --name $storageAccountName --query connectionString --output tsv
$cosmosEndpoint = az cosmosdb show --resource-group $ResourceGroup --name $cosmosAccountName --query documentEndpoint --output tsv
$cosmosKey = az cosmosdb keys list --resource-group $ResourceGroup --name $cosmosAccountName --type keys --query primaryMasterKey --output tsv
$searchEndpoint = "https://$searchServiceName.search.windows.net"
$searchAdminKey = az search admin-key show --resource-group $ResourceGroup --service-name $searchServiceName --query primaryKey --output tsv
$docIntelEndpoint = az cognitiveservices account show --resource-group $DocumentIntelligenceResourceGroup --name $documentIntelligenceName --query properties.endpoint --output tsv
$docIntelKey = az cognitiveservices account keys list --resource-group $DocumentIntelligenceResourceGroup --name $documentIntelligenceName --query key1 --output tsv

"Configuring Azure Functions app settings..."
az functionapp config appsettings set --resource-group $ResourceGroup --name $functionAppName --settings `
  "AZURE_STORAGE_CONNECTION_STRING=$storageConnectionString" `
  "BLOB_CONTAINER_NAME=loan-documents" `
  "BLOB_MEMO_CONTAINER_NAME=loan-credit-memos" `
  "COSMOS_DB_ENDPOINT=$cosmosEndpoint" `
  "COSMOS_DB_KEY=$cosmosKey" `
  "COSMOS_DB_DATABASE=docintellidb" `
  "COSMOS_DB_CONTAINER=loan-documents-index" `
  "COSMOS_DB_MEMO_CONTAINER=credit-memos" `
  "AZURE_SEARCH_ENDPOINT=$searchEndpoint" `
  "AZURE_SEARCH_ADMIN_KEY=$searchAdminKey" `
  "AZURE_SEARCH_INDEX_NAME=loan-documents-index" `
  "AZURE_SEARCH_INDEXER_NAME=loan-documents-indexer" `
  "AZURE_SEARCH_DATASOURCE_NAME=loan-documents-datasource" `
  "AZURE_SEARCH_SKILLSET_NAME=loan-documents-skillset" `
  "DOCUMENT_INTELLIGENCE_ENDPOINT=$docIntelEndpoint" `
  "DOCUMENT_INTELLIGENCE_KEY=$docIntelKey" `
  "AZURE_COGNITIVE_SERVICES_KEY=$CognitiveServicesKey" `
  "CLASSIFIER_ID=$ClassifierId" `
  "AZURE_OPENAI_ENDPOINT=$OpenAIEndpoint" `
  "AZURE_OPENAI_API_KEY=$OpenAIApiKey" `
  "AZURE_OPENAI_KEY=$OpenAIApiKey" `
  "AZURE_OPENAI_DEPLOYMENT_NAME=$OpenAIDeploymentName" `
  "AZURE_OPENAI_EMBEDDING_DEPLOYMENT=$OpenAIEmbeddingDeployment" `
  "FOUNDRY_PROJECT_ENDPOINT=$FoundryProjectEndpoint" `
  "FOUNDRY_API_KEY=$FoundryApiKey" `
  "FOUNDRY_MEMO_AGENT_NAME=$FoundryMemoAgentName" `
  "FOUNDRY_CHAT_AGENT_NAME=$FoundryChatAgentName" | Out-Null
Assert-LastExitCode -Step "Configuring Function App settings"

"Deploying Azure Functions code..."
func azure functionapp publish "$functionAppName"
Assert-LastExitCode -Step "Publishing Function App code"

"Azure AI Search artifacts are provisioned via Bicep deployment script."

"Fetching Function key for proxy auth forwarding..."
$functionKey = az functionapp keys list --resource-group $ResourceGroup --name $functionAppName --query "functionKeys.default" --output tsv
Assert-LastExitCode -Step "Retrieving Function key"
if ([string]::IsNullOrWhiteSpace($functionKey)) {
  $functionKey = az functionapp keys list --resource-group $ResourceGroup --name $functionAppName --query "hostKeys.default" --output tsv
  Assert-LastExitCode -Step "Retrieving host key"
}
if ([string]::IsNullOrWhiteSpace($functionKey)) {
  throw "Unable to retrieve Function key for proxy deployment."
}

"Installing workspace dependencies..."
pnpm install --frozen-lockfile --dir (Join-Path $repoRoot "actual_ui/Code-Generation-UI")
Assert-LastExitCode -Step "Installing UI/proxy workspace dependencies"

"Building proxy API bundle..."
pnpm --dir (Join-Path $repoRoot "actual_ui/Code-Generation-UI") --filter @workspace/api-server run build
Assert-LastExitCode -Step "Building proxy API bundle"

"Building and pushing proxy image to ACR..."
$imageTag = "{0}-proxy" -f (Get-Date -Format "yyyyMMddHHmmss")
$acrLoginServer = az acr show --resource-group $ResourceGroup --name $containerRegistryName --query loginServer --output tsv
$proxyImage = "$acrLoginServer/cm-proxy:$imageTag"
$proxyDockerfile = Join-Path $repoRoot "actual_ui/Code-Generation-UI/artifacts/api-server/Dockerfile"
$proxyBuildContext = Join-Path $repoRoot "actual_ui/Code-Generation-UI/artifacts/api-server"
az acr build --registry $containerRegistryName --resource-group $ResourceGroup --image "cm-proxy:$imageTag" --file "$proxyDockerfile" "$proxyBuildContext" --output none
Assert-LastExitCode -Step "Building and pushing proxy image"

"Updating proxy container app revision..."
az containerapp secret set --resource-group $ResourceGroup --name $proxyContainerAppName --secrets "azure-func-api-key=$functionKey" | Out-Null
Assert-LastExitCode -Step "Updating proxy secrets"
az containerapp update --resource-group $ResourceGroup --name $proxyContainerAppName --image $proxyImage --set-env-vars `
  "NODE_ENV=production" `
  "LOG_LEVEL=info" `
  "PORT=8080" `
  "AZURE_FUNC_API_URL=$functionApiBaseUrl" `
  "AZURE_FUNC_API_KEY=secretref:azure-func-api-key" | Out-Null
Assert-LastExitCode -Step "Updating proxy container app"

$proxyFqdn = az containerapp show --resource-group $ResourceGroup --name $proxyContainerAppName --query "properties.configuration.ingress.fqdn" --output tsv
Assert-LastExitCode -Step "Reading proxy ingress URL"
$proxyBaseUrl = "https://$proxyFqdn"

"Building UI..."
$env:PORT = "5173"
$env:BASE_PATH = "/"
pnpm --dir (Join-Path $repoRoot "actual_ui/Code-Generation-UI") --filter @workspace/doculender-ai run build
Assert-LastExitCode -Step "Building React UI"

"Deploying UI to Static Web Apps..."
$deploymentToken = az staticwebapp secrets list --resource-group $ResourceGroup --name $staticWebAppName --query "properties.apiKey" --output tsv
Assert-LastExitCode -Step "Reading Static Web App deployment token"
$uiDist = Join-Path $repoRoot "actual_ui/Code-Generation-UI/artifacts/doculender-ai/dist/public"
if (-not (Test-Path -LiteralPath $uiDist)) {
  throw "UI dist folder not found at '$uiDist'. Build did not produce deployable artifacts."
}
npx --yes @azure/static-web-apps-cli deploy "$uiDist" --env production --deployment-token "$deploymentToken"
Assert-LastExitCode -Step "Deploying Static Web App"

$staticHost = az staticwebapp show --resource-group $ResourceGroup --name $staticWebAppName --query "defaultHostname" --output tsv
Assert-LastExitCode -Step "Reading Static Web App hostname"
$staticWebAppUrl = "https://$staticHost"

if (-not $SkipFoundryBootstrap) {
  "Bootstrapping Foundry agents from repo config..."
  & cmd /c "python -c \"import azure.ai.projects\" >nul 2>nul"
  $pythonProjectsImportExitCode = $LASTEXITCODE
  if ($pythonProjectsImportExitCode -ne 0) {
    "Installing Python backend dependencies for Foundry bootstrap..."
    python -m pip install -r (Join-Path $repoRoot "requirements.txt")
    Assert-LastExitCode -Step "Installing Python dependencies"
  }

  $bootstrapJsonFile = Join-Path $env:TEMP ("credit-memo-foundry-{0}.json" -f (Get-Date -Format "yyyyMMddHHmmss"))
  python (Join-Path $repoRoot "scripts/bootstrap_foundry_agents.py") `
    --project-endpoint "$FoundryProjectEndpoint" `
    --model-deployment "$OpenAIDeploymentName" `
    --memo-config (Join-Path $repoRoot "config/foundry-agents/memo-agent.json") `
    --chat-config (Join-Path $repoRoot "config/foundry-agents/chat-agent.json") `
    --json-out "$bootstrapJsonFile"
  Assert-LastExitCode -Step "Bootstrapping Foundry agents"
  if (-not (Test-Path -LiteralPath $bootstrapJsonFile)) {
    throw "Foundry bootstrap did not produce output file '$bootstrapJsonFile'."
  }

  $agentResult = (Get-Content -LiteralPath $bootstrapJsonFile -Raw) | ConvertFrom-Json
  $memoAgentId = [string]$agentResult.memoAgent.id
  $chatAgentId = [string]$agentResult.chatAgent.id
  $memoAgentResolvedName = [string]$agentResult.memoAgent.name
  $chatAgentResolvedName = [string]$agentResult.chatAgent.name

  if ($memoAgentId -and $chatAgentId) {
    az functionapp config appsettings set --resource-group $ResourceGroup --name $functionAppName --settings `
      "FOUNDRY_MEMO_AGENT_ID=$memoAgentId" `
      "FOUNDRY_CHAT_AGENT_ID=$chatAgentId" `
      "FOUNDRY_MEMO_AGENT_NAME=$memoAgentResolvedName" `
      "FOUNDRY_CHAT_AGENT_NAME=$chatAgentResolvedName" | Out-Null
  }
}

if (-not $SkipSmokeTest) {
  "Running smoke tests..."
  & (Join-Path $scriptRoot "smoke-test.ps1") -ProxyBaseUrl $proxyBaseUrl -StaticWebAppUrl $staticWebAppUrl
}

""
"Deployment complete"
"Resource Group: $ResourceGroup"
"Function App: $functionAppName"
"Azure SQL Server: $sqlServerName.database.windows.net"
"Azure SQL Database: $sqlDatabaseName"
"Proxy URL: $proxyBaseUrl"
"Static Web App URL: $staticWebAppUrl"
}
finally {
  Pop-Location
}
