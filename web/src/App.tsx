import { useMemo, useState } from "react";
import ChatLive from "./ChatLive";

type TokenOut = { access_token: string; token_type: string };

export default function App() {
  const [username, setUsername] = useState("DEMO-ADMIN");
  const [password, setPassword] = useState("demo-admin");
  const [token, setToken] = useState(localStorage.getItem("mg_token") || "");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const isAuthed = useMemo(() => !!token, [token]);

  async function doLogin() {
    setError("");
    setLoading(true);
    try {
      const body = new URLSearchParams();
      body.set("username", username.trim());
      body.set("password", password);

      const res = await fetch("/api/v1/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: body.toString(),
      });

      if (!res.ok) {
        const txt = await res.text();
        throw new Error(`${res.status} ${res.statusText}: ${txt}`);
      }

      const data: TokenOut = await res.json();
      localStorage.setItem("mg_token", data.access_token);
      setToken(data.access_token);
    } catch (e: any) {
      setError(e?.message || String(e));
      localStorage.removeItem("mg_token");
      setToken("");
    } finally {
      setLoading(false);
    }
  }

  function logout() {
    localStorage.removeItem("mg_token");
    setToken("");
  }

  if (!isAuthed) {
    return (
      <div style={{ padding: 40, fontFamily: "system-ui, Arial" }}>
        <h2>Mini Gramm Local</h2><p>Внутренняя корпоративная платформа</p>

        <div style={{ display: "grid", gap: 10, maxWidth: 360 }}>
          <input
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            placeholder="User ID (например DEMO-ADMIN)"
            style={{ padding: 10, borderRadius: 10 }}
          />
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="Password"
            style={{ padding: 10, borderRadius: 10 }}
          />
          <button onClick={doLogin} disabled={loading} style={{ padding: 10, borderRadius: 10 }}>
            {loading ? "..." : "Login"}
          </button>

          {error && (
            <pre style={{ background: "#fee", padding: 10, borderRadius: 10, whiteSpace: "pre-wrap" }}>
              {error}
            </pre>
          )}
        </div>
      </div>
    );
  }

  return (
    <>
      <div style={{ padding: 10 }}>
        <button onClick={logout} style={{ padding: 10, borderRadius: 10 }}>
          Logout
        </button>
      </div>

      {/* ВАЖНО: ChatLive должен сам использовать token из localStorage */}
      <ChatLive />
    </>
  );
}
