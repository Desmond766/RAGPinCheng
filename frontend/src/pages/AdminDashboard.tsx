import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { useAuth } from "../context/AuthContext";
import type {
  AdminConversation,
  AdminFeedbackEntry,
  AdminStats,
  AdminUser,
  ConversationState,
} from "../types";

type Tab = "users" | "conversations" | "stats" | "feedback";

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
