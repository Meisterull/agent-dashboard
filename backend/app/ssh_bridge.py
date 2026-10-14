"""WebSocket ↔ SSH-Bridge mit persistenten Sessions (xterm.js).

Frontend -> Server: JSON {type:"data",data} | {type:"resize",cols,rows} | {type:"kill"}
                    | {type:"ping"}.
Server -> Frontend: rohe Terminal-Ausgabe als TEXT-Frames; BINÄR-Frames sind
der Kontrollkanal (heute nur b"pong" als Antwort auf ping). Das Frontend
schickt bei Funkstille ein Ping und verwirft die Verbindung, wenn das Pong
ausbleibt — bei schlechtem Netz meldet ein WebSocket oft minutenlang KEIN
close, während Tastendrücke schon ins Leere gehen (10.09.2026).

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

from app import projekte
from app.config import agent_connection as _agent_connection  # auch von remote_files genutzt
from app.ssh_connect import connect_ssh

# Projekt-Terminals (11.10.2026, app/projekte.py): eine sid "p:<projekt>" startet
# statt der nackten Shell eine tmux-Sitzung im Projektpfad (oder den Rückfall
# ohne tmux). Beim Reattach wird für sie KEIN Puffer nachgespielt — tmux
# zeichnet den Bildschirm beim Anhängen selbst; der Puffer bleibt trotzdem
# gefüllt, weil _pump ihn für alle Sessions führt. Und für alle Sessions gilt
# nach einem Reattach der „Resize-Wackler": Spalten −1 und zurück, damit eine
# laufende TUI (Claude Code, tmux) sich per SIGWINCH neu zeichnet — der nach-
# gespielte Rohstrom war für die alte Fenstergröße gezeichnet und stand nach
# einem Browser-Neustart in der neuen Breite zerrissen da.

# Überlebenszeit ohne verbundenen Client; 0 = unbegrenzt (nur explizites
# Beenden oder Shell-Exit räumen auf). Env: SSH_GRACE_SECONDS.
GRACE_SECONDS = int(os.getenv("SSH_GRACE_SECONDS", "86400"))
BUFFER_LIMIT = 256 * 1024    # Replay-Puffer pro Session
MIN_COLS, MIN_ROWS = 20, 3   # kleinere resize-Maße sind Messmüll, nicht Bildschirme
PONG = b"pong"               # Binär-Frame: Antwort auf {type:"ping"}


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
        self.projekt: dict | None = None       # Projekt-Terminal (sid "p:…")
        self.tmux = False                      # läuft auf dem Zielrechner in tmux
        self.neuzeichnen = False               # nach Reattach: Resize-Wackler fällig


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


async def hole_verlauf(agent_name: str, sid: str) -> str | None:
    """Wie get_buffer — für tmux-Projekt-Terminals aber der echte Verlauf
    aus tmux (capture-pane, Scrollback + Bildschirm) statt des Rohstroms."""
    sess = _sessions.get(f"{agent_name}:{sid}")
    if not sess or sess.closed:
        return None
    if sess.projekt and sess.tmux:
        try:
            ergebnis = await sess.conn.run(
                projekte.tmux_verlauf_kommando(sess.projekt), check=False, timeout=15
            )
            if ergebnis.exit_status == 0 and ergebnis.stdout is not None:
                return str(ergebnis.stdout).rstrip("\n")
        except Exception:
            pass
    return strip_ansi("".join(sess.buffer))


def _buffer_append(sess: _Session, data: str) -> None:
    sess.buffer.append(data)
    sess.buf_len += len(data)
    while sess.buf_len > BUFFER_LIMIT and sess.buffer:
        sess.buf_len -= len(sess.buffer.popleft())


async def _kill(sess: _Session, notify: bool = True, tmux_auch: bool = False) -> None:
    """Session endgültig beenden und aufräumen.

    tmux_auch: die tmux-Sitzung auf dem Zielrechner mit beenden — NUR beim
    ausdrücklichen Beenden (⏻ / kill). Endet der SSH-Prozess von selbst,
    hat der Mensch vielleicht gerade bewusst detacht (Strg-b d) oder tmux
    ist schon weg; die Sitzung dann zu killen wäre Datenverlust.
    """
    sess.closed = True
    _sessions.pop(sess.key, None)
    if sess.expire_task:
        sess.expire_task.cancel()
    if tmux_auch and sess.projekt and sess.tmux:
        try:
            await sess.conn.run(projekte.tmux_kill_kommando(sess.projekt), check=False, timeout=15)
        except Exception:
            pass
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
    await _kill(sess, tmux_auch=True)
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
            "projekt": s.projekt["name"] if s.projekt else None,
        }
        for s in list(_sessions.values())
        if not s.closed
    ]


async def _wackler(sess: _Session, cols: int, rows: int) -> None:
    """Resize-Wackler nach Reattach: erst eine Spalte schmaler, dann die
    echte Größe (vom Aufrufer gesetzt) — zwei SIGWINCH, auf die eine TUI
    (Claude Code, tmux) mit komplettem Neuzeichnen antwortet. Nötig, weil die
    neue Größe der alten gleichen kann und dann gar kein Signal käme."""
    try:
        sess.proc.change_terminal_size(max(MIN_COLS, cols - 1), rows)
        await asyncio.sleep(0.05)
    except Exception:
        pass


async def _projekt_start(conn, projekt: dict) -> tuple[str, bool]:
    """Startkommando für ein Projekt-Terminal bestimmen: POSIX mit tmux (oder
    dem tmux-losen Rückfall in derselben Zeile) bzw. Windows ohne tmux.
    Rückgabe (befehl, tmux_erwartet) — ob tmux wirklich da ist, entscheidet
    die Shell-Zeile selbst; `tmux_erwartet` steuert nur kill/Verlauf."""
    windows = False
    try:
        ergebnis = await conn.run("uname -s", check=False, timeout=10)
        windows = ergebnis.exit_status != 0
    except Exception:
        windows = False
    if windows:
        return projekte.windows_startkommando(projekt), False
    return projekte.posix_startkommando(projekt), True


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
        # Schnappschuss VOR dem Senden (N10): _pump hängt nebenher an und
        # verwirft links, sobald BUFFER_LIMIT greift — mit laufendem Index
        # würden dabei Stücke übersprungen oder doppelt gesendet.
        # Projekt-Terminal: kein Replay — tmux zeichnet beim Anhängen selbst,
        # und der Rohstrom passte nach einem Browser-Neustart ohnehin nicht
        # mehr zur neuen Breite. Der Wackler unten holt den Bildschirm.
        schnappschuss = [] if sess.projekt else list(sess.buffer)
        try:
            for teil in schnappschuss:
                await websocket.send_text(teil)
        except Exception:
            # Replay gescheitert (Client schon wieder weg): Verfallsuhr wieder
            # anwerfen, sonst bleibt die Session ohne Client unsterblich (N10).
            if not sess.closed:
                sess.detached_at = time.time()
                if sess.expire_task:
                    sess.expire_task.cancel()
                sess.expire_task = asyncio.create_task(_expire_later(sess))
            return
        sess.ws = websocket
        sess.detached_at = None
        sess.neuzeichnen = True
    else:
        conn_cfg = _agent_connection(agent_name)
        if not conn_cfg or not conn_cfg.get("host"):
            await websocket.send_text(f"\r\n[Keine SSH-Konfiguration für '{agent_name}']\r\n")
            await websocket.close(code=4404)
            return

        projekt = None
        if projekte.ist_projekt_sid(sid):
            projekt = projekte.finde(agent_name, projekte.projekt_name_aus_sid(sid))
            if projekt is None:
                await websocket.send_text(
                    f"\r\n[Kein Projekt '{projekte.projekt_name_aus_sid(sid)}' für '{agent_name}' — "
                    "unter + → Projekte anlegen]\r\n"
                )
                await websocket.close(code=4404)
                return

        tmux = False
        try:
            conn = await connect_ssh(conn_cfg)
            if projekt is None:
                proc = await conn.create_process(term_type="xterm-256color", term_size=(80, 24))
            else:
                befehl, tmux = await _projekt_start(conn, projekt)
                proc = await conn.create_process(
                    befehl, term_type="xterm-256color", term_size=(80, 24)
                )
        except Exception as exc:  # noqa: BLE001
            await websocket.send_text(f"\r\n[SSH-Verbindung fehlgeschlagen: {exc}]\r\n")
            await websocket.close(code=4404)
            return

        sess = _Session(key, agent_name, sid, conn, proc)
        sess.projekt, sess.tmux = projekt, tmux
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
                cols, rows = int(msg.get("cols", 80)), int(msg.get("rows", 24))
                # Maße unter MIN_COLS×MIN_ROWS sind nie ein echter Bildschirm,
                # sondern der Messfehler eines ausgeblendeten Panels: das
                # FitAddon liest bei display:none die Breite "100%" als 100 px
                # und meldete so 7 Spalten — die Shell brach danach jede Zeile
                # in Siebener-Häppchen um, dauerhaft sichtbar im Replay.
                if cols >= MIN_COLS and rows >= MIN_ROWS:
                    if sess.neuzeichnen:
                        sess.neuzeichnen = False
                        await _wackler(sess, cols, rows)
                    sess.proc.change_terminal_size(cols, rows)
            elif mtype == "ping":
                await websocket.send_bytes(PONG)
            elif mtype == "kill":
                await _kill(sess, notify=False, tmux_auch=True)
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
