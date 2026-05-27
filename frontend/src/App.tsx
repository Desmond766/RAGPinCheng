import { Navigate, Route, BrowserRouter as Router, Routes } from "react-router-dom";
import { ChatLayout } from "./components/ChatLayout";
import { AuthProvider, useAuth } from "./context/AuthContext";
import { AdminDashboard } from "./pages/AdminDashboard";
import { LoginPage } from "./pages/LoginPage";
import { RegisterPage } from "./pages/RegisterPage";

function FullPageLoader({ label }: { label: string }) {
  return (
    <div className="h-full flex items-center justify-center text-muted text-sm">
      {label}
    </div>
  );
}

function RequireAuth({ children }: { children: JSX.Element }) {
  const { state } = useAuth();
  if (state.status === "loading") return <FullPageLoader label="正在恢复登录…" />;
  if (state.status !== "authed") return <Navigate to="/login" replace />;
  return children;
}

function RequireAdmin({ children }: { children: JSX.Element }) {
  const { state } = useAuth();
  if (state.status === "loading") return <FullPageLoader label="正在恢复登录…" />;
  if (state.status !== "authed") return <Navigate to="/login" replace />;
  if (state.user.role !== "admin") return <Navigate to="/" replace />;
  return children;
}

function RedirectIfAuthed({ children }: { children: JSX.Element }) {
  const { state } = useAuth();
  if (state.status === "loading") return <FullPageLoader label="加载中…" />;
  if (state.status === "authed") return <Navigate to="/" replace />;
  return children;
}

export default function App() {
  return (
    <Router>
      <AuthProvider>
        <Routes>
          <Route
            path="/login"
            element={
              <RedirectIfAuthed>
                <LoginPage />
              </RedirectIfAuthed>
            }
          />
          <Route
            path="/register"
            element={
              <RedirectIfAuthed>
                <RegisterPage />
              </RedirectIfAuthed>
            }
          />
          <Route
            path="/admin"
            element={
              <RequireAdmin>
                <AdminDashboard />
              </RequireAdmin>
            }
          />
          <Route
            path="/"
            element={
              <RequireAuth>
                <ChatLayout />
              </RequireAuth>
            }
          />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </AuthProvider>
    </Router>
  );
}
