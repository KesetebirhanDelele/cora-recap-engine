# spec/05_eval_plan.md

1. Completed inbound call with consent YES -> task created, summary written to GHL.
2. Completed inbound call with consent NO -> task created, no summary writeback.
3. Completed outbound new-lead call -> one task, CRM note write, summary gate runs.
4. Completed outbound cold-lead call -> one task, CRM enrichment write, summary gate runs.
5. Cold Lead voicemail first hit -> wait 2 hours then Synthflow callback.
6. Cold Lead second tier -> wait 2 days then Synthflow callback.
7. Cold Lead third tier -> wait 2 days then Synthflow callback.
8. Final stop -> campaign value becomes 3, terminal writes execute, no callback.
9. Duplicate replay -> no duplicate task or summary writeback.
10. SQL Server unavailable -> exception created, no unsafe progression.
11. GHL API key invalid -> immediate critical exception.
12. Blank transcript -> blank summary, no summary writeback.
13. New Lead and Cold Lead campaigns both use canonical states `None,0,1,2,3` while applying different policy maps.
14. Google Sheets shadow mode active -> sheet data mirrors into SQL Server without affecting production routing.

Regression checklist:
- [ ] Task rule still applies to every completed non-voicemail call.
- [ ] Consent gate still blocks summary writeback on NO.
- [ ] Cold Lead timing remains 2h / 2d / 2d.
- [ ] SQL Server remains authoritative.
- [ ] Redis/RQ remains execution rail.
- [ ] Canonical tier numbering remains unified across campaigns.
- [ ] Google Sheets remains mirror-only during shadowing.

