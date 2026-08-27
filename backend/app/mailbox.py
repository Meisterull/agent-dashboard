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
- Read-Modify-Write (Rückfragen parken/auflösen, Anspruch stempeln, Antwort
  schreiben) läuft unter einem Datei-Lock je Mailbox (`<agent>/.lock`).
  Atomares Schreiben verhindert nur halbe Dateien — NICHT verlorene Updates:
  API-Prozess, MCP-Server und Watcher ändern dieselben Envelopes nebenläufig.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional

try:  # POSIX-Lock; auf Plattformen ohne fcntl bleibt nur die Atomarität
    import fcntl
except ImportError:  # pragma: no cover — Windows
    fcntl = None  # type: ignore[assignment]

VALID_STATUS = {"pending", "running", "done", "error", "needs_confirm"}

# Der Orchestrator ist die Instanz am Dashboard — also der MENSCH davor.
# Default-Absender für Aufträge und Empfänger der Rückfragen, die wirklich
# eine menschliche Entscheidung brauchen (Issue #22).
ORCHESTRATOR = "orchestrator"

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
    """Kollisionsarme ID für Envelopes (z.B. msg-1a2b3c4d5e6f7a8b).

    16 Hex-Zeichen statt 8: bei 8 liegt die Kollisionsschwelle (Geburtstag) bei
    ~65k Nachrichten, und eine Kollision würde einen fremden Envelope beim
    Schreiben still überschreiben.
    """
    return f"{prefix}-{uuid.uuid4().hex[:16]}"


class AlreadyClaimed(RuntimeError):
    """Task wird bereits bearbeitet — ein zweiter Claim ist kein Erfolg.

    Ohne dieses Signal liefert claim_task einen fremd beanspruchten Task erneut
    aus, und zwei Watcher führen denselben Auftrag doppelt aus.
    """

    def __init__(self, task_id: str, seit: str | None = None) -> None:
        self.task_id = task_id
        self.seit = seit
        super().__init__(
            f"Task {task_id} wird bereits bearbeitet"
            + (f" (seit {seit})" if seit else "")
        )


# Welche Mailbox-Locks dieser Thread schon hält — flock ist an die geöffnete
# Datei gebunden, ein verschachtelter Lock auf denselben Pfad würde sich also
# selbst blockieren.
_gehaltene_locks = threading.local()


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
    sender: str = ORCHESTRATOR  # wer die Aufgabe stellt (z.B. ein Koordinator)
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
        "sender": env.get("sender") or env.get("from") or ORCHESTRATOR,
        "to": env.get("to") or env.get("agent"),
        "text": env.get("text") or env.get("instruction") or env.get("result") or "",
        "status": env.get("status", "pending"),
        "reply_to": env.get("reply_to"),
        "created_at": env.get("created_at"),
        # project/files gehören zum Auftrag: der Watcher wählt daraus sein
        # Arbeitsverzeichnis (Issue #19). Fehlen sie hier, arbeitet er still im
        # falschen Verzeichnis statt zu scheitern.
        "project": env.get("project"),
        "files": env.get("files") or [],
        # Vorgegebene Antworten einer Rückfrage (Issue #30): Sie werden im
        # Banner zu Knöpfen und in der Push-Benachrichtigung zu Aktionen.
        # Ohne Durchreichen hier wären sie im Envelope zwar gespeichert, für
        # die Oberfläche aber unsichtbar.
        "options": env.get("options") or [],
    }


def _sortier_schluessel(env: dict[str, Any], pfad: Path) -> tuple[str, str]:
    """Eingangsreihenfolge (FIFO): created_at, ersatzweise die Dateizeit.

    Nach Dateinamen zu sortieren hieße nach Zufalls-Hex zu sortieren — Tasks
    liefen dann in beliebiger Reihenfolge statt in der ihrer Einreichung.
    """
    stamp = env.get("created_at")
    if not stamp:
        try:
            stamp = datetime.fromtimestamp(
                pfad.stat().st_mtime, timezone.utc
            ).astimezone().isoformat(timespec="seconds")
        except OSError:
            stamp = ""
    return (str(stamp), pfad.name)


def _alter_sekunden(env: dict[str, Any], pfad: Path) -> float:
    """Wie lange liegt dieser Envelope schon in Arbeit? (claimed_at, sonst mtime)"""
    stamp = env.get("claimed_at")
    if stamp:
        try:
            return max(0.0, time.time() - datetime.fromisoformat(str(stamp)).timestamp())
        except ValueError:
            pass
    try:
        return max(0.0, time.time() - pfad.stat().st_mtime)
    except OSError:
        return 0.0


