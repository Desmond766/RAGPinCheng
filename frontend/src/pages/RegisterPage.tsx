import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../context/AuthContext";

export function RegisterPage() {
  const { register } = useAuth();
  const [employeeId, setEmployeeId] = useState("");
  const [realName, setRealName] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPw, setConfirmPw] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (password !== confirmPw) {
      setError("两次输入的密码不一致");
      return;
    }
    if (password.length < 6) {
      setError("密码至少 6 位");
      return;
    }
    setSubmitting(true);
    try {
      await register(employeeId.trim(), realName.trim(), password);
    } catch (err: any) {
      setError(err?.message?.replace(/^\d+\s+\w+:\s*/, "") || "注册失败");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="h-full flex items-center justify-center bg-bg">
      <div className="w-full max-w-sm rounded-2xl border border-gray-200 bg-panel p-8 shadow-sm">
        <h1 className="text-xl font-semibold mb-1">注册 · 品成 BIM 知识库</h1>
        <p className="text-sm text-muted mb-6">填写工号、姓名和密码</p>
        <form onSubmit={onSubmit} className="space-y-4">
          <label className="block">
            <span className="text-sm">工号（登录用，唯一）</span>
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
            <span className="text-sm">真实姓名</span>
            <input
              type="text"
              value={realName}
              onChange={(e) => setRealName(e.target.value)}
              className="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm bg-bg"
              required
            />
          </label>
          <label className="block">
            <span className="text-sm">密码（至少 6 位）</span>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm bg-bg"
              required
              minLength={6}
            />
          </label>
          <label className="block">
            <span className="text-sm">确认密码</span>
            <input
              type="password"
              value={confirmPw}
              onChange={(e) => setConfirmPw(e.target.value)}
              className="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm bg-bg"
              required
              minLength={6}
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
            {submitting ? "注册中…" : "注册"}
          </button>
        </form>
        <div className="mt-4 text-sm text-muted text-center">
          已有账号？{" "}
          <Link to="/login" className="text-accent hover:underline">
            登录
          </Link>
        </div>
      </div>
    </div>
  );
}
