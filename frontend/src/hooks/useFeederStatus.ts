import { useQuery } from "@tanstack/react-query";
import { fetchFeeders, type FeederStatusResponse } from "../lib/api";

/**
 * Fetch all feeder statuses.
 * Polls every 5 minutes.
 */
export function useFeederStatus() {
  return useQuery<FeederStatusResponse[]>({
    queryKey: ["feeders"],
    queryFn: fetchFeeders,
    refetchInterval: 5 * 60 * 1000,
    staleTime: 60 * 1000,
  });
}
