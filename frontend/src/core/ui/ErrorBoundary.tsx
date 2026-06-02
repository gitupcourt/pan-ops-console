// React error boundary — the app's last line of defense against a single
// bad payload taking down the whole console.
//
// Why this exists: React unmounts the entire component tree when a render
// throws and nothing catches it. With no boundary, one malformed API
// response (e.g. a snapshot-diff area that serialized to `null`) turns the
// whole page black until a manual refresh — exactly the "Compare diff →
// black screen" bug. A boundary converts that into a contained, readable
// error with a way out.
//
// Error boundaries MUST be class components — there is no hook equivalent
// for getDerivedStateFromError / componentDidCatch.

import { Component, ErrorInfo, ReactNode } from "react";

import { Button } from "./ui";

type Props = {
  children: ReactNode;
  // Custom fallback. Receives the caught error and a reset() that clears the
  // error state so the subtree re-mounts (useful for inline/panel boundaries
  // where "Dismiss" should restore the surrounding UI).
  fallback?: (error: Error, reset: () => void) => ReactNode;
  // Heading shown by the default fallback.
  title?: string;
  // When true, the default fallback offers "Reload page" (full reload) —
  // appropriate at the app root. Inline boundaries leave this off and rely
  // on reset().
  allowReload?: boolean;
};

type State = { error: Error | null };

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // Keep a console trail for debugging; the boundary swallows the throw
    // so the user-facing UI stays intact.
    // eslint-disable-next-line no-console
    console.error("ErrorBoundary caught:", error, info.componentStack);
  }

  reset = (): void => this.setState({ error: null });

  render(): ReactNode {
    const { error } = this.state;
    if (error === null) return this.props.children;

    if (this.props.fallback) return this.props.fallback(error, this.reset);

    return (
      <div className="p-4">
        <div className="max-w-lg rounded border border-rose-900/50 bg-rose-950/30 p-4 text-sm">
          <div className="font-semibold text-rose-200">
            {this.props.title ?? "Something went wrong rendering this view"}
          </div>
          <p className="mt-1 text-[12px] text-zinc-400">
            The rest of the console is unaffected. You can dismiss this and
            keep working, or reload if it persists.
          </p>
          {error.message && (
            <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap break-all rounded bg-zinc-950/60 p-2 font-mono text-[11px] text-rose-300/90">
              {error.message}
            </pre>
          )}
          <div className="mt-3 flex gap-2">
            <Button onClick={this.reset}>Dismiss</Button>
            {this.props.allowReload && (
              <Button variant="primary" onClick={() => window.location.reload()}>
                Reload page
              </Button>
            )}
          </div>
        </div>
      </div>
    );
  }
}
