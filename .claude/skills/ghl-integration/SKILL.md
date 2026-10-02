---
name: ghl-integration
description: Use when building or changing anything that talks to GoHighLevel (GHL / LeadConnector) - writing contact custom fields to trigger workflows (SMS/email), reading conversations or delivery status, DND/opt-outs, tokens and env vars, or planning GHL's Voice AI agent. Loads a project-neutral kit of verified API behaviour, env variables, safety standards and open research items.
---

# GHL integration

1. Read `KIT.md` in this folder before designing or coding. It is project-neutral: pattern, tokens and scopes, env variables, DND, delivery tracking, safety standards, Voice AI research list.
2. Non-negotiables: shadow mode by default and two switches for live; writes by custom-field UUID resolved from the location; the field write is the send, so gate it; a successful write means "handed to GHL", not delivered; opt-outs must be set as GHL DND and checked via DND, tags and the app's own flags before any call, text or email; read runtime config through a DB-first helper, not the settings object.
3. Business rules (cadence, timing, wording, caps, thresholds, campaign names) do not belong here. Put them in the project's own spec and runtime config.
4. Anything marked UNVERIFIED (notably Voice AI call start and results) must be confirmed against the live account or GHL docs and written into a spec before code.
5. Keep the project's `.env.example`, specs and tests in sync with any change; never send real messages from tests.
