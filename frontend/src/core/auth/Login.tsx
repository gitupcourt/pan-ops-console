import { FormEvent, useState } from "react";

import { ApiError } from "../../api";
import { Button, Field, Input } from "../ui/ui";
import { useAuth } from "./AuthContext";

export default function Login() {
  const { login, bootstrap } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  // Two-stage TOTP flow: backend returns { needs_totp: true } after a
  // valid password, then expects a follow-up call with the code.
  const [needsTotp, setNeedsTotp] = useState(false);
  const [totpCode, setTotpCode] = useState("");
  // Surface OIDC error messages bounced back via ?oidc_error=
  const oidcErr = new URLSearchParams(window.location.search).get("oidc_error");
  const [err, setErr] = useState<string | null>(oidcErr);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setErr(null);
    setBusy(true);
    try {
      const result = await login(
        username,
        password,
        needsTotp ? totpCode : undefined,
      );
      if (result === "needs_totp") {
        setNeedsTotp(true);
      }
      // result === "ok" — AuthProvider has flipped user state; App will re-render.
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Sign in failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center p-4">
      <form onSubmit={submit} className="w-full max-w-sm space-y-5">
        <div className="text-center mb-4">
          <h1 className="text-xl font-semibold text-zinc-100">PAN Capacity Analyzer</h1>
          <p className="text-xs text-zinc-500 mt-1">Sign in to continue</p>
        </div>

        {!needsTotp && (
          <>
            <Field label="Username">
              <Input
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoComplete="username"
                autoFocus
                required
              />
            </Field>
            <Field label="Password">
              <Input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
                required
              />
            </Field>
          </>
        )}

        {needsTotp && (
          <>
            <div className="text-xs text-zinc-400">
              Signed in as <span className="text-zinc-200">{username}</span>. Enter the
              6-digit code from your authenticator, or one of your backup codes.
            </div>
            <Field label="Authenticator code">
              <Input
                value={totpCode}
                onChange={(e) => setTotpCode(e.target.value)}
                inputMode="numeric"
                autoComplete="one-time-code"
                autoFocus
                required
                placeholder="123456 or backup code"
              />
            </Field>
            <button
              type="button"
              className="text-[11px] text-zinc-500 hover:text-zinc-200"
              onClick={() => {
                setNeedsTotp(false);
                setTotpCode("");
                setErr(null);
              }}
            >
              ← back
            </button>
          </>
        )}

        {err && <div className="text-xs text-rose-400">{err}</div>}

        <Button type="submit" variant="primary" disabled={busy} className="w-full">
          {busy ? "Signing in…" : needsTotp ? "Verify" : "Sign in"}
        </Button>

        {!needsTotp && (bootstrap?.oidc_providers?.length ?? 0) > 0 && (
          <div className="pt-4 border-t border-zinc-800 space-y-2">
            <div className="text-[11px] text-zinc-500 text-center">or sign in with</div>
            {bootstrap!.oidc_providers.map((p) => (
              <Button
                key={p}
                type="button"
                className="w-full"
                onClick={() => {
                  window.location.href = `/api/auth/oidc/${encodeURIComponent(p)}/login`;
                }}
              >
                {p}
              </Button>
            ))}
          </div>
        )}
      </form>
    </div>
  );
}
