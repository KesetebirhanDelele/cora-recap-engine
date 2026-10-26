"use client";

import { Fragment, useEffect, useState } from "react";
import {
  Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import {
  fetchSignupIntake,
  type SignupAllTime, type SignupCounts, type SignupIntakeResponse,
} from "@/lib/api";

const CARD: React.CSSProperties = {
  background: "#ffffff", border: "1px solid #e2e8f0", borderRadius: 8, padding: "0.875rem 1rem",
  boxShadow: "0 1px 3px rgba(0,0,0,0.04)",
};
const COLORS = { explorer: "#6366f1", open_house: "#f59e0b", email: "#0ea5e9", phone: "#16a34a" };
const axis = { tick: { fontSize: 10, fill: "#94a3b8" }, tickLine: false, axisLine: false } as const;
const RANGES = [7, 14, 30, 90];

function ago(iso: string | null): string {
  if (!iso) return "not refreshed yet";
  const m = Math.floor((Date.now() - new Date(iso).getTime()) / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m} min ago`;
  const h = Math.floor(m / 60);
  return h < 48 ? `${h} h ago` : `${(h / 24).toFixed(1)} days ago`;
}

const label = (day: string) => day.slice(5);                       // MM-DD
const pct = (n: number, d: number) => (d ? `${Math.round((100 * n) / d)}%` : "-");

function Tile({ title, color, a }: { title: string; color: string; a: SignupAllTime }) {
  return (
    <div style={{ ...CARD, borderTop: `3px solid ${color}` }}>
      <div style={{ color: "#64748b", fontSize: "0.8rem", fontWeight: 700 }}>{title}</div>
      <div style={{ fontSize: "1.9rem", fontWeight: 800 }}>{a.signups}</div>
      <div style={{ fontSize: "0.85rem", display: "grid", gap: 2 }}>
        <span>with email: <strong>{a.with_email}</strong> ({a.email_pct}%)</span>
        <span>with phone: <strong>{a.with_phone}</strong> ({a.phone_pct}%)</span>
        <span>with both: <strong>{a.with_both}</strong></span>
        <span style={{ color: "#64748b" }}>campaign tag: {a.with_campaign} ({a.campaign_pct}%) · fb {a.utm_fb} · ig {a.utm_ig}</span>
      </div>
    </div>
  );
}

function Row({ day, seg, name }: { day: string; seg: SignupCounts; name: string }) {
  return (
    <tr style={{ borderTop: "1px solid #e2e8f0", color: seg.signups ? undefined : "#cbd5e1" }}>
      <td>{day}</td><td>{name}</td><td style={{ fontWeight: 700 }}>{seg.signups}</td>
      <td>{seg.with_email} <span style={{ color: "#94a3b8" }}>({pct(seg.with_email, seg.signups)})</span></td>
      <td>{seg.with_phone} <span style={{ color: "#94a3b8" }}>({pct(seg.with_phone, seg.signups)})</span></td>
      <td>{seg.with_both}</td>
      <td>{seg.with_campaign} <span style={{ color: "#94a3b8" }}>({pct(seg.with_campaign, seg.signups)})</span></td>
    </tr>
  );
}

export default function SignupIntakeClient() {
  const [days, setDays] = useState(30);
  const [d, setD] = useState<SignupIntakeResponse | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    const load = () => fetchSignupIntake(days).then((x) => { setD(x); setErr(null); }).catch((e) => setErr(String(e)));
    load();
    const t = setInterval(load, 300000);
    return () => clearInterval(t);
  }, [days]);

  if (err) return <div style={{ ...CARD, color: "#dc2626" }}>Could not load sign-up intake: {err}</div>;
  if (!d) return <div style={CARD}>Loading…</div>;
  if (!d.first_tracked_day) {
    return <div style={CARD}>No sign-ups recorded yet. The hourly sync fills this page on its first run.</div>;
  }

  const bars = d.days.map((x) => ({
    day: label(x.day), Explorer: x.explorer.signups, "Open House": x.open_house.signups,
  }));
  const contact = d.days.map((x) => ({
    day: label(x.day),
    "Explorer with email": x.explorer.with_email, "Explorer with phone": x.explorer.with_phone,
    "Open House with email": x.open_house.with_email, "Open House with phone": x.open_house.with_phone,
  }));
  const cumulative = d.cumulative.map((x) => ({
    day: label(x.day),
    "Explorer with email": x.explorer?.with_email ?? 0, "Explorer with phone": x.explorer?.with_phone ?? 0,
    "Open House with phone": x.open_house?.with_phone ?? 0,
  }));
  const rows = [...d.days].reverse();

  return (
    <div style={{ display: "grid", gap: "1rem" }}>
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        {RANGES.map((r) => (
          <button key={r} onClick={() => setDays(r)}
                  style={{ padding: "4px 12px", borderRadius: 6, border: "1px solid #cbd5e1", cursor: "pointer",
                           background: days === r ? "#1e293b" : "#ffffff", color: days === r ? "#ffffff" : "#1e293b" }}>
            {r} days
          </button>
        ))}
        <span style={{ color: "#64748b", fontSize: "0.8rem" }}>
          Tracked since {d.first_tracked_day} · refreshed {ago(d.last_refreshed)} · days are {d.timezone}
        </span>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))", gap: "1rem" }}>
        <Tile title="EXPLORER SIGN-UPS (all time)" color={COLORS.explorer} a={d.alltime.explorer} />
        <Tile title="OPEN HOUSE RSVPs (all time)" color={COLORS.open_house} a={d.alltime.open_house} />
      </div>

      <div style={CARD}>
        <strong>New sign-ups per day</strong>
        <div style={{ height: 240, marginTop: 8 }}>
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={bars}>
              <CartesianGrid stroke="#e2e8f0" vertical={false} />
              <XAxis dataKey="day" {...axis} /><YAxis allowDecimals={false} {...axis} />
              <Tooltip /><Legend />
              <Bar dataKey="Explorer" stackId="s" fill={COLORS.explorer} />
              <Bar dataKey="Open House" stackId="s" fill={COLORS.open_house} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>

      <div style={CARD}>
        <strong>Of those who signed up that day: with an email, with a phone</strong>
        <div style={{ color: "#64748b", fontSize: "0.8rem" }}>
          Counted by sign-up day using today's contact data, so a phone added later raises an earlier day.
        </div>
        <div style={{ height: 240, marginTop: 8 }}>
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={contact}>
              <CartesianGrid stroke="#e2e8f0" vertical={false} />
              <XAxis dataKey="day" {...axis} /><YAxis allowDecimals={false} {...axis} />
              <Tooltip /><Legend />
              <Line type="monotone" dataKey="Explorer with email" stroke={COLORS.email} dot={false} strokeWidth={2} />
              <Line type="monotone" dataKey="Explorer with phone" stroke={COLORS.phone} dot={false} strokeWidth={2} />
              <Line type="monotone" dataKey="Open House with email" stroke={COLORS.email} strokeDasharray="4 3" dot={false} />
              <Line type="monotone" dataKey="Open House with phone" stroke={COLORS.phone} strokeDasharray="4 3" dot={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>

      <div style={CARD}>
        <strong>Running totals: when emails and phones arrived</strong>
        <div style={{ color: "#64748b", fontSize: "0.8rem" }}>
          Cumulative, from hourly snapshots (last snapshot of each day). This is the line to watch once the sign-up form starts asking for a phone.
        </div>
        <div style={{ height: 220, marginTop: 8 }}>
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={cumulative}>
              <CartesianGrid stroke="#e2e8f0" vertical={false} />
              <XAxis dataKey="day" {...axis} /><YAxis allowDecimals={false} {...axis} />
              <Tooltip /><Legend />
              <Line type="monotone" dataKey="Explorer with email" stroke={COLORS.email} dot={false} strokeWidth={2} />
              <Line type="monotone" dataKey="Explorer with phone" stroke={COLORS.phone} dot={false} strokeWidth={2} />
              <Line type="monotone" dataKey="Open House with phone" stroke={COLORS.open_house} dot={false} strokeWidth={2} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>

      <div style={{ ...CARD, overflowX: "auto" }}>
        <strong>By day</strong>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.85rem", marginTop: 6 }}>
          <thead>
            <tr style={{ textAlign: "left", color: "#64748b", fontSize: "0.8rem" }}>
              <th>Day</th><th>Segment</th><th>Sign-ups</th><th>With email</th><th>With phone</th><th>Both</th><th>Campaign tag</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((x) => (
              <Fragment key={x.day}>
                <Row day={x.day} seg={x.explorer} name="Explorer" />
                <Row day="" seg={x.open_house} name="Open House" />
              </Fragment>
            ))}
          </tbody>
        </table>
        <div style={{ marginTop: 8, fontSize: "0.8rem", color: "#64748b" }}>
          Explorer = tag src-free_signup (or source "api v1" without an Open House tag). Open House = tag src-open_house.
          "Campaign tag" = the contact carries a camp-* tag, so its ad campaign is known.
        </div>
      </div>
    </div>
  );
}
