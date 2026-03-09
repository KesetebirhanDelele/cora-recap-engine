# spec/09_security_privacy.md

- Sensitive education/customer data but not regulated health data.
- Encrypt Postgres data at rest.
- Restrict transcript, recording URL, and CRM note access.
- Redact direct contact identifiers in standard logs.
- Retain call metadata, transcripts, AI outputs, and audit records indefinitely.
- Store recording URL and metadata indefinitely; do not copy media unless separately required.
- Use API keys and secrets manager-backed configuration.

