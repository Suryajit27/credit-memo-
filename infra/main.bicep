targetScope = 'resourceGroup'

@description('Deployment location for most resources.')
param location string = resourceGroup().location

@description('Location for the Static Web App.')
param staticWebAppLocation string = 'eastus2'

@description('Environment name used for deterministic naming (for example dev, staging, prod).')
param environmentName string = 'dev'

@description('Azure OpenAI endpoint used by search skillset embedding and runtime app settings.')
param azureOpenAIEndpoint string

@secure()
@description('Azure OpenAI API key used by search skillset embedding and runtime app settings.')
param azureOpenAIApiKey string

@description('Azure OpenAI embedding deployment name used by search skillset.')
param azureOpenAIEmbeddingDeployment string = 'text-embedding-3-small'

@secure()
@description('Optional Azure AI Services multi-service key for AI Search enrichment. Leave empty to use DefaultCognitiveServices (limited free enrichment).')
param azureCognitiveServicesKey string = ''

@description('Tags applied to all resources.')
param tags object = {}

@allowed([
  'free'
  'basic'
  'standard'
  'standard2'
  'standard3'
])
@description('Azure AI Search SKU.')
param searchSku string = 'basic'

@allowed([
  'Free'
  'Basic'
])
@description('Azure Container Registry SKU.')
param acrSku string = 'Basic'

@allowed([
  'F0'
  'S0'
])
@description('Document Intelligence SKU. F0 is best-effort and may be unavailable in some subscriptions/regions.')
param documentIntelligenceSku string = 'S0'

@description('Cosmos SQL database name.')
param cosmosDatabaseName string = 'docintellidb'

@description('Cosmos SQL container for document indexing telemetry.')
param cosmosDocumentsContainerName string = 'loan-documents-index'

@description('Cosmos SQL container for memo state.')
param cosmosMemoContainerName string = 'credit-memos'

@description('Blob container for source loan documents.')
param blobDocumentsContainerName string = 'loan-documents'

@description('Blob container for finalized memo files.')
param blobMemoContainerName string = 'loan-credit-memos'

@description('Microsoft Entra administrator object ID for the Azure SQL logical server.')
param sqlEntraAdministratorObjectId string

@description('Microsoft Entra administrator display name or sign-in name for the Azure SQL logical server.')
param sqlEntraAdministratorLogin string

@description('Microsoft Entra tenant ID for the Azure SQL logical server administrator.')
param sqlEntraAdministratorTenantId string

@secure()
@description('Temporary SQL administrator password used while Azure SQL is created before Entra-only authentication is enabled.')
param sqlAdministratorPassword string

var suffix = toLower(uniqueString(resourceGroup().id, environmentName))
var storageAccountName = toLower('cmst${take(suffix, 20)}')
var cosmosAccountName = toLower('cm-cosmos-${take(suffix, 12)}')
var searchServiceName = toLower('cm-search-${take(suffix, 12)}')
var functionPlanName = toLower('cm-func-plan-${take(suffix, 10)}')
var functionAppName = toLower('cm-func-${take(suffix, 12)}')
var appInsightsName = toLower('cm-appi-${take(suffix, 12)}')
var logAnalyticsName = toLower('cm-logs-${take(suffix, 12)}')
var acrName = toLower('cmacr${take(suffix, 20)}')
var containerEnvironmentName = toLower('cm-ca-env-${take(suffix, 12)}')
var proxyContainerAppName = toLower('cm-proxy-${take(suffix, 12)}')
var staticWebAppName = toLower('cm-ui-${take(suffix, 12)}')
var documentIntelligenceName = toLower('cm-docintel-${take(suffix, 12)}')
var searchDatasourceName = 'loan-documents-datasource'
var searchIndexName = 'loan-documents-index'
var searchSkillsetName = 'loan-documents-skillset'
var searchIndexerName = 'loan-documents-indexer'

module sql 'modules/sql.bicep' = {
  name: 'sql'
  params: {
    location: location
    environmentName: environmentName
    tags: tags
    entraAdministratorObjectId: sqlEntraAdministratorObjectId
    entraAdministratorLogin: sqlEntraAdministratorLogin
    entraAdministratorTenantId: sqlEntraAdministratorTenantId
    sqlAdministratorPassword: sqlAdministratorPassword
  }
}

resource storageAccount 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageAccountName
  location: location
  tags: tags
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    supportsHttpsTrafficOnly: true
    allowSharedKeyAccess: true
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  name: 'default'
  parent: storageAccount
}

resource blobDocumentsContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  name: blobDocumentsContainerName
  parent: blobService
  properties: {
    publicAccess: 'None'
  }
}

