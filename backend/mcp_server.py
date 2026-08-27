"""MCP-Server: das Tool-Belt des Orchestrators und der Agenten.

Designentscheidung (siehe PROJECT.md → MCP-Server):
  (1) MCP = Werkzeugkasten des Orchestrators/Koordinators.  <-- DIESE.
      Eine Tool-Schicht für Claude/OpenRouter/Ollama gleichermaßen.
  (2) MCP als Transport zu den Agenten = später; aktuell macht das die Mailbox.

Tool-Gruppen:
  - Delegation:    list_agents, send_task (create_task als Alias), read_responses
  - Task-Lebenszyklus (Agent-Seite): claim_task, complete_task  (Gegenstück zu
      send_task — ohne complete_task bleibt jeder MCP-getriebene Task pending)
  - Agent-↔-Agent: send_message, ask, answer, inbox, mark_read  (killt das
      Fenster-Wechseln; mark_read archiviert Gelesenes)
  - Projektdateien: write_project_file, read_project_file
  - Integrationen:  list_integrations, call_integration  (config-getrieben, generisch)

Kanäle (Issue #13): Der FREIE Kanal (127.0.0.1:9000, intern — Orchestrator)
bietet wie bisher alle Tools mit frei wählbaren agent/sender-Parametern.
Zusätzlich lauscht pro SSH-Agent ein GEBUNDENER Kanal auf einem eigenen
Container-Port (app/mcp_scope.py): dorthin forwardet der Reverse-Tunnel des
Agenten, die Identität kommt also aus dem Kanal. Auf gebundenen Kanälen werden
agent/sender aus der Bindung abgeleitet, abweichende Werte abgelehnt und nur
die Tools der optionalen Allowlist (`tools:` in agents.yaml) registriert —
nicht Erlaubtes erscheint gar nicht erst in der Tool-Liste. Jeder Tool-Aufruf
wird mit Kanal-Name geloggt (Nachvollziehbarkeit bei mehreren Clients).

Transport: Streamable-HTTP, alle Ports nur auf 127.0.0.1 — intern hinter
nginx, nicht veröffentlicht. Alle Pfade gegen WORKSPACE_DIR gehärtet.

Nebenläufigkeit (Issue #34): Alle Kanäle laufen in EINEM Event-Loop, und das
SDK ruft sync-Tools direkt darin auf. Jedes Tool wird darum über `werkzeug`
registriert und in einem Thread ausgeführt — sonst friert ein langer Aufruf
(z.B. call_integration) alle anderen Agenten mit ein.
"""
from __future__ import annotations

import asyncio
import functools
import os
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from app import integrations, mcp_scope
from app.config import load_agents_full
from app.files import decode_text
from app.mailbox import (
    AGENT_NAME_RE,
    AlreadyClaimed,
    Mailbox,
    Task,
    merged_instruction,
    new_id,
    normalize_envelope,
)
from app.mcp_scope import ScopeError, resolve_ident

WORKSPACE = Path(os.environ.get("WORKSPACE_DIR", "/workspace")).resolve()
MAILBOX_ROOT = WORKSPACE / "mailboxes"
PROJECTS_ROOT = WORKSPACE / "projects"

MCP_HOST = os.environ.get("MCP_HOST", "127.0.0.1")
MCP_PORT = int(os.environ.get("MCP_PORT", "9000"))


def _safe(root: Path, *parts: str) -> Path:
    p = (root.joinpath(*parts)).resolve()
    if not (p == root or root in p.parents):
        raise ValueError(f"Pfad verlässt erlaubten Bereich: {p}")
    return p


