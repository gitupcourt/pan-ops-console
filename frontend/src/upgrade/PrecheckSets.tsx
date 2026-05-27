import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { NavLink } from "react-router-dom";

import {
  api,
  PrecheckSetCreate,
  PrecheckSetRead,
  PrecheckSetUpdate,
} from "../api";
import { Button, Card, CardHeader, Input } from "../core/ui/ui";

/**
 * Precheck-set CRUD page at `/upgrade/precheck-sets`.
 *
 * Phase 13b. The seed migration ships two presets ("Standard" + "Full");
 * this page lets operators add per-team / per-platform sets, tune
 * thresholds, and pick which one is the default for new jobs.
 *
 * Mirrors the structure of the Alerts /rules card from phase 12b —
 * same edit-inline pattern, same delete-with-confirm, same toggle
 * affordance. The novel piece is the check-name picker: each set
 * holds an ordered list of check names from the platform catalog,
 * so the editor shows a multi-select against `availableChecks.all`.
 */
export default function PrecheckSets() {
  const setsQ = useQuery({
    queryKey: ["precheck-sets"],
    queryFn: api.listPrecheckSets,
  });
  const availableQ = useQuery({
    queryKey: ["precheck-available"],
    queryFn: api.listAvailableChecks,
  });

  const [adding, setAdding] = useState(false);

  return (
    <div className="space-y-4">
      <div className="text-xs text-zinc-500">
        <NavLink to="/upgrade" className="text-blue-400 hover:text-blue-300">
          Jobs
        </NavLink>
        {" › "}
        <span>Pre/post-check sets</span>
      </div>

      <h2 className="text-xl font-semibold text-zinc-100">
        Pre/post-check sets
      </h2>

      <Card>
        <CardHeader
          title="Configured sets"
          description="Named ordered lists of readiness checks. New jobs default to whichever set is flagged default."
          action={
            !adding && (
              <button
                onClick={() => setAdding(true)}
                className="text-xs text-blue-400 hover:text-blue-300"
              >
                + Add set
              </button>
            )
          }
        />
        {adding && (
          <AddForm
            allChecks={availableQ.data?.all ?? []}
            defaultChecks={availableQ.data?.default ?? []}
            onClose={() => setAdding(false)}
          />
        )}
        {setsQ.isLoading ? (
          <div className="p-4 text-center text-xs text-zinc-500">Loading…</div>
        ) : (setsQ.data ?? []).length === 0 ? (
          <div className="p-4 text-center text-xs text-zinc-500">
            No sets configured. The seed migration normally creates two —
            if you're seeing this, something's wrong.
          </div>
        ) : (
          <ul className="divide-y divide-zinc-800/60">
            {(setsQ.data ?? []).map((s) => (
              <SetRow
                key={s.id}
                set={s}
                allChecks={availableQ.data?.all ?? []}
              />
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}

function SetRow({
  set: s,
  allChecks,
}: {
  set: PrecheckSetRead;
  allChecks: string[];
}) {
  const qc = useQueryClient();
  const [expanded, setExpanded] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draftName, setDraftName] = useState(s.name);
  const [draftDesc, setDraftDesc] = useState(s.description ?? "");
  const [draftChecks, setDraftChecks] = useState<string[]>(s.checks);

  const onSuccess = () => qc.invalidateQueries({ queryKey: ["precheck-sets"] });

  const updateMutation = useMutation({
    mutationFn: (body: PrecheckSetUpdate) => api.updatePrecheckSet(s.id, body),
    onSuccess,
  });
  const deleteMutation = useMutation({
    mutationFn: () => api.deletePrecheckSet(s.id),
    onSuccess,
  });

  const setAsDefault = () => updateMutation.mutate({ is_default: true });

  const save = () => {
    const patch: PrecheckSetUpdate = {};
    if (draftName !== s.name) patch.name = draftName;
    if ((draftDesc || null) !== (s.description ?? null))
      patch.description = draftDesc.trim() === "" ? null : draftDesc;
    if (
      JSON.stringify(draftChecks) !== JSON.stringify(s.checks)
    ) {
      patch.checks = draftChecks;
    }
    if (Object.keys(patch).length > 0) {
      updateMutation.mutate(patch, {
        onSuccess: () => {
          onSuccess();
          setEditing(false);
        },
      });
    } else {
      setEditing(false);
    }
  };

  const cancel = () => {
    setDraftName(s.name);
    setDraftDesc(s.description ?? "");
    setDraftChecks(s.checks);
    setEditing(false);
  };

  const remove = () => {
    if (
      window.confirm(
        `Delete precheck set "${s.name}"? Jobs that referenced it will fall back to the default set.`,
      )
    ) {
      deleteMutation.mutate();
    }
  };

  const toggleCheck = (name: string) => {
    setDraftChecks((cur) =>
      cur.includes(name) ? cur.filter((c) => c !== name) : [...cur, name],
    );
  };

  return (
    <li className="p-4 space-y-2">
      <div className="flex items-center gap-2 flex-wrap">
        <button
          onClick={() => setExpanded((v) => !v)}
          className="text-zinc-500 hover:text-zinc-300 text-xs w-3"
        >
          {expanded ? "▾" : "▸"}
        </button>
        {editing ? (
          <Input
            value={draftName}
            onChange={(e) => setDraftName(e.target.value)}
            className="text-sm w-48"
          />
        ) : (
          <span className="text-sm font-medium text-zinc-100">{s.name}</span>
        )}
        {s.is_default && (
          <span className="text-[10px] px-2 py-0.5 rounded border border-emerald-800/60 text-emerald-400 uppercase tracking-wider">
            default
          </span>
        )}
        <span className="text-xs text-zinc-500">
          {s.checks.length} check{s.checks.length === 1 ? "" : "s"}
        </span>
        <div className="ml-auto flex items-center gap-3 text-xs">
          {!editing && !s.is_default && (
            <button
              onClick={setAsDefault}
              disabled={updateMutation.isPending}
              className="text-zinc-400 hover:text-zinc-200"
            >
              set as default
            </button>
          )}
          {editing ? (
            <>
              <button
                onClick={save}
                disabled={updateMutation.isPending}
                className="text-blue-400 hover:text-blue-300 disabled:opacity-50"
              >
                save
              </button>
              <button onClick={cancel} className="text-zinc-500 hover:text-zinc-300">
                cancel
              </button>
            </>
          ) : (
            <>
              <button
                onClick={() => {
                  setEditing(true);
                  setExpanded(true);
                }}
                className="text-zinc-400 hover:text-zinc-200"
              >
                edit
              </button>
              <button
                onClick={remove}
                disabled={deleteMutation.isPending}
                className="text-rose-500 hover:text-rose-300 disabled:opacity-50"
              >
                delete
              </button>
            </>
          )}
        </div>
      </div>

      {editing ? (
        <Input
          value={draftDesc}
          onChange={(e) => setDraftDesc(e.target.value)}
          placeholder="Description (optional)"
          className="text-xs w-full"
        />
      ) : (
        s.description && (
          <div className="text-xs text-zinc-400 pl-5">{s.description}</div>
        )
      )}

      {expanded && (
        <div className="pl-5 pt-2 grid grid-cols-2 md:grid-cols-3 gap-1 text-xs">
          {(editing ? allChecks : s.checks).map((name) => {
            const isInSet = editing
              ? draftChecks.includes(name)
              : true; // in display mode we only render the set's own checks
            if (editing) {
              return (
                <label
                  key={name}
                  className="flex items-center gap-2 cursor-pointer text-zinc-300"
                >
                  <input
                    type="checkbox"
                    checked={isInSet}
                    onChange={() => toggleCheck(name)}
                  />
                  <span className="font-mono text-[11px]">{name}</span>
                </label>
              );
            }
            return (
              <span
                key={name}
                className="font-mono text-[11px] text-zinc-300 px-1.5 py-0.5 rounded bg-zinc-800/40"
              >
                {name}
              </span>
            );
          })}
        </div>
      )}
    </li>
  );
}

function AddForm({
  allChecks,
  defaultChecks,
  onClose,
}: {
  allChecks: string[];
  defaultChecks: string[];
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  // Seed the new set with the catalog defaults so operators rarely
  // start from scratch — the "Standard" preset is a sensible
  // starting point even for custom variants.
  const [checks, setChecks] = useState<string[]>(defaultChecks);
  const [isDefault, setIsDefault] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const createMutation = useMutation({
    mutationFn: (body: PrecheckSetCreate) => api.createPrecheckSet(body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["precheck-sets"] });
      onClose();
    },
    onError: (e: unknown) => {
      setError(e instanceof Error ? e.message : "Failed to create set");
    },
  });

  const toggleCheck = (n: string) =>
    setChecks((cur) =>
      cur.includes(n) ? cur.filter((c) => c !== n) : [...cur, n],
    );

  const submit = () => {
    setError(null);
    if (!name.trim()) {
      setError("Name is required.");
      return;
    }
    if (checks.length === 0) {
      setError("Pick at least one check.");
      return;
    }
    createMutation.mutate({
      name: name.trim(),
      description: description.trim() === "" ? null : description.trim(),
      checks,
      is_default: isDefault,
    });
  };

  return (
    <div className="px-4 py-3 border-b border-zinc-800 bg-zinc-900/40 space-y-3">
      <div className="flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1 text-[11px] text-zinc-400">
          Name
          <Input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. EU-DC tight"
            className="text-xs w-48"
          />
        </label>
        <label className="flex flex-col gap-1 text-[11px] text-zinc-400 flex-1 min-w-[200px]">
          Description (optional)
          <Input
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="Why this set exists"
            className="text-xs w-full"
          />
        </label>
        <label className="flex items-center gap-1.5 text-[11px] text-zinc-400 pb-1.5">
          <input
            type="checkbox"
            checked={isDefault}
            onChange={(e) => setIsDefault(e.target.checked)}
          />
          Make default
        </label>
      </div>

      <div className="grid gap-1">
        <div className="text-[11px] uppercase tracking-wider text-zinc-500 flex items-center gap-2">
          <span>Checks ({checks.length} selected)</span>
          <button
            type="button"
            onClick={() => setChecks(defaultChecks)}
            className="text-blue-400 hover:text-blue-300 normal-case tracking-normal"
          >
            reset to defaults
          </button>
          <button
            type="button"
            onClick={() => setChecks(allChecks)}
            className="text-blue-400 hover:text-blue-300 normal-case tracking-normal"
          >
            select all
          </button>
          <button
            type="button"
            onClick={() => setChecks([])}
            className="text-zinc-500 hover:text-zinc-300 normal-case tracking-normal"
          >
            clear
          </button>
        </div>
        <div className="grid grid-cols-2 md:grid-cols-3 gap-1">
          {allChecks.map((c) => (
            <label
              key={c}
              className="flex items-center gap-2 cursor-pointer text-zinc-300 text-xs"
            >
              <input
                type="checkbox"
                checked={checks.includes(c)}
                onChange={() => toggleCheck(c)}
              />
              <span className="font-mono text-[11px]">{c}</span>
            </label>
          ))}
        </div>
      </div>

      {error && <div className="text-xs text-rose-400">{error}</div>}

      <div className="flex items-center gap-2">
        <Button onClick={submit} disabled={createMutation.isPending}>
          {createMutation.isPending ? "Saving…" : "Add set"}
        </Button>
        <button onClick={onClose} className="text-xs text-zinc-500 hover:text-zinc-300">
          cancel
        </button>
      </div>
    </div>
  );
}

