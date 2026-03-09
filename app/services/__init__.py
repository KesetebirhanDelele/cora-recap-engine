"""
Services package — business logic orchestration.

Services implemented per phase:

  ai              (Phase 5)  — call analysis, summary generation, consent detection, VM content
  routing         (Phase 6+) — event routing: pending, call-through, voicemail
  tier_engine     (Phase 7)  — unified tier model None→0→1→2→3 with campaign policies
  dedupe          (Phase 6)  — idempotency key checking before irreversible actions
  exception_svc   (Phase 8)  — exception creation, state transitions, operator actions

Layer rule: services orchestrate — they do not call external APIs directly.
All external calls go through app.adapters.
"""
