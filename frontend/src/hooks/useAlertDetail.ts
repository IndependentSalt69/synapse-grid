import { useQuery } from "@tanstack/react-query";
import { fetchAlertDetail, type AlertDetail } from "../lib/api";

/**
 * Fetch full alert detail for a given alert ID.
 * Only runs when alertId is non-null.
 */
export function useAlertDetail(alertId: string | null) {
  return useQuery<AlertDetail>({
    queryKey: ["alert", alertId],
    queryFn: () => fetchAlertDetail(alertId!),
    enabled: !!alertId,
    staleTime: 30 * 1000, // 30 seconds
  });
}
