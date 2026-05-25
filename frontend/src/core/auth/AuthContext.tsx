// AuthContext: holds the current user (or null) and exposes login/logout/refresh.
//
// All `current user` reads in the app go through useAuth() so we have one
// place to evict the user state on 401, force a redirect, etc.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, ReactNode, useContext } from "react";

import { api, ApiError, BootstrapStatus, User } from "../../api";

type AuthState = {
  user: User | null;
  isLoading: boolean;
  bootstrap: BootstrapStatus | undefined;
  isBootstrapLoading: boolean;
  refresh: () => Promise<void>;
  // Login may complete in one call OR require a TOTP code. The two-stage
  // flow returns "needs_totp"; the caller renders the code field and
  // calls login again with the code.
  login: (username: string, password: string, totp_code?: string) => Promise<"ok" | "needs_totp">;
  signupFirst: (username: string, email: string | null, password: string) => Promise<void>;
  logout: () => Promise<void>;
};

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();

  // bootstrap-status is the first read on app load. It tells us whether
  // to render the first-user setup screen or the login screen.
  const bootstrapQ = useQuery({
    queryKey: ["auth", "bootstrap"],
    queryFn: api.bootstrapStatus,
    staleTime: 60_000,
  });

  // /auth/me — null when 401, the user object when authenticated.
  // We swallow 401 silently so it doesn't show as an error to the user.
  const meQ = useQuery({
    queryKey: ["auth", "me"],
    queryFn: async () => {
      try {
        return await api.me();
      } catch (e) {
        if (e instanceof ApiError && e.status === 401) return null;
        throw e;
      }
    },
    staleTime: 30_000,
    retry: false,
    // Don't run /me while bootstrap is loading. If bootstrap says
    // needs_bootstrap=true we'll render the signup page anyway.
    enabled: bootstrapQ.isSuccess && !bootstrapQ.data?.needs_bootstrap,
  });

  const loginM = useMutation({
    mutationFn: ({ username, password, totp_code }: { username: string; password: string; totp_code?: string }) =>
      api.login({ username, password, totp_code: totp_code ?? null }),
    onSuccess: (result) => {
      // Only cache the user if we actually got one (i.e. login completed).
      // The needs_totp shape doesn't have an id.
      if (result && typeof result === "object" && "id" in result) {
        qc.setQueryData(["auth", "me"], result);
        qc.invalidateQueries({ queryKey: ["auth", "bootstrap"] });
      }
    },
  });

  const signupM = useMutation({
    mutationFn: ({ username, email, password }: { username: string; email: string | null; password: string }) =>
      api.signupFirst({ username, email, password }),
    onSuccess: (user) => {
      qc.setQueryData(["auth", "me"], user);
      qc.invalidateQueries({ queryKey: ["auth", "bootstrap"] });
    },
  });

  const logoutM = useMutation({
    mutationFn: () => api.logout(),
    onSuccess: () => {
      // Reset every cached query — the next user may have different access.
      qc.clear();
      qc.setQueryData(["auth", "me"], null);
    },
  });

  const state: AuthState = {
    user: meQ.data ?? null,
    isLoading: meQ.isLoading,
    bootstrap: bootstrapQ.data,
    isBootstrapLoading: bootstrapQ.isLoading,
    refresh: async () => {
      await Promise.all([bootstrapQ.refetch(), meQ.refetch()]);
    },
    login: async (username, password, totp_code) => {
      const r = await loginM.mutateAsync({ username, password, totp_code });
      if (r && typeof r === "object" && "needs_totp" in r) return "needs_totp";
      return "ok";
    },
    signupFirst: async (username, email, password) => {
      await signupM.mutateAsync({ username, email, password });
    },
    logout: async () => {
      await logoutM.mutateAsync();
    },
  };

  return <AuthContext.Provider value={state}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const v = useContext(AuthContext);
  if (!v) throw new Error("useAuth() must be used inside <AuthProvider>");
  return v;
}
