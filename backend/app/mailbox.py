"""Atomare Datei-Mailbox — die riskanteste Primitive des Projekts.

Warum so viel Sorgfalt für "ein paar JSON-Dateien"?
Weil Polling/Watcher und Schreiber NEBENLÄUFIG auf dieselben Verzeichnisse
zugreifen. Ohne atomares Schreiben liest der Orchestrator irgendwann eine
halb geschriebene Datei und der ganze Roundtrip wird unzuverlässig.

Protokoll:
- Schreiben: erst nach <name>.tmp schreiben + fsync, dann os.replace()
  auf den Zielnamen. os.replace() ist auf POSIX atomar -> Leser sehen
  entweder die alte oder die vollständige neue Datei, nie etwas dazwischen.
- Verarbeiten: gelesene Tasks werden nach inbox/.processing/ verschoben,
  bevor gearbeitet wird -> kein Doppel-Pickup bei mehreren Watcher-Ticks.
- Verbrauch: erledigte Tasks räumt write_response ab (inbox/ UND .processing/),
  gelesene message/answer wandern per mark_read nach inbox/.archive/ ->
  die Inbox enthält nur Offenes, nichts wird doppelt ausgeliefert.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import uuid
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

VALID_STATUS = {"pending", "running", "done", "error", "needs_confirm"}

# Agent-Namen werden roh in Pfade gejoint (root/<agent>/inbox) — ohne diese
# Allowlist wäre `to="../.."` ein Path-Traversal aus dem Workspace heraus.
AGENT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")

# Inbox-Envelopes haben ein `kind`:
#   task     – Arbeitsauftrag (nur diese holt der Watcher und führt sie aus)
#   message  – informativer Hinweis Agent → Agent
#   question – Rückfrage, die eine Antwort braucht (Status needs_confirm)
#   answer   – Antwort auf eine question (reply_to verweist auf deren id)
MESSAGE_KINDS = {"task", "message", "question", "answer"}


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def new_id(prefix: str = "msg") -> str:
    """Kurze, kollisionsarme ID für Envelopes (z.B. msg-1a2b3c4d)."""
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    """Schreibt JSON atomar (tmp + fsync + replace) im selben Verzeichnis."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)  # atomar
    except BaseException:
        # Aufräumen, falls zwischen mkstemp und replace etwas schiefgeht.
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


@dataclass
class Task:
    task_id: str
    agent: str  # Empfänger
    instruction: str
    project: Optional[str] = None
    files: list[str] = field(default_factory=list)
    status: str = "pending"
    sender: str = "orchestrator"  # wer die Aufgabe stellt (z.B. ein Koordinator)
    kind: str = "task"
    created_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_envelope(env: dict[str, Any]) -> dict[str, Any]:
    """Vereinheitlicht Task- und Message-Envelopes für UI/Tools.

    Liefert immer: id, kind, sender, to, text, status, reply_to, created_at.
    """
    kind = env.get("kind", "task")
    return {
        "id": env.get("id") or env.get("task_id"),
        "kind": kind,
        "sender": env.get("sender") or env.get("from") or "orchestrator",
        "to": env.get("to") or env.get("agent"),
        "text": env.get("text") or env.get("instruction") or env.get("result") or "",
        "status": env.get("status", "pending"),
        "reply_to": env.get("reply_to"),
        "created_at": env.get("created_at"),
    }


