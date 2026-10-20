import { useCallback, useEffect, useRef, useState } from "react";

async function getJson<T>(url: string, signal: AbortSignal): Promise<T> {
  const response = await fetch(url, {
    signal,
    credentials: "same-origin",
    cache: "no-store",
  });
  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const detail =
      body && typeof body === "object" && "detail" in body
        ? String(body.detail)
        : `Request failed (${response.status})`;
    throw new Error(detail);
  }
  return body as T;
}

// Polls ``url`` while the tab is visible; a new url replaces the old request.
export function usePoll<T>(url: string | null, everyMs: number) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState("");
  const current = useRef<AbortController | null>(null);

  const load = useCallback(async () => {
    if (!url) return;
    current.current?.abort();
    const controller = new AbortController();
    current.current = controller;
    const timeout = window.setTimeout(() => controller.abort(), 15_000);
    try {
      const value = await getJson<T>(url, controller.signal);
      if (current.current === controller) {
        setData(value);
        setError("");
      }
    } catch (failure) {
      if (current.current === controller && !controller.signal.aborted) {
        setError(failure instanceof Error ? failure.message : "Request failed");
      }
    } finally {
      window.clearTimeout(timeout);
    }
  }, [url]);

  useEffect(() => {
    setData(null);
    void load();
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") void load();
    }, everyMs);
    return () => {
      window.clearInterval(timer);
      current.current?.abort();
    };
  }, [load, everyMs]);

  return { data, error, reload: load };
}