resource blobMemoContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  name: blobMemoContainerName
  parent: blobService
  properties: {
    publicAccess: 'None'
  }
}

resource cosmosAccount 'Microsoft.DocumentDB/databaseAccounts@2024-05-15' = {
  name: cosmosAccountName
  location: location
  tags: tags
  kind: 'GlobalDocumentDB'
  properties: {
    databaseAccountOfferType: 'Standard'
    locations: [
      {
        locationName: location
        failoverPriority: 0
        isZoneRedundant: false
      }
    ]
    consistencyPolicy: {
      defaultConsistencyLevel: 'Session'
    }
    disableLocalAuth: false
    enableAutomaticFailover: false
    publicNetworkAccess: 'Enabled'
  }
}

resource cosmosSqlDatabase 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2024-05-15' = {
  name: cosmosDatabaseName
  parent: cosmosAccount
  properties: {
    resource: {
      id: cosmosDatabaseName
    }
    options: {}
  }
}

resource cosmosDocsContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-05-15' = {
  name: cosmosDocumentsContainerName
  parent: cosmosSqlDatabase
  properties: {
    resource: {
      id: cosmosDocumentsContainerName
      partitionKey: {
        paths: [
          '/requestId'
        ]
        kind: 'Hash'
      }
    }
    options: {}
  }
}

resource cosmosMemoContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-05-15' = {
  name: cosmosMemoContainerName
  parent: cosmosSqlDatabase
  properties: {
    resource: {
      id: cosmosMemoContainerName
      partitionKey: {
        paths: [
          '/requestId'
        ]
        kind: 'Hash'
      }
    }
    options: {}
  }
}

resource searchService 'Microsoft.Search/searchServices@2023-11-01' = {
  name: searchServiceName
  location: location
  tags: tags
  sku: {
    name: searchSku
  }
  properties: {
    replicaCount: 1
    partitionCount: 1
    publicNetworkAccess: 'enabled'
    semanticSearch: 'free'
  }
}

resource documentIntelligence 'Microsoft.CognitiveServices/accounts@2024-10-01' = {
  name: documentIntelligenceName
  location: location
  tags: tags
  kind: 'FormRecognizer'
  sku: {
    name: documentIntelligenceSku
  }
  properties: {
    customSubDomainName: documentIntelligenceName
    publicNetworkAccess: 'Enabled'
  }
}

