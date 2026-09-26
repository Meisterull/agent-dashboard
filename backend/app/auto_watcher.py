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
    Der Aufrufer wartet NICHT mit (M7): der Sanft-Stopp läuft im Hintergrund,
    die API antwortet sofort mit Status "stoppt".
  - Not-Aus = hart: erst "kill" auf stdin (der Watcher schießt den laufenden
    claude-Lauf samt Kindern ab — ohne PTY gibt es kein SIGHUP, ein bloßes
    Schließen der Verbindung ließe claude verwaist weiterarbeiten, H3), kurz
    auf das Prozessende warten, dann Verbindung schließen.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import shlex
import time
from collections import deque
from pathlib import Path
from typing import Any

from app import ereignisse, weckruf
from app.config import load_agents_full, load_settings, save_settings

RECONCILE_INTERVAL = 15   # Sekunden, bis settings-/agents-Änderungen greifen
RECONNECT_DELAY = 30      # Sekunden zwischen Startversuchen nach Fehler
# Sanft-Stopp: so lange darf ein laufender Task fertig werden, danach wird hart
# gestoppt. Muss ÜBER dem Wanduhr-Deckel des Watchers liegen (Issue #38:
# Default 7200 s, je Agent `automatik_timeout`) — sonst kappt „Aus" einen
# Lauf, den der Watcher selbst noch arbeiten ließe. Je Agent wird deshalb
# mindestens dessen Deckel + 60 s gewartet (siehe _Watcher.stop_grace).
STOP_GRACE = int(os.environ.get("AUTO_STOP_GRACE", "7260"))
WATCHER_TIMEOUT_DEFAULT = 7200  # = CLAUDE_TIMEOUT in scripts/agent_watcher.py
MAILBOX_ROOT = Path(os.environ.get("WORKSPACE_DIR", "/workspace")) / "mailboxes"
# Wartezeit auf das Prozessende nach "kill" (Not-Aus) — danach fällt die
# Verbindung sowieso. Kurz halten: der Endpunkt wartet mit.
KILL_GRACE = float(os.environ.get("AUTO_KILL_GRACE", "5"))
REMOTE_MCP_PORT_DEFAULT = int(os.environ.get("MCP_TUNNEL_REMOTE_PORT",
                                             os.environ.get("MCP_PORT", "9000")))
REMOTE_SCRIPT = ".agent-dashboard/agent_watcher.py"
REMOTE_SCRIPT_HASH = REMOTE_SCRIPT + ".sha256"


def _zeit() -> str:
    """Zeitstempel für die [automatik]-Zeilen (Issue #40) — ohne ihn ließ sich
    ein Ablauf nur über `docker logs -t` rekonstruieren."""
    from datetime import datetime
    return datetime.now().astimezone().isoformat(timespec="seconds")


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


def _script_hash(pfad: Path) -> str:
    return hashlib.sha256(pfad.read_bytes()).hexdigest()


def _quote_cmd(wert: str) -> str:
    """Kommando aus agents.yaml für die Remote-Shell absichern (N15).

    Mehrteilige Angaben ("py -3") bleiben erhalten — jeder Teil wird einzeln
    gequotet, damit weder Leerzeichen noch ein `;`/`&&` aus der Config in die
    Shell durchschlagen."""
    try:
        teile = shlex.split(str(wert), posix=False)
    except ValueError:
        teile = []
    return " ".join(shlex.quote(t) for t in teile) or shlex.quote(str(wert))


