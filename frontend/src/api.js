import { t } from "./sprache";
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
// Eine Antwort ungekürzt (Issue #41): die Liste trägt lange Texte nur noch
// angeschnitten, der volle Eintrag kommt beim Aufklappen.
// Ereignis-Log je Agent (Beobachtbarkeit): neueste zuerst, `vor` = „mehr
// laden" (Einträge älter als diese Zeit), `probleme` = nur warnung/fehler.
export const getEreignisse = (name, { limit = 50, vor, probleme } = {}) =>
  jget(
    `/api/agents/${encodeURIComponent(name)}/ereignisse?limit=${limit}` +
      (vor ? `&vor=${encodeURIComponent(vor)}` : "") +
      (probleme ? "&probleme=1" : ""),
  );
export const getOutboxEintrag = (name, taskId) =>
  jget(
    `/api/agents/${encodeURIComponent(name)}/outbox/${encodeURIComponent(taskId)}`,
  );
export const closeTask = (agent, taskId, status = "done", result = "") =>
  jsend(
    `/api/tasks/${encodeURIComponent(agent)}/${encodeURIComponent(taskId)}/close`,
    "POST",
    { status, result },
  );
export const markInboxRead = (name) =>
  jsend(`/api/agents/${encodeURIComponent(name)}/inbox/read-all`, "POST");
// Eine einzelne Nachricht wegräumen (Issue #33) — "alles gelesen" quittiert
// sonst auch Eingänge, die man noch gar nicht gesehen hat.
export const markEnvelopeRead = (name, envelopeId) =>
  jsend(
    `/api/agents/${encodeURIComponent(name)}/inbox/${encodeURIComponent(envelopeId)}/read`,
    "POST",
  );
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

