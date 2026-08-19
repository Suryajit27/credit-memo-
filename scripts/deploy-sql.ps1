Param(
  [Parameter(Mandatory = $true)] [string] $SubscriptionId,
  [string] $ResourceGroup = "",
  [string] $EnvironmentName = "dev",
  [string] $Location = "eastus",
  [string] $SqlEntraAdministratorObjectId = "",
  [string] $SqlEntraAdministratorLogin = "",
  [string] $SqlEntraAdministratorTenantId = "",
  [string] $SqlServerName = "",
  [string] $SqlDatabaseName = "",
  [string] $FunctionAppName = "",
  [switch] $SkipInfrastructure
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($ResourceGroup)) {
  $ResourceGroup = ("credit-memo-{0}-rg" -f $EnvironmentName).ToLower()
}

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Resolve-Path (Join-Path $scriptRoot "..")
$migrationsPath = Join-Path $repoRoot "database/migrations"
$seedManifestPath = Join-Path $repoRoot "database/seeds/seed-manifest.json"

function Assert-LastExitCode {
  Param([Parameter(Mandatory = $true)] [string] $Step)

  if ($LASTEXITCODE -ne 0) {
    throw "$Step failed with exit code $LASTEXITCODE"
  }
}

function Require-Value {
  Param(
    [Parameter(Mandatory = $true)] [string] $Value,
    [Parameter(Mandatory = $true)] [string] $Name
  )

  if ([string]::IsNullOrWhiteSpace($Value)) {
    throw "Missing required value: $Name"
  }

  return $Value
}

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

function New-SqlAdministratorPassword {
  $characters = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
  $suffix = -join (1..29 | ForEach-Object { $characters[(Get-Random -Minimum 0 -Maximum $characters.Length)] })
  return "Aa1!$suffix"
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

  Require-Value -Value $script:SqlEntraAdministratorObjectId -Name "SqlEntraAdministratorObjectId" | Out-Null
  Require-Value -Value $script:SqlEntraAdministratorLogin -Name "SqlEntraAdministratorLogin" | Out-Null
  Require-Value -Value $script:SqlEntraAdministratorTenantId -Name "SqlEntraAdministratorTenantId" | Out-Null
}

function Invoke-SqlCommand {
  Param(
    [string] $Query = "",
    [string] $InputFile = ""
  )

  $sqlcmd = Get-Command -Name "sqlcmd" -ErrorAction SilentlyContinue
  if (-not $sqlcmd) {
    throw "sqlcmd is required to apply Azure SQL migrations. Install Microsoft SQL Server command-line tools, then retry."
  }

  $arguments = @(
    "-S", "$script:SqlServerName.database.windows.net",
    "-d", $script:SqlDatabaseName,
    "-G",
    "-C",
    "-b",
    "-l", "30"
  )

  if (-not [string]::IsNullOrWhiteSpace($InputFile)) {
    $arguments += @("-i", $InputFile)
  }
  else {
    $arguments += @("-Q", $Query)
  }

  for ($attempt = 1; $attempt -le 3; $attempt++) {
    $result = & $sqlcmd.Source @arguments 2>&1
    if ($LASTEXITCODE -eq 0) {
      return $result
    }

    $errorText = $result -join [Environment]::NewLine
    $isTransientConnectionFailure = $errorText -match "TLS Handshake failed|forcibly closed|connection.*closed|network-related"
    if (-not $isTransientConnectionFailure -or $attempt -eq 3) {
      throw "Azure SQL command failed: $errorText"
    }

    "Retrying transient Azure SQL connection failure (attempt $attempt of 3)..."
    Start-Sleep -Seconds 5
  }
}

