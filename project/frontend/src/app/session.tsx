/**
 * Session state (SEC-4, SG-16). The access token is held in memory by the API client; the refresh
 * token is an HttpOnly cookie. On load the session is restored through /auth/refresh. Signing out
 * clears every cached query so nothing remains on a shared device.
 */
import { useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import { api, CSRF_HEADER, onSessionEnded, setAccessToken } from "@/shared/api/client";

export type Role = "patient" | "guardian" | "doctor" | "hospital_admin" | "system_admin";

export interface SessionUser {
  id: string;
  role: Role;
  display_name: string;
  language: "si" | "en";
  preview?: boolean; // development preview only, never a real session
}

interface Session {
  user: SessionUser | null;
  loading: boolean;
  signIn: (login: string, password: string) => Promise<boolean>;
  register: (input: Registration) => Promise<boolean>;
  signOut: () => Promise<void>;
  previewAs?: (role: Role) => void; // present only in development builds
}

export interface Registration {
  role: "patient" | "guardian"; // doctors and admins are created by an administrator
  display_name: string;
  login: string;
  password: string;
  language: "si" | "en";
}

const SessionContext = createContext<Session | null>(null);

export const HOME_BY_ROLE: Record<Role, string> = {
  patient: "/patient",
  guardian: "/guardian",
  doctor: "/doctor",
  hospital_admin: "/hospital",
  system_admin: "/admin",
};

async function loadMe(): Promise<SessionUser | null> {
  const { data, response } = await api.GET("/api/v1/me");
  return response.ok ? (data as unknown as SessionUser) : null;
}

export function SessionProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [user, setUser] = useState<SessionUser | null>(null);
  const [loading, setLoading] = useState(true);

  const clear = useCallback(() => {
    setAccessToken(null);
    setUser(null);
    queryClient.clear();
  }, [queryClient]);

  useEffect(() => onSessionEnded(clear), [clear]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const refreshed = await fetch("/api/v1/auth/refresh", { method: "POST", headers: CSRF_HEADER })
        .then(async (r) => (r.ok ? ((await r.json()) as { access_token?: string }) : null))
        .catch(() => null);
      if (refreshed?.access_token) {
        setAccessToken(refreshed.access_token);
        const me = await loadMe();
        if (!cancelled) setUser(me);
      }
      if (!cancelled) setLoading(false);
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const signIn = useCallback(async (login: string, password: string) => {
    const response = await fetch("/api/v1/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ login, password }),
    });
    if (!response.ok) return false;
    const body = (await response.json()) as { access_token?: string };
    setAccessToken(body.access_token ?? null);
    const me = await loadMe();
    setUser(me);
    return me !== null;
  }, []);

  const register = useCallback(async (input: Registration) => {
    const response = await fetch("/api/v1/auth/register", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    });
    if (!response.ok) return false;
    const body = (await response.json()) as { access_token?: string };
    setAccessToken(body.access_token ?? null);
    const me = await loadMe();
    setUser(me);
    return me !== null;
  }, []);

  const signOut = useCallback(async () => {
    if (!user?.preview) {
      // Through the API client, so the access token goes with it; the cookie ends the session.
      await api.POST("/api/v1/auth/logout").catch(() => undefined);
    }
    clear();
  }, [clear, user]);

  const value = useMemo<Session>(() => {
    const session: Session = { user, loading, signIn, register, signOut };
    if (import.meta.env.DEV) {
      // Lets teammates see every screen before the identity module lands. Removed from production builds.
      session.previewAs = (role) =>
        setUser({ id: `preview-${role}`, role, display_name: `Preview ${role}`, language: "si", preview: true });
    }
    return session;
  }, [user, loading, signIn, register, signOut]);

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): Session {
  const session = useContext(SessionContext);
  if (!session) throw new Error("useSession must be used inside SessionProvider");
  return session;
}
