"""WebSocket ↔ SSH-Bridge mit persistenten Sessions (xterm.js).

Frontend -> Server: JSON {type:"data",data} | {type:"resize",cols,rows} | {type:"kill"}.
Server -> Frontend: rohe Terminal-Ausgabe als Text.

Die SSH-Session lebt unabhängig vom WebSocket: bricht die Verbindung ab
(Handy gesperrt, Netzwechsel) oder wird das Fenster geschlossen, läuft die
Shell GRACE_SECONDS weiter und puffert ihren Output; der Client verbindet
sich mit derselben sid (?sid=… in der WS-URL) neu und bekommt den Puffer
nachgespielt. Da das Frontend eine stabile sid pro Verbindung nutzt, klappt
das auch von einem anderen PC/Browser aus. Beendet wird nur noch explizit
(kill / DELETE-Endpoint) oder durch Shell-Exit; list_sessions() speist
GET /api/ssh/sessions (Badge + Auto-Reopen im UI).

Close-Codes Richtung Client:
  4401  nicht angemeldet (main.py, vor der Bridge)
  4404  Session beendet (Shell-Exit oder kill) — Client soll NICHT reconnecten
  4000  von anderem Client übernommen — Client soll NICHT reconnecten
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
import uuid
from collections import deque

from app.config import agent_connection as _agent_connection  # auch von remote_files genutzt
from app.ssh_connect import connect_ssh

# Überlebenszeit ohne verbundenen Client; 0 = unbegrenzt (nur explizites
# Beenden oder Shell-Exit räumen auf). Env: SSH_GRACE_SECONDS.
GRACE_SECONDS = int(os.getenv("SSH_GRACE_SECONDS", "86400"))
BUFFER_LIMIT = 256 * 1024    # Replay-Puffer pro Session


class _Session:
    def __init__(self, key: str, agent: str, sid: str, conn, proc):
        self.key = key
        self.agent = agent
        self.sid = sid
        self.conn = conn
        self.proc = proc
        self.buffer: deque[str] = deque()
        self.buf_len = 0
        self.ws = None            # aktuell angehängter WebSocket (oder None)
        self.expire_task: asyncio.Task | None = None
        self.pump_task: asyncio.Task | None = None
        self.closed = False
        self.started = time.time()
        self.detached_at: float | None = None  # None = Client hängt dran


_sessions: dict[str, _Session] = {}


# ANSI-/Steuersequenzen für die Klartext-Ansicht des Replay-Puffers entfernen
# (GET /api/ssh/{name}/buffer, "Voller Verlauf" im Kopier-Modus). Bewusst kein
# Terminal-Emulator: CSI/OSC/DCS & Co. werden verworfen; \r-Überschreibungen
# löst strip_ansi zeilenweise auf (letzter Stand gewinnt — Spinner-/Progress-
# Redraws erscheinen einmal statt hundertfach).
_ANSI_RE = re.compile(
    r"\x1b\[[0-9;?]*[ -/]*[@-~]"            # CSI (Cursor, Farben, Modi)
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?"  # OSC (Fenstertitel u.ä.)
    r"|\x1b[PX^_][^\x1b]*(?:\x1b\\)?"       # DCS/SOS/PM/APC
    r"|\x1b[()*+][0-9A-Za-z]"               # Zeichensatz-Auswahl (ESC ( B …)
    r"|\x1b."                               # sonstige ESC-Sequenzen
    r"|[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]"    # Steuerzeichen außer \t \n \r
)


def strip_ansi(raw: str) -> str:
    text = _ANSI_RE.sub("", raw)
    lines = []
    for line in text.replace("\r\n", "\n").split("\n"):
        lines.append(line.rsplit("\r", 1)[-1] if "\r" in line else line)
    return "\n".join(lines)


def get_buffer(agent_name: str, sid: str) -> str | None:
    """Klartext des Replay-Puffers einer laufenden Session.

    Der Puffer hält den echten Stream (BUFFER_LIMIT) — auch das, was eine
    Alt-Screen-TUI (Claude Code) längst vom Bildschirm gewischt hat und was
    im xterm-Puffer deshalb nicht mehr existiert. None, wenn die Session
    nicht (mehr) läuft.
    """
    sess = _sessions.get(f"{agent_name}:{sid}")
    if not sess or sess.closed:
        return None
    return strip_ansi("".join(sess.buffer))


def _buffer_append(sess: _Session, data: str) -> None:
    sess.buffer.append(data)
    sess.buf_len += len(data)
    while sess.buf_len > BUFFER_LIMIT and sess.buffer:
        sess.buf_len -= len(sess.buffer.popleft())


async def _kill(sess: _Session, notify: bool = True) -> None:
    """Session endgültig beenden und aufräumen."""
    sess.closed = True
    _sessions.pop(sess.key, None)
    if sess.expire_task:
        sess.expire_task.cancel()
    ws, sess.ws = sess.ws, None
    if ws and notify:
        try:
            await ws.send_text("\r\n[Session beendet]\r\n")
            await ws.close(code=4404)
        except Exception:
            pass
    try:
        sess.proc.terminate()
    except Exception:
        pass
    try:
        sess.conn.close()
    except Exception:
        pass


async def _expire_later(sess: _Session) -> None:
    if GRACE_SECONDS <= 0:
        return  # unbegrenzt: Session lebt bis kill oder Shell-Exit
    try:
        await asyncio.sleep(GRACE_SECONDS)
    except asyncio.CancelledError:
        return
    if sess.ws is None and not sess.closed:
        await _kill(sess, notify=False)


async def _pump(sess: _Session) -> None:
    """Shell-Output dauerhaft lesen: puffern + an den aktuellen Client senden."""
    try:
        while True:
            data = await sess.proc.stdout.read(1024)
            if not data:
                break
            _buffer_append(sess, data)
            ws = sess.ws
            if ws is not None:
                try:
                    await ws.send_text(data)
                except Exception:
                    # Client tot, ohne dass die Bridge es schon gemerkt hat:
                    # hier detachen, sonst startet nie ein Expiry-Timer.
                    sess.ws = None
                    sess.detached_at = time.time()
                    if sess.expire_task:
                        sess.expire_task.cancel()
                    sess.expire_task = asyncio.create_task(_expire_later(sess))
    except Exception:
        pass
    if not sess.closed:  # Shell hat sich beendet (exit)
        await _kill(sess)


async def kill_session(agent_name: str, sid: str) -> bool:
    """Explizites Beenden aus dem UI (DELETE /api/ssh/{name}/session)."""
    sess = _sessions.get(f"{agent_name}:{sid}")
    if not sess:
        return False
    await _kill(sess)
    return True


def list_sessions() -> list[dict]:
    """Laufende Sessions für GET /api/ssh/sessions (Badge + Auto-Reopen)."""
    now = time.time()
    return [
        {
            "name": s.agent,
            "sid": s.sid,
            "attached": s.ws is not None,
            "age": int(now - s.started),
            "idle": int(now - s.detached_at) if s.detached_at else 0,
        }
        for s in list(_sessions.values())
        if not s.closed
    ]


async def bridge(websocket, agent_name: str) -> None:
    """Eine WebSocket-Sitzung an eine (ggf. schon laufende) SSH-Shell koppeln."""
    await websocket.accept()

    sid = websocket.query_params.get("sid") or uuid.uuid4().hex
    key = f"{agent_name}:{sid}"
    sess = _sessions.get(key)

    if sess and not sess.closed:
        # Reattach: Expiry stoppen, evtl. alten Client rauswerfen, Puffer nachspielen
        if sess.expire_task:
            sess.expire_task.cancel()
        old, sess.ws = sess.ws, None
        if old is not None:
            try:
                await old.close(code=4000)
            except Exception:
                pass
        try:
            i = 0
            while i < len(sess.buffer):
                await websocket.send_text(sess.buffer[i])
                i += 1
        except Exception:
            return
        sess.ws = websocket
        sess.detached_at = None
    else:
        conn_cfg = _agent_connection(agent_name)
        if not conn_cfg or not conn_cfg.get("host"):
            await websocket.send_text(f"\r\n[Keine SSH-Konfiguration für '{agent_name}']\r\n")
            await websocket.close(code=4404)
            return

        try:
            conn = await connect_ssh(conn_cfg)
            proc = await conn.create_process(term_type="xterm-256color", term_size=(80, 24))
        except Exception as exc:  # noqa: BLE001
            await websocket.send_text(f"\r\n[SSH-Verbindung fehlgeschlagen: {exc}]\r\n")
            await websocket.close(code=4404)
            return

        sess = _Session(key, agent_name, sid, conn, proc)
        _sessions[key] = sess
        sess.ws = websocket
        sess.pump_task = asyncio.create_task(_pump(sess))

    try:
        while True:
            raw = await websocket.receive_text()
            msg = json.loads(raw)
            mtype = msg.get("type")
            if mtype == "data":
                sess.proc.stdin.write(msg.get("data", ""))
            elif mtype == "resize":
                sess.proc.change_terminal_size(
                    int(msg.get("cols", 80)), int(msg.get("rows", 24))
                )
            elif mtype == "kill":
                await _kill(sess, notify=False)
                break
    except Exception:
        pass
    finally:
        # Nur detachen, nicht beenden — die Session wartet GRACE_SECONDS auf Reattach.
        if sess.ws is websocket:
            sess.ws = None
            if not sess.closed:
                sess.detached_at = time.time()
                sess.expire_task = asyncio.create_task(_expire_later(sess))
        try:
            await websocket.close()
        except Exception:
            pass
