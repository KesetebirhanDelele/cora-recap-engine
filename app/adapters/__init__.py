"""
Adapters package — external service clients.

All adapters default to read-only / shadow mode until Phase 4+.
Write paths are feature-gated by mode flags in app.config.settings.

  ghl        — GHL / LeadConnector CRM (Phase 4)
  synthflow  — Synthflow callback scheduling (Phase 7)
  openai_client — OpenAI prompt execution (Phase 5)
  sheets     — Google Sheets shadow mirror (Phase 9)
"""
