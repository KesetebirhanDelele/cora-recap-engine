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
        "Trends",
        "Recent Calls",
        "Lead State",
        "Shadow Actions",
        "Scheduled Jobs",
        "Exceptions",
        "Contact Drill-Down",
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
        WITH base AS (
            SELECT
                DATE_TRUNC('{trunc}', ce.created_at)::date          AS period,
                CASE
                    WHEN ce.direction = 'inbound' THEN 'Inbound'
                    ELSE COALESCE(ls.campaign_name, 'Unknown')
                END                                                   AS campaign,
                ce.id                                                 AS call_id,
                ce.contact_id,
                ce.status,
                ce.end_call_reason
            FROM call_events ce
            LEFT JOIN lead_state ls ON ls.contact_id = ce.contact_id
            WHERE ce.created_at >= :start_ts
              AND ce.created_at <  :end_ts
        ),
        errors AS (
            SELECT DISTINCT e.entity_id AS contact_id
            FROM exceptions e
            WHERE e.created_at >= :start_ts
              AND e.created_at <  :end_ts
        )
        SELECT
            b.period,
            b.campaign,
            COUNT(*)                                                              AS total_calls,
            COUNT(*) FILTER (WHERE b.status = 'completed')                       AS completed_calls,
            COUNT(*) FILTER (WHERE b.end_call_reason ILIKE '%goodbye%')          AS goodbye_calls,
            COUNT(DISTINCT b.contact_id)
                FILTER (WHERE e.contact_id IS NOT NULL)                          AS error_contacts
        FROM base b
        LEFT JOIN errors e ON e.contact_id = b.contact_id
        WHERE b.campaign IN ({campaign_filter_sql})
        GROUP BY b.period, b.campaign
        ORDER BY b.period ASC, b.campaign
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
            Errors=("error_contacts", "sum"),
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

        c1, c2 = st.columns(2)

        with c1:
            st.markdown("**Total Calls per Period**")
            st.bar_chart(camp_df[["total_calls"]])

        with c2:
            st.markdown("**Errors per Period**")
            st.bar_chart(camp_df[["error_contacts"]])

        c3, c4 = st.columns(2)

        with c3:
            st.markdown("**% Completed Call**")
            st.line_chart(camp_df[["pct_completed"]])

        with c4:
            st.markdown("**% Goodbye**")
            st.line_chart(camp_df[["pct_goodbye"]])

        with st.expander(f"Raw data — {campaign}"):
            st.dataframe(
                camp_df[
                    ["total_calls", "completed_calls", "pct_completed",
                     "goodbye_calls", "pct_goodbye", "error_contacts"]
                ].rename(columns={
                    "total_calls":     "Total",
                    "completed_calls": "Completed",
                    "pct_completed":   "% Completed",
                    "goodbye_calls":   "Goodbye",
                    "pct_goodbye":     "% Goodbye",
                    "error_contacts":  "Errors",
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
            ce.contact_id,
            ce.call_id,
            ce.status,
            ce.duration_seconds,
            LEFT(ce.transcript, 120) AS transcript_preview,
            ls.campaign_name,
            ce.created_at
        FROM call_events ce
        LEFT JOIN lead_state ls ON ls.contact_id = ce.contact_id
        ORDER BY ce.created_at DESC
        LIMIT {limit}
        """
    )
    if not df.empty:
        st.dataframe(df, use_container_width=True)
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
