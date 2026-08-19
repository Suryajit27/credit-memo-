SET NOCOUNT ON;

IF OBJECT_ID(N'dbo.loan_cases', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.loan_cases (
        case_id uniqueidentifier NOT NULL CONSTRAINT PK_loan_cases PRIMARY KEY,
        request_id nvarchar(100) NOT NULL CONSTRAINT UQ_loan_cases_request_id UNIQUE,
        loan_number nvarchar(50) NOT NULL CONSTRAINT UQ_loan_cases_loan_number UNIQUE,
        borrower_name nvarchar(200) NOT NULL,
        loan_stage nvarchar(80) NOT NULL,
        relationship_manager_name nvarchar(200) NOT NULL,
        portfolio_manager_name nvarchar(200) NOT NULL,
        next_credit_committee_date date NULL,
        updated_at datetime2(7) NOT NULL CONSTRAINT DF_loan_cases_updated_at DEFAULT SYSUTCDATETIME()
    );
END;

IF OBJECT_ID(N'dbo.relationship_profiles', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.relationship_profiles (
        case_id uniqueidentifier NOT NULL CONSTRAINT PK_relationship_profiles PRIMARY KEY,
        relationship_since date NOT NULL,
        operating_account_status nvarchar(80) NOT NULL,
        average_operating_balance decimal(19, 2) NULL,
        treasury_management_enabled bit NOT NULL,
        merchant_services_enabled bit NOT NULL,
        CONSTRAINT FK_relationship_profiles_loan_cases FOREIGN KEY (case_id) REFERENCES dbo.loan_cases(case_id)
    );
END;

IF OBJECT_ID(N'dbo.loan_monitoring', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.loan_monitoring (
        case_id uniqueidentifier NOT NULL CONSTRAINT PK_loan_monitoring PRIMARY KEY,
        internal_risk_grade nvarchar(80) NOT NULL,
        annual_review_due_date date NULL,
        latest_monitoring_date date NULL,
        monitoring_status nvarchar(80) NOT NULL,
        watchlist_status nvarchar(80) NOT NULL,
        CONSTRAINT FK_loan_monitoring_loan_cases FOREIGN KEY (case_id) REFERENCES dbo.loan_cases(case_id)
    );
END;

IF OBJECT_ID(N'dbo.credit_conditions', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.credit_conditions (
        condition_id int IDENTITY(1, 1) NOT NULL CONSTRAINT PK_credit_conditions PRIMARY KEY,
        case_id uniqueidentifier NOT NULL,
        condition_category nvarchar(100) NOT NULL,
        condition_description nvarchar(1000) NOT NULL,
        owner_name nvarchar(200) NOT NULL,
        condition_status nvarchar(80) NOT NULL,
        due_date date NULL,
        CONSTRAINT FK_credit_conditions_loan_cases FOREIGN KEY (case_id) REFERENCES dbo.loan_cases(case_id)
    );
END;

IF OBJECT_ID(N'dbo.collateral_controls', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.collateral_controls (
        control_id int IDENTITY(1, 1) NOT NULL CONSTRAINT PK_collateral_controls PRIMARY KEY,
        case_id uniqueidentifier NOT NULL,
        control_type nvarchar(100) NOT NULL,
        control_status nvarchar(80) NOT NULL,
        control_detail nvarchar(1000) NOT NULL,
        target_date date NULL,
        CONSTRAINT FK_collateral_controls_loan_cases FOREIGN KEY (case_id) REFERENCES dbo.loan_cases(case_id)
    );
END;
GO

CREATE OR ALTER PROCEDURE dbo.usp_get_agent_loan_context
    @request_id nvarchar(100)
AS
BEGIN
    SET NOCOUNT ON;

    SELECT (
        SELECT
            N'sql_server_current_operational_context' AS [source],
            900 AS citationId,
            JSON_QUERY((
                SELECT
                    c.loan_number AS loanNumber,
                    c.loan_stage AS loanStage,
                    c.relationship_manager_name AS relationshipManager,
                    c.portfolio_manager_name AS portfolioManager,
                    c.next_credit_committee_date AS nextCreditCommitteeDate
                FOR JSON PATH, WITHOUT_ARRAY_WRAPPER
            )) AS workflow,
            JSON_QUERY((
                SELECT
                    r.relationship_since AS relationshipSince,
                    r.operating_account_status AS operatingAccountStatus,
                    r.average_operating_balance AS averageOperatingBalance,
                    r.treasury_management_enabled AS treasuryManagementEnabled,
                    r.merchant_services_enabled AS merchantServicesEnabled
                FROM dbo.relationship_profiles r
                WHERE r.case_id = c.case_id
                FOR JSON PATH, WITHOUT_ARRAY_WRAPPER
            )) AS relationship,
            JSON_QUERY((
                SELECT
                    m.internal_risk_grade AS internalRiskGrade,
                    m.annual_review_due_date AS annualReviewDueDate,
                    m.latest_monitoring_date AS latestMonitoringDate,
                    m.monitoring_status AS monitoringStatus,
                    m.watchlist_status AS watchlistStatus
                FROM dbo.loan_monitoring m
                WHERE m.case_id = c.case_id
                FOR JSON PATH, WITHOUT_ARRAY_WRAPPER
            )) AS monitoring,
            JSON_QUERY((
                SELECT
                    cc.condition_category AS category,
                    cc.condition_description AS [description],
                    cc.owner_name AS owner,
                    cc.condition_status AS [status],
                    cc.due_date AS dueDate
                FROM dbo.credit_conditions cc
                WHERE cc.case_id = c.case_id
                ORDER BY cc.due_date, cc.condition_id
                FOR JSON PATH
            )) AS creditConditions,
            JSON_QUERY((
                SELECT
                    control.control_type AS controlType,
                    control.control_status AS [status],
                    control.control_detail AS detail,
                    control.target_date AS targetDate
                FROM dbo.collateral_controls control
                WHERE control.case_id = c.case_id
                ORDER BY control.target_date, control.control_id
                FOR JSON PATH
            )) AS collateralControls
        FROM dbo.loan_cases c
        WHERE c.request_id = @request_id
        FOR JSON PATH, WITHOUT_ARRAY_WRAPPER
    ) AS context_json;
END;
GO

GRANT EXECUTE ON OBJECT::dbo.usp_get_agent_loan_context TO credit_memo_agent_runtime;
