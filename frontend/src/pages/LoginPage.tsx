import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../context/AuthContext";

export function LoginPage() {
  const { login } = useAuth();
  const [employeeId, setEmployeeId] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await login(employeeId.trim(), password);
    } catch (err: any) {
      setError(err?.message?.replace(/^\d+\s+\w+:\s*/, "") || "登录失败");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="h-full flex items-center justify-center bg-bg">
      <div className="w-full max-w-sm rounded-2xl border border-gray-200 bg-panel p-8 shadow-sm">
        <h1 className="text-xl font-semibold mb-1">登录 · 品成 BIM 知识库</h1>
        <p className="text-sm text-muted mb-6">使用工号和密码登录</p>
        <form onSubmit={onSubmit} className="space-y-4">
          <label className="block">
            <span className="text-sm">工号</span>
            <input
              type="text"
              value={employeeId}
              onChange={(e) => setEmployeeId(e.target.value)}
              className="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm bg-bg"
              autoFocus
              required
            />
          </label>
          <label className="block">
            <span className="text-sm">密码</span>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm bg-bg"
              required
            />
          </label>
          {error && (
            <div className="text-sm text-red-600 bg-red-50 dark:bg-red-950/30 rounded-lg px-3 py-2">
              {error}
            </div>
          )}
          <button
            type="submit"
            disabled={submitting}
            className="w-full rounded-lg bg-accent text-white px-3 py-2 text-sm hover:opacity-90 disabled:opacity-50"
          >
            {submitting ? "登录中…" : "登录"}
          </button>
        </form>
        <div className="mt-4 text-sm text-muted text-center">
          还没有账号？{" "}
          <Link to="/register" className="text-accent hover:underline">
            注册
          </Link>
        </div>
      </div>
    </div>
  );
}
