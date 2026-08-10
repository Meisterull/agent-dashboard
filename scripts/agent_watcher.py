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
                     result: str, status: str) -> None:
    """Ergebnis zusätzlich als kind="response" in die Inbox des Auftraggebers
    legen — der sieht es dann in seinem normalen inbox()-Zyklus, statt fremde
    Outboxen pollen zu müssen. Best-effort: schlägt die Zustellung fehl,
    bleibt die Outbox-Response die Quelle der Wahrheit."""
    if not sender or not AGENT_NAME_RE.fullmatch(sender) or sender == agent:
        return
    rid = f"response-{uuid.uuid4().hex[:8]}"
    try:
        atomic_write_json(root / sender / "inbox" / f"{rid}.json", {
            "id": rid, "kind": "response", "sender": agent, "to": sender,
            "text": result, "status": status, "reply_to": task_id,
            "created_at": now(),
        })
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


def run_claude(instruction: str, workdir: Path, dry_run: bool) -> tuple[str, str, int]:
    """Gibt (result, log, returncode) zurück."""
    if dry_run:
        return f"[dry-run] hätte ausgeführt: {instruction}", "", 0
    # Headless Claude-Code. --print => einmalige, nicht-interaktive Ausführung.
    proc = subprocess.run(
        ["claude", "--print", instruction],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=1800,
    )
    return proc.stdout.strip(), proc.stderr.strip(), proc.returncode


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


def mcp_loop(url: str, agent: str, workdir: Path, interval: float,
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
                try:
                    result, err, rc = run_claude(instruction, workdir, dry_run)
                    status = "done" if rc == 0 else "error"
                except Exception as exc:  # noqa: BLE001 — alles zurückmelden
                    result, err, status = "", repr(exc), "error"
                client.call("complete_task", {
                    "task_id": task_id, "result": result,
                    "status": status, "log": err,
                })
                print(f"[{now()}] {agent}: {task_id} abgeschlossen ({status})", flush=True)
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
                 agent: str, workdir: Path, dry_run: bool,
                 with_mcp_hint: bool = False) -> int:
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
        try:
            instruction = task["instruction"]
            if with_mcp_hint:
                instruction = mcp_hint(agent) + instruction
            result, err, rc = run_claude(instruction, workdir, dry_run)
            status = "done" if rc == 0 else "error"
            atomic_write_json(
                outbox / f"{task_id}-response.json",
                {"task_id": task_id, "agent": agent, "to": sender or None,
                 "result": result, "status": status, "log": err,
                 "responded_at": now()},
            )
            deliver_response(root, sender, agent, task_id, result, status)
        except Exception as exc:  # noqa: BLE001 — alles zurückmelden, nie crashen
            atomic_write_json(
                outbox / f"{task_id}-response.json",
                {"task_id": task_id, "agent": agent, "to": sender or None,
                 "result": "", "status": "error", "log": repr(exc),
                 "responded_at": now()},
            )
            deliver_response(root, sender, agent, task_id, "", "error")
        finally:
            claimed.unlink(missing_ok=True)
        handled += 1
    return handled


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", required=True)
    ap.add_argument("--root", help="Mailbox-Wurzel (enthält <agent>/) — Datei-Transport")
    ap.add_argument("--mcp-url", help="MCP-Endpunkt (http://127.0.0.1:<mcp_port>/mcp) — "
                                      "Transport über den Reverse-Tunnel, kein Mount nötig")
    ap.add_argument("--workdir", default=".", help="Arbeitsverzeichnis für Claude-Code")
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

    if args.mcp_url:
        print(f"[{now()}] Watcher gestartet für '{args.agent}' "
              f"(dry_run={args.dry_run}) — MCP {args.mcp_url}", flush=True)
        try:
            return mcp_loop(args.mcp_url, args.agent, workdir, args.interval,
                            args.dry_run, args.mcp_hint, args.once)
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
            process_once(inbox, processing, outbox, args.agent, workdir,
                         args.dry_run, args.mcp_hint)
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
