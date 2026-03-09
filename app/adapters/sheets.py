"""
Google Sheets adapter — Phase 1 stub.

Mirror-only shadow mode: sheet data is read and ingested into Postgres.
Production routing must never depend on Sheets.
Implemented in Phase 9.

Stop conditions before implementation:
  - GOOGLE_SHEETS_CALL_LOG_ID must be set
  - GOOGLE_SHEETS_CAMPAIGN_DATA_ID must be set
  - Tab names (INBOUND, NEW_LEADS, COLD_LEADS) must be defined
  - GOOGLE_SERVICE_ACCOUNT_JSON must be available
  - shadow_sheet_rows SQL table must exist (Phase 3)
"""
from __future__ import annotations


class GoogleSheetsClient:
    """Google Sheets mirror client. Implemented in Phase 9."""

    def mirror_google_sheet_rows(self, sync_payload: dict) -> dict:
        raise NotImplementedError("Sheets adapter not yet implemented (Phase 9)")
