/**
 * alertStore.ts — Zustand store for alert UI state and dispatch workflow.
 *
 * Manages:
 * - Selected alert ID
 * - Feeder filter (from map click)
 * - Dispatch workflow state (pending action, reason code, confirmation modal)
 *
 * State transitions enforced:
 * - selectAlert() resets all dispatch state
 * - setPendingAction() clears reason code when action changes
 * - Terminal states (DISPATCHED/DISMISSED) are enforced in DispatchPanel
 */

import { create } from "zustand";

export type ActionType = "DISPATCH_LINEMAN" | "LOAD_BALANCE" | "DISMISS";
export type ReasonCode = "VACATION" | "PLANNED_OUTAGE" | "FALSE_POSITIVE" | "OTHER";

interface AlertStore {
  // Selection state
  selectedAlertId: string | null;
  feederFilter: string | null;

  // Dispatch workflow state
  pendingAction: ActionType | null;
  pendingReasonCode: ReasonCode | null;
  showConfirmModal: boolean;

  // Actions
  selectAlert: (id: string) => void;
  clearSelection: () => void;
  setFeederFilter: (feederId: string | null) => void;
  setPendingAction: (action: ActionType | null) => void;
  setPendingReasonCode: (code: ReasonCode | null) => void;
  openConfirmModal: () => void;
  closeConfirmModal: () => void;
  resetDispatch: () => void;
}

export const useAlertStore = create<AlertStore>((set) => ({
  // Initial state
  selectedAlertId: null,
  feederFilter: null,
  pendingAction: null,
  pendingReasonCode: null,
  showConfirmModal: false,

  // Select an alert — resets all dispatch state
  selectAlert: (id: string) =>
    set({
      selectedAlertId: id,
      pendingAction: null,
      pendingReasonCode: null,
      showConfirmModal: false,
    }),

  // Clear selection
  clearSelection: () =>
    set({
      selectedAlertId: null,
      pendingAction: null,
      pendingReasonCode: null,
      showConfirmModal: false,
    }),

  // Set feeder filter from map click
  setFeederFilter: (feederId: string | null) =>
    set({ feederFilter: feederId }),

  // Set pending action — clears reason code when action changes
  setPendingAction: (action: ActionType | null) =>
    set({
      pendingAction: action,
      pendingReasonCode: null, // always clear reason code on action change
    }),

  // Set reason code (only relevant when action=DISMISS)
  setPendingReasonCode: (code: ReasonCode | null) =>
    set({ pendingReasonCode: code }),

  // Open confirmation modal
  openConfirmModal: () => set({ showConfirmModal: true }),

  // Close confirmation modal
  closeConfirmModal: () => set({ showConfirmModal: false }),

  // Reset all dispatch state after successful submission or cancel
  resetDispatch: () =>
    set({
      pendingAction: null,
      pendingReasonCode: null,
      showConfirmModal: false,
    }),
}));
