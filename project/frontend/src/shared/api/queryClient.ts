import { QueryClient } from "@tanstack/react-query";

import { ApiError } from "./errors";

/** FE-33: explicit staleTime per query type; nothing is persisted in the browser. */
export const STALE = {
  reference: 60 * 60 * 1000, // specialties, programme catalogue
  search: 60 * 1000, // doctor search
  live: 0, // cases and alerts; kept fresh by SSE
} as const;

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: STALE.search,
      retry: (failureCount, error) =>
        !(error instanceof ApiError && (error.problem.status ?? 500) < 500) && failureCount < 2,
      refetchOnWindowFocus: false,
    },
  },
});
