SET NOCOUNT ON;

IF SCHEMA_ID(N'reporting') IS NULL
BEGIN
    EXEC(N'CREATE SCHEMA reporting');
END;
GO

CREATE OR ALTER VIEW reporting.vw_loan_cases
AS
SELECT
    case_id,
    request_id,
    loan_number,
    borrower_name,
    loan_stage,
    relationship_manager_name,
    portfolio_manager_name,
    next_credit_committee_date,
    updated_at
FROM dbo.loan_cases;
GO

CREATE OR ALTER VIEW reporting.vw_relationship_profiles
AS
SELECT
    c.request_id,
    c.loan_number,
    c.borrower_name,
    c.relationship_manager_name,
    c.portfolio_manager_name,
    r.relationship_since,
    r.operating_account_status,
    r.average_operating_balance,
    r.treasury_management_enabled,
    r.merchant_services_enabled
FROM dbo.relationship_profiles r
INNER JOIN dbo.loan_cases c ON c.case_id = r.case_id;
GO

CREATE OR ALTER VIEW reporting.vw_loan_monitoring
AS
SELECT
    c.request_id,
    c.loan_number,
    c.borrower_name,
    c.relationship_manager_name,
    c.portfolio_manager_name,
    m.internal_risk_grade,
    m.annual_review_due_date,
    m.latest_monitoring_date,
    m.monitoring_status,
    m.watchlist_status
FROM dbo.loan_monitoring m
INNER JOIN dbo.loan_cases c ON c.case_id = m.case_id;
GO

CREATE OR ALTER VIEW reporting.vw_credit_conditions
AS
SELECT
    c.request_id,
    c.loan_number,
    c.borrower_name,
    c.relationship_manager_name,
    c.portfolio_manager_name,
    cc.condition_id,
    cc.condition_category,
    cc.condition_description,
    cc.owner_name,
    cc.condition_status,
    cc.due_date
FROM dbo.credit_conditions cc
INNER JOIN dbo.loan_cases c ON c.case_id = cc.case_id;
GO

CREATE OR ALTER VIEW reporting.vw_collateral_controls
AS
SELECT
    c.request_id,
    c.loan_number,
    c.borrower_name,
    c.relationship_manager_name,
    c.portfolio_manager_name,
    control.control_id,
    control.control_type,
    control.control_status,
    control.control_detail,
    control.target_date
FROM dbo.collateral_controls control
INNER JOIN dbo.loan_cases c ON c.case_id = control.case_id;
GO

GRANT SELECT ON SCHEMA::reporting TO credit_memo_agent_runtime;
