import React, { useState } from "react";
import { useAlertQueue } from "../hooks/useAlertQueue";
import { useAlertStore } from "../stores/alertStore";
import type { AlertSummary } from "../lib/api";

const STATE_COLORS: Record<string, { bg: string; text: string }> = {
  NEW: { bg: "#dbeafe", text: "#1d4ed8" },
  WATCHING: { bg: "#fef9c3", text: "#854d0e" },
  UNDER_REVIEW: { bg: "#ede9fe", text: "#6d28d9" },
  DISPATCHED: { bg: "#dcfce7", text: "#15803d" },
  DISMISSED: { bg: "#f1f5f9", text: "#64748b" },
};

const PATTERN_LABELS: Record<string, string> = {
  SUSTAINED_DROP: "Sustained Drop",
  RECURRING_DAILY_DIP: "Daily Dip",
  SPIKE: "Spike",
};

function ConfidenceBadge({ confidence }: { confidence: number }) {
  const pct = (confidence * 100).toFixed(0);
  let bg = "#f1f5f9";
  let text = "#64748b";
  if (confidence >= 0.95) {
    bg = "#fee2e2";
    text = "#dc2626";
  } else if (confidence >= 0.9) {
    bg = "#fef3c7";
    text = "#d97706";
  }
  return (
    <span
      style={{
        background: bg,
        color: text,
        padding: "2px 8px",
        borderRadius: 12,
        fontSize: "0.75rem",
        fontWeight: 600,
      }}
    >
      {pct}%
    </span>
  );
}

function StateBadge({ state }: { state: string }) {
  const colors = STATE_COLORS[state] ?? { bg: "#f1f5f9", text: "#64748b" };
  return (
    <span
      style={{
        background: colors.bg,
        color: colors.text,
        padding: "2px 8px",
        borderRadius: 12,
        fontSize: "0.7rem",
        fontWeight: 600,
      }}
    >
      {state.replace("_", " ")}
    </span>
  );
}

function PatternBadge({ pattern }: { pattern: string | null }) {
  if (!pattern) return null;
  return (
    <span
      style={{
        background: "#f0fdf4",
        color: "#166534",
        padding: "2px 8px",
        borderRadius: 12,
        fontSize: "0.7rem",
        border: "1px solid #bbf7d0",
      }}
    >
      {PATTERN_LABELS[pattern] ?? pattern}
    </span>
  );
}

function AlertRow({
  alert,
  isSelected,
  onClick,
}: {
  alert: AlertSummary;
  isSelected: boolean;
  onClick: () => void;
}) {
  return (
    <div
      onClick={onClick}
      style={{
        padding: "12px 16px",
        borderBottom: "1px solid #f1f5f9",
        cursor: "pointer",
        backgroundColor: isSelected ? "#eff6ff" : "#ffffff",
        borderLeft: isSelected ? "3px solid #3b82f6" : "3px solid transparent",
        transition: "background-color 0.1s",
      }}
    >
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "flex-start",
          marginBottom: 6,
        }}
      >
        <div>
          <span style={{ fontWeight: 600, fontSize: "0.875rem", color: "#1e293b" }}>
            {alert.meter_id}
          </span>
          {alert.feeder_id && (
            <span style={{ fontSize: "0.75rem", color: "#94a3b8", marginLeft: 6 }}>
              {alert.feeder_id}
            </span>
          )}
        </div>
        <ConfidenceBadge confidence={alert.anomaly_confidence} />
      </div>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
        <StateBadge state={alert.state} />
        <PatternBadge pattern={alert.pattern_type} />
        <span
          style={{
            fontSize: "0.7rem",
            color: "#94a3b8",
            background: "#f8fafc",
            padding: "2px 6px",
            borderRadius: 4,
          }}
        >
          {alert.alert_type.replace("_", " ")}
        </span>
      </div>
      <div style={{ fontSize: "0.7rem", color: "#94a3b8", marginTop: 4 }}>
        {new Date(alert.triggered_at).toLocaleString()}
      </div>
    </div>
  );
}

/**
 * AlertQueue — sorted list of alerts with state and pattern badges.
 * Supports state filter dropdown.
 */
export default function AlertQueue() {
  const [stateFilter, setStateFilter] = useState<string>("ALL");
  const { data: alerts, isLoading, error } = useAlertQueue();
  const selectedAlertId = useAlertStore((s) => s.selectedAlertId);
  const selectAlert = useAlertStore((s) => s.selectAlert);

  const filtered = (alerts ?? []).filter(
    (a) => stateFilter === "ALL" || a.state === stateFilter
  );

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      {/* Header */}
      <div
        style={{
          padding: "12px 16px",
          borderBottom: "1px solid #e2e8f0",
          backgroundColor: "#ffffff",
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <h2 style={{ margin: 0, fontSize: "1rem", fontWeight: 700, color: "#1e293b" }}>
            Alert Queue
          </h2>
          <span
            style={{
              background: "#fee2e2",
              color: "#dc2626",
              borderRadius: 12,
              padding: "1px 8px",
              fontSize: "0.75rem",
              fontWeight: 600,
            }}
          >
            {filtered.length}
          </span>
        </div>
        <select
          value={stateFilter}
          onChange={(e) => setStateFilter(e.target.value)}
          style={{
            fontSize: "0.75rem",
            padding: "4px 8px",
            border: "1px solid #e2e8f0",
            borderRadius: 4,
            color: "#475569",
            backgroundColor: "#f8fafc",
          }}
        >
          <option value="ALL">All States</option>
          <option value="NEW">NEW</option>
          <option value="WATCHING">WATCHING</option>
          <option value="UNDER_REVIEW">UNDER REVIEW</option>
          <option value="DISPATCHED">DISPATCHED</option>
          <option value="DISMISSED">DISMISSED</option>
        </select>
      </div>

      {/* Alert list */}
      <div style={{ flex: 1, overflowY: "auto" }}>
        {isLoading && (
          <div style={{ padding: 24, textAlign: "center", color: "#94a3b8", fontSize: "0.875rem" }}>
            Loading alerts...
          </div>
        )}
        {error && (
          <div style={{ padding: 24, textAlign: "center", color: "#ef4444", fontSize: "0.875rem" }}>
            Failed to load alerts. Is the API running?
          </div>
        )}
        {!isLoading && !error && filtered.length === 0 && (
          <div style={{ padding: 24, textAlign: "center", color: "#94a3b8", fontSize: "0.875rem" }}>
            No alerts found. Run the pipeline to generate alerts.
          </div>
        )}
        {filtered.map((alert) => (
          <AlertRow
            key={alert.alert_id}
            alert={alert}
            isSelected={selectedAlertId === alert.alert_id}
            onClick={() => selectAlert(alert.alert_id)}
          />
        ))}
      </div>
    </div>
  );
}