def _lese_ordner(ordner: Path) -> list[tuple[Path, dict[str, Any]]]:
    """Alle Envelopes eines Ordners in Eingangsreihenfolge (kaputte übersprungen)."""
    out = []
    for p in ordner.glob("*.json"):
        try:
            out.append((p, json.loads(p.read_text(encoding="utf-8"))))
        except (json.JSONDecodeError, OSError):
            continue
    out.sort(key=lambda paar: _sortier_schluessel(paar[1], paar[0]))
    return out


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

    # --- Nebenläufigkeit ----------------------------------------------------
    @contextmanager
    def _lock(self) -> Iterator[None]:
        """Exklusiver Lock für Read-Modify-Write-Folgen auf DIESER Mailbox.

        Nötig, weil API-Prozess und MCP-Server getrennte Prozesse sind und
        beide dieselben Envelopes ändern (Frage parken vs. Antwort einarbeiten
        überschrieben sich sonst gegenseitig). Ohne fcntl (Windows) bleibt es
        beim atomaren Schreiben — dort läuft nur der Datei-Watcher.
        """
        gehalten = getattr(_gehaltene_locks, "pfade", None)
        if gehalten is None:
            gehalten = _gehaltene_locks.pfade = set()
        key = str(self.base)
        if fcntl is None or key in gehalten:
            yield  # kein flock verfügbar oder in diesem Thread schon gehalten
            return
        gehalten.add(key)
        try:
            try:
                fh = open(self.base / ".lock", "a", encoding="utf-8")
            except OSError as exc:
                # Kein Lock möglich (z.B. Dateisystem ohne flock) — weiterarbeiten
                # ist besser als gar nicht: atomares Schreiben greift trotzdem.
                print(f"[mailbox] {self.agent}: Lock nicht möglich ({exc})", flush=True)
                yield
                return
            try:
                fcntl.flock(fh, fcntl.LOCK_EX)
            except OSError as exc:
                print(f"[mailbox] {self.agent}: flock nicht unterstützt ({exc})", flush=True)
                fh.close()
                yield
                return
            try:
                yield
            finally:
                try:
                    fcntl.flock(fh, fcntl.LOCK_UN)
                finally:
                    fh.close()
        finally:
            gehalten.discard(key)

    def _freier_pfad(self, ordner: Path, env_id: str) -> Path:
        """Zielpfad für einen neuen Envelope — nie einen bestehenden treffen."""
        ziel = ordner / f"{env_id}.json"
        if not ziel.exists():
            return ziel
        raise FileExistsError(f"Envelope {env_id} existiert bereits")

    # --- Orchestrator-Seite ------------------------------------------------
    def put_task(self, task: Task) -> Path:
        if task.status not in VALID_STATUS:
            raise ValueError(f"ungültiger Status: {task.status}")
        target = self._freier_pfad(self.inbox, task.task_id)
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
        for _ in range(5):
            try:
                ziel = self._freier_pfad(self.inbox, env["id"])
            except FileExistsError:
                env["id"] = new_id(env["kind"])  # ID-Kollision: neu würfeln
                continue
            atomic_write_json(ziel, env)
            return env
        raise FileExistsError(f"keine freie Envelope-ID in {self.inbox}")

    def read_inbox(self, kind: str | None = None) -> list[dict[str, Any]]:
        """Inbox lesen (ohne zu beanspruchen). Optional nach kind filtern."""
        return [
            env
            for _, env in _lese_ordner(self.inbox)
            if kind is None or env.get("kind", "task") == kind
        ]

    def mark_read(self, env_id: str) -> bool:
        """Gelesenen Envelope aus der Inbox ins Archiv verschieben.

        Damit message/answer/response (und erledigte questions) nicht bei
        jedem Inbox-Lesen erneut auftauchen. Offene Tasks sind tabu — die schließt
        write_response (sonst verschwände Arbeit ohne Rückmeldung).
        """
        with self._lock():
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

    def alle_gelesen(self) -> int:
        """Alles Archivierbare auf einmal aus der Inbox räumen (Issue #21).

        Wer nur beauftragt und die Ergebnisse im Dashboard liest, ruft nie
        `mark_read` — die Responses stapeln sich dann für immer. Das hier ist
        der Knopf dafür: ein Durchgang, alles Erledigte ins Archiv.

        Tabu bleiben offene Tasks (die schließt write_response) und offene
        Rückfragen — eine weggeräumte `needs_confirm`-Frage verschwände aus
        dem Banner, ohne dass jemand geantwortet hätte.
        """
        weg = 0
        for _, env in _lese_ordner(self.inbox):
            env_id = env.get("id")
            if not env_id:
                continue
            if env.get("kind") == "question" and env.get("status") == "needs_confirm":
                continue
            try:
                if self.mark_read(env_id):
                    weg += 1
            except ValueError:
                continue  # offener Task
        return weg

    def read_responses(self, limit: int | None = None) -> list[dict[str, Any]]:
        """Antworten aus der Outbox, neueste zuerst.

        `limit` deckelt die Menge: die Outbox wächst unbegrenzt, und ein
        ungedeckelter Aufruf schiebt irgendwann das halbe Archiv in den
        LLM-Kontext.
        """
        out = []
        for p in self.outbox.glob("*-response.json"):
            try:
                out.append(json.loads(p.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError):
                # Halb geschriebene Datei beim nächsten Tick erneut versuchen.
                continue
        out.sort(key=lambda r: str(r.get("responded_at") or ""), reverse=True)
        return out[:limit] if limit else out

    # --- Agent-Seite (Watcher) --------------------------------------------
    def claim_tasks(self) -> Iterable[tuple[str, dict[str, Any]]]:
        """Verschiebt offene *Tasks* atomar nach .processing/ und liefert sie.

        Nur kind == "task" (oder ohne kind, Rückwärtskompatibilität) wird
        beansprucht — message/question/answer bleiben für Koordinator/Dashboard.
        """
        for p, env in _lese_ordner(self.inbox):
            if env.get("kind", "task") != "task":
                continue
            claimed = self.processing / p.name
            try:
                os.replace(p, claimed)  # atomar -> exklusiver Anspruch
            except FileNotFoundError:
                continue
            env["claimed_at"] = _now()
            try:
                atomic_write_json(claimed, env)
            except OSError:
                pass  # Stempel ist Diagnose, kein Grund den Task fallenzulassen
            yield claimed.name, env

    def task_offen(self, task_id: str) -> bool:
        """Liegt der Task noch irgendwo offen (Inbox oder in Arbeit)?"""
        return (self.inbox / f"{task_id}.json").exists() or (
            self.processing / f"{task_id}.json"
        ).exists()

    def claim_task(self, task_id: str, erneut: bool = False) -> Optional[dict[str, Any]]:
        """EINEN bestimmten Task beanspruchen (inbox → .processing).

        Der Anspruch ist EXKLUSIV: liegt der Task schon in .processing, wirft
        das AlreadyClaimed statt ihn erneut auszuliefern — sonst führen zwei
        Watcher (Netz-Flap, manuell gestarteter zweiter Watcher) denselben
        Auftrag doppelt aus. `erneut=True` liefert ihn trotzdem: der EIGENE
        Bearbeiter braucht nach einem Kontextverlust seinen Auftragstext.
        None, wenn der Task nirgends (mehr) liegt.
        """
        src = self.inbox / f"{task_id}.json"
        dst = self.processing / f"{task_id}.json"
        with self._lock():
            env = None
            try:
                env = json.loads(src.read_text(encoding="utf-8"))
                if env.get("kind", "task") != "task":
                    raise ValueError(f"{task_id} ist kein Task (kind={env.get('kind')!r})")
                os.replace(src, dst)  # atomar -> exklusiver Anspruch
            except (FileNotFoundError, json.JSONDecodeError):
                env = None
            if env is not None:
                # Zeitstempel des Anspruchs: Grundlage für "verwaist?" und für
                # die Fehlermeldung an einen zweiten Claimer.
                env["claimed_at"] = _now()
                atomic_write_json(dst, env)
                return env
            try:
                laufend = json.loads(dst.read_text(encoding="utf-8"))
            except (FileNotFoundError, json.JSONDecodeError):
                return None
            if not erneut:
                raise AlreadyClaimed(task_id, laufend.get("claimed_at"))
            return laufend

    # --- Rückfragen während eines Tasks (Issue #17) --------------------------

    def link_question(self, question_id: str, question_text: str) -> None:
        """Eine gestellte Rückfrage an die laufenden Tasks DIESES Agenten heften.

        Wird beim ask() des Agenten aufgerufen: jeder Task in .processing/
        bekommt die Frage in `open_questions`. complete_task weiß dadurch
        später, dass der Task nicht wirklich fertig ist — ohne dass der Agent
        etwas mitschicken muss (der Watcher hat genau einen Task in Arbeit)."""
        with self._lock():
            for p, env in _lese_ordner(self.processing):
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
        with self._lock():
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

    def verwerfe_frage(self, question_id: str, grund: str = "") -> list[str]:
        """Eine ohne Antwort geschlossene Rückfrage aus den eigenen Tasks lösen (Issue #23).

        Gegenstück zu resolve_question, auf der Mailbox des FRAGESTELLERS: es
        kommt keine Antwort mehr. Die Frage fliegt aus `open_questions`, der
        Klartext bleibt als Nachtrag stehen — und ein Task, der NUR auf sie
        gewartet hat (needs_confirm, keine weitere Frage offen), scheitert mit
        ebendiesem Klartext. Über Issue #15 landet er mitsamt instruction in
        inbox/.failed/ und ist von dort wiederanlauffähig; ohne das bliebe er
        für immer in .processing/ liegen (requeue_stale fasst needs_confirm
        bewusst nicht an). Hängen weitere Fragen an ihm, bleibt er geparkt.
        Gibt die IDs der gescheiterten Tasks zurück."""
        hinweis = grund.strip() or "kein Grund angegeben"
        text = f"Rückfrage wurde ohne Antwort geschlossen: {hinweis}"
        gescheitert: list[str] = []
        with self._lock():
            for p, env in _lese_ordner(self.processing):
                fragen = env.get("open_questions") or []
                passend = [f for f in fragen if f.get("id") == question_id]
                if not passend:
                    continue
                env["open_questions"] = [f for f in fragen if f.get("id") != question_id]
                env.setdefault("nachtraege", []).append(
                    {"frage": passend[0].get("frage", ""), "antwort": f"[{text}]"}
                )
                # Erst den Envelope aktualisieren, dann ggf. scheitern lassen —
                # so trägt auch die Kopie in .failed/ den Nachtrag.
                atomic_write_json(p, env)
                if env.get("status") == "needs_confirm" and not env["open_questions"]:
                    task_id = env.get("task_id") or p.stem
                    self._write_response(task_id, text, "error", log="question closed")
                    gescheitert.append(task_id)
        return gescheitert

    def resolve_question(self, question_id: str, antwort: str) -> list[str]:
        """Antwort auf eine Rückfrage in geparkte/laufende Tasks einarbeiten.

        Auf der Mailbox des FRAGESTELLERS aufrufen, sobald eine answer zu
        `question_id` zugestellt wird: entfernt die Frage aus open_questions,
        hält Frage+Antwort als Nachtrag fest (merged_instruction reicht beides
        an den nächsten Lauf durch) und legt geparkte Tasks ohne weitere
        offene Fragen zurück in die Inbox — der Watcher greift sie beim
        nächsten Tick. Gibt die IDs der wieder angestoßenen Tasks zurück."""
        wieder: list[str] = []
        with self._lock():
            for p, env in _lese_ordner(self.processing):
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
                    env.pop("claimed_at", None)  # zurück in der Warteschlange
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
        """Task abschließen: Response schreiben, zustellen, Task abräumen.

        Läuft unter dem Mailbox-Lock, weil Abschluss und Rückfragen-Parken
        (Issue #17) denselben Envelope anfassen.
        """
        with self._lock():
            return self._write_response(task_id, result, status, log)

    def _write_response(
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

    # --- Aufräumen / Wiederanlauf -------------------------------------------

    def beantworte_frage(
        self,
        question_id: str,
        text: str,
        an: str | None = None,
        answered_by: str | None = None,
    ) -> dict[str, Any]:
        """Eine Rückfrage aus DIESER Inbox beantworten — eine Wahrheit für alle Wege.

        Dashboard und MCP-`answer` liefen früher auseinander: der eine setzte
        die Frage auf "done" und ließ sie liegen (Inbox wuchs), der andere
        fasste sie gar nicht an (Banner zeigte sie weiter als offen). Hier
        passiert beides zusammen: Antwort in die Inbox des Fragestellers, Frage
        ins Archiv, geparkte Tasks des Fragestellers wieder anstoßen.

        `answered_by` vermerkt, WER statt des Empfängers geantwortet hat
        (Dashboard-Mensch statt Agent, Issue #22) — für den Fragesteller wäre
        das sonst ununterscheidbar.
        """
        qpath = self.inbox / f"{question_id}.json"
        frage: dict[str, Any] | None = None
        with self._lock():
            try:
                frage = json.loads(qpath.read_text(encoding="utf-8"))
            except (FileNotFoundError, json.JSONDecodeError):
                frage = None
            if frage is not None:
                frage["status"] = "done"
                frage["answered_at"] = _now()
                atomic_write_json(qpath, frage)
                try:
                    os.replace(qpath, self.archive / qpath.name)
                except FileNotFoundError:
                    pass
        ziel = (frage or {}).get("sender") or an
        if not ziel:
            raise ValueError(f"kein Empfänger für die Antwort auf {question_id}")
        # Lock ist hier bewusst wieder frei: die nächsten Schritte laufen auf
        # der Mailbox des FRAGESTELLERS, die ihren eigenen Lock nimmt.
        fragesteller = Mailbox(self.root, ziel)
        envelope = {
            "kind": "answer",
            "sender": self.agent,
            "to": ziel,
            "text": text,
            "reply_to": question_id,
        }
        if answered_by:
            envelope["answered_by"] = answered_by
        antwort = fragesteller.post(envelope)
        wieder = fragesteller.resolve_question(question_id, text)
        return {
            "answer": antwort,
            "to": ziel,
            "frage_archiviert": frage is not None,
            "wieder_angestossen": wieder,
        }

    def schliesse_frage(self, question_id: str, grund: str = "") -> dict[str, Any]:
        """Eine Rückfrage aus DIESER Inbox OHNE Antwort schließen (Issue #23).

        Das Gegenstück zu `beantworte_frage`, für Fragen, die sich erledigt
        haben, ins Leere zielen oder falsch adressiert sind. Bisher gab es aus
        einer Rückfrage genau einen Ausgang — antworten. Seit Issue #17 hängt
        an ihr aber ein geparkter Task, also war das auch der einzige Ausgang
        aus dem Task.

        Frage ins Archiv (mit `closed_at`/`closed_reason`, damit dort nicht wie
        bei einer echten Antwort "done" ohne Kontext steht), dann beim
        Fragesteller aufräumen: `verwerfe_frage` lässt den geparkten Task mit
        Klartext scheitern statt ihn stillschweigend weiterlaufen zu lassen.
        """
        qpath = self.inbox / f"{question_id}.json"
        frage: dict[str, Any] | None = None
        with self._lock():
            try:
                frage = json.loads(qpath.read_text(encoding="utf-8"))
            except (FileNotFoundError, json.JSONDecodeError):
                frage = None
            if frage is not None:
                frage["status"] = "done"  # VALID_STATUS kennt kein "closed"
                frage["closed_at"] = _now()
                frage["closed_reason"] = grund
                atomic_write_json(qpath, frage)
                try:
                    os.replace(qpath, self.archive / qpath.name)
                except FileNotFoundError:
                    pass
        ziel = (frage or {}).get("sender")
        # Lock wieder frei — der Fragesteller nimmt seinen eigenen (s.o.).
        gescheitert = Mailbox(self.root, ziel).verwerfe_frage(question_id, grund) if ziel else []
        return {
            "to": ziel,
            "frage_archiviert": frage is not None,
            "gescheiterte_tasks": gescheitert,
        }

    def requeue_stale(
        self, max_alter: float, max_versuche: int = 3
    ) -> dict[str, list[str]]:
        """Verwaiste Tasks aus .processing/ zurück in die Warteschlange.

        Stirbt ein Watcher mitten im Lauf (Absturz, Not-Aus, Stromausfall,
        SSH-Abbruch), bleibt sein Task für immer "running" — .processing/ liest
        sonst niemand mehr. Wartet ein Task auf eine Rückfrage (needs_confirm),
        bleibt er unangetastet. Nach `max_versuche` vergeblichen Anläufen wird
        er als Fehlschlag abgeschlossen, damit ein giftiger Task nicht ewig
        zwischen Inbox und .processing kreist.
        """
        requeued: list[str] = []
        aufgegeben: list[str] = []
        with self._lock():
            for p, env in _lese_ordner(self.processing):
                if env.get("kind", "task") != "task":
                    continue
                if env.get("status") == "needs_confirm":
                    continue  # wartet auf eine Antwort, nicht verwaist
                if _alter_sekunden(env, p) < max_alter:
                    continue
                versuche = int(env.get("requeues") or 0) + 1
                task_id = env.get("task_id") or p.stem
                if versuche > max_versuche:
                    aufgegeben.append(task_id)
                    continue
                env["requeues"] = versuche
                env["status"] = "pending"
                env.pop("claimed_at", None)
                atomic_write_json(p, env)
                try:
                    os.replace(p, self.inbox / p.name)
                except FileNotFoundError:
                    continue
                requeued.append(task_id)
        # Aufgeben heißt abschließen — außerhalb der Schleife, weil
        # write_response denselben Lock nimmt (bei uns re-entrant, aber die
        # Ordner-Iteration soll nicht unter der Hand verändert werden).
        for task_id in aufgegeben:
            self.write_response(
                task_id,
                f"[Abgebrochen: {max_versuche}× ohne Ergebnis wieder aufgenommen — "
                f"der Bearbeiter bricht offenbar reproduzierbar ab.]",
                "error",
                log="requeue-limit erreicht",
            )
        return {"requeued": requeued, "aufgegeben": aufgegeben}

    def aufraeumen(self, max_tage: float, inbox_tage: float = 0) -> int:
        """Alte Ablagen rotieren: .archive/, .failed/ und Outbox-Responses.

        Ohne das wächst die Mailbox unbegrenzt — und `read_responses` bzw. das
        Agenten-Panel liefern irgendwann Jahresarchive aus.

        `inbox_tage` > 0 nimmt zusätzlich die Inbox mit (Issue #21), aber nur
        alte **Protokoll**-Envelopes: `response` und `answer`. Die stapeln sich
        bei einem Agenten, der nur beauftragt und nie `mark_read` ruft. Tasks
        und Fragen bleiben ausdrücklich liegen — das ist Arbeitsvorrat, kein
        Protokoll, und niemand darf ihn im Hintergrund verschwinden lassen.
        Gelöscht wird hier nichts: die Envelopes wandern ins Archiv und
        verfallen erst dort nach `max_tage`. Deshalb ist `inbox_tage` sinnvoll
        kleiner als `max_tage`.
        """
        grenze = time.time() - max_tage * 86400
        weg = 0
        for ordner in (self.archive, self.failed, self.outbox):
            for p in ordner.glob("*.json"):
                try:
                    if p.stat().st_mtime >= grenze:
                        continue
                    p.unlink()
                    weg += 1
                except OSError:
                    continue
        if inbox_tage > 0:
            grenze_inbox = time.time() - inbox_tage * 86400
            with self._lock():
                for p, env in _lese_ordner(self.inbox):
                    if env.get("kind") not in ("response", "answer"):
                        continue
                    try:
                        if p.stat().st_mtime >= grenze_inbox:
                            continue
                        os.replace(p, self.archive / p.name)
                        weg += 1
                    except OSError:
                        continue
        return weg


# --- Wartung über alle Mailboxen ------------------------------------------

def alle_mailboxen(root: str | os.PathLike) -> list["Mailbox"]:
    """Jede existierende Mailbox unter root (ungültige Ordnernamen ignoriert)."""
    basis = Path(root)
    if not basis.is_dir():
        return []
    out = []
    for p in sorted(basis.iterdir()):
        if not p.is_dir() or not AGENT_NAME_RE.fullmatch(p.name):
            continue
        try:
            out.append(Mailbox(basis, p.name))
        except (ValueError, OSError):
            continue
    return out


def pflege(
    root: str | os.PathLike, stale_alter: float, archiv_tage: float,
    inbox_tage: float = 0
) -> dict[str, Any]:
    """Periodische Mailbox-Pflege: verwaiste Tasks + alte Ablagen.

    Wird vom API-Prozess regelmäßig aufgerufen (main.py). Bewusst hier und
    nicht im Watcher: der Watcher ist genau der Prozess, der stirbt.

    `inbox_tage` reicht die Inbox-Rotation aus Issue #21 durch (nur alte
    response/answer, siehe `Mailbox.aufraeumen`).
    """
    bericht: dict[str, Any] = {"requeued": [], "aufgegeben": [], "geloescht": 0}
    for box in alle_mailboxen(root):
        try:
            ergebnis = box.requeue_stale(stale_alter)
            bericht["requeued"] += [f"{box.agent}/{t}" for t in ergebnis["requeued"]]
            bericht["aufgegeben"] += [f"{box.agent}/{t}" for t in ergebnis["aufgegeben"]]
            bericht["geloescht"] += box.aufraeumen(archiv_tage, inbox_tage)
        except OSError:
            continue
    return bericht
