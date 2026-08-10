"""FastAPI-Wrapper um Orchestrator + Mailbox + Dashboard-Daten.

Endpunkte (alle unter /api, nginx proxyt /api -> 127.0.0.1:5000):

  GET  /api/health                 Liveness (Docker-Healthcheck), LLM-frei
  GET  /api/auth/check             Login nötig/vorhanden? (öffentlich)
  POST /api/auth/login             Login (Passwort) → Session-Cookie
  POST /api/auth/logout            Session-Cookie löschen
       alles Weitere unter /api    nur mit gültigem Session-Cookie (auth.py)
  GET  /api/agents                 Agenten = Mailbox-Ordner
  GET  /api/agents/{name}/tasks    Inbox (+ .processing als running) + Outbox
  POST /api/tasks/{agent}/{id}/close  hängengebliebenen Task manuell abschließen
  POST /api/chat                   Eine Chat-Runde (Orchestrator + Tool-Calls)
  GET  /api/chat/sessions          gespeicherte Sessions (SQLite, chat_store)
  GET  /api/chat/{id}              History einer Session (Anzeige-Format)
  DEL  /api/chat/{id}              Session löschen
  GET  /api/files?path=            Dateibaum im Workspace
  GET  /api/files/content?path=    Dateiinhalt
  PUT  /api/files/content          Dateiinhalt speichern (Editor)
  GET  /api/files/download?path=   Datei herunterladen
  POST /api/files/upload?path=     Datei(en) hochladen (multipart)
  GET  /api/remote/{name}/files    dasselbe für Agenten-PCs via SFTP
  GET  /api/remote/{name}/file     (…/file lesen, PUT speichern,
  GET  /api/remote/{name}/download  …/download, …/upload)
  GET  /api/connections            SSH-Verbindungen (ohne Credentials)
  GET  /api/settings               Editierbare UI-Settings
  PUT  /api/settings               Settings speichern
  GET  /api/automatik              Automatikmodus: Not-Aus + Status je Agent
  POST /api/automatik/{name}       Automatik für einen Agenten an/aus
  POST /api/automatik/notaus       globaler Not-Aus (an = alle hart stoppen)
  GET  /api/ssh/sessions           laufende Terminal-Sessions (Badge/Auto-Reopen)
  DEL  /api/ssh/{name}/session     Terminal-Session explizit beenden (?sid=…)
  GET  /api/ssh/{name}/buffer      Klartext-Replay-Puffer einer Session (?sid=…)
  WS   /ws/ssh/{name}              SSH-Terminal-Bridge (xterm.js)

Start (lokal):  cd backend && uvicorn main:app --host 127.0.0.1 --port 5000
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from pathlib import Path
from typing import Any

from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request, Response, UploadFile, WebSocket
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from app import auth, auto_watcher, chat_store, llm, remote_files
from app.config import (
    KEYS_DIR,
    add_ui_connection,
    load_agents_full,
    load_connections,
    load_settings,
    remove_ui_connection,
    save_settings,
)
from app import files as ws_files
from app.files import (
    FilesError,
    file_path,
    list_dir,
    read_file,
    save_upload,
    write_file,
)
from app.remote_files import RemoteFilesError
from app.integrations import list_integrations
from app.mailbox import Mailbox, atomic_write_json, normalize_envelope
from app.orchestrator_core import mcp_session, run_turn
from app.ssh_bridge import bridge as ssh_bridge, get_buffer, kill_session, list_sessions

WORKSPACE = Path(os.environ.get("WORKSPACE_DIR", "/workspace"))
MAILBOXES = WORKSPACE / "mailboxes"

app = FastAPI(title="agent-dashboard", version="0.1.0")


@app.on_event("startup")
async def _automatik_start() -> None:
    """Automatikmodus-Manager (Issue #12): stellt den gewünschten Zustand aus
    settings.json wieder her und hält die Remote-Watcher per SSH."""
    auto_watcher.manager.start()


@app.on_event("shutdown")
async def _automatik_stop() -> None:
    # Container fährt herunter — hart schließen, die Reconcile-Logik startet
    # die Watcher nach dem Neustart aus settings.json neu.
    await auto_watcher.manager.stopp_alle_hart()

# --- Auth ---------------------------------------------------------------
# Alles unter /api ist geschützt außer Health (Docker-Healthcheck) und den
# Auth-Endpunkten selbst. WebSockets werden separat in ws_ssh geprüft
# (HTTP-Middleware greift dort nicht).

_PUBLIC_PATHS = {"/api/health", "/api/auth/login", "/api/auth/check"}


@app.middleware("http")
async def _require_session(request: Request, call_next):
    path = request.url.path
    if (
        path.startswith("/api")
        and path not in _PUBLIC_PATHS
        and auth.enabled()
        and not auth.check_token(request.cookies.get(auth.COOKIE_NAME))
    ):
        return JSONResponse({"detail": "nicht angemeldet"}, status_code=401)
    return await call_next(request)


class LoginIn(BaseModel):
    password: str


@app.get("/api/auth/verify")
async def auth_verify() -> Response:
    """nginx auth_request für /ext/ (externe Fenster, z. B. noVNC).

    Die Session-Middleware hat den Cookie hier schon geprüft (Pfad ist nicht
    öffentlich) — kommt die Anfrage bis hierher, ist sie autorisiert.
    """
    return Response(status_code=204)


@app.get("/api/auth/check")
async def auth_check(request: Request) -> dict:
    return {
        "required": auth.enabled(),
        "authed": (not auth.enabled())
        or auth.check_token(request.cookies.get(auth.COOKIE_NAME)),
    }


@app.post("/api/auth/login")
async def auth_login(body: LoginIn, response: Response) -> dict:
    if not auth.enabled():
        return {"ok": True}
    if not auth.verify_password(body.password):
        await asyncio.sleep(0.8)  # simple Brute-Force-Bremse
        raise HTTPException(401, "falsches Passwort")
    response.set_cookie(
        auth.COOKIE_NAME,
        auth.make_token(),
        max_age=auth.SESSION_TTL,
        httponly=True,
        samesite="lax",
        secure=True,  # https in Prod; localhost gilt im Browser als secure
    )
    return {"ok": True}


@app.post("/api/auth/logout")
async def auth_logout(response: Response) -> dict:
    response.delete_cookie(auth.COOKIE_NAME)
    return {"ok": True}

# Chat-History liegt in SQLite (/workspace/chat.db, app/chat_store.py);
# hier nur noch die pro-Session-Locks gegen parallele Turns.
_locks: dict[str, asyncio.Lock] = {}


def _lock_for(session_id: str) -> asyncio.Lock:
    return _locks.setdefault(session_id, asyncio.Lock())


# --- Modelle ---------------------------------------------------------------

class ChatIn(BaseModel):
    message: str
    session_id: str | None = None


class ChatOut(BaseModel):
    session_id: str
    reply: str
    tool_calls: list[dict]


class ExternalWindowIn(BaseModel):
    name: str
    url: str


class SettingsIn(BaseModel):
    llm_provider: str | None = None
    language: str | None = None
    telegram_enabled: bool | None = None
    orch_model: str | None = None
    external_windows: list[ExternalWindowIn] | None = None


# --- Health / Agenten / Tasks ---------------------------------------------

@app.get("/api/health")
async def health() -> dict:
    """Liveness — hängt bewusst NICHT von MCP/Anthropic ab (Docker-Healthcheck)."""
    return {"status": "ok"}


@app.get("/api/agents")
async def agents() -> dict:
    names = sorted(p.name for p in MAILBOXES.iterdir() if p.is_dir()) if MAILBOXES.exists() else []
    return {"agents": names}


def _read_jsons(folder: Path) -> list[dict[str, Any]]:
    out = []
    if folder.exists():
        for p in sorted(folder.glob("*.json")):
            try:
                out.append(json.loads(p.read_text(encoding="utf-8")))
            except json.JSONDecodeError:
                continue
    return out


@app.get("/api/agents/{name}/tasks")
async def agent_tasks(name: str) -> dict:
    base = MAILBOXES / name
    if not base.exists():
        raise HTTPException(404, f"Agent '{name}' unbekannt.")
    inbox = [e for e in _read_jsons(base / "inbox") if e.get("kind", "task") == "task"]
    # Beanspruchte Tasks (.processing/) sichtbar machen — als "running".
    claimed = [
        {**e, "status": "running"}
        for e in _read_jsons(base / "inbox" / ".processing")
        if e.get("kind", "task") == "task"
    ]
    return {"agent": name, "inbox": inbox + claimed, "outbox": _read_jsons(base / "outbox")}


@app.get("/api/agents/{name}/inbox")
async def agent_inbox(name: str, kind: str | None = None) -> dict:
    """Alle Eingänge eines Agenten (Tasks + Nachrichten + Rückfragen), normalisiert."""
    base = MAILBOXES / name
    if not base.exists():
        raise HTTPException(404, f"Agent '{name}' unbekannt.")
    items = [normalize_envelope(e) for e in _read_jsons(base / "inbox")]
    if kind:
        items = [i for i in items if i["kind"] == kind]
    return {"agent": name, "inbox": items}


@app.get("/api/questions")
async def open_questions() -> dict:
    """Offene Rückfragen (needs_confirm) über ALLE Agenten — fürs Dashboard-Banner."""
    out = []
    if MAILBOXES.exists():
        for agent_dir in sorted(MAILBOXES.iterdir()):
            if not agent_dir.is_dir():
                continue
            for env in _read_jsons(agent_dir / "inbox"):
                if env.get("kind") == "question" and env.get("status") == "needs_confirm":
                    item = normalize_envelope(env)
                    item["agent"] = agent_dir.name  # in wessen Inbox die Frage liegt
                    out.append(item)
    return {"questions": out}


class AnswerIn(BaseModel):
    text: str


@app.post("/api/questions/{agent}/{qid}/answer")
async def answer_question(agent: str, qid: str, body: AnswerIn) -> dict:
    """Eine Rückfrage beantworten: Antwort an den Fragesteller, Frage erledigen."""
    qpath = MAILBOXES / agent / "inbox" / f"{qid}.json"
    if not qpath.exists():
        raise HTTPException(404, "Rückfrage nicht gefunden.")
    question = json.loads(qpath.read_text(encoding="utf-8"))
    asker = question.get("sender") or "orchestrator"
    # Antwort in die Inbox des Fragestellers legen (Absender = der Beantworter = agent).
    Mailbox(MAILBOXES, asker).post(
        {"kind": "answer", "sender": agent, "to": asker, "text": body.text, "reply_to": qid}
    )
    # Frage als erledigt markieren (atomar überschreiben).
    question["status"] = "done"
    atomic_write_json(qpath, question)
    return {"answered": qid, "to": asker}


class CloseTaskIn(BaseModel):
    result: str = ""
    status: str = "done"


@app.post("/api/tasks/{agent}/{task_id}/close")
async def close_task(agent: str, task_id: str, body: CloseTaskIn) -> dict:
    """Hängengebliebenen Task von Hand abschließen (Knopf im Agenten-Panel).

    Für Tasks, deren Agent nicht (mehr) antwortet: schreibt eine Response in
    die Outbox und räumt den Task aus inbox/ bzw. .processing/ ab.
    """
    if body.status not in ("done", "error"):
        raise HTTPException(400, 'status muss "done" oder "error" sein.')
    base = MAILBOXES / agent
    if not base.exists():
        raise HTTPException(404, f"Agent '{agent}' unbekannt.")
    open_task = [base / "inbox" / f"{task_id}.json",
                 base / "inbox" / ".processing" / f"{task_id}.json"]
    if not any(p.exists() for p in open_task):
        raise HTTPException(404, f"Task '{task_id}' liegt nicht (mehr) bei '{agent}'.")
    Mailbox(MAILBOXES, agent).write_response(
        task_id,
        body.result or "[von Hand im Dashboard geschlossen, ohne Ergebnis]",
        body.status,
        log="closed via dashboard",
    )
    return {"closed": task_id, "agent": agent, "status": body.status}


# --- Chat ------------------------------------------------------------------

@app.post("/api/chat", response_model=ChatOut)
async def chat(body: ChatIn) -> ChatOut:
    # Env-Default + Live-Override aus den Settings (Modellwahl im Dashboard).
    cfg = llm.apply_settings(llm.provider_from_env(), load_settings())
    if llm.needs_api_key(cfg) and not os.environ.get("ANTHROPIC_API_KEY"):
        raise HTTPException(
            503, "ANTHROPIC_API_KEY fehlt — oder ORCH_PROVIDER=ollama setzen."
        )
    session_id = body.session_id or uuid.uuid4().hex
    async with _lock_for(session_id):
        messages = chat_store.load(session_id)
        messages.append({"role": "user", "content": body.message})
        try:
            async with mcp_session() as (session, tools):
                result = await run_turn(session, tools, messages, cfg)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(502, f"Orchestrator-Fehler: {exc}") from exc
        chat_store.save(session_id, messages)  # erst nach erfolgreichem Turn
    return ChatOut(session_id=session_id, reply=result["text"], tool_calls=result["tool_calls"])


@app.get("/api/chat/sessions")
async def chat_sessions() -> dict:
    return {"sessions": chat_store.list_sessions()}


@app.get("/api/chat/{session_id}")
async def chat_history(session_id: str) -> dict:
    return {"session_id": session_id, "messages": chat_store.display_messages(session_id)}


@app.delete("/api/chat/{session_id}")
async def chat_delete(session_id: str) -> dict:
    return {"deleted": chat_store.delete_session(session_id)}


# --- Dateibaum -------------------------------------------------------------

@app.get("/api/files")
async def files(path: str = "") -> dict:
    try:
        return list_dir(path)
    except FilesError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/api/files/content")
async def file_content(path: str) -> dict:
    try:
        return read_file(path)
    except FilesError as exc:
        raise HTTPException(404, str(exc)) from exc


class FileWriteIn(BaseModel):
    path: str
    content: str
    # Kodierung aus dem Lese-Ergebnis (utf-8/utf-8-sig/utf-16/cp1252) —
    # so bleibt eine Windows-Datei beim Speichern eine Windows-Datei.
    encoding: str = "utf-8"


def _attachment(filename: str) -> dict[str, str]:
    return {
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"
    }


@app.put("/api/files/content")
async def file_write(body: FileWriteIn) -> dict:
    try:
        return write_file(body.path, body.content, body.encoding)
    except FilesError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/files/download")
async def file_download(path: str) -> FileResponse:
    try:
        p = file_path(path)
    except FilesError as exc:
        raise HTTPException(404, str(exc)) from exc
    return FileResponse(p, filename=p.name, headers=_attachment(p.name))


@app.post("/api/files/upload")
async def file_upload(path: str = "", files: list[UploadFile] = None) -> dict:  # noqa: B008
    if not files:
        raise HTTPException(400, "keine Dateien im Upload")
    saved = []
    for f in files:
        try:
            saved.append(save_upload(path, f.filename, await f.read()))
        except FilesError as exc:
            raise HTTPException(400, str(exc)) from exc
    return {"saved": saved}


class MkdirIn(BaseModel):
    path: str


class RenameIn(BaseModel):
    path: str
    new_path: str


@app.post("/api/files/mkdir")
async def file_mkdir(body: MkdirIn) -> dict:
    try:
        return ws_files.make_dir(body.path)
    except FilesError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/files/rename")
async def file_rename(body: RenameIn) -> dict:
    try:
        return ws_files.rename(body.path, body.new_path)
    except FilesError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.delete("/api/files")
async def file_delete(path: str) -> dict:
    try:
        return ws_files.delete(path)
    except FilesError as exc:
        raise HTTPException(400, str(exc)) from exc


# --- Dateien auf den Agenten-PCs (SFTP) -------------------------------------

@app.get("/api/remote/{name}/files")
async def remote_list(name: str, path: str = "") -> dict:
    try:
        return await remote_files.list_dir(name, path)
    except RemoteFilesError as exc:
        raise HTTPException(502, str(exc)) from exc


@app.get("/api/remote/{name}/file")
async def remote_read(name: str, path: str) -> dict:
    try:
        return await remote_files.read_file(name, path)
    except RemoteFilesError as exc:
        raise HTTPException(502, str(exc)) from exc


@app.put("/api/remote/{name}/file")
async def remote_write(name: str, body: FileWriteIn) -> dict:
    try:
        return await remote_files.write_file(name, body.path, body.content, body.encoding)
    except RemoteFilesError as exc:
        raise HTTPException(502, str(exc)) from exc


@app.get("/api/remote/{name}/download")
async def remote_download(name: str, path: str) -> StreamingResponse:
    import posixpath

    filename = posixpath.basename(path) or "download"
    return StreamingResponse(
        remote_files.stream_file(name, path),
        media_type="application/octet-stream",
        headers=_attachment(filename),
    )


@app.post("/api/remote/{name}/mkdir")
async def remote_mkdir(name: str, body: MkdirIn) -> dict:
    try:
        return await remote_files.make_dir(name, body.path)
    except RemoteFilesError as exc:
        raise HTTPException(502, str(exc)) from exc


@app.post("/api/remote/{name}/rename")
async def remote_rename(name: str, body: RenameIn) -> dict:
    try:
        return await remote_files.rename(name, body.path, body.new_path)
    except RemoteFilesError as exc:
        raise HTTPException(502, str(exc)) from exc


@app.delete("/api/remote/{name}/files")
async def remote_delete(name: str, path: str) -> dict:
    try:
        return await remote_files.delete(name, path)
    except RemoteFilesError as exc:
        raise HTTPException(502, str(exc)) from exc


@app.post("/api/remote/{name}/upload")
async def remote_upload(name: str, path: str, files: list[UploadFile] = None) -> dict:  # noqa: B008
    if not files:
        raise HTTPException(400, "keine Dateien im Upload")
    saved = []
    for f in files:
        try:
            saved.append(await remote_files.upload_file(name, path, f.filename, f))
        except RemoteFilesError as exc:
            raise HTTPException(502, str(exc)) from exc
    return {"saved": saved}


# --- Verbindungen / Settings ----------------------------------------------

@app.get("/api/connections")
async def connections() -> dict:
    return {"connections": load_connections()}


_CONN_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,32}$")


class ConnectionIn(BaseModel):
    name: str
    host: str
    port: int = 22
    user: str
    private_key: str | None = None  # leer = Server erzeugt neues ed25519-Paar


def _setup_command(public_key: str) -> str:
    return (
        "mkdir -p ~/.ssh && chmod 700 ~/.ssh && "
        f"echo '{public_key}' >> ~/.ssh/authorized_keys && "
        "chmod 600 ~/.ssh/authorized_keys"
    )


@app.post("/api/connections")
async def connection_create(body: ConnectionIn) -> dict:
    """Neue SSH-Verbindung anlegen (agents_ui.yaml + Key unter /workspace/keys)."""
    import asyncssh

    if not _CONN_NAME_RE.match(body.name):
        raise HTTPException(400, "Name: nur Buchstaben/Zahlen/_/-, max. 32 Zeichen")
    if any(a.get("name") == body.name for a in load_agents_full()):
        raise HTTPException(400, f"Verbindung '{body.name}' existiert schon")
    if body.private_key:
        try:
            key = asyncssh.import_private_key(body.private_key)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(400, f"privater Schlüssel nicht lesbar: {exc}") from exc
    else:
        key = asyncssh.generate_private_key(
            "ssh-ed25519", comment=f"agent-dashboard:{body.name}"
        )

    KEYS_DIR.mkdir(parents=True, exist_ok=True)
    key_path = KEYS_DIR / f"{body.name}_key"
    key_path.write_bytes(key.export_private_key())
    key_path.chmod(0o600)
    public_key = key.export_public_key().decode().strip()
    (KEYS_DIR / f"{body.name}_key.pub").write_text(public_key + "\n", encoding="utf-8")

    add_ui_connection(body.name, body.host, body.port, body.user, str(key_path))
    return {
        "name": body.name,
        "public_key": public_key,
        "setup_command": _setup_command(public_key),
    }


@app.get("/api/connections/{name}/pubkey")
async def connection_pubkey(name: str) -> dict:
    """Public Key + Einrichtungsbefehl einer UI-Verbindung erneut anzeigen."""
    pub_path = KEYS_DIR / f"{name}_key.pub"
    if not pub_path.exists():
        raise HTTPException(404, "kein Dashboard-verwalteter Schlüssel für diese Verbindung")
    public_key = pub_path.read_text(encoding="utf-8").strip()
    return {
        "name": name,
        "public_key": public_key,
        "setup_command": _setup_command(public_key),
    }


@app.delete("/api/connections/{name}")
async def connection_delete(name: str) -> dict:
    """UI-verwaltete Verbindung entfernen (handgepflegte agents.yaml bleibt tabu)."""
    removed = remove_ui_connection(name)
    if removed is None:
        raise HTTPException(
            404, "nicht gefunden oder in agents.yaml gepflegt (dort von Hand entfernen)"
        )
    for suffix in ("_key", "_key.pub"):
        p = KEYS_DIR / f"{name}{suffix}"
        if p.exists():
            p.unlink()
    return {"deleted": name}


@app.get("/api/integrations")
async def integrations_list() -> dict:
    return {"integrations": list_integrations()}


@app.get("/api/models")
async def models() -> dict:
    """Verfügbare Modelle des aktiven Providers + aktuell wirksames Modell.

    LLM-frei robust: list_models ist best-effort und liefert bei Fehler [].
    """
    cfg = llm.apply_settings(llm.provider_from_env(), load_settings())
    return {
        "provider": cfg["provider"],
        "current": cfg.get("model", ""),
        "models": llm.list_models(cfg),
    }


@app.get("/api/settings")
async def get_settings() -> dict:
    return load_settings()


@app.put("/api/settings")
async def put_settings(body: SettingsIn) -> dict:
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    return save_settings(patch)


# --- Automatikmodus (Issue #12) --------------------------------------------

class AutomatikIn(BaseModel):
    an: bool


@app.get("/api/automatik")
async def automatik_status() -> dict:
    """Not-Aus + je SSH-Agent: gewünschter Zustand und ECHTER Prozess-Status."""
    return auto_watcher.manager.status()


@app.post("/api/automatik/notaus")
async def automatik_notaus(body: AutomatikIn) -> dict:
    """Globaler Not-Aus: an = alle Watcher sofort hart stoppen. Beim Lösen
    starten die einzeln eingeschalteten Automatiken wieder (settings.json)."""
    await auto_watcher.manager.notaus(body.an)
    return auto_watcher.manager.status()


@app.post("/api/automatik/{name}")
async def automatik_schalten(name: str, body: AutomatikIn) -> dict:
    """Automatik eines Agenten an/aus. Aus = sanft: laufender Task darf fertig
    werden, danach endet der Watcher-Prozess auf dem Agenten-PC wirklich."""
    status = auto_watcher.manager.status()
    agent = status["agents"].get(name)
    if agent is None:
        raise HTTPException(404, f"kein SSH-Agent '{name}'")
    if body.an and not agent["startbar"]:
        raise HTTPException(409, f"'{name}' hat keine nutzbare SSH-Verbindung (key_file?)")
    await auto_watcher.manager.schalte(name, body.an)
    return auto_watcher.manager.status()


# --- SSH-Terminal ----------------------------------------------------------

@app.get("/api/ssh/sessions")
async def ssh_sessions() -> dict:
    """Laufende Terminal-Sessions — fürs UI (Badge an den Tabs, Auto-Reopen)."""
    return {"sessions": list_sessions()}


@app.delete("/api/ssh/{name}/session")
async def ssh_session_kill(name: str, sid: str) -> dict:
    """Persistente Terminal-Session explizit beenden (Beenden-Button im UI)."""
    return {"killed": await kill_session(name, sid)}


@app.get("/api/ssh/{name}/buffer")
async def ssh_session_buffer(name: str, sid: str) -> dict:
    """Klartext-Verlauf einer laufenden Terminal-Session (Kopier-Modus).

    Liefert den serverseitigen Replay-Puffer ANSI-bereinigt — im Gegensatz
    zum xterm-Puffer enthält er auch, was eine Alt-Screen-TUI (Claude Code)
    nur neu gezeichnet statt gescrollt hat.
    """
    text = get_buffer(name, sid)
    if text is None:
        raise HTTPException(404, f"Keine laufende Terminal-Session '{sid}' für '{name}'.")
    return {"name": name, "sid": sid, "text": text}


@app.websocket("/ws/ssh/{name}")
async def ws_ssh(websocket: WebSocket, name: str) -> None:
    if auth.enabled() and not auth.check_token(websocket.cookies.get(auth.COOKIE_NAME)):
        # erst accept, dann close: nur so kommt der Code 4401 beim Client an
        # (Reconnect-Logik unterscheidet "nicht angemeldet" von Netz-Abbruch)
        await websocket.accept()
        await websocket.close(code=4401)
        return
    await ssh_bridge(websocket, name)
