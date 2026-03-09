# spec/adr/ADR-0002-data-storage.md

## Context
Business decision changed authoritative DB from prior Postgres drafts to SQL Server.

## Decision
Use SQL Server as authoritative state and audit store. Use Redis only for job execution. Keep Google Sheets active during shadowing and mirror its data into SQL Server.

## Consequences
- consistent with enterprise DB preference
- requires SQL Server-specific migration and operational setup
- preserves durable canonical state independent of Redis
- supports safer cutover via mirror reconciliation