function Ensure-DeploymentFirewallRule {
  try {
    $publicIp = (Invoke-RestMethod -Uri "https://api.ipify.org?format=json" -TimeoutSec 20).ip
  }
  catch {
    throw "Unable to determine the deployment machine's public IP address for the Azure SQL firewall rule: $($_.Exception.Message)"
  }

  $ruleName = "deployment-client"
  $ruleCount = az sql server firewall-rule list --resource-group $ResourceGroup --server $script:SqlServerName --query "[?name=='$ruleName'] | length(@)" --output tsv
  Assert-LastExitCode -Step "Reading Azure SQL firewall rules"
  if ($ruleCount -eq "1") {
    az sql server firewall-rule update --resource-group $ResourceGroup --server $script:SqlServerName --name $ruleName --start-ip-address $publicIp --end-ip-address $publicIp --output none
  }
  else {
    az sql server firewall-rule create --resource-group $ResourceGroup --server $script:SqlServerName --name $ruleName --start-ip-address $publicIp --end-ip-address $publicIp --output none
  }
  Assert-LastExitCode -Step "Configuring deployment machine Azure SQL firewall rule"
}

function Wait-ForSqlReady {
  $lastError = $null
  for ($attempt = 1; $attempt -le 12; $attempt++) {
    try {
      Invoke-SqlCommand -Query "SET NOCOUNT ON; SELECT 1;" | Out-Null
      return
    }
    catch {
      $lastError = $_
      if ($attempt -lt 12) {
        "Waiting for Azure SQL and Microsoft Entra administrator propagation (attempt $attempt of 12)..."
        Start-Sleep -Seconds 10
      }
    }
  }

  throw "Azure SQL did not become available for the configured Microsoft Entra administrator. $($lastError.Exception.Message)"
}

function Initialize-FunctionIdentity {
  if ([string]::IsNullOrWhiteSpace($FunctionAppName)) {
    return
  }

  $principalId = az functionapp identity show --resource-group $ResourceGroup --name $FunctionAppName --query principalId --output tsv
  Assert-LastExitCode -Step "Reading Function App managed identity"
  Require-Value -Value $principalId -Name "Function App managed identity principal ID" | Out-Null

  $clientId = az ad sp show --id $principalId --query appId --output tsv
  Assert-LastExitCode -Step "Reading Function App managed identity client ID"
  Require-Value -Value $clientId -Name "Function App managed identity client ID" | Out-Null

  $escapedUserName = $FunctionAppName.Replace("'", "''")
  $escapedClientId = $clientId.Replace("'", "''")
  $identitySql = @"
DECLARE @userName sysname = N'$escapedUserName';
DECLARE @clientId uniqueidentifier = CONVERT(uniqueidentifier, N'$escapedClientId');

IF EXISTS (
    SELECT 1
    FROM sys.database_principals
    WHERE name = @userName
      AND sid <> CONVERT(varbinary(16), @clientId)
)
BEGIN
    IF EXISTS (
        SELECT 1
        FROM sys.database_role_members membership
        INNER JOIN sys.database_principals role_principal ON role_principal.principal_id = membership.role_principal_id
        INNER JOIN sys.database_principals member_principal ON member_principal.principal_id = membership.member_principal_id
        WHERE role_principal.name = N'credit_memo_agent_runtime'
          AND member_principal.name = @userName
    )
    BEGIN
        DECLARE @removeMemberSql nvarchar(max) =
            N'ALTER ROLE [credit_memo_agent_runtime] DROP MEMBER ' + QUOTENAME(@userName) + N';';
        EXEC sp_executesql @removeMemberSql;
    END;

    DECLARE @dropUserSql nvarchar(max) = N'DROP USER ' + QUOTENAME(@userName) + N';';
    EXEC sp_executesql @dropUserSql;
END;

IF DATABASE_PRINCIPAL_ID(@userName) IS NULL
BEGIN
    DECLARE @createUserSql nvarchar(max) =
        N'CREATE USER ' + QUOTENAME(@userName) +
        N' WITH SID = ' + CONVERT(varchar(34), CONVERT(varbinary(16), @clientId), 1) +
        N', TYPE = E;';
    EXEC sp_executesql @createUserSql;
END;

IF NOT EXISTS (
    SELECT 1
    FROM sys.database_role_members membership
    INNER JOIN sys.database_principals role_principal ON role_principal.principal_id = membership.role_principal_id
    INNER JOIN sys.database_principals member_principal ON member_principal.principal_id = membership.member_principal_id
    WHERE role_principal.name = N'credit_memo_agent_runtime'
      AND member_principal.name = @userName
)
BEGIN
    DECLARE @addMemberSql nvarchar(max) =
        N'ALTER ROLE [credit_memo_agent_runtime] ADD MEMBER ' + QUOTENAME(@userName) + N';';
    EXEC sp_executesql @addMemberSql;
END;
"@
  Invoke-SqlCommand -Query $identitySql | Out-Null
}

