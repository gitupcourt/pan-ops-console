import { useMutation } from "@tanstack/react-query";
import {
  AlertTriangle,
  CheckCircle2,
  MinusCircle,
  Network,
  XCircle,
} from "lucide-react";
import { api } from "@/lib/api";

/**
 * "Test connectivity" panel on the expanded device row.
 *
 * Runs from inside the backend container (the same network namespace the
 * worker uses for pre-checks). The four steps map 1:1 to where things
 * typically break when "I can hit the firewall from my browser but the
 * app can't":
 *
 *   - DNS:  the container's resolver may be different from the host's
 *           (Docker Desktop on Windows / WSL2 is the classic offender)
 *   - TCP:  RST means firewall mgmt-profile is rejecting THIS source IP
 *           (the desktop and the docker bridge are different source IPs)
 *   - TLS:  cert verification — handled separately from network so the
 *           operator can flip verify_tls without confusing it with a
 *           network issue
 *   - API:  credential / role / API access
 *
 * Each step shows latency too. A failing TCP step with <50 ms latency is
 * an immediate RST (almost certainly an ACL); a failing TCP with >2000 ms
 * is a silent drop (routing / VLAN). That distinction matters.
 */
type Step = {
  name: "host" | "dns" | "tcp" | "tls" | "api";
  status: "pass" | "fail" | "skip";
  message: string;
  latency_ms: number | null;
  detail?: string | null;
};

const STEP_LABEL: Record<Step["name"], string> = {
  host: "Host",
  dns: "DNS lookup",
  tcp: "TCP /443",
  tls: "TLS handshake",
  api: "PAN-OS API",
};

type DnsResult = {
  host: string;
  resolved_ip: string | null;
  error: string | null;
  collision_hint: string | null;
  resolver_state: { resolv_conf?: string; hosts?: string };
};

export function ConnectivityPanel({ deviceId }: { deviceId: number }) {
  const test = useMutation({
    mutationFn: () =>
      api<{ steps: Step[] }>(`/api/devices/${deviceId}/connectivity-test`, {
        method: "POST",
      }),
  });
  // Cheap, DNS-only mutation. Used when the operator is flushing caches
  // (Docker Desktop restart, AD DNS refresh) and wants to confirm the new
  // record without waiting for the full TCP/TLS/API path to time out.
  const dns = useMutation({
    mutationFn: () =>
      api<DnsResult>(`/api/devices/${deviceId}/dns-resolve`, { method: "POST" }),
  });

  return (
    <div className="mt-4 rounded-md border border-slate-800 bg-slate-950 p-3">
      <div className="mb-2 flex items-center justify-between">
        <div className="flex items-center gap-2 text-sm">
          <Network className="h-4 w-4 text-teal-400" />
          <span className="font-medium">Connectivity test</span>
          <span className="text-xs text-slate-500">
            DNS → TCP → TLS → API, run from the backend container
          </span>
        </div>
        <div className="flex items-center gap-1">
          <button
            onClick={() => dns.mutate()}
            disabled={dns.isPending}
            className="rounded border border-slate-700 px-2 py-0.5 text-xs hover:bg-slate-800 disabled:opacity-50"
            title="DNS lookup only — fast, repeatable. Use while flushing DNS caches."
          >
            {dns.isPending ? "Resolving…" : "Resolve only"}
          </button>
          <button
            onClick={() => test.mutate()}
            disabled={test.isPending}
            className="rounded border border-slate-700 px-2 py-0.5 text-xs hover:bg-slate-800 disabled:opacity-50"
          >
            {test.isPending ? "Testing…" : "Run full test"}
          </button>
        </div>
      </div>

      {dns.data && (
        <div className="mb-2 rounded border border-slate-800/60 bg-slate-900/40 px-2 py-1.5 text-xs">
          <div className="font-medium text-slate-200">
            {dns.data.host} → {dns.data.resolved_ip ?? "(no answer)"}
            {dns.data.error && (
              <span className="ml-2 text-red-400">{dns.data.error}</span>
            )}
          </div>
          {dns.data.collision_hint && (
            <div className="mt-1 whitespace-pre-wrap text-amber-300">
              {dns.data.collision_hint}
            </div>
          )}
          {dns.data.resolver_state.resolv_conf && (
            <details className="mt-1">
              <summary className="cursor-pointer text-slate-500">
                /etc/resolv.conf
              </summary>
              <pre className="mt-0.5 whitespace-pre-wrap text-slate-400">
                {dns.data.resolver_state.resolv_conf}
              </pre>
            </details>
          )}
          {dns.data.resolver_state.hosts && (
            <details className="mt-1">
              <summary className="cursor-pointer text-slate-500">
                /etc/hosts
              </summary>
              <pre className="mt-0.5 whitespace-pre-wrap text-slate-400">
                {dns.data.resolver_state.hosts}
              </pre>
            </details>
          )}
        </div>
      )}

      {test.data && (
        <div className="space-y-1.5">
          {test.data.steps.map((s) => (
            <StepRow key={s.name} s={s} />
          ))}
        </div>
      )}
      {test.isError && (
        <div className="text-xs text-red-400">
          Test failed to run: {(test.error as Error).message}
        </div>
      )}
      {!test.data && !test.isPending && (
        <div className="text-xs text-slate-500">
          Click <span className="text-slate-300">Run test</span> to diagnose
          why the app can or cannot reach this device. Useful when the device's
          UI works in your browser but pre-checks fail.
        </div>
      )}
    </div>
  );
}

function StepRow({ s }: { s: Step }) {
  const icon =
    s.status === "pass" ? (
      <CheckCircle2 className="h-4 w-4 text-emerald-400" />
    ) : s.status === "fail" ? (
      <XCircle className="h-4 w-4 text-red-400" />
    ) : (
      <MinusCircle className="h-4 w-4 text-slate-500" />
    );
  return (
    <div className="flex items-start gap-2 rounded border border-slate-800/60 px-2 py-1.5 text-xs">
      {icon}
      <div className="flex-1">
        <div className="flex items-center gap-2 text-slate-200">
          <span className="font-medium">{STEP_LABEL[s.name] ?? s.name}</span>
          {s.latency_ms != null && (
            <span className="text-slate-500 tabular-nums">{s.latency_ms} ms</span>
          )}
          {s.status === "fail" && (
            <AlertTriangle className="h-3 w-3 text-amber-400" />
          )}
        </div>
        <div className="mt-0.5 whitespace-pre-wrap text-slate-400">{s.message}</div>
      </div>
    </div>
  );
}