async def _script_hochladen(sftp, pfad: Path) -> bool:
    """agent_watcher.py nur bei Hash-Abweichung übertragen (M15).

    Bei jedem Reconnect ~30 kB über SFTP zu schieben ist unnötig; die Summe
    liegt als Nachbardatei auf dem Agenten-PC. Gibt True zurück, wenn wirklich
    hochgeladen wurde."""
    summe = _script_hash(pfad)
    vorhanden = None
    try:
        async with sftp.open(REMOTE_SCRIPT_HASH, "r") as f:
            vorhanden = (await f.read()).strip()
        if vorhanden == summe and await sftp.exists(REMOTE_SCRIPT):
            return False
    except Exception:  # noqa: BLE001 — keine/kaputte Summe: einfach hochladen
        pass
    await sftp.put(str(pfad), REMOTE_SCRIPT)
    try:
        async with sftp.open(REMOTE_SCRIPT_HASH, "w") as f:
            await f.write(summe)
    except Exception:  # noqa: BLE001 — ohne Summe wird nächstes Mal neu geladen
        pass
    return True


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
        # Berechtigungen des Headless-Laufs (Issue #19) — hier konfiguriert
        # statt unsichtbar in der Settings-Datei des Agenten-PCs
        "permission_mode": agent.get("permission_mode") or conn.get("permission_mode"),
        "allowed_tools": agent.get("allowed_tools") or conn.get("allowed_tools"),
        # Sitzung fortsetzen (Default AN): `resume: false` lässt jeden Task
        # wieder frisch starten; die Grenzen sind optional (Sekunden / Tokens).
        # Zeitgrenzen des Laufs (Issue #38): Wanduhr-Deckel und Leerlauf in s.
        "automatik_timeout": _erstes_gesetztes(agent.get("automatik_timeout"),
                                               conn.get("automatik_timeout")),
        "automatik_leerlauf": _erstes_gesetztes(agent.get("automatik_leerlauf"),
                                                conn.get("automatik_leerlauf")),
        "resume": _erstes_gesetztes(agent.get("resume"), conn.get("resume")),
        "resume_max_pause": _erstes_gesetztes(agent.get("resume_max_pause"),
                                              conn.get("resume_max_pause")),
        "resume_max_kontext": _erstes_gesetztes(agent.get("resume_max_kontext"),
                                                conn.get("resume_max_kontext")),
    }


def _erstes_gesetztes(*werte: Any) -> Any:
    """Erster Wert, der nicht None ist — `or` verschluckte ein `false`/0."""
    for w in werte:
        if w is not None:
            return w
    return None


def _ganzzahl(wert: Any) -> int | None:
    try:
        return int(float(wert))
    except (TypeError, ValueError, OverflowError):
        return None


def _zeit_flags(cfg: dict[str, Any]) -> str:
    """--timeout/--leerlauf aus agents.yaml (Issue #38). Wie bei den
    resume-Grenzen: nur geprüfte Zahlen erreichen die Remote-Shell.
    Leerlauf 0 ist gültig (= Wächter aus), ein Timeout unter 60 s nicht."""
    teile = ""
    deckel = _ganzzahl(cfg.get("automatik_timeout"))
    if deckel is not None and deckel >= 60:
        teile += f" --timeout {deckel}"
    ruhe = _ganzzahl(cfg.get("automatik_leerlauf"))
    if ruhe is not None and ruhe >= 0:
        teile += f" --leerlauf {ruhe}"
    return teile


def _resume_flags(cfg: dict[str, Any]) -> str:
    """Kommandozeilen-Anteil für das Fortsetzen der Sitzung.

    Zahlen werden HIER zu int gemacht: der Wert kommt aus agents.yaml und
    landet in einer Remote-Shell — ein Nicht-Zahl-Wert fällt weg statt durch."""
    teile = ""
    if cfg.get("resume") is False or str(cfg.get("resume")).strip().lower() in (
            "false", "nein", "aus", "no", "off", "0"):
        return " --no-resume"
    for feld, flag in (("resume_max_pause", "--resume-max-pause"),
                       ("resume_max_kontext", "--resume-max-kontext")):
        try:
            zahl = int(float(cfg.get(feld)))
        except (TypeError, ValueError, OverflowError):
            continue
        if zahl > 0:
            teile += f" {flag} {zahl}"
    return teile


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
        # Fehlerserie auf dem Agenten-PC (Watcher-Exit 1, Issue #14): kein
        # Auto-Neustart mehr, bis der Nutzer die Automatik neu einschaltet —
        # sonst frisst der Manager alle 30 s weiter die Warteschlange (M12).
        self.gesperrt = False
        self.task: asyncio.Task | None = None
        self.stop_grace = STOP_GRACE     # Sanft-Stopp-Frist dieses Agenten (s.o.)
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
            "gesperrt": self.gesperrt,
        }


