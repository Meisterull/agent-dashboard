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
  - Dateiaustausch: send_file  (Maschine → Austausch-Ordner einer anderen, per
      SFTP am Modell vorbei — app/austausch.py)
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
import json
import os
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from app import austausch, erinnerungen, freigaben, geheimnisse, integrations, kosten, vorgaenge, pakete, mcp_scope, rollen, weckruf, zwischenspeicher
from app.config import load_agents_full, load_settings
from app.files import decode_text
from datetime import datetime

from app import ereignisse
from app.mailbox import (
    _lese_ordner,
    AGENT_NAME_RE,
    CLAIM_SITZUNG,
    CLAIM_WATCHER,
    AlreadyClaimed,
    Mailbox,
    Task,
    ZuFrueh,
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


def _darf_ereignisse_lesen(identity: str, ziel: str) -> bool:
    """Lesendes Recht eines gebundenen Kanals auf das Ereignis-Log eines
    anderen Agenten (Issue #47): agents.yaml `darf_ereignisse_lesen: [a, b]`
    oder `"*"` am Agenten bzw. in seiner connection. Default: nein."""
    try:
        eintrag = next((a for a in load_agents_full() if a.get("name") == identity), None)
    except Exception:  # noqa: BLE001 — kaputte agents.yaml gibt nichts frei
        return False
    if not eintrag:
        return False
    for quelle in (eintrag.get("darf_ereignisse_lesen"),
                   (eintrag.get("connection") or {}).get("darf_ereignisse_lesen")):
        if quelle is None:
            continue
        erlaubt = [quelle] if isinstance(quelle, str) else list(quelle)
        return "*" in erlaubt or ziel in erlaubt
    return False


def _modelle_erlaubt(agent: str) -> list[str]:
    """agents.yaml `modelle_erlaubt` des Empfängers (Issue #62): welche
    Modelle ein Auftraggeber per send_task(model=…) bestellen darf."""
    try:
        cfg = next((a for a in load_agents_full() if a.get("name") == agent), None) or {}
    except Exception:  # noqa: BLE001
        return []
    roh = cfg.get("modelle_erlaubt")
    if roh is None:
        roh = (cfg.get("connection") or {}).get("modelle_erlaubt")
    if isinstance(roh, str):
        roh = roh.split(",")
    return [str(m).strip() for m in (roh or []) if str(m).strip()]


def _weck_hinweis(env: dict, to: str, kind: str) -> dict:
    """Issue #65: dem Absender sagen, wenn seine Post einen Automatik-Agenten
    nicht weckt — er kann dann selbst auf send_task umsteigen."""
    try:
        cfg = next((a for a in load_agents_full() if a.get("name") == to), None)
        hinweis = weckruf.absender_hinweis(to, kind, cfg, load_settings())
    except Exception:  # noqa: BLE001 — ein Hinweis darf den Versand nie kippen
        hinweis = None
    return {**env, "hinweis": hinweis} if hinweis else env


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


def _pruefe_absender(identity: str | None, absender: str) -> dict | None:
    """Geister-Mailbox-Schutz für SENDER (Review P1-7).

    Auf gebundenen Kanälen ist der Absender die (konfigurierte) Bindung —
    nichts zu prüfen. Auf dem FREIEN Kanal ist er frei wählbar, und jeder
    Pfad, der später Mailbox(absender) anfasst (Response-Zustellung,
    link_question, beantworte_frage), legt das Verzeichnis an: ein erfundener
    Name zementierte sich damit in list_agents. `orchestrator` ist die
    eingebaute Identität und immer erlaubt."""
    if identity is not None or absender == "orchestrator":
        return None
    unbekannt = _pruefe_empfaenger(absender)
    if unbekannt:
        return {"error": f"sender: {unbekannt['error']}"}
    return None


def _log(kanal: str, tool: str, **info: object) -> None:
    """Ein Aufruf-Logeintrag pro Tool-Call: welcher Kanal hat was aufgerufen.

    Bewusst nur Metadaten (Namen/IDs/Längen), keine Inhalte — die Logs sollen
    Fehlersuche ermöglichen, nicht Mailbox-Inhalte duplizieren."""
    kv = " ".join(f"{k}={v}" for k, v in info.items() if v not in (None, "", []))
    # Zeitstempel in der Zeile selbst (Issue #40): ohne ihn ließ sich ein
    # Ablauf nur über `docker logs -t` rekonstruieren.
    print(f"{_zeit()} [mcp] {kanal}: {tool} {kv}".rstrip(), flush=True)


def _zeit() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def fehler_im_ergebnis(wert: object) -> str | None:
    """Fehlertext, wenn ein Tool seinen Fehler als WERT zurückgab (Issue #40).

    Die Tools melden Fehler bewusst als {"error": …} statt per Exception —
    das Modell soll den Text lesen können. Das Log buchte solche Aufrufe aber
    als `ergebnis=ok`: fünf Integrations-Timeouts in Folge sahen aus wie fünf
    Erfolge. Listen-Tools (inbox, read_responses) verpacken den Fehler als
    einziges Listenelement."""
    if isinstance(wert, list) and len(wert) == 1:
        wert = wert[0]
    if isinstance(wert, dict) and wert.get("error"):
        return str(wert["error"])
    return None


def ist_watcher_poll(tool: str, kwargs: dict, wert: object) -> bool:
    """Das Leerlauf-Pollen des Automatik-Watchers: inbox(kind="task") ohne
    Treffer. Es ist weder ein Lebenszeichen (Issue #42) noch eine Logzeile
    wert (Issue #41: >1.200 Aufrufe in zwei Stunden, je zwei Zeilen — die
    interessanten Zeilen rotierten nach wenigen Tagen aus dem Log)."""
    return tool == "inbox" and kwargs.get("kind") == "task" and wert == []


# Werkzeuge, die den Bearbeiter nicht als „arbeitet gerade" ausweisen: das
# Pollen und der Anspruch selbst (claim stempelt den Task ohnehin).
_KEIN_LEBENSZEICHEN = {"claim_task", "list_agents", "list_integrations", "list_rollen"}


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

# Dateiübergaben (send_file) ebenso: eine große Datei über zwei SFTP-Strecken
# dauert, und sie soll weder die Mailbox-Tools noch die Integrationen bremsen.
_AUSTAUSCH_POOL = ThreadPoolExecutor(
    max_workers=max(1, int(os.environ.get("MCP_AUSTAUSCH_PARALLEL", "2"))),
    thread_name_prefix="austausch",
)


# Freigabe-Anfragen (Issue #56) WARTEN auf den Menschen — bis zu 15 min je
# Aufruf. Eigener Pool, damit sie keinem anderen Tool einen Thread wegnehmen.
_FREIGABE_POOL = ThreadPoolExecutor(
    max_workers=max(2, int(os.environ.get("MCP_FREIGABE_PARALLEL", "16"))),
    thread_name_prefix="freigabe",
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
        still = False
        try:
            wert = await asyncio.get_running_loop().run_in_executor(
                pool, functools.partial(fn, **kwargs)
            )
            fehler = fehler_im_ergebnis(wert)
            if fehler:
                # Kurz genug für eine Logzeile, ohne Mailbox-Inhalte: es ist
                # der Fehlertext des Tools, nicht die Nutzlast.
                ergebnis = "fehler:" + " ".join(fehler.split())[:120]
            still = ist_watcher_poll(fn.__name__, kwargs, wert)
            if kanal != "frei" and not still and fn.__name__ not in _KEIN_LEBENSZEICHEN:
                _lebenszeichen(kanal)
            return wert
        except BaseException:
            ergebnis = "fehler"
            raise
        finally:
            # Gegenstück zum Start-Log in jedem Tool: erst mit dem Ende sieht
            # man im Log, WELCHER Aufruf wie lange gehangen hat (Issue #34) —
            # eine Startzeile ohne Endzeile ist der noch laufende. Der leere
            # Watcher-Poll bleibt still, solange er nicht auffällig lange lief.
            dauer = time.monotonic() - start
            if not still or dauer >= 2.0:
                _log(kanal, fn.__name__, fertig=f"{dauer:.1f}s", ergebnis=ergebnis)

    return lauf


def _lebenszeichen(agent: str) -> None:
    """Echte MCP-Aktivität eines gebundenen Kanals = der Agent arbeitet
    (Issue #42) — die Pflege reiht dann keinen seiner Tasks zurück."""
    try:
        Mailbox(MAILBOX_ROOT, agent).lebenszeichen()
    except (ValueError, OSError):
        pass


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

    if on("agent_events"):
        @werkzeug
        def agent_events(agent: str | None = None, seit: str | None = None,
                         art: str | None = None, limit: int = 30,
                         nur_probleme: bool = False) -> dict:
            """Ereignis-Log eines Agenten lesen (Beobachtbarkeit der Automatik).

            Neueste zuerst: Läufe (Status, Dauer, Tokens, Kosten, Kontext),
            Sitzung neu/fortgesetzt samt Grund, verweigerte Aufrufe, Timeouts,
            Fehlserien und Watcher-Abrisse, Weckrufe, send_file, Integrationen.
            `agent` = wessen Log (auf einem gebundenen Kanal weglassen = du
            selbst), `seit` = ISO-Zeit, `art` = eine Art oder kommagetrennt,
            `nur_probleme` = nur warnung/fehler. Nur lesend.
            """
            # Issue #47: ein gebundener Kanal darf das Log eines ANDEREN nur
            # lesen, wenn agents.yaml es freigibt (`darf_ereignisse_lesen`) —
            # sonst käme ein Agent, den der Mensch bittet, bei einem anderen
            # nachzusehen, nur per `docker exec` an dessen Zeitleiste.
            if identity is not None and agent and agent != identity:
                if not _darf_ereignisse_lesen(identity, agent):
                    return {"error": f"Dieser Kanal ist an '{identity}' gebunden — das "
                                     f"Ereignis-Log von '{agent}' ist nicht freigegeben "
                                     f"(agents.yaml: darf_ereignisse_lesen)."}
                wer = agent
            else:
                try:
                    wer = ident(agent, "agent")
                except ScopeError as exc:
                    return {"error": str(exc)}
            unbekannt = _pruefe_empfaenger(wer)
            if unbekannt:
                return unbekannt
            arten = [a.strip() for a in (art or "").split(",") if a.strip()] or None
            _log(kanal, "agent_events", agent=wer, art=art, limit=limit)
            try:
                daten = ereignisse.lies(MAILBOX_ROOT, wer, max(1, min(int(limit), 200)),
                                        None, seit, arten, bool(nur_probleme))
            except ValueError as exc:
                return {"error": str(exc)}
            return {"agent": wer, **daten}

    if on("send_task"):
        @werkzeug
        def send_task(
            to: str,
            instruction: str,
            sender: str | None = None,
            project: str | None = None,
            files: list[str] | None = None,
            rolle: str | None = None,
            nicht_vor: str | None = None,
            thread: str | None = None,
            timeout: int | None = None,
            model: str | None = None,
        ) -> dict:
            """Einen Arbeitsauftrag in die Inbox eines Agenten legen.

            `sender` ist, wer delegiert (z.B. ein Koordinator-Agent) — an ihn geht
            nach Abschluss das Ergebnis als kind="response" in die Inbox zurück,
            also IMMER den eigenen Namen angeben (auf einem gebundenen Kanal ist
            er automatisch dein Name; weglassen genügt). Nur diese task-Envelopes
            führt der Watcher auf der Agent-Seite tatsächlich aus.

            `rolle` (optional) gibt dem Lauf eine definierte Rolle (z.B.
            "review"): ihr Prompt wird an den Lauf angehängt, ihre Rechte können
            die des Agenten nur EINSCHRÄNKEN, nie erweitern. Verfügbare Rollen
            liefert list_rollen(); eine unbekannte Rolle ist ein Fehler.

            `project` (optional) ist ein VERZEICHNISNAME unterhalb des
            Arbeitsverzeichnisses des Empfängers (z.B. "backend_stack") — kein
            Thema und kein Titel. Kennst du die Verzeichnisse dort nicht, lass
            es weg; ein Thema gehört in `thread`.

            `nicht_vor` (optional, ISO-8601 in lokaler Serverzeit, z.B.
            "2026-09-02T22:00") plant den Task: er bleibt in der Inbox liegen
            und wird erst ab diesem Zeitpunkt ausgeführt.

            `thread` (optional) benennt den VORGANG, zu dem der Auftrag gehört
            (z.B. "startseite-aufgaben"): arbeitet der Empfänger im
            Automatikmodus, setzen alle Aufträge mit demselben thread dieselbe
            Claude-Sitzung fort — eine Nachbesserung kennt dann die Vorarbeit,
            statt bei null zu beginnen. Für Folge-Aufträge IMMER denselben
            thread wiederverwenden.

            `timeout` (optional, Sekunden) begrenzt die Laufzeit dieses Auftrags
            im Automatikmodus; er kann die Grenze des Empfängers nur senken.

            `model` (optional) bestellt für diesen Auftrag ein bestimmtes
            Modell (z.B. "sonnet" für Doku/Nachträge). Geht nur mit Modellen,
            die der Empfänger freigibt (agents.yaml `modelle_erlaubt`); meist
            weglassen — Rolle und Agent bringen ihr Modell selbst mit.
            """
            try:
                absender = ident(sender, "sender") if identity else (sender or "orchestrator")
            except ScopeError as exc:
                return {"error": str(exc)}
            unbekannt = _pruefe_absender(identity, absender)
            if unbekannt:
                return unbekannt
            unbekannt = _pruefe_empfaenger(to)
            if unbekannt:
                return unbekannt
            # Rolle SERVERSEITIG auflösen und in den Envelope einfrieren — beide
            # Watcher-Transporte lesen dann dieselben Felder, und eine später
            # editierte Rollen-Datei ändert keinen schon eingereihten Task.
            rollen_felder: dict = {}
            if rolle:
                try:
                    rollen_felder = rollen.rolle_fuer_task(rolle)
                except rollen.RollenFehler as exc:
                    return {"error": str(exc)}
            if nicht_vor:
                try:
                    ziel = datetime.fromisoformat(str(nicht_vor).replace("Z", "+00:00"))
                except ValueError:
                    return {"error": f"nicht_vor muss ISO-8601 sein "
                                     f"(z.B. 2026-09-02T22:00), nicht {nicht_vor!r}"}
                # Review P1-5: den Zeitpunkt HIER tz-behaftet einfrieren
                # (naiv = lokale SERVERzeit, TZ steht in docker-compose) —
                # sonst deutete jeder Leser ihn in seiner eigenen Zone: ein
                # Datei-Transport-Agent auf UTC startete den 22-Uhr-Task
                # um Mitternacht, das Browser-Badge zeigte Browser-Zeit.
                if ziel.tzinfo is None:
                    ziel = ziel.astimezone()
                nicht_vor = ziel.isoformat(timespec="seconds")
            # thread wird zum Schlüssel im Sitzungsbuch des Watchers — auf ein
            # harmloses Kürzel bringen statt den Auftrag daran scheitern zu lassen.
            faden = "-".join("".join(
                c if (c.isalnum() and c.isascii()) or c in "_.-" else " "
                for c in str(thread or "")).split())[:64] or None
            try:
                frist = int(timeout) if timeout else None
            except (TypeError, ValueError):
                return {"error": f"timeout muss eine Zahl (Sekunden) sein, nicht {timeout!r}"}
            if frist is not None and frist < 60:
                return {"error": "timeout unter 60 s ergibt keinen sinnvollen Lauf"}
            # Vorgang vererben (Issue #58): entsteht der Auftrag AUS einem
            # laufenden Task heraus (gebundener Kanal, genau ein Task in
            # Arbeit), gehört er zu dessen Vorgang — ohne das reißt die Kette,
            # sobald ein Agent vergisst, den thread von Hand zu setzen.
            parent_id = None
            vorgang = faden
            if identity is not None:
                try:
                    laufend = [e for _p, e in _lese_ordner(
                        Mailbox(MAILBOX_ROOT, identity).processing)
                        if e.get("kind", "task") == "task"
                        and e.get("status") != "needs_confirm"]
                except (ValueError, OSError):
                    laufend = []
                if len(laufend) == 1:
                    parent_id = laufend[0].get("id") or laufend[0].get("task_id")
                    if not faden:
                        faden = laufend[0].get("thread") or None
                        vorgang = vorgaenge.schluessel_fuer(laufend[0], parent_id)
            modell = str(model or "").strip() or None
            if modell:
                erlaubt = _modelle_erlaubt(to)
                if not rollen.MODEL_RE.fullmatch(modell) or modell not in erlaubt:
                    return {"error": f"model {modell!r} ist für {to} nicht freigegeben — "
                                     f"erlaubt: {', '.join(erlaubt) or '(keine Modellwahl je Task)'}"}
            _log(kanal, "send_task", to=to, sender=absender, rolle=rolle,
                 nicht_vor=nicht_vor, thread=faden, timeout=frist,
                 zeichen=len(instruction))
            task = Task(
                task_id=new_id("task"),
                agent=to,
                instruction=instruction,
                project=project,
                files=files or [],
                sender=absender,
                nicht_vor=nicht_vor,
                thread=faden,
                timeout=frist,
                model=modell,
                vorgang=vorgang,
                parent_task_id=parent_id,
                **rollen_felder,
            )
            Mailbox(MAILBOX_ROOT, to).put_task(task)
            return {"id": task.task_id, "to": to, "status": "pending",
                    **({"rolle": rolle} if rolle else {}),
                    **({"nicht_vor": nicht_vor} if nicht_vor else {}),
                    **({"thread": faden} if faden else {}),
                    **({"model": modell} if modell else {})}

    if on("list_rollen"):
        @werkzeug
        def list_rollen() -> list[dict]:
            """Verfügbare Rollen für send_task(rolle=…) — Name, Beschreibung
            und ob sie Rechte einschränkt. Rollen pflegt der Mensch im
            Dashboard (Agenten-Panel → Rollen)."""
            _log(kanal, "list_rollen")
            return rollen.liste_rollen()

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
        def read_responses(worker: str, for_sender: str | None = None, limit: int = 20,
                           archiv: bool = False) -> list[dict]:
            """Rückmeldungen aus der Outbox eines BEARBEITERS lesen (Archiv erledigter Tasks).

            `worker` ist der Agent, der für dich gearbeitet hat — NICHT dein eigener
            Name (anders als bei claim_task/complete_task, wo `agent` = du selbst).
            Die eigene Outbox enthält nur die eigenen Antworten an andere.
            Meist unnötig: Ergebnisse landen beim Abschließen zusätzlich als
            kind="response" in der Inbox des Auftraggebers — `inbox()` genügt.
            `for_sender` filtert die Outbox auf Antworten an einen bestimmten
            Auftraggeber; auf einem gebundenen Kanal ist das immer dein Name.
            `limit` begrenzt auf die neuesten Einträge (die Outbox ist ein Archiv).
            `archiv=True` nimmt das Monatsarchiv dazu — Antworten, die älter
            als die Aufbewahrungsfrist sind (dann mit `archiviert: true`).
            """
            if identity is not None:
                try:
                    for_sender = resolve_ident(identity, for_sender, "for_sender")
                except ScopeError as exc:
                    return [{"error": str(exc)}]
            _log(kanal, "read_responses", worker=worker, for_sender=for_sender)
            # P1-7: auch das LESETOOL legte über Mailbox(worker) ein
            # Verzeichnis an — ein Tippfehler zementierte sich in list_agents.
            unbekannt = _pruefe_empfaenger(worker)
            if unbekannt:
                return [unbekannt]
            out = Mailbox(MAILBOX_ROOT, worker).read_responses(mit_archiv=bool(archiv))
            if for_sender:
                out = [r for r in out if r.get("to") == for_sender]
            return out[: max(1, limit)]

    if on("claim_task"):
        @werkzeug
        def claim_task(task_id: str, agent: str | None = None, erneut: bool = False,
                       watcher: bool = False) -> dict:
            """Einen Task aus der eigenen Inbox annehmen, BEVOR du daran arbeitest.

            Markiert ihn als "in Arbeit" (inbox → .processing) — im Dashboard sichtbar,
            und kein Watcher greift ihn doppelt. `agent` = du selbst (der Bearbeiter;
            auf einem gebundenen Kanal weglassen). Danach: Aufgabe erledigen und mit
            complete_task abschließen. Ein bereits laufender Task wird NICHT erneut
            vergeben; arbeitest du selbst daran und brauchst den Auftragstext noch
            einmal, dann `erneut=True`. `watcher` setzt nur der Automatik-Watcher.
            """
            try:
                wer = ident(agent, "agent")
            except ScopeError as exc:
                return {"error": str(exc)}
            _log(kanal, "claim_task", agent=wer, task=task_id)
            # Kostendeckel (Issue #55): bei erreichter Tagesgrenze nimmt die
            # AUTOMATIK nichts Neues an (Fortsetzen eines schon laufenden
            # Tasks und der Handbetrieb bleiben frei).
            if watcher and not erneut:
                sperre = kosten.gesperrt(MAILBOX_ROOT, wer)
                if sperre:
                    return {"error": "Tageskosten-Grenze erreicht — die Automatik wartet "
                                     "auf die Freigabe des Menschen.",
                            "kosten_gesperrt": True}
            try:
                # Issue #46: nur gekennzeichnete Watcher-Ansprüche zählen für
                # die Aufgabe-Grenze der Pflege; alles andere ist eine Sitzung.
                env = Mailbox(MAILBOX_ROOT, wer).claim_task(
                    task_id, erneut=erneut,
                    von=CLAIM_WATCHER if watcher else CLAIM_SITZUNG)
            except AlreadyClaimed as exc:
                return {
                    "error": str(exc) + ". Falls du selbst daran arbeitest: "
                    "claim_task(..., erneut=True).",
                    "already_claimed": True,
                }
            except ZuFrueh as exc:
                # Geplanter Task (St.2): liegt absichtlich noch in der Inbox.
                return {"error": str(exc), "zu_frueh": True,
                        "nicht_vor": exc.nicht_vor}
            except ValueError as exc:
                # ungültige Task-ID (P0-1) o.ä. — Fehler-Dict statt Tool-Crash
                return {"error": str(exc)}
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
                # Auftraggeber + Zahl schon beantworteter Rückfragen (Issue
                # #54): der Watcher fragt bei unklarem project-Verzeichnis
                # nach — aber nur einmal.
                "sender": env.get("sender"),
                "nachtraege": len(env.get("nachtraege") or []),
                # Vorgang + Laufzeit-Deckel (Issues #37/#38) — der Watcher
                # wählt daraus seine Sitzung bzw. senkt seinen Timeout.
                "thread": env.get("thread"),
                "timeout": env.get("timeout"),
                # Rollen-Felder (Dashboard-Paket St.1): der Watcher rechnet
                # daraus die Schnittmenge mit seinen Agenten-Rechten und hängt
                # den Rollen-Prompt an den Lauf. Der inbox()-Weg liefert sie
                # NICHT (normalize_envelope trägt nur den Namen) — dieser
                # claim ist die verbindliche Quelle.
                "rolle": env.get("rolle"),
                "rollen_prompt": env.get("rollen_prompt"),
                "rollen_permission_mode": env.get("rollen_permission_mode"),
                "rollen_tools": env.get("rollen_tools"),
                # Modellwahl (Issue #62) + Weckruf-Kennzeichen (Nachlauf-Modell)
                "rollen_model": env.get("rollen_model"),
                "model": env.get("model"),
                "weckruf": bool(env.get("weckruf")),
            }

    if on("complete_task"):
        @werkzeug
        def complete_task(
            task_id: str,
            result: str,
            status: str = "done",
            log: str = "",
            agent: str | None = None,
            verbrauch: dict | None = None,
            lauf: dict | None = None,
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
            try:
                offen = box.task_offen(task_id)
            except ValueError as exc:
                # ungültige Task-ID (P0-1): Fehler-Dict statt Tool-Crash —
                # und der Ausbruchsversuch steht damit auch im Kanal-Log.
                return {"error": str(exc)}
            # Wiederholter Abschluss ist KEIN Fehler: geht die Antwort auf dem
            # Rückweg verloren (Tunnel-Reconnect), liefert der Watcher dasselbe
            # Ergebnis erneut ab. Der Task ist dann schon abgeräumt — das als
            # Erfolg melden, statt ihn 5 Retrys lang gegen eine Wand laufen zu
            # lassen und am Ende fälschlich "nicht abgeliefert" zu loggen.
            # Issue #43: schließt das Claude-Kind seinen eigenen Task selbst ab,
            # kommt der Watcher mit lauf/verbrauch/log als Zweiter — die
            # fehlenden Felder werden dann nachgetragen statt verworfen.
            # Issue #46: hat die PFLEGE den Task aufgegeben, während der
            # Bearbeiter in Wahrheit noch daran saß, ist dies das echte
            # Ergebnis — es ersetzt die Abbruch-Antwort und wird neu zugestellt,
            # statt in `already: true` zu verschwinden.
            nach_aufgabe = False
            if not offen and box.nach_aufgabe_oeffnen(task_id):
                offen = nach_aufgabe = True
                _log(kanal, "complete_task", agent=wer, task=task_id, status="nach-aufgabe")
            if not offen:
                if (box.outbox / f"{task_id}-response.json").exists():
                    ergaenzt = box.response_ergaenzen(
                        task_id, log,
                        verbrauch if isinstance(verbrauch, dict) else None,
                        lauf if isinstance(lauf, dict) else None)
                    _log(kanal, "complete_task", agent=wer, task=task_id, status="bereits",
                         ergaenzt=",".join(ergaenzt) or "-")
                    antwort = {"task_id": task_id, "agent": wer, "status": status,
                               "already": True}
                    if ergaenzt:
                        antwort["ergaenzt"] = ergaenzt
                    return antwort
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
            # `verbrauch` (St.3): usage/Kosten des Laufs — der Watcher liefert
            # sie mit, der Zähler aggregiert sie aus der Outbox. Optional.
            # `lauf` (Issues #37–#39): Sitzung, Timeout-Grund, verweigerte
            # Aufrufe — liefert nur der Watcher; ein Modell lässt es weg.
            # Issue #64: bekannte Geheimniswerte des Agenten nicht im Klartext
            # in Antwort und Log ablegen (der Watcher schwärzt selbst; das hier
            # fängt den Eigenabschluss des Laufs und interaktive Sitzungen).
            bekannte = geheimnisse.werte(wer)
            if bekannte:
                result = geheimnisse.schwaerze_mit(result, bekannte)
                log = geheimnisse.schwaerze_mit(log, bekannte)
            box.write_response(task_id, result, status, log,
                               verbrauch if isinstance(verbrauch, dict) else None,
                               lauf if isinstance(lauf, dict) else None)
            antwort = {"task_id": task_id, "agent": wer, "status": status}
            if nach_aufgabe:
                antwort["ersetzt_aufgabe"] = True
            return antwort

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
            unbekannt = _pruefe_absender(identity, absender) or _pruefe_empfaenger(to)
            if unbekannt:
                return unbekannt
            _log(kanal, "send_message", to=to, sender=absender, zeichen=len(text))
            return _weck_hinweis(Mailbox(MAILBOX_ROOT, to).post(
                {"kind": "message", "sender": absender, "to": to, "text": text}
            ), to, "message")

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
            unbekannt = _pruefe_absender(identity, absender) or _pruefe_empfaenger(to)
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
            return _weck_hinweis(env, to, "question")

    if on("answer"):
        @werkzeug
        def answer(to: str, text: str, sender: str | None = None, reply_to: str | None = None) -> dict:
            """Eine Rückfrage beantworten. `reply_to` = id der beantworteten question.

            `sender` = dein Name (auf einem gebundenen Kanal weglassen)."""
            try:
                absender = ident(sender, "sender") if identity else (sender or "orchestrator")
            except ScopeError as exc:
                return {"error": str(exc)}
            unbekannt = _pruefe_absender(identity, absender)
            if unbekannt:
                return unbekannt
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
            gefunden = [normalize_envelope(e)
                        for e in Mailbox(MAILBOX_ROOT, wer).read_inbox(kind)]
            # Der leere Watcher-Poll (kind="task", nichts da) bleibt still
            # (Issue #41) — alles andere steht wie bisher im Kanal-Log.
            if not ist_watcher_poll("inbox", {"kind": kind}, gefunden):
                _log(kanal, "inbox", agent=wer, kind=kind, anzahl=len(gefunden))
            return gefunden

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

    # --- Dateiaustausch zwischen Maschinen ----------------------------------

    if on("send_file"):
        @werkzeug(pool=_AUSTAUSCH_POOL)
        def send_file(
            to: str,
            paths: list[str] | str,
            note: str | None = None,
            source: str | None = None,
        ) -> dict:
            """Datei(en) von deiner Maschine in den Austausch-Ordner einer anderen legen.

            Das Dashboard kopiert per SFTP direkt von Platte zu Platte — der
            Inhalt läuft NICHT durch dich, also auch für Binäres und Großes
            (PDF, Export, Archiv). Nimm das statt Dateiinhalte in Nachrichten
            zu kopieren.

            `to` = Empfänger-Maschine; sie braucht einen eingeschalteten
            Austausch-Ordner (sonst nennt der Fehler die möglichen Empfänger).
            `paths` = Dateien auf DEINER Maschine, am besten absolute Pfade
            (relativ und `~/` gelten ab dem Home). Nur einzelne Dateien — einen
            Ordner vorher packen (zip/tar). `note` = optionaler Begleittext.
            `source` auf einem gebundenen Kanal weglassen; nur der freie Kanal
            muss sagen, von welcher Maschine gelesen wird.

            Die Dateien landen unter <Austausch-Ordner>/von-<deine Maschine>/,
            Vorhandenes wird nie überschrieben (-2, -3 …). Der Empfänger bekommt
            automatisch eine Nachricht mit den Zielpfaden — kein zusätzliches
            send_message nötig. Rückgabe: `zugestellt` (mit Zielpfad) und
            `fehler` je Datei.
            """
            try:
                quelle = ident(source, "source")
            except ScopeError as exc:
                return {"error": str(exc)}
            liste = [paths] if isinstance(paths, str) else list(paths or [])
            _log(kanal, "send_file", to=to, source=quelle, dateien=len(liste))
            try:
                ereignisse.schreibe(MAILBOX_ROOT, quelle, "send_file",
                                    f"{len(liste)} Datei(en) an {to}", an=to, dateien=len(liste))
            except ValueError:
                pass
            try:
                return austausch.uebergib_sync(
                    quelle, liste, to, melder=identity or "orchestrator", nachricht=note
                )
            except austausch.AustauschError as exc:
                return {"error": str(exc)}

    # --- Deploy-Pakete (Issue #61) -------------------------------------------

    if on("deploy_status"):
        @werkzeug
        def deploy_status(
            token: str | None = None,
            phase: str | None = None,
            title: str | None = None,
            target: str | None = None,
            repos: list | None = None,
            flags: list[str] | str | None = None,
            tasks: list[str] | str | None = None,
            threads: list[str] | str | None = None,
            note: str | None = None,
            sender: str | None = None,
        ) -> dict | list:
            """Stand eines Deploy-/Freigabe-Pakets melden — das Dashboard zeigt
            ihn im Deploy-Panel (es deployt NICHT selbst).

            Ohne `token`: die offenen Pakete auflisten (was steht an, was
            blockiert). Mit `token` + `phase` melden:
            "ausgestellt" (Paket/Intent erstellt bzw. erneut ausgestellt),
            "uebernommen" (der Einspielende hat es angenommen), "gelaufen"
            (eingespielt), "live" (geprüft), "fehler", "zurueckgezogen".
            Beim Ausstellen mitgeben: `repos` = [{"name", "sha", "message"?,
            "vorhanden"?}] (erwarteter Stand je Repo), `flags` (z.B.
            ["MIGRATE", "IMAGE-NEUBAU"]), `tasks` (Task-IDs) und `threads`
            (Vorgänge), die in dem Paket stecken, `title`, `target` (wohin),
            `note`. Spätere Phasenmeldungen brauchen nur token + phase (+ note).
            `sender` auf einem gebundenen Kanal weglassen.
            """
            if not token:
                _log(kanal, "deploy_status", liste=True)
                return pakete.liste(nur_offene=True)
            try:
                absender = ident(sender, "sender") if identity else (sender or "orchestrator")
            except ScopeError as exc:
                return {"error": str(exc)}
            _log(kanal, "deploy_status", token=token, phase=phase, sender=absender)
            try:
                p = pakete.melde(token, phase or "", absender, title, target, repos, flags,
                                 tasks, threads, note)
            except pakete.PaketFehler as exc:
                return {"error": str(exc)}
            schwere = "fehler" if p["phase"] == "fehler" else "info"
            try:
                ereignisse.schreibe(MAILBOX_ROOT, absender, "paket",
                                    f"Paket {pakete.kurztext(p)} — {p['phase']}"
                                    + (f" · {note}" if note else ""),
                                    schwere=schwere, token=p["token"], phase=p["phase"])
            except ValueError:
                pass
            # Der Mensch erfährt Ausgang und Fehler aktiv (Panel „Nachrichten" + Push).
            if p["phase"] in ("gelaufen", "live", "fehler") and absender != "orchestrator":
                wort = {"gelaufen": "eingespielt", "live": "live, geprüft",
                        "fehler": "FEHLGESCHLAGEN"}[p["phase"]]
                try:
                    Mailbox(MAILBOX_ROOT, "orchestrator").post({
                        "kind": "message", "sender": absender, "weckt": False,
                        "text": f"📦 Paket {pakete.kurztext(p)} — {wort}"
                                + (f"\n{note}" if note else "")})
                except (ValueError, OSError):
                    pass
            return {"token": p["token"], "phase": p["phase"],
                    "ausstellungen": p["ausstellungen"], "kurz": pakete.kurztext(p)}

    # --- Vorgänge (Issue #58) ------------------------------------------------

    if on("thread_status"):
        @werkzeug
        def thread_status(thread: str | None = None, days: int = 14) -> dict | list:
            """Stand eines Vorgangs über alle Agenten — oder ohne `thread` die
            Liste der Vorgänge der letzten `days` Tage (Status, beteiligte
            Agenten, Kosten, letzte Aktivität, Notiz)."""
            _log(kanal, "thread_status", thread=thread)
            try:
                if thread:
                    return vorgaenge.zeitleiste(MAILBOX_ROOT, thread)
                return vorgaenge.liste(MAILBOX_ROOT, float(max(1, min(int(days), 90))))
            except (vorgaenge.VorgangFehler, ValueError) as exc:
                return {"error": str(exc)}

    if on("thread_note"):
        @werkzeug
        def thread_note(thread: str, note: str) -> dict:
            """Kurze Status-Notiz an einen Vorgang heften (z.B. „wartet auf
            Abend-Deploy DV") — sichtbar in der Vorgangs-Ansicht des
            Dashboards. Leerer Text entfernt die Notiz."""
            _log(kanal, "thread_note", thread=thread)
            try:
                return {"thread": thread, **vorgaenge.setze_notiz(
                    MAILBOX_ROOT, thread, note, identity or "orchestrator")}
            except vorgaenge.VorgangFehler as exc:
                return {"error": str(exc)}

    # --- Erinnerungs-Postfach (Issue #52) ------------------------------------

    if on("remind"):
        @werkzeug
        def remind(when: str, text: str, project: str | None = None,
                   als: str | None = None, agent: str | None = None) -> dict:
            """Dir selbst eine Wiedervorlage setzen: zum Termin landet `text`
            wieder in deiner Inbox — ohne dass ein Mensch daran denken muss.

            `when` = ISO-Datum oder Datum mit Uhrzeit in Serverzeit
            ("2026-11-26" = 08:00 Uhr, oder "2026-11-26T14:30"). `text` so
            schreiben, dass du ihn OHNE heutigen Kontext verstehst: was prüfen,
            warum, worauf achten, welcher Vorgang/welches Ticket. `als` =
            "task" (wird abgearbeitet) oder "message" (nur Hinweis); ohne
            Angabe: Task, wenn deine Automatik läuft, sonst Nachricht.
            `project` = Verzeichnis für den späteren Lauf. `agent` auf einem
            gebundenen Kanal weglassen. Rückgabe: die Erinnerung samt `id`.
            """
            try:
                wer = ident(agent, "agent")
            except ScopeError as exc:
                return {"error": str(exc)}
            unbekannt = _pruefe_empfaenger(wer)
            if unbekannt:
                return unbekannt
            _log(kanal, "remind", agent=wer, when=when)
            try:
                return erinnerungen.lege_an(MAILBOX_ROOT, wer, when, text, project, als,
                                            von=identity or "orchestrator")
            except (erinnerungen.ErinnerungFehler, ValueError) as exc:
                return {"error": str(exc)}

    if on("reminders"):
        @werkzeug
        def reminders(include_done: bool = False, agent: str | None = None) -> list[dict]:
            """Deine offenen Erinnerungen (mit `include_done` auch zugestellte
            und gelöschte der letzten 30 Tage), nach Fälligkeit sortiert."""
            try:
                wer = ident(agent, "agent")
            except ScopeError as exc:
                return [{"error": str(exc)}]
            _log(kanal, "reminders", agent=wer)
            try:
                return erinnerungen.liste(MAILBOX_ROOT, wer, bool(include_done))
            except ValueError as exc:
                return [{"error": str(exc)}]

    if on("cancel_reminder"):
        @werkzeug
        def cancel_reminder(reminder_id: str, agent: str | None = None) -> dict:
            """Eine offene Erinnerung löschen (id aus remind/reminders)."""
            try:
                wer = ident(agent, "agent")
            except ScopeError as exc:
                return {"error": str(exc)}
            _log(kanal, "cancel_reminder", agent=wer, id=reminder_id)
            try:
                return erinnerungen.loesche(MAILBOX_ROOT, wer, reminder_id)
            except (erinnerungen.ErinnerungFehler, ValueError) as exc:
                return {"error": str(exc)}

    if on("snooze_reminder"):
        @werkzeug
        def snooze_reminder(reminder_id: str, when: str, agent: str | None = None) -> dict:
            """Eine offene Erinnerung auf einen neuen Termin verschieben."""
            try:
                wer = ident(agent, "agent")
            except ScopeError as exc:
                return {"error": str(exc)}
            _log(kanal, "snooze_reminder", agent=wer, id=reminder_id, when=when)
            try:
                return erinnerungen.verschiebe(MAILBOX_ROOT, wer, reminder_id, when)
            except (erinnerungen.ErinnerungFehler, ValueError) as exc:
                return {"error": str(exc)}

    # --- Freigaben der Automatik (Issue #56) ---------------------------------
    # Nur auf GEBUNDENEN Kanälen: das Tool ruft Claude Code selbst auf
    # (--permission-prompt-tool), die Identität kommt aus dem Kanal. Es wird
    # immer registriert — eine Tool-Allowlist des Agenten soll die Automatik
    # nicht an einem fehlenden Freigabe-Tool scheitern lassen.
    if identity is not None:
        @werkzeug(pool=_FREIGABE_POOL)
        def freigabe(tool_name: str, input: dict | None = None,  # noqa: A002
                     tool_use_id: str | None = None) -> str:
            """Berechtigungs-Rückfrage eines Automatik-Laufs an den Menschen.

            NICHT von Hand aufrufen: Claude Code ruft dieses Tool selbst, wenn
            ein Werkzeug-Aufruf eine Freigabe braucht (--permission-prompt-tool).
            Wartet auf die Entscheidung im Dashboard (Banner/Push) und liefert
            {"behavior": "allow"|"deny", …} als JSON-Text.
            """
            _log(kanal, "freigabe", agent=identity, werkzeug=tool_name)
            if not freigaben.einstellung(identity)["an"]:
                # nicht eingeschaltet → wie bisher ablehnen, ohne den Menschen
                # mit Anfragen zu behelligen
                return json.dumps({"behavior": "deny", "message":
                                   "Freigaben über das Dashboard sind für diesen Agenten "
                                   "nicht eingeschaltet."}, ensure_ascii=False)
            antwort = freigaben.pruefe(
                MAILBOX_ROOT, identity, tool_name, input or {}, tool_use_id,
                abbruch=lambda: bool(load_settings().get("automatik_notaus")))
            return json.dumps(antwort, ensure_ascii=False)

    if on("fetch_file"):
        @werkzeug
        def fetch_file(file_id: str, agent: str | None = None) -> dict:
            """Eine für dich bereitliegende Datei abholen (Issue #57).

            Für Maschinen, die das Dashboard nicht per SFTP erreicht: die
            Nachricht „… liegen für dich zum Abholen bereit" nennt je Datei
            eine id. Dieses Tool liefert dazu eine Download-URL — lade sie mit
            `curl` (bzw. `Invoke-WebRequest`) DIREKT auf die Platte; der Inhalt
            läuft nicht durch dich. `agent` auf einem gebundenen Kanal weglassen.
            Ohne `url` im Ergebnis: `pfad` an Schema+Host deiner MCP-URL hängen.
            """
            try:
                wer = ident(agent, "agent")
            except ScopeError as exc:
                return {"error": str(exc)}
            _log(kanal, "fetch_file", agent=wer, datei=file_id)
            try:
                meta = zwischenspeicher.lies(file_id)
            except zwischenspeicher.SpeicherFehler as exc:
                return {"error": str(exc)}
            if meta.get("an") != wer or meta.get("status") != "bereit":
                return {"error": f"Datei {file_id} liegt nicht für {wer} bereit."}
            wo = zwischenspeicher.url(meta)
            ziel = wo.get("url") or "<Schema+Host deiner MCP-URL>" + wo["pfad"]
            return {
                "id": file_id, "name": meta["name"], "bytes": meta["bytes"],
                "von": meta.get("von"), **wo,
                "befehl": f'curl -fsS -o "{meta["name"]}" "{ziel}"',
                "befehl_windows": f'Invoke-WebRequest -Uri "{ziel}" -OutFile "{meta["name"]}"',
                "hinweis": "Bei eigener (interner) CA: curl --cacert <ca.crt> bzw. -k.",
            }

    if on("upload_url"):
        @werkzeug
        def upload_url(to: str, name: str, note: str | None = None,
                       sender: str | None = None) -> dict:
            """Eine Datei von DEINER Maschine an eine andere schicken, wenn das
            Dashboard deine Maschine nicht per SFTP erreicht (Issue #57).

            Liefert eine einmalige Upload-URL: lade die Datei per HTTP PUT hoch
            (`curl -T datei "<url>"`) — der Inhalt läuft nicht durch dich. Das
            Dashboard legt sie danach in den Austausch-Ordner von `to` (bzw.
            zum Abholen bereit, wenn `to` selbst keine SSH-Maschine ist) und
            benachrichtigt den Empfänger. `name` = Dateiname beim Empfänger,
            `note` = optionaler Begleittext. `sender` auf einem gebundenen
            Kanal weglassen.
            """
            try:
                absender = ident(sender, "sender")
            except ScopeError as exc:
                return {"error": str(exc)}
            unbekannt = _pruefe_empfaenger(to)
            if unbekannt:
                return unbekannt
            if to == absender:
                return {"error": "Absender und Empfänger sind dieselbe Maschine."}
            try:
                dateiname = austausch.sicherer_name(name)
            except austausch.AustauschError as exc:
                return {"error": str(exc)}
            if not austausch.ist_token_maschine(to) and to not in austausch.ziele():
                moeglich = ", ".join(sorted(austausch.ziele())) or "(keine)"
                return {"error": f"{to} hat keinen Austausch-Ordner — einschalten im "
                                 f"Datei-Panel (📥). Mögliche Empfänger: {moeglich}"}
            _log(kanal, "upload_url", to=to, sender=absender, name=dateiname)
            meta = zwischenspeicher.lege_an(absender, absender, dateiname, status="erwartet",
                                            weiter_an=to, notiz=note)
            wo = zwischenspeicher.url(meta)
            ziel = wo.get("url") or "<Schema+Host deiner MCP-URL>" + wo["pfad"]
            return {
                "id": meta["id"], "methode": "PUT", **wo, "an": to, "name": dateiname,
                "befehl": f'curl -fsS -T "<lokale Datei>" "{ziel}"',
                "befehl_windows": (f'Invoke-WebRequest -Method Put -InFile "<lokale Datei>" '
                                   f'-Uri "{ziel}"'),
                "gueltig_stunden": zwischenspeicher.ERWARTET_STUNDEN,
            }

    # Zustellen aus dem Dashboard-Workspace (Issue #53) — nur der freie Kanal
    # (Orchestrator): gebundene Agenten haben keinen Zugriff auf die Uploads.
    if identity is None and on("deliver_upload"):
        @werkzeug(pool=_AUSTAUSCH_POOL)
        def deliver_upload(upload: str, to: str, dest_dir: str | None = None,
                           name: str | None = None, note: str | None = None,
                           keep: bool = False) -> dict:
            """Einen Chat-Anhang aus dem Dashboard an seinen Platz auf einer
            Maschine legen (Issue #53).

            `upload` = Workspace-Pfad des Anhangs (steht unter „Anhänge:" in
            der Nachricht, beginnt mit uploads/). `to` = Maschine. `dest_dir` =
            Zielordner RELATIV zum Arbeitsverzeichnis des Agenten dort (z.B.
            "strahler/Datenblaetter"); weglassen = Arbeitsverzeichnis selbst.
            `name` = anderer Dateiname am Ziel. Per SFTP direkt von Platte zu
            Platte, Vorhandenes wird nie überschrieben (-2, -3 …). Ist das Ziel
            unklar: NACHFRAGEN statt raten. Nach dem Zustellen wird der Anhang
            im Dashboard gelöscht (`keep=True` behält ihn, z.B. für eine zweite
            Maschine). Rückgabe: `zugestellt` mit dem endgültigen Pfad —
            nenne ihn dem Nutzer. Token-Maschinen bekommen die Datei zum
            Abholen (fetch_file).
            """
            unbekannt = _pruefe_empfaenger(to)
            if unbekannt:
                return unbekannt
            try:
                quelle = _safe(WORKSPACE / "uploads", str(upload).removeprefix("uploads/"))
            except Exception as exc:  # noqa: BLE001
                return {"error": f"ungültiger Upload-Pfad: {exc}"}
            if not quelle.is_file():
                return {"error": f"Anhang nicht gefunden: {upload}"}
            _log(kanal, "deliver_upload", to=to, upload=str(upload), ziel=dest_dir)
            try:
                ergebnis = austausch.stelle_zu_sync(
                    quelle, to, dest_dir, name or quelle.name, nachricht=note)
            except austausch.AustauschError as exc:
                return {"error": str(exc)}
            if ergebnis.get("zugestellt"):
                z = ergebnis["zugestellt"][0]
                try:
                    ereignisse.schreibe(
                        MAILBOX_ROOT, to, "send_file",
                        f"Anhang aus dem Chat abgelegt: {z.get('pfad') or z.get('abholen')}",
                        datei=z.get("name"), bytes=z.get("bytes"))
                except ValueError:
                    pass
                if not keep:
                    try:
                        quelle.unlink()
                    except OSError:
                        pass
            return ergebnis

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
            wer_ruft = identity or "orchestrator"
            try:
                ergebnis = integrations.call_integration(name, method, path, body)
            except integrations.IntegrationError as exc:
                # Serie von Timeouts (Issue #40): einmal den Menschen rufen —
                # als Nachricht in die Orchestrator-Inbox (= Panel + Push).
                warnung = integrations.timeout_warnung(name)
                ereignisse.schreibe(MAILBOX_ROOT, wer_ruft, "integration",
                                    f"{name} {method} {path}: {exc}",
                                    schwere="fehler" if warnung else "warnung",
                                    name=name, method=method, path=path,
                                    timeout_serie=bool(warnung) or None)
                if warnung:
                    _log(kanal, "call_integration", warnung="timeout-serie", name=name)
                    try:
                        Mailbox(MAILBOX_ROOT, "orchestrator").post({
                            "kind": "message", "sender": identity or "orchestrator",
                            "weckt": False, "text": warnung})
                    except (ValueError, OSError):
                        pass
                return {"error": str(exc)}
            ereignisse.schreibe(MAILBOX_ROOT, wer_ruft, "integration",
                                f"{name} {method} {path}", name=name, method=method, path=path,
                                status=ergebnis.get("status") if isinstance(ergebnis, dict) else None)
            return ergebnis


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
