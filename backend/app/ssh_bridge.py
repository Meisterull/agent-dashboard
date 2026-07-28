"""WebSocket ↔ SSH-Bridge mit persistenten Sessions (xterm.js).

Frontend -> Server: JSON {type:"data",data} | {type:"resize",cols,rows} | {type:"kill"}.
Server -> Frontend: rohe Terminal-Ausgabe als Text.

Die SSH-Session lebt unabhängig vom WebSocket: bricht die Verbindung ab
(Handy gesperrt, Netzwechsel), läuft die Shell GRACE_SECONDS weiter und
puffert ihren Output; der Client verbindet sich mit derselben sid
(?sid=… in der WS-URL) neu und bekommt den Puffer nachgespielt.
Explizites Schließen im UI beendet die Session (kill / DELETE-Endpoint).

Close-Codes Richtung Client:
  4401  nicht angemeldet (main.py, vor der Bridge)
  4404  Session beendet (Shell-Exit oder kill) — Client soll NICHT reconnecten
  4000  von anderem Client übernommen — Client soll NICHT reconnecten
"""
from __future__ import annotations

import asyncio
import json
import uuid
from collections import deque

from app.config import agent_connection as _agent_connection  # auch von remote_files genutzt
from app.ssh_connect import connect_ssh

GRACE_SECONDS = 900          # Überlebenszeit ohne verbundenen Client
BUFFER_LIMIT = 256 * 1024    # Replay-Puffer pro Session


class _Session:
    def __init__(self, key: str, conn, proc):
        self.key = key
        self.conn = conn
        self.proc = proc
        self.buffer: deque[str] = deque()
        self.buf_len = 0
        self.ws = None            # aktuell angehängter WebSocket (oder None)
        self.expire_task: asyncio.Task | None = None
        self.pump_task: asyncio.Task | None = None
        self.closed = False


_sessions: dict[str, _Session] = {}


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
                    sess.ws = None
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

        sess = _Session(key, conn, proc)
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
                sess.expire_task = asyncio.create_task(_expire_later(sess))
        try:
            await websocket.close()
        except Exception:
            pass
