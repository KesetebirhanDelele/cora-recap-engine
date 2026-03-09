# spec/02_acceptance_criteria.md

## Event ingestion and enrichment
- Given an inbound event contains a valid `call_id`, when the system receives it, then it enriches the call from the canonical call-analysis source and GHL and persists an idempotent event record.
- Given an inbound event lacks a resolvable call identity, when processing begins, then downstream work stops and an exception record becomes visible in the dashboard.
- Given a call has already been processed for a given `call_id` and `action_type`, when the same event is replayed, then the system does not create a second task, second summary writeback, or second tier advancement.

## Task creation
- Given a completed non-voicemail call, when the call-through path succeeds, then exactly one GHL task is created and the due date is blank.

## Student summary and consent
- Given a completed call with valid transcript content and consent detection returns `YES`, when recap processing finishes, then the generated student summary is written to the configured GHL recap field.
- Given a completed call with valid transcript content and consent detection returns `NO`, when recap processing finishes, then the system does not write the student summary into GHL.
- Given a completed call with blank or unusable transcript, when summary generation runs, then the returned summary is blank and no recap writeback occurs.

## Unified tier engine
- Given a Cold Lead with campaign value `None`, when voicemail path is entered, then the system updates campaign value to `0` and waits 2 hours before scheduling a Synthflow call.
- Given a Cold Lead with campaign value `0`, when the next voicemail path is entered, then the system updates campaign value to `1` and waits 2 days before scheduling a Synthflow call.
- Given a Cold Lead with campaign value `1`, when the next voicemail path is entered, then the system updates campaign value to `2` and waits 2 days before scheduling a Synthflow call.
- Given any campaign at campaign value `2`, when the next voicemail policy step is entered and policy defines finalization, then the system updates campaign value to `3`, executes finalization writes, and does not schedule another Synthflow call.
- Given a New Lead campaign and a Cold Lead campaign, when both enter voicemail handling, then both use the same canonical campaign values `None,0,1,2,3` but may apply different delay durations, actions, and finalization writes according to configured policy.

## Dashboard actions
- Given an exception is open in the dashboard, when an operator clicks Retry Now, then the system enqueues a new job attempt and records the operator action in audit history.
- Given a lead has scheduled future jobs, when an operator cancels future jobs, then those jobs are marked canceled in authoritative state and no callback runs from them.

