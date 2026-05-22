import { FormEvent, useState } from "react";

import { ApiError } from "../api";
import { useAuth } from "../auth";
import { Button, Field, Input } from "../components/ui";

// First-user setup page. Shown when /auth/bootstrap-status returns
// needs_bootstrap=true. The created user is auto-admin and auto-logged-in.
//
// If an OIDC provider is configured, we surface a "Sign in with $provider"
// option too — that user becomes admin on first login. The local form
// remains as the always-available fallback in case OIDC isn't reachable.

export default function Bootstrap() {
  const { signupFirst, bootstrap } = useAuth();
  const [username, setUsername] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setErr(null);
    if (password !== confirm) {
      setErr("Passwords don't match.");
      return;
    }
    setBusy(true);
    try {
      await signupFirst(username, email.trim() || null, password);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Sign up failed");
    } finally {
      setBusy(false);
    }
  }

  const hasOidc = (bootstrap?.oidc_providers?.length ?? 0) > 0;

  return (
    <div className="min-h-screen flex items-center justify-center p-4">
      <div className="w-full max-w-sm space-y-5">
        <div className="text-center">
          <h1 className="text-xl font-semibold text-zinc-100">Welcome</h1>
          <p className="text-xs text-zinc-500 mt-1">
            Fresh install — create the first admin account.
          </p>
        </div>

        {hasOidc && (
          <>
            <div className="space-y-2">
              {bootstrap!.oidc_providers.map((p) => (
                <Button
                  key={p}
                  type="button"
                  className="w-full"
                  variant="primary"
                  onClick={() => {
                    window.location.href = `/api/auth/oidc/${encodeURIComponent(p)}/login`;
                  }}
                >
                  Set up admin via {p}
                </Button>
              ))}
            </div>
            <div className="flex items-center gap-3 text-[11px] text-zinc-500">
              <div className="flex-1 border-t border-zinc-800" />
              or use a local account
              <div className="flex-1 border-t border-zinc-800" />
            </div>
          </>
        )}

        <form onSubmit={submit} className="space-y-3">
          <Field label="Username">
            <Input
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
              required
              minLength={3}
              autoFocus
            />
          </Field>
          <Field label="Email (optional)">
            <Input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoComplete="email"
            />
          </Field>
          <Field
            label="Password"
            hint="At least 12 characters. Use a passphrase — a few uncommon words beat any 'P@ssw0rd!'-style trick."
          >
            <Input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="new-password"
              required
              minLength={12}
            />
          </Field>
          <Field label="Confirm password">
            <Input
              type="password"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              autoComplete="new-password"
              required
              minLength={12}
            />
          </Field>

          {err && <div className="text-xs text-rose-400">{err}</div>}

          <Button type="submit" variant="primary" disabled={busy} className="w-full">
            {busy ? "Creating account…" : "Create admin account"}
          </Button>
        </form>

        <p className="text-[11px] text-zinc-600 text-center">
          After this, additional accounts are invite-only by an admin.
        </p>
      </div>
    </div>
  );
}
