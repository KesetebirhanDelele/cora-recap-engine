"""
Cora Recap Engine — application package.

Layer responsibilities:
  app.api        — FastAPI routes (webhook intake, dashboard APIs)
  app.worker     — RQ worker entrypoints and job definitions
  app.config     — settings loading and mode-flag resolution
  app.adapters   — external service clients (GHL, Synthflow, OpenAI, Sheets)
  app.models     — SQLAlchemy ORM models
  app.services   — business logic orchestration (routing, tier engine, consent gate)
"""
__version__ = "0.1.0"