class AutoWatcherManager:
    def __init__(self) -> None:
        self._watcher: dict[str, _Watcher] = {}
        self._weck = asyncio.Event()
        self._task: asyncio.Task | None = None
        # Hintergrund-Stopps (M7): Referenz halten, sonst kann der GC einen
        # noch laufenden Task einsammeln.
        self._hintergrund: set[asyncio.Task] = set()

    def _im_hintergrund(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._hintergrund.add(task)
        task.add_done_callback(self._hintergrund.discard)
        return task

    # --- öffentlich (API) ---------------------------------------------------

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._reconcile_loop())

    def stop(self) -> None:
        """Reconcile-Loop beenden (Review P1-9): ohne das machte er den
        Shutdown-Hart-Stopp ≤15 s später rückgängig und startete mitten im
        Herunterfahren frische Remote-Watcher (SSH-Connect inklusive)."""
        if self._task is not None:
            self._task.cancel()
            self._task = None

    def status(self) -> dict[str, Any]:
        settings = load_settings()
        gewuenscht = settings.get("automatik") or {}
        agents: dict[str, Any] = {}
        for agent in load_agents_full():
            name = agent.get("name")
            if not name or (agent.get("connection") or {}).get("type") != "ssh":
                continue
            w = self._watcher.get(name)
            arten = weckruf.weck_arten(agent)
            agents[name] = {
                "gewuenscht": bool(gewuenscht.get(name)),
                "startbar": _ssh_cfg(agent) is not None,
                # Weckruf (Issue #36): worauf die Automatik außer Tasks
                # reagiert — und wie viel Post deshalb gerade LIEGEN bleibt.
                "weckt": ["task", *arten],
                "ungeweckt": (weckruf.ungeweckt(MAILBOX_ROOT, name, arten)
                              if gewuenscht.get(name) else 0),
                **(w.als_dict() if w else {"status": "aus", "detail": "", "seit": None,
                                           "log": [], "gesperrt": False}),
            }
        return {"notaus": bool(settings.get("automatik_notaus")), "agents": agents}

    async def schalte(self, name: str, an: bool) -> None:
        gewuenscht = dict(load_settings().get("automatik") or {})
        gewuenscht[name] = an
        save_settings({"automatik": gewuenscht})
        w = self._watcher.get(name)
        if an:
            # Neu einschalten hebt die Fehlerserien-Sperre auf (M12) — der
            # Nutzer hat die Umgebung vermutlich repariert.
            if w is not None and w.gesperrt:
                self._watcher.pop(name, None)
        elif w is not None:
            # Sanft-Stopp NICHT abwarten (M7): bis zu STOP_GRACE (31 min)
            # Blockade würde nginx/Browser in den Timeout laufen lassen.
            if w.proc is not None and not w.beenden:
                w.setze("stoppt", "wartet auf laufenden Task")
            self._im_hintergrund(self._stopp_sanft(w))
        self._weck.set()

    async def notaus(self, an: bool) -> None:
        save_settings({"automatik_notaus": an})
        if an:
            await self.stopp_alle_hart()
        self._weck.set()

    async def stopp_alle_hart(self) -> None:
        await asyncio.gather(*(self._stopp_hart(w) for w in list(self._watcher.values())),
                             return_exceptions=True)

    # --- Reconcile ----------------------------------------------------------

    async def _reconcile_loop(self) -> None:
        print(f"{_zeit()} [automatik] Manager gestartet", flush=True)
        while True:
            try:
                self._reconcile()
            except Exception as exc:  # noqa: BLE001 — Loop darf nie sterben
                print(f"{_zeit()} [automatik] Reconcile-Fehler: {exc}", flush=True)
            try:
                # Datei-I/O unter Mailbox-Locks — nicht im Event-Loop.
                await asyncio.to_thread(self._weckrufe)
            except Exception as exc:  # noqa: BLE001
                print(f"{_zeit()} [automatik] Weckruf-Fehler: {exc}", flush=True)
            self._weck.clear()
            try:
                await asyncio.wait_for(self._weck.wait(), timeout=RECONCILE_INTERVAL)
            except asyncio.TimeoutError:
                pass

    def _weckrufe(self) -> None:
        """Ungelesene Post zu Weckruf-Tasks bündeln (Issue #36) — nur für
        Agenten mit eingeschalteter Automatik und `automatik_weckt`."""
        settings = load_settings()
        if settings.get("automatik_notaus"):
            return
        gewuenscht = settings.get("automatik") or {}
        for agent in load_agents_full():
            name = agent.get("name")
            if not name or not gewuenscht.get(name):
                continue
            arten = weckruf.weck_arten(agent)
            if not arten:
                continue
            bericht = weckruf.pruefe(MAILBOX_ROOT, name, arten)
            for task_id in bericht["geweckt"]:
                print(f"{_zeit()} [automatik] {name}: Weckruf {task_id} aus ungelesener Post", flush=True)
                ereignisse.schreibe(MAILBOX_ROOT, name, "weckruf",
                                    "Weckruf aus ungelesener Post", task_id=task_id)
            for absender in bericht["gebremst"]:
                print(f"{_zeit()} [automatik] {name}: Schleifenschutz — Post von {absender} "
                      f"bleibt liegen (>{weckruf.WECK_MAX_JE_STUNDE}/h)", flush=True)

    def _reconcile(self) -> None:
        settings = load_settings()
        notaus = bool(settings.get("automatik_notaus"))
        gewuenscht = settings.get("automatik") or {}
        agenten = {a.get("name"): a for a in load_agents_full() if a.get("name")}

        for name, an in gewuenscht.items():
            w = self._watcher.get(name)
            laeuft = w is not None and w.task is not None and not w.task.done()
            if an and not notaus:
                if w is not None and w.gesperrt:
                    continue  # Fehlerserie: erst wieder nach manuellem Schalten (M12)
                if not laeuft and name in agenten and _ssh_cfg(agenten[name]):
                    neu = _Watcher(name)
                    neu.task = asyncio.create_task(self._lauf(neu, agenten[name]["name"]))
                    self._watcher[name] = neu
            elif laeuft and not w.beenden:
                # gewünscht aus (oder Not-Aus): sanft bzw. hart stoppen
                self._im_hintergrund(
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
                        if await _script_hochladen(sftp, _watcher_script()):
                            print(f"{_zeit()} [automatik] {name}: agent_watcher.py aktualisiert",
                                  flush=True)
                    cmd = (
                        f"{_quote_cmd(cfg['python'])} -u {REMOTE_SCRIPT} "
                        f"--agent {shlex.quote(name)} "
                        f"--mcp-url http://127.0.0.1:{cfg['mcp_port']}/mcp "
                        f"--mcp-hint --interval 5"
                    )
                    if cfg.get("workdir"):
                        cmd += f" --workdir {shlex.quote(str(cfg['workdir']))}"
                    if cfg.get("claude_bin"):
                        cmd += f" --claude-bin {shlex.quote(str(cfg['claude_bin']))}"
                    if cfg.get("permission_mode"):
                        cmd += f" --permission-mode {shlex.quote(str(cfg['permission_mode']))}"
                    tools = cfg.get("allowed_tools")
                    if tools:
                        # YAML-Liste → EIN Komma-Argument (Issue #19). Hier ist
                        # es die Kommandozeile des WATCHERS (argparse, ein Wert)
                        # — dass claudes eigene Option variadisch ist und den
                        # Prompt verschluckt, löst baue_claude_cmd mit "--"
                        # im Watcher selbst (Issue #20).
                        if isinstance(tools, (list, tuple)):
                            tools = ",".join(str(t).strip() for t in tools if str(t).strip())
                        cmd += f" --allowed-tools {shlex.quote(str(tools))}"
                    cmd += _resume_flags(cfg) + _zeit_flags(cfg)
                    deckel = _ganzzahl(cfg.get("automatik_timeout"))
                    w.stop_grace = max(STOP_GRACE, (deckel if deckel and deckel >= 60
                                                    else WATCHER_TIMEOUT_DEFAULT) + 60)
                    proc = await conn.create_process(cmd, stderr=asyncssh.STDOUT)
                    w.proc = proc
                    w.setze("an", "")
                    print(f"{_zeit()} [automatik] {name}: Watcher läuft (MCP :{cfg['mcp_port']})", flush=True)
                    ereignisse.schreibe(MAILBOX_ROOT, name, "watcher_start",
                                        f"Watcher gestartet (MCP :{cfg['mcp_port']})")
                    async for zeile in proc.stdout:
                        zeile = zeile.rstrip()
                        if zeile:
                            w.log.append(zeile)
                            w.detail = zeile
                    abschluss = await proc.wait()
                    rc = getattr(abschluss, "exit_status", None)
                    if rc is None:
                        rc = getattr(proc, "exit_status", None)
                w.proc = None
                w.conn = None
                if w.beenden:
                    break
                if rc == 1:
                    # Der Watcher hat sich selbst gestoppt: Preflight oder
                    # Fehlerserie (Issue #14). Ein blinder Neustart alle 30 s
                    # würde die Warteschlange weiter verbrauchen (M12).
                    w.gesperrt = True
                    w.setze("fehler", (w.detail or "Watcher-Preflight fehlgeschlagen")
                            + " — kein Auto-Neustart, Automatik neu einschalten")
                    print(f"{_zeit()} [automatik] {name}: Watcher mit Fehler beendet (rc=1) — "
                          f"Auto-Neustart ausgesetzt ({w.detail})", flush=True)
                    ereignisse.schreibe(MAILBOX_ROOT, name, "fehlserie",
                                        f"Watcher gestoppt: {w.detail} — Automatik neu einschalten",
                                        schwere="fehler", detail=w.detail)
                    return
                # rc=2 (Instanz-Lock, H2) läuft bewusst in den normalen
                # Reconnect: der andere Watcher endet irgendwann von selbst.
                w.setze("fehler", w.detail or "Watcher-Prozess beendet")
                print(f"{_zeit()} [automatik] {name}: Prozess endete — Neustart in {RECONNECT_DELAY}s "
                      f"({w.detail})", flush=True)
                ereignisse.schreibe(MAILBOX_ROOT, name, "watcher_abriss",
                                    f"Watcher-Prozess endete (rc={rc}) — Neustart in {RECONNECT_DELAY}s",
                                    schwere="fehler", rc=rc, detail=w.detail or None)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — Reconnect-Loop, nie crashen
                w.proc = None
                w.conn = None
                if w.beenden:
                    break
                w.setze("fehler", f"{type(exc).__name__}: {exc}")
                print(f"{_zeit()} [automatik] {name}: {w.detail} — Neustart in {RECONNECT_DELAY}s",
                      flush=True)
                ereignisse.schreibe(MAILBOX_ROOT, name, "watcher_abriss",
                                    f"Verbindung zum Watcher verloren: {w.detail} — "
                                    f"Neustart in {RECONNECT_DELAY}s",
                                    schwere="fehler", detail=w.detail)
            try:
                await asyncio.wait_for(self._warte_auf_beenden(w), timeout=RECONNECT_DELAY)
            except asyncio.TimeoutError:
                pass
        w.setze("aus")
        print(f"{_zeit()} [automatik] {name}: Watcher gestoppt", flush=True)

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
            await asyncio.wait_for(w.proc.wait(), timeout=w.stop_grace)
        except (asyncio.TimeoutError, Exception):  # noqa: BLE001
            await self._stopp_hart(w)
            return
        w.setze("aus")

    async def _stopp_hart(self, w: _Watcher) -> None:
        """Not-Aus: erst "kill" auf stdin, dann Verbindung schließen (H3).

        Das Schließen allein reicht nicht: ohne PTY bekommt der laufende
        claude-Prozess kein SIGHUP und arbeitet verwaist weiter (ändert
        Dateien!), während der Task über den separaten MCP-Tunnel längst als
        Fehler quittiert wurde. Das stdin-Kommando lässt den Watcher die
        ganze Prozessgruppe abschießen; erst danach fällt die Verbindung."""
        w.beenden = True
        w.hart = True
        w.setze("stoppt", "Not-Aus: breche laufenden Lauf ab")
        proc = w.proc
        if proc is not None:
            try:
                proc.stdin.write("kill\n")
                await asyncio.wait_for(proc.wait(), timeout=KILL_GRACE)
            except Exception:  # noqa: BLE001 — stdin/Prozess weg: hart schließen
                pass
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
