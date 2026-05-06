import React from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { submitAlertAction, type ActionType, type ReasonCode } from "../lib/api";
import { useAlertStore } from "../stores/alertStore";
import ConfirmationModal from "./ConfirmationModal";

interface Props {
  alertId: string;
  alertState: string;
}

const REASON_CODES: { value: ReasonCode; label: string }[] = [
  { value: "VACATION", label: "Vacation" },
  { value: "PLANNED_OUTAGE", label: "Planned Outage" },
  { value: "FALSE_POSITIVE", label: "False Positive" },
  { value: "OTHER", label: "Other" },
];

/**
 * DispatchPanel — three-action gate with mandatory reason code for DISMISS.
 *
 * Business rules enforced:
 * - Submit button disabled until an action is selected
 * - DISMISS requires a reason code before submit is enabled
 * - Confirmation modal shown before any action is submitted
 * - Terminal states (DISPATCHED/DISMISSED) disable all buttons
 */
export default function DispatchPanel({ alertId, alertState }: Props) {
  const {
    pendingAction,
    pendingReasonCode,
    showConfirmModal,
    setPendingAction,
    setPendingReasonCode,
    openConfirmModal,
    closeConfirmModal,
    resetDispatch,
  } = useAlertStore();

  const queryClient = useQueryClient();

  const isTerminal = alertState === "DISPATCHED" || alertState === "DISMISSED";

  const mutation = useMutation({
    mutationFn: () =>
      submitAlertAction(alertId, {
        action: pendingAction!,
        reason_code: pendingReasonCode ?? undefined,
        resolver_id: "dispatcher-001", // Hardcoded for prototype (no auth)
      }),
    onSuccess: () => {
      resetDispatch();
      queryClient.invalidateQueries({ queryKey: ["alerts"] });
      queryClient.invalidateQueries({ queryKey: ["alert", alertId] });
    },
    onError: (err: Error) => {
      alert(`Action failed: ${err.message}`);
      closeConfirmModal();
    },
  });

  // Submit button disabled conditions
  const submitDisabled =
    !pendingAction ||
    (pendingAction === "DISMISS" && !pendingReasonCode) ||
    mutation.isPending;

  function handleActionClick(action: ActionType) {
    setPendingAction(action);
  }

  function handleSubmit() {
    openConfirmModal();
  }

  function handleConfirm() {
    closeConfirmModal();
    mutation.mutate();
  }

  return (
    <div
      style={{
        borderTop: "1px solid #e2e8f0",
        padding: "16px",
        backgroundColor: "#f8fafc",
      }}
    >
      <div
        style={{
          fontSize: "0.8rem",
          fontWeight: 700,
          color: "#475569",
          marginBottom: 12,
        }}
      >
        Dispatch Action
      </div>

      {/* Three action buttons */}
      <div style={{ display: "flex", gap: 8, marginBottom: 12 }}>
        <ActionButton
          label="Dispatch Lineman"
          action="DISPATCH_LINEMAN"
          selected={pendingAction === "DISPATCH_LINEMAN"}
          disabled={isTerminal}
          activeColor="#dc2626"
          activeBg="#fee2e2"
          onClick={() => handleActionClick("DISPATCH_LINEMAN")}
        />
        <ActionButton
          label="Load Balance"
          action="LOAD_BALANCE"
          selected={pendingAction === "LOAD_BALANCE"}
          disabled={isTerminal}
          activeColor="#d97706"
          activeBg="#fef3c7"
          onClick={() => handleActionClick("LOAD_BALANCE")}
        />
        <ActionButton
          label="Dismiss"
          action="DISMISS"
          selected={pendingAction === "DISMISS"}
          disabled={isTerminal}
          activeColor="#475569"
          activeBg="#f1f5f9"
          onClick={() => handleActionClick("DISMISS")}
        />
      </div>

      {/* Reason code dropdown — only shown when DISMISS selected */}
      {pendingAction === "DISMISS" && (
        <div style={{ marginBottom: 12 }}>
          <select
            value={pendingReasonCode ?? ""}
            onChange={(e) =>
              setPendingReasonCode((e.target.value as ReasonCode) || null)
            }
            style={{
              width: "100%",
              padding: "8px 12px",
              border: `1px solid ${pendingReasonCode ? "#94a3b8" : "#fca5a5"}`,
              borderRadius: 6,
              fontSize: "0.85rem",
              color: pendingReasonCode ? "#1e293b" : "#94a3b8",
              backgroundColor: "#ffffff",
            }}
          >
            <option value="">Select reason code...</option>
            {REASON_CODES.map((rc) => (
              <option key={rc.value} value={rc.value}>
                {rc.label}
              </option>
            ))}
          </select>
          {!pendingReasonCode && (
            <div style={{ fontSize: "0.7rem", color: "#ef4444", marginTop: 4 }}>
              Reason code required for DISMISS
            </div>
          )}
        </div>
      )}

      {/* Submit button */}
      {!isTerminal && (
        <button
          disabled={submitDisabled}
          onClick={handleSubmit}
          style={{
            width: "100%",
            padding: "10px",
            backgroundColor: submitDisabled ? "#e2e8f0" : "#3b82f6",
            color: submitDisabled ? "#94a3b8" : "#ffffff",
            border: "none",
            borderRadius: 6,
            fontSize: "0.875rem",
            fontWeight: 600,
            cursor: submitDisabled ? "not-allowed" : "pointer",
            transition: "background-color 0.15s",
          }}
        >
          {mutation.isPending ? "Submitting..." : "Submit Action"}
        </button>
      )}

      {/* Terminal state display */}
      {isTerminal && (
        <div
          style={{
            padding: "10px 12px",
            background: "#f1f5f9",
            borderRadius: 6,
            fontSize: "0.8rem",
            color: "#64748b",
            textAlign: "center",
          }}
        >
          Alert {alertState.toLowerCase()}. No further actions available.
        </div>
      )}

      {/* Confirmation modal */}
      {showConfirmModal && pendingAction && (
        <ConfirmationModal
          action={pendingAction}
          reasonCode={pendingReasonCode}
          meterId={alertId}
          onConfirm={handleConfirm}
          onCancel={closeConfirmModal}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// ActionButton sub-component
// ---------------------------------------------------------------------------

interface ActionButtonProps {
  label: string;
  action: ActionType;
  selected: boolean;
  disabled: boolean;
  activeColor: string;
  activeBg: string;
  onClick: () => void;
}

function ActionButton({
  label,
  selected,
  disabled,
  activeColor,
  activeBg,
  onClick,
}: ActionButtonProps) {
  return (
    <button
      disabled={disabled}
      onClick={onClick}
      style={{
        flex: 1,
        padding: "8px 4px",
        backgroundColor: selected ? activeBg : "#ffffff",
        color: selected ? activeColor : "#64748b",
        border: `1px solid ${selected ? activeColor : "#e2e8f0"}`,
        borderRadius: 6,
        fontSize: "0.75rem",
        fontWeight: selected ? 700 : 500,
        cursor: disabled ? "not-allowed" : "pointer",
        opacity: disabled ? 0.5 : 1,
        transition: "all 0.1s",
      }}
    >
      {label}
    </button>
  );
}