def _atomic_write_text(path: Path, content: str) -> None:
    """Text atomar schreiben (tmp + fsync + replace) — Muster aus der Mailbox."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def _bekannte_agenten() -> list[str]:
    """Agenten mit existierender Mailbox — plus die konfigurierten SSH-Agenten."""
    # Der Orchestrator ist die eingebaute Identität (Default-`sender` überall)
    # und hat nicht zwingend schon eine Mailbox — Rückfragen an ihn müssen
    # trotzdem ankommen.
    namen = {"orchestrator"}
    if MAILBOX_ROOT.exists():
        namen |= {p.name for p in MAILBOX_ROOT.iterdir() if p.is_dir()}
    try:
        namen |= {a["name"] for a in load_agents_full() if a.get("name")}
    except Exception:  # noqa: BLE001 — kaputte agents.yaml darf Tools nicht killen
        pass
    return sorted(namen)


def _pruefe_empfaenger(to: str) -> dict | None:
    """Fehler-Dict, wenn `to` kein bekannter Agent ist — sonst None.

    Ohne diese Prüfung legt ein Tippfehler des LLM still eine neue Mailbox an:
    der Auftrag liegt dann für immer in einer Geister-Inbox, und der erfundene
    Name taucht ab da in list_agents auf und zementiert sich selbst.
    """
    if not AGENT_NAME_RE.fullmatch(to or ""):
        return {"error": f"ungültiger Agentenname: {to!r}"}
    bekannt = _bekannte_agenten()
    if to not in bekannt:
        return {
            "error": f"unbekannter Agent {to!r} — verfügbar: {', '.join(bekannt) or '(keine)'}"
        }
    return None


def _log(kanal: str, tool: str, **info: object) -> None:
    """Ein Aufruf-Logeintrag pro Tool-Call: welcher Kanal hat was aufgerufen.

    Bewusst nur Metadaten (Namen/IDs/Längen), keine Inhalte — die Logs sollen
    Fehlersuche ermöglichen, nicht Mailbox-Inhalte duplizieren."""
    kv = " ".join(f"{k}={v}" for k, v in info.items() if v not in (None, "", []))
    print(f"[mcp] {kanal}: {tool} {kv}".rstrip(), flush=True)


# --- Nebenläufigkeit: kein Tool blockiert den Event-Loop (Issue #34) --------
#
# Das mcp-SDK ruft SYNCHRONE Tools direkt im Event-Loop auf
# (fastmcp/utilities/func_metadata.py: `if fn_is_async: await fn(...) else:
# fn(...)`) — es gibt keinen Threadpool darunter. Und alle Kanäle teilen sich
# EINEN Loop (siehe _serve). Ein einziger langer Aufruf legte damit den
# gesamten MCP-Dienst stumm: ein `call_integration` auf eine minutenlange
# Browser-Automation fror auch `inbox` von `erp` und jedes `initialize` eines
# anderen Kanals ein, bis der Aufruf nach dem Timeout zurückkam.
#
# Darum wird JEDES Tool als async registriert und läuft in einem Thread. Die
# Mailbox ist darauf ausgelegt (flock je Mailbox + thread-lokale Buchführung
# der gehaltenen Locks), zwei Threads auf derselben Mailbox serialisieren also
# weiterhin sauber — nur eben ohne den Loop mitzunehmen.
#
# Integrationen bekommen einen EIGENEN Pool: sie sind die einzigen Aufrufe, die
# von Natur aus minutenlang dauern. Im gemeinsamen Pool könnten sie sonst alle
# Threads besetzen und die Mailbox-Tools ausbremsen — hier warten sie
# untereinander und lassen den Rest in Ruhe.
_INTEGRATION_PARALLEL = max(1, int(os.environ.get("MCP_INTEGRATION_PARALLEL", "4")))
_INTEGRATION_POOL = ThreadPoolExecutor(
    max_workers=_INTEGRATION_PARALLEL, thread_name_prefix="integration"
)


def _im_thread(fn, kanal: str, pool: ThreadPoolExecutor | None = None):
    """Ein sync-Tool in ein async-Tool verwandeln, das in einem Thread läuft.

    functools.wraps ist hier nicht Kosmetik: FastMCP baut Name, Beschreibung
    und JSON-Schema aus `inspect.signature`/`__doc__` der übergebenen Funktion,
    und `inspect.signature` folgt `__wrapped__` — die Tool-Beschreibung bleibt
    dadurch exakt dieselbe wie vorher.
    """

    @functools.wraps(fn)
    async def lauf(**kwargs):
        start = time.monotonic()
        ergebnis = "ok"
        try:
            return await asyncio.get_running_loop().run_in_executor(
                pool, functools.partial(fn, **kwargs)
            )
        except BaseException:
            ergebnis = "fehler"
            raise
        finally:
            # Gegenstück zum Start-Log in jedem Tool: erst mit dem Ende sieht
            # man im Log, WELCHER Aufruf wie lange gehangen hat (Issue #34) —
            # eine Startzeile ohne Endzeile ist der noch laufende.
            _log(kanal, fn.__name__, fertig=f"{time.monotonic() - start:.1f}s",
                 ergebnis=ergebnis)

    return lauf


def register_tools(mcp: FastMCP, identity: str | None, allowed: set[str] | None) -> None:
    """Alle Tools eines Kanals registrieren.

    identity=None -> freier Kanal (agent/sender frei, wie immer).
    identity=<name> -> gebundener Kanal: agent/sender werden aus der Bindung
    abgeleitet (Parameter optional), abweichende Werte abgelehnt.
    allowed=None -> alle Tools; sonst nur die gelisteten (Rest unsichtbar).
    """
    kanal = identity or "frei"

    def on(name: str) -> bool:
        return allowed is None or name in allowed

    def ident(given: str | None, feld: str) -> str:
        """Identität für einen Aufruf bestimmen (Kanal-Bindung vor Parameter)."""
        if identity is None:
            if not given:
                raise ScopeError(f"{feld} fehlt (freier Kanal hat keine Bindung).")
            return given
        return resolve_ident(identity, given, feld)

    def werkzeug(fn=None, *, pool: ThreadPoolExecutor | None = None):
        """Registriert ein Tool, dessen Aufruf im Thread läuft (Issue #34).

        Ersetzt den Dekorator des SDK: registriert wird die async-Hülle,
        zurückgegeben die unveränderte Funktion — Tests und Nachbarcode sehen
        weiterhin ein normales `def`.
        """

        def deco(f):
            mcp.tool()(_im_thread(f, kanal, pool))
            return f

        return deco if fn is None else deco(fn)

    # --- Delegation ---------------------------------------------------------

    if on("list_agents"):
        @werkzeug
        def list_agents() -> list[str]:
            """Listet die ansprechbaren Agenten.

            Das sind die vorhandenen Mailboxen PLUS die in agents.yaml
            konfigurierten Rechner — ein frisch eingerichteter Agent hat noch
            keine Mailbox, muss aber adressierbar sein (genau diese Menge
            akzeptieren send_task/send_message/ask).
            """
            _log(kanal, "list_agents")
            return _bekannte_agenten()

    if on("send_task"):
        @werkzeug
        def send_task(
            to: str,
            instruction: str,
            sender: str | None = None,
            project: str | None = None,
            files: list[str] | None = None,
        ) -> dict:
            """Einen Arbeitsauftrag in die Inbox eines Agenten legen.

            `sender` ist, wer delegiert (z.B. ein Koordinator-Agent) — an ihn geht
            nach Abschluss das Ergebnis als kind="response" in die Inbox zurück,
            also IMMER den eigenen Namen angeben (auf einem gebundenen Kanal ist
            er automatisch dein Name; weglassen genügt). Nur diese task-Envelopes
            führt der Watcher auf der Agent-Seite tatsächlich aus.
            """
            try:
                absender = ident(sender, "sender") if identity else (sender or "orchestrator")
            except ScopeError as exc:
                return {"error": str(exc)}
            unbekannt = _pruefe_empfaenger(to)
            if unbekannt:
                return unbekannt
            _log(kanal, "send_task", to=to, sender=absender, zeichen=len(instruction))
            task = Task(
                task_id=new_id("task"),
                agent=to,
                instruction=instruction,
                project=project,
                files=files or [],
                sender=absender,
            )
            Mailbox(MAILBOX_ROOT, to).put_task(task)
            return {"id": task.task_id, "to": to, "status": "pending"}

    if on("create_task"):
        @werkzeug
        def create_task(agent: str, instruction: str, project: str | None = None) -> dict:
            """Alias für send_task (Rückwärtskompatibilität). Absender = du
            (gebundener Kanal) bzw. "orchestrator" (freier Kanal)."""
            absender = identity or "orchestrator"
            unbekannt = _pruefe_empfaenger(agent)
            if unbekannt:
                return unbekannt
            _log(kanal, "create_task", to=agent, sender=absender, zeichen=len(instruction))
            task = Task(
                task_id=new_id("task"),
                agent=agent,
                instruction=instruction,
                project=project,
                files=[],
                sender=absender,
            )
            Mailbox(MAILBOX_ROOT, agent).put_task(task)
            return {"id": task.task_id, "to": agent, "status": "pending"}

    if on("read_responses"):
        @werkzeug
        def read_responses(worker: str, for_sender: str | None = None, limit: int = 20) -> list[dict]:
            """Rückmeldungen aus der Outbox eines BEARBEITERS lesen (Archiv erledigter Tasks).

            `worker` ist der Agent, der für dich gearbeitet hat — NICHT dein eigener
            Name (anders als bei claim_task/complete_task, wo `agent` = du selbst).
            Die eigene Outbox enthält nur die eigenen Antworten an andere.
            Meist unnötig: Ergebnisse landen beim Abschließen zusätzlich als
            kind="response" in der Inbox des Auftraggebers — `inbox()` genügt.
            `for_sender` filtert die Outbox auf Antworten an einen bestimmten
            Auftraggeber; auf einem gebundenen Kanal ist das immer dein Name.
            `limit` begrenzt auf die neuesten Einträge (die Outbox ist ein Archiv).
            """
            if identity is not None:
                try:
                    for_sender = resolve_ident(identity, for_sender, "for_sender")
                except ScopeError as exc:
                    return [{"error": str(exc)}]
            _log(kanal, "read_responses", worker=worker, for_sender=for_sender)
            if not AGENT_NAME_RE.fullmatch(worker or ""):
                return [{"error": f"ungültiger Agentenname: {worker!r}"}]
            out = Mailbox(MAILBOX_ROOT, worker).read_responses()
            if for_sender:
                out = [r for r in out if r.get("to") == for_sender]
            return out[: max(1, limit)]

    if on("claim_task"):
        @werkzeug
        def claim_task(task_id: str, agent: str | None = None, erneut: bool = False) -> dict:
            """Einen Task aus der eigenen Inbox annehmen, BEVOR du daran arbeitest.

            Markiert ihn als "in Arbeit" (inbox → .processing) — im Dashboard sichtbar,
            und kein Watcher greift ihn doppelt. `agent` = du selbst (der Bearbeiter;
            auf einem gebundenen Kanal weglassen). Danach: Aufgabe erledigen und mit
            complete_task abschließen. Ein bereits laufender Task wird NICHT erneut
            vergeben; arbeitest du selbst daran und brauchst den Auftragstext noch
            einmal, dann `erneut=True`.
            """
            try:
                wer = ident(agent, "agent")
            except ScopeError as exc:
                return {"error": str(exc)}
            _log(kanal, "claim_task", agent=wer, task=task_id)
            try:
                env = Mailbox(MAILBOX_ROOT, wer).claim_task(task_id, erneut=erneut)
            except AlreadyClaimed as exc:
                return {
                    "error": str(exc) + ". Falls du selbst daran arbeitest: "
                    "claim_task(..., erneut=True).",
                    "already_claimed": True,
                }
            if env is None:
                return {"error": f"Task {task_id} liegt nicht (mehr) bei {wer}."}
            # merged_instruction: nach einem geparkten Lauf (Issue #17) stehen
            # Rückfrage-Antworten und Zwischenstand mit im Prompt.
            # project/files müssen mit: der Watcher wählt daraus sein
            # Arbeitsverzeichnis (Issue #19) — fehlen sie, arbeitet er im
            # falschen Verzeichnis, statt sichtbar zu scheitern.
            return {
                "claimed": task_id,
                "instruction": merged_instruction(env),
                "status": "running",
                "project": env.get("project"),
                "files": env.get("files") or [],
            }

    if on("complete_task"):
        @werkzeug
        def complete_task(
            task_id: str,
            result: str,
            status: str = "done",
            log: str = "",
            agent: str | None = None,
        ) -> dict:
            """Einen bearbeiteten Task abschließen — das Gegenstück zu send_task.

            IMMER aufrufen, wenn du einen Task aus deiner Inbox fertig bearbeitet hast:
            legt `result` als kind="response" in die Inbox des Auftraggebers (der es
            dort per inbox() sieht), archiviert es in deiner Outbox und räumt den Task
            aus deiner Inbox. Ohne diesen Aufruf bleibt der Task für immer pending.
            `agent` = du selbst (der Bearbeiter — NICHT der Auftraggeber; auf einem
            gebundenen Kanal weglassen), status: "done" bei Erfolg, "error" bei Fehlschlag.
            """
            if status not in ("done", "error"):
                return {"error": 'status muss "done" oder "error" sein'}
            try:
                wer = ident(agent, "agent")
            except ScopeError as exc:
                return {"error": str(exc)}
            box = Mailbox(MAILBOX_ROOT, wer)
            # Wiederholter Abschluss ist KEIN Fehler: geht die Antwort auf dem
            # Rückweg verloren (Tunnel-Reconnect), liefert der Watcher dasselbe
            # Ergebnis erneut ab. Der Task ist dann schon abgeräumt — das als
            # Erfolg melden, statt ihn 5 Retrys lang gegen eine Wand laufen zu
            # lassen und am Ende fälschlich "nicht abgeliefert" zu loggen.
            if not box.task_offen(task_id):
                if (box.outbox / f"{task_id}-response.json").exists():
                    _log(kanal, "complete_task", agent=wer, task=task_id, status="bereits")
                    return {"task_id": task_id, "agent": wer, "status": status,
                            "already": True}
                return {"error": f"Task {task_id} liegt nicht (mehr) bei {wer}."}
            if status == "done":
                # Offene Rückfrage? Dann ist die Arbeit NICHT getan — Task
                # parken statt Erfolg zu melden; nach der Antwort wird er
                # automatisch wieder angestoßen (Issue #17).
                offen = box.park_wenn_offene_fragen(task_id, result)
                if offen:
                    _log(kanal, "complete_task", agent=wer, task=task_id,
                         status="needs_confirm", offene_fragen=len(offen))
                    return {"task_id": task_id, "agent": wer, "status": "needs_confirm",
                            "parked": True,
                            "hinweis": "Rückfrage unbeantwortet — Task wartet und "
                                       "läuft nach der Antwort erneut."}
            _log(kanal, "complete_task", agent=wer, task=task_id, status=status, zeichen=len(result))
            box.write_response(task_id, result, status, log)
            return {"task_id": task_id, "agent": wer, "status": status}

    # --- Agent-↔-Agent-Kommunikation ----------------------------------------

    if on("send_message"):
        @werkzeug
        def send_message(to: str, text: str, sender: str | None = None) -> dict:
            """Informativen Hinweis an einen anderen Agenten schicken (keine Aufgabe).

            `sender` = dein Name (auf einem gebundenen Kanal weglassen)."""
            try:
                absender = ident(sender, "sender") if identity else (sender or "orchestrator")
            except ScopeError as exc:
                return {"error": str(exc)}
            unbekannt = _pruefe_empfaenger(to)
            if unbekannt:
                return unbekannt
            _log(kanal, "send_message", to=to, sender=absender, zeichen=len(text))
            return Mailbox(MAILBOX_ROOT, to).post(
                {"kind": "message", "sender": absender, "to": to, "text": text}
            )

    if on("ask"):
        @werkzeug
        def ask(
            to: str,
            question: str,
            sender: str | None = None,
            reply_to: str | None = None,
            options: list[str] | None = None,
        ) -> dict:
            """Eine Rückfrage stellen, die eine Antwort braucht (Status needs_confirm).

            Damit fragt ein Worker z.B. den Koordinator nach Klärung — oder der
            Koordinator den Nutzer. Im Dashboard erscheint das als offene Rückfrage.
            `sender` = dein Name (auf einem gebundenen Kanal weglassen).

            `options` sind vorgegebene Antworten, z.B. ["ja", "nein"]. Sie
            erscheinen als Knöpfe im Rückfragen-Banner UND direkt in der
            Push-Benachrichtigung auf dem Handy — beantwortbar, ohne die App zu
            öffnen (Issue #30). Für Fragen mit schwerwiegenden Folgen bewusst
            WEGLASSEN: Ohne Optionen führt der Weg über die App, wo die volle
            Frage sichtbar ist, statt über einen Knopf am Sperrbildschirm.
            """
            try:
                absender = ident(sender, "sender") if identity else (sender or "orchestrator")
            except ScopeError as exc:
                return {"error": str(exc)}
            unbekannt = _pruefe_empfaenger(to)
            if unbekannt:
                return unbekannt
            _log(kanal, "ask", to=to, sender=absender)
            env = Mailbox(MAILBOX_ROOT, to).post(
                {
                    "kind": "question",
                    "sender": absender,
                    "to": to,
                    "text": question,
                    "status": "needs_confirm",
                    "reply_to": reply_to,
                    # Höchstens zwei: mehr Knöpfe zeigt keine Benachrichtigung
                    # an, und im Banner wird es unübersichtlich.
                    "options": [str(o) for o in options[:2]] if options else None,
                }
            )
            # Frage an die laufenden Tasks des Fragestellers heften (Issue #17):
            # complete_task(done) parkt den Task dann, bis die Antwort da ist.
            try:
                Mailbox(MAILBOX_ROOT, absender).link_question(env["id"], question)
            except ValueError:
                pass  # Absender ohne gültigen Mailbox-Namen — nichts zu verknüpfen
            return env

    if on("answer"):
        @werkzeug
        def answer(to: str, text: str, sender: str | None = None, reply_to: str | None = None) -> dict:
            """Eine Rückfrage beantworten. `reply_to` = id der beantworteten question.

            `sender` = dein Name (auf einem gebundenen Kanal weglassen)."""
            try:
                absender = ident(sender, "sender") if identity else (sender or "orchestrator")
            except ScopeError as exc:
                return {"error": str(exc)}
            _log(kanal, "answer", to=to, sender=absender)
            if reply_to:
                # Ein Weg für Dashboard und Tool (mailbox.beantworte_frage):
                # Antwort zustellen, die Frage aus DER EIGENEN Inbox archivieren
                # (sonst bleibt sie dort ewig als offen liegen) und die deswegen
                # geparkten Tasks des Fragestellers anstoßen (Issue #17).
                try:
                    ergebnis = Mailbox(MAILBOX_ROOT, absender).beantworte_frage(
                        reply_to, text, an=to
                    )
                except ValueError as exc:
                    return {"error": str(exc)}
                if ergebnis["wieder_angestossen"]:
                    _log(kanal, "answer", to=ergebnis["to"],
                         wieder_angestossen=",".join(ergebnis["wieder_angestossen"]))
                return {**ergebnis["answer"],
                        "wieder_angestossen": ergebnis["wieder_angestossen"]}
            unbekannt = _pruefe_empfaenger(to)
            if unbekannt:
                return unbekannt
            return Mailbox(MAILBOX_ROOT, to).post(
                {"kind": "answer", "sender": absender, "to": to, "text": text, "reply_to": reply_to}
            )

    if on("inbox"):
        @werkzeug
        def inbox(agent: str | None = None, kind: str | None = None) -> list[dict]:
            """Eingehende Envelopes eines Agenten lesen (Tasks, Nachrichten, Rückfragen
            und Task-Ergebnisse).

            `agent` = Besitzer der Inbox — auf einem gebundenen Kanal weglassen,
            du liest immer deine eigene. Ergebnisse delegierter Tasks kommen als
            kind="response" mit reply_to=<task_id> hier an. Die Inbox enthält nur
            Unerledigtes: Verarbeitetes danach mit mark_read archivieren (Tasks
            stattdessen mit claim_task/complete_task abschließen), sonst kommt
            derselbe Stapel bei jedem Aufruf wieder.
            """
            try:
                wer = ident(agent, "agent")
            except ScopeError as exc:
                return [{"error": str(exc)}]
            _log(kanal, "inbox", agent=wer, kind=kind)
            return [normalize_envelope(e) for e in Mailbox(MAILBOX_ROOT, wer).read_inbox(kind)]

    if on("mark_read"):
        @werkzeug
        def mark_read(envelope_id: str, agent: str | None = None) -> dict:
            """Einen verarbeiteten Envelope (message/answer/response/erledigte question) archivieren.

            Verschiebt ihn aus der Inbox nach inbox/.archive/ — er taucht bei künftigen
            inbox()-Aufrufen nicht mehr auf. Offene Tasks lassen sich so NICHT
            wegräumen (dafür complete_task). `agent` = Besitzer der Inbox (auf einem
            gebundenen Kanal weglassen).
            """
            try:
                wer = ident(agent, "agent")
            except ScopeError as exc:
                return {"error": str(exc)}
            _log(kanal, "mark_read", agent=wer, envelope=envelope_id)
            try:
                moved = Mailbox(MAILBOX_ROOT, wer).mark_read(envelope_id)
            except ValueError as exc:
                return {"error": str(exc)}
            if not moved:
                return {"error": f"Envelope {envelope_id} liegt nicht in der Inbox von {wer}."}
            return {"archived": envelope_id}

    # --- Projektdateien ------------------------------------------------------

    if on("write_project_file"):
        @werkzeug
        def write_project_file(project: str, relpath: str, content: str) -> dict:
            """Schreibt eine Datei unter /workspace/projects/<project>/<relpath>."""
            _log(kanal, "write_project_file", project=project, pfad=relpath, zeichen=len(content))
            target = _safe(PROJECTS_ROOT, project, relpath)
            target.parent.mkdir(parents=True, exist_ok=True)
            # Atomar wie die Mailbox: ein zweiter Agent liest über
            # read_project_file/SFTP evtl. gerade mit und darf keine halb
            # geschriebene Datei sehen.
            _atomic_write_text(target, content)
            return {"path": str(target), "bytes": len(content.encode("utf-8"))}

    if on("read_project_file"):
        @werkzeug
        def read_project_file(project: str, relpath: str) -> str:
            """Liest eine Datei unter /workspace/projects/<project>/<relpath>."""
            _log(kanal, "read_project_file", project=project, pfad=relpath)
            data = _safe(PROJECTS_ROOT, project, relpath).read_bytes()
            return decode_text(data)[0]

    # --- Integrationen (config-getrieben, generisch) -------------------------

    if on("list_integrations"):
        @werkzeug
        def list_integrations() -> list[dict]:
            """Verfügbare Integrationen (Name + erlaubte Methoden), ohne Secrets."""
            _log(kanal, "list_integrations")
            return integrations.list_integrations()

    if on("call_integration"):
        @werkzeug(pool=_INTEGRATION_POOL)
        def call_integration(name: str, method: str = "GET", path: str = "/", body: dict | None = None) -> dict:
            """Einen konfigurierten HTTP-Endpunkt aufrufen (z.B. eine interne API abfragen).

            Nur in integrations.yaml definierte Integrationen + erlaubte Methoden.
            Auth wird serverseitig injiziert. Gibt {status, body} zurück.
            """
            _log(kanal, "call_integration", name=name, method=method, path=path)
            try:
                return integrations.call_integration(name, method, path, body)
            except integrations.IntegrationError as exc:
                return {"error": str(exc)}


def build_server(port: int, identity: str | None = None, allowed: set[str] | None = None) -> FastMCP:
    """Eine FastMCP-Instanz für einen Kanal bauen (freier oder gebundener)."""
    name = "agent-dashboard" if identity is None else f"agent-dashboard[{identity}]"
    mcp = FastMCP(name, host=MCP_HOST, port=port)
    register_tools(mcp, identity, allowed)
    return mcp


def build_all() -> list[FastMCP]:
    """Freier Kanal + ein gebundener Kanal je SSH-Agent; schreibt die Port-Map."""
    instances = [build_server(MCP_PORT)]
    try:
        scopes, warnungen = mcp_scope.compute_scopes(load_agents_full())
    except Exception as exc:  # noqa: BLE001 — kaputte agents.yaml darf :9000 nicht killen
        print(f"[mcp] Scopes nicht ladbar ({exc}) — nur freier Kanal :{MCP_PORT}", flush=True)
        return instances
    for warnung in warnungen:
        print(f"[mcp] WARNUNG {warnung}", flush=True)
    for name, sc in scopes.items():
        tools = sc.get("tools")
        allowed = None if tools is None else set(tools)
        instances.append(build_server(sc["port"], identity=name, allowed=allowed))
        umfang = "alle Tools" if allowed is None else f"{len(allowed)} Tools"
        print(f"[mcp] gebundener Kanal {name} auf :{sc['port']} ({umfang})", flush=True)
    mcp_scope.write_port_map(scopes)
    return instances


async def _serve(instances: list[FastMCP]) -> None:
    import uvicorn

    servers = []
    for m in instances:
        cfg = uvicorn.Config(
            m.streamable_http_app(),
            host=m.settings.host,
            port=m.settings.port,
            log_level="warning",
        )
        servers.append(uvicorn.Server(cfg).serve())
    await asyncio.gather(*servers)


if __name__ == "__main__":
    alle = build_all()
    print(f"[mcp] {len(alle)} Kanäle — frei :{MCP_PORT} auf {MCP_HOST}", flush=True)
    asyncio.run(_serve(alle))
