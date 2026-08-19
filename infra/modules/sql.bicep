targetScope = 'resourceGroup'

@description('Deployment location for the Azure SQL resources.')
param location string

@description('Environment name used for deterministic naming.')
param environmentName string

@description('Tags applied to Azure SQL resources.')
param tags object = {}

@description('Microsoft Entra administrator object ID for the Azure SQL logical server.')
param entraAdministratorObjectId string

@description('Microsoft Entra administrator display name or sign-in name for the Azure SQL logical server.')
param entraAdministratorLogin string

@description('Microsoft Entra tenant ID for the Azure SQL logical server administrator.')
param entraAdministratorTenantId string

@description('Temporary SQL administrator used only while Azure SQL is created before Entra-only authentication is enabled.')
param sqlAdministratorLogin string = 'cmbootstrapadmin'

@secure()
@description('Temporary SQL administrator password. This is not retained by the deployment scripts.')
param sqlAdministratorPassword string

@description('Set to true only while creating a new logical server before Entra-only authentication is enabled.')
param includeBootstrapSqlAdministrator bool = true

@description('Azure SQL database name.')
param databaseName string = 'creditmemo'

var suffix = toLower(uniqueString(resourceGroup().id, environmentName))
var serverName = toLower('cm-sql-${take(suffix, 12)}')

resource sqlServer 'Microsoft.Sql/servers@2023-08-01-preview' = {
  name: serverName
  location: location
  tags: tags
  properties: union({
    version: '12.0'
    minimalTlsVersion: '1.2'
    publicNetworkAccess: 'Enabled'
    restrictOutboundNetworkAccess: 'Disabled'
  }, includeBootstrapSqlAdministrator ? {
    administratorLogin: sqlAdministratorLogin
    administratorLoginPassword: sqlAdministratorPassword
  } : {})
}

resource sqlEntraAdministrator 'Microsoft.Sql/servers/administrators@2023-08-01-preview' = {
  name: 'ActiveDirectory'
  parent: sqlServer
  properties: {
    administratorType: 'ActiveDirectory'
    login: entraAdministratorLogin
    sid: entraAdministratorObjectId
    tenantId: entraAdministratorTenantId
  }
}

resource sqlEntraOnlyAuthentication 'Microsoft.Sql/servers/azureADOnlyAuthentications@2023-08-01-preview' = {
  name: 'Default'
  parent: sqlServer
  properties: {
    azureADOnlyAuthentication: true
  }
  dependsOn: [
    sqlEntraAdministrator
  ]
}

resource allowAzureServices 'Microsoft.Sql/servers/firewallRules@2023-08-01-preview' = {
  name: 'AllowAzureServices'
  parent: sqlServer
  properties: {
    startIpAddress: '0.0.0.0'
    endIpAddress: '0.0.0.0'
  }
}

resource sqlDatabase 'Microsoft.Sql/servers/databases@2023-08-01-preview' = {
  name: databaseName
  parent: sqlServer
  location: location
  tags: tags
  sku: {
    name: 'GP_S_Gen5_1'
    tier: 'GeneralPurpose'
    family: 'Gen5'
    capacity: 1
  }
  properties: {
    autoPauseDelay: 60
    minCapacity: json('0.5')
    maxSizeBytes: 34359738368
    requestedBackupStorageRedundancy: 'Local'
    zoneRedundant: false
  }
  dependsOn: [
    sqlEntraOnlyAuthentication
  ]
}

output sqlServerName string = sqlServer.name
output sqlDatabaseName string = sqlDatabase.name
output sqlServerFullyQualifiedDomainName string = sqlServer.properties.fullyQualifiedDomainName
