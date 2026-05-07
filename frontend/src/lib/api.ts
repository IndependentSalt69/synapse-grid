/**
 * api.ts — Typed fetch wrappers for all Synapse-Grid API endpoints.
 */

const API_BASE = "/api/v1";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface ShapEntry {
  feature: string;
  value: number;
  plain_english: string;
}

export interface AlertSummary {
  alert_id: string;
  meter_id: string;
  alert_type: "THEFT_SUSPECT" | "LOAD_STRESS" | "HARDWARE_ISSUE";
  state: "NEW" | "UNDER_REVIEW" | "WATCHING" | "DISPATCHED" | "DISMISSED";
  anomaly_confidence: number;
  pattern_type: "SUSTAINED_DROP" | "RECURRING_DAILY_DIP" | "SPIKE" | null;
  triggered_at: string;
  feeder_id: string | null;
}

export interface AlertDetail extends AlertSummary {
  pct_deviation_from_baseline: number | null;
  pct_deviation_from_peer_median: number | null;
  pct_deviation_from_cluster_norm: number | null;
  z_score: number | null;
  shap_top3: ShapEntry[];
  peer_status_summary: { normal: number; elevated: number; anomalous: number };
  repeat_days_count: number;
  dispatch_action: string | null;
  dismiss_reason: string | null;
  resolved_at: string | null;
  resolver_id: string | null;
}

export interface ReadingPoint {
  timestamp: string;
  kwh: number | null;
  voltage: number | null;
  power_factor: number | null;
}

export interface BaselinePoint {
  hour_of_day: number;
  day_of_week: number;
  baseline_kwh: number;
}

export interface PeerStatus {
  meter_id: string;
  lat: number;
  lng: number;
  status: "normal" | "elevated" | "anomalous";
}

export interface ForecastPoint {
  timestamp: string;
  predicted_utilization_pct: number;
}

export interface FeederStatusResponse {
  feeder_id: string;
  current_utilization_pct: number;
  stress_level: "GREEN" | "AMBER" | "RED";
  transformer_rated_kva: number | null;
  forecast_24h: ForecastPoint[];
}

export type ActionType = "DISPATCH_LINEMAN" | "LOAD_BALANCE" | "DISMISS";
export type ReasonCode = "VACATION" | "PLANNED_OUTAGE" | "FALSE_POSITIVE" | "OTHER";

export interface ActionRequest {
  action: ActionType;
  reason_code?: ReasonCode;
  resolver_id: string;
}

// ---------------------------------------------------------------------------
// Core fetch helper
// ---------------------------------------------------------------------------

async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });

  if (!res.ok) {
    let detail = res.statusText;
    try {
      const err = await res.json();
      detail = err.detail ?? detail;
    } catch {
      // ignore JSON parse error
    }
    throw new Error(`API ${res.status}: ${detail}`);
  }

  return res.json() as Promise<T>;
}

// ---------------------------------------------------------------------------
// Alert endpoints
// ---------------------------------------------------------------------------

export interface AlertFilters {
  state?: string;
  alert_type?: string;
  feeder_id?: string;
}

export const fetchAlerts = (filters?: AlertFilters): Promise<AlertSummary[]> => {
  const qs = new URLSearchParams();
  if (filters?.state) qs.set("state", filters.state);
  if (filters?.alert_type) qs.set("alert_type", filters.alert_type);
  if (filters?.feeder_id) qs.set("feeder_id", filters.feeder_id);
  const query = qs.toString() ? `?${qs}` : "";
  return apiFetch<AlertSummary[]>(`/alerts${query}`);
};

export const fetchAlertDetail = (alertId: string): Promise<AlertDetail> =>
  apiFetch<AlertDetail>(`/alerts/${alertId}`);

export const submitAlertAction = (
  alertId: string,
  body: ActionRequest
): Promise<AlertDetail> =>
  apiFetch<AlertDetail>(`/alerts/${alertId}/action`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });

// ---------------------------------------------------------------------------
// Meter endpoints
// ---------------------------------------------------------------------------

export const fetchMeterReadings = (meterId: string): Promise<ReadingPoint[]> =>
  apiFetch<ReadingPoint[]>(`/meters/${meterId}/readings`);

export const fetchMeterBaseline = (meterId: string): Promise<BaselinePoint[]> =>
  apiFetch<BaselinePoint[]>(`/meters/${meterId}/baseline`);

export const fetchMeterPeers = (meterId: string): Promise<PeerStatus[]> =>
  apiFetch<PeerStatus[]>(`/meters/${meterId}/peers`);

export interface MeterInfo {
  meter_id: string;
  feeder_id: string | null;
  zone: string | null;
  consumer_category: string | null;
  sanctioned_kva: number | null;
  lat: number | null;
  lng: number | null;
}

export const fetchAllMeters = (): Promise<MeterInfo[]> =>
  apiFetch<MeterInfo[]>(`/meters`);

// ---------------------------------------------------------------------------
// Feeder endpoints
// ---------------------------------------------------------------------------

export const fetchFeeders = (): Promise<FeederStatusResponse[]> =>
  apiFetch<FeederStatusResponse[]>(`/feeders`);
