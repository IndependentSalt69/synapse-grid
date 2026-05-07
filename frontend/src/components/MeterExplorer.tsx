import React, { useState, useMemo, useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  ComposedChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from "recharts";
import { fetchAllMeters, type MeterInfo } from "../lib/api";
import { useMeterReadings, useMeterBaseline } from "../hooks/useMeterReadings";
import { useAlertStore } from "../stores/alertStore";

// ---------------------------------------------------------------------------
// Helpers (same as AlertDetail)
// ---------------------------------------------------------------------------

function parseTs(ts: string): Date {
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
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function buildChartData(
  readings: Array<{ timestamp: string; kwh: number | null }>,
  baseline: Array<{ hour_of_day: number; day_of_week: number; baseline_kwh: number }>
) {
  const baselineLookup: Record<string, number> = {};
  for (const b of baseline) {
    baselineLookup[`${b.hour_of_day}_${b.day_of_week}`] = b.baseline_kwh;
  }
  return readings.map((r) => {
    const d = parseTs(r.timestamp);
    const key = `${d.getHours()}_${d.getDay()}`;
    return {
      timestamp: r.timestamp,
      actual: r.kwh,
      baseline: baselineLookup[key] ?? null,
    };
  });
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function MeterChart({ meterId }: { meterId: string }) {
  const { data: readings, isLoading: loadingReadings } = useMeterReadings(meterId);
  const { data: baseline, isLoading: loadingBaseline } = useMeterBaseline(meterId);

  const chartData = useMemo(
    () => buildChartData(readings ?? [], baseline ?? []),
    [readings, baseline]
  );

  if (loadingReadings || loadingBaseline) {
    return (
      <div style={{ padding: "24px", textAlign: "center", color: "#94a3b8", fontSize: "0.85rem" }}>
        Loading readings...
      </div>
    );
  }

  if (chartData.length === 0) {
    return (
      <div
        style={{
          padding: "16px",
          background: "#fef9c3",
          borderRadius: 6,
          fontSize: "0.8rem",
          color: "#92400e",
        }}
      >
        No readings available for this meter.
      </div>
    );
  }

  // Stats
  const kwhValues = chartData.map((d) => d.actual).filter((v): v is number => v !== null);
  const minKwh = Math.min(...kwhValues).toFixed(3);
  const maxKwh = Math.max(...kwhValues).toFixed(3);
  const avgKwh = (kwhValues.reduce((a, b) => a + b, 0) / kwhValues.length).toFixed(3);

  return (
    <div>
      {/* Stats row */}
      <div
        style={{
          display: "flex",
          gap: 12,
          marginBottom: 12,
          padding: "8px 12px",
          background: "#f8fafc",
          borderRadius: 6,
          border: "1px solid #e2e8f0",
        }}
      >
        {[
          { label: "Min", value: `${minKwh} kWh` },
          { label: "Avg", value: `${avgKwh} kWh` },
          { label: "Max", value: `${maxKwh} kWh` },
          { label: "Points", value: chartData.length.toLocaleString() },
        ].map(({ label, value }) => (
          <div key={label} style={{ flex: 1, textAlign: "center" }}>
            <div style={{ fontSize: "0.7rem", color: "#94a3b8" }}>{label}</div>
            <div style={{ fontSize: "0.8rem", fontWeight: 600, color: "#1e293b" }}>{value}</div>
          </div>
        ))}
      </div>

      {/* Chart */}
      <ResponsiveContainer width="100%" height={220}>
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
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export default function MeterExplorer() {
  const [search, setSearch] = useState("");
  const [selectedMeter, setSelectedMeter] = useState<MeterInfo | null>(null);
  const [dropdownOpen, setDropdownOpen] = useState(false);

  const explorerMeterId = useAlertStore((s) => s.explorerMeterId);

  const { data: meters, isLoading, error } = useQuery({
    queryKey: ["all-meters"],
    queryFn: fetchAllMeters,
    staleTime: 10 * 60 * 1000, // 10 minutes — registry rarely changes
  });

  // When explorerMeterId is set from the map, auto-select that meter
  useEffect(() => {
    if (explorerMeterId && meters) {
      const found = meters.find((m) => m.meter_id === explorerMeterId);
      if (found) {
        setSelectedMeter(found);
        setSearch(found.meter_id);
        setDropdownOpen(false);
      }
    }
  }, [explorerMeterId, meters]);

  const filtered = useMemo(() => {
    if (!meters) return [];
    const q = search.toLowerCase();
    return meters.filter(
      (m) =>
        m.meter_id.toLowerCase().includes(q) ||
        (m.feeder_id ?? "").toLowerCase().includes(q) ||
        (m.zone ?? "").toLowerCase().includes(q)
    );
  }, [meters, search]);

  function handleSelect(meter: MeterInfo) {
    setSelectedMeter(meter);
    setSearch(meter.meter_id);
    setDropdownOpen(false);
  }

  function handleSearchChange(e: React.ChangeEvent<HTMLInputElement>) {
    setSearch(e.target.value);
    setDropdownOpen(true);
    if (e.target.value === "") setSelectedMeter(null);
  }

  return (
    <div style={{ padding: "16px", overflowY: "auto", height: "100%" }}>
      {/* Header */}
      <div style={{ marginBottom: 16 }}>
        <h3 style={{ margin: "0 0 4px 0", fontSize: "1rem", fontWeight: 700, color: "#1e293b" }}>
          Meter Explorer
        </h3>
        <div style={{ fontSize: "0.75rem", color: "#94a3b8" }}>
          Browse any meter's 14-day consumption and baseline on demand.
        </div>
      </div>

      {/* Searchable dropdown */}
      <div style={{ position: "relative", marginBottom: 16 }}>
        <input
          type="text"
          value={search}
          onChange={handleSearchChange}
          onFocus={() => setDropdownOpen(true)}
          placeholder={isLoading ? "Loading meters..." : "Search meter ID, feeder, or zone…"}
          disabled={isLoading || !!error}
          style={{
            width: "100%",
            padding: "8px 12px",
            border: "1px solid #e2e8f0",
            borderRadius: 6,
            fontSize: "0.875rem",
            color: "#1e293b",
            backgroundColor: isLoading ? "#f8fafc" : "#ffffff",
            boxSizing: "border-box",
            outline: "none",
          }}
        />

        {/* Dropdown list */}
        {dropdownOpen && filtered.length > 0 && (
          <div
            style={{
              position: "absolute",
              top: "100%",
              left: 0,
              right: 0,
              zIndex: 100,
              background: "#ffffff",
              border: "1px solid #e2e8f0",
              borderRadius: 6,
              boxShadow: "0 4px 16px rgba(0,0,0,0.1)",
              maxHeight: 240,
              overflowY: "auto",
            }}
          >
            {filtered.slice(0, 50).map((meter) => (
              <div
                key={meter.meter_id}
                onClick={() => handleSelect(meter)}
                style={{
                  padding: "8px 12px",
                  cursor: "pointer",
                  borderBottom: "1px solid #f8fafc",
                  backgroundColor:
                    selectedMeter?.meter_id === meter.meter_id ? "#eff6ff" : "#ffffff",
                }}
                onMouseEnter={(e) =>
                  ((e.currentTarget as HTMLDivElement).style.backgroundColor = "#f8fafc")
                }
                onMouseLeave={(e) =>
                  ((e.currentTarget as HTMLDivElement).style.backgroundColor =
                    selectedMeter?.meter_id === meter.meter_id ? "#eff6ff" : "#ffffff")
                }
              >
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <span style={{ fontWeight: 600, fontSize: "0.85rem", color: "#1e293b" }}>
                    {meter.meter_id}
                  </span>
                  <div style={{ display: "flex", gap: 6 }}>
                    {meter.feeder_id && (
                      <span
                        style={{
                          fontSize: "0.7rem",
                          background: "#dbeafe",
                          color: "#1d4ed8",
                          padding: "1px 6px",
                          borderRadius: 10,
                        }}
                      >
                        {meter.feeder_id}
                      </span>
                    )}
                    {meter.consumer_category && (
                      <span
                        style={{
                          fontSize: "0.7rem",
                          background: "#f1f5f9",
                          color: "#64748b",
                          padding: "1px 6px",
                          borderRadius: 10,
                        }}
                      >
                        {meter.consumer_category}
                      </span>
                    )}
                  </div>
                </div>
                {meter.zone && (
                  <div style={{ fontSize: "0.7rem", color: "#94a3b8", marginTop: 2 }}>
                    {meter.zone}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}

        {/* Close dropdown on outside click */}
        {dropdownOpen && (
          <div
            style={{ position: "fixed", inset: 0, zIndex: 99 }}
            onClick={() => setDropdownOpen(false)}
          />
        )}
      </div>

      {/* Error state */}
      {error && (
        <div
          style={{
            padding: "12px",
            background: "#fee2e2",
            borderRadius: 6,
            fontSize: "0.8rem",
            color: "#dc2626",
            marginBottom: 12,
          }}
        >
          Failed to load meter list. Is the API running?
        </div>
      )}

      {/* Selected meter info + chart */}
      {selectedMeter ? (
        <div>
          {/* Meter metadata card */}
          <div
            style={{
              background: "#f8fafc",
              border: "1px solid #e2e8f0",
              borderRadius: 8,
              padding: "10px 14px",
              marginBottom: 14,
              display: "grid",
              gridTemplateColumns: "1fr 1fr",
              gap: "4px 16px",
            }}
          >
            {[
              ["Meter ID", selectedMeter.meter_id],
              ["Feeder", selectedMeter.feeder_id ?? "—"],
              ["Zone", selectedMeter.zone ?? "—"],
              ["Category", selectedMeter.consumer_category ?? "—"],
              ["Sanctioned kVA", selectedMeter.sanctioned_kva != null ? `${selectedMeter.sanctioned_kva} kVA` : "—"],
            ].map(([label, value]) => (
              <div key={label}>
                <span style={{ fontSize: "0.7rem", color: "#94a3b8" }}>{label}: </span>
                <span style={{ fontSize: "0.8rem", fontWeight: 600, color: "#1e293b" }}>{value}</span>
              </div>
            ))}
          </div>

          {/* Chart */}
          <div style={{ fontSize: "0.8rem", fontWeight: 600, color: "#475569", marginBottom: 8 }}>
            14-Day Consumption
          </div>
          <MeterChart meterId={selectedMeter.meter_id} />
        </div>
      ) : (
        !isLoading && (
          <div
            style={{
              textAlign: "center",
              color: "#94a3b8",
              fontSize: "0.85rem",
              padding: "32px 16px",
            }}
          >
            <div style={{ fontSize: "1.5rem", marginBottom: 8 }}>🔍</div>
            <div>Search for a meter above to view its consumption history.</div>
            {meters && (
              <div style={{ marginTop: 6, fontSize: "0.75rem" }}>
                {meters.length} meters available
              </div>
            )}
          </div>
        )
      )}
    </div>
  );
}