// Rekursive Suche ab einem Pfad (Issue #45): Namen, oder mit `inhalt` im
// Text. `signal` (AbortController) bricht eine laufende Suche ab — der Server
// beendet dann auch find/grep auf der Maschine.
export async function searchFiles(source, path, q, inhalt = false, signal = undefined) {
  const params = `path=${encodeURIComponent(path)}&q=${encodeURIComponent(q)}&inhalt=${inhalt ? 1 : 0}`;
  const url =
    source === "ws"
      ? `/api/files/suche?${params}`
      : `/api/remote/${encodeURIComponent(source)}/suche?${params}`;
  const res = await fetch(url, { signal });
  if (!res.ok) {
    notifyUnauthorized(res);
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

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

// Upload (Issue #48): je Datei EINE Anfrage, der Rumpf sind die rohen Bytes —
// nginx reicht sie ungepuffert durch, das Backend streamt ins Ziel. So gilt
// die Grenze je Datei (nicht für die Summe einer Auswahl), ein Fehler kostet
// nur eine Datei, und XMLHttpRequest liefert Fortschritt (fetch kann das beim
// Senden nicht).
let uploadGrenze = null;
/** Grenze je Datei in MB, 0 = keine. Einmal geholt; ohne Antwort: keine Vorprüfung. */
export const getUploadGrenze = () =>
  (uploadGrenze ??= jget("/api/upload/grenze").catch(() => {
    uploadGrenze = null;
    return { max_mb: 0 };
  }));

export function dateiGroesse(n) {
  if (n < 1024 * 1024) return `${Math.max(1, Math.round(n / 1024))} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

function ladeEineDatei(url, datei, { onProgress, signal }, maxMb) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const abbruch = () => xhr.abort();
    const fertig = (fn, wert) => {
      signal?.removeEventListener("abort", abbruch);
      fn(wert);
    };
    xhr.open("POST", url);
    xhr.setRequestHeader("Content-Type", "application/octet-stream");
    xhr.upload.onprogress = (e) => onProgress?.(e.loaded);
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          fertig(resolve, JSON.parse(xhr.responseText));
        } catch {
          fertig(reject, new Error(`HTTP ${xhr.status}`));
        }
        return;
      }
      if (xhr.status === 401) window.dispatchEvent(new CustomEvent("auth:required"));
      // 413 kommt meist von nginx (HTML-Seite, kein JSON) — die Grenze selbst
      // nennen statt nur die Statuszahl.
      if (xhr.status === 413) {
        fertig(
          reject,
          new Error(
            maxMb
              ? t("„{0}“ ist zu groß ({1}) — erlaubt sind {2} MB je Datei.", datei.name, dateiGroesse(datei.size), maxMb)
              : t("„{0}“ ist zu groß ({1}) — der Server nimmt sie nicht an.", datei.name, dateiGroesse(datei.size)),
          ),
        );
        return;
      }
      let detail = "";
      try {
        detail = JSON.parse(xhr.responseText).detail || "";
      } catch {
        /* keine JSON-Antwort */
      }
      fertig(reject, new Error(detail || `HTTP ${xhr.status}`));
    };
    xhr.onerror = () => fertig(reject, new Error(t("Upload fehlgeschlagen: Verbindung unterbrochen.")));
    xhr.onabort = () => fertig(reject, new Error(t("Upload abgebrochen.")));
    if (signal) {
      if (signal.aborted) return fertig(reject, new Error(t("Upload abgebrochen.")));
      signal.addEventListener("abort", abbruch);
    }
    xhr.send(datei);
  });
}

/**
 * Lädt Dateien nacheinander hoch. `onProgress({datei, index, anzahl, geladen,
 * gesamt})` meldet den Stand über ALLE Dateien (Bytes), `signal` bricht ab.
 * Antwort wie früher: {saved: [...]}.
 */
export async function uploadFiles(source, path, fileList, { onProgress, signal } = {}) {
  const dateien = Array.from(fileList);
  const { max_mb: maxMb } = await getUploadGrenze();
  // Vor dem Senden prüfen: wer 2 GB auswählt, soll es nicht erst nach
  // Minuten erfahren.
  if (maxMb) {
    const zuGross = dateien.find((d) => d.size > maxMb * 1024 * 1024);
    if (zuGross) {
      throw new Error(
        t("„{0}“ ist zu groß ({1}) — erlaubt sind {2} MB je Datei.", zuGross.name, dateiGroesse(zuGross.size), maxMb),
      );
    }
  }
  const gesamt = dateien.reduce((summe, d) => summe + d.size, 0);
  const saved = [];
  let davor = 0;
  for (let index = 0; index < dateien.length; index++) {
    const datei = dateien[index];
    const url =
      source === "ws"
        ? `/api/files/upload?path=${encodeURIComponent(path)}&name=${encodeURIComponent(datei.name)}`
        : `/api/remote/${encodeURIComponent(source)}/upload?path=${encodeURIComponent(path)}&datei=${encodeURIComponent(datei.name)}`;
    const melde = (geladen) =>
      onProgress?.({ datei: datei.name, index, anzahl: dateien.length, geladen: davor + geladen, gesamt });
    melde(0);
    const antwort = await ladeEineDatei(url, datei, { onProgress: melde, signal }, maxMb);
    saved.push(...(antwort.saved || []));
    davor += datei.size;
  }
  return { saved };
}
// Austausch-Ordner je Maschine (backend/app/austausch.py): wer einen hat,
// empfängt Dateien anderer Maschinen unter <ordner>/von-<absender>/. Das
// Kopieren läuft serverseitig per SFTP von Maschine zu Maschine.
export const getAustausch = () => jget("/api/austausch");
export const setAustausch = (name, ordner) =>
  jsend(`/api/austausch/${encodeURIComponent(name)}`, "PUT", { ordner });
export const deleteAustausch = (name) =>
  jsend(`/api/austausch/${encodeURIComponent(name)}`, "DELETE");
export const sendeDateien = (von, an, pfade, nachricht) =>
  jsend("/api/austausch/senden", "POST", { von, an, pfade, nachricht });

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
// Rollen für Task-Läufe (Dashboard-Paket St.1)
export const getRollen = () => jget("/api/rollen");
export const getRolle = (name) => jget(`/api/rollen/${encodeURIComponent(name)}`);
export const saveRolle = (name, text) =>
  jsend(`/api/rollen/${encodeURIComponent(name)}`, "PUT", { text });
export const deleteRolle = (name) =>
  jsend(`/api/rollen/${encodeURIComponent(name)}`, "DELETE");

// Zeitpläne (Dashboard-Paket St.2)
export const getZeitplaene = () => jget("/api/zeitplaene");
export const saveZeitplaene = (plaene) => jsend("/api/zeitplaene", "PUT", { plaene });
export const runZeitplanJetzt = (name) =>
  jsend(`/api/zeitplaene/${encodeURIComponent(name)}/jetzt`, "POST", {});

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
//
// Frist nur für die ANTWORT-KOPFZEILEN: fetch hat keinen eigenen Timeout, und
// bei schlechtem Netz hing der Versand minutenlang — `loading` blieb wahr,
// der Senden-Knopf gesperrt, „ich kann nicht mehr weiterschreiben"
// (10.09.2026). Sobald der Server antwortet (das start-Event kommt sofort),
// darf der Strom selbst beliebig lange laufen; abgebrochen wird er nur über
// den Knopf (cancelChatStream).
const ANTWORT_FRIST_S = 30;

export async function streamChat(message, sessionId, { onStart, onTool } = {}) {
  const abbruch = new AbortController();
  const frist = setTimeout(() => abbruch.abort(), ANTWORT_FRIST_S * 1000);
  let res;
  try {
    res = await fetch("/api/chat/stream", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ message, session_id: sessionId }),
      signal: abbruch.signal,
    });
  } catch (e) {
    if (abbruch.signal.aborted)
      throw new Error(
        t(
          "Keine Antwort vom Server nach {0} s — Netz prüfen; der Text bleibt in der Eingabe.",
          ANTWORT_FRIST_S,
        ),
      );
    throw e;
  } finally {
    clearTimeout(frist);
  }
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
      else if (d.type === "error") throw new Error(d.detail || t("Orchestrator-Fehler"));
    }
  }
  // Verbindung weg, bevor done/aborted/error kam: der Turn läuft serverseitig
  // weiter und speichert — die Antwort steht danach im Verlauf.
  throw new Error(
    t(
      "Stream abgerissen — der Orchestrator arbeitet weiter; die Antwort erscheint danach im Verlauf (Session neu öffnen).",
    ),
  );
}
