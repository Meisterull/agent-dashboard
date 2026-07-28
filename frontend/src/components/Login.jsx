import { useState } from "react";
import { login } from "../api";

// Passwort-Login (Einzelbenutzer). Erscheint, wenn /api/auth/check "required"
// meldet oder irgendein API-Call mit 401 antwortet (Event "auth:required").
//
// Passwortmanager (KeePass/KeePassDX, Bitwarden, Browser-Autofill) brauchen
// ein echtes Formular mit Benutzer- UND Passwortfeld samt autocomplete-Hints,
// sonst bieten sie kein Ausfüllen an. Das Benutzerfeld ist sichtbar und mit
// "admin" vorbelegt (der Server prüft nur das Passwort), damit der Manager
// einen Eintrag zuordnen kann.
export default function Login({ onSuccess }) {
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  async function submit(e) {
    e.preventDefault();
    if (!password || busy) return;
    setBusy(true);
    setError(null);
    try {
      await login(password);
      onSuccess();
    } catch (err) {
      setError(String(err.message || err));
    } finally {
      setBusy(false);
    }
  }

  const inputCls =
    "mb-3 w-full rounded border border-slate-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none dark:border-slate-600 dark:bg-slate-800 dark:text-slate-100";

  return (
    <div className="flex h-dvh items-center justify-center bg-slate-100 p-4 dark:bg-slate-950">
      <form
        onSubmit={submit}
        method="post"
        action="/api/auth/login"
        className="w-full max-w-xs rounded-lg bg-white p-6 shadow dark:bg-slate-900"
      >
        <h1 className="mb-4 text-center font-semibold text-slate-800 dark:text-slate-100">
          agent-dashboard
        </h1>
        <input
          type="text"
          id="username"
          name="username"
          autoComplete="username"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          placeholder="Benutzer"
          className={inputCls}
        />
        <input
          type="password"
          id="password"
          name="password"
          autoComplete="current-password"
          autoFocus
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder="Passwort"
          className={inputCls}
        />
        {error && (
          <p className="mb-3 text-center text-xs text-red-600 dark:text-red-400">
            {error}
          </p>
        )}
        <button
          type="submit"
          disabled={busy || !password}
          className="w-full rounded bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-40"
        >
          {busy ? "prüft…" : "Anmelden"}
        </button>
      </form>
    </div>
  );
}
