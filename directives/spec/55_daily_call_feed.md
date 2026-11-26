# Spec 55: daily call feed for Ali (who was called, outcome, summary)

Status: ON HOLD, superseded 2026-11-25, not built. Ali declined call summaries and duration for now and asked for three optional push fields instead (reached, call_outcome, caller), added on his side first; the staff-call summary column in our database is a coaching score, not a lead summary, so it must not be sent. Kept for the name guardrails if a summary is ever wanted. Original status: DRAFT, not built. Written 2026-11-24. Decisions by Kes: identify people by GHL contact id only; serve over a protected link that is fetched from inside the Hetzner server (not stored in git); the spec stays in the repo for now and nothing is built until Kes says so; no personal names anywhere in the feed except Rose, Taiwo, Ali and Kes (see "Name guardrails").

## Problem
Ali wants a daily record of who was called, how it ended, and a short conversation summary, in a form his system can fetch and read. Today he only gets what Kes pastes into email (the weekly attempt-cap report is counts only, spec 54).

## Out of scope
- Phone numbers, names, email addresses, transcripts, recording links.
- Writing anything into GHL or Ali's system. This feed is read-only.
- Any file committed to a repository. Call records and summaries are personal data and must not enter git (CLAUDE.md, repository hygiene; see also the AI-inbox-Manager purge, 2026-11-24).

## Decisions and assumptions
1. **Identifier:** `ghl_contact_id` only. Ali already joins on it.
2. **Format:** JSON Lines (`application/x-ndjson`), one call per line, one document per day. A bad line cannot break the rest, and Ali's code can parse it without cleanup.
3. **Delivery:** a read-only HTTP GET. Not exposed through the public proxy. Reachable only from inside the Hetzner server (Docker network or localhost). ASSUMPTION, to confirm with Ali: his fetcher runs on the same server. If it runs elsewhere, this must be re-decided before building.
4. **Day:** Central Time calendar day. A day's file is final from 7:00 AM Central the next day (after the 6:37 AM nightly Explorer sync and its call judging).
5. **Both caller types** appear in one file: `source` = `cora_ai` (Synthflow calls, `call_events`) or `staff` (Rose, Taiwo and other staff, `staff_call_quality`).

## Fields (one JSON object per line)
| field | type | meaning |
|---|---|---|
| `date` | string | Central calendar day, `YYYY-MM-DD` |
| `source` | string | `cora_ai` or `staff` |
| `caller` | string | agent label (e.g. `ColdLead`, `NewLead`) for AI; for staff only `Rose` or `Taiwo`, any other staff member is written as `staff` |
| `ghl_contact_id` | string | the person |
| `call_time_utc` | string | ISO 8601, `Z` |
| `direction` | string | `outbound` or `inbound` |
| `duration_seconds` | int or null | |
| `connected` | bool | the call got past ringing |
| `person_answered` | bool or null | a live person answered; null = not judged yet |
| `outcome` | string | AI: call status plus detected intent (e.g. `completed`, `booked`); staff: `reached`, `voicemail`, `no_answer` |
| `summary` | string or null | short conversation summary; null when none exists |

Rules: no field may contain a phone number, email address or the person's name. Contact ids not matching the GHL id shape are dropped and counted. Rows are sorted by `call_time_utc` then `ghl_contact_id` so the file is stable. Calls flagged `report_excluded` are left out.

## Name guardrails (Kes, 2026-11-24)
The only personal names allowed anywhere in the feed are **Rose, Taiwo, Ali and Kes** (allow-list in settings, `call_feed_allowed_names`). Every other person's name, whether a lead, a student, another staff member or a third party, must not leave the server. Cora (the agent's own name) is not a personal name. Layers, applied to `summary` and `outcome` before a line is written, and checked again on the finished line:
1. **Known names removed:** the lead's own name from the call and contact records (every part of it, case-insensitive) becomes `[name]`. Names of staff other than Rose and Taiwo (the staff and assistants lists in Settings) also become `[name]`.
2. **Pattern strip:** phone-like and email-like strings are removed.
3. **Detector for the rest:** a capitalised word that is not at the start of a sentence, not in the allow-list and not in a fixed vocabulary of safe terms (product, course and company names, months, weekdays, place words) is treated as a possible name and replaced with `[name]`. A name at the start of a sentence is caught by a first-name list.
4. **Fail closed:** if a line still looks risky after layers 1 to 3 (for example more than two replacements, or a replaced word that could not be classified), `summary` is set to null and the line is counted in `X-Redacted`. A missing summary is acceptable; a leaked name is not.
5. **Source rule:** the summary prompt for new calls is told not to name anyone except the allow-list. This reduces the work of layers 1 to 4; it is never relied on alone.
6. **Final check:** a test-time and run-time scan of the whole response; one failure of the scan returns 500 and no data rather than a file with a name in it.

