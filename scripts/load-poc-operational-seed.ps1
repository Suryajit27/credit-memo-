Param(
  [Parameter(Mandatory = $true)] [string] $SqlServerName,
  [Parameter(Mandatory = $true)] [string] $SqlDatabaseName,
  [Parameter(Mandatory = $true)] [string] $SeedFile
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $SeedFile)) {
  throw "Seed data file not found: $SeedFile"
}

$sqlcmd = Get-Command -Name "sqlcmd" -ErrorAction SilentlyContinue
if (-not $sqlcmd) {
  throw "sqlcmd is required to apply Azure SQL seed data."
}

$seed = (Get-Content -LiteralPath $SeedFile -Raw) | ConvertFrom-Json
foreach ($requiredProperty in @("caseId", "requestId", "loanNumber", "borrowerName", "workflow", "relationship", "monitoring", "conditions", "collateralControls")) {
  if ($null -eq $seed.$requiredProperty) {
    throw "Seed file is missing required property '$requiredProperty'."
  }
}

$seedJson = Get-Content -LiteralPath $SeedFile -Raw
$escapedSeedJson = $seedJson.Replace("'", "''")
$tempSqlPath = [System.IO.Path]::ChangeExtension([System.IO.Path]::GetTempFileName(), ".sql")

$upsertSql = @'
SET XACT_ABORT ON;
BEGIN TRANSACTION;

DECLARE @seed nvarchar(max) = N'__SEED_JSON__';
DECLARE @caseId uniqueidentifier = TRY_CONVERT(uniqueidentifier, JSON_VALUE(@seed, '$.caseId'));
DECLARE @requestId nvarchar(100) = JSON_VALUE(@seed, '$.requestId');

IF @caseId IS NULL OR @requestId IS NULL
    THROW 50000, 'Seed caseId and requestId are required.', 1;

MERGE dbo.loan_cases AS target
USING (
    SELECT
        @caseId AS case_id,
        @requestId AS request_id,
        JSON_VALUE(@seed, '$.loanNumber') AS loan_number,
        JSON_VALUE(@seed, '$.borrowerName') AS borrower_name,
        JSON_VALUE(@seed, '$.workflow.loanStage') AS loan_stage,
        JSON_VALUE(@seed, '$.workflow.relationshipManager') AS relationship_manager_name,
        JSON_VALUE(@seed, '$.workflow.portfolioManager') AS portfolio_manager_name,
        TRY_CONVERT(date, JSON_VALUE(@seed, '$.workflow.nextCreditCommitteeDate')) AS next_credit_committee_date
) AS source
ON target.request_id = source.request_id
WHEN MATCHED THEN UPDATE SET
    case_id = source.case_id,
    loan_number = source.loan_number,
    borrower_name = source.borrower_name,
    loan_stage = source.loan_stage,
    relationship_manager_name = source.relationship_manager_name,
    portfolio_manager_name = source.portfolio_manager_name,
    next_credit_committee_date = source.next_credit_committee_date,
    updated_at = SYSUTCDATETIME()
WHEN NOT MATCHED THEN INSERT (
    case_id, request_id, loan_number, borrower_name, loan_stage, relationship_manager_name, portfolio_manager_name, next_credit_committee_date
) VALUES (
    source.case_id, source.request_id, source.loan_number, source.borrower_name, source.loan_stage, source.relationship_manager_name, source.portfolio_manager_name, source.next_credit_committee_date
);

MERGE dbo.relationship_profiles AS target
USING (
    SELECT
        @caseId AS case_id,
        TRY_CONVERT(date, JSON_VALUE(@seed, '$.relationship.relationshipSince')) AS relationship_since,
        JSON_VALUE(@seed, '$.relationship.operatingAccountStatus') AS operating_account_status,
        TRY_CONVERT(decimal(19, 2), JSON_VALUE(@seed, '$.relationship.averageOperatingBalance')) AS average_operating_balance,
        TRY_CONVERT(bit, JSON_VALUE(@seed, '$.relationship.treasuryManagementEnabled')) AS treasury_management_enabled,
        TRY_CONVERT(bit, JSON_VALUE(@seed, '$.relationship.merchantServicesEnabled')) AS merchant_services_enabled
) AS source
ON target.case_id = source.case_id
WHEN MATCHED THEN UPDATE SET
    relationship_since = source.relationship_since,
    operating_account_status = source.operating_account_status,
    average_operating_balance = source.average_operating_balance,
    treasury_management_enabled = source.treasury_management_enabled,
    merchant_services_enabled = source.merchant_services_enabled
