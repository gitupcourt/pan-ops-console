// Admin-only user management.
//
// Patterns mirror Inventory.tsx — inline expandable form for "invite user",
// inline per-row actions, optimistic-ish via react-query invalidations.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FormEvent, useState } from "react";

import { ApiError, api, User } from "../api";
import { useAuth } from "../auth";
import { Button, Card, CardHeader, Empty, Field, Input, Select } from "../components/ui";

export default function Users() {
  const { user: me } = useAuth();
  const qc = useQueryClient();
  const usersQ = useQuery({ queryKey: ["users"], queryFn: api.listUsers });
  const [adding, setAdding] = useState(false);

  const setActive = useMutation({
    mutationFn: ({ id, active }: { id: number; active: boolean }) =>
      api.setUserActive(id, active),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["users"] }),
  });
  const setAdmin = useMutation({
    mutationFn: ({ id, is_admin }: { id: number; is_admin: boolean }) =>
      api.setUserAdmin(id, is_admin),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["users"] }),
  });
  const del = useMutation({
    mutationFn: api.deleteUser,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["users"] }),
  });

  const users = usersQ.data ?? [];

  return (
    <Card>
      <CardHeader
        title="Users"
        description="Invite-only after the first admin. Deactivating a user kills their sessions immediately."
        action={
          <Button variant="primary" onClick={() => setAdding((v) => !v)}>
            {adding ? "Cancel" : "Invite user"}
          </Button>
        }
      />
      {adding && <InviteForm onDone={() => setAdding(false)} />}
      {users.length === 0 ? (
        <Empty>No users yet (shouldn't happen since you're signed in).</Empty>
      ) : (
        <table className="w-full text-sm">
          <thead className="text-xs uppercase text-zinc-500 border-b border-zinc-800">
            <tr>
              <th className="text-left px-4 py-2 font-medium">Username</th>
              <th className="text-left px-4 py-2 font-medium">Email</th>
              <th className="text-left px-4 py-2 font-medium">Role</th>
              <th className="text-left px-4 py-2 font-medium">Active</th>
              <th className="text-left px-4 py-2 font-medium">TOTP</th>
              <th className="text-left px-4 py-2 font-medium">Last sign-in</th>
              <th className="px-4 py-2"></th>
            </tr>
          </thead>
          <tbody>
            {users.map((u) => {
              const isSelf = me?.id === u.id;
              return (
                <tr key={u.id} className="border-b border-zinc-800/50">
                  <td className="px-4 py-2 text-zinc-100">
                    {u.username}
                    {isSelf && <span className="ml-2 text-[10px] text-zinc-500">(you)</span>}
                  </td>
                  <td className="px-4 py-2 text-zinc-400">{u.email ?? "—"}</td>
                  <td className="px-4 py-2 text-xs">
                    {u.is_admin ? (
                      <span className="text-amber-400">admin</span>
                    ) : (
                      <span className="text-zinc-400">user</span>
                    )}
                  </td>
                  <td className="px-4 py-2 text-xs">
                    {u.is_active ? (
                      <span className="text-emerald-400">yes</span>
                    ) : (
                      <span className="text-zinc-500">no</span>
                    )}
                  </td>
                  <td className="px-4 py-2 text-xs">
                    {u.totp_enabled ? (
                      <span className="text-emerald-400">on</span>
                    ) : (
                      <span className="text-zinc-500">off</span>
                    )}
                  </td>
                  <td className="px-4 py-2 text-xs text-zinc-500">
                    {u.last_login_at ? new Date(u.last_login_at).toLocaleString() : "never"}
                  </td>
                  <td className="px-4 py-2 text-right space-x-2 whitespace-nowrap">
                    <Button
                      onClick={() => setActive.mutate({ id: u.id, active: !u.is_active })}
                      disabled={isSelf && u.is_active /* can't deactivate yourself */}
                      title={isSelf && u.is_active ? "Can't deactivate yourself" : undefined}
                    >
                      {u.is_active ? "Deactivate" : "Activate"}
                    </Button>
                    <Button
                      onClick={() => setAdmin.mutate({ id: u.id, is_admin: !u.is_admin })}
                      disabled={isSelf && u.is_admin /* can't demote self */}
                      title={isSelf && u.is_admin ? "Ask another admin to demote you" : undefined}
                    >
                      {u.is_admin ? "Revoke admin" : "Make admin"}
                    </Button>
                    <Button
                      variant="danger"
                      onClick={() => del.mutate(u.id)}
                      disabled={isSelf}
                      title={isSelf ? "Can't delete yourself" : undefined}
                    >
                      Delete
                    </Button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </Card>
  );
}

function InviteForm({ onDone }: { onDone: () => void }) {
  const qc = useQueryClient();
  const [username, setUsername] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [isAdmin, setIsAdmin] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const create = useMutation({
    mutationFn: () =>
      api.createUser({
        username,
        email: email.trim() || null,
        password,
        is_admin: isAdmin,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["users"] });
      onDone();
    },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Create failed"),
  });

  function submit(e: FormEvent) {
    e.preventDefault();
    setErr(null);
    create.mutate();
  }

  return (
    <form onSubmit={submit} className="border-b border-zinc-800 bg-zinc-950/40 p-4 grid gap-3">
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        <Field label="Username">
          <Input
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            required
            minLength={3}
            autoFocus
          />
        </Field>
        <Field label="Email (optional)">
          <Input type="email" value={email} onChange={(e) => setEmail(e.target.value)} />
        </Field>
        <Field
          label="Role"
        >
          <Select
            value={isAdmin ? "admin" : "user"}
            onChange={(e) => setIsAdmin(e.target.value === "admin")}
          >
            <option value="user">User</option>
            <option value="admin">Admin</option>
          </Select>
        </Field>
      </div>
      <Field
        label="Temporary password"
        hint="At least 12 characters. Share with the new user out-of-band; they should change it on first sign-in."
      >
        <Input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
          minLength={12}
          autoComplete="new-password"
        />
      </Field>

      {err && <div className="text-xs text-rose-400">{err}</div>}

      <div className="flex gap-2">
        <Button type="submit" variant="primary" disabled={create.isPending}>
          {create.isPending ? "Inviting…" : "Invite"}
        </Button>
        <Button type="button" onClick={onDone}>
          Cancel
        </Button>
      </div>
    </form>
  );
}