function Apply-Migrations {
  if (-not (Test-Path -LiteralPath $migrationsPath)) {
    throw "Migration directory not found: $migrationsPath"
  }

  $migrationFiles = @(Get-ChildItem -LiteralPath $migrationsPath -Filter "*.sql" -File | Sort-Object Name)
  if ($migrationFiles.Count -eq 0) {
    throw "No SQL migrations were found in $migrationsPath"
  }

  foreach ($migration in $migrationFiles) {
    $migrationId = $migration.Name
    $escapedMigrationId = $migrationId.Replace("'", "''")

    if ($migrationId -eq "000_bootstrap.sql") {
      "Applying bootstrap migration: $migrationId"
      Invoke-SqlCommand -InputFile $migration.FullName | Out-Null
      $bootstrapRecordSql = "IF NOT EXISTS (SELECT 1 FROM dbo.schema_migrations WHERE migration_id = N'$escapedMigrationId') INSERT INTO dbo.schema_migrations (migration_id) VALUES (N'$escapedMigrationId');"
      Invoke-SqlCommand -Query $bootstrapRecordSql | Out-Null
      continue
    }

    $statusSql = "SET NOCOUNT ON; SELECT CASE WHEN EXISTS (SELECT 1 FROM dbo.schema_migrations WHERE migration_id = N'$escapedMigrationId') THEN 'APPLIED' ELSE 'PENDING' END;"
    $status = (Invoke-SqlCommand -Query $statusSql) -join [Environment]::NewLine
    if ($status -match "APPLIED") {
      "Migration already applied: $migrationId"
      continue
    }

    "Applying migration: $migrationId"
    Invoke-SqlCommand -InputFile $migration.FullName | Out-Null
    $recordSql = "IF NOT EXISTS (SELECT 1 FROM dbo.schema_migrations WHERE migration_id = N'$escapedMigrationId') INSERT INTO dbo.schema_migrations (migration_id) VALUES (N'$escapedMigrationId');"
    Invoke-SqlCommand -Query $recordSql | Out-Null
  }
}

function Apply-Seeds {
  if (-not (Test-Path -LiteralPath $seedManifestPath)) {
    throw "Seed manifest not found: $seedManifestPath"
  }

  $manifest = (Get-Content -LiteralPath $seedManifestPath -Raw) | ConvertFrom-Json
  $seeds = @($manifest.seeds)
  if ($seeds.Count -eq 0) {
    "No seed data is configured yet. Skipping seed deployment."
    return
  }

  foreach ($seed in $seeds) {
    if ([string]::IsNullOrWhiteSpace([string]$seed.id) -or [string]::IsNullOrWhiteSpace([string]$seed.file) -or [string]::IsNullOrWhiteSpace([string]$seed.script)) {
      throw "Every seed-manifest entry must define 'id', 'file', and 'script'."
    }

    $seedId = "seed:$($seed.id)"
    $escapedSeedId = $seedId.Replace("'", "''")
    $statusSql = "SET NOCOUNT ON; SELECT CASE WHEN EXISTS (SELECT 1 FROM dbo.schema_migrations WHERE migration_id = N'$escapedSeedId') THEN 'APPLIED' ELSE 'PENDING' END;"
    $status = (Invoke-SqlCommand -Query $statusSql) -join [Environment]::NewLine
    if ($status -match "APPLIED") {
      "Seed already applied: $($seed.id)"
      continue
    }

    $seedScriptPath = Join-Path $repoRoot ([string]$seed.script)
    if (-not (Test-Path -LiteralPath $seedScriptPath)) {
      throw "Seed loader not found: $seedScriptPath"
    }

    $seedFilePath = Join-Path $repoRoot ([string]$seed.file)
    if (-not (Test-Path -LiteralPath $seedFilePath)) {
      throw "Seed data file not found: $seedFilePath"
    }

    "Applying seed: $($seed.id)"
    & $seedScriptPath -SqlServerName $script:SqlServerName -SqlDatabaseName $script:SqlDatabaseName -SeedFile $seedFilePath
    Assert-LastExitCode -Step "Applying seed '$($seed.id)'"
    $recordSql = "INSERT INTO dbo.schema_migrations (migration_id) VALUES (N'$escapedSeedId');"
    Invoke-SqlCommand -Query $recordSql | Out-Null
  }
}

