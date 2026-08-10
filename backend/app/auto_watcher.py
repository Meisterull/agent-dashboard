"""Automatikmodus (Issue #12): hält pro Agent einen Remote-Watcher per SSH.

Muster wie app/mcp_tunnel.py — ein Hintergrund-Task je Agent plus ein
Reconcile-Loop, aber im API-Prozess (die Toggle-Endpunkte greifen direkt zu;
im Container gibt es keinen supervisorctl-Socket). Ablauf pro Agent:

  SSH-Verbindung (ssh_connect, Host-Key-Pinning) → scripts/agent_watcher.py
  per SFTP nach ~/.agent-dashboard/ schieben (immer aktuelle Version, kein
  Installationsschritt) → `python3 -u agent_watcher.py --agent <n>
  --mcp-url http://127.0.0.1:<mcp_port>/mcp --mcp-hint` starten. Der Watcher
  arbeitet über den gebundenen MCP-Kanal des Agenten (Issue #13) — kein
  SSHFS-Mount, Identität und Tool-Allowlist kommen aus dem Kanal.

Zustandsmodell:
  - GEWÜNSCHT lebt in settings.json (`automatik`: {name: true}, plus
    globaler Not-Aus `automatik_notaus`) und übersteht Neustarts; der
    Reconcile-Loop stellt ihn wieder her.
  - IST ist der echte Prozess: stirbt er oder die Verbindung, zeigt der
    Status "fehler"/Reconnect — nie weiter "an", wenn nichts läuft.
  - "Aus" = sanft: "stop" auf stdin des Watchers, laufender Claude-Lauf darf
    fertig werden (Deckel AUTO_STOP_GRACE, Default 1860 s), dann Verbindung zu.
  - Not-Aus = hart: Verbindung sofort schließen (portabel, auch Windows-
    OpenSSH ohne Signal-Support — Session weg beendet den Prozess).
"""
from __future__ import annotations

import asyncio
import os
import shlex
import time
from collections import deque
from pathlib import Path
from typing import Any

from app.config import load_agents_full, load_settings, save_settings

RECONCILE_INTERVAL = 15   # Sekunden, bis settings-/agents-Änderungen greifen
RECONNECT_DELAY = 30      # Sekunden zwischen Startversuchen nach Fehler
STOP_GRACE = int(os.environ.get("AUTO_STOP_GRACE", "1860"))  # > claude-Timeout 1800 s
REMOTE_MCP_PORT_DEFAULT = int(os.environ.get("MCP_TUNNEL_REMOTE_PORT",
                                             os.environ.get("MCP_PORT", "9000")))
REMOTE_SCRIPT = ".agent-dashboard/agent_watcher.py"


def _watcher_script() -> Path:
    """Lokaler Pfad von scripts/agent_watcher.py (Container: /app/scripts)."""
    kandidaten = [
        Path(os.environ.get("WATCHER_SCRIPT", "/app/scripts/agent_watcher.py")),
        Path(__file__).resolve().parents[2] / "scripts" / "agent_watcher.py",
    ]
    for p in kandidaten:
        if p.exists():
            return p
    raise FileNotFoundError("scripts/agent_watcher.py nicht gefunden")


def _ssh_cfg(agent: dict[str, Any]) -> dict[str, Any] | None:
    """Start-Config eines Automatik-fähigen Agenten; None wenn nicht startbar."""
    conn = agent.get("connection") or {}
    key_file = conn.get("key_file")
    if conn.get("type") != "ssh" or not conn.get("host") or not key_file:
        return None
    if not Path(key_file).exists():
        return None
    return {
        "host": conn["host"],
        "port": int(conn.get("port", 22)),
        "user": conn.get("user"),
        "key_file": key_file,
        "mcp_port": int(conn.get("mcp_port", REMOTE_MCP_PORT_DEFAULT)),
        # Optional in agents.yaml: Arbeitsverzeichnis / Python- / Claude-Kommando
        "workdir": agent.get("workdir") or conn.get("workdir"),
        "python": agent.get("python") or conn.get("python") or "python3",
        "claude_bin": agent.get("claude_bin") or conn.get("claude_bin"),
    }