resource searchArtifactsBootstrap 'Microsoft.Resources/deploymentScripts@2023-08-01' = {
  name: 'search-artifacts-bootstrap'
  location: location
  kind: 'AzureCLI'
  properties: {
    azCliVersion: '2.61.0'
    timeout: 'PT30M'
    retentionInterval: 'P1D'
    cleanupPreference: 'OnSuccess'
    scriptContent: '''
set -euo pipefail

API_VERSION="2024-07-01"
D='$'

if [ -n "${AZURE_COGNITIVE_SERVICES_KEY:-}" ]; then
  COGNITIVE_SERVICES_BLOCK="{\"@odata.type\":\"#Microsoft.Azure.Search.CognitiveServicesByKey\",\"key\":\"$AZURE_COGNITIVE_SERVICES_KEY\"}"
else
  COGNITIVE_SERVICES_BLOCK="{\"@odata.type\":\"#Microsoft.Azure.Search.DefaultCognitiveServices\"}"
fi

put_resource() {
  local url="$1"
  local payload="$2"
  az rest \
    --method put \
    --uri "$url" \
    --headers "Content-Type=application/json" "api-key=$SEARCH_ADMIN_KEY" \
    --body "$payload" \
    --only-show-errors > /dev/null
}

DATASOURCE_PAYLOAD=$(cat <<EOF
{
  "name": "$DATASOURCE_NAME",
  "type": "azureblob",
  "credentials": {
    "connectionString": "$STORAGE_CONNECTION_STRING"
  },
  "container": {
    "name": "$BLOB_CONTAINER_NAME"
  }
}
EOF
)

INDEX_PAYLOAD=$(cat <<EOF
{
  "name": "$INDEX_NAME",
  "fields": [
    {"name": "id", "type": "Edm.String", "key": true, "searchable": false, "filterable": true},
    {"name": "content", "type": "Edm.String", "searchable": true, "filterable": false},
    {"name": "contentVector", "type": "Collection(Edm.Single)", "searchable": true, "dimensions": 1536, "vectorSearchProfile": "myHnswProfile"},
    {"name": "requestId", "type": "Edm.String", "searchable": false, "filterable": true, "facetable": true},
    {"name": "documentType", "type": "Edm.String", "searchable": true, "filterable": true, "facetable": true},
    {"name": "confidence", "type": "Edm.Double", "searchable": false, "filterable": true},
    {"name": "blobName", "type": "Edm.String", "searchable": true, "filterable": true},
    {"name": "uploadedAt", "type": "Edm.String", "searchable": false, "filterable": true, "sortable": true},
    {"name": "chunkIndex", "type": "Edm.Int32", "searchable": false, "filterable": true, "sortable": true},
    {"name": "totalChunks", "type": "Edm.Int32", "searchable": false, "filterable": true}
  ],
  "vectorSearch": {
    "algorithms": [{"name": "myHnsw", "kind": "hnsw"}],
    "profiles": [{"name": "myHnswProfile", "algorithm": "myHnsw"}]
  }
}
EOF
)

SKILLSET_PAYLOAD=$(cat <<EOF
{
  "name": "$SKILLSET_NAME",
  "description": "OCR -> Merge -> Embed skillset for loan documents",
  "skills": [
    {
      "@odata.type": "#Microsoft.Skills.Vision.OcrSkill",
      "name": "ocr-skill",
      "context": "/document/normalized_images/*",
      "defaultLanguageCode": "en",
      "detectOrientation": true,
      "inputs": [{"name": "image", "source": "/document/normalized_images/*"}],
      "outputs": [{"name": "text", "targetName": "text"}]
    },
    {
      "@odata.type": "#Microsoft.Skills.Text.MergeSkill",
      "name": "merge-skill",
      "context": "/document",
      "insertPreTag": " ",
      "insertPostTag": " ",
      "inputs": [
        {"name": "text", "source": "/document/content"},
        {"name": "itemsToInsert", "source": "= ${D}(/document/normalized_images/*/text)"}
      ],
      "outputs": [{"name": "mergedText", "targetName": "mergedContent"}]
    },
    {
      "@odata.type": "#Microsoft.Skills.Text.AzureOpenAIEmbeddingSkill",
      "name": "embed-skill",
      "context": "/document",
      "resourceUri": "$AZURE_OPENAI_ENDPOINT",
      "apiKey": "$AZURE_OPENAI_KEY",
      "deploymentId": "$AZURE_OPENAI_EMBEDDING_DEPLOYMENT",
      "modelName": "$AZURE_OPENAI_EMBEDDING_DEPLOYMENT",
      "inputs": [{"name": "text", "source": "/document/mergedContent"}],
      "outputs": [{"name": "embedding", "targetName": "contentVector"}]
    }
  ],
  "cognitiveServices": $COGNITIVE_SERVICES_BLOCK
}
EOF
)

INDEXER_PAYLOAD=$(cat <<EOF
{
  "name": "$INDEXER_NAME",
  "dataSourceName": "$DATASOURCE_NAME",
  "targetIndexName": "$INDEX_NAME",
  "skillsetName": "$SKILLSET_NAME",
  "parameters": {
    "configuration": {
      "dataToExtract": "contentAndMetadata",
      "imageAction": "generateNormalizedImages",
      "parsingMode": "default"
    }
  },
  "fieldMappings": [
    {"sourceFieldName": "metadata_storage_path", "targetFieldName": "id", "mappingFunction": {"name": "base64Encode"}},
    {"sourceFieldName": "metadata_storage_path", "targetFieldName": "blobName"},
    {"sourceFieldName": "requestid", "targetFieldName": "requestId"},
    {"sourceFieldName": "documenttype", "targetFieldName": "documentType"},
    {"sourceFieldName": "uploadTimestamp", "targetFieldName": "uploadedAt"},
    {"sourceFieldName": "chunkindex", "targetFieldName": "chunkIndex"},
    {"sourceFieldName": "totalchunks", "targetFieldName": "totalChunks"}
  ],
  "outputFieldMappings": [
    {"sourceFieldName": "/document/mergedContent", "targetFieldName": "content"},
    {"sourceFieldName": "/document/contentVector", "targetFieldName": "contentVector"}
  ]
}
EOF
)

put_resource "$SEARCH_ENDPOINT/datasources/$DATASOURCE_NAME?api-version=$API_VERSION" "$DATASOURCE_PAYLOAD"
put_resource "$SEARCH_ENDPOINT/indexes/$INDEX_NAME?api-version=$API_VERSION" "$INDEX_PAYLOAD"
put_resource "$SEARCH_ENDPOINT/skillsets/$SKILLSET_NAME?api-version=$API_VERSION" "$SKILLSET_PAYLOAD"
put_resource "$SEARCH_ENDPOINT/indexers/$INDEXER_NAME?api-version=$API_VERSION" "$INDEXER_PAYLOAD"

echo "Search artifacts provisioned."
'''
    environmentVariables: [
      {
        name: 'SEARCH_ENDPOINT'
        value: 'https://${searchService.name}.search.windows.net'
      }
      {
        name: 'SEARCH_ADMIN_KEY'
        secureValue: listAdminKeys(searchService.id, searchService.apiVersion).primaryKey
      }
      {
        name: 'STORAGE_CONNECTION_STRING'
        secureValue: 'DefaultEndpointsProtocol=https;AccountName=${storageAccount.name};AccountKey=${listKeys(storageAccount.id, storageAccount.apiVersion).keys[0].value};EndpointSuffix=${environment().suffixes.storage}'
      }
      {
        name: 'BLOB_CONTAINER_NAME'
        value: blobDocumentsContainerName
      }
      {
        name: 'DATASOURCE_NAME'
        value: searchDatasourceName
      }
      {
        name: 'INDEX_NAME'
        value: searchIndexName
      }
      {
        name: 'SKILLSET_NAME'
        value: searchSkillsetName
      }
      {
        name: 'INDEXER_NAME'
        value: searchIndexerName
      }
      {
        name: 'AZURE_OPENAI_ENDPOINT'
        value: azureOpenAIEndpoint
      }
      {
        name: 'AZURE_OPENAI_KEY'
        secureValue: azureOpenAIApiKey
      }
      {
        name: 'AZURE_OPENAI_EMBEDDING_DEPLOYMENT'
        value: azureOpenAIEmbeddingDeployment
      }
      {
        name: 'AZURE_COGNITIVE_SERVICES_KEY'
        secureValue: azureCognitiveServicesKey
      }
    ]
  }
}

