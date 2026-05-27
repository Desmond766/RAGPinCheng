import type {
  AdminConversation,
  AdminFeedbackEntry,
  AdminStats,
  AdminUser,
  ApiConfig,
  AuthUser,
  CategoryTree,
  Conversation,
  ConversationState,
  FeedbackPayload,
  Health,
  IndexJob,
  IndexedDocument,
  LlmHealth,
} from "../types";

// Mutating methods send X-CSRF-Token. Cookies always go along via credentials.
const MUTATING = new Set(["POST", "PATCH", "PUT", "DELETE"]);

let csrfToken: string | null = null;

export function setCsrfToken(token: string | null) {
  csrfToken = token;
}

export function getCsrfToken(): string | null {
  return csrfToken;
}

let unauthorizedHandler: (() => void) | null = null;

export function setUnauthorizedHandler(fn: (() => void) | null) {
  unauthorizedHandler = fn;
}

export class ApiError extends Error {
  status: number;
  body: string;
  constructor(status: number, body: string, message: string) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

async function rawFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const method = (init.method || "GET").toUpperCase();
  const headers: Record<string, string> = {
    ...(init.headers as Record<string, string> | undefined),
  };
  if (init.body && !headers["content-type"] && !headers["Content-Type"]) {
    headers["content-type"] = "application/json";
  }
  if (MUTATING.has(method) && csrfToken) {
    headers["X-CSRF-Token"] = csrfToken;
  }
  const res = await fetch(path, { ...init, headers, credentials: "include" });
  if (res.status === 401 && unauthorizedHandler) {
    // Fire-and-forget; the handler resets local auth state.
    try {
      unauthorizedHandler();
    } catch {
      /* noop */
    }
  }
  return res;
}

async function jsonFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await rawFetch(path, init);
  if (!res.ok) {
    const txt = await res.text().catch(() => "");
    let detail = txt;
    try {
      const parsed = JSON.parse(txt);
      if (parsed && typeof parsed.detail === "string") detail = parsed.detail;
    } catch {
      /* keep raw */
    }
    throw new ApiError(res.status, txt, `${res.status} ${res.statusText}${detail ? `: ${detail}` : ""}`);
  }
  // 204 has no body.
  if (res.status === 204) return undefined as unknown as T;
  return (await res.json()) as T;
}

export const api = {
  // cross-cutting
  health: () => jsonFetch<Health>("/api/health"),
  llmHealth: (force = false) =>
    jsonFetch<LlmHealth>(`/api/llm_health${force ? "?force=true" : ""}`),
  config: () => jsonFetch<ApiConfig>("/api/config"),
  categories: () => jsonFetch<{ categories: string[] }>("/api/categories"),
  sendFeedback: (payload: FeedbackPayload) =>
    jsonFetch<{ ok: boolean }>("/api/feedback", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  // auth
  me: () => jsonFetch<AuthUser>("/api/auth/me"),
  login: (employee_id: string, password: string) =>
    jsonFetch<AuthUser>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ employee_id, password }),
    }),
  register: (employee_id: string, real_name: string, password: string) =>
    jsonFetch<AuthUser>("/api/auth/register", {
      method: "POST",
      body: JSON.stringify({ employee_id, real_name, password }),
    }),
  logout: () => jsonFetch<void>("/api/auth/logout", { method: "POST" }),

  // conversations
  listConversations: () =>
    jsonFetch<{ conversations: Conversation[] }>("/api/conversations"),
  createConversation: () =>
    jsonFetch<Conversation>("/api/conversations", { method: "POST", body: "{}" }),
  getConversation: (id: string) =>
    jsonFetch<ConversationState>(`/api/conversations/${id}`),
  deleteConversation: (id: string) =>
    jsonFetch<void>(`/api/conversations/${id}`, { method: "DELETE" }),

  // admin
  adminListUsers: () =>
    jsonFetch<{ users: AdminUser[] }>("/api/admin/users"),
  adminPatchUser: (
    id: number,
    body: Partial<{ is_active: boolean; role: "user" | "admin"; reset_password: string }>,
  ) =>
    jsonFetch<AdminUser>(`/api/admin/users/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  adminListUserConversations: (userId: number) =>
    jsonFetch<{ conversations: AdminConversation[] }>(
      `/api/admin/users/${userId}/conversations`,
    ),
  adminListAllConversations: (limit = 200) =>
    jsonFetch<{ conversations: AdminConversation[] }>(
      `/api/admin/conversations?limit=${limit}`,
    ),
  adminGetConversation: (id: string) =>
    jsonFetch<ConversationState>(`/api/conversations/${id}`),
  adminStats: () => jsonFetch<AdminStats>("/api/admin/stats"),
  adminFeedback: (limit = 200) =>
    jsonFetch<{ entries: AdminFeedbackEntry[]; total: number }>(
      `/api/admin/feedback?limit=${limit}`,
    ),
  adminSweep: () =>
    jsonFetch<{ deleted_conversations: number; deleted_auth_sessions: number }>(
      "/api/admin/sweep",
      { method: "POST" },
    ),

  // admin: indexing
  adminCategoryTree: () =>
    jsonFetch<CategoryTree>("/api/admin/index/category-tree"),
  adminUploadDocuments: async (
    files: File[],
    category: string,
    subcategory?: string,
  ) => {
    // FormData triggers multipart; we deliberately don't set content-type
    // so the browser appends the multipart boundary itself. The CSRF
    // header is still injected by rawFetch via the X-CSRF-Token branch.
    const fd = new FormData();
    for (const f of files) fd.append("files", f, f.name);
    fd.append("category", category);
    if (subcategory) fd.append("subcategory", subcategory);
    const method = "POST";
    const csrf = csrfToken;
    const headers: Record<string, string> = {};
    if (csrf) headers["X-CSRF-Token"] = csrf;
    const res = await fetch("/api/admin/upload", {
      method,
      headers,
      body: fd,
      credentials: "include",
    });
    if (res.status === 401 && unauthorizedHandler) {
      try { unauthorizedHandler(); } catch { /* noop */ }
    }
    if (!res.ok) {
      const txt = await res.text().catch(() => "");
      let detail = txt;
      try {
        const parsed = JSON.parse(txt);
        if (parsed && typeof parsed.detail === "string") detail = parsed.detail;
      } catch {
        /* keep raw */
      }
      throw new ApiError(res.status, txt, `${res.status} ${res.statusText}${detail ? `: ${detail}` : ""}`);
    }
    return (await res.json()) as {
      accepted: IndexJob[];
      skipped: { filename: string; reason: string }[];
    };
  },
  adminListIndexJobs: (limit = 100) =>
    jsonFetch<{ jobs: IndexJob[] }>(`/api/admin/index/jobs?limit=${limit}`),
  adminRetryIndexJob: (id: number) =>
    jsonFetch<IndexJob>(`/api/admin/index/jobs/${id}/retry`, { method: "POST" }),
  adminDeleteIndexJob: (id: number) =>
    jsonFetch<void>(`/api/admin/index/jobs/${id}`, { method: "DELETE" }),
  adminListIndexedDocuments: () =>
    jsonFetch<{ documents: IndexedDocument[] }>("/api/admin/index/documents"),
  adminDeleteIndexedDocument: (source_path: string, delete_file: boolean) =>
    jsonFetch<{ parents_deleted: number; file_deleted: boolean }>(
      "/api/admin/index/documents",
      { method: "DELETE", body: JSON.stringify({ source_path, delete_file }) },
    ),
};
