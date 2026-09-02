"""FastAPI-Wrapper um Orchestrator + Mailbox + Dashboard-Daten.

Endpunkte (alle unter /api, nginx proxyt /api -> 127.0.0.1:5000):

  GET  /api/health                 Liveness (Docker-Healthcheck), LLM-frei
  GET  /api/auth/check             Login nötig/vorhanden? (öffentlich)
  POST /api/auth/login             Login (Passwort) → Session-Cookie
  POST /api/auth/logout            Session-Cookie löschen
       alles Weitere unter /api    nur mit gültigem Session-Cookie (auth.py)
  GET  /api/agents                 Agenten = Mailbox-Ordner
  GET  /api/agents/{name}/tasks    Inbox (+ .processing als running) + Outbox
                                   + messages (Nachrichten/Antworten, #33)
  POST /api/agents/{name}/inbox/read-all  alles Erledigte ins Archiv (#21)
  POST /api/agents/{name}/inbox/{id}/read  eine Nachricht ins Archiv (#33)
  POST /api/tasks/{agent}/{id}/close  hängengebliebenen Task manuell abschließen
  POST /api/chat                   Eine Chat-Runde (Orchestrator + Tool-Calls)
  POST /api/chat/stream            dito als SSE-Strom: tool-Events live + Abbruch (F3)
  POST /api/chat/stream/{id}/cancel  laufenden Stream-Turn anhalten
  GET  /api/chat/sessions          gespeicherte Sessions (SQLite, chat_store)
  GET  /api/chat/{id}              History einer Session (Anzeige-Format)
  DEL  /api/chat/{id}              Session löschen
  GET  /api/files?path=            Dateibaum im Workspace
  GET  /api/files/content?path=    Dateiinhalt
  PUT  /api/files/content          Dateiinhalt speichern (Editor)
  GET  /api/files/download?path=   Datei herunterladen
  GET  /api/files/raw?path=        Datei anzeigen/abspielen (inline, echter Typ)
  POST /api/files/upload?path=     Datei(en) hochladen (multipart)
  GET  /api/remote/{name}/files    dasselbe für Agenten-PCs via SFTP
  GET  /api/remote/{name}/file     (…/file lesen, PUT speichern,
  GET  /api/remote/{name}/download  …/download, …/raw, …/upload)
  POST /mcp/{agent}                MCP-Kanal über HTTPS (Bearer-Token je Agent)
  GET  /api/connections            SSH-Verbindungen (ohne Credentials)
  GET  /api/rollen                 Rollen für Task-Läufe (config/rollen/*.md)
  GET  /api/rollen/{name}          eine Rolle: Rohtext + geparste Felder
  PUT  /api/rollen/{name}          Rolle speichern · DELETE /api/rollen/{name} löschen
  GET  /api/zeitplaene             geplante Tasks (config/zeitplaene.yaml)
  PUT  /api/zeitplaene             Pläne speichern (ersetzt die Liste, validiert)
  POST /api/zeitplaene/{name}/jetzt  einen Plan sofort laufen lassen (Test)
  GET  /api/settings               Editierbare UI-Settings
  PUT  /api/settings               Settings speichern
  GET  /api/automatik              Automatikmodus: Not-Aus + Status je Agent
  POST /api/automatik/{name}       Automatik für einen Agenten an/aus
  POST /api/automatik/notaus       globaler Not-Aus (an = alle hart stoppen)
  GET  /api/events                 Mailbox-Änderungen als SSE (F4, Polling bleibt Fallback)
  GET  /api/push/key               Web-Push: VAPID-Key + Status (F10)
  POST /api/push/subscribe         Gerät für Push registrieren (Browser-Subscription)
  POST /api/push/unsubscribe       Gerät abmelden
  POST /api/push/test              Testbenachrichtigung an alle Geräte
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

import httpx
import mimetypes
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request, Response, UploadFile, WebSocket
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from app import auth, auto_watcher, chat_store, events, llm, push, remote_files
from app import mcp_scope, mcp_token, rollen, zeitplaene
from app.config import (
    KEYS_DIR,
    add_ui_connection,
    ist_erlaubtes_ext_ziel,
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
from app.mailbox import AGENT_NAME_RE, ORCHESTRATOR, Mailbox, normalize_envelope
from app.mailbox import pflege as mailbox_pflege
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


# Mailbox-Pflege: wie lange darf ein Task in .processing/ liegen, bevor er als
# verwaist gilt (Watcher gestorben), und wie lange bleiben Archiv/Outbox liegen.
# WICHTIG: STALE_TASK_ALTER muss deutlich über dem CLAUDE_TIMEOUT des Watchers
# liegen (dort 1800 s), sonst wird ein noch laufender Task ein zweites Mal
# eingereiht und doppelt ausgeführt. 3 h = 6-facher Sicherheitsabstand.
PFLEGE_INTERVALL = float(os.environ.get("MAILBOX_PFLEGE_INTERVALL", "900"))
STALE_TASK_ALTER = float(os.environ.get("MAILBOX_STALE_ALTER", "10800"))  # 3 h
ARCHIV_TAGE = float(os.environ.get("MAILBOX_ARCHIV_TAGE", "30"))
# Issue #21: alte response/answer wandern auch aus der INBOX ins Archiv — sonst
# wächst sie bei einem Agenten, der nie mark_read ruft, unbegrenzt weiter (und
# das Agenten-Panel zieht den ganzen Stapel alle 8 s mit). Tasks/Fragen bleiben
# unangetastet. 0 schaltet die Inbox-Rotation ab.
INBOX_TAGE = float(os.environ.get("MAILBOX_INBOX_TAGE", "14"))

_pflege_task: asyncio.Task | None = None


async def _mailbox_pflege_schleife() -> None:
    """Verwaiste Tasks zurück in die Warteschlange, alte Ablagen rotieren.

    Bewusst im API-Prozess und nicht im Watcher: der Watcher ist genau der
    Prozess, der bei Absturz/Not-Aus/Stromausfall stirbt und seinen Task als
    ewiges "running" hinterlässt.
    """
    while True:
        await asyncio.sleep(PFLEGE_INTERVALL)
        try:
            bericht = await asyncio.to_thread(
                mailbox_pflege, MAILBOXES, STALE_TASK_ALTER, ARCHIV_TAGE,
                INBOX_TAGE
            )
        except Exception as exc:  # noqa: BLE001 — Pflege darf die API nie killen
            print(f"[pflege] Fehler: {exc}", flush=True)
            continue
        if bericht["requeued"] or bericht["aufgegeben"] or bericht["geloescht"]:
            print(
                f"[pflege] wieder eingereiht: {bericht['requeued'] or '-'}; "
                f"aufgegeben: {bericht['aufgegeben'] or '-'}; "
                f"alte Ablagen gelöscht: {bericht['geloescht']}",
                flush=True,
            )


@app.on_event("startup")
async def _pflege_start() -> None:
    global _pflege_task
    _pflege_task = asyncio.create_task(_mailbox_pflege_schleife())


@app.on_event("startup")
async def _events_start() -> None:
    """Mailbox-Wächter (F4/F10): SSE-Events an die Frontends + Push-Auslöser."""
    events.start()


@app.on_event("startup")
async def _planer_start() -> None:
    """Zeitpläne (Paket St.2): fällige Pläne als ganz normale Tasks posten —
    Automatik, Rückfragen und Push greifen dann von selbst."""
    zeitplaene.start()


@app.on_event("shutdown")
async def _automatik_stop() -> None:
    # Container fährt herunter — hart schließen, die Reconcile-Logik startet
    # die Watcher nach dem Neustart aus settings.json neu.
    if _pflege_task is not None:
        _pflege_task.cancel()
    events.stop()
    zeitplaene.stop()
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
async def auth_verify(request: Request) -> Response:
    """nginx auth_request für /ext/ (externe Fenster, z. B. noVNC).

    Die Session-Middleware hat den Cookie schon geprüft — das allein genügt
    aber nicht: der /ext/-Proxy erlaubt nginx-seitig JEDE private IPv4, und
    was er ausliefert, läuft unter der Origin des Dashboards. Eine beliebige
    LAN-Seite könnte damit per JavaScript die gesamte API mit dem
    Session-Cookie bedienen (Dateien, SSH-Keys, Terminals). Deshalb hier die
    zweite Hälfte der Prüfung: nur ausdrücklich eingetragene Ziele.

    Maßgeblich ist X-Ext-Ziel (nginx-Captures = echtes Proxy-Ziel), NICHT die
    Original-URI — siehe ist_erlaubtes_ext_ziel.
    """
    if not ist_erlaubtes_ext_ziel(request.headers.get("X-Ext-Ziel")):
        return Response(status_code=403)
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
    lock = _locks.setdefault(session_id, asyncio.Lock())
    if len(_locks) > 200:
        # Ohne Aufräumen wächst der Dict mit jeder je gesehenen Session-ID.
        # Ungenutzte Locks (niemand wartet, keiner hält) dürfen weg.
        for sid, l in [(s, x) for s, x in _locks.items() if s != session_id]:
            if not l.locked():
                _locks.pop(sid, None)
    return lock




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
    # Achtung: neue Felder müssen AUCH in config.ALLOWED_KEYS stehen, sonst
    # verwirft save_settings sie still.
    language: str | None = None
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


# Envelope-/Task-IDs kommen als Pfadsegment aus der URL. Ohne Prüfung wäre
# `%2e%2e` ein Weg aus mailboxes/ heraus — und answer_question SCHREIBT auf
# den zusammengebauten Pfad. Konvention des Projekts: nie roh joinen.
_ENV_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def _agent_base(name: str) -> Path:
    """Geprüftes Mailbox-Verzeichnis eines Agenten."""
    if not AGENT_NAME_RE.fullmatch(name):
        raise HTTPException(400, f"ungültiger Agentenname: {name!r}")
    base = MAILBOXES / name
    if not base.is_dir():
        raise HTTPException(404, f"Agent '{name}' unbekannt.")
    return base


def _geprüfte_id(wert: str, feld: str) -> str:
    if not _ENV_ID_RE.fullmatch(wert or ""):
        raise HTTPException(400, f"ungültige {feld}: {wert!r}")
    return wert


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
    base = _agent_base(name)
    roh = _read_jsons(base / "inbox")
    inbox = [e for e in roh if e.get("kind", "task") == "task"]
    # Beanspruchte Tasks (.processing/) sichtbar machen — als "running";
    # geparkte (Rückfrage offen, Issue #17) behalten ihr needs_confirm.
    claimed = [
        {**e, "status": "needs_confirm" if e.get("status") == "needs_confirm" else "running"}
        for e in _read_jsons(base / "inbox" / ".processing")
        if e.get("kind", "task") == "task"
    ]
    # Alles Nicht-Task aus derselben Lesung mitgeben (Issue #33): Nachrichten,
    # Antworten und Task-Ergebnisse lagen zwar in der Inbox, waren am Dashboard
    # aber unsichtbar — wer am Handy nachsah, hielt die Zustellung für kaputt.
    # Bewusst im SELBEN Endpunkt statt als zweiter Poll: das Panel fragt alle
    # 8 s für JEDEN Agenten, ein zweiter Aufruf verdoppelte diese Last.
    messages = sorted(
        (normalize_envelope(e) for e in roh if e.get("kind", "task") != "task"),
        key=lambda m: m.get("created_at") or "",
        reverse=True,  # neueste zuerst — anders als Tasks, die FIFO abgearbeitet werden
    )
    return {
        "agent": name,
        "inbox": inbox + claimed,
        "outbox": _read_jsons(base / "outbox"),
        "messages": messages,
    }


@app.get("/api/agents/{name}/inbox")
async def agent_inbox(name: str, kind: str | None = None) -> dict:
    """Alle Eingänge eines Agenten (Tasks + Nachrichten + Rückfragen), normalisiert."""
    base = _agent_base(name)
    items = [normalize_envelope(e) for e in _read_jsons(base / "inbox")]
    if kind:
        items = [i for i in items if i["kind"] == kind]
    return {"agent": name, "inbox": items}


@app.post("/api/agents/{name}/inbox/read-all")
async def agent_inbox_read_all(name: str) -> dict:
    """Alles Erledigte auf einmal aus der Inbox ins Archiv (Issue #21).

    Gegenstück zum `mark_read` je Envelope: wer nur beauftragt und die
    Ergebnisse hier im Dashboard liest, müsste sonst jede einzelne Response
    von Hand quittieren. Offene Tasks und offene Rückfragen bleiben liegen.
    """
    _agent_base(name)
    archiviert = Mailbox(MAILBOXES, name).alle_gelesen()
    return {"agent": name, "archiviert": archiviert}


@app.post("/api/agents/{name}/inbox/{envelope_id}/read")
async def agent_envelope_read(name: str, envelope_id: str) -> dict:
    """Eine einzelne Nachricht ins Archiv legen (✕ an der Karte, Issue #33).

    Gegenstück zum `mark_read` der MCP-Seite: wer eine Nachricht im Panel
    gelesen hat, soll sie einzeln wegräumen können, ohne mit "alles gelesen"
    auch alles andere zu quittieren. Offene Tasks lassen sich so NICHT
    schließen (dafür /api/tasks/{agent}/{id}/close).
    """
    _agent_base(name)
    _geprüfte_id(envelope_id, "Envelope-ID")
    try:
        moved = Mailbox(MAILBOXES, name).mark_read(envelope_id)
    except ValueError as exc:  # offener Task
        raise HTTPException(400, str(exc)) from exc
    if not moved:
        raise HTTPException(404, f"Envelope '{envelope_id}' liegt nicht in der Inbox von '{name}'.")
    return {"archived": envelope_id, "agent": name}


@app.get("/api/questions")
async def open_questions(to: str | None = None) -> dict:
    """Offene Rückfragen (needs_confirm) über ALLE Agenten — fürs Dashboard-Banner.

    Am Dashboard sitzt ein MENSCH, und der ist der Orchestrator. Fragen, die
    zwei Agenten untereinander stellen, landen hier zwar auch (sie liegen in
    einer Mailbox und niemand sonst sieht sie), dürfen aber nicht wie eine
    Entscheidung aussehen, die er zu treffen hat — daher `fuer_mensch` je
    Frage und optional `?to=<agent>` als harter Filter (Issue #22).
    """
    out = []
    if MAILBOXES.exists():
        for agent_dir in sorted(MAILBOXES.iterdir()):
            if not agent_dir.is_dir():
                continue
            if to and agent_dir.name != to:
                continue
            for env in _read_jsons(agent_dir / "inbox"):
                if env.get("kind") == "question" and env.get("status") == "needs_confirm":
                    item = normalize_envelope(env)
                    item["agent"] = agent_dir.name  # in wessen Inbox die Frage liegt
                    item["fuer_mensch"] = agent_dir.name == ORCHESTRATOR
                    out.append(item)
    return {"questions": out, "orchestrator": ORCHESTRATOR}


class AnswerIn(BaseModel):
    text: str


@app.post("/api/questions/{agent}/{qid}/answer")
async def answer_question(agent: str, qid: str, body: AnswerIn) -> dict:
    """Eine Rückfrage beantworten: Antwort an den Fragesteller, Frage erledigen.

    Dieselbe Primitive wie das MCP-Tool `answer` (mailbox.beantworte_frage) —
    sonst driften die beiden Wege auseinander (Frage bleibt offen bzw. liegt
    für immer in der Inbox).

    `answered_by="dashboard"` geht in den Antwort-Envelope: hier antwortet ein
    Mensch, unter Umständen anstelle des eigentlich gefragten Agenten — für
    den Fragesteller wäre das sonst nicht zu erkennen (Issue #22).
    """
    base = _agent_base(agent)
    _geprüfte_id(qid, "Rückfrage-ID")
    if not (base / "inbox" / f"{qid}.json").exists():
        raise HTTPException(404, "Rückfrage nicht gefunden.")
    try:
        ergebnis = Mailbox(MAILBOXES, agent).beantworte_frage(
            qid, body.text, answered_by="dashboard"
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "answered": qid,
        "to": ergebnis["to"],
        "wieder_angestossen": ergebnis["wieder_angestossen"],
    }


class CloseQuestionIn(BaseModel):
    grund: str = ""


@app.post("/api/questions/{agent}/{qid}/close")
async def close_question(agent: str, qid: str, body: CloseQuestionIn) -> dict:
    """Rückfrage ohne Antwort schließen — Gegenstück zum ✕ am Task (Issue #23).

    Eine Frage, die sich erledigt hat oder falsch adressiert war, hatte bisher
    keinen Ausgang außer einer Antwort — und mit ihr blieb der seit Issue #17
    geparkte Task für immer in .processing/ liegen. Hier wird die Frage
    archiviert und der wartende Task scheitert mit Klartext: über Issue #15
    landet er samt instruction in inbox/.failed/ und ist wiederanlauffähig.
    """
    base = _agent_base(agent)
    _geprüfte_id(qid, "Rückfrage-ID")
    if not (base / "inbox" / f"{qid}.json").exists():
        raise HTTPException(404, "Rückfrage nicht gefunden.")
    ergebnis = Mailbox(MAILBOXES, agent).schliesse_frage(qid, body.grund)
    return {
        "closed": qid,
        "to": ergebnis["to"],
        "gescheiterte_tasks": ergebnis["gescheiterte_tasks"],
    }


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
    base = _agent_base(agent)
    _geprüfte_id(task_id, "Task-ID")
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
            # History trotzdem sichern: bis hierher ausgeführte Tool-Calls
            # (send_task!) sind echte Seiteneffekte. Würfe man die Runde weg,
            # wüsste der Orchestrator beim nächsten Turn nichts davon und
            # verschickte womöglich alles ein zweites Mal.
            chat_store.save(session_id, llm.repariere_history(messages))
            raise HTTPException(502, f"Orchestrator-Fehler: {exc}") from exc
        chat_store.save(session_id, messages)
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


# --- Chat-Streaming (F3) ----------------------------------------------------
# Laufende Streams: stream_id -> Abbruch-Flag. Der Abbrechen-Knopf setzt es;
# es greift VOR dem nächsten Tool-Call (ein laufender LLM-Call läuft durch).
_stream_abbruch: dict[str, bool] = {}
# create_task-Referenzen halten: asyncio hält laufende Tasks nur schwach —
# ohne das Set könnte der GC einen laufenden Chat-Turn einsammeln.
_chat_tasks: set[asyncio.Task] = set()

_SSE_HEADERS = {
    # nginx puffert /api/ (kein proxy_buffering off dort) — dieser Header
    # schaltet das je Antwort ab, sonst käme der Strom erst am Ende an.
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
}


@app.post("/api/chat/stream")
async def chat_stream(body: ChatIn) -> StreamingResponse:
    """Wie /api/chat, aber als SSE-Strom: start → tool… → done/aborted/error.

    Der Blackbox-POST zeigte bei bis zu 25 Tool-Runden minutenlang nur
    „denkt…" — hier sieht das Frontend jeden Tool-Call live und kann
    abbrechen. Verliert der Client die Verbindung (Handy gesperrt), läuft der
    Turn serverseitig weiter und speichert — die Antwort steht dann wie beim
    alten Endpunkt im gespeicherten Verlauf.
    """
    cfg = llm.apply_settings(llm.provider_from_env(), load_settings())
    if llm.needs_api_key(cfg) and not os.environ.get("ANTHROPIC_API_KEY"):
        raise HTTPException(
            503, "ANTHROPIC_API_KEY fehlt — oder ORCH_PROVIDER=ollama setzen."
        )
    session_id = body.session_id or uuid.uuid4().hex
    stream_id = uuid.uuid4().hex
    _stream_abbruch[stream_id] = False
    queue: asyncio.Queue = asyncio.Queue()

    async def lauf() -> None:
        try:
            async with _lock_for(session_id):
                messages = chat_store.load(session_id)
                messages.append({"role": "user", "content": body.message})
                try:
                    async with mcp_session() as (session, tools):
                        result = await run_turn(
                            session, tools, messages, cfg,
                            on_tool=lambda name: queue.put({"type": "tool", "name": name}),
                            ist_abgebrochen=lambda: _stream_abbruch.get(stream_id, False),
                        )
                except llm.TurnAbbruch:
                    # Bis hierher ausgeführte Tools sind echte Seiteneffekte —
                    # History reparieren und sichern statt wegwerfen.
                    chat_store.save(session_id, llm.repariere_history(messages))
                    await queue.put({"type": "aborted", "session_id": session_id})
                    return
                except Exception as exc:  # noqa: BLE001 — wie /api/chat: Stand sichern
                    chat_store.save(session_id, llm.repariere_history(messages))
                    await queue.put(
                        {"type": "error", "detail": f"Orchestrator-Fehler: {exc}"}
                    )
                    return
                chat_store.save(session_id, messages)
                await queue.put(
                    {
                        "type": "done",
                        "session_id": session_id,
                        "reply": result["text"],
                        "tool_calls": result["tool_calls"],
                    }
                )
        finally:
            _stream_abbruch.pop(stream_id, None)

    task = asyncio.create_task(lauf())
    _chat_tasks.add(task)
    task.add_done_callback(_chat_tasks.discard)

    async def gen():
        start = {"type": "start", "stream_id": stream_id, "session_id": session_id}
        yield f"data: {json.dumps(start)}\n\n"
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=20)
            except asyncio.TimeoutError:
                yield ": ping\n\n"  # hält nginx' 60-s-Read-Timeout fern
                continue
            yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
            if item["type"] in ("done", "aborted", "error"):
                return

    return StreamingResponse(gen(), media_type="text/event-stream", headers=_SSE_HEADERS)


@app.post("/api/chat/stream/{stream_id}/cancel")
async def chat_stream_cancel(stream_id: str) -> dict:
    """Laufenden Stream-Turn anhalten — greift vor dem nächsten Tool-Call."""
    bekannt = stream_id in _stream_abbruch
    if bekannt:
        _stream_abbruch[stream_id] = True
    return {"ok": True, "known": bekannt}


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


# Inline-Auslieferung für Vorschau und Wiedergabe im Dashboard (Issues #25/#26).
# Ohne sie führt jeder Blick auf ein Screenshot, PDF oder eine Sprachnotiz über
# den Download-Ordner und eine fremde App — auf dem Handy so umständlich, dass
# man es lässt.
#
# Skriptfähige Formate werden dabei eingesperrt: Eine SVG- oder HTML-Datei darf
# ein Agent jederzeit ins Projekt legen, und inline im Origin des Dashboards
# ausgeliefert liefe ihr Skript mit den Rechten der angemeldeten Sitzung. Die
# CSP `sandbox` (ohne allow-scripts) unterbindet das. Bilder, PDF und Audio
# bekommen sie NICHT: Chromes PDF-Betrachter braucht Skripte und bliebe sonst
# leer.
SKRIPTFAEHIG = {
    "image/svg+xml",
    "text/html",
    "application/xhtml+xml",
    "text/xml",
    "application/xml",
}


def _medientyp(filename: str) -> str:
    typ, _ = mimetypes.guess_type(filename)
    return typ or "application/octet-stream"


def _inline(filename: str, medientyp: str) -> dict[str, str]:
    kopf = {
        "Content-Disposition": f"inline; filename*=UTF-8''{quote(filename)}"
    }
    if medientyp in SKRIPTFAEHIG:
        kopf["Content-Security-Policy"] = "sandbox"
    return kopf


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


@app.get("/api/files/raw")
async def file_raw(path: str) -> FileResponse:
    """Wie /download, aber zum Anzeigen im Dashboard statt zum Speichern.

    Der Medientyp MUSS stimmen: nginx setzt `X-Content-Type-Options: nosniff`,
    ein `application/octet-stream` ergäbe also eine leere Fläche bzw. einen
    stummen Player. FileResponse bringt Range-Requests mit — ohne die kann
    Safari Audio weder abspielen noch spulen.
    """
    try:
        p = file_path(path)
    except FilesError as exc:
        raise HTTPException(404, str(exc)) from exc
    typ = _medientyp(p.name)
    return FileResponse(p, media_type=typ, headers=_inline(p.name, typ))


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


@app.get("/api/remote/{name}/raw")
async def remote_raw(name: str, path: str) -> StreamingResponse:
    """Inline-Fassung für entfernte Quellen.

    Einschränkung gegenüber dem lokalen Weg: Ein Stream kann keine
    Range-Requests beantworten. Bilder und PDFs sind davon unberührt, Audio
    spielt von vorn, lässt sich aber nicht spulen — und Safari verweigert die
    Wiedergabe womöglich ganz.
    """
    import posixpath

    filename = posixpath.basename(path) or "download"
    typ = _medientyp(filename)
    return StreamingResponse(
        remote_files.stream_file(name, path),
        media_type=typ,
        headers=_inline(filename, typ),
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


# --- Rollen (Dashboard-Paket St.1) -----------------------------------------
# Rollen-Dateien pflegt der Mensch (Agenten-Panel → Rollen-Dialog); der
# MCP-Server bettet sie beim send_task in den Task-Envelope ein. Namensprüfung
# macht rollen._pfad (dieselbe Strenge wie Agentennamen — Path-Traversal).

class RolleIn(BaseModel):
    text: str


@app.get("/api/rollen")
async def rollen_liste() -> dict:
    return {"rollen": rollen.liste_rollen()}


@app.get("/api/rollen/{name}")
async def rolle_lesen(name: str) -> dict:
    try:
        text = rollen.roher_text(name)
    except rollen.RollenFehler as exc:
        raise HTTPException(400, str(exc)) from exc
    if text is None:
        raise HTTPException(404, f"Rolle '{name}' gibt es nicht.")
    # Geparste Felder best-effort mitliefern — eine von Hand zerschriebene
    # Datei soll im Dialog trotzdem aufgehen (zum Reparieren).
    try:
        geparst = rollen.lade_rolle(name)
    except rollen.RollenFehler as exc:
        geparst = {"fehler": str(exc)}
    return {"name": name, "text": text, "geparst": geparst}


@app.put("/api/rollen/{name}")
async def rolle_speichern(name: str, body: RolleIn) -> dict:
    try:
        gespeichert = rollen.speichere_rolle(name, body.text)
    except rollen.RollenFehler as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"gespeichert": name, **{k: v for k, v in gespeichert.items() if k != "prompt"}}


@app.delete("/api/rollen/{name}")
async def rolle_loeschen(name: str) -> dict:
    try:
        weg = rollen.loesche_rolle(name)
    except rollen.RollenFehler as exc:
        raise HTTPException(400, str(exc)) from exc
    if not weg:
        raise HTTPException(404, f"Rolle '{name}' gibt es nicht.")
    return {"geloescht": name}


# --- Zeitpläne (Dashboard-Paket St.2) ---------------------------------------
# Der Planer-Loop (app/zeitplaene.py) läuft im API-Prozess; hier nur die
# Verwaltung. PUT ersetzt die ganze Liste (der Dialog schickt sie komplett),
# `letzter_lauf` bestehender Pläne bleibt dabei serverseitig erhalten.

class ZeitplaeneIn(BaseModel):
    plaene: list[dict[str, Any]]


@app.get("/api/zeitplaene")
async def zeitplaene_liste() -> dict:
    plaene, fehler = zeitplaene.lade_plaene()
    return {"plaene": plaene, **({"fehler": fehler} if fehler else {})}


@app.put("/api/zeitplaene")
async def zeitplaene_speichern(body: ZeitplaeneIn) -> dict:
    try:
        return {"plaene": zeitplaene.speichere_plaene(body.plaene)}
    except zeitplaene.ZeitplanFehler as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/zeitplaene/{name}/jetzt")
async def zeitplan_jetzt(name: str) -> dict:
    """Einen Plan sofort laufen lassen — der Test-Knopf im Dialog."""
    try:
        bericht = zeitplaene.jetzt_ausfuehren(name)
    except zeitplaene.ZeitplanFehler as exc:
        raise HTTPException(404, str(exc)) from exc
    if bericht.get("fehler"):
        raise HTTPException(400, bericht["fehler"])
    return bericht


@app.get("/api/models")
async def models() -> dict:
    """Verfügbare Modelle des aktiven Providers + aktuell wirksames Modell.

    LLM-frei robust: list_models ist best-effort und liefert bei Fehler [].
    """
    cfg = llm.apply_settings(llm.provider_from_env(), load_settings())
    return {
        "provider": cfg["provider"],
        "current": cfg.get("model", ""),
        # Synchroner HTTP-Aufruf mit 10 s Timeout: im Thread, sonst steht bei
        # hängendem Ollama der ganze Event-Loop (API + Terminals).
        "models": await asyncio.to_thread(llm.list_models, cfg),
    }


@app.get("/api/settings")
async def get_settings() -> dict:
    return load_settings()


@app.put("/api/settings")
async def put_settings(body: SettingsIn) -> dict:
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    return save_settings(patch)


# --- Live-Events (F4) -------------------------------------------------------

@app.get("/api/events")
async def events_stream() -> StreamingResponse:
    """Mailbox-Änderungen als SSE — das Frontend lädt dann sofort nach.

    Das 5–8-s-Polling im Frontend bleibt als Fallback: reißt dieser Strom ab
    (Standby, Proxy), stimmt die Anzeige spätestens einen Poll später wieder.
    """
    async def gen():
        q = events.broadcaster.subscribe()
        try:
            yield "retry: 3000\n\n"  # Reconnect-Abstand für den Browser
            yield f"data: {json.dumps({'type': 'hallo'})}\n\n"
            while True:
                try:
                    item = await asyncio.wait_for(q.get(), timeout=25)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"  # hält nginx' 60-s-Read-Timeout fern
                    continue
                yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
        finally:
            events.broadcaster.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream", headers=_SSE_HEADERS)


# --- Web-Push (F10) ---------------------------------------------------------

class PushSubIn(BaseModel):
    # Exakt das JSON von PushSubscription.toJSON() im Browser.
    endpoint: str
    keys: dict[str, str] = {}
    expirationTime: float | None = None


class PushUnsubIn(BaseModel):
    endpoint: str


@app.get("/api/push/key")
async def push_key() -> dict:
    """VAPID-Public-Key (beim ersten Aufruf erzeugt) + Versand-Status."""
    key = await asyncio.to_thread(push.public_key)
    return {
        "enabled": key is not None,
        "key": key,
        "subscriptions": push.anzahl_subscriptions(),
        # False = pywebpush (noch) nicht im Image: Subscriptions sammeln geht,
        # gesendet wird erst nach dem nächsten Rebuild.
        "sender": push.versand_verfuegbar(),
    }


@app.post("/api/push/subscribe")
async def push_subscribe(body: PushSubIn) -> dict:
    try:
        n = push.add_subscription(body.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "subscriptions": n}


@app.post("/api/push/unsubscribe")
async def push_unsubscribe(body: PushUnsubIn) -> dict:
    return {"removed": push.remove_subscription(body.endpoint)}


@app.post("/api/push/test")
async def push_test() -> dict:
    """Testbenachrichtigung an alle Geräte (Knopf in den Settings)."""
    n = await push.sende_an_alle(
        "agent-dashboard", "Push-Benachrichtigungen funktionieren.", tag="push-test"
    )
    return {"gesendet": n, "subscriptions": push.anzahl_subscriptions()}


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


# --- MCP über HTTPS (Issue #32) --------------------------------------------
# Agenten ohne SSH — ein Notebook mit Claude Desktop, ein Gerät hinter NAT —
# erreichen ihren gebundenen MCP-Kanal über denselben HTTPS-Zugang wie der
# Browser. Das Dashboard reicht die Anfrage an den Loopback-Port des Kanals
# weiter; die MCP-Ports selbst bleiben unveröffentlicht wie bisher.
#
# Die Identität kommt weiter aus dem Kanal, nicht aus einem Parameter: Ein
# Token öffnet genau den Port SEINES Agenten. Wer Token X hat, kann nicht als
# Y auftreten, weil er Y's Port nicht erreicht (Issue #13).

_MCP_WEITERGELEITETE_KOPFZEILEN = {
    "content-type",
    "accept",
    "mcp-session-id",
    "mcp-protocol-version",
    "last-event-id",
}


async def _mcp_weiterleiten(request: Request, agent: str, rest: str = "") -> Response:
    try:
        mcp_token.pruefe(agent, request.headers.get("authorization"))
    except mcp_token.TokenFehler as exc:
        print(f"[mcp-http] abgelehnt: {exc}", flush=True)
        # Nach außen bewusst ohne Grund: Ob ein Agent existiert, ob sein Token
        # fehlt oder falsch ist, geht einen nicht angemeldeten Client nichts an.
        return JSONResponse({"detail": "nicht berechtigt"}, status_code=401)

    scopes = mcp_scope.read_port_map()
    port = scopes.get(agent)
    if not port:
        return JSONResponse(
            {"detail": "für diesen Agenten gibt es keinen gebundenen Kanal"},
            status_code=404,
        )

    ziel = f"http://127.0.0.1:{port}/mcp{rest}"
    kopf = {
        k: v
        for k, v in request.headers.items()
        if k.lower() in _MCP_WEITERGELEITETE_KOPFZEILEN
    }
    rumpf = await request.body()

    client = httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=None))
    try:
        anfrage = client.build_request(
            request.method, ziel, headers=kopf, content=rumpf
        )
        # `stream=True`: Streamable HTTP antwortet je nach Aufruf mit einer
        # einzelnen JSON-Antwort ODER einem offenen SSE-Strom. Würde hier auf
        # den vollständigen Rumpf gewartet, bliebe jede Server-Meldung liegen,
        # bis die Verbindung endet.
        antwort = await client.send(anfrage, stream=True)
    except httpx.HTTPError as exc:
        await client.aclose()
        return JSONResponse(
            {"detail": f"MCP-Kanal nicht erreichbar: {exc}"}, status_code=502
        )

    async def strom():
        try:
            async for stueck in antwort.aiter_raw():
                yield stueck
        finally:
            await antwort.aclose()
            await client.aclose()

    durchreichen = {
        k: v
        for k, v in antwort.headers.items()
        if k.lower() in {"content-type", "mcp-session-id", "cache-control"}
    }
    return StreamingResponse(
        strom(), status_code=antwort.status_code, headers=durchreichen
    )


@app.post("/mcp/{agent}")
@app.get("/mcp/{agent}")
@app.delete("/mcp/{agent}")
async def mcp_kanal(agent: str, request: Request) -> Response:
    return await _mcp_weiterleiten(request, agent)


@app.post("/mcp/{agent}/{rest:path}")
@app.get("/mcp/{agent}/{rest:path}")
async def mcp_kanal_unterpfad(agent: str, rest: str, request: Request) -> Response:
    return await _mcp_weiterleiten(request, agent, f"/{rest}" if rest else "")
