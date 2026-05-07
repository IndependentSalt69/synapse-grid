import React from "react";
import {
  ComposedChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ReferenceLine,
  ResponsiveContainer,
} from "recharts";
import { MapContainer, TileLayer, CircleMarker, Popup } from "react-leaflet";
import type { AlertDetail as AlertDetailType } from "../lib/api";
import { useMeterReadings, useMeterBaseline, useMeterPeers } from "../hooks/useMeterReadings";

interface Props {
  alert: AlertDetailType;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Normalise any timestamp string to a JS Date safely. */
function parseTs(ts: string): Date {
  // DB stores "2024-01-01 00:00:00+00:00" — replace space with T for ISO 8601
  return new Date(ts.replace(" ", "T"));
}

function formatTick(ts: string): string {
  const d = parseTs(ts);
  if (isNaN(d.getTime())) return "";
  return d.toLocaleDateString("en-IN", { month: "short", day: "numeric" });
}

function formatTooltipLabel(ts: string): string {
  const d = parseTs(ts);
  if (isNaN(d.getTime())) return ts;
  return d.toLocaleString("en-IN", {
    month: "short", day: "numeric",
    hour: "2-digit", minute: "2-digit",
  });
}

// ---------------------------------------------------------------------------
// Chart data builder
// ---------------------------------------------------------------------------

function buildChartData(
  readings: Array<{ timestamp: string; kwh: number | null }>,
  baseline: Array<{ hour_of_day: number; day_of_week: number; baseline_kwh: number }>
) {
  // Build baseline lookup keyed by "hour_dayOfWeek"
  const baselineLookup: Record<string, number> = {};
  for (const b of baseline) {
    baselineLookup[`${b.hour_of_day}_${b.day_of_week}`] = b.baseline_kwh;
  }

  const data = readings.map((r) => {
    const d = parseTs(r.timestamp);
    const hour = d.getHours();
    const dow = d.getDay(); // 0=Sun … 6=Sat
    const key = `${hour}_${dow}`;
    return {
      timestamp: r.timestamp,
      actual: r.kwh,
      baseline: baselineLookup[key] ?? null,
    };
  });

  console.log("[AlertDetail] chartData sample (first 3):", data.slice(0, 3));
  console.log("[AlertDetail] chartData length:", data.length);
  console.log("[AlertDetail] baseline entries:", baseline.length);
  console.log(
    "[AlertDetail] rows with baseline value:",
    data.filter((d) => d.baseline !== null).length
  );

  return data;
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function DeviationRow({
  label,
  value,
}: {
  label: string;
  value: number | null;
}) {
  if (value === null || value === undefined) return null;
  const isNeg = value < 0;
  return (
    <div
      style={{
        display: "flex",
        justifyContent: "space-between",
        padding: "6px 0",
        borderBottom: "1px solid #f1f5f9",
      }}
    >
      <span style={{ fontSize: "0.8rem", color: "#64748b" }}>{label}</span>
      <span
        style={{
          fontSize: "0.85rem",
          fontWeight: 600,
          color: isNeg ? "#dc2626" : "#16a34a",
        }}
      >
        {isNeg ? "" : "+"}
        {value.toFixed(1)}%
      </span>
    </div>
  );
}

function ShapCard({ shap_top3 }: { shap_top3: AlertDetailType["shap_top3"] }) {
  if (!shap_top3 || shap_top3.length === 0) return null;
  const maxAbs = Math.max(...shap_top3.map((s) => Math.abs(s.value)), 0.001);

  return (
    <div
      style={{
        background: "#fffbeb",
        border: "1px solid #fde68a",
        borderRadius: 8,
        padding: "12px 16px",
        marginBottom: 12,
      }}
    >
      <div
        style={{
          fontSize: "0.8rem",
          fontWeight: 700,
          color: "#92400e",
          marginBottom: 10,
        }}
      >
        Why this alert?
      </div>
      {shap_top3.map((entry, i) => (
        <div key={i} style={{ marginBottom: 10 }}>
          <div style={{ fontSize: "0.8rem", color: "#1e293b", marginBottom: 4 }}>
            {entry.plain_english}
          </div>
          <div
            style={{
              height: 4,
              borderRadius: 2,
              background: "#fde68a",
              position: "relative",
            }}
          >
            <div
              style={{
                height: "100%",
                width: `${(Math.abs(entry.value) / maxAbs) * 100}%`,
                background: entry.value > 0 ? "#ef4444" : "#3b82f6",
                borderRadius: 2,
              }}
            />
          </div>
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export default function AlertDetail({ alert }: Props) {
  const { data: readings } = useMeterReadings(alert.meter_id);
  const { data: baseline } = useMeterBaseline(alert.meter_id);
  const { data: peers } = useMeterPeers(alert.meter_id);

  const chartData = buildChartData(readings ?? [], baseline ?? []);

  // Find the index of the triggered_at timestamp in chartData for the ReferenceLine
  const triggeredTs = alert.triggered_at;

  // Find a representative lat/lng for the alerted meter from peers
  const peerCenter: [number, number] = [12.9716, 77.5946];

  return (
    <div style={{ padding: "16px", overflowY: "auto" }}>
      {/* Header */}
      <div style={{ marginBottom: 16 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
          <h3 style={{ margin: 0, fontSize: "1rem", fontWeight: 700, color: "#1e293b" }}>
            {alert.meter_id}
          </h3>
          <span
            style={{
              background: "#fee2e2",
              color: "#dc2626",
              padding: "2px 8px",
              borderRadius: 12,
              fontSize: "0.75rem",
              fontWeight: 600,
            }}
          >
            {alert.alert_type.replace("_", " ")}
          </span>
        </div>
        <div style={{ fontSize: "0.75rem", color: "#94a3b8" }}>
          Triggered: {new Date(alert.triggered_at).toLocaleString()}
          {alert.feeder_id && ` · Feeder: ${alert.feeder_id}`}
        </div>
      </div>

      {/* 14-day consumption chart */}
      {chartData.length > 0 && (
        <div style={{ marginBottom: 16 }}>
          <div style={{ fontSize: "0.8rem", fontWeight: 600, color: "#475569", marginBottom: 8 }}>
            14-Day Consumption
          </div>
          <ResponsiveContainer width="100%" height={200}>
            <ComposedChart data={chartData} margin={{ top: 4, right: 8, bottom: 4, left: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
              <XAxis
                dataKey="timestamp"
                tickFormatter={formatTick}
                interval={95}
                tick={{ fontSize: 10 }}
              />
              <YAxis unit=" kWh" tick={{ fontSize: 10 }} width={55} />
              <Tooltip
                labelFormatter={(ts) => formatTooltipLabel(ts as string)}
                formatter={(val: number) => [`${val?.toFixed(3)} kWh`]}
              />
              <Legend wrapperStyle={{ fontSize: "0.75rem" }} />
              {/* Mark the anomaly trigger time with a vertical line */}
              <ReferenceLine
                x={triggeredTs}
                stroke="#ca8a04"
                strokeDasharray="4 2"
                label={{ value: "Alert", fontSize: 9, fill: "#ca8a04", position: "top" }}
              />
              <Line
                type="monotone"
                dataKey="actual"
                stroke="#3b82f6"
                strokeWidth={1.5}
                dot={false}
                name="Actual"
              />
              <Line
                type="monotone"
                dataKey="baseline"
                stroke="#9ca3af"
                strokeWidth={1}
                strokeDasharray="5 5"
                dot={false}
                name="Baseline"
              />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* Debug: show message if no chart data */}
      {chartData.length === 0 && readings !== undefined && (
        <div style={{ padding: "12px", background: "#fef9c3", borderRadius: 6, marginBottom: 12, fontSize: "0.8rem", color: "#92400e" }}>
          No readings data available for this meter yet.
        </div>
      )}

      {/* Deviation metrics */}
      <div
        style={{
          background: "#f8fafc",
          border: "1px solid #e2e8f0",
          borderRadius: 8,
          padding: "12px 16px",
          marginBottom: 12,
        }}
      >
        <div style={{ fontSize: "0.8rem", fontWeight: 700, color: "#475569", marginBottom: 8 }}>
          Deviation Metrics
        </div>
        <DeviationRow label="vs Personal Baseline" value={alert.pct_deviation_from_baseline} />
        <DeviationRow label="vs Peer Median" value={alert.pct_deviation_from_peer_median} />
        <DeviationRow label="vs Cluster Norm" value={alert.pct_deviation_from_cluster_norm} />
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            padding: "6px 0",
          }}
        >
          <span style={{ fontSize: "0.8rem", color: "#64748b" }}>Confidence</span>
          <span style={{ fontSize: "0.85rem", fontWeight: 600, color: "#dc2626" }}>
            {(alert.anomaly_confidence * 100).toFixed(1)}%
          </span>
        </div>
      </div>

      {/* SHAP explanation */}
      <ShapCard shap_top3={alert.shap_top3} />

      {/* Neighborhood mini-map */}
      {peers && peers.length > 0 && (
        <div style={{ marginBottom: 12 }}>
          <div style={{ fontSize: "0.8rem", fontWeight: 600, color: "#475569", marginBottom: 8 }}>
            Neighborhood Status
          </div>
          <div style={{ height: 180, borderRadius: 8, overflow: "hidden", border: "1px solid #e2e8f0" }}>
            <MapContainer
              center={peerCenter}
              zoom={15}
              style={{ height: "100%", width: "100%" }}
              zoomControl={false}
            >
              <TileLayer
                attribution='&copy; OpenStreetMap'
                url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
              />
              {peers.map((peer) => {
                const color =
                  peer.status === "anomalous"
                    ? "#ef4444"
                    : peer.status === "elevated"
                    ? "#f59e0b"
                    : "#22c55e";
                return (
                  <CircleMarker
                    key={peer.meter_id}
                    center={[peer.lat, peer.lng]}
                    radius={8}
                    pathOptions={{ color, fillColor: color, fillOpacity: 0.7 }}
                  >
                    <Popup>
                      <div style={{ fontSize: "0.75rem" }}>
                        <strong>{peer.meter_id}</strong>
                        <br />
                        {peer.status}
                      </div>
                    </Popup>
                  </CircleMarker>
                );
              })}
            </MapContainer>
          </div>
          <div style={{ display: "flex", gap: 12, marginTop: 6, fontSize: "0.7rem", color: "#64748b" }}>
            <span>
              <span style={{ color: "#22c55e" }}>●</span> Normal:{" "}
              {alert.peer_status_summary?.normal ?? 0}
            </span>
            <span>
              <span style={{ color: "#f59e0b" }}>●</span> Elevated:{" "}
              {alert.peer_status_summary?.elevated ?? 0}
            </span>
            <span>
              <span style={{ color: "#ef4444" }}>●</span> Anomalous:{" "}
              {alert.peer_status_summary?.anomalous ?? 0}
            </span>
          </div>
        </div>
      )}
    </div>
  );
}
