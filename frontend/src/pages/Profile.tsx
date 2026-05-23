// Per-user self-service: change password.
//
// TOTP enable/disable will land here in slice 2 (Phase 1.5).

import { useMutation } from "@tanstack/react-query";
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
                <span className="text-zinc-500">disabled (coming soon)</span>
              )
            }
          />
        </div>
      </Card>

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
