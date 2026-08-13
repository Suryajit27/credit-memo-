# Seed Data

The POC seed is deliberately complementary to the supplied underwriting documents. It contains internal relationship, workflow, monitoring, closing-condition, and collateral-control data for Meridian Foods LLC rather than copying submitted financial, collateral valuation, credit report, or AML facts.

To demonstrate combined SQL and RAG retrieval, upload and index the Meridian sample documents using request ID `sample-meridian-foods-2026`. That request ID is the SQL-to-RAG link for the seeded case.

Add future JSON or CSV data through `seed-manifest.json` with an idempotent loader script. The deployment runner records completed seed IDs in `dbo.schema_migrations`, so a revised dataset should use a new seed ID rather than modifying an already-applied seed.
