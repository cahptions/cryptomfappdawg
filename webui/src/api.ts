import { useCallback, useEffect, useRef, useState } from "react";
import type { Snapshot } from "./types";

async function jsonResponse(response: Response): Promise<unknown> {
  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const error =
      body && typeof body === "object" && "detail" in body
        ? String(body.detail)
        : `Request failed (${response.status})`;
    throw new Error(error);
  }
  return body;
}

// One bounded snapshot and one shared connection for the whole application.
// Polling is a visibility-aware fallback, never a second permanent update loop.
export function useDashboard() {
  const [data, setData] = useState<Snapshot | null>(null);
  const [connection, setConnection] = useState<
    "connecting" | "live" | "retrying"
  >("connecting");
  const [error, setError] = useState("");
  const request = useRef<AbortController | null>(null);
  const mounted = useRef(true);
  const refresh = useCallback(async () => {
    if (request.current || !mounted.current) return;
    const controller = new AbortController();
    request.current = controller;
    const timeout = window.setTimeout(() => controller.abort(), 10_000);
    try {
      const snapshot = (await jsonResponse(
        await fetch("/api/dashboard", {
          signal: controller.signal,
          credentials: "same-origin",
          cache: "no-store",
        }),
      )) as Snapshot;
      if (mounted.current) {
        setData(snapshot);
        setError("");
      }
    } catch (cause) {
      if (mounted.current)
        setError(
          cause instanceof Error && cause.name !== "AbortError"
            ? cause.message
            : "Snapshot timed out. Reconnecting…",
        );
    } finally {
      window.clearTimeout(timeout);
      if (request.current === controller) request.current = null;
    }
  }, []);

  useEffect(() => {
    mounted.current = true;
    let connected = false;
    let stopped = false;
    let stream: EventSource | null = null;
    let fallback: number | undefined;
    const poll = () => {
      if (!stopped && !connected && !document.hidden) void refresh();
    };
    void refresh().then(() => {
      if (stopped) return;
      stream = new EventSource("/api/events");
      stream.onopen = () => {
        connected = true;
        setConnection("live");
      };
      stream.addEventListener("snapshot", (event) => {
        try {
          const snapshot: Snapshot = JSON.parse(
            (event as MessageEvent<string>).data,
          );
          if (snapshot && typeof snapshot === "object") {
            setData(snapshot);
            setError("");
          }
        } catch {
          setError(
            "An unreadable update was skipped. Waiting for the next snapshot.",
          );
        }
      });
      stream.onerror = () => {
        connected = false;
        setConnection("retrying");
      };
      fallback = window.setInterval(poll, 10_000);
    });
    document.addEventListener("visibilitychange", poll);
    return () => {
      stopped = true;
      mounted.current = false;
      stream?.close();
      window.clearInterval(fallback);
      request.current?.abort();
      request.current = null;
      document.removeEventListener("visibilitychange", poll);
    };
  }, [refresh]);
  return { data, connection, error, refresh };
}

export async function control(
  action: string,
  body: Record<string, unknown> = {},
) {
  const session = (await jsonResponse(
    await fetch("/api/session", {
      credentials: "same-origin",
      cache: "no-store",
      signal: AbortSignal.timeout(10_000),
    }),
  )) as { csrf_token?: string };
  if (!session.csrf_token)
    throw new Error("Session expired. Reload the terminal and try again.");
  return jsonResponse(
    await fetch(`/api/control/${encodeURIComponent(action)}`, {
      method: "POST",
      credentials: "same-origin",
      signal: AbortSignal.timeout(15_000),
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": session.csrf_token,
      },
      body: JSON.stringify(body),
    }),
  );
}

export async function loadDetail(endpoint: string, signal: AbortSignal) {
  return jsonResponse(
    await fetch(endpoint, {
      signal,
      credentials: "same-origin",
      cache: "no-store",
    }),
  );
}
