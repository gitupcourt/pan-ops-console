import { FormEvent, useState } from "react";

import { ApiError } from "../api";
import { useAuth } from "../auth";
import { Button, Field, Input } from "../components/ui";

export default function Login() {
  const { login, bootstrap } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setErr(null);
    setBusy(true);
    try {
      await login(username, password);
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

        {err && <div className="text-xs text-rose-400">{err}</div>}

        <Button type="submit" variant="primary" disabled={busy} className="w-full">
          {busy ? "Signing in…" : "Sign in"}
        </Button>

        {bootstrap?.oidc_providers && bootstrap.oidc_providers.length > 0 && (
          <div className="pt-4 border-t border-zinc-800 space-y-2">
            <div className="text-[11px] text-zinc-500 text-center">or sign in with</div>
            {bootstrap.oidc_providers.map((p) => (
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
