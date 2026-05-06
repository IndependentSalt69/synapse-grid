import { useQuery } from "@tanstack/react-query";
import {
  fetchMeterReadings,
  fetchMeterBaseline,
  fetchMeterPeers,
  type ReadingPoint,
  type BaselinePoint,
  type PeerStatus,
} from "../lib/api";

/**
 * Fetch last 14 days of 15-min readings for a meter.
 * Only runs when meterId is non-null.
 */
export function useMeterReadings(meterId: string | null) {
  return useQuery<ReadingPoint[]>({
    queryKey: ["readings", meterId],
    queryFn: () => fetchMeterReadings(meterId!),
    enabled: !!meterId,
    staleTime: 60 * 1000,
  });
}

/**
 * Fetch the 28-day rolling baseline for a meter.
 * Only runs when meterId is non-null.
 */
export function useMeterBaseline(meterId: string | null) {
  return useQuery<BaselinePoint[]>({
    queryKey: ["baseline", meterId],
    queryFn: () => fetchMeterBaseline(meterId!),
    enabled: !!meterId,
    staleTime: 5 * 60 * 1000, // Baseline changes slowly
  });
}

/**
 * Fetch peer meter IDs and their current status for a meter.
 * Only runs when meterId is non-null.
 */
export function useMeterPeers(meterId: string | null) {
  return useQuery<PeerStatus[]>({
    queryKey: ["peers", meterId],
    queryFn: () => fetchMeterPeers(meterId!),
    enabled: !!meterId,
    staleTime: 60 * 1000,
  });
}