WHEN NOT MATCHED THEN INSERT (
    case_id, relationship_since, operating_account_status, average_operating_balance, treasury_management_enabled, merchant_services_enabled
) VALUES (
    source.case_id, source.relationship_since, source.operating_account_status, source.average_operating_balance, source.treasury_management_enabled, source.merchant_services_enabled
);

MERGE dbo.loan_monitoring AS target
USING (
    SELECT
        @caseId AS case_id,
        JSON_VALUE(@seed, '$.monitoring.internalRiskGrade') AS internal_risk_grade,
        TRY_CONVERT(date, JSON_VALUE(@seed, '$.monitoring.annualReviewDueDate')) AS annual_review_due_date,
        TRY_CONVERT(date, JSON_VALUE(@seed, '$.monitoring.latestMonitoringDate')) AS latest_monitoring_date,
        JSON_VALUE(@seed, '$.monitoring.monitoringStatus') AS monitoring_status,
        JSON_VALUE(@seed, '$.monitoring.watchlistStatus') AS watchlist_status
) AS source
ON target.case_id = source.case_id
WHEN MATCHED THEN UPDATE SET
    internal_risk_grade = source.internal_risk_grade,
    annual_review_due_date = source.annual_review_due_date,
    latest_monitoring_date = source.latest_monitoring_date,
    monitoring_status = source.monitoring_status,
    watchlist_status = source.watchlist_status
WHEN NOT MATCHED THEN INSERT (
    case_id, internal_risk_grade, annual_review_due_date, latest_monitoring_date, monitoring_status, watchlist_status
) VALUES (
    source.case_id, source.internal_risk_grade, source.annual_review_due_date, source.latest_monitoring_date, source.monitoring_status, source.watchlist_status
);

DELETE FROM dbo.credit_conditions WHERE case_id = @caseId;
INSERT INTO dbo.credit_conditions (case_id, condition_category, condition_description, owner_name, condition_status, due_date)
SELECT
    @caseId,
    condition_data.category,
    condition_data.[description],
    condition_data.owner,
    condition_data.[status],
    TRY_CONVERT(date, condition_data.dueDate)
FROM OPENJSON(@seed, '$.conditions') WITH (
    category nvarchar(100) '$.category',
    [description] nvarchar(1000) '$.description',
    owner nvarchar(200) '$.owner',
    [status] nvarchar(80) '$.status',
    dueDate nvarchar(30) '$.dueDate'
) AS condition_data;

DELETE FROM dbo.collateral_controls WHERE case_id = @caseId;
INSERT INTO dbo.collateral_controls (case_id, control_type, control_status, control_detail, target_date)
SELECT
    @caseId,
    control_data.controlType,
    control_data.[status],
    control_data.detail,
    TRY_CONVERT(date, control_data.targetDate)
FROM OPENJSON(@seed, '$.collateralControls') WITH (
    controlType nvarchar(100) '$.controlType',
    [status] nvarchar(80) '$.status',
    detail nvarchar(1000) '$.detail',
    targetDate nvarchar(30) '$.targetDate'
) AS control_data;

COMMIT TRANSACTION;
'@
$upsertSql = $upsertSql.Replace("__SEED_JSON__", $escapedSeedJson)

try {
  Set-Content -LiteralPath $tempSqlPath -Value $upsertSql -NoNewline -Encoding utf8
  & $sqlcmd.Source "-S" "$SqlServerName.database.windows.net" "-d" $SqlDatabaseName "-G" "-C" "-b" "-l" "30" "-i" $tempSqlPath
  if ($LASTEXITCODE -ne 0) {
    throw "Applying POC operational seed data failed with exit code $LASTEXITCODE"
  }
}
finally {
  if (Test-Path -LiteralPath $tempSqlPath) {
    Remove-Item -LiteralPath $tempSqlPath -Force
  }
}
