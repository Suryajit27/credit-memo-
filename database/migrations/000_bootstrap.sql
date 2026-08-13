SET NOCOUNT ON;

IF OBJECT_ID(N'dbo.schema_migrations', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.schema_migrations (
        migration_id nvarchar(255) NOT NULL PRIMARY KEY,
        applied_at datetime2(7) NOT NULL CONSTRAINT DF_schema_migrations_applied_at DEFAULT SYSUTCDATETIME()
    );
END;

IF DATABASE_PRINCIPAL_ID(N'credit_memo_agent_runtime') IS NULL
BEGIN
    CREATE ROLE credit_memo_agent_runtime;
END;
