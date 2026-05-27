import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { useTheme } from "../hooks/useTheme";

export function UserMenu() {
  const { state, logout } = useAuth();
  const [theme, toggleTheme] = useTheme();
  const [open, setOpen] = useState(false);
  const navigate = useNavigate();
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    function onDocClick(e: MouseEvent) {
      if (!ref.current) return;
      if (!ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, []);

  if (state.status !== "authed") return null;
  const u = state.user;
  const initials = (u.real_name || u.employee_id).slice(0, 1);

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center gap-3 rounded-lg px-2 py-2 hover:bg-gray-100 dark:hover:bg-gray-800"
      >
        <span className="inline-flex h-8 w-8 items-center justify-center rounded-full bg-accent text-white text-sm font-semibold">
          {initials}
        </span>
        <div className="flex-1 min-w-0 text-left">
          <div className="text-sm truncate">{u.real_name}</div>
          <div className="text-[11px] text-muted truncate">工号 {u.employee_id}</div>
        </div>
        <span className="text-muted text-xs">▾</span>
      </button>
      {open && (
        <div className="absolute bottom-12 left-0 right-0 rounded-lg border border-gray-200 bg-panel shadow-lg py-1 z-10">
          <button
            type="button"
            onClick={() => {
              toggleTheme();
              setOpen(false);
            }}
            className="w-full text-left px-3 py-2 text-sm hover:bg-gray-100 dark:hover:bg-gray-800"
          >
            {theme === "dark" ? "☀️ 浅色模式" : "🌙 深色模式"}
          </button>
          {u.role === "admin" && (
            <button
              type="button"
              onClick={() => {
                setOpen(false);
                navigate("/admin");
              }}
              className="w-full text-left px-3 py-2 text-sm hover:bg-gray-100 dark:hover:bg-gray-800"
            >
              🛠️ 管理后台
            </button>
          )}
          <button
            type="button"
            onClick={async () => {
              setOpen(false);
              await logout();
              navigate("/login");
            }}
            className="w-full text-left px-3 py-2 text-sm hover:bg-gray-100 dark:hover:bg-gray-800 text-red-600"
          >
            退出登录
          </button>
        </div>
      )}
    </div>
  );
}
