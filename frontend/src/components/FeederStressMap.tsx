import React, { useState } from "react";
import { MapContainer, TileLayer, CircleMarker, Popup } from "react-leaflet";
import { useQuery } from "@tanstack/react-query";
import "leaflet/dist/leaflet.css";
import { useFeederStatus } from "../hooks/useFeederStatus";
import { useAlertStore } from "../stores/alertStore";
import { fetchAllMeters, fetchAlerts, type FeederStatusResponse, type MeterInfo } from "../lib/api";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

// Feeder centroid coordinates — used for the large feeder stress markers
const FEEDER_COORDS: Record<string, [number, number]> = {
  F001: [12.9716, 77.5946],
  F002: [12.985, 77.6101],
  F003: [12.958, 77.641],
  F004: [12.9352, 77.6245],
  F005: [13.01, 77.55],
};

const STRESS_COLORS: Record<string, string> = {
  GREEN: "#22c55e",
  AMBER: "#f59e0b",
  RED: "#ef4444",
};

// Meter dot colors by alert status
const METER_STATUS_COLORS: Record<string, string> = {
  anomalous: "#ef4444",   // red — has active alert
  watching: "#f59e0b",    // amber — in WATCHING state
  normal: "#22c55e",      // green — no active alert
};

function getFeederCoords(feederId: string): [number, number] {
  return FEEDER_COORDS[feederId] ?? [12.9716, 77.5946];
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

/**
 * FeederStressMap — react-leaflet map with two overlays:
 * 1. Large feeder stress circles (GREEN/AMBER/RED by utilization %)
 * 2. Small meter dots (50 meters, colored by alert status)
 *
 * Clicking a feeder circle filters the Alert Queue.
 * Clicking a meter dot opens it in the Meter Explorer tab.
 */
export default function FeederStressMap() {
  const [showMeters, setShowMeters] = useState(true);

  const { data: feeders, isLoading: loadingFeeders } = useFeederStatus();
  const { data: meters } = useQuery({
    queryKey: ["all-meters"],
    queryFn: fetchAllMeters,
    staleTime: 10 * 60 * 1000,
  });
  const { data: alerts } = useQuery({
    queryKey: ["alerts"],
    queryFn: () => fetchAlerts(),
    staleTime: 60 * 1000,
  });

  const setFeederFilter = useAlertStore((s) => s.setFeederFilter);
  const feederFilter = useAlertStore((s) => s.feederFilter);
  const setExplorerMeter = useAlertStore((s) => s.setExplorerMeter);
  const explorerMeterId = useAlertStore((s) => s.explorerMeterId);

  // Build a lookup: meter_id → worst alert state
  const meterAlertStatus = React.useMemo(() => {
    const status: Record<string, "anomalous" | "watching" | "normal"> = {};
    if (!alerts) return status;
    for (const alert of alerts) {
      const current = status[alert.meter_id];
      if (alert.state === "NEW" || alert.state === "UNDER_REVIEW") {
        status[alert.meter_id] = "anomalous";
      } else if (alert.state === "WATCHING" && current !== "anomalous") {
        status[alert.meter_id] = "watching";
      } else if (!current) {
        status[alert.meter_id] = "normal";
      }
    }
    return status;
  }, [alerts]);

  return (
    <div style={{ height: "100%", position: "relative" }}>
      {/* Loading indicator */}
      {loadingFeeders && (
        <div
          style={{
            position: "absolute",
            top: 8,
            left: "50%",
            transform: "translateX(-50%)",
            zIndex: 1000,
            background: "rgba(255,255,255,0.9)",
            padding: "4px 12px",
            borderRadius: 4,
            fontSize: "0.75rem",
            color: "#64748b",
          }}
        >
          Loading...
        </div>
      )}

      {/* Active feeder filter badge */}
      {feederFilter && (
        <div
          style={{
            position: "absolute",
            top: 8,
            right: 8,
            zIndex: 1000,
            background: "rgba(255,255,255,0.95)",
            padding: "4px 10px",
            borderRadius: 4,
            fontSize: "0.75rem",
            color: "#3b82f6",
            cursor: "pointer",
            border: "1px solid #bfdbfe",
          }}
          onClick={() => setFeederFilter(null)}
        >
          Filter: {feederFilter} ✕
        </div>
      )}

      {/* Meter overlay toggle */}
      <div
        style={{
          position: "absolute",
          bottom: 90,
          left: 8,
          zIndex: 1000,
          background: "rgba(255,255,255,0.95)",
          padding: "5px 10px",
          borderRadius: 6,
          fontSize: "0.72rem",
          boxShadow: "0 1px 4px rgba(0,0,0,0.15)",
          cursor: "pointer",
          color: showMeters ? "#1d4ed8" : "#64748b",
          border: `1px solid ${showMeters ? "#bfdbfe" : "#e2e8f0"}`,
          userSelect: "none",
        }}
        onClick={() => setShowMeters((v) => !v)}
      >
        {showMeters ? "● Hide meters" : "○ Show meters"}
      </div>

      <MapContainer
        center={[12.9716, 77.5946]}
        zoom={12}
        style={{ height: "100%", width: "100%" }}
        zoomControl={true}
      >
        <TileLayer
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
          url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
        />

        {/* ── Meter dots overlay ── */}
        {showMeters &&
          meters
            ?.filter((m): m is MeterInfo & { lat: number; lng: number } =>
              m.lat != null && m.lng != null
            )
            .map((meter) => {
              const alertStatus = meterAlertStatus[meter.meter_id] ?? "normal";
              const color = METER_STATUS_COLORS[alertStatus];
              const isSelected = explorerMeterId === meter.meter_id;

              return (
                <CircleMarker
                  key={meter.meter_id}
                  center={[meter.lat, meter.lng]}
                  radius={isSelected ? 7 : 5}
                  pathOptions={{
                    color: isSelected ? "#1d4ed8" : color,
                    fillColor: color,
                    fillOpacity: 0.85,
                    weight: isSelected ? 2 : 1,
                  }}
                  eventHandlers={{
                    click: (e) => {
                      // Stop propagation so feeder click doesn't also fire
                      e.originalEvent.stopPropagation();
                      setExplorerMeter(meter.meter_id);
                    },
                  }}
                >
                  <Popup>
                    <div style={{ minWidth: 130 }}>
                      <div style={{ fontWeight: 600, fontSize: "0.85rem", marginBottom: 3 }}>
                        {meter.meter_id}
                      </div>
                      <div style={{ fontSize: "0.75rem", color: "#475569" }}>
                        Feeder: {meter.feeder_id ?? "—"}
                      </div>
                      <div style={{ fontSize: "0.75rem", color: "#475569" }}>
                        {meter.consumer_category ?? "—"} · {meter.zone ?? "—"}
                      </div>
                      <div
                        style={{
                          fontSize: "0.72rem",
                          marginTop: 4,
                          color: color,
                          fontWeight: 600,
                          textTransform: "uppercase",
                        }}
                      >
                        {alertStatus}
                      </div>
                      <div
                        style={{
                          fontSize: "0.72rem",
                          marginTop: 6,
                          color: "#3b82f6",
                          cursor: "pointer",
                        }}
                        onClick={() => setExplorerMeter(meter.meter_id)}
                      >
                        Open in Explorer →
                      </div>
                    </div>
                  </Popup>
                </CircleMarker>
              );
            })}

        {/* ── Feeder stress circles (rendered on top of meter dots) ── */}
        {feeders?.map((feeder: FeederStatusResponse) => {
          const coords = getFeederCoords(feeder.feeder_id);
          const color = STRESS_COLORS[feeder.stress_level] ?? "#94a3b8";
          const isSelected = feederFilter === feeder.feeder_id;

          return (
            <CircleMarker
              key={feeder.feeder_id}
              center={coords}
              radius={isSelected ? 22 : 18}
              pathOptions={{
                color: isSelected ? "#1d4ed8" : color,
                fillColor: color,
                fillOpacity: 0.45,
                weight: isSelected ? 3 : 2,
              }}
              eventHandlers={{
                click: () => {
                  setFeederFilter(
                    feederFilter === feeder.feeder_id ? null : feeder.feeder_id
                  );
                },
              }}
            >
              <Popup>
                <div style={{ minWidth: 140 }}>
                  <div style={{ fontWeight: 600, marginBottom: 4 }}>
                    {feeder.feeder_id}
                  </div>
                  <div style={{ fontSize: "0.85rem", color: "#475569" }}>
                    Utilization:{" "}
                    <strong>{feeder.current_utilization_pct.toFixed(1)}%</strong>
                  </div>
                  <div
                    style={{
                      fontSize: "0.8rem",
                      marginTop: 4,
                      color: color,
                      fontWeight: 600,
                    }}
                  >
                    {feeder.stress_level}
                  </div>
                  <div
                    style={{
                      fontSize: "0.75rem",
                      marginTop: 6,
                      color: "#3b82f6",
                      cursor: "pointer",
                    }}
                    onClick={() =>
                      setFeederFilter(
                        feederFilter === feeder.feeder_id ? null : feeder.feeder_id
                      )
                    }
                  >
                    {feederFilter === feeder.feeder_id
                      ? "Clear filter"
                      : "Filter alerts"}
                  </div>
                </div>
              </Popup>
            </CircleMarker>
          );
        })}
      </MapContainer>

      {/* Legend */}
      <div
        style={{
          position: "absolute",
          bottom: 24,
          left: 8,
          zIndex: 1000,
          background: "rgba(255,255,255,0.95)",
          padding: "8px 12px",
          borderRadius: 6,
          fontSize: "0.72rem",
          boxShadow: "0 1px 4px rgba(0,0,0,0.15)",
        }}
      >
        <div style={{ fontWeight: 600, color: "#475569", marginBottom: 4 }}>Feeders</div>
        {Object.entries(STRESS_COLORS).map(([level, color]) => (
          <div
            key={level}
            style={{ display: "flex", alignItems: "center", gap: 5, marginBottom: 2 }}
          >
            <div
              style={{
                width: 10,
                height: 10,
                borderRadius: "50%",
                backgroundColor: color,
                opacity: 0.7,
                border: "1px solid rgba(0,0,0,0.15)",
              }}
            />
            <span style={{ color: "#475569" }}>{level}</span>
          </div>
        ))}
        {showMeters && (
          <>
            <div style={{ fontWeight: 600, color: "#475569", marginTop: 6, marginBottom: 4 }}>
              Meters
            </div>
            {[
              ["anomalous", "#ef4444", "Active alert"],
              ["watching", "#f59e0b", "Watching"],
              ["normal", "#22c55e", "Normal"],
            ].map(([key, color, label]) => (
              <div
                key={key}
                style={{ display: "flex", alignItems: "center", gap: 5, marginBottom: 2 }}
              >
                <div
                  style={{
                    width: 8,
                    height: 8,
                    borderRadius: "50%",
                    backgroundColor: color,
                    border: "1px solid rgba(0,0,0,0.15)",
                  }}
                />
                <span style={{ color: "#475569" }}>{label}</span>
              </div>
            ))}
          </>
        )}
      </div>
    </div>
  );
}
