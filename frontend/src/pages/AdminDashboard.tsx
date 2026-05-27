import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { useAuth } from "../context/AuthContext";
import type {
  AdminConversation,
  AdminFeedbackEntry,
  AdminStats,
  AdminUser,
  CategoryTree,
  ConversationState,
  IndexJob,
  IndexedDocument,
} from "../types";

type Tab = "users" | "conversations" | "stats" | "feedback" | "corpus";

function fmtDate(ts: number | null | undefined): string {
  if (!ts) return "—";
  const d = new Date(ts * 1000);
  return d.toLocaleString();
}

export function AdminDashboard() {
  const { state, logout } = useAuth();
  const [tab, setTab] = useState<Tab>("users");

  return (
    <div className="h-full flex flex-col bg-bg">
      <header className="border-b border-gray-200 bg-panel px-6 py-3 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <span className="text-lg">🛠️</span>
          <h1 className="font-semibold">管理后台 · 品成 BIM 知识库</h1>
        </div>
        <div className="flex items-center gap-3 text-sm">
          <Link to="/" className="text-accent hover:underline">
            ← 返回对话
          </Link>
          {state.status === "authed" && (
            <span className="text-muted">
              {state.user.real_name}（{state.user.employee_id}）
            </span>
          )}
          <button
            type="button"
            onClick={async () => {
              await logout();
              window.location.href = "/login";
            }}
            className="text-muted hover:text-red-600"
          >
            退出
          </button>
        </div>
      </header>

      <div className="border-b border-gray-200 bg-panel px-6">
        <nav className="flex gap-1">
          {([
            ["users", "用户"],
            ["conversations", "对话"],
            ["corpus", "资料管理"],
            ["stats", "概览"],
            ["feedback", "反馈"],
          ] as [Tab, string][]).map(([key, label]) => (
            <button
              key={key}
              type="button"
              onClick={() => setTab(key)}
              className={
                "px-4 py-2 text-sm border-b-2 -mb-px " +
                (tab === key
                  ? "border-accent text-accent"
                  : "border-transparent text-muted hover:text-ink")
              }
            >
              {label}
            </button>
          ))}
        </nav>
      </div>

      <div className="flex-1 overflow-y-auto p-6">
        {tab === "users" && <UsersTab />}
        {tab === "conversations" && <ConversationsTab />}
        {tab === "corpus" && <CorpusTab />}
        {tab === "stats" && <StatsTab />}
        {tab === "feedback" && <FeedbackTab />}
      </div>
    </div>
  );
}

// ── Users ─────────────────────────────────────────────────────────────────


