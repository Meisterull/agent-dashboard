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

export const getAgents = () => jget("/api/agents");
export const getTasks = (name) =>
  jget(`/api/agents/${encodeURIComponent(name)}/tasks`);
export const getFiles = (path = "") =>
  jget(`/api/files?path=${encodeURIComponent(path)}`);
export const getFileContent = (path) =>
  jget(`/api/files/content?path=${encodeURIComponent(path)}`);
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
export const getIntegrations = () => jget("/api/integrations");
export const getSettings = () => jget("/api/settings");
export const getInbox = (name, kind) =>
  jget(`/api/agents/${encodeURIComponent(name)}/inbox${kind ? `?kind=${kind}` : ""}`);
export const getQuestions = () => jget("/api/questions");
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
