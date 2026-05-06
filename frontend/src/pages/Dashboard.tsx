import React from "react";
import FeederStressMap from "../components/FeederStressMap";
import AlertQueue from "../components/AlertQueue";
import AlertDetail from "../components/AlertDetail";
import DispatchPanel from "../components/DispatchPanel";
import { useAlertStore } from "../stores/alertStore";
import { useAlertDetail } from "../hooks/useAlertDetail";

/**
 * Dashboard — 3-panel layout:
 * Left (25%):  Feeder Stress Map
 * Center (35%): Alert Queue
 * Right (40%): Alert Detail + Dispatch Panel (renders when alert selected)
 */
export default function Dashboard() {
  const selectedAlertId = useAlertStore((s) => s.selectedAlertId);
  const { data: alertDetail, isLoading } = useAlertDetail(selectedAlertId);

  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "25% 35% 40%",
        height: "100vh",
        overflow: "hidden",
        backgroundColor: "#f1f5f9",
      }}
    >
      {/* Left panel — Feeder Stress Map */}
      <div
        style={{
          borderRight: "1px solid #e2e8f0",
          overflow: "hidden",
          display: "flex",
          flexDirection: "column",
        }}
      >
        <div className="panel-header">
          <h2 className="panel-title">Feeder Stress Map</h2>
        </div>
        <div style={{ flex: 1, overflow: "hidden" }}>
          <FeederStressMap />
        </div>
      </div>

      {/* Center panel — Alert Queue */}
      <div
        style={{
          borderRight: "1px solid #e2e8f0",
          overflow: "hidden",
          display: "flex",
          flexDirection: "column",
        }}
      >
        <AlertQueue />
      </div>

      {/* Right panel — Alert Detail + Dispatch */}
      <div
        style={{
          overflow: "auto",
          display: "flex",
          flexDirection: "column",
          backgroundColor: "#ffffff",
        }}
      >
        {!selectedAlertId ? (
          <div
            style={{
              flex: 1,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              color: "#94a3b8",
              fontSize: "0.9rem",
              padding: "2rem",
              textAlign: "center",
            }}
          >
            <div>
              <div style={{ fontSize: "2rem", marginBottom: "0.5rem" }}>⚡</div>
              <div>Select an alert from the queue to view details and take action.</div>
            </div>
          </div>
        ) : isLoading ? (
          <div
            style={{
              flex: 1,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              color: "#94a3b8",
            }}
          >
            <div>Loading alert details...</div>
          </div>
        ) : alertDetail ? (
          <>
            <AlertDetail alert={alertDetail} />
            <DispatchPanel alertId={alertDetail.alert_id} alertState={alertDetail.state} />
          </>
        ) : (
          <div
            style={{
              flex: 1,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              color: "#ef4444",
              padding: "2rem",
            }}
          >
            Failed to load alert details.
          </div>
        )}
      </div>
    </div>
  );
}
