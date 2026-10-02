---
name: ghl-integration
description: Use when building or changing anything that talks to GoHighLevel (GHL / LeadConnector) - writing contact custom fields to trigger workflows (SMS/email), reading conversations or delivery status, DND/opt-outs, tokens and env vars, or planning GHL's Voice AI agent instead of Synthflow. Loads the portable kit of verified API behaviour, env variables, safety standards and open research items.
---

# GHL integration

1. Read `KIT.md` in this folder before designing or coding. It is the standard: pattern, tokens/scopes, env variables, DND, delivery tracking, safety rules, Voice AI research list.
2. Non-negotiables: shadow mode by default; two switches for live; writes by custom-field UUID resolved from the location; the field write **is** the send (so gate it); success of a write is "handed to GHL", not delivered; opt-outs must be set as GHL DND **and** checked via tags/flags before every call, text or email; read config through the DB-first helper, not the settings object.
3. Anything marked **UNVERIFIED** (notably Voice AI call start/results) must be confirmed against the live account or GHL docs and written into a spec before code.
4. Keep `.env.example`, the spec in `directives/spec/`, and this kit in sync with any change; add tests; never send real messages from tests.
