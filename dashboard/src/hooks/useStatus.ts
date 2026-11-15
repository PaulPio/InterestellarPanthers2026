import { api } from "../api/client";
import type { Status } from "../api/types.gen";
import { useDataSource } from "../lib/useDataSource";
import { POLL_MS, STALE_MS, usePolling, type Polled } from "./usePolling";

/** `/api/status`, polled every 5 s; stale after 15 s without a success. */
export function useStatus(): Polled<Status> {
  const source = useDataSource();
  return usePolling((signal) => api.status(signal), POLL_MS, STALE_MS, source);
}
