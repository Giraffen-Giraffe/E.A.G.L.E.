import { FormEvent, useState } from "react";
import { api } from "../api";
import type { Info } from "../types";

export default function Login({ info, onLogin }: { info: Info; onLogin: () => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api.post("/api/auth/login", { username, password });
      onLogin();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login">
      <form className="login-card" onSubmit={submit}>
        <img src="/eagle.png" alt="" width={72} />
        <h1>E.A.G.L.E.</h1>
        <p className="muted">Evolutionary Automated Generative Learning Engine</p>
        {info.needs_setup && (
          <div className="notice">
            管理者が未作成です。サーバーで <code>python -m eagle user add admin --role admin</code> を実行してください。
          </div>
        )}
        <label className="field">
          <span className="field-label">ユーザー名</span>
          <input autoFocus value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" />
        </label>
        <label className="field">
          <span className="field-label">パスワード</span>
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" />
        </label>
        {error && <div className="error-text">{error}</div>}
        <button className="btn primary" disabled={busy || !username}>ログイン</button>
      </form>
    </div>
  );
}
