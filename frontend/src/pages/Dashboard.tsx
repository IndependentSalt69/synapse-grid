import React, { useState, useEffect } from "react";
import FeederStressMap from "../components/FeederStressMap";
import AlertQueue from "../components/AlertQueue";
import AlertDetail from "../components/AlertDetail";
import DispatchPanel from "../components/DispatchPanel";
import MeterExplorer from "../components/MeterExplorer";
import { useAlertStore } from "../stores/alertStore";
import { useAlertDetail } from "../hooks/useAlertDetail";

type RightPanelTab = "alert" | "explorer";

/**
 * Dashboard — 3-panel layout:
 * Left (25%):  Feeder Stress Map
 * Center (35%): Alert Queue
 * Right (40%): Tab-switched between Alert Detail + Dispatch / Meter Explorer
 */
export default function Dashboard() {
  const [rightTab, setRightTab] = useState<RightPanelTab>("alert");
  const selectedAlertId = useAlertStore((s) => s.selectedAlertId);
  const explorerMeterId = useAlertStore((s) => s.explorerMeterId);
  const { data: alertDetail, isLoading } = useAlertDetail(selectedAlertId);

  // When a meter is selected from the map, switch to Explorer tab automatically
  useEffect(() => {
    if (explorerMeterId) {
      setRightTab("explorer");
    }
  }, [explorerMeterId]);

  // Switch to alert tab automatically when an alert is selected
  const handleAlertSelect = () => {
    setRightTab("alert");
  };

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
        <div
          style={{
            padding: "10px 16px",
            borderBottom: "1px solid #e2e8f0",
            backgroundColor: "#ffffff",
          }}
        >
          <h2 style={{ margin: 0, fontSize: "0.95rem", fontWeight: 700, color: "#1e293b" }}>
            Feeder Stress Map
          </h2>
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
        {/* Wrap AlertQueue so clicking an alert also switches the right tab */}
        <div
          style={{ flex: 1, overflow: "hidden", display: "flex", flexDirection: "column" }}
          onClick={handleAlertSelect}
        >
          <AlertQueue />
        </div>
      </div>

      {/* Right panel — tabbed: Alert Detail | Meter Explorer */}
      <div
        style={{
          overflow: "hidden",
          display: "flex",
          flexDirection: "column",
          backgroundColor: "#ffffff",
        }}
      >
        {/* Tab bar */}
        <div
          style={{
            display: "flex",
            borderBottom: "1px solid #e2e8f0",
            backgroundColor: "#f8fafc",
            flexShrink: 0,
          }}
        >
          <TabButton
            label="Alert Detail"
            icon="⚡"
            active={rightTab === "alert"}
            onClick={() => setRightTab("alert")}
            badge={selectedAlertId ? "1" : undefined}
          />
          <TabButton
            label="Meter Explorer"
            icon="🔍"
            active={rightTab === "explorer"}
            onClick={() => setRightTab("explorer")}
          />
        </div>

        {/* Tab content */}
        <div style={{ flex: 1, overflow: "auto" }}>
          {rightTab === "alert" ? (
            <>
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
                    height: "100%",
                  }}
                >
                  <div>
                    <div style={{ fontSize: "2rem", marginBottom: "0.5rem" }}>⚡</div>
                    <div>Select an alert from the queue to view details and take action.</div>
                    <div
                      style={{ marginTop: 12, fontSize: "0.8rem", color: "#cbd5e1", cursor: "pointer" }}
                      onClick={() => setRightTab("explorer")}
                    >
                      Or browse meters in the Meter Explorer →
                    </div>
                  </div>
                </div>
              ) : isLoading ? (
                <div
                  style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    color: "#94a3b8",
                    height: "100%",
                  }}
                >
                  Loading alert details...
                </div>
              ) : alertDetail ? (
                <>
                  <AlertDetail alert={alertDetail} />
                  <DispatchPanel alertId={alertDetail.alert_id} alertState={alertDetail.state} />
                </>
              ) : (
                <div
                  style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    color: "#ef4444",
                    padding: "2rem",
                    height: "100%",
                  }}
                >
                  Failed to load alert details.
                </div>
              )}
            </>
          ) : (
            <MeterExplorer />
          )}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// TabButton sub-component
// ---------------------------------------------------------------------------

function TabButton({
  label,
  icon,
  active,
  onClick,
  badge,
}: {
  label: string;
  icon: string;
  active: boolean;
  onClick: () => void;
  badge?: string;
}) {
  return (
    <button
      onClick={onClick}
      style={{
        flex: 1,
        padding: "10px 8px",
        border: "none",
        borderBottom: active ? "2px solid #3b82f6" : "2px solid transparent",
        backgroundColor: "transparent",
        color: active ? "#1d4ed8" : "#64748b",
        fontWeight: active ? 600 : 400,
        fontSize: "0.8rem",
        cursor: "pointer",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        gap: 5,
        transition: "color 0.1s",
      }}
    >
      <span>{icon}</span>
      <span>{label}</span>
      {badge && (
        <span
          style={{
            background: "#3b82f6",
            color: "#ffffff",
            borderRadius: 10,
            padding: "0 5px",
            fontSize: "0.65rem",
            fontWeight: 700,
            lineHeight: "16px",
          }}
        >
          {badge}
        </span>
      )}
    </button>
  );
}