Push-Location $repoRoot
try {
  "Using subscription: $SubscriptionId"
  az account set --subscription $SubscriptionId | Out-Null
  Assert-LastExitCode -Step "Setting Azure subscription"

  if ($SkipInfrastructure) {
    $script:SqlServerName = Require-Value -Value $SqlServerName -Name "SqlServerName"
    $script:SqlDatabaseName = Require-Value -Value $SqlDatabaseName -Name "SqlDatabaseName"
  }
  else {
    Resolve-SqlEntraAdministrator
    $temporarySqlAdministratorPassword = New-SqlAdministratorPassword
    $existingServerCount = az sql server list --resource-group $ResourceGroup --query "[?starts_with(name, 'cm-sql-')] | length(@)" --output tsv
    Assert-LastExitCode -Step "Checking for an existing Azure SQL server"
    $includeBootstrapSqlAdministrator = ([int]$existingServerCount -eq 0)
    "Ensuring Azure SQL infrastructure..."
    $deploymentName = "cm-sql-{0}" -f (Get-Date -Format "yyyyMMddHHmmss")
    $outputsJson = az deployment group create `
      --name $deploymentName `
      --resource-group $ResourceGroup `
      --template-file (Join-Path $repoRoot "infra/modules/sql.bicep") `
      --parameters "location=$Location" "environmentName=$EnvironmentName" "entraAdministratorObjectId=$SqlEntraAdministratorObjectId" "entraAdministratorLogin=$SqlEntraAdministratorLogin" "entraAdministratorTenantId=$SqlEntraAdministratorTenantId" "sqlAdministratorPassword=$temporarySqlAdministratorPassword" "includeBootstrapSqlAdministrator=$includeBootstrapSqlAdministrator" `
      --query "properties.outputs" `
      --output json
    Assert-LastExitCode -Step "Deploying Azure SQL infrastructure"
    $outputs = $outputsJson | ConvertFrom-Json
    $script:SqlServerName = Get-OutputValue -Outputs $outputs -Name "sqlServerName"
    $script:SqlDatabaseName = Get-OutputValue -Outputs $outputs -Name "sqlDatabaseName"
  }

  Ensure-DeploymentFirewallRule
  Wait-ForSqlReady
  Apply-Migrations
  Initialize-FunctionIdentity
  Apply-Seeds

  if (-not [string]::IsNullOrWhiteSpace($FunctionAppName)) {
    "Configuring Azure Functions SQL settings..."
    az functionapp config appsettings set --resource-group $ResourceGroup --name $FunctionAppName --settings `
      "SQL_SERVER=$script:SqlServerName.database.windows.net" `
      "SQL_DATABASE=$script:SqlDatabaseName" `
      "SQL_ODBC_DRIVER=ODBC Driver 18 for SQL Server" `
      "SQL_AUTHENTICATION=managed_identity" | Out-Null
    Assert-LastExitCode -Step "Configuring Function App SQL settings"
  }

  "Azure SQL deployment complete"
  "SQL Server: $script:SqlServerName.database.windows.net"
  "Database: $script:SqlDatabaseName"
}
finally {
  Pop-Location
}
