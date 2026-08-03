#!/usr/bin/env python3
"""Remote-Watcher (Variante B: Pull über Mailbox) — der Vertical Slice.

Läuft auf dem ENTFERNTEN Agenten-PC neben Claude-Code. Pollt seine Inbox,
startet bei einer Aufgabe Claude-Code headless und schreibt das Ergebnis
atomar zurück in die Outbox. Bewusst abhängigkeitsfrei (nur Standardlib),
damit er auf jedem Ziel-PC ohne pip-Install läuft.

Das Mailbox-Verzeichnis ist typischerweise per SSHFS/SFTP gemountet oder
wird vom Container per SSH synchronisiert.

Test ohne echtes Claude-Code:  --dry-run  (echoed die instruction zurück).

    python3 agent_watcher.py --agent frontend \
        --root /mnt/agent-dashboard/mailboxes --dry-run
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

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
    ap.add_argument("--root", required=True, help="Mailbox-Wurzel (enthält <agent>/)")
    ap.add_argument("--workdir", default=".", help="Arbeitsverzeichnis für Claude-Code")
    ap.add_argument("--interval", type=float, default=2.0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--once", action="store_true", help="einmal durchlaufen und beenden")
    ap.add_argument("--mcp-hint", action="store_true",
                    help="Identitäts-/Tool-Kontext voranstellen (wenn der "
                         "Dashboard-MCP-Server auf diesem PC registriert ist)")
    args = ap.parse_args()

    base = Path(args.root) / args.agent
    inbox, processing, outbox = base / "inbox", base / "inbox" / ".processing", base / "outbox"
    for d in (inbox, processing, outbox):
        d.mkdir(parents=True, exist_ok=True)
    workdir = Path(args.workdir).resolve()

    print(f"[{now()}] Watcher gestartet für '{args.agent}' "
          f"(dry_run={args.dry_run}) — beobachte {inbox}", flush=True)
    try:
        while True:
            process_once(inbox, processing, outbox, args.agent, workdir,
                         args.dry_run, args.mcp_hint)
            if args.once:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nWatcher beendet.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