class _Watcher:
    """Laufzeit-Zustand eines gehaltenen Remote-Watchers."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.status = "startet"          # startet | an | stoppt | fehler | aus
        self.detail = ""
        self.seit = time.time()
        # 20 Zeilen: seit stream-json (Issue #18) liefert der Watcher laufend
        # Fortschritt, nicht mehr nur zwei Zeilen pro Task.
        self.log: deque[str] = deque(maxlen=20)
        self.beenden = False             # sanfter Stopp angefordert
        self.hart = False                # harter Stopp (Not-Aus)
        self.task: asyncio.Task | None = None
        self.conn = None                 # asyncssh-Verbindung
        self.proc = None                 # asyncssh-Prozess

    def setze(self, status: str, detail: str = "") -> None:
        if status != self.status:
            self.seit = time.time()
        self.status = status
        if detail:
            self.detail = detail

    def als_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "detail": self.detail,
            "seit": self.seit,
            "log": list(self.log),
        }


class AutoWatcherManager:
    def __init__(self) -> None:
        self._watcher: dict[str, _Watcher] = {}
        self._weck = asyncio.Event()
        self._task: asyncio.Task | None = None

    # --- öffentlich (API) ---------------------------------------------------

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._reconcile_loop())

    def status(self) -> dict[str, Any]:
        settings = load_settings()
        gewuenscht = settings.get("automatik") or {}
        agents: dict[str, Any] = {}
        for agent in load_agents_full():
            name = agent.get("name")
            if not name or (agent.get("connection") or {}).get("type") != "ssh":
                continue
            w = self._watcher.get(name)
            agents[name] = {
                "gewuenscht": bool(gewuenscht.get(name)),
                "startbar": _ssh_cfg(agent) is not None,
                **(w.als_dict() if w else {"status": "aus", "detail": "", "seit": None, "log": []}),
            }
        return {"notaus": bool(settings.get("automatik_notaus")), "agents": agents}

    async def schalte(self, name: str, an: bool) -> None:
        gewuenscht = dict(load_settings().get("automatik") or {})
        gewuenscht[name] = an
        save_settings({"automatik": gewuenscht})
        if not an:
            w = self._watcher.get(name)
            if w:
                await self._stopp_sanft(w)
        self._weck.set()

    async def notaus(self, an: bool) -> None:
        save_settings({"automatik_notaus": an})
        if an:
            await self.stopp_alle_hart()
        self._weck.set()

    async def stopp_alle_hart(self) -> None:
        await asyncio.gather(*(self._stopp_hart(w) for w in self._watcher.values()),
                             return_exceptions=True)

    # --- Reconcile ----------------------------------------------------------

    async def _reconcile_loop(self) -> None:
        print("[automatik] Manager gestartet", flush=True)
        while True:
            try:
                self._reconcile()
            except Exception as exc:  # noqa: BLE001 — Loop darf nie sterben
                print(f"[automatik] Reconcile-Fehler: {exc}", flush=True)
            self._weck.clear()
            try:
                await asyncio.wait_for(self._weck.wait(), timeout=RECONCILE_INTERVAL)
            except asyncio.TimeoutError:
                pass

    def _reconcile(self) -> None:
        settings = load_settings()
        notaus = bool(settings.get("automatik_notaus"))
        gewuenscht = settings.get("automatik") or {}
        agenten = {a.get("name"): a for a in load_agents_full() if a.get("name")}

        for name, an in gewuenscht.items():
            w = self._watcher.get(name)
            laeuft = w is not None and w.task is not None and not w.task.done()
            if an and not notaus:
                if not laeuft and name in agenten and _ssh_cfg(agenten[name]):
                    neu = _Watcher(name)
                    neu.task = asyncio.create_task(self._lauf(neu, agenten[name]["name"]))
                    self._watcher[name] = neu
            elif laeuft and not w.beenden:
                # gewünscht aus (oder Not-Aus): sanft bzw. hart stoppen
                asyncio.create_task(
                    self._stopp_hart(w) if notaus else self._stopp_sanft(w)
                )

    # --- Lebenszyklus eines Watchers ---------------------------------------

    async def _lauf(self, w: _Watcher, name: str) -> None:
        """Watcher-Prozess halten; bei Abriss mit Abstand neu starten."""
        import asyncssh

        from app.ssh_connect import connect_ssh

        while not w.beenden:
            try:
                agent = next((a for a in load_agents_full() if a.get("name") == name), None)
                cfg = _ssh_cfg(agent) if agent else None
                if cfg is None:
                    w.setze("fehler", "Verbindung nicht (mehr) konfiguriert")
                    return
                w.setze("startet")
                conn = await connect_ssh(cfg, keepalive_interval=30)
                w.conn = conn
                async with conn:
                    async with conn.start_sftp_client() as sftp:
                        if not await sftp.isdir(".agent-dashboard"):
                            await sftp.mkdir(".agent-dashboard")
                        await sftp.put(str(_watcher_script()), REMOTE_SCRIPT)
                    cmd = (
                        f"{cfg['python']} -u {REMOTE_SCRIPT} --agent {shlex.quote(name)} "
                        f"--mcp-url http://127.0.0.1:{cfg['mcp_port']}/mcp "
                        f"--mcp-hint --interval 5"
                    )
                    if cfg.get("workdir"):
                        cmd += f" --workdir {shlex.quote(str(cfg['workdir']))}"
                    if cfg.get("claude_bin"):
                        cmd += f" --claude-bin {shlex.quote(str(cfg['claude_bin']))}"
                    proc = await conn.create_process(cmd, stderr=asyncssh.STDOUT)
                    w.proc = proc
                    w.setze("an", "")
                    print(f"[automatik] {name}: Watcher läuft (MCP :{cfg['mcp_port']})", flush=True)
                    async for zeile in proc.stdout:
                        zeile = zeile.rstrip()
                        if zeile:
                            w.log.append(zeile)
                            w.detail = zeile
                    await proc.wait()
                w.proc = None
                w.conn = None
                if w.beenden:
                    break
                w.setze("fehler", w.detail or "Watcher-Prozess beendet")
                print(f"[automatik] {name}: Prozess endete — Neustart in {RECONNECT_DELAY}s "
                      f"({w.detail})", flush=True)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — Reconnect-Loop, nie crashen
                w.proc = None
                w.conn = None
                if w.beenden:
                    break
                w.setze("fehler", f"{type(exc).__name__}: {exc}")
                print(f"[automatik] {name}: {w.detail} — Neustart in {RECONNECT_DELAY}s",
                      flush=True)
            try:
                await asyncio.wait_for(self._warte_auf_beenden(w), timeout=RECONNECT_DELAY)
            except asyncio.TimeoutError:
                pass
        w.setze("aus")
        print(f"[automatik] {name}: Watcher gestoppt", flush=True)

    @staticmethod
    async def _warte_auf_beenden(w: _Watcher) -> None:
        while not w.beenden:
            await asyncio.sleep(1)

    async def _stopp_sanft(self, w: _Watcher) -> None:
        """"stop" auf stdin — laufender Task darf fertig werden (Deckel STOP_GRACE)."""
        w.beenden = True
        if w.proc is None:
            w.setze("aus")
            return
        w.setze("stoppt", "wartet auf laufenden Task")
        try:
            w.proc.stdin.write("stop\n")
        except Exception:  # noqa: BLE001 — stdin evtl. schon zu → hart schließen
            await self._stopp_hart(w)
            return
        try:
            await asyncio.wait_for(w.proc.wait(), timeout=STOP_GRACE)
        except (asyncio.TimeoutError, Exception):  # noqa: BLE001
            await self._stopp_hart(w)
            return
        w.setze("aus")

    async def _stopp_hart(self, w: _Watcher) -> None:
        """Verbindung schließen — beendet die Remote-Session samt Prozess (portabel)."""
        w.beenden = True
        w.hart = True
        conn = w.conn
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
        if w.task is not None and not w.task.done():
            w.task.cancel()
        w.setze("aus")


manager = AutoWatcherManager()
