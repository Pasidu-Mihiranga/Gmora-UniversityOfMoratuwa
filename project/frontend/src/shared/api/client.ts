/**
 * Typed API client (API-3, FE-18). Types come from the backend's OpenAPI document.
 *
 * The access token lives in memory only. The refresh token is an HttpOnly cookie the browser sends
 * to /api/v1/auth/refresh, which also requires the CSRF header (SEC-4, SG-6). On a 401 the client
 * refreshes once and retries; if that fails the session ends.
 */
import createClient, { type Middleware } from "openapi-fetch";

import type { paths } from "./openapi";

let accessToken: string | null = null;
let refreshing: Promise<boolean> | null = null;
const sessionListeners = new Set<() => void>();

export const CSRF_HEADER = { "X-SuwaPath-Request": "1" } as const;

export function setAccessToken(token: string | null): void {
  accessToken = token;
}

export function onSessionEnded(listener: () => void): () => void {
  sessionListeners.add(listener);
  return () => sessionListeners.delete(listener);
}

async function refreshAccessToken(): Promise<boolean> {
  refreshing ??= fetch("/api/v1/auth/refresh", { method: "POST", credentials: "same-origin", headers: CSRF_HEADER })
    .then(async (response) => {
      if (!response.ok) return false;
      const body = (await response.json()) as { access_token?: string };
      setAccessToken(body.access_token ?? null);
      return Boolean(body.access_token);
    })
    .catch(() => false)
    .finally(() => {
      refreshing = null;
    });
  return refreshing;
}

const auth: Middleware = {
  onRequest({ request }) {
    if (accessToken) request.headers.set("Authorization", `Bearer ${accessToken}`);
    return request;
  },
  async onResponse({ request, response }) {
    if (response.status !== 401 || request.headers.get("X-Retried") === "1") return response;
    if (!(await refreshAccessToken())) {
      setAccessToken(null);
      sessionListeners.forEach((listener) => listener());
      return response;
    }
    const retry = new Request(request, { headers: new Headers(request.headers) });
    retry.headers.set("Authorization", `Bearer ${accessToken}`);
    retry.headers.set("X-Retried", "1");
    return fetch(retry);
  },
};

export const api = createClient<paths>({ baseUrl: "", credentials: "same-origin" });
api.use(auth);
