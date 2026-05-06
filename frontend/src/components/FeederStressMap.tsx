import React from "react";
import { MapContainer, TileLayer, CircleMarker, Popup } from "react-leaflet";
import "leaflet/dist/leaflet.css";
import { useFeederStatus } from "../hooks/useFeederStatus";
import { useAlertStore } from "../stores/alertStore";
import type { FeederStatusResponse } from "../lib/api";

// Bangalore area feeder centroid coordinates (approximate, matching registry clusters)
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

function getFeederCoords(feederId: string): [number, number] {
  return FEEDER_COORDS[feederId] ?? [12.9716, 77.5946];
}

/**
 * FeederStressMap — react-leaflet choropleth map showing feeder stress levels.
 * Clicking a feeder filters the Alert Queue to that feeder.
 */
export default function FeederStressMap() {
  const { data: feeders, isLoading, error } = useFeederStatus();
  const setFeederFilter = useAlertStore((s) => s.setFeederFilter);
  const feederFilter = useAlertStore((s) => s.feederFilter);

  return (
    <div style={{ height: "100%", position: "relative" }}>
      {isLoading && (
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
          Loading feeders...
        </div>
      )}

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
                fillOpacity: 0.75,
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
          fontSize: "0.75rem",
          boxShadow: "0 1px 4px rgba(0,0,0,0.15)",
        }}
      >
        {Object.entries(STRESS_COLORS).map(([level, color]) => (
          <div
            key={level}
            style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 2 }}
          >
            <div
              style={{
                width: 12,
                height: 12,
                borderRadius: "50%",
                backgroundColor: color,
              }}
            />
            <span style={{ color: "#475569" }}>{level}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
