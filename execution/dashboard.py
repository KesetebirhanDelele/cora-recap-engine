"""
Cora Recap Engine — Monitoring Dashboard

Read-only Streamlit dashboard. Queries Postgres directly using the same
DATABASE_URL that the API and worker use.

Usage:
    pip install streamlit
    streamlit run execution/dashboard.py

Requires the project .env to be loadable (DATABASE_URL must be set).
"""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure project root is on sys.path so app.* imports resolve
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

try:
    import streamlit as st
except ImportError:
    print("ERROR: streamlit is required. Install with: pip install streamlit")
    sys.exit(1)

import pandas as pd
from sqlalchemy import text

from app.config import get_settings
from app.db import get_sync_engine

# ── Page config ───────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Cora Recap Engine",
    page_icon="📞",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── DB helper ─────────────────────────────────────────────────────────────────

@st.cache_resource
def _engine():
    return get_sync_engine()


def _query(sql: str, params: dict | None = None) -> pd.DataFrame:
    try:
        with _engine().connect() as conn:
            return pd.read_sql_query(text(sql), conn, params=params or {})
    except Exception as exc:
        st.error(f"Query error: {exc}")
        return pd.DataFrame()


# ── Sidebar ───────────────────────────────────────────────────────────────────

settings = get_settings()
st.sidebar.title("Cora Recap Engine")
st.sidebar.markdown(f"**Env:** `{settings.app_env}`")
st.sidebar.markdown(
    f"**Shadow mode:** {'🟡 ON' if settings.shadow_mode_enabled else '🟢 OFF'}"
)
st.sidebar.markdown(
    f"**GHL writes:** `{settings.ghl_write_mode}`"
)
st.sidebar.divider()

section = st.sidebar.radio(
    "Section",
    [
        "Overview",
        "Campaign Overview",
        "Trends",
        "Recent Calls",
        "Lead State",
        "Shadow Actions",
        "Scheduled Jobs",
        "Exceptions",
        "Contact Drill-Down",
        "Lead Journey",
    ],
)

# ── Overview ──────────────────────────────────────────────────────────────────

if section == "Overview":
    st.title("Overview")

    col1, col2, col3, col4 = st.columns(4)

    calls_today = _query(
        "SELECT COUNT(*) AS n FROM call_events "
        "WHERE created_at >= NOW() - INTERVAL '24 hours'"
    )
    shadow_total = _query("SELECT COUNT(*) AS n FROM shadow_actions")
    exceptions_open = _query(
        "SELECT COUNT(*) AS n FROM exceptions WHERE status = 'open'"
    )
    jobs_failed = _query(
        "SELECT COUNT(*) AS n FROM scheduled_jobs "
        "WHERE status = 'failed' AND updated_at >= NOW() - INTERVAL '24 hours'"
    )

    col1.metric("Calls (24 h)", int(calls_today["n"].iloc[0]) if not calls_today.empty else 0)
    col2.metric("Shadow Actions", int(shadow_total["n"].iloc[0]) if not shadow_total.empty else 0)
    col3.metric("Open Exceptions", int(exceptions_open["n"].iloc[0]) if not exceptions_open.empty else 0)
    col4.metric("Failed Jobs (24 h)", int(jobs_failed["n"].iloc[0]) if not jobs_failed.empty else 0)

    st.divider()

    st.subheader("Jobs by Status (last 24 h)")
    job_counts = _query(
        "SELECT status, COUNT(*) AS count FROM scheduled_jobs "
        "WHERE created_at >= NOW() - INTERVAL '24 hours' "
        "GROUP BY status ORDER BY count DESC"
    )
    if not job_counts.empty:
        st.bar_chart(job_counts.set_index("status")["count"])

    st.subheader("Shadow Actions by Type")
    shadow_by_type = _query(
        "SELECT action_type, COUNT(*) AS count FROM shadow_actions "
        "GROUP BY action_type ORDER BY count DESC"
    )
    if not shadow_by_type.empty:
        st.bar_chart(shadow_by_type.set_index("action_type")["count"])
    else:
        st.info("No shadow actions recorded yet.")

# ── Campaign Overview ─────────────────────────────────────────────────────────

