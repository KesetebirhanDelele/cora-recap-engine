# spec/adr/ADR-0001-architecture-choice.md

## Context
Replace multiple Zapier workflows with one Python platform.

## Decision
Use hybrid architecture: API + worker in one codebase.

## Consequences
- simpler deployment than microservices
- enough separation for scale
- straightforward dashboard and replay APIs

