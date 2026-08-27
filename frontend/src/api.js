// Zentrale fetch-Helfer. Relative /api-Pfade funktionieren in Dev (Vite-Proxy)
// und Produktion (nginx).

// Bei 401 global den Login-Screen auslösen (App.jsx hört auf das Event).
function notifyUnauthorized(res) {
  if (res.status === 401) window.dispatchEvent(new CustomEvent("auth:required"));
}

async function jget(url) {
  const res = await fetch(url);
  if (!res.ok) {
    notifyUnauthorized(res);
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

export const authCheck = () => jget("/api/auth/check");

export async function login(password) {
  const res = await fetch("/api/auth/login", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ password }),
  });
  if (!res.ok) {
    notifyUnauthorized(res);
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

export const logout = () => jsend("/api/auth/logout", "POST");

export const getAgents = () => jget("/api/agents");
export const getTasks = (name) =>
  jget(`/api/agents/${encodeURIComponent(name)}/tasks`);
export const closeTask = (agent, taskId, status = "done", result = "") =>
  jsend(
    `/api/tasks/${encodeURIComponent(agent)}/${encodeURIComponent(taskId)}/close`,
    "POST",
    { status, result },
  );
export const markInboxRead = (name) =>
  jsend(`/api/agents/${encodeURIComponent(name)}/inbox/read-all`, "POST");
export const getFiles = (path = "") =>
  jget(`/api/files?path=${encodeURIComponent(path)}`);
export const getFileContent = (path) =>
  jget(`/api/files/content?path=${encodeURIComponent(path)}`);
export const getAutomatik = () => jget("/api/automatik");
export const setAutomatik = (name, an) =>
  jsend(`/api/automatik/${encodeURIComponent(name)}`, "POST", { an });
export const setNotaus = (an) => jsend("/api/automatik/notaus", "POST", { an });

export const getConnections = () => jget("/api/connections");
export const createConnection = (data) => jsend("/api/connections", "POST", data);
export const deleteConnection = (name) =>
  jsend(`/api/connections/${encodeURIComponent(name)}`, "DELETE");
export const getConnectionPubkey = (name) =>
  jget(`/api/connections/${encodeURIComponent(name)}/pubkey`);

// --- Dateien: Workspace ("ws") oder SSH-Verbindung (SFTP) -------------------

export const getRemoteFiles = (name, path = "") =>
  jget(
    `/api/remote/${encodeURIComponent(name)}/files?path=${encodeURIComponent(path)}`,
  );
export const getRemoteFile = (name, path) =>
  jget(
    `/api/remote/${encodeURIComponent(name)}/file?path=${encodeURIComponent(path)}`,
  );

// Inline statt Download: zum Anzeigen und Abspielen im Dashboard selbst
// (Issues #25/#26). Der Server setzt dabei den echten Medientyp — mit
// `nosniff` im nginx wäre die Fläche sonst leer bzw. der Player stumm.
export const rawUrl = (source, path) =>
  source === "ws"
    ? `/api/files/raw?path=${encodeURIComponent(path)}`
    : `/api/remote/${encodeURIComponent(source)}/raw?path=${encodeURIComponent(path)}`;

export const downloadUrl = (source, path) =>
  source === "ws"
    ? `/api/files/download?path=${encodeURIComponent(path)}`
    : `/api/remote/${encodeURIComponent(source)}/download?path=${encodeURIComponent(path)}`;

export async function saveFile(source, path, content, encoding = "utf-8") {
  const url =
    source === "ws"
      ? "/api/files/content"
      : `/api/remote/${encodeURIComponent(source)}/file`;
  const res = await fetch(url, {
    method: "PUT",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ path, content, encoding }),
  });
  if (!res.ok) {
    notifyUnauthorized(res);
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

async function jsend(url, method, body) {
  const res = await fetch(url, {
    method,
    headers: body ? { "content-type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    notifyUnauthorized(res);
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

export const mkdir = (source, path) =>
  source === "ws"
    ? jsend("/api/files/mkdir", "POST", { path })
    : jsend(`/api/remote/${encodeURIComponent(source)}/mkdir`, "POST", { path });

export const renamePath = (source, path, newPath) =>
  source === "ws"
    ? jsend("/api/files/rename", "POST", { path, new_path: newPath })
    : jsend(`/api/remote/${encodeURIComponent(source)}/rename`, "POST", {
        path,
        new_path: newPath,
      });

export const deletePath = (source, path) =>
  source === "ws"
    ? jsend(`/api/files?path=${encodeURIComponent(path)}`, "DELETE")
    : jsend(
        `/api/remote/${encodeURIComponent(source)}/files?path=${encodeURIComponent(path)}`,
        "DELETE",
      );

export async function uploadFiles(source, path, fileList) {
  const url =
    source === "ws"
      ? `/api/files/upload?path=${encodeURIComponent(path)}`
      : `/api/remote/${encodeURIComponent(source)}/upload?path=${encodeURIComponent(path)}`;
  const form = new FormData();
  for (const f of fileList) form.append("files", f);
  const res = await fetch(url, { method: "POST", body: form });
  if (!res.ok) {
    notifyUnauthorized(res);
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail || `HTTP ${res.status}`);
  }
  return res.json();
}
export const getSshBuffer = (name, sid) =>
  jget(
    `/api/ssh/${encodeURIComponent(name)}/buffer?sid=${encodeURIComponent(sid)}`,
  );
export const getSshSessions = () => jget("/api/ssh/sessions");
export const deleteSshSession = (name, sid) =>
  jsend(
    `/api/ssh/${encodeURIComponent(name)}/session?sid=${encodeURIComponent(sid)}`,
    "DELETE",
  );
export const getSettings = () => jget("/api/settings");
export const getModels = () => jget("/api/models");
// Ohne `to` alle offenen Rückfragen (jede trägt `fuer_mensch`), mit `to` nur
// die aus einer Mailbox — z.B. `getQuestions("orchestrator")` (Issue #22).
export const getQuestions = (to) =>
  jget(to ? `/api/questions?to=${encodeURIComponent(to)}` : "/api/questions");
export const getChatSessions = () => jget("/api/chat/sessions");
export const getChatHistory = (id) =>
  jget(`/api/chat/${encodeURIComponent(id)}`);
export const deleteChatSession = (id) =>
  jsend(`/api/chat/${encodeURIComponent(id)}`, "DELETE");

export async function answerQuestion(agent, qid, text) {
  const res = await fetch(
    `/api/questions/${encodeURIComponent(agent)}/${encodeURIComponent(qid)}/answer`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ text }),
    },
  );
  if (!res.ok) {
    notifyUnauthorized(res);
    throw new Error(`HTTP ${res.status}`);
  }
  return res.json();
}

// Rückfrage ohne Antwort schließen (Issue #23) — der wartende Task scheitert
// dabei mit Klartext und landet wiederanlauffähig in .failed/.
export const closeQuestion = (agent, qid, grund = "") =>
  jsend(
    `/api/questions/${encodeURIComponent(agent)}/${encodeURIComponent(qid)}/close`,
    "POST",
    { grund },
  );

export async function putSettings(patch) {
  const res = await fetch("/api/settings", {
    method: "PUT",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!res.ok) {
    notifyUnauthorized(res);
    throw new Error(`HTTP ${res.status}`);
  }
  return res.json();
}

export async function postChat(message, sessionId) {
  const res = await fetch("/api/chat", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ message, session_id: sessionId }),
  });
  if (!res.ok) {
    notifyUnauthorized(res);
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

// --- Live-Events / Web-Push / Chat-Streaming (F3/F4/F10) --------------------

export const getPushKey = () => jget("/api/push/key");
export const subscribePush = (sub) => jsend("/api/push/subscribe", "POST", sub);
export const unsubscribePush = (endpoint) =>
  jsend("/api/push/unsubscribe", "POST", { endpoint });
export const pushTest = () => jsend("/api/push/test", "POST", {});
export const cancelChatStream = (streamId) =>
  jsend(`/api/chat/stream/${encodeURIComponent(streamId)}/cancel`, "POST", {});

// Chat als SSE-Strom (F3). EventSource kann kein POST — deshalb fetch +
// eigener Parser (Events sind durch Leerzeilen getrennt, ": ping" sind
// Heartbeats). onStart liefert die stream_id (für den Abbrechen-Knopf),
// onTool jeden Tool-Call live.
export async function streamChat(message, sessionId, { onStart, onTool } = {}) {
  const res = await fetch("/api/chat/stream", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ message, session_id: sessionId }),
  });
  if (!res.ok || !res.body) {
    notifyUnauthorized(res);
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail || `HTTP ${res.status}`);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buf.indexOf("\n\n")) >= 0) {
      const block = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      const line = block.split("\n").find((l) => l.startsWith("data: "));
      if (!line) continue; // Heartbeat/retry
      let d;
      try {
        d = JSON.parse(line.slice(6));
      } catch {
        continue;
      }
      if (d.type === "start") onStart?.(d);
      else if (d.type === "tool") onTool?.(d);
      else if (d.type === "done")
        return { sessionId: d.session_id, reply: d.reply, toolCalls: d.tool_calls };
      else if (d.type === "aborted") return { sessionId: d.session_id, aborted: true };
      else if (d.type === "error") throw new Error(d.detail || "Orchestrator-Fehler");
    }
  }
  // Verbindung weg, bevor done/aborted/error kam: der Turn läuft serverseitig
  // weiter und speichert — die Antwort steht danach im Verlauf.
  throw new Error(
    "Stream abgerissen — der Orchestrator arbeitet weiter; die Antwort erscheint danach im Verlauf (Session neu öffnen).",
  );
}
