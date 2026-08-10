#!/usr/bin/env python3
"""Remote-Watcher — führt Tasks aus der Inbox des Agenten mit Claude-Code aus.

Läuft auf dem ENTFERNTEN Agenten-PC neben Claude-Code. Bewusst
abhängigkeitsfrei (nur Standardlib), damit er auf jedem Ziel-PC ohne
pip-Install läuft. Zwei Transporte:

  --root <pfad>   Datei-Mailbox (Variante B, braucht SSHFS/SFTP-Mount)
  --mcp-url <url> MCP über den Reverse-Tunnel (Issue #12) — KEIN Mount nötig:
                  inbox/claim_task/complete_task laufen über den gebundenen
                  Kanal des Agenten (http://127.0.0.1:<mcp_port>/mcp), die
                  Identität kommt aus dem Kanal (Issue #13).

Sanftes Beenden (Automatikmodus): "stop" auf stdin (oder stdin-EOF, wenn die
haltende SSH-Verbindung stirbt) → kein neuer Task wird mehr angenommen, ein
laufender Claude-Lauf darf fertig werden und sein Ergebnis abliefern.

Test ohne echtes Claude-Code:  --dry-run  (echoed die instruction zurück).

    python3 agent_watcher.py --agent frontend \
        --root /mnt/agent-dashboard/mailboxes --dry-run
    python3 agent_watcher.py --agent frontend \
        --mcp-url http://127.0.0.1:9000/mcp --mcp-hint
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Sanft-Stopp-Signal: gesetzt durch "stop" auf stdin oder stdin-EOF.
STOP = threading.Event()

# Fehlschlag-Dämpfung (Issue #14): Scheitern mehrere Tasks unmittelbar
# hintereinander, ist die Umgebung kaputt (Binary weg, workdir weg, …) —
# dann anhalten statt die Warteschlange im Sekundentakt zu verbrauchen.
FEHLER_SCHWELLE = 3       # so viele schnelle Fehlschläge in Folge → Stopp
SCHNELL_SEKUNDEN = 20.0   # "schnell" = Lauf endete früher als das
_schnelle_fehler = 0

# Muss zur Allowlist in app/mailbox.py passen — sender wird in Pfade gejoint.
AGENT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def deliver_response(root: Path, sender: str, agent: str, task_id: str,
                     result: str, status: str, instruction: str | None = None) -> None:
    """Ergebnis zusätzlich als kind="response" in die Inbox des Auftraggebers
    legen — der sieht es dann in seinem normalen inbox()-Zyklus, statt fremde
    Outboxen pollen zu müssen. Best-effort: schlägt die Zustellung fehl,
    bleibt die Outbox-Response die Quelle der Wahrheit."""
    if not sender or not AGENT_NAME_RE.fullmatch(sender) or sender == agent:
        return
    rid = f"response-{uuid.uuid4().hex[:8]}"
    env = {
        "id": rid, "kind": "response", "sender": agent, "to": sender,
        "text": result, "status": status, "reply_to": task_id,
        "created_at": now(),
    }
    if instruction:  # Fehlschlag: Aufgabenbeschreibung mitgeben (Issue #15)
        env["instruction"] = instruction
    try:
        atomic_write_json(root / sender / "inbox" / f"{rid}.json", env)
    except OSError:
        pass


def mcp_hint(agent: str) -> str:
    """Identitäts-/Tool-Kontext für Claude-Code, wenn der Dashboard-MCP-Server
    auf diesem PC registriert ist (setup_agent_pc.sh + Reverse-Tunnel)."""
    return (
        f"[Kontext] Du bist der Agent '{agent}' im Agent-Dashboard. Über den "
        f"MCP-Server 'dashboard' kannst du mit Orchestrator und anderen Agenten "
        f"reden: inbox('{agent}') zeigt Nachrichten und Rückfragen an dich; mit "
        f"ask/answer/send_message (immer sender='{agent}') antwortest du; "
        f"verarbeitete Nachrichten archivierst du mit mark_read('{agent}', id), "
        f"sonst siehst du sie beim nächsten Mal erneut. Delegierst du selbst "
        f"per send_task(sender='{agent}'), kommt das Ergebnis als "
        f"kind='response' in DEINE Inbox zurück. Prüfe "
        f"zu Beginn deine Inbox und stelle Rückfragen per ask statt zu raten.\n\n"
    )


def finde_claude(hint: str) -> str | None:
    """Claude-Binary auflösen (Issue #14).

    Der Watcher läuft in einer nicht-interaktiven SSH-Shell — deren PATH
    enthält ~/.local/bin (Standard-Installationsort von Claude Code) meist
    NICHT. Deshalb nach `which` noch die üblichen Installationsorte absuchen."""
    if os.sep in hint or (os.altsep and os.altsep in hint):
        pfad = Path(hint).expanduser()
        return str(pfad) if pfad.is_file() and os.access(pfad, os.X_OK) else None
    gefunden = shutil.which(hint)
    if gefunden:
        return gefunden
    home = Path.home()
    zusatz = (home / ".local" / "bin", home / ".npm-global" / "bin",
              home / "bin", Path("/usr/local/bin"), Path("/opt/homebrew/bin"))
    return shutil.which(hint, path=os.pathsep.join(str(d) for d in zusatz))


def preflight(claude_hint: str, workdir: Path, dry_run: bool) -> str | None:
    """Arbeitsfähigkeit prüfen, BEVOR der erste Task beansprucht wird.

    Gibt einen Klartext-Fehler zurück (landet als letzte Log-Zeile im
    Automatik-Panel) oder None. Ohne diese Prüfung würde eine kaputte
    Umgebung jeden eingehenden Task verbrauchen und mit leerem error
    quittieren (Issue #14)."""
    if not workdir.is_dir():
        return f"Arbeitsverzeichnis fehlt auf dem Agenten-PC: {workdir}"
    if dry_run:
        return None
    if finde_claude(claude_hint) is None:
        return (f"Claude-Binary '{claude_hint}' nicht gefunden — weder im PATH "
                f"({os.environ.get('PATH', '')}) noch in ~/.local/bin & Co. "
                f"In agents.yaml 'claude_bin' setzen oder Claude-Code installieren.")
    return None


def fehlerserie(status: str, dauer: float) -> bool:
    """Fehlschlag-Zähler füttern; True = anhalten (Serie sofortiger Fehler)."""
    global _schnelle_fehler
    if status == "error" and dauer < SCHNELL_SEKUNDEN:
        _schnelle_fehler += 1
    else:
        _schnelle_fehler = 0
    return _schnelle_fehler >= FEHLER_SCHWELLE


def run_claude(claude_bin: str, instruction: str, workdir: Path,
               dry_run: bool) -> tuple[str, str, int]:
    """Gibt (result, log, returncode) zurück."""
    if dry_run:
        return f"[dry-run] hätte ausgeführt: {instruction}", "", 0
    # Headless Claude-Code. --print => einmalige, nicht-interaktive Ausführung.
    # stdin=DEVNULL (Issue #16): der stdin des Watchers gehört dem Sanft-Stopp
    # ("stop"-Zeile) — erbt ihn das Kind, kann claude das Stopp-Kommando
    # verschlucken und wartet obendrein 3 s auf Piped-Input.
    try:
        proc = subprocess.run(
            [claude_bin, "--print", instruction],
            cwd=str(workdir),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=1800,
        )
    except FileNotFoundError:
        # errno 2 allein nennt die Datei nicht — hier Klartext liefern (#14).
        return "", f"Claude-Binary nicht ausführbar: {claude_bin}", 127
    return proc.stdout.strip(), proc.stderr.strip(), proc.returncode


def fehler_result(result: str, err: str) -> str:
    """Leeres result bei status=error ist für den Auftraggeber wertlos —
    dort gehört die Fehlerursache hinein (Issue #14)."""
    if result:
        return result
    return f"[watcher] Ausführung fehlgeschlagen: {err[:2000] or 'keine Ausgabe'}"


def merge_instruction(task: dict) -> str:
    """Pendant zu app/mailbox.merged_instruction (Dateitransport, Issue #17):
    nach einem geparkten Lauf gehören Rückfrage-Antworten (`nachtraege`) und
    `zwischenstand` mit in den Prompt — der neue Lauf hat kein Gedächtnis."""
    teile = [task.get("instruction", "")]
    if task.get("zwischenstand"):
        teile.append("[Zwischenstand deines vorherigen Laufs — er endete mit "
                     "einer Rückfrage:]\n" + str(task["zwischenstand"]))
    for n in task.get("nachtraege") or []:
        teile.append(f'[Antwort auf deine Rückfrage "{n.get("frage", "")}": '
                     f'{n.get("antwort", "")}]')
    return "\n\n".join(t for t in teile if t)


def unbeantwortete_fragen(inbox: Path, claimed: Path) -> list[dict]:
    """Offene Rückfragen eines Tasks ermitteln (Dateitransport, Issue #17).

    `open_questions` heftet der MCP-Server beim ask() des Agenten an den Task
    in .processing/; Antworten liegen als kind=answer in Inbox/Archiv."""
    try:
        env = json.loads(claimed.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return []
    fragen = [f for f in env.get("open_questions") or [] if f.get("id")]
    if not fragen:
        return []
    beantwortet = set()
    for ordner in (inbox, inbox / ".archive"):
        if not ordner.is_dir():
            continue
        for p in ordner.glob("*.json"):
            try:
                e = json.loads(p.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            if e.get("kind") == "answer" and e.get("reply_to"):
                beantwortet.add(e["reply_to"])
    return [f for f in fragen if f["id"] not in beantwortet]


def stdin_stop_waechter() -> None:
    """Daemon-Thread: "stop" auf stdin = sanft beenden; EOF (haltende
    SSH-Verbindung weg) ebenso — so bleibt nie ein verwaister Watcher zurück."""
    def _lauscher() -> None:
        try:
            for zeile in sys.stdin:
                if zeile.strip().lower() == "stop":
                    print(f"[{now()}] Stop-Kommando empfangen — beende nach laufendem Task.", flush=True)
                    break
            else:
                print(f"[{now()}] stdin geschlossen — beende nach laufendem Task.", flush=True)
        except Exception:  # noqa: BLE001 — stdin-Eigenheiten dürfen nie crashen
            pass
        STOP.set()

    threading.Thread(target=_lauscher, daemon=True).start()


# --- MCP-Transport (Streamable-HTTP, nur Standardlib) -----------------------

class McpFehler(RuntimeError):
    pass


class McpClient:
    """Minimaler MCP-Client für den gebundenen Kanal des Agenten.

    Spricht Streamable-HTTP (JSON-RPC per POST, Antwort JSON oder SSE) mit
    urllib — genau die drei Tools, die der Watcher braucht. Auf dem gebundenen
    Kanal (Issue #13) braucht kein Aufruf einen agent-Parameter."""

    def __init__(self, url: str, timeout: float = 30.0) -> None:
        self.url = url
        self.timeout = timeout
        self.session_id: str | None = None
        self.protocol: str = "2025-03-26"
        self._id = 0

    def _post(self, body: dict, erwarte_antwort: bool = True) -> dict | None:
        daten = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(self.url, data=daten, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json, text/event-stream")
        req.add_header("MCP-Protocol-Version", self.protocol)
        if self.session_id:
            req.add_header("Mcp-Session-Id", self.session_id)
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            sid = resp.headers.get("Mcp-Session-Id")
            if sid:
                self.session_id = sid
            if not erwarte_antwort:
                resp.read()
                return None
            ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip()
            roh = resp.read().decode("utf-8", errors="replace")
        if ctype == "text/event-stream":
            # SSE: jede "data:"-Zeile ist eine JSON-RPC-Message; die Antwort
            # auf unsere id ist die letzte relevante.
            antwort = None
            for zeile in roh.splitlines():
                if zeile.startswith("data:"):
                    try:
                        msg = json.loads(zeile[5:].strip())
                    except json.JSONDecodeError:
                        continue
                    if msg.get("id") == body.get("id"):
                        antwort = msg
            if antwort is None:
                raise McpFehler("keine Antwort im SSE-Stream")
            return antwort
        return json.loads(roh)

    def _rpc(self, method: str, params: dict) -> dict:
        self._id += 1
        msg = {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params}
        antwort = self._post(msg)
        if "error" in antwort:
            raise McpFehler(f"{method}: {antwort['error']}")
        return antwort.get("result") or {}

    def connect(self) -> None:
        result = self._rpc("initialize", {
            "protocolVersion": self.protocol,
            "capabilities": {},
            "clientInfo": {"name": "agent-watcher", "version": "1.0"},
        })
        self.protocol = result.get("protocolVersion", self.protocol)
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"},
                   erwarte_antwort=False)

    def call(self, tool: str, argumente: dict | None = None):
        """Tool aufrufen; gibt die geparsten Daten zurück (dict oder Liste)."""
        result = self._rpc("tools/call", {"name": tool, "arguments": argumente or {}})
        if result.get("isError"):
            texte = [c.get("text", "") for c in result.get("content", [])]
            raise McpFehler(f"{tool}: {' '.join(texte) or 'Tool-Fehler'}")
        structured = result.get("structuredContent")
        if isinstance(structured, dict):
            # FastMCP verpackt Nicht-Objekt-Ergebnisse als {"result": ...}
            return structured.get("result", structured) if set(structured) == {"result"} else structured
        daten = []
        for c in result.get("content", []):
            text = c.get("text")
            if text is None:
                continue
            try:
                daten.append(json.loads(text))
            except json.JSONDecodeError:
                daten.append(text)
        if len(daten) == 1:
            return daten[0]
        return daten


def mcp_loop(url: str, agent: str, claude_bin: str, workdir: Path, interval: float,
             dry_run: bool, with_mcp_hint: bool, once: bool = False) -> int:
    """Poll-Schleife über MCP: inbox → claim_task → claude → complete_task.

    Verbindungsfehler (Tunnel weg, Server-Neustart) werden mit Abstand erneut
    versucht — der Watcher stirbt nicht, solange ihn niemand stoppt."""
    client: McpClient | None = None
    letzter_fehler: str | None = None
    while not STOP.is_set():
        try:
            if client is None:
                client = McpClient(url)
                client.connect()
                print(f"[{now()}] MCP verbunden: {url}", flush=True)
                letzter_fehler = None
            envelopes = client.call("inbox", {"kind": "task"})
            if isinstance(envelopes, dict):
                envelopes = [envelopes]
            for env in envelopes or []:
                if STOP.is_set():
                    break
                if not isinstance(env, dict) or env.get("kind", "task") != "task":
                    continue
                if env.get("error"):
                    raise McpFehler(str(env["error"]))
                task_id = env.get("id") or env.get("task_id")
                if not task_id:
                    continue
                claimed = client.call("claim_task", {"task_id": task_id})
                if not isinstance(claimed, dict) or claimed.get("error"):
                    continue  # schon von jemand anderem beansprucht
                instruction = claimed.get("instruction") or env.get("text") or ""
                print(f"[{now()}] {agent}: bearbeite {task_id}", flush=True)
                if with_mcp_hint:
                    instruction = mcp_hint(agent) + instruction
                start = time.monotonic()
                try:
                    result, err, rc = run_claude(claude_bin, instruction, workdir, dry_run)
                    status = "done" if rc == 0 else "error"
                except Exception as exc:  # noqa: BLE001 — alles zurückmelden
                    result, err, status = "", repr(exc), "error"
                dauer = time.monotonic() - start
                if status == "error":
                    result = fehler_result(result, err)
                fertig = client.call("complete_task", {
                    "task_id": task_id, "result": result,
                    "status": status, "log": err,
                })
                if isinstance(fertig, dict) and fertig.get("parked"):
                    # Rückfrage offen (Issue #17): Server hat den Task geparkt,
                    # nach der Antwort landet er automatisch wieder in der Inbox.
                    print(f"[{now()}] {agent}: {task_id} wartet auf Antwort "
                          f"einer Rückfrage (geparkt)", flush=True)
                else:
                    print(f"[{now()}] {agent}: {task_id} abgeschlossen ({status})", flush=True)
                if fehlerserie(status, dauer):
                    print(f"[{now()}] {agent}: {FEHLER_SCHWELLE} Tasks in Folge sofort "
                          f"gescheitert — Umgebungsproblem vermutet, Watcher hält an. "
                          f"Letzter Fehler: {err[:300]}", flush=True)
                    return 1
            if once:
                break
        except (McpFehler, urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            client = None  # Session neu aufbauen
            msg = f"{type(exc).__name__}: {exc}"
            if msg != letzter_fehler:  # nur Zustandswechsel loggen
                print(f"[{now()}] MCP-Fehler: {msg} — neuer Versuch alle {max(interval, 10):.0f}s",
                      flush=True)
                letzter_fehler = msg
            if once:
                return 1
            STOP.wait(max(interval, 10))
            continue
        STOP.wait(interval)
    print(f"[{now()}] Watcher beendet.", flush=True)
    return 0


def process_once(inbox: Path, processing: Path, outbox: Path,
                 agent: str, claude_bin: str, workdir: Path, dry_run: bool,
                 with_mcp_hint: bool = False) -> int:
    """Gibt die Zahl bearbeiteter Tasks zurück; -1 = Fehlerserie, bitte anhalten."""
    handled = 0
    for task_path in sorted(inbox.glob("*.json")):
        # Nur Arbeitsaufträge ausführen; Agent-↔-Agent-Nachrichten (message/
        # question/answer) liegen lassen — die liest der Koordinator/das Dashboard.
        try:
            peek = json.loads(task_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, FileNotFoundError):
            continue
        if peek.get("kind", "task") != "task":
            continue
        claimed = processing / task_path.name
        try:
            os.replace(task_path, claimed)  # atomarer, exklusiver Anspruch
        except FileNotFoundError:
            continue
        try:
            task = json.loads(claimed.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue  # nächster Tick

        task_id = task.get("task_id", claimed.stem)
        sender = task.get("sender") or ""
        root = inbox.parent.parent  # root/<agent>/inbox → Mailbox-Wurzel
        print(f"[{now()}] {agent}: bearbeite {task_id}", flush=True)
        start = time.monotonic()
        status, err = "error", ""
        try:
            task["instruction"]  # fehlende instruction soll wie bisher scheitern
            instruction = merge_instruction(task)
            if with_mcp_hint:
                instruction = mcp_hint(agent) + instruction
            result, err, rc = run_claude(claude_bin, instruction, workdir, dry_run)
            status = "done" if rc == 0 else "error"
            if status == "error":
                result = fehler_result(result, err)
        except Exception as exc:  # noqa: BLE001 — alles zurückmelden, nie crashen
            status, err = "error", repr(exc)
            result = fehler_result("", err)
        if status == "done":
            offen = unbeantwortete_fragen(inbox, claimed)
            if offen:
                # Rückfrage offen (Issue #17): parken statt Erfolg melden. Der
                # Server legt den Task nach der Antwort zurück in die Inbox.
                try:
                    env = json.loads(claimed.read_text(encoding="utf-8"))
                    env.update(status="needs_confirm", open_questions=offen)
                    if result:
                        env["zwischenstand"] = result
                    atomic_write_json(claimed, env)
                    print(f"[{now()}] {agent}: {task_id} wartet auf Antwort "
                          f"einer Rückfrage (geparkt)", flush=True)
                    handled += 1
                    continue
                except (json.JSONDecodeError, OSError):
                    pass  # Envelope nicht lesbar — dann regulär abschließen
        antwort = {"task_id": task_id, "agent": agent, "to": sender or None,
                   "result": result, "status": status, "log": err,
                   "responded_at": now()}
        if status == "error" and task.get("instruction"):
            # Fehlschlag: die einzige Kopie der Aufgabenbeschreibung darf
            # nicht verloren gehen (Issue #15).
            antwort["instruction"] = task["instruction"]
        try:
            atomic_write_json(outbox / f"{task_id}-response.json", antwort)
            deliver_response(root, sender, agent, task_id, result, status,
                             antwort.get("instruction"))
        finally:
            if status == "error":
                failed = inbox / ".failed"
                failed.mkdir(exist_ok=True)
                try:  # Task für einen Wiederanlauf aufheben (Issue #15)
                    os.replace(claimed, failed / claimed.name)
                except FileNotFoundError:
                    pass
            else:
                claimed.unlink(missing_ok=True)
        handled += 1
        print(f"[{now()}] {agent}: {task_id} abgeschlossen ({status})", flush=True)
        if fehlerserie(status, time.monotonic() - start):
            print(f"[{now()}] {agent}: {FEHLER_SCHWELLE} Tasks in Folge sofort "
                  f"gescheitert — Umgebungsproblem vermutet, Watcher hält an. "
                  f"Letzter Fehler: {err[:300]}", flush=True)
            return -1
    return handled


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", required=True)
    ap.add_argument("--root", help="Mailbox-Wurzel (enthält <agent>/) — Datei-Transport")
    ap.add_argument("--mcp-url", help="MCP-Endpunkt (http://127.0.0.1:<mcp_port>/mcp) — "
                                      "Transport über den Reverse-Tunnel, kein Mount nötig")
    ap.add_argument("--workdir", default=".", help="Arbeitsverzeichnis für Claude-Code")
    ap.add_argument("--claude-bin", default="claude",
                    help="Claude-Binary (Name oder Pfad); Default: 'claude' im PATH "
                         "bzw. den üblichen Installationsorten (~/.local/bin …)")
    ap.add_argument("--interval", type=float, default=2.0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--once", action="store_true", help="einmal durchlaufen und beenden")
    ap.add_argument("--mcp-hint", action="store_true",
                    help="Identitäts-/Tool-Kontext voranstellen (wenn der "
                         "Dashboard-MCP-Server auf diesem PC registriert ist)")
    args = ap.parse_args()
    if bool(args.root) == bool(args.mcp_url):
        ap.error("genau eines von --root und --mcp-url angeben")
    workdir = Path(args.workdir).resolve()
    stdin_stop_waechter()

    # Preflight VOR dem ersten Claim: kaputte Umgebung → gar nicht erst
    # anfangen, kein einziger Task geht verloren (Issue #14).
    problem = preflight(args.claude_bin, workdir, args.dry_run)
    if problem:
        print(f"[{now()}] PREFLIGHT FEHLGESCHLAGEN: {problem}", flush=True)
        return 1
    claude_bin = args.claude_bin if args.dry_run else finde_claude(args.claude_bin)

    if args.mcp_url:
        print(f"[{now()}] Watcher gestartet für '{args.agent}' "
              f"(dry_run={args.dry_run}, claude={claude_bin}) — MCP {args.mcp_url}",
              flush=True)
        try:
            return mcp_loop(args.mcp_url, args.agent, claude_bin, workdir,
                            args.interval, args.dry_run, args.mcp_hint, args.once)
        except KeyboardInterrupt:
            print("\nWatcher beendet.", flush=True)
            return 0

    base = Path(args.root) / args.agent
    inbox, processing, outbox = base / "inbox", base / "inbox" / ".processing", base / "outbox"
    for d in (inbox, processing, outbox):
        d.mkdir(parents=True, exist_ok=True)

    print(f"[{now()}] Watcher gestartet für '{args.agent}' "
          f"(dry_run={args.dry_run}) — beobachte {inbox}", flush=True)
    try:
        while not STOP.is_set():
            if process_once(inbox, processing, outbox, args.agent, claude_bin,
                            workdir, args.dry_run, args.mcp_hint) < 0:
                return 1
            if args.once:
                break
            STOP.wait(args.interval)
        else:
            print(f"[{now()}] Watcher beendet.", flush=True)
    except KeyboardInterrupt:
        print("\nWatcher beendet.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