function UsersTab() {
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [drillUser, setDrillUser] = useState<AdminUser | null>(null);
  const [filter, setFilter] = useState("");

  // Filter on both real_name and employee_id since they live in the same
  // column visually and admins will sometimes search by either.
  const visibleUsers = useMemo(() => {
    const q = filter.trim().toLowerCase();
    if (!q) return users;
    return users.filter(
      (u) =>
        u.real_name.toLowerCase().includes(q) ||
        u.employee_id.toLowerCase().includes(q),
    );
  }, [users, filter]);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const { users } = await api.adminListUsers();
      setUsers(users);
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  async function toggleActive(u: AdminUser) {
    try {
      await api.adminPatchUser(u.id, { is_active: !u.is_active });
      refresh();
    } catch (e: any) {
      alert(e?.message || String(e));
    }
  }
  async function toggleRole(u: AdminUser) {
    const newRole = u.role === "admin" ? "user" : "admin";
    if (!confirm(`将 ${u.real_name}（${u.employee_id}）的角色改为 ${newRole}？`)) return;
    try {
      await api.adminPatchUser(u.id, { role: newRole });
      refresh();
    } catch (e: any) {
      alert(e?.message || String(e));
    }
  }
  async function resetPw(u: AdminUser) {
    const pw = prompt(`为 ${u.real_name}（${u.employee_id}）设置新密码（≥ 6 位）：`);
    if (!pw) return;
    if (pw.length < 6) {
      alert("密码至少 6 位");
      return;
    }
    try {
      await api.adminPatchUser(u.id, { reset_password: pw });
      alert("密码已重置；该用户的所有会话已失效。");
    } catch (e: any) {
      alert(e?.message || String(e));
    }
  }

  return (
    <div className="space-y-4">
      {error && <div className="text-sm text-red-600">{error}</div>}
      <div className="flex items-center gap-3">
        <input
          type="search"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="按姓名或工号筛选…"
          className="w-72 rounded-lg border border-gray-300 px-3 py-1.5 text-sm bg-bg"
        />
        <span className="text-xs text-muted">
          {filter
            ? `${visibleUsers.length} / ${users.length}`
            : `${users.length} 位用户`}
        </span>
        {filter && (
          <button
            type="button"
            onClick={() => setFilter("")}
            className="text-xs text-accent hover:underline"
          >
            清空
          </button>
        )}
      </div>
      <div className="overflow-x-auto rounded-lg border border-gray-200 bg-panel">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 dark:bg-gray-800 text-muted">
            <tr>
              <th className="text-left px-3 py-2">工号</th>
              <th className="text-left px-3 py-2">姓名</th>
              <th className="text-left px-3 py-2">角色</th>
              <th className="text-left px-3 py-2">状态</th>
              <th className="text-right px-3 py-2">对话数</th>
              <th className="text-left px-3 py-2">最近登录</th>
              <th className="text-left px-3 py-2">注册时间</th>
              <th className="text-left px-3 py-2">操作</th>
            </tr>
          </thead>
          <tbody>
            {visibleUsers.map((u) => (
              <tr key={u.id} className="border-t border-gray-100 dark:border-gray-800">
                <td className="px-3 py-2 font-mono">{u.employee_id}</td>
                <td className="px-3 py-2">{u.real_name}</td>
                <td className="px-3 py-2">
                  <span
                    className={
                      u.role === "admin"
                        ? "px-1.5 py-0.5 rounded text-xs bg-purple-100 text-purple-700"
                        : "px-1.5 py-0.5 rounded text-xs bg-gray-100 text-gray-700"
                    }
                  >
                    {u.role}
                  </span>
                </td>
                <td className="px-3 py-2">
                  {u.is_active ? (
                    <span className="text-green-600">启用</span>
                  ) : (
                    <span className="text-red-600">已停用</span>
                  )}
                </td>
                <td className="px-3 py-2 text-right">
                  <button
                    type="button"
                    className="text-accent hover:underline"
                    onClick={() => setDrillUser(u)}
                  >
                    {u.conversation_count}
                  </button>
                </td>
                <td className="px-3 py-2 text-muted">{fmtDate(u.last_login_at)}</td>
                <td className="px-3 py-2 text-muted">{fmtDate(u.created_at)}</td>
                <td className="px-3 py-2 space-x-2 whitespace-nowrap">
                  <button
                    type="button"
                    className="text-xs text-accent hover:underline"
                    onClick={() => toggleActive(u)}
                  >
                    {u.is_active ? "停用" : "启用"}
                  </button>
                  <button
                    type="button"
                    className="text-xs text-accent hover:underline"
                    onClick={() => toggleRole(u)}
                  >
                    {u.role === "admin" ? "降为用户" : "升为管理员"}
                  </button>
                  <button
                    type="button"
                    className="text-xs text-accent hover:underline"
                    onClick={() => resetPw(u)}
                  >
                    重置密码
                  </button>
                </td>
              </tr>
            ))}
            {!loading && visibleUsers.length === 0 && (
              <tr>
                <td colSpan={8} className="px-3 py-6 text-center text-muted">
                  {filter
                    ? `没有匹配 “${filter}” 的用户`
                    : "（暂无用户）"}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {drillUser && (
        <UserConversationsDrillIn
          user={drillUser}
          onClose={() => setDrillUser(null)}
        />
      )}
    </div>
  );
}


function UserConversationsDrillIn({
  user,
  onClose,
}: {
  user: AdminUser;
  onClose: () => void;
}) {
  const [list, setList] = useState<AdminConversation[]>([]);
  const [selected, setSelected] = useState<ConversationState | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    (async () => {
      try {
        const { conversations } = await api.adminListUserConversations(user.id);
        setList(conversations);
      } finally {
        setLoading(false);
      }
    })();
  }, [user.id]);

  return (
    <div className="fixed inset-0 bg-black/40 flex items-stretch justify-center p-6 z-20">
      <div className="bg-panel rounded-xl w-full max-w-5xl flex flex-col overflow-hidden">
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200">
          <div className="text-sm">
            <span className="font-semibold">{user.real_name}</span>
            <span className="text-muted ml-2">工号 {user.employee_id} 的对话</span>
          </div>
          <button onClick={onClose} className="text-muted hover:text-ink">
            ✕
          </button>
        </div>
        <div className="flex flex-1 min-h-0">
          <div className="w-72 border-r border-gray-200 overflow-y-auto">
            {loading && <div className="p-3 text-xs text-muted">加载中…</div>}
            {list.map((c) => (
              <button
                key={c.id}
                type="button"
                onClick={async () => {
                  try {
                    const state = await api.adminGetConversation(c.id);
                    setSelected(state);
                  } catch (e: any) {
                    alert(e?.message || String(e));
                  }
                }}
                className={
                  "w-full text-left px-3 py-2 text-sm border-b border-gray-100 dark:border-gray-800 " +
                  (selected?.id === c.id ? "bg-accent/10" : "hover:bg-gray-100 dark:hover:bg-gray-800")
                }
              >
                <div className="truncate">{c.title}</div>
                <div className="text-[11px] text-muted">{fmtDate(c.updated_at)}</div>
              </button>
            ))}
            {!loading && list.length === 0 && (
              <div className="p-3 text-xs text-muted">该用户尚无对话。</div>
            )}
          </div>
          <div className="flex-1 overflow-y-auto p-4 space-y-3">
            {!selected && (
              <div className="text-sm text-muted">从左侧选择一条对话查看消息内容。</div>
            )}
            {selected?.messages.map((m, i) => (
              <div
                key={i}
                className={
                  "rounded-lg px-3 py-2 text-sm whitespace-pre-wrap " +
                  (m.role === "user"
                    ? "bg-accent/10"
                    : "bg-gray-50 dark:bg-gray-800 border border-gray-200 dark:border-gray-700")
                }
              >
                <div className="text-[11px] text-muted mb-1">{m.role}</div>
                {m.content}
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}


// ── Conversations (all) ───────────────────────────────────────────────────


function ConversationsTab() {
  const [list, setList] = useState<AdminConversation[]>([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<ConversationState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState("");

  useEffect(() => {
    (async () => {
      try {
        const { conversations } = await api.adminListAllConversations(200);
        setList(conversations);
      } catch (e: any) {
        setError(e?.message || String(e));
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  // Filter on user name, employee_id, and conversation title — admins
  // browsing for "what was this person asking about?" benefit from matching
  // either the user or the topic.
  const visible = useMemo(() => {
    const q = filter.trim().toLowerCase();
    if (!q) return list;
    return list.filter(
      (c) =>
        c.real_name.toLowerCase().includes(q) ||
        c.employee_id.toLowerCase().includes(q) ||
        c.title.toLowerCase().includes(q),
    );
  }, [list, filter]);

  return (
    <div className="space-y-3 h-[calc(100vh-220px)] flex flex-col">
      <div className="flex items-center gap-3">
        <input
          type="search"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="按姓名、工号或对话标题筛选…"
          className="w-80 rounded-lg border border-gray-300 px-3 py-1.5 text-sm bg-bg"
        />
        <span className="text-xs text-muted">
          {filter ? `${visible.length} / ${list.length}` : `${list.length} 条对话`}
        </span>
        {filter && (
          <button
            type="button"
            onClick={() => setFilter("")}
            className="text-xs text-accent hover:underline"
          >
            清空
          </button>
        )}
      </div>
      <div className="grid grid-cols-12 gap-4 flex-1 min-h-0">
        <div className="col-span-5 overflow-y-auto rounded-lg border border-gray-200 bg-panel">
          {error && <div className="p-3 text-sm text-red-600">{error}</div>}
          {loading && <div className="p-3 text-sm text-muted">加载中…</div>}
          {!loading && visible.length === 0 && (
            <div className="p-3 text-xs text-muted">
              {filter ? `没有匹配 “${filter}” 的对话` : "（暂无对话）"}
            </div>
          )}
          {visible.map((c) => (
            <button
              key={c.id}
              type="button"
              onClick={async () => {
                try {
                  const state = await api.adminGetConversation(c.id);
                  setSelected(state);
                } catch (e: any) {
                  alert(e?.message || String(e));
                }
              }}
              className={
                "w-full text-left px-3 py-2 text-sm border-b border-gray-100 dark:border-gray-800 " +
                (selected?.id === c.id ? "bg-accent/10" : "hover:bg-gray-100 dark:hover:bg-gray-800")
              }
            >
              <div className="truncate font-medium">{c.title}</div>
              <div className="text-[11px] text-muted">
                {c.real_name} · {c.employee_id} · {fmtDate(c.updated_at)} · {c.turn_index} 轮
              </div>
            </button>
          ))}
        </div>
        <div className="col-span-7 overflow-y-auto rounded-lg border border-gray-200 bg-panel p-3 space-y-3">
          {!selected && <div className="text-sm text-muted">从左侧选择一条对话查看消息。</div>}
          {selected?.messages.map((m, i) => (
            <div
              key={i}
              className={
                "rounded-lg px-3 py-2 text-sm whitespace-pre-wrap " +
                (m.role === "user"
                  ? "bg-accent/10"
                  : "bg-gray-50 dark:bg-gray-800 border border-gray-200 dark:border-gray-700")
              }
            >
              <div className="text-[11px] text-muted mb-1">{m.role}</div>
              {m.content}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}


// ── Stats ─────────────────────────────────────────────────────────────────


function StatsTab() {
  const [stats, setStats] = useState<AdminStats | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sweepResult, setSweepResult] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      try {
        setStats(await api.adminStats());
      } catch (e: any) {
        setError(e?.message || String(e));
      }
    })();
  }, []);

  async function sweep() {
    if (!confirm("立即清理过期对话（>30 天未活动）和失效登录会话？")) return;
    try {
      const r = await api.adminSweep();
      setSweepResult(`已删除 ${r.deleted_conversations} 条对话、${r.deleted_auth_sessions} 条登录会话。`);
      setStats(await api.adminStats());
    } catch (e: any) {
      alert(e?.message || String(e));
    }
  }

  if (error) return <div className="text-sm text-red-600">{error}</div>;
  if (!stats) return <div className="text-sm text-muted">加载中…</div>;

  const cards: [string, number, string][] = [
    ["用户总数", stats.users_total, "已注册账号"],
    ["启用用户", stats.users_active, "未停用账号"],
    ["对话总数", stats.conversations_total, "活跃保留中"],
    ["对话（近 7 天）", stats.conversations_7d, "最近 7 天有新消息"],
    ["消息总数", stats.messages_total, "用户 + 助手"],
    ["消息（近 7 天）", stats.messages_7d, ""],
  ];
  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
        {cards.map(([label, value, hint]) => (
          <div key={label} className="rounded-lg border border-gray-200 bg-panel p-4">
            <div className="text-xs text-muted">{label}</div>
            <div className="text-2xl font-semibold mt-1">{value}</div>
            {hint && <div className="text-[11px] text-muted mt-1">{hint}</div>}
          </div>
        ))}
      </div>
      <div className="rounded-lg border border-gray-200 bg-panel p-4 text-sm">
        <div className="font-medium mb-2">维护</div>
        <button
          type="button"
          onClick={sweep}
          className="rounded-lg border border-gray-300 px-3 py-1.5 text-sm hover:bg-gray-50"
        >
          立即清理过期对话
        </button>
        {sweepResult && <div className="text-green-700 mt-2">{sweepResult}</div>}
        <p className="text-xs text-muted mt-2">
          清理策略：对话 30 天无活动即删除；失效登录会话同时清理。后台每小时自动执行一次。
        </p>
      </div>
    </div>
  );
}


// ── Feedback ──────────────────────────────────────────────────────────────


function FeedbackTab() {
  const [entries, setEntries] = useState<AdminFeedbackEntry[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    (async () => {
      try {
        const r = await api.adminFeedback(200);
        setEntries(r.entries);
        setTotal(r.total);
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  if (loading) return <div className="text-sm text-muted">加载中…</div>;
  if (entries.length === 0) {
    return <div className="text-sm text-muted">暂无反馈记录。</div>;
  }

  return (
    <div className="space-y-3">
      <div className="text-xs text-muted">
        共 {total} 条；显示最近 {entries.length} 条。
      </div>
      {entries.map((e, i) => (
        <div key={i} className="rounded-lg border border-gray-200 bg-panel p-3 text-sm">
          <div className="flex items-center gap-2 text-xs text-muted">
            <span>{e.ts || "—"}</span>
            <span className="px-1.5 rounded bg-gray-100 dark:bg-gray-800">{e.kind || "?"}</span>
            {e.rating && (
              <span
                className={
                  e.rating === "down"
                    ? "px-1.5 rounded bg-red-100 text-red-700"
                    : "px-1.5 rounded bg-green-100 text-green-700"
                }
              >
                {e.rating}
              </span>
            )}
          </div>
          {e.query && (
            <div className="mt-2">
              <div className="text-[11px] text-muted">问题</div>
              <div>{e.query}</div>
            </div>
          )}
          {e.note && (
            <div className="mt-2">
              <div className="text-[11px] text-muted">用户反馈</div>
              <div className="whitespace-pre-wrap">{e.note}</div>
            </div>
          )}
          {e.doc_title && (
            <div className="mt-2 text-xs text-muted">
              来源：[{e.doc_title}] {e.section_path || ""} {e.start_time ? `@${e.start_time}` : ""}
            </div>
          )}
          {e.answer_text && (
            <details className="mt-2">
              <summary className="text-[11px] text-muted cursor-pointer">查看回答</summary>
              <div className="mt-1 whitespace-pre-wrap text-xs text-gray-700 dark:text-gray-300">
                {e.answer_text}
              </div>
            </details>
          )}
        </div>
      ))}
    </div>
  );
}


// ── Corpus management ─────────────────────────────────────────────────────


const NEW_CATEGORY_SENTINEL = "__new__";

const STATUS_LABELS: Record<string, string> = {
  pending: "排队中",
  uploading: "上传中",
  queued_mineru: "等待 MinerU",
  parsing: "解析中",
  chunking: "切块中",
  embedding: "嵌入中",
  done: "已完成",
  failed: "失败",
};

const STATUS_HINTS: Record<string, string> = {
  uploading: "正在上传文件至 MinerU…",
  queued_mineru: "文件已提交，等待 MinerU 服务器开始解析（通常需 1–3 分钟）",
  parsing: "MinerU 正在解析 PDF…",
  chunking: "切块中…",
  embedding: "向量嵌入中…",
};

const STATUS_COLORS: Record<string, string> = {
  pending: "bg-gray-100 text-gray-700",
  uploading: "bg-sky-100 text-sky-700",
  queued_mineru: "bg-violet-100 text-violet-700",
  parsing: "bg-blue-100 text-blue-700",
  chunking: "bg-blue-100 text-blue-700",
  embedding: "bg-amber-100 text-amber-700",
  done: "bg-green-100 text-green-700",
  failed: "bg-red-100 text-red-700",
};

const ACTIVE_STATUSES = new Set(["pending", "uploading", "queued_mineru", "parsing", "chunking", "embedding"]);

function useElapsed(startTs: number | null | undefined): string {
  const [now, setNow] = useState(() => Date.now());
  const active = startTs != null;
  useEffect(() => {
    if (!active) return;
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, [active]);
  if (!startTs) return "";
  const sec = Math.floor((now - startTs * 1000) / 1000);
  if (sec < 60) return `${sec}s`;
  return `${Math.floor(sec / 60)}m${sec % 60}s`;
}

function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  return `${(n / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}


function CorpusTab() {
  const [tree, setTree] = useState<CategoryTree | null>(null);
  const [documents, setDocuments] = useState<IndexedDocument[]>([]);
  const [jobs, setJobs] = useState<IndexJob[]>([]);
  const [loadingDocs, setLoadingDocs] = useState(true);
  const [loadingJobs, setLoadingJobs] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refreshAll = useCallback(async () => {
    try {
      const [t, docs, j] = await Promise.all([
        api.adminCategoryTree(),
        api.adminListIndexedDocuments(),
        api.adminListIndexJobs(100),
      ]);
      setTree(t);
      setDocuments(docs.documents);
      setJobs(j.jobs);
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setLoadingDocs(false);
      setLoadingJobs(false);
    }
  }, []);

  useEffect(() => {
    refreshAll();
  }, [refreshAll]);

  // While anything is queued or running, poll the jobs list every 3s so the
  // admin sees status flip without manually refreshing. Stop polling when
  // everything settles.
  const hasActive = jobs.some(
    (j) => j.status !== "done" && j.status !== "failed",
  );
  useEffect(() => {
    if (!hasActive) return;
    const t = window.setInterval(async () => {
      try {
        const { jobs: latest } = await api.adminListIndexJobs(100);
        setJobs(latest);
        // Once a job finishes, refresh documents too.
        if (latest.some((j) => j.status === "done")) {
          const docs = await api.adminListIndexedDocuments();
          setDocuments(docs.documents);
        }
      } catch {
        /* ignore — next tick retries */
      }
    }, 3000);
    return () => window.clearInterval(t);
  }, [hasActive]);

  return (
    <div className="space-y-6">
      {error && <div className="text-sm text-red-600">{error}</div>}
      <UploadCard
        tree={tree}
        onUploaded={() => refreshAll()}
      />
      <DocumentsCard
        documents={documents}
        loading={loadingDocs}
        onChange={() => refreshAll()}
      />
      <JobsCard
        jobs={jobs}
        loading={loadingJobs}
        onChange={() => refreshAll()}
      />
    </div>
  );
}


function UploadCard({
  tree,
  onUploaded,
}: {
  tree: CategoryTree | null;
  onUploaded: () => void;
}) {
  const [files, setFiles] = useState<File[]>([]);
  const [pickedCategory, setPickedCategory] = useState<string>("");
  const [newCategory, setNewCategory] = useState<string>("");
  const [pickedSub, setPickedSub] = useState<string>("");
  const [newSub, setNewSub] = useState<string>("");
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<{
    accepted: number;
    skipped: { filename: string; reason: string }[];
  } | null>(null);

  const categoryNames = useMemo(
    () => (tree ? tree.categories.map((c) => c.name) : []),
    [tree],
  );

  // Default the dropdown to the first known category once they load.
  useEffect(() => {
    if (!pickedCategory && categoryNames.length > 0) {
      setPickedCategory(categoryNames[0]);
    }
  }, [categoryNames, pickedCategory]);

  const effectiveCategory =
    pickedCategory === NEW_CATEGORY_SENTINEL ? newCategory.trim() : pickedCategory;

  // Look up the node for the currently-selected (existing) category.
  // New categories typed by the admin are treated as flat — admins who need
  // a two-level new category can just type "客户标准" which already exists.
  const currentNode = useMemo(() => {
    if (!tree) return null;
    return tree.categories.find((c) => c.name === effectiveCategory) || null;
  }, [tree, effectiveCategory]);

  const needsSubcategory = !!currentNode?.two_level;
  const existingSubs = currentNode?.subcategories || [];

  // Reset subcategory selection when the parent category changes so the
  // dropdown defaults sensibly (first existing sub, or "+ new" if empty).
  useEffect(() => {
    if (!needsSubcategory) {
      setPickedSub("");
      setNewSub("");
      return;
    }
    if (existingSubs.length > 0) {
      setPickedSub(existingSubs[0]);
    } else {
      setPickedSub(NEW_CATEGORY_SENTINEL);
    }
    setNewSub("");
  }, [effectiveCategory, needsSubcategory, existingSubs.join("|")]);

  const effectiveSub = needsSubcategory
    ? pickedSub === NEW_CATEGORY_SENTINEL ? newSub.trim() : pickedSub
    : "";

  const canSubmit =
    files.length > 0 &&
    !!effectiveCategory &&
    (!needsSubcategory || !!effectiveSub);

  async function submit() {
    if (!canSubmit) return;
    setSubmitting(true);
    setResult(null);
    try {
      const r = await api.adminUploadDocuments(
        files,
        effectiveCategory,
        effectiveSub || undefined,
      );
      setResult({ accepted: r.accepted.length, skipped: r.skipped });
      setFiles([]);
      const input = document.getElementById("corpus-upload-input") as HTMLInputElement | null;
      if (input) input.value = "";
      onUploaded();
    } catch (e: any) {
      setResult({ accepted: 0, skipped: [{ filename: "(upload)", reason: e?.message || String(e) }] });
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="rounded-lg border border-gray-200 bg-panel p-4">
      <h2 className="font-semibold mb-3">上传资料</h2>
      <p className="text-xs text-muted mb-3">
        支持 <code>.pdf</code>（自动经 MinerU 解析）与 <code>.md</code>。
        在「教学视频」分类下上传的 <code>.md</code> 会按转写格式（说话人 + 时间戳）处理，
        其它分类下则作为普通 Markdown 文档（按标题切分）处理。
        可一次选择多个文件，会依次排队；处理过程中可继续聊天，但响应可能变慢。
      </p>
      <div className="flex flex-col gap-3">
        <div>
          <label className="block text-sm mb-1">分类</label>
          <div className="flex items-center gap-2">
            <select
              value={pickedCategory}
              onChange={(e) => setPickedCategory(e.target.value)}
              className="rounded-lg border border-gray-300 px-3 py-1.5 text-sm bg-bg"
            >
              {categoryNames.length === 0 && <option value="">（暂无现有分类）</option>}
              {categoryNames.map((c) => (
                <option key={c} value={c}>{c}</option>
              ))}
              <option value={NEW_CATEGORY_SENTINEL}>＋ 新建分类…</option>
            </select>
            {pickedCategory === NEW_CATEGORY_SENTINEL && (
              <input
                type="text"
                value={newCategory}
                onChange={(e) => setNewCategory(e.target.value)}
                placeholder="新分类名（如：行业规范）"
                className="rounded-lg border border-gray-300 px-3 py-1.5 text-sm bg-bg flex-1"
                autoFocus
              />
            )}
          </div>
        </div>
        {needsSubcategory && (
          <div>
            <label className="block text-sm mb-1">
              {effectiveCategory === "客户标准" ? "客户" : "公司"}
              <span className="text-xs text-muted ml-2">
                （「{effectiveCategory}」按 {effectiveCategory === "客户标准" ? "客户" : "公司"} 分组）
              </span>
            </label>
            <div className="flex items-center gap-2">
              <select
                value={pickedSub}
                onChange={(e) => setPickedSub(e.target.value)}
                className="rounded-lg border border-gray-300 px-3 py-1.5 text-sm bg-bg"
              >
                {existingSubs.length === 0 && (
                  <option value={NEW_CATEGORY_SENTINEL}>
                    （暂无；请新建）
                  </option>
                )}
                {existingSubs.map((s) => (
                  <option key={s} value={s}>{s}</option>
                ))}
                {existingSubs.length > 0 && (
                  <option value={NEW_CATEGORY_SENTINEL}>＋ 新建…</option>
                )}
              </select>
              {pickedSub === NEW_CATEGORY_SENTINEL && (
                <input
                  type="text"
                  value={newSub}
                  onChange={(e) => setNewSub(e.target.value)}
                  placeholder={
                    effectiveCategory === "客户标准"
                      ? "新客户名（如：C客户标准）"
                      : "新公司名"
                  }
                  className="rounded-lg border border-gray-300 px-3 py-1.5 text-sm bg-bg flex-1"
                  autoFocus
                />
              )}
            </div>
          </div>
        )}
        <div>
          <label className="block text-sm mb-1">文件</label>
          <input
            id="corpus-upload-input"
            type="file"
            multiple
            accept=".pdf,.md"
            onChange={(e) => setFiles(Array.from(e.target.files || []))}
            className="text-sm"
          />
          {files.length > 0 && (
            <ul className="mt-2 text-xs text-muted space-y-0.5">
              {files.map((f) => (
                <li key={f.name}>
                  {f.name} <span className="ml-1">· {fmtBytes(f.size)}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
        <div>
          <button
            type="button"
            onClick={submit}
            disabled={submitting || !canSubmit}
            className="rounded-lg bg-accent text-white px-4 py-1.5 text-sm hover:opacity-90 disabled:opacity-50"
          >
            {submitting ? "上传中…" : `上传 ${files.length} 个文件`}
          </button>
        </div>
        {result && (
          <div className="text-sm">
            {result.accepted > 0 && (
              <div className="text-green-700">
                已加入队列 {result.accepted} 个文件，可在下方“索引任务”查看进度。
              </div>
            )}
            {result.skipped.length > 0 && (
              <div className="text-red-600 mt-1">
                以下文件未受理：
                <ul className="list-disc list-inside">
                  {result.skipped.map((s, i) => (
                    <li key={i}>{s.filename}：{s.reason}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}


function DocumentsCard({
  documents,
  loading,
  onChange,
}: {
  documents: IndexedDocument[];
  loading: boolean;
  onChange: () => void;
}) {
  const [filter, setFilter] = useState("");

  const visible = useMemo(() => {
    const q = filter.trim().toLowerCase();
    if (!q) return documents;
    return documents.filter(
      (d) =>
        d.doc_title.toLowerCase().includes(q) ||
        d.category.toLowerCase().includes(q),
    );
  }, [documents, filter]);

  async function onDelete(d: IndexedDocument) {
    const ok = confirm(
      `从索引中移除「${d.doc_title}」？\n` +
        `这将删除该资料的 ${d.parent_count} 个父段落及其所有子块。`,
    );
    if (!ok) return;
    const alsoFile = confirm("同时从磁盘删除源文件？（取消 = 仅清除索引，保留文件）");
    try {
      await api.adminDeleteIndexedDocument(d.source_path, alsoFile);
      onChange();
    } catch (e: any) {
      alert(e?.message || String(e));
    }
  }

  return (
    <div className="rounded-lg border border-gray-200 bg-panel p-4">
      <div className="flex items-center justify-between mb-3">
        <h2 className="font-semibold">已索引资料</h2>
        <div className="flex items-center gap-2">
          <input
            type="search"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="按标题或分类筛选…"
            className="w-64 rounded-lg border border-gray-300 px-3 py-1 text-sm bg-bg"
          />
          <span className="text-xs text-muted">
            {filter ? `${visible.length} / ${documents.length}` : `${documents.length} 个文档`}
          </span>
        </div>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-muted">
            <tr>
              <th className="text-left px-2 py-1">标题</th>
              <th className="text-left px-2 py-1">分类</th>
              <th className="text-left px-2 py-1">类型</th>
              <th className="text-right px-2 py-1">父段落</th>
              <th className="text-left px-2 py-1">操作</th>
            </tr>
          </thead>
          <tbody>
            {loading && (
              <tr><td colSpan={5} className="px-2 py-3 text-muted">加载中…</td></tr>
            )}
            {!loading && visible.length === 0 && (
              <tr>
                <td colSpan={5} className="px-2 py-3 text-muted">
                  {filter ? `没有匹配 “${filter}” 的资料` : "（暂无已索引资料 — 上传 PDF 或转写以开始）"}
                </td>
              </tr>
            )}
            {visible.map((d) => (
              <tr key={d.source_path} className="border-t border-gray-100 dark:border-gray-800">
                <td className="px-2 py-1.5 max-w-md truncate" title={d.doc_title}>
                  {d.doc_title}
                </td>
                <td className="px-2 py-1.5">{d.category}</td>
                <td className="px-2 py-1.5 text-muted">
                  {d.doc_type === "transcript"
                    ? "教学视频转写"
                    : d.source_path.toLowerCase().endsWith(".md")
                      ? "Markdown 文档"
                      : "PDF"}
                </td>
                <td className="px-2 py-1.5 text-right">{d.parent_count}</td>
                <td className="px-2 py-1.5">
                  <button
                    type="button"
                    onClick={() => onDelete(d)}
                    className="text-xs text-red-600 hover:underline"
                  >
                    删除
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}


function JobStatusCell({ job: j }: { job: IndexJob }) {
  const isActive = ACTIVE_STATUSES.has(j.status);
  const elapsed = useElapsed(isActive ? j.started_at ?? j.created_at : null);
  const hint = isActive ? STATUS_HINTS[j.status] : null;
  return (
    <>
      <span
        className={
          "inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[11px] " +
          (STATUS_COLORS[j.status] || "bg-gray-100 text-gray-700") +
          (isActive ? " animate-pulse" : "")
        }
      >
        {isActive && (
          <svg className="w-2.5 h-2.5 animate-spin" viewBox="0 0 24 24" fill="none">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v4l3-3-3-3v4a8 8 0 00-8 8h4z" />
          </svg>
        )}
        {STATUS_LABELS[j.status] || j.status}
        {elapsed && <span className="opacity-70">{elapsed}</span>}
      </span>
      {hint && (
        <div className="text-[11px] text-muted mt-0.5">{hint}</div>
      )}
      {j.error && (
        <div
          className="text-[11px] text-red-600 mt-1 max-w-xs whitespace-pre-wrap"
          title={j.error}
        >
          {j.error.length > 200 ? j.error.slice(0, 200) + "…" : j.error}
        </div>
      )}
    </>
  );
}

function JobsCard({
  jobs,
  loading,
  onChange,
}: {
  jobs: IndexJob[];
  loading: boolean;
  onChange: () => void;
}) {
  async function onRetry(j: IndexJob) {
    try {
      await api.adminRetryIndexJob(j.id);
      onChange();
    } catch (e: any) {
      alert(e?.message || String(e));
    }
  }
  async function onDelete(j: IndexJob) {
    if (!confirm(`删除该任务记录？（不影响已索引的内容）`)) return;
    try {
      await api.adminDeleteIndexJob(j.id);
      onChange();
    } catch (e: any) {
      alert(e?.message || String(e));
    }
  }

  return (
    <div className="rounded-lg border border-gray-200 bg-panel p-4">
      <h2 className="font-semibold mb-3">索引任务</h2>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-muted">
            <tr>
              <th className="text-left px-2 py-1">文件</th>
              <th className="text-left px-2 py-1">分类</th>
              <th className="text-left px-2 py-1">状态</th>
              <th className="text-left px-2 py-1">上传者</th>
              <th className="text-left px-2 py-1">时间</th>
              <th className="text-right px-2 py-1">规模</th>
              <th className="text-left px-2 py-1">操作</th>
            </tr>
          </thead>
          <tbody>
            {loading && (
              <tr><td colSpan={7} className="px-2 py-3 text-muted">加载中…</td></tr>
            )}
            {!loading && jobs.length === 0 && (
              <tr><td colSpan={7} className="px-2 py-3 text-muted">（暂无任务）</td></tr>
            )}
            {jobs.map((j) => (
              <tr key={j.id} className="border-t border-gray-100 dark:border-gray-800 align-top">
                <td className="px-2 py-1.5 max-w-xs truncate" title={j.filename}>
                  {j.filename}
                  <div className="text-[11px] text-muted">{fmtBytes(j.file_size)}</div>
                </td>
                <td className="px-2 py-1.5">{j.category}</td>
                <td className="px-2 py-1.5">
                  <JobStatusCell job={j} />
                </td>
                <td className="px-2 py-1.5 text-muted">
                  {j.real_name || "—"}
                  {j.employee_id && (
                    <div className="text-[11px]">{j.employee_id}</div>
                  )}
                </td>
                <td className="px-2 py-1.5 text-muted text-[11px]">
                  <div>提交 {fmtDate(j.created_at)}</div>
                  {j.started_at && !j.finished_at && (
                    <div>开始 {fmtDate(j.started_at)}</div>
                  )}
                  {j.finished_at && <div>完成 {fmtDate(j.finished_at)}</div>}
                </td>
                <td className="px-2 py-1.5 text-right text-[11px] text-muted">
                  {j.status === "done" && j.parents != null && j.children != null
                    ? `${j.parents} 父 / ${j.children} 子`
                    : "—"}
                </td>
                <td className="px-2 py-1.5 space-x-2 whitespace-nowrap">
                  {(j.status === "failed" || j.status === "done") && (
                    <button
                      type="button"
                      onClick={() => onRetry(j)}
                      className="text-xs text-accent hover:underline"
                    >
                      重试
                    </button>
                  )}
                  {(j.status === "done" || j.status === "failed") && (
                    <button
                      type="button"
                      onClick={() => onDelete(j)}
                      className="text-xs text-muted hover:text-red-600"
                    >
                      删除记录
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
