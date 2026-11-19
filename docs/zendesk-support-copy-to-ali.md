# Zendesk: copies of support@colaberry.com sent to Ali

**Set up:** 2026-11-17, by Kes in the Zendesk admin screens (colaberry.zendesk.com).
**Not part of Cora.** Nothing in this repo reads or writes Zendesk. This page exists so the setup can be repeated or repaired.

## What it does

Every email sent to support@colaberry.com, and every public reply an agent sends back, is also emailed to ali@colaberry.com.

| Copy | Subject Ali sees | Body |
|---|---|---|
| New ticket (incoming email) | `Support ticket <id>: <ticket title>` | Sender's email address and message |
| Agent reply sent out | `REPLY SENT - ticket <id>: <ticket title>` | Who it went to and the reply text |

The ticket id is the same in both, so one conversation can be followed end to end.

## Background

- support@colaberry.com is not in GHL. It forwards into a Zendesk help desk (brand "Colaberry Careers"). admissions@colaberry.com is the address wired into GHL.
- A Zendesk trigger's "Notify by > User email" can only pick people who already have a Zendesk login. Ali has none, so an email target is used instead. A target can send to any address.
- The subject of an email target is fixed on the target. The body is set in each trigger. That is why there are two targets.

## Configuration

### 1. Targets (Admin Center > Apps and integrations > Targets > Targets)

| Title | Type | Email address | Subject |
|---|---|---|---|
| Ali copy | Email target | ali@colaberry.com | `Support ticket {{ticket.id}}: {{ticket.title}}` |
| Ali copy - replies | Email target | ali@colaberry.com | `REPLY SENT - ticket {{ticket.id}}: {{ticket.title}}` |

Each was tested with the "Test target" option before saving. The test email says "Test message from Zendesk"; that proves delivery only, not the triggers.

### 2. Triggers (Admin Center > Objects and rules > Business rules > Triggers)

Both are in a trigger category called **Copy Ali**.

**Trigger A: "Copy to Ali - incoming"**
- Conditions (meet ALL): Ticket > Ticket > Is > Created
- Action: Notify by > Target > "Ali copy"
- Message: `{{ticket.requester.email}} and {{ticket.latest_comment_formatted}}`

**Trigger B: "Copy to Ali - replies sent"**
- Conditions (meet ALL):
  1. Ticket > Ticket > Is > Updated
  2. Ticket > Comment > Is > Public
  3. Ticket details > Current user > Is > (agent)
- Action: Notify by > Target > "Ali copy - replies"
- Message: `REPLY SENT to {{ticket.requester.email}}: {{ticket.latest_comment_formatted}}`

Why condition 3 matters: an earlier version used "Ticket > Assignee > Is > (current user)". That only fires when the ticket's own assignee replies, so replies by any other agent would be missed. "Current user is (agent)" fires for any agent and ignores the student's own replies.

**Trigger C: "Copy to Ali - lead/student replies"** (created by Kes 2026-11-19; Ali asked to verify receipt on his side)
- Third target first: Title "Ali copy - follow-ups", Email target, ali@colaberry.com, Subject `FOLLOW-UP from lead - ticket {{ticket.id}}: {{ticket.title}}`. Test target, then save.
- Conditions (meet ALL):
  1. Ticket > Ticket > Is > Updated
  2. Ticket > Comment > Is > Public
  3. Ticket details > Current user > Is not > (agent)
- Action: Notify by > Target > "Ali copy - follow-ups"
- Message: `FOLLOW-UP from {{ticket.requester.email}}: {{ticket.latest_comment_formatted}}`
- Put it in the Copy Ali category. Test: reply to a ticket from the outside address; Ali should receive "FOLLOW-UP from lead - ticket <id>: ...".
- Watch for: automatic replies (out-of-office) from a lead's mailbox will also be copied.

## How to check it works

1. Send an email to support@colaberry.com from any outside address. Ali should receive "Support ticket <id>: ..." with the real message text.
2. In Zendesk, reply to that ticket as an agent (public reply). Ali should receive "REPLY SENT - ticket <id>: ...".
3. Admin Center > Triggers: both triggers show as Active.

## Known limits

- Auto-replies and spam sent to support@ also create tickets, so Ali's copies include them.
- Copies (and every mail Zendesk sends from support@) show the sender name from the Zendesk brand. It was "Colaberry Careers"; Ali asked on 2026-11-19 for "Colaberry Support" because support@ carries job applications and general inquiries alike. Renamed on 2026-11-19 (see Status below).
- Internal notes are not copied (Comment must be Public).
- If a student replies, Zendesk records it as an update, but Trigger B ignores it because the current user is not an agent. Student follow-ups are therefore not copied; only new tickets are.
- Student follow-ups: see Trigger C below (added so Ali sees replies from the lead or student too).
- The two targets send to Ali only. They do not give him a Zendesk login. If he wants to search tickets himself, add him under Admin Center > People > Team members.

## Status

- 2026-11-17: delivery from both targets confirmed (test emails received).
- 2026-11-19: **end-to-end confirmed by Ali.** He received three incoming copies and two REPLY SENT copies with matching ticket numbers, including Kes's own test (ticket 20336914: inbound 10:21 PM, agent reply 11:45 PM Central). Ticket 20336915 (a job application) had no reply copy yet, which just means it was still open.
- 2026-11-19: **Trigger C and the target "Ali copy - follow-ups" created by Kes** so lead/student replies are copied too. Ali told by email what to verify (FOLLOW-UP arrives with the right ticket id, no duplicates, no copy of agent replies). Not confirmed by Ali yet.
- 2026-11-19: Ali's only requested change: rename the sender from "Colaberry Careers" to "Colaberry Support". A careers-specific identity, if wanted later, belongs on its own address. **Done 2026-11-19:** Kes renamed it in Zendesk and confirmed it worked; Ali was told by email. (Zendesk admin setting, nothing in this repo.)
