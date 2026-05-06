import { useQuery } from "@tanstack/react-query";
import { fetchAlerts, type AlertSummary, type AlertFilters } from "../lib/api";
import { useAlertStore } from "../stores/alertStore";

/**
 * Fetch the alert queue, filtered by the current feeder filter from the store.
 * Polls every 5 minutes.
 */
export function useAlertQueue(extraFilters?: AlertFilters) {
  const feederFilter = useAlertStore((s) => s.feederFilter);

  return useQuery<AlertSummary[]>({
    queryKey: ["alerts", feederFilter, extraFilters],
    queryFn: () =>
      fetchAlerts({
        feeder_id: feederFilter ?? undefined,
        ...extraFilters,
      }),
    refetchInterval: 5 * 60 * 1000, // 5 minutes
    staleTime: 60 * 1000,           // 1 minute
  });
}
