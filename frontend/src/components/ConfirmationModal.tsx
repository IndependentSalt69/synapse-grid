import React from "react";
import type { ActionType, ReasonCode } from "../lib/api";

interface Props {
  action: ActionType;
  reasonCode: ReasonCode | null;
  meterId: string;
  onConfirm: () => void;
  onCancel: () => void;
}

const ACTION_LABELS: Record<ActionType, string> = {
  DISPATCH_LINEMAN: "Dispatch Lineman",
  LOAD_BALANCE: "Load Balance",
  DISMISS: "Dismiss",
};

const ACTION_COLORS: Record<ActionType, string> = {
  DISPATCH_LINEMAN: "#dc2626",
  LOAD_BALANCE: "#d97706",
  DISMISS: "#475569",
};

/**
 * ConfirmationModal — shown before any dispatch action is submitted.
 * Requires explicit confirmation; no auto-close.
 */
export default function ConfirmationModal({
  action,
  reasonCode,
  meterId,
  onConfirm,
  onCancel,
}: Props) {
  const actionLabel = ACTION_LABELS[action];
  const actionColor = ACTION_COLORS[action];

  return (
    /* Backdrop */
    <div
      style={{
        position: "fixed",
        inset: 0,
        backgroundColor: "rgba(0, 0, 0, 0.45)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 9999,
      }}
      onClick={onCancel}
    >
      {/* Dialog */}
      <div
        style={{
          backgroundColor: "#ffffff",
          borderRadius: 12,
          padding: "24px 28px",
          maxWidth: 400,
          width: "90%",
          boxShadow: "0 20px 60px rgba(0,0,0,0.2)",
        }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Title */}
        <h3
          style={{
            margin: "0 0 8px 0",
            fontSize: "1.05rem",
            fontWeight: 700,
            color: "#1e293b",
          }}
        >
          Confirm Action
        </h3>

        {/* Body */}
        <p style={{ margin: "0 0 16px 0", fontSize: "0.875rem", color: "#475569" }}>
          You are about to{" "}
          <strong style={{ color: actionColor }}>{actionLabel}</strong> for meter{" "}
          <strong>{meterId}</strong>.
        </p>

        {action === "DISMISS" && reasonCode && (
          <div
            style={{
              background: "#f8fafc",
              border: "1px solid #e2e8f0",
              borderRadius: 6,
              padding: "8px 12px",
              marginBottom: 16,
              fontSize: "0.8rem",
              color: "#475569",
            }}
          >
            Reason: <strong>{reasonCode.replace("_", " ")}</strong>
          </div>
        )}

        <p
          style={{
            margin: "0 0 20px 0",
            fontSize: "0.8rem",
            color: "#94a3b8",
          }}
        >
          This action is final and cannot be undone.
        </p>

        {/* Buttons */}
        <div style={{ display: "flex", gap: 10, justifyContent: "flex-end" }}>
          <button
            onClick={onCancel}
            style={{
              padding: "8px 20px",
              backgroundColor: "#f1f5f9",
              color: "#475569",
              border: "1px solid #e2e8f0",
              borderRadius: 6,
              fontSize: "0.875rem",
              fontWeight: 500,
              cursor: "pointer",
            }}
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            style={{
              padding: "8px 20px",
              backgroundColor: actionColor,
              color: "#ffffff",
              border: "none",
              borderRadius: 6,
              fontSize: "0.875rem",
              fontWeight: 600,
              cursor: "pointer",
            }}
          >
            Confirm {actionLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
