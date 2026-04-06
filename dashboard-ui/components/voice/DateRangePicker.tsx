"use client";

interface Props {
  fromDate: string;
  toDate: string;
  onFromChange: (v: string) => void;
  onToChange: (v: string) => void;
}

export default function DateRangePicker({ fromDate, toDate, onFromChange, onToChange }: Props) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: "0.5rem",
        background: "#f8fafc",
        border: "1px solid #e2e8f0",
        borderRadius: 8,
        padding: "0.5rem 0.875rem",
      }}
    >
      <span style={{ fontSize: "0.75rem", color: "#64748b", fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.05em" }}>
        From
      </span>
      <input
        type="date"
        value={fromDate}
        onChange={(e) => onFromChange(e.target.value)}
        style={{
          border: "none",
          background: "transparent",
          fontSize: "0.875rem",
          color: "#1e293b",
          cursor: "pointer",
          outline: "none",
        }}
      />
      <span style={{ color: "#cbd5e1", fontSize: "1.2rem" }}>–</span>
      <span style={{ fontSize: "0.75rem", color: "#64748b", fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.05em" }}>
        To
      </span>
      <input
        type="date"
        value={toDate}
        onChange={(e) => onToChange(e.target.value)}
        style={{
          border: "none",
          background: "transparent",
          fontSize: "0.875rem",
          color: "#1e293b",
          cursor: "pointer",
          outline: "none",
        }}
      />
    </div>
  );
}
