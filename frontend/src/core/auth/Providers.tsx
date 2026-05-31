// Admin-only OIDC provider configuration.
//
// Mirrors the Users page pattern: inline expanding form for "Add provider",
// inline per-row actions, react-query invalidation on mutation success.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Fragment, FormEvent, useState } from "react";

import { ApiError, OIDCProvider, OIDCProviderCreate, api } from "../../api";
import { Button, Card, CardHeader, Empty, Field, Input } from "../ui/ui";

export default function Providers() {
  const qc = useQueryClient();
  const providersQ = useQuery({
    queryKey: ["oidc-providers"],
    queryFn: api.listOIDCProviders,
  });
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<number | null>(null);

  const toggleEnabled = useMutation({
    mutationFn: (p: OIDCProvider) => api.updateOIDCProvider(p.id, { enabled: !p.enabled }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["oidc-providers"] }),
  });
  const del = useMutation({
    mutationFn: api.deleteOIDCProvider,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["oidc-providers"] }),
  });

  const providers = providersQ.data ?? [];

  return (
    <Card>
      <CardHeader
        title="Authentication providers"
        description="Single sign-on via OIDC. Add a provider (Authentik, Microsoft Entra, Keycloak, Google, etc.) and a 'Sign in with…' button appears on the login page. Invite-only: sign-in links to a pre-existing account by verified email — or, for a provider you mark Trusted, by email/UPN without email_verified (needed for Entra)."
        action={
          <Button variant="primary" onClick={() => setAdding((v) => !v)}>
            {adding ? "Cancel" : "Add provider"}
          </Button>
        }
      />
      {adding && <ProviderForm onDone={() => setAdding(false)} />}
      {providers.length === 0 ? (
        <Empty>
          No providers yet. Click "Add provider" to wire up OIDC. Users still
          sign in locally with username + password in the meantime.
        </Empty>
      ) : (
        <table className="w-full text-sm">
          <thead className="text-xs uppercase text-zinc-500 border-b border-zinc-800">
            <tr>
              <th className="text-left px-4 py-2 font-medium">Slug</th>
              <th className="text-left px-4 py-2 font-medium">Display name</th>
              <th className="text-left px-4 py-2 font-medium">Issuer</th>
              <th className="text-left px-4 py-2 font-medium">Client ID</th>
              <th className="text-left px-4 py-2 font-medium">Enabled</th>
              <th className="px-4 py-2"></th>
            </tr>
          </thead>
          <tbody>
            {providers.map((p) => (
              <Fragment key={p.id}>
                <tr className="border-b border-zinc-800/50 align-top">
                  <td className="px-4 py-2 text-zinc-100 font-mono text-xs">{p.slug}</td>
                  <td className="px-4 py-2 text-zinc-300">{p.display_name}</td>
                  <td className="px-4 py-2 text-zinc-500 text-xs break-all max-w-xs">{p.issuer}</td>
                  <td className="px-4 py-2 text-zinc-500 text-xs font-mono">{p.client_id.slice(0, 12)}…</td>
                  <td className="px-4 py-2 text-xs">
                    <button
                      onClick={() => toggleEnabled.mutate(p)}
                      className={`px-1.5 py-0.5 rounded border ${
                        p.enabled
                          ? "border-emerald-700 bg-emerald-900/30 text-emerald-300"
                          : "border-zinc-700 bg-zinc-800 text-zinc-400"
                      }`}
                    >
                      {p.enabled ? "on" : "off"}
                    </button>
                  </td>
                  <td className="px-4 py-2 text-right space-x-2 whitespace-nowrap">
                    <Button onClick={() => setEditing(editing === p.id ? null : p.id)}>
                      {editing === p.id ? "Close" : "Edit"}
                    </Button>
                    <Button variant="danger" onClick={() => del.mutate(p.id)}>
                      Delete
                    </Button>
                  </td>
                </tr>
                {editing === p.id && (
                  <tr className="bg-zinc-950/60">
                    <td colSpan={6} className="p-0">
                      <ProviderForm initial={p} onDone={() => setEditing(null)} />
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}

function ProviderForm({
  initial,
  onDone,
}: {
  initial?: OIDCProvider;
  onDone: () => void;
}) {
  const qc = useQueryClient();
  const [slug, setSlug] = useState(initial?.slug ?? "");
  const [displayName, setDisplayName] = useState(initial?.display_name ?? "");
  const [issuer, setIssuer] = useState(initial?.issuer ?? "");
  const [clientId, setClientId] = useState(initial?.client_id ?? "");
  const [clientSecret, setClientSecret] = useState("");
  const [scopes, setScopes] = useState(initial?.scopes ?? "openid email profile");
  const [enabled, setEnabled] = useState(initial?.enabled ?? true);
  const [trustedIdentity, setTrustedIdentity] = useState(
    initial?.trusted_identity ?? false,
  );
  const [err, setErr] = useState<string | null>(null);

  const save = useMutation({
    mutationFn: () => {
      if (initial) {
        // PATCH — only send changed fields; client_secret only when actually rotated
        return api.updateOIDCProvider(initial.id, {
          display_name: displayName || null,
          issuer: issuer || null,
          client_id: clientId || null,
          client_secret: clientSecret || null,
          scopes: scopes || null,
          enabled,
          trusted_identity: trustedIdentity,
        });
      }
      const body: OIDCProviderCreate = {
        slug,
        display_name: displayName,
        issuer,
        client_id: clientId,
        client_secret: clientSecret,
        scopes,
        enabled,
        trusted_identity: trustedIdentity,
      };
      return api.createOIDCProvider(body);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["oidc-providers"] });
      onDone();
    },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Save failed"),
  });

  function submit(e: FormEvent) {
    e.preventDefault();
    setErr(null);
    save.mutate();
  }

  return (
    <form onSubmit={submit} className="border-b border-zinc-800 bg-zinc-950/40 p-4 grid gap-3">
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <Field
          label="Slug"
          hint="URL-safe identifier (lowercase, hyphens). Appears in the OIDC callback URL. Cannot be changed after create."
        >
          <Input
            value={slug}
            onChange={(e) => setSlug(e.target.value)}
            required
            disabled={!!initial}
            pattern="[a-z0-9]+(-[a-z0-9]+)*"
            placeholder="entra"
          />
        </Field>
        <Field label="Display name" hint="Shown on the 'Sign in with…' button.">
          <Input
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            required
            placeholder="Microsoft"
          />
        </Field>
      </div>
      <Field
        label="Issuer URL"
        hint="Base URL — the app appends /.well-known/openid-configuration to discover endpoints."
      >
        <Input
          value={issuer}
          onChange={(e) => setIssuer(e.target.value)}
          required
          type="url"
          placeholder="https://login.microsoftonline.com/<tenant-id>/v2.0"
        />
      </Field>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <Field label="Client ID">
          <Input
            value={clientId}
            onChange={(e) => setClientId(e.target.value)}
            required
            placeholder="abc123-…"
          />
        </Field>
        <Field
          label={initial ? "Client secret (leave blank to keep existing)" : "Client secret"}
          hint="Stored encrypted at rest. Write-only — once saved, the value cannot be retrieved through the UI."
        >
          <Input
            type="password"
            value={clientSecret}
            onChange={(e) => setClientSecret(e.target.value)}
            required={!initial}
            autoComplete="new-password"
          />
        </Field>
      </div>
      <Field label="Scopes" hint="Space-separated. 'openid' is required and added automatically if missing.">
        <Input
          value={scopes}
          onChange={(e) => setScopes(e.target.value)}
          placeholder="openid email profile"
        />
      </Field>
      <label className="flex items-center gap-2 text-xs text-zinc-400">
        <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
        Enabled (provider appears on the login page)
      </label>

      <div className="rounded border border-amber-800/60 bg-amber-950/20 p-3">
        <label className="flex items-start gap-2 text-xs text-zinc-300">
          <input
            type="checkbox"
            className="mt-0.5"
            checked={trustedIdentity}
            onChange={(e) => setTrustedIdentity(e.target.checked)}
          />
          <span>
            <span className="text-amber-300 font-medium">
              Trusted identity — link without <code>email_verified</code>
            </span>
            <br />
            Lets a new sign-in link to a pre-invited account by matching the
            IdP's email / UPN even when it doesn't assert{" "}
            <code>email_verified</code> (e.g. Microsoft Entra, which never
            sends it). Still invite-only — a matching local account must
            already exist.{" "}
            <span className="text-amber-400">
              Enable ONLY for an IdP you control (your own tenant)
            </span>
            : on a multi-tenant or untrusted IdP this would allow account
            takeover by asserting someone else's address.
          </span>
        </label>
      </div>

      {err && <div className="text-xs text-rose-400">{err}</div>}

      <div className="flex gap-2">
        <Button type="submit" variant="primary" disabled={save.isPending}>
          {save.isPending ? "Saving…" : initial ? "Save changes" : "Add provider"}
        </Button>
        <Button type="button" onClick={onDone}>
          Cancel
        </Button>
      </div>
    </form>
  );
}
