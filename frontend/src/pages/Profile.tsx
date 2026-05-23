import { useMutation, useQueryClient } from "@tanstack/react-query";
import { QRCodeSVG } from "qrcode.react";
import { FormEvent, useState } from "react";

import { ApiError, api } from "../api";
import { useAuth } from "../auth";
import { Button, Card, CardHeader, Field, Input } from "../components/ui";

export default function Profile() {
  const { user } = useAuth();
  if (!user) return null;

  return (
    <div className="max-w-xl space-y-6">
      <Card>
        <CardHeader title="Account" description="Your basic account info." />
        <div className="p-4 text-sm space-y-2">
          <Row label="Username" value={user.username} />
          <Row label="Email" value={user.email ?? "—"} />
          <Row label="Role" value={user.is_admin ? "Admin" : "User"} />
          <Row
            label="Two-factor (TOTP)"
            value={
              user.totp_enabled ? (
                <span className="text-emerald-400">enabled</span>
              ) : (
                <span className="text-zinc-500">disabled</span>
              )
            }
          />
        </div>
      </Card>

      <TOTPCard />
      <ChangePasswordCard />
    </div>
  );
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex justify-between gap-4">
      <span className="text-zinc-500">{label}</span>
      <span className="text-zinc-100 tabular-nums">{value}</span>
    </div>
  );
}

// =====================================================================
// TOTP
//
// Three states:
//   "off"      — user hasn't enabled TOTP. Show "Enable" button.
//   "enrolling"— /auth/totp/setup returned a secret + URI. Show QR + code
//                input. User verifies.
//   "on"       — totp_enabled=true on the user object. Show disable form.
//
// Backup codes are surfaced once at the end of enrollment and never again.
// =====================================================================

type TOTPState =
  | { phase: "off" }
  | { phase: "enrolling"; secret: string; uri: string }
  | { phase: "backup_codes"; codes: string[] }
  | { phase: "on" };

function TOTPCard() {
  const { user, refresh } = useAuth();
  const qc = useQueryClient();
  const [state, setState] = useState<TOTPState>(
    user?.totp_enabled ? { phase: "on" } : { phase: "off" },
  );
  const [code, setCode] = useState("");
  const [err, setErr] = useState<string | null>(null);

  const startSetup = useMutation({
    mutationFn: api.totpSetup,
    onSuccess: (r) => {
      setState({ phase: "enrolling", secret: r.secret, uri: r.otpauth_uri });
      setErr(null);
    },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Setup failed"),
  });

  const verifyCode = useMutation({
    mutationFn: () => api.totpVerify(code),
    onSuccess: async (r) => {
      setCode("");
      setState({ phase: "backup_codes", codes: r.backup_codes });
      await refresh();
      qc.invalidateQueries({ queryKey: ["auth", "me"] });
    },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Verification failed"),
  });

  // ---- Render by phase ----

  if (state.phase === "off") {
    return (
      <Card>
        <CardHeader
          title="Two-factor authentication"
          description="Add a second factor via an authenticator app (Google Authenticator, Aegis, 1Password, Bitwarden, etc.). When enabled, sign-in requires both your password and a 6-digit code."
        />
        <div className="p-4 space-y-3">
          <Button
            variant="primary"
            disabled={startSetup.isPending}
            onClick={() => startSetup.mutate()}
          >
            {startSetup.isPending ? "Setting up…" : "Enable two-factor"}
          </Button>
          {err && <div className="text-xs text-rose-400">{err}</div>}
        </div>
      </Card>
    );
  }

  if (state.phase === "enrolling") {
    return (
      <Card>
        <CardHeader
          title="Scan + verify"
          description="Scan the QR with your authenticator app, then enter the 6-digit code it shows you."
        />
        <div className="p-4 space-y-4">
          <div className="flex flex-col items-center gap-3 bg-white rounded p-4 mx-auto w-fit">
            <QRCodeSVG value={state.uri} size={192} />
          </div>
          <div className="text-[11px] text-zinc-500 text-center">
            Can't scan? Manually enter this secret:
            <div className="font-mono text-zinc-300 mt-1 tracking-wider break-all">
              {state.secret}
            </div>
          </div>

          <form
            onSubmit={(e: FormEvent) => {
              e.preventDefault();
              setErr(null);
              verifyCode.mutate();
            }}
            className="space-y-3"
          >
            <Field label="6-digit code">
              <Input
                value={code}
                onChange={(e) => setCode(e.target.value)}
                inputMode="numeric"
                autoComplete="one-time-code"
                required
                placeholder="123456"
              />
            </Field>
            {err && <div className="text-xs text-rose-400">{err}</div>}
            <div className="flex gap-2">
              <Button type="submit" variant="primary" disabled={verifyCode.isPending}>
                {verifyCode.isPending ? "Verifying…" : "Verify and enable"}
              </Button>
              <Button type="button" onClick={() => setState({ phase: "off" })}>
                Cancel
              </Button>
            </div>
          </form>
        </div>
      </Card>
    );
  }

  if (state.phase === "backup_codes") {
    return (
      <Card>
        <CardHeader
          title="Save your backup codes"
          description="Use these if you lose access to your authenticator app. Each code works exactly once. They will not be shown again."
        />
        <div className="p-4 space-y-3">
          <div className="grid grid-cols-2 gap-2 p-3 rounded bg-zinc-900 font-mono text-sm">
            {state.codes.map((c) => (
              <div key={c} className="text-zinc-200 tracking-wider">{c}</div>
            ))}
          </div>
          <div className="text-[11px] text-zinc-500">
            Copy these into a password manager or write them down. Each is single-use; once
            consumed, they're gone for good (you can generate fresh ones by disabling and
            re-enrolling TOTP).
          </div>
          <Button variant="primary" onClick={() => setState({ phase: "on" })}>
            I've saved them
          </Button>
        </div>
      </Card>
    );
  }

  // phase === "on"
  return <TOTPDisableCard onDisabled={() => setState({ phase: "off" })} />;
}

