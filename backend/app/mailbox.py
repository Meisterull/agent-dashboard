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
- Verbrauch: erledigte Tasks räumt write_response ab (inbox/ UND .processing/).
  Bei status="done" wird gelöscht; bei status="error" wandert der Task nach
  inbox/.failed/ und die instruction steht zusätzlich in der Response — die
  einzige Kopie der Aufgabenbeschreibung darf bei einem Fehlschlag nicht
  verloren gehen (Issue #15). Gelesene message/answer wandern per mark_read
  nach inbox/.archive/ -> die Inbox enthält nur Offenes, nichts wird doppelt
  ausgeliefert.
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
#   response – Ergebnis eines erledigten Tasks (reply_to = task_id); legt
#              write_response in die Inbox des Auftraggebers
MESSAGE_KINDS = {"task", "message", "question", "answer", "response"}


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


def merged_instruction(env: dict[str, Any]) -> str:
    """Instruction eines Tasks inklusive Kontext aus einem früheren Lauf.

    Wurde ein Task wegen einer Rückfrage geparkt (Issue #17), stehen die
    Antworten in `nachtraege` und das bisherige Ergebnis in `zwischenstand`.
    Der nächste Lauf ist ein frischer Claude-Prozess ohne Gedächtnis — er
    braucht beides im Prompt, sonst ist die Antwort kontextlos."""
    teile = [env.get("instruction", "")]
    if env.get("zwischenstand"):
        teile.append("[Zwischenstand deines vorherigen Laufs — er endete mit "
                     "einer Rückfrage:]\n" + str(env["zwischenstand"]))
    for n in env.get("nachtraege") or []:
        teile.append(f'[Antwort auf deine Rückfrage "{n.get("frage", "")}": '
                     f'{n.get("antwort", "")}]')
    return "\n\n".join(t for t in teile if t)


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
        self.root = Path(root)
        self.base = self.root / agent
        self.inbox = self.base / "inbox"
        self.processing = self.inbox / ".processing"
        self.archive = self.inbox / ".archive"
        self.failed = self.inbox / ".failed"
        self.outbox = self.base / "outbox"
        for d in (self.inbox, self.processing, self.archive, self.failed, self.outbox):
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

        Damit message/answer/response (und erledigte questions) nicht bei
        jedem Inbox-Lesen erneut auftauchen. Offene Tasks sind tabu — die schließt
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

    # --- Rückfragen während eines Tasks (Issue #17) --------------------------

    def link_question(self, question_id: str, question_text: str) -> None:
        """Eine gestellte Rückfrage an die laufenden Tasks DIESES Agenten heften.

        Wird beim ask() des Agenten aufgerufen: jeder Task in .processing/
        bekommt die Frage in `open_questions`. complete_task weiß dadurch
        später, dass der Task nicht wirklich fertig ist — ohne dass der Agent
        etwas mitschicken muss (der Watcher hat genau einen Task in Arbeit)."""
        for p in sorted(self.processing.glob("*.json")):
            try:
                env = json.loads(p.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            if env.get("kind", "task") != "task":
                continue
            fragen = env.get("open_questions") or []
            if any(f.get("id") == question_id for f in fragen):
                continue
            fragen.append({"id": question_id, "frage": question_text})
            env["open_questions"] = fragen
            atomic_write_json(p, env)

    def _beantwortet(self, question_id: str) -> bool:
        """Liegt bereits eine Antwort auf diese Frage in Inbox oder Archiv?"""
        for ordner in (self.inbox, self.archive):
            for p in ordner.glob("*.json"):
                try:
                    env = json.loads(p.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    continue
                if env.get("kind") == "answer" and env.get("reply_to") == question_id:
                    return True
        return False

    def park_wenn_offene_fragen(self, task_id: str, zwischenstand: str) -> list[dict[str, Any]] | None:
        """Task mit unbeantworteter Rückfrage parken statt "done" melden (Issue #17).

        Ein `--print`-Lauf kann nicht auf Antworten warten — endet er, während
        seine Frage offen ist, wäre "done" ein falsches Erfolgssignal. Der Task
        bleibt stattdessen in .processing/ mit Status needs_confirm ("wartet
        auf Antwort" im Panel); resolve_question stößt ihn nach der Antwort
        wieder an. Rückgabe: die offenen Fragen, oder None (nichts offen,
        normal abschließen)."""
        p = self.processing / f"{task_id}.json"
        try:
            env = json.loads(p.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return None
        offen = [f for f in env.get("open_questions") or []
                 if f.get("id") and not self._beantwortet(f["id"])]
        if not offen:
            return None
        env["open_questions"] = offen
        env["status"] = "needs_confirm"
        if zwischenstand:
            env["zwischenstand"] = zwischenstand
        atomic_write_json(p, env)
        return offen

    def resolve_question(self, question_id: str, antwort: str) -> list[str]:
        """Antwort auf eine Rückfrage in geparkte/laufende Tasks einarbeiten.

        Auf der Mailbox des FRAGESTELLERS aufrufen, sobald eine answer zu
        `question_id` zugestellt wird: entfernt die Frage aus open_questions,
        hält Frage+Antwort als Nachtrag fest (merged_instruction reicht beides
        an den nächsten Lauf durch) und legt geparkte Tasks ohne weitere
        offene Fragen zurück in die Inbox — der Watcher greift sie beim
        nächsten Tick. Gibt die IDs der wieder angestoßenen Tasks zurück."""
        wieder: list[str] = []
        for p in sorted(self.processing.glob("*.json")):
            try:
                env = json.loads(p.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            fragen = env.get("open_questions") or []
            passend = [f for f in fragen if f.get("id") == question_id]
            if not passend:
                continue
            env["open_questions"] = [f for f in fragen if f.get("id") != question_id]
            env.setdefault("nachtraege", []).append(
                {"frage": passend[0].get("frage", ""), "antwort": antwort}
            )
            if env.get("status") == "needs_confirm" and not env["open_questions"]:
                # Erst in .processing/ aktualisieren, DANN atomar in die Inbox
                # schieben — so existiert der Task nie an zwei Orten zugleich.
                env["status"] = "pending"
                atomic_write_json(p, env)
                try:
                    os.replace(p, self.inbox / p.name)
                except FileNotFoundError:
                    continue
                wieder.append(env.get("task_id") or p.stem)
            else:
                # Task läuft noch (oder weitere Fragen offen): nur vermerken.
                atomic_write_json(p, env)
        return wieder

    def write_response(
        self, task_id: str, result: str, status: str = "done", log: str = ""
    ) -> Path:
        if status not in VALID_STATUS:
            raise ValueError(f"ungültiger Status: {status}")
        target = self.outbox / f"{task_id}-response.json"
        # Auftraggeber (`sender`) VOR dem Abräumen aus dem Task-Envelope retten —
        # danach wäre nicht mehr rekonstruierbar, für wen die Antwort war.
        stale_paths = (self.processing / f"{task_id}.json", self.inbox / f"{task_id}.json")
        task_env = None
        for p in stale_paths:
            try:
                task_env = json.loads(p.read_text(encoding="utf-8"))
                break
            except (FileNotFoundError, json.JSONDecodeError):
                continue
        to = (task_env or {}).get("sender")
        if to is None:
            # Doppelter Abschluss: Task-Envelope ist schon weg — `to` aus der
            # bereits geschriebenen Response erhalten statt es zu überschreiben.
            try:
                to = json.loads(target.read_text(encoding="utf-8")).get("to")
            except (FileNotFoundError, json.JSONDecodeError):
                pass
        response = {
            "task_id": task_id,
            "agent": self.agent,
            "to": to,
            "result": result,
            "status": status,
            "log": log,
            "responded_at": _now(),
        }
        # Fehlschlag: Aufgabenbeschreibung mit in die Antwort nehmen — der
        # Auftraggeber muss nachvollziehen können, WORUM es ging (Issue #15).
        if status == "error" and task_env is not None:
            for feld in ("instruction", "project", "files"):
                if task_env.get(feld):
                    response[feld] = task_env[feld]
        atomic_write_json(target, response)
        # Ergebnis zusätzlich als kind="response" in die Inbox des Auftraggebers
        # legen — analog ask/answer greift dann dessen normaler inbox()/
        # mark_read-Zyklus, ohne dass er fremde Outboxen pollen muss. Nur beim
        # ersten Abschluss (task_env noch da), sonst käme es doppelt an.
        if task_env is not None and to and to != self.agent:
            envelope = {
                "kind": "response",
                "sender": self.agent,
                "to": to,
                "text": result,
                "status": status,
                "reply_to": task_id,
            }
            if "instruction" in response:
                envelope["instruction"] = response["instruction"]
            try:
                Mailbox(self.root, to).post(envelope)
            except (ValueError, OSError):
                pass  # Zustellung ist best-effort; die Outbox-Response bleibt
        # Erledigten Task abräumen — aus BEIDEN möglichen Ablagen: .processing/
        # (beansprucht) UND inbox/ (nie beansprucht, z.B. interaktiver Agent
        # ohne claim_task). Sonst würde der Task trotz Antwort weiter geliefert.
        # done: löschen. error: nach inbox/.failed/ verschieben — die einzige
        # Kopie der instruction bleibt so für einen Wiederanlauf erhalten (#15).
        for stale in stale_paths:
            if status == "error":
                try:
                    os.replace(stale, self.failed / stale.name)
                except FileNotFoundError:
                    pass
            else:
                stale.unlink(missing_ok=True)
        return target