resource functionPlan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: functionPlanName
  location: location
  tags: tags
  kind: 'functionapp'
  sku: {
    name: 'Y1'
    tier: 'Dynamic'
  }
  properties: {
    reserved: true
  }
}

resource logAnalyticsWorkspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: logAnalyticsName
  location: location
  tags: tags
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: appInsightsName
  location: location
  kind: 'web'
  tags: tags
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logAnalyticsWorkspace.id
  }
}

resource functionApp 'Microsoft.Web/sites@2023-12-01' = {
  name: functionAppName
  location: location
  tags: tags
  kind: 'functionapp,linux'
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    serverFarmId: functionPlan.id
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: 'Python|3.11'
      minTlsVersion: '1.2'
      appSettings: [
        {
          name: 'AzureWebJobsStorage'
          value: 'DefaultEndpointsProtocol=https;AccountName=${storageAccount.name};AccountKey=${listKeys(storageAccount.id, storageAccount.apiVersion).keys[0].value};EndpointSuffix=${environment().suffixes.storage}'
        }
        {
          name: 'FUNCTIONS_WORKER_RUNTIME'
          value: 'python'
        }
        {
          name: 'FUNCTIONS_EXTENSION_VERSION'
          value: '~4'
        }
        {
          name: 'WEBSITE_RUN_FROM_PACKAGE'
          value: '1'
        }
        {
          name: 'APPINSIGHTS_INSTRUMENTATIONKEY'
          value: appInsights.properties.InstrumentationKey
        }
        {
          name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
          value: appInsights.properties.ConnectionString
        }
        {
          name: 'AZURE_STORAGE_CONNECTION_STRING'
          value: 'DefaultEndpointsProtocol=https;AccountName=${storageAccount.name};AccountKey=${listKeys(storageAccount.id, storageAccount.apiVersion).keys[0].value};EndpointSuffix=${environment().suffixes.storage}'
        }
        {
          name: 'BLOB_CONTAINER_NAME'
          value: blobDocumentsContainerName
        }
        {
          name: 'BLOB_MEMO_CONTAINER_NAME'
          value: blobMemoContainerName
        }
        {
          name: 'COSMOS_DB_ENDPOINT'
          value: cosmosAccount.properties.documentEndpoint
        }
        {
          name: 'COSMOS_DB_KEY'
          value: listKeys(cosmosAccount.id, cosmosAccount.apiVersion).primaryMasterKey
        }
        {
          name: 'COSMOS_DB_DATABASE'
          value: cosmosDatabaseName
        }
        {
          name: 'COSMOS_DB_CONTAINER'
          value: cosmosDocumentsContainerName
        }
        {
          name: 'COSMOS_DB_MEMO_CONTAINER'
          value: cosmosMemoContainerName
        }
        {
          name: 'AZURE_SEARCH_ENDPOINT'
          value: 'https://${searchService.name}.search.windows.net'
        }
        {
          name: 'AZURE_SEARCH_ADMIN_KEY'
          value: listAdminKeys(searchService.id, searchService.apiVersion).primaryKey
        }
        {
          name: 'AZURE_SEARCH_INDEX_NAME'
          value: searchIndexName
        }
        {
          name: 'AZURE_SEARCH_INDEXER_NAME'
          value: searchIndexerName
        }
        {
          name: 'AZURE_SEARCH_DATASOURCE_NAME'
          value: searchDatasourceName
        }
        {
          name: 'AZURE_SEARCH_SKILLSET_NAME'
          value: searchSkillsetName
        }
        {
          name: 'DOCUMENT_INTELLIGENCE_ENDPOINT'
          value: documentIntelligence.properties.endpoint
        }
        {
          name: 'DOCUMENT_INTELLIGENCE_KEY'
          value: listKeys(documentIntelligence.id, documentIntelligence.apiVersion).key1
        }
        {
          name: 'AZURE_COGNITIVE_SERVICES_KEY'
          value: azureCognitiveServicesKey
        }
        {
          name: 'SQL_SERVER'
          value: sql.outputs.sqlServerFullyQualifiedDomainName
        }
        {
          name: 'SQL_DATABASE'
          value: sql.outputs.sqlDatabaseName
        }
        {
          name: 'SQL_ODBC_DRIVER'
          value: 'ODBC Driver 18 for SQL Server'
        }
        {
          name: 'SQL_AUTHENTICATION'
          value: 'managed_identity'
        }
      ]
    }
  }
}