function TOTPDisableCard({ onDisabled }: { onDisabled: () => void }) {
  const { refresh } = useAuth();
  const [password, setPassword] = useState("");
  const [err, setErr] = useState<string | null>(null);

  const disable = useMutation({
    mutationFn: () => api.totpDisable(password),
    onSuccess: async () => {
      setPassword("");
      await refresh();
      onDisabled();
    },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Disable failed"),
  });

  return (
    <Card>
      <CardHeader
        title="Two-factor authentication"
        description="TOTP is enabled. To disable, confirm with your password — this prevents someone with a hijacked session from silently stripping 2FA."
      />
      <form
        onSubmit={(e: FormEvent) => {
          e.preventDefault();
          setErr(null);
          disable.mutate();
        }}
        className="p-4 space-y-3"
      >
        <Field label="Password">
          <Input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            autoComplete="current-password"
          />
        </Field>
        {err && <div className="text-xs text-rose-400">{err}</div>}
        <Button type="submit" variant="danger" disabled={disable.isPending}>
          {disable.isPending ? "Disabling…" : "Disable two-factor"}
        </Button>
      </form>
    </Card>
  );
}

function ChangePasswordCard() {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [ok, setOk] = useState(false);

  const change = useMutation({
    mutationFn: () =>
      api.changePassword({ current_password: current, new_password: next }),
    onSuccess: () => {
      setOk(true);
      setCurrent("");
      setNext("");
      setConfirm("");
    },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Change failed"),
  });

  function submit(e: FormEvent) {
    e.preventDefault();
    setErr(null);
    setOk(false);
    if (next !== confirm) {
      setErr("New passwords don't match.");
      return;
    }
    change.mutate();
  }

  return (
    <Card>
      <CardHeader
        title="Change password"
        description="Changes here sign you out of every other session — the cookie on this device stays valid."
      />
      <form onSubmit={submit} className="p-4 space-y-3">
        <Field label="Current password">
          <Input
            type="password"
            value={current}
            onChange={(e) => setCurrent(e.target.value)}
            required
            autoComplete="current-password"
          />
        </Field>
        <Field
          label="New password"
          hint="At least 12 characters. Long passphrase &gt; short complicated."
        >
          <Input
            type="password"
            value={next}
            onChange={(e) => setNext(e.target.value)}
            required
            minLength={12}
            autoComplete="new-password"
          />
        </Field>
        <Field label="Confirm new password">
          <Input
            type="password"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            required
            minLength={12}
            autoComplete="new-password"
          />
        </Field>
        {err && <div className="text-xs text-rose-400">{err}</div>}
        {ok && <div className="text-xs text-emerald-400">Password updated. Other sessions signed out.</div>}
        <Button type="submit" variant="primary" disabled={change.isPending}>
          {change.isPending ? "Updating…" : "Change password"}
        </Button>
      </form>
    </Card>
  );
}