class Mailbox:
    """Eine Mailbox = inbox/ + outbox/ unterhalb von root/<agent>/."""

    def __init__(self, root: str | os.PathLike, agent: str) -> None:
        if not AGENT_NAME_RE.fullmatch(agent):
            raise ValueError(f"ungültiger Agent-Name: {agent!r}")
        self.agent = agent
        self.base = Path(root) / agent
        self.inbox = self.base / "inbox"
        self.processing = self.inbox / ".processing"
        self.archive = self.inbox / ".archive"
        self.outbox = self.base / "outbox"
        for d in (self.inbox, self.processing, self.archive, self.outbox):
            d.mkdir(parents=True, exist_ok=True)

    # --- Orchestrator-Seite ------------------------------------------------
    def put_task(self, task: Task) -> Path:
        if task.status not in VALID_STATUS:
            raise ValueError(f"ungültiger Status: {task.status}")
        target = self.inbox / f"{task.task_id}.json"
        atomic_write_json(target, task.to_dict())
        return target

    def post(self, envelope: dict[str, Any]) -> dict[str, Any]:
        """Beliebigen Envelope (message/question/answer) in DIESE Inbox legen.

        Das ist die Agent-↔-Agent-Primitive: ein Koordinator legt Aufträge in
        Worker-Inboxes, ein Worker legt eine Rückfrage in die Koordinator-Inbox.
        """
        env = dict(envelope)
        env.setdefault("kind", "message")
        env.setdefault("id", new_id(env["kind"]))
        env.setdefault("status", "pending")
        env.setdefault("to", self.agent)
        env.setdefault("created_at", _now())
        if env["kind"] not in MESSAGE_KINDS:
            raise ValueError(f"ungültiges kind: {env['kind']}")
        if env["status"] not in VALID_STATUS:
            raise ValueError(f"ungültiger Status: {env['status']}")
        atomic_write_json(self.inbox / f"{env['id']}.json", env)
        return env

    def read_inbox(self, kind: str | None = None) -> list[dict[str, Any]]:
        """Inbox lesen (ohne zu beanspruchen). Optional nach kind filtern."""
        out = []
        for p in sorted(self.inbox.glob("*.json")):
            try:
                env = json.loads(p.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            if kind is None or env.get("kind", "task") == kind:
                out.append(env)
        return out

    def mark_read(self, env_id: str) -> bool:
        """Gelesenen Envelope aus der Inbox ins Archiv verschieben.

        Damit message/answer (und erledigte questions) nicht bei jedem
        Inbox-Lesen erneut auftauchen. Offene Tasks sind tabu — die schließt
        write_response (sonst verschwände Arbeit ohne Rückmeldung).
        """
        src = self.inbox / f"{env_id}.json"
        try:
            env = json.loads(src.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return False
        if env.get("kind", "task") == "task" and env.get("status") not in ("done", "error"):
            raise ValueError(
                f"{env_id} ist ein offener Task — mit complete_task/write_response "
                f"abschließen statt archivieren"
            )
        try:
            os.replace(src, self.archive / f"{env_id}.json")
        except FileNotFoundError:
            return False
        return True

    def read_responses(self) -> list[dict[str, Any]]:
        out = []
        for p in sorted(self.outbox.glob("*-response.json")):
            try:
                out.append(json.loads(p.read_text(encoding="utf-8")))
            except json.JSONDecodeError:
                # Halb geschriebene Datei beim nächsten Tick erneut versuchen.
                continue
        return out

    # --- Agent-Seite (Watcher) --------------------------------------------
    def claim_tasks(self) -> Iterable[tuple[str, dict[str, Any]]]:
        """Verschiebt offene *Tasks* atomar nach .processing/ und liefert sie.

        Nur kind == "task" (oder ohne kind, Rückwärtskompatibilität) wird
        beansprucht — message/question/answer bleiben für Koordinator/Dashboard.
        """
        for p in sorted(self.inbox.glob("*.json")):
            try:
                env = json.loads(p.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            if env.get("kind", "task") != "task":
                continue
            claimed = self.processing / p.name
            try:
                os.replace(p, claimed)  # atomar -> exklusiver Anspruch
            except FileNotFoundError:
                continue
            yield claimed.name, env

    def claim_task(self, task_id: str) -> Optional[dict[str, Any]]:
        """EINEN bestimmten Task beanspruchen (inbox → .processing).

        Für interaktive Agenten (MCP), die einen Task gezielt annehmen —
        macht "in Arbeit" sichtbar und verhindert Doppel-Pickup durch einen
        Watcher. Idempotent: ein schon beanspruchter Task wird erneut
        geliefert. None, wenn der Task nirgends (mehr) liegt.
        """
        src = self.inbox / f"{task_id}.json"
        dst = self.processing / f"{task_id}.json"
        try:
            env = json.loads(src.read_text(encoding="utf-8"))
            if env.get("kind", "task") != "task":
                raise ValueError(f"{task_id} ist kein Task (kind={env.get('kind')!r})")
            os.replace(src, dst)  # atomar -> exklusiver Anspruch
            return env
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        # Nicht (mehr) in der Inbox — evtl. schon beansprucht.
        try:
            return json.loads(dst.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return None

    def write_response(
        self, task_id: str, result: str, status: str = "done", log: str = ""
    ) -> Path:
        if status not in VALID_STATUS:
            raise ValueError(f"ungültiger Status: {status}")
        target = self.outbox / f"{task_id}-response.json"
        atomic_write_json(
            target,
            {
                "task_id": task_id,
                "agent": self.agent,
                "result": result,
                "status": status,
                "log": log,
                "responded_at": _now(),
            },
        )
        # Erledigten Task abräumen — aus BEIDEN möglichen Ablagen: .processing/
        # (beansprucht) UND inbox/ (nie beansprucht, z.B. interaktiver Agent
        # ohne claim_task). Sonst würde der Task trotz Antwort weiter geliefert.
        for stale in (self.processing / f"{task_id}.json", self.inbox / f"{task_id}.json"):
            stale.unlink(missing_ok=True)
        return target