resource containerRegistry 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: acrName
  location: location
  tags: tags
  sku: {
    name: acrSku
  }
  properties: {
    adminUserEnabled: true
    publicNetworkAccess: 'Enabled'
  }
}

resource containerEnvironment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: containerEnvironmentName
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalyticsWorkspace.properties.customerId
        sharedKey: listKeys(logAnalyticsWorkspace.id, logAnalyticsWorkspace.apiVersion).primarySharedKey
      }
    }
  }
}

resource proxyContainerApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: proxyContainerAppName
  location: location
  tags: tags
  properties: {
    managedEnvironmentId: containerEnvironment.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        allowInsecure: false
        targetPort: 8080
        transport: 'auto'
      }
      secrets: [
        {
          name: 'acr-password'
          value: listCredentials(containerRegistry.id, containerRegistry.apiVersion).passwords[0].value
        }
        {
          name: 'azure-func-api-key'
          value: 'placeholder'
        }
      ]
      registries: [
        {
          server: containerRegistry.properties.loginServer
          username: listCredentials(containerRegistry.id, containerRegistry.apiVersion).username
          passwordSecretRef: 'acr-password'
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'api-server'
          image: 'mcr.microsoft.com/k8se/quickstart:latest'
          env: [
            {
              name: 'NODE_ENV'
              value: 'production'
            }
            {
              name: 'LOG_LEVEL'
              value: 'info'
            }
            {
              name: 'PORT'
              value: '8080'
            }
            {
              name: 'AZURE_FUNC_API_URL'
              value: 'https://${functionApp.name}.azurewebsites.net/api'
            }
            {
              name: 'AZURE_FUNC_API_KEY'
              secretRef: 'azure-func-api-key'
            }
          ]
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 1
      }
    }
  }
}

resource staticWebApp 'Microsoft.Web/staticSites@2023-12-01' = {
  name: staticWebAppName
  location: staticWebAppLocation
  tags: tags
  sku: {
    name: 'Free'
    tier: 'Free'
  }
  properties: {}
}

output storageAccountName string = storageAccount.name
output cosmosAccountName string = cosmosAccount.name
output cosmosDatabaseName string = cosmosDatabaseName
output cosmosDocumentsContainerName string = cosmosDocumentsContainerName
output cosmosMemoContainerName string = cosmosMemoContainerName
output searchServiceName string = searchService.name
output functionAppName string = functionApp.name
output proxyContainerAppName string = proxyContainerApp.name
output staticWebAppName string = staticWebApp.name
output containerRegistryName string = containerRegistry.name
output documentIntelligenceName string = documentIntelligence.name
output sqlServerName string = sql.outputs.sqlServerName
output sqlDatabaseName string = sql.outputs.sqlDatabaseName
output sqlServerFullyQualifiedDomainName string = sql.outputs.sqlServerFullyQualifiedDomainName
output functionApiBaseUrl string = 'https://${functionApp.name}.azurewebsites.net/api'
output proxyBaseUrl string = 'https://${proxyContainerApp.properties.configuration.ingress.fqdn}'
output staticWebAppUrl string = 'https://${staticWebApp.properties.defaultHostname}'