## Endpoint
- `GET /api/v1/call-feed/{YYYY-MM-DD}` on the dashboard API (port 8001), `Authorization: Bearer <CALL_FEED_TOKEN>`.
- **A new, dedicated, read-only token** (`CALL_FEED_TOKEN`), compared in constant time. Do NOT reuse `SECRET_KEY` (operator actions) or `CORA_SERVICE_TOKEN` (that is Ali's token for Cora calling his feed, not for him calling us).
- Response: 200 JSON Lines; 400 bad date; 401/403 bad token; 404 future date; an empty day returns 200 with an empty body. A header `X-Rows` carries the row count and `X-Dropped` the count dropped by the id check.
- Not added to the Caddyfile. Port 8001 must not be reachable from the internet (check the Hetzner firewall before go-live; Docker publishes it on the host by default).
- Settings: `call_feed_enabled` (default false), `CALL_FEED_TOKEN` in the server `.env`. Disabled or token unset: the endpoint returns 404.

## Acceptance criteria
1. A day with known fixtures returns exactly the expected lines, in order, with only the fields above.
2. No phone number, email or personal name appears in any line, except Rose, Taiwo, Ali and Kes (tested with seeded names, phones and emails in the source rows and in summaries, including a name at the start of a sentence, a possessive such as "Maria's", a lowercase name and a name inside a quoted phrase). Another staff member's name never appears.
3. Wrong or missing token returns 403 and no data; with the token unset the endpoint is 404.
4. Requesting the same day twice returns identical bytes once the day is final.
5. Calls near midnight Central land on the correct day (tests on both sides of 00:00 CT, including the daylight-saving change).
6. The endpoint is read-only: it writes nothing to the database or GHL.
7. Port 8001 is confirmed unreachable from outside the server before the flag is turned on.

## Constraints
- **Musts:** read-only; GHL id only; dedicated token; dedupe on `source` + call id so a retried ingest cannot create a duplicate line; apply every name guardrail above before a line leaves the server.
- **Must-nots:** no transcripts or recording links; nothing committed to git; no reuse of existing tokens.
- **Escalation triggers:** Ali's fetcher runs outside the server; summaries still contain names after sanitising; the source rows lack `contact_id` for a large share of calls (measure first).

## Open points for Kes
1. Summaries are free text written by a language model and can mention a name. DECIDED (Kes, 2026-11-24): keep them, behind the name guardrails above, with fail-closed redaction. No detector is perfect, so before Ali relies on the feed, read one week of real output (redaction counts and a sample of 50 lines) and record the result in PROGRESS.md.
2. Confirm with Ali: fetcher location, fetch time, and how far back he wants to read.
3. The 4 allowed names (Rose, Taiwo, Ali, Kes) are matched as whole words only. A lead who shares one of these first names would not be redacted; accept, or add a lead-name check against the allow-list (layer 1 already removes the lead's own name from the record, so this only matters when it appears in a different form).

## Break pattern
1. Query builder for one Central day (both sources) plus unit tests, no HTTP.
2. Sanitiser plus tests.
3. Endpoint, token check and settings, with tests.
4. Network check (port 8001) and a read-only trial on the server with the flag off.
5. Flag on after Kes's go-ahead; Ali fetches one day; compare counts against the weekly report.

## Tests (to write)
Happy path, empty day, near-midnight and daylight-saving days, mixed AI and staff rows, dropped bad ids, seeded personal data in summary, wrong token, token unset, repeat fetch identical. Name guardrails: allow-list names kept (Rose, Taiwo, Ali, Kes), other staff names redacted, caller written as `staff` for anyone else, lead name removed in every part and case, name at sentence start, possessive, lowercase name, name in quotes, a risky line returns a null summary and increments `X-Redacted`, a final-scan failure returns 500 with no body. Negative case: a summary with no names passes unchanged.
