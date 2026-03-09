"""
Integration tests — may touch dev sandboxes, mock APIs, and test sheets.

Rules:
  - Never touch production systems.
  - Require explicit opt-in via INTEGRATION_TESTS=1 env var or CI label.
  - Never send real comms during tests.

Integration tests are added from Phase 4 onward.
"""