elif section == "Campaign Overview":
    st.subheader("Campaign Overview (Minimal)")

    import datetime as _dt

    # ── Date filter ───────────────────────────────────────────────────────────
    col_d1, col_d2 = st.columns(2)
    today     = _dt.date.today()
    date_from = col_d1.date_input("Next action from", value=today)
    date_to   = col_d2.date_input("Next action to",   value=today + _dt.timedelta(days=7))

    # ── Helpers ───────────────────────────────────────────────────────────────
    _JOB_LABEL = {
        "launch_outbound_call": "Call",
        "send_sms":             "SMS",
        "send_email":           "Email",
    }

    def _fmt_delay(ts) -> str:
        """Human-readable time until ts (tz-aware or naive treated as UTC)."""
        now = _dt.datetime.now(_dt.timezone.utc)
        if ts is None or (hasattr(ts, "tzinfo") and ts.tzinfo is None):
            ts = ts.replace(tzinfo=_dt.timezone.utc) if ts else None
        if ts is None:
            return "Now"
        secs = (ts - now).total_seconds()
        if secs <= 0:
            return "Now"
        if secs < 3600:
            return f"{int(secs // 60)}m"
        if secs < 86400:
            h, m = int(secs // 3600), int((secs % 3600) // 60)
            return f"{h}h {m}m" if m else f"{h}h"
        d, h = int(secs // 86400), int((secs % 86400) // 3600)
        return f"{d}d {h}h" if h else f"{d}d"

    # ── Single query — all lead_state rows ────────────────────────────────────
    # next_action_at  : nurture scheduler target (set on lead_state by intent handler)
    # sj.run_at       : earliest pending scheduled_job for this contact
    # effective_at    : whichever is sooner (preferring the concrete job)
    raw = _query(
        """
        SELECT
            ls.contact_id,
            COALESCE(ls.normalized_phone, ls.contact_id)  AS contact,
            ls.campaign_name,
            ls.status,
            ls.do_not_call,
            ls.invalid,
            ls.next_action_at,
            sj.job_type,
            sj.run_at                                      AS job_run_at,
            ce.last_call_at,
            CASE
                WHEN sj.run_at IS NOT NULL AND ls.next_action_at IS NOT NULL
                    THEN LEAST(sj.run_at, ls.next_action_at)
                ELSE COALESCE(sj.run_at, ls.next_action_at)
            END                                            AS effective_at
        FROM lead_state ls
        LEFT JOIN LATERAL (
            SELECT job_type, run_at
            FROM scheduled_jobs
            WHERE entity_id = ls.contact_id
              AND status    = 'pending'
            ORDER BY run_at ASC
            LIMIT 1
        ) sj ON true
        LEFT JOIN LATERAL (
            SELECT MAX(created_at) AS last_call_at
            FROM call_events
            WHERE contact_id = ls.contact_id
        ) ce ON true
        ORDER BY ce.last_call_at DESC NULLS LAST
        """,
    )

    if raw.empty:
        st.info("No leads found.")
        st.stop()

    # ── Apply date filter on effective_at (in Python — avoids NULL edge cases) ─
    def _to_utc(ts):
        if ts is None or (not hasattr(ts, "tzinfo")):
            return None
        if ts.tzinfo is None:
            return ts.replace(tzinfo=_dt.timezone.utc)
        return ts

    window_start = _dt.datetime.combine(date_from, _dt.time.min).replace(tzinfo=_dt.timezone.utc)
    window_end   = _dt.datetime.combine(date_to + _dt.timedelta(days=1), _dt.time.min).replace(tzinfo=_dt.timezone.utc)

    # ── Build display rows ────────────────────────────────────────────────────
    rows = []
    for _, r in raw.iterrows():
        status_val = (r["status"] or "").lower()
        dnc        = bool(r["do_not_call"])
        inv        = bool(r["invalid"])
        is_terminal = dnc or inv or status_val in ("enrolled", "closed")

        effective = _to_utc(r["effective_at"])

        # Apply date window:
        # - Terminal contacts: always shown
        # - Contacts with no effective_at but a recent call: always shown (unprocessed/orphaned)
        # - Active contacts with effective_at: shown only if within window
        has_recent_call = pd.notna(r["last_call_at"]) and r["last_call_at"] is not None
        if not is_terminal:
            if effective is None:
                if not has_recent_call:
                    continue                    # no call history and no action — skip
                # else: fall through and show with "Unscheduled" label
            elif not (window_start <= effective < window_end):
                continue                        # outside the selected window

        # ── Determine Next Action label ────────────────────────────────────
        if is_terminal:
            next_action = "—"
        else:
            job_type = r["job_type"] if pd.notna(r["job_type"]) else None
            job_run_at = _to_utc(r["job_run_at"]) if pd.notna(r.get("job_run_at")) else None
            naa        = _to_utc(r["next_action_at"]) if pd.notna(r["next_action_at"]) else None

            # Use concrete job if it's the sooner (or the only) source
            if job_run_at and (naa is None or job_run_at <= naa):
                label = _JOB_LABEL.get(job_type, job_type or "Scheduled")
                next_action = f"{label} in {_fmt_delay(job_run_at)}"
            elif naa:
                next_action = f"Follow-up in {_fmt_delay(naa)}"
            else:
                next_action = "⚠ Unscheduled"

        # ── Determine Status label ─────────────────────────────────────────
        if dnc:
            final_status = "Do Not Call"
        elif inv:
            final_status = "Invalid"
        elif status_val == "enrolled":
            final_status = "Enrolled"
        elif status_val == "closed":
            final_status = "Closed"
        else:
            final_status = "—"

        last_call = r["last_call_at"]
        if pd.notna(last_call) and last_call is not None:
            lc = _to_utc(last_call)
            last_call_str = lc.strftime("%Y-%m-%d %H:%M UTC") if lc else "—"
        else:
            last_call_str = "—"

        rows.append({
            "Contact":     r["contact"],
            "Campaign":    r["campaign_name"] or "—",
            "Last Call":   last_call_str,
            "Next Action": next_action,
            "Status":      final_status,
        })

    if not rows:
        st.info(f"No contacts with scheduled activity between {date_from} and {date_to}.")
        st.stop()

    df_out = pd.DataFrame(rows)
    st.caption(f"{len(df_out)} contact(s) — next action {date_from} → {date_to}")
    st.dataframe(df_out, use_container_width=True, hide_index=True)

# ── Trends ────────────────────────────────────────────────────────────────────

elif section == "Trends":
    st.title("Trends")

    from datetime import date, timedelta

    # ── Filters ───────────────────────────────────────────────────────────────
    col_f1, col_f2, col_f3 = st.columns([2, 2, 2])
    default_start = date.today() - timedelta(days=29)
    default_end = date.today()

    start_date = col_f1.date_input("From", value=default_start)
    end_date = col_f2.date_input("To", value=default_end)

    granularity = col_f3.selectbox("Bucket", ["Day", "Week", "Month"], index=0)
    trunc_map = {"Day": "day", "Week": "week", "Month": "month"}
    trunc = trunc_map[granularity]

    campaign_options = ["New Lead", "Cold Lead", "Inbound"]
    selected_campaigns = st.multiselect(
        "Campaigns",
        campaign_options,
        default=campaign_options,
    )

    if not selected_campaigns:
        st.info("Select at least one campaign.")
        st.stop()

    # ── Core trend query ──────────────────────────────────────────────────────
    #
    # campaign_bucket derivation:
    #   - call_events with direction='inbound'  → 'Inbound'
    #   - otherwise join lead_state.campaign_name (New Lead / Cold Lead)
    #
    # Metrics per bucket per time period:
    #   total_calls   — all call_events in range
    #   completed     — status = 'completed'
    #   goodbye       — end_call_reason ILIKE '%goodbye%'
    #   errors        — distinct contact_ids that have ≥1 exception in range
    #
    campaign_filter_sql = ", ".join(f"'{c}'" for c in selected_campaigns)

    trend_df = _query(
        f"""
        SELECT
            DATE_TRUNC('{trunc}', ce.created_at)::date          AS period,
            CASE
                WHEN ce.direction = 'inbound' THEN 'Inbound'
                ELSE COALESCE(ls.campaign_name, 'Unknown')
            END                                                   AS campaign,
            COUNT(*)                                              AS total_calls,
            COUNT(*) FILTER (WHERE ce.status = 'completed')      AS completed_calls,
            COUNT(*) FILTER (
                WHERE ce.end_call_reason ILIKE '%goodbye%'
            )                                                     AS goodbye_calls
        FROM call_events ce
        LEFT JOIN lead_state ls ON ls.contact_id = ce.contact_id
        WHERE ce.created_at >= :start_ts
          AND ce.created_at <  :end_ts
          AND (
              CASE
                  WHEN ce.direction = 'inbound' THEN 'Inbound'
                  ELSE COALESCE(ls.campaign_name, 'Unknown')
              END
          ) IN ({campaign_filter_sql})
        GROUP BY period, campaign
        ORDER BY period ASC, campaign
        """,
        {
            "start_ts": str(start_date),
            "end_ts": str(end_date + timedelta(days=1)),
        },
    )

    if trend_df.empty:
        st.info("No call data in the selected date range.")
        st.stop()

    # Compute percentage columns
    trend_df["pct_completed"] = (
        trend_df["completed_calls"] / trend_df["total_calls"].replace(0, float("nan")) * 100
    ).round(1)
    trend_df["pct_goodbye"] = (
        trend_df["goodbye_calls"] / trend_df["total_calls"].replace(0, float("nan")) * 100
    ).round(1)

    # ── Summary table ─────────────────────────────────────────────────────────
    st.subheader("Summary by Campaign")
    summary = (
        trend_df.groupby("campaign")
        .agg(
            Total_Calls=("total_calls", "sum"),
            Completed=("completed_calls", "sum"),
            Goodbye=("goodbye_calls", "sum"),
        )
        .assign(
            **{
                "% Completed": lambda d: (d["Completed"] / d["Total_Calls"].replace(0, float("nan")) * 100).round(1),
                "% Goodbye":   lambda d: (d["Goodbye"]   / d["Total_Calls"].replace(0, float("nan")) * 100).round(1),
            }
        )
        .reset_index()
    )
    st.dataframe(summary, use_container_width=True)

    st.divider()

    # ── Per-campaign trend charts ──────────────────────────────────────────────
    for campaign in selected_campaigns:
        camp_df = trend_df[trend_df["campaign"] == campaign].copy()
        if camp_df.empty:
            continue

        camp_df = camp_df.set_index("period")

        st.subheader(f"{campaign}")

        c1, c2, c3 = st.columns(3)

        with c1:
            st.markdown("**Total Calls per Period**")
            st.bar_chart(camp_df[["total_calls"]])

        with c2:
            st.markdown("**% Completed Call**")
            st.line_chart(camp_df[["pct_completed"]])

        with c3:
            st.markdown("**% Goodbye**")
            st.line_chart(camp_df[["pct_goodbye"]])

        with st.expander(f"Raw data — {campaign}"):
            st.dataframe(
                camp_df[
                    ["total_calls", "completed_calls", "pct_completed",
                     "goodbye_calls", "pct_goodbye"]
                ].rename(columns={
                    "total_calls":     "Total",
                    "completed_calls": "Completed",
                    "pct_completed":   "% Completed",
                    "goodbye_calls":   "Goodbye",
                    "pct_goodbye":     "% Goodbye",
                }),
                use_container_width=True,
            )

        st.divider()

# ── Recent Calls ──────────────────────────────────────────────────────────────

elif section == "Recent Calls":
    st.title("Recent Calls")

    limit = st.slider("Rows", 10, 200, 50)
    df = _query(
        f"""
        SELECT
            COALESCE(ls.normalized_phone, ce.contact_id)          AS contact,
            ce.call_id,
            ce.status,
            ce.duration_seconds,
            -- Campaign: lead_state value, fall back to call direction when NULL
            COALESCE(
                ls.campaign_name,
                CASE
                    WHEN ce.direction = 'inbound'  THEN 'Inbound'
                    WHEN ce.direction = 'outbound' THEN 'Outbound'
                    ELSE '—'
                END
            )                                                      AS campaign,
            -- detected_intent written at processing time by ai_jobs / voicemail_jobs
            ce.detected_intent,
            -- lead status used as fallback qualifier for terminal outcomes
            ls.status                                              AS lead_status,
            LEFT(ce.transcript, 120)                               AS transcript_preview,
            ce.created_at
        FROM call_events ce
        LEFT JOIN lead_state ls ON ls.contact_id = ce.contact_id
        ORDER BY ce.created_at DESC
        LIMIT {limit}
        """
    )
    if not df.empty:
        # Build human-readable "Outcome" column: "{status} · {intent qualifier}"
        # Intent qualifier is only shown for completed (answered) calls.
        def _outcome(row) -> str:
            status  = (row.get("status") or "").lower()
            intent  = (row.get("detected_intent") or "").strip()
            lead_st = (row.get("lead_status") or "").lower()

            if status != "completed":
                return status

            # Primary qualifier: detected_intent stored on the call_event row.
            # Fallback: lead_state.status for terminal outcomes (enrolled, closed,
            # human_transfer, cold) where no follow-up call is scheduled.
            qualifier = ""
            if intent:
                qualifier = intent.replace("_", " ")
            elif lead_st in ("enrolled", "closed", "human_transfer", "cold"):
                qualifier = lead_st.replace("_", " ")

            return f"{status} · {qualifier}" if qualifier else status

        df["outcome"] = df.apply(_outcome, axis=1)

        display_df = df[["contact", "call_id", "campaign", "outcome",
                          "duration_seconds", "transcript_preview", "created_at"]]
        display_df = display_df.rename(columns={
            "call_id":            "Call ID",
            "campaign":           "Campaign",
            "outcome":            "Outcome",
            "duration_seconds":   "Duration (s)",
            "transcript_preview": "Transcript",
            "created_at":         "Time",
        })
        st.dataframe(display_df, use_container_width=True)
    else:
        st.info("No call events found.")

# ── Lead State ────────────────────────────────────────────────────────────────

elif section == "Lead State":
    st.title("Lead State")

    status_filter = st.multiselect(
        "Filter by status",
        ["active", "nurture", "enrolled", "closed"],
        default=[],
    )
    campaign_filter = st.multiselect(
        "Filter by campaign",
        ["New Lead", "Cold Lead"],
        default=[],
    )

    where_clauses = []
    if status_filter:
        statuses = ", ".join(f"'{s}'" for s in status_filter)
        where_clauses.append(f"status IN ({statuses})")
    if campaign_filter:
        campaigns = ", ".join(f"'{c}'" for c in campaign_filter)
        where_clauses.append(f"campaign_name IN ({campaigns})")
    where = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""

    df = _query(
        f"""
        SELECT contact_id, campaign_name, ai_campaign_value, status,
               do_not_call, next_action_at, updated_at
        FROM lead_state
        {where}
        ORDER BY updated_at DESC
        LIMIT 200
        """
    )
    if not df.empty:
        st.dataframe(df, use_container_width=True)
    else:
        st.info("No leads found.")

# ── Shadow Actions ────────────────────────────────────────────────────────────

elif section == "Shadow Actions":
    st.title("Shadow Actions")
    if not settings.shadow_mode_enabled:
        st.warning("Shadow mode is currently OFF. This table will be empty.")

    action_filter = st.selectbox(
        "Action type", ["all", "outbound_call", "sms", "email"], index=0
    )
    limit = st.slider("Rows", 10, 500, 100)

    where = "" if action_filter == "all" else f"WHERE action_type = '{action_filter}'"
    df = _query(
        f"""
        SELECT contact_id, action_type, payload, created_at
        FROM shadow_actions
        {where}
        ORDER BY created_at DESC
        LIMIT {limit}
        """
    )
    if not df.empty:
        st.dataframe(df, use_container_width=True)
    else:
        st.info("No shadow actions recorded.")

# ── Scheduled Jobs ────────────────────────────────────────────────────────────

elif section == "Scheduled Jobs":
    st.title("Scheduled Jobs")

    status_filter = st.multiselect(
        "Status",
        ["pending", "claimed", "running", "completed", "failed", "cancelled"],
        default=["pending", "running", "failed"],
    )
    job_type_filter = st.text_input("Job type contains (optional)", "")
    limit = st.slider("Rows", 10, 500, 100)

    where_clauses = []
    if status_filter:
        statuses = ", ".join(f"'{s}'" for s in status_filter)
        where_clauses.append(f"status IN ({statuses})")
    if job_type_filter.strip():
        where_clauses.append(f"job_type ILIKE '%{job_type_filter.strip()}%'")
    where = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""

    df = _query(
        f"""
        SELECT job_type, status, run_at,
               payload_json->>'contact_id' AS contact_id,
               payload_json->>'campaign_name' AS campaign,
               created_at, updated_at
        FROM scheduled_jobs
        {where}
        ORDER BY run_at ASC
        LIMIT {limit}
        """
    )
    if not df.empty:
        st.dataframe(df, use_container_width=True)
    else:
        st.info("No jobs match the current filters.")

# ── Exceptions ────────────────────────────────────────────────────────────────

elif section == "Exceptions":
    st.title("Exceptions")

    severity_filter = st.multiselect(
        "Severity",
        ["critical", "warning", "info"],
        default=["critical", "warning"],
    )
    status_filter = st.selectbox("Status", ["open", "resolved", "ignored", "all"], index=0)
    limit = st.slider("Rows", 10, 200, 50)

    where_clauses = []
    if severity_filter:
        sevs = ", ".join(f"'{s}'" for s in severity_filter)
        where_clauses.append(f"severity IN ({sevs})")
    if status_filter != "all":
        where_clauses.append(f"status = '{status_filter}'")
    where = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""

    df = _query(
        f"""
        SELECT type, severity, status, entity_type, entity_id,
               context_json, created_at
        FROM exceptions
        {where}
        ORDER BY severity DESC, created_at DESC
        LIMIT {limit}
        """
    )
    if not df.empty:
        st.dataframe(df, use_container_width=True)
    else:
        st.success("No exceptions matching the filters.")

# ── Contact Drill-Down ────────────────────────────────────────────────────────

elif section == "Contact Drill-Down":
    st.title("Contact Drill-Down")

    contact_id = st.text_input("Contact ID", placeholder="e.g. sim-sc1-001 or +15551110001")

    if not contact_id.strip():
        st.info("Enter a contact_id above to inspect all data for that contact.")
        st.stop()

    cid = contact_id.strip()

    st.subheader("Lead State")
    lead_df = _query(
        "SELECT contact_id, campaign_name, ai_campaign_value, status, "
        "do_not_call, next_action_at, version, updated_at "
        "FROM lead_state WHERE contact_id = :cid",
        {"cid": cid},
    )
    if not lead_df.empty:
        st.dataframe(lead_df, use_container_width=True)
    else:
        st.warning("No lead_state row found.")

    st.subheader("Call Events")
    calls_df = _query(
        "SELECT call_id, status, duration_seconds, "
        "LEFT(transcript, 120) AS transcript_preview, created_at "
        "FROM call_events WHERE contact_id = :cid ORDER BY created_at DESC",
        {"cid": cid},
    )
    if not calls_df.empty:
        st.dataframe(calls_df, use_container_width=True)
    else:
        st.info("No call events.")

    st.subheader("Shadow Actions")
    shadow_df = _query(
        "SELECT action_type, payload, created_at "
        "FROM shadow_actions WHERE contact_id = :cid ORDER BY created_at DESC",
        {"cid": cid},
    )
    if not shadow_df.empty:
        st.dataframe(shadow_df, use_container_width=True)
    else:
        st.info("No shadow actions.")

    st.subheader("Scheduled Jobs")
    jobs_df = _query(
        "SELECT job_type, status, run_at, payload_json, created_at "
        "FROM scheduled_jobs "
        "WHERE payload_json->>'contact_id' = :cid "
        "ORDER BY created_at DESC LIMIT 50",
        {"cid": cid},
    )
    if not jobs_df.empty:
        st.dataframe(jobs_df, use_container_width=True)
    else:
        st.info("No scheduled jobs.")

    st.subheader("Outbound Messages")
    outbound_df = _query(
        "SELECT channel, status, LEFT(body, 100) AS body_preview, created_at "
        "FROM outbound_messages WHERE contact_id = :cid ORDER BY created_at DESC",
        {"cid": cid},
    )
    if not outbound_df.empty:
        st.dataframe(outbound_df, use_container_width=True)
    else:
        st.info("No outbound messages.")

    st.subheader("Exceptions")
    exc_df = _query(
        "SELECT type, severity, status, context_json, created_at "
        "FROM exceptions WHERE entity_id = :cid ORDER BY created_at DESC",
        {"cid": cid},
    )
    if not exc_df.empty:
        st.dataframe(exc_df, use_container_width=True)
    else:
        st.info("No exceptions.")

# ── Lead Journey ──────────────────────────────────────────────────────────────

elif section == "Lead Journey":
    import datetime as _dt
    import re as _re

    st.title("Lead Journey")
    st.caption(
        "Full chronological history of every touchpoint Cora had with a lead — "
        "calls, messages, campaign switches, and the next scheduled contact."
    )

    # ── Phone number input ────────────────────────────────────────────────────
    raw_phone = st.text_input(
        "Phone number",
        placeholder="+13137029103",
        help="Enter in E.164 format (+1XXXXXXXXXX) or digits only — normalised automatically.",
    )

    if not raw_phone.strip():
        st.info("Enter a phone number to view this lead's journey.")
        st.stop()

    # Normalise: strip non-digits, prepend + if missing
    digits = _re.sub(r"\D", "", raw_phone.strip())
    normalised_phone = f"+{digits}" if not raw_phone.strip().startswith("+") else f"+{digits}"

    # ── Resolve lead_state by phone (two-pass lookup) ────────────────────────
    # Pass 1: normalized_phone column (fast — covers voicemail-first leads and
    #         leads created after the lifecycle_jobs fix that sets normalized_phone).
    lead_row = _query(
        """
        SELECT contact_id, normalized_phone, campaign_name, status,
               do_not_call, invalid, next_action_at, ai_campaign_value,
               preferred_channel, last_replied_at, created_at, updated_at
        FROM lead_state
        WHERE normalized_phone = :phone
        LIMIT 1
        """,
        {"phone": normalised_phone},
    )

    # Pass 2: fall back to call_events payload when normalized_phone is NULL
    # (leads whose first contact was a completed call; update_lead_state previously
    # created the lead_state row without setting normalized_phone).
    if lead_row.empty:
        fallback = _query(
            """
            SELECT ls.contact_id, ls.normalized_phone, ls.campaign_name, ls.status,
                   ls.do_not_call, ls.invalid, ls.next_action_at, ls.ai_campaign_value,
                   ls.preferred_channel, ls.last_replied_at, ls.created_at, ls.updated_at
            FROM call_events ce
            JOIN lead_state ls ON ls.contact_id = ce.contact_id
            WHERE ce.raw_payload_json->>'phone_number_to' = :phone
               OR ce.raw_payload_json->>'phone_number'    = :phone
               OR ce.raw_payload_json->>'phone'           = :phone
            ORDER BY ce.created_at DESC
            LIMIT 1
            """,
            {"phone": normalised_phone},
        )
        if not fallback.empty:
            lead_row = fallback

    if lead_row.empty:
        st.warning(
            f"No lead found for **{normalised_phone}**. "
            "Check the number or try the Contact Drill-Down section."
        )
        st.stop()

    lead = lead_row.iloc[0]
    cid  = lead["contact_id"]

    # ── Summary card ──────────────────────────────────────────────────────────
    st.subheader("Lead Summary")

    def _fmt_ts(ts) -> str:
        if ts is None or (hasattr(ts, "__class__") and ts.__class__.__name__ == "NaTType"):
            return "—"
        if hasattr(ts, "tzinfo") and ts.tzinfo is None:
            ts = ts.replace(tzinfo=_dt.timezone.utc)
        return ts.strftime("%Y-%m-%d %H:%M UTC")

    def _delay_label(ts) -> str:
        if ts is None:
            return "—"
        now = _dt.datetime.now(_dt.timezone.utc)
        if hasattr(ts, "tzinfo") and ts.tzinfo is None:
            ts = ts.replace(tzinfo=_dt.timezone.utc)
        secs = (ts - now).total_seconds()
        if secs <= 0:
            return "now"
        if secs < 3600:
            return f"in {int(secs // 60)}m"
        if secs < 86400:
            h, m = int(secs // 3600), int((secs % 3600) // 60)
            return f"in {h}h {m}m" if m else f"in {h}h"
        d, h = int(secs // 86400), int((secs % 86400) // 3600)
        return f"in {d}d {h}h" if h else f"in {d}d"

    _STATUS_ICON = {
        "active":  "🟢",
        "nurture": "🟡",
        "closed":  "🔴",
        "human_transfer": "🔵",
    }

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Campaign",  lead["campaign_name"] or "—")
    col2.metric("Status",    f"{_STATUS_ICON.get(lead['status'] or '', '⚪')} {lead['status'] or '—'}")
    col3.metric("VM Tier",   lead["ai_campaign_value"] if lead["ai_campaign_value"] is not None else "none")
    col4.metric("DNC",       "Yes ⛔" if lead["do_not_call"] else "No")

    col5, col6 = st.columns(2)
    col5.metric("Contact ID", cid)
    col6.metric("Pref. Channel", lead["preferred_channel"] or "voice (default)")

    # Next pending job
    next_job = _query(
        """
        SELECT job_type, run_at, payload_json->>'campaign_name' AS campaign
        FROM scheduled_jobs
        WHERE entity_id = :cid AND status = 'pending'
        ORDER BY run_at ASC
        LIMIT 1
        """,
        {"cid": cid},
    )

    _JOB_ICON = {
        "launch_outbound_call": "📞",
        "send_sms":  "💬",
        "send_email": "📧",
    }

    if not next_job.empty:
        nj = next_job.iloc[0]
        icon = _JOB_ICON.get(nj["job_type"], "⏰")
        label = {"launch_outbound_call": "Call", "send_sms": "SMS", "send_email": "Email"}.get(
            nj["job_type"], nj["job_type"]
        )
        st.info(
            f"**Next action:** {icon} {label} — "
            f"{_fmt_ts(nj['run_at'])} ({_delay_label(nj['run_at'])})"
        )
    else:
        st.info("**Next action:** none scheduled")

    st.divider()

    # ── Unified timeline query ─────────────────────────────────────────────────
    timeline_df = _query(
        """
        SELECT
            ce.created_at                                       AS ts,
            'call'                                              AS event_type,
            ce.status                                           AS call_status,
            ce.duration_seconds,
            ce.detected_intent                                  AS intent,
            COALESCE(
                ce.raw_payload_json->>'campaign_name',
                '—'
            )                                                   AS campaign,
            CASE
                WHEN ce.status IN ('completed')
                    THEN COALESCE(
                        LEFT(ce.transcript, 200),
                        '(no transcript)'
                    )
                ELSE NULL
            END                                                 AS detail,
            ce.recording_url
        FROM call_events ce
        WHERE ce.contact_id = :cid
           OR ce.raw_payload_json->>'phone_number_to' = :phone
           OR ce.raw_payload_json->>'phone_number'    = :phone
           OR ce.raw_payload_json->>'phone'           = :phone

        UNION ALL

        SELECT
            om.created_at                                       AS ts,
            'message'                                           AS event_type,
            om.channel                                          AS call_status,
            NULL                                                AS duration_seconds,
            NULL                                                AS intent,
            '—'                                                 AS campaign,
            LEFT(om.body, 200)                                  AS detail,
            NULL                                                AS recording_url
        FROM outbound_messages om
        WHERE om.contact_id = :cid

        UNION ALL

        SELECT
            al.created_at                                       AS ts,
            'campaign_switch'                                   AS event_type,
            NULL                                                AS call_status,
            NULL                                                AS duration_seconds,
            al.context_json->>'reason'                          AS intent,
            al.context_json->>'from'                            AS campaign,
            al.context_json->>'to'                              AS detail,
            NULL                                                AS recording_url
        FROM audit_log al
        WHERE al.entity_id = :cid
          AND al.action = 'campaign_switch'

        ORDER BY ts DESC
        """,
        {"cid": cid, "phone": normalised_phone},
    )

    # De-duplicate rows — the phone-based OR clauses can produce duplicate call
    # rows when contact_id == normalised_phone (phone-derived contact IDs).
    if not timeline_df.empty:
        timeline_df = timeline_df.drop_duplicates(subset=["ts", "event_type", "call_status"])

    st.subheader("Journey Timeline")

    if timeline_df.empty:
        st.info("No touchpoints recorded yet for this lead.")
        st.stop()

    # ── Render timeline ───────────────────────────────────────────────────────
    _CALL_STATUS_ICON = {
        "completed":            "📞 Answered",
        "voicemail":            "📵 Voicemail",
        "hangup_on_voicemail":  "📵 VM (hangup)",
        "left_voicemail":       "📵 VM (left)",
        "voicemail_detected":   "📵 VM (detected)",
        "machine_detected":     "📵 VM (machine)",
        "failed":               "❌ Failed",
        "in-progress":          "🔄 In progress",
        "queue":                "⏳ Queue",
        "sms":                  "💬 SMS",
        "email":                "📧 Email",
    }

    for _, row in timeline_df.iterrows():
        etype = row["event_type"]
        ts    = _fmt_ts(row["ts"])

        if etype == "campaign_switch":
            frm = row["campaign"] or "?"
            to  = row["detail"] or "?"
            reason = row["intent"] or "—"
            st.markdown(
                f"🔄 **Campaign switch** &nbsp; `{frm}` → `{to}` &nbsp;&nbsp; "
                f"reason: *{reason}* &nbsp;&nbsp; <small>{ts}</small>",
                unsafe_allow_html=True,
            )
            st.divider()
            continue

        if etype == "message":
            channel_icon = "💬 SMS" if row["call_status"] == "sms" else "📧 Email"
            with st.expander(f"{channel_icon} &nbsp; {ts}", expanded=False):
                st.text(row["detail"] or "(empty)")
            continue

        # etype == "call"
        status_label = _CALL_STATUS_ICON.get(row["call_status"] or "", f"📞 {row['call_status']}")
        duration = (
            f"{int(row['duration_seconds'] // 60)}m {int(row['duration_seconds'] % 60)}s"
            if row["duration_seconds"] is not None
            else "—"
        )
        intent   = row["intent"] or "—"
        campaign = row["campaign"] or "—"

        header = (
            f"{status_label} &nbsp; "
            f"Campaign: **{campaign}** &nbsp; "
            f"Intent: **{intent}** &nbsp; "
            f"Duration: {duration} &nbsp; "
            f"<small>{ts}</small>"
        )

        is_answered = (row["call_status"] or "") == "completed"
        with st.expander(header, expanded=False):
            if is_answered and row["detail"]:
                st.markdown("**Transcript preview:**")
                st.text(row["detail"])
                if row["recording_url"]:
                    st.markdown(f"[🎙 Recording]({row['recording_url']})")
            elif is_answered:
                st.info("No transcript recorded.")
            else:
                st.info("Voicemail — no transcript.")
