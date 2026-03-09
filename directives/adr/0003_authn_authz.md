# spec/adr/ADR-0003-authn-authz.md

## Context
App integrates with GHL, Synthflow, OpenAI, Redis, Postgres, and Google Sheets mirror.

## Decision
Use managed secrets, per-location GHL API keys, and role-based access for dashboard users.

## Consequences
- least-privilege access model
- easier rotation and isolation by environment/location

