"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import Link from "next/link";
import { fetchDbTables, runDbQuery } from "@/lib/api";

interface TableInfo {
  name: string;
  row_estimate: number;
}

interface QueryResult {
  columns: string[];
  rows: (string | null)[][];
  row_count: number;
  truncated: boolean;
}

const CARD: React.CSSProperties = {
  background: "#ffffff",
  border: "1px solid #e2e8f0",
  borderRadius: 8,
  boxShadow: "0 1px 3px rgba(0,0,0,0.05)",
};

export default function DbExplorerPage() {
  const [tables, setTables] = useState<TableInfo[]>([]);
  const [sql, setSql] = useState("SELECT * FROM lead_state LIMIT 50;");
  const [result, setResult] = useState<QueryResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [loadingTables, setLoadingTables] = useState(true);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    fetchDbTables()
      .then((r) => setTables(r.tables))
      .catch((e) => console.error("table list failed", e))
      .finally(() => setLoadingTables(false));
  }, []);

  const runQuery = useCallback(async () => {
    if (!sql.trim()) return;
    setRunning(true);
    setError(null);
    setResult(null);
    try {
      const r = await runDbQuery(sql);
      setResult(r);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setRunning(false);
    }
  }, [sql]);

  // Ctrl+Enter / Cmd+Enter to run
  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
      e.preventDefault();
      runQuery();
    }
  };

  const handleTableClick = (name: string) => {
    setSql(`SELECT * FROM ${name} LIMIT 100;`);
    textareaRef.current?.focus();
  };

  return (
    <div
      style={{
        height: "100vh",
        overflow: "hidden",
        display: "flex",
        flexDirection: "column",
        background: "#f1f5f9",
        fontFamily: "system-ui, -apple-system, sans-serif",
        color: "#1e293b",
      }}
    >
      {/* Top bar */}
      <div
        style={{
          height: 40,
          flexShrink: 0,
          background: "#ffffff",
          borderBottom: "1px solid #e2e8f0",
          display: "flex",
          alignItems: "center",
          padding: "0 1rem",
          gap: "0.625rem",
        }}
      >
        <Link href="/" style={{ color: "#64748b", textDecoration: "none", fontSize: "0.9rem" }}>
          ← Dashboard
        </Link>
        <span style={{ color: "#e2e8f0" }}>|</span>
        <span style={{ fontSize: "1rem", fontWeight: 600, color: "#1e293b" }}>
          DB Explorer
        </span>
        <span style={{ marginLeft: "auto", fontSize: "0.8rem", color: "#94a3b8" }}>
          Ctrl+Enter to run · max 500 rows
        </span>
      </div>

      {/* Main */}
      <div
        style={{
          flex: 1,
          minHeight: 0,
          display: "flex",
          gap: "0.625rem",
          padding: "0.625rem",
          overflow: "hidden",
        }}
      >
        {/* Left: table list */}
        <div
          style={{
            width: 220,
            flexShrink: 0,
            ...CARD,
            display: "flex",
            flexDirection: "column",
            overflow: "hidden",
          }}
        >
          <div
            style={{
              padding: "0.5rem 0.75rem",
              borderBottom: "1px solid #e2e8f0",
              fontSize: "0.78rem",
              fontWeight: 700,
              color: "#f59e0b",
              textTransform: "uppercase",
              letterSpacing: "0.07em",
              flexShrink: 0,
            }}
          >
            Tables
          </div>
          <div style={{ flex: 1, overflowY: "auto" }}>
            {loadingTables ? (
              <div style={{ padding: "1rem", color: "#94a3b8", fontSize: "0.85rem" }}>Loading…</div>
            ) : (
              tables.map((t) => (
                <button
                  key={t.name}
                  onClick={() => handleTableClick(t.name)}
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    width: "100%",
                    padding: "0.4rem 0.75rem",
                    background: "none",
                    border: "none",
                    borderBottom: "1px solid #f1f5f9",
                    cursor: "pointer",
                    textAlign: "left",
                    fontSize: "0.82rem",
                    color: "#334155",
                    gap: "0.5rem",
                  }}
                  onMouseEnter={(e) => (e.currentTarget.style.background = "#f8fafc")}
                  onMouseLeave={(e) => (e.currentTarget.style.background = "none")}
                >
                  <span style={{ fontFamily: "monospace" }}>{t.name}</span>
                  <span style={{ color: "#94a3b8", fontSize: "0.75rem", flexShrink: 0 }}>
                    {t.row_estimate.toLocaleString()}
                  </span>
                </button>
              ))
            )}
          </div>
        </div>

        {/* Right: editor + results */}
        <div
          style={{
            flex: 1,
            minWidth: 0,
            display: "flex",
            flexDirection: "column",
            gap: "0.625rem",
            overflow: "hidden",
          }}
        >
          {/* SQL editor card */}
          <div
            style={{
              flexShrink: 0,
              ...CARD,
              padding: "0.625rem",
              display: "flex",
              flexDirection: "column",
              gap: "0.5rem",
            }}
          >
            <textarea
              ref={textareaRef}
              value={sql}
              onChange={(e) => setSql(e.target.value)}
              onKeyDown={handleKeyDown}
              rows={5}
              spellCheck={false}
              style={{
                width: "100%",
                fontFamily: "ui-monospace, 'Cascadia Code', monospace",
                fontSize: "0.88rem",
                padding: "0.5rem 0.625rem",
                border: "1px solid #e2e8f0",
                borderRadius: 6,
                background: "#f8fafc",
                color: "#1e293b",
                resize: "vertical",
                outline: "none",
                lineHeight: 1.6,
                boxSizing: "border-box",
              }}
            />
            <div style={{ display: "flex", alignItems: "center", gap: "0.75rem" }}>
              <button
                onClick={runQuery}
                disabled={running}
                style={{
                  padding: "0.4rem 1.25rem",
                  background: running ? "#94a3b8" : "#f59e0b",
                  color: "#ffffff",
                  border: "none",
                  borderRadius: 6,
                  fontWeight: 700,
                  fontSize: "0.88rem",
                  cursor: running ? "not-allowed" : "pointer",
                }}
              >
                {running ? "Running…" : "▶ Run"}
              </button>
              {result && !error && (
                <span style={{ fontSize: "0.82rem", color: "#64748b" }}>
                  {result.row_count.toLocaleString()} row{result.row_count !== 1 ? "s" : ""}
                  {result.truncated && " (truncated at 500)"}
                </span>
              )}
              {error && (
                <span style={{ fontSize: "0.82rem", color: "#dc2626" }}>
                  ⚠ {error}
                </span>
              )}
            </div>
          </div>

          {/* Results card */}
          <div
            style={{
              flex: 1,
              minHeight: 0,
              ...CARD,
              overflow: "auto",
            }}
          >
            {!result && !error && (
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  height: "100%",
                  color: "#94a3b8",
                  fontSize: "0.875rem",
                }}
              >
                Click a table on the left or type a query and press Run
              </div>
            )}
            {result && result.columns.length > 0 && (
              <table
                style={{
                  width: "100%",
                  borderCollapse: "collapse",
                  fontSize: "0.82rem",
                  fontFamily: "ui-monospace, monospace",
                }}
              >
                <thead>
                  <tr>
                    {result.columns.map((col) => (
                      <th
                        key={col}
                        style={{
                          padding: "0.4rem 0.625rem",
                          textAlign: "left",
                          background: "#f8fafc",
                          borderBottom: "2px solid #e2e8f0",
                          color: "#475569",
                          fontWeight: 700,
                          whiteSpace: "nowrap",
                          position: "sticky",
                          top: 0,
                          zIndex: 1,
                        }}
                      >
                        {col}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {result.rows.map((row, i) => (
                    <tr
                      key={i}
                      style={{ background: i % 2 === 0 ? "#ffffff" : "#f8fafc" }}
                    >
                      {row.map((cell, j) => (
                        <td
                          key={j}
                          style={{
                            padding: "0.35rem 0.625rem",
                            borderBottom: "1px solid #f1f5f9",
                            color: cell === null ? "#94a3b8" : "#1e293b",
                            fontStyle: cell === null ? "italic" : "normal",
                            maxWidth: 320,
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                            whiteSpace: "nowrap",
                          }}
                          title={cell ?? "NULL"}
                        >
                          {cell === null ? "NULL" : cell}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            {result && result.columns.length === 0 && (
              <div style={{ padding: "1rem", color: "#64748b", fontSize: "0.875rem" }}>
                Query executed. {result.row_count} row{result.row_count !== 1 ? "s" : ""} affected.
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
