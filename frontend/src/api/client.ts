import type { ApiConfig, Health } from "../types";

async function jsonFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { "content-type": "application/json", ...(init?.headers || {}) },
  });
  if (!res.ok) {
    const txt = await res.text().catch(() => "");
    throw new Error(`${res.status} ${res.statusText}${txt ? `: ${txt}` : ""}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  health: () => jsonFetch<Health>("/api/health"),
  config: () => jsonFetch<ApiConfig>("/api/config"),
  categories: () => jsonFetch<{ categories: string[] }>("/api/categories"),
  createSession: () => jsonFetch<{ session_id: string }>("/api/sessions", { method: "POST", body: "{}" }),
  deleteSession: (sid: string) =>
    fetch(`/api/sessions/${sid}`, { method: "DELETE" }).then((r) => {
      if (!r.ok && r.status !== 404) throw new Error(`delete failed: ${r.status}`);
    }),
};
