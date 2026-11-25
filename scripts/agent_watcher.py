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

Hartes Beenden (Not-Aus): "kill" auf stdin → der laufende Claude-Lauf wird
SOFORT samt Kindprozessen abgeschossen (POSIX killpg, Windows taskkill /T) und
der Watcher endet. Ohne dieses Kommando würde ein bloßes Schließen der
SSH-Verbindung claude verwaist weiterlaufen lassen (kein PTY = kein SIGHUP).

Pro Agent läuft nur EIN Watcher je PC: eine Lock-Datei
(~/.agent-dashboard/<agent>.lock) verhindert, dass ein zweiter Start denselben
Task ein zweites Mal ausführt.

Gedächtnis zwischen Tasks: je Arbeitsverzeichnis + Rolle wird EINE claude-
Sitzung per --resume fortgesetzt (Buch: ~/.agent-dashboard/<agent>.sitzungen.json),
statt jeden Task frisch zu starten — siehe run_claude_sitzung. Aus: --no-resume.

Test ohne echtes Claude-Code:  --dry-run  (echoed die instruction zurück).

    python3 agent_watcher.py --agent frontend \
        --root /mnt/agent-dashboard/mailboxes --dry-run
    python3 agent_watcher.py --agent frontend \
        --mcp-url http://127.0.0.1:9000/mcp --mcp-hint
"""
from __future__ import annotations

import argparse
import atexit
import base64
import difflib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

# Sanft-Stopp-Signal: gesetzt durch "stop" auf stdin oder stdin-EOF.
STOP = threading.Event()
# Hart-Stopp-Signal (Not-Aus): gesetzt durch "kill" auf stdin. Bricht den
# laufenden Claude-Lauf ab, statt ihn fertig werden zu lassen.
HART = threading.Event()

# Der aktuell laufende claude-Prozess — der Not-Aus muss ihn von außen (aus dem
# stdin-Thread) samt Kindern beenden können.
_PROZESS_LOCK = threading.Lock()
_LAUFENDER: "subprocess.Popen | None" = None

# Ablieferung des Ergebnisses (M8): eigene Retry-Schleife, entkoppelt vom
# Poll-Loop — bis zu 30 min Claude-Arbeit dürfen nicht an einem Netz-Blip
# oder am gerade gesetzten Sanft-Stopp verloren gehen.
ABLIEFER_VERSUCHE = 5
ABLIEFER_PAUSE = 10.0

# Fehlschlag-Dämpfung (Issue #14): Scheitern mehrere Tasks unmittelbar
# hintereinander, ist die Umgebung kaputt (Binary weg, workdir weg, …) —
# dann anhalten statt die Warteschlange im Sekundentakt zu verbrauchen.
FEHLER_SCHWELLE = 3       # so viele schnelle Fehlschläge in Folge → Stopp
SCHNELL_SEKUNDEN = 20.0   # "schnell" = Lauf endete früher als das
_schnelle_fehler = 0

# Sitzung fortsetzen (21.09.2026): Bis dahin startete JEDER Task einen frischen
# `claude --print` ohne Gedächtnis — Startkontext, Projekt-Orientierung und bei
# Rückfragen sogar der komplette Lauf fielen je Task neu an. Jetzt führt der
# Watcher je Arbeitsverzeichnis + Rolle EINE Sitzung per `--resume` weiter, bis
# eine der Grenzen greift; dann beginnt eine neue. Abschaltbar je Agent
# (agents.yaml `resume: false` → --no-resume).
RESUME_MAX_PAUSE = 12 * 3600.0       # s seit dem letzten Lauf: ein Arbeitstag.
#   Länger als die Cache-Lebensdauer ist Absicht: eine kalte Fortsetzung kostet
#   etwa so viel wie Neustart + Neu-Orientierung, behält aber das Gedächtnis.
# Kontextgrenze (Issue #44): der feste Wert 150 000 war auf einer Bash-lastigen
# Box nach EINEM Task erreicht — das Gedächtnis griff praktisch nie, obwohl
# die aktuellen Modelle ein 1M-Fenster haben und Claude Code beim Fortsetzen
# selbst kompaktiert. Default 0 = relativ: 85 % des Fensters, das zum Modell
# der Sitzung gehört (init-Event); unbekanntes Modell = keine Kontextgrenze
# (die Kompaktierung übernimmt das Binary). Ein positiver Wert bleibt eine
# harte Grenze in Tokens (--resume-max-kontext / agents.yaml).
RESUME_MAX_KONTEXT = 0
KONTEXT_FENSTER_ANTEIL = 0.85
KONTEXT_FENSTER_1M = 1_000_000
KONTEXT_FENSTER_200K = 200_000
RESUME_MAX_TASKS = 25                # Deckel gegen endlos wachsende Sitzungen
RESUME_TASK_MERKDAUER = 7 * 86400.0  # so lange findet ein geparkter Task seine Sitzung
RESUME_TASK_MERKZAHL = 100
RESUME_SITZUNG_VERFALL = 30 * 86400.0  # claude räumt Transkripte nach ~30 Tagen ab
# Ein benannter Vorgang (`thread` im Task, Issue #37) darf länger ruhen: wer
# nach drei Tagen „Punkt d fehlt noch" schickt, meint denselben Faden.
RESUME_THREAD_MAX_PAUSE = 7 * 86400.0
THREAD_RE = re.compile(r"[^A-Za-z0-9_.-]+")
# Kosten beim Fortsetzen (Issue #55): nach langer Pause ist der Prompt-Cache
# verfallen — der GANZE Verlauf wird neu eingelesen und zum Schreibpreis
# gecacht (gemessen: 55 s Nachtrag = 7,10 $ bei 225k). Ist die Sitzung kalt
# UND groß, beginnt deshalb eine neue mit kompakter Übergabe (die Ergebnisse
# der letzten Läufe aus dem Sitzungsbuch). 0 schaltet die Regel jeweils ab.
RESUME_KALT_MINUTEN = 60.0
RESUME_KOMPAKT_AB = 200_000
UEBERGABE_LAEUFE = 3          # so viele Ergebnisse je Sitzungsschlüssel merken
UEBERGABE_ZEICHEN = 2500      # je Ergebnis

# Lebenszeichen an den Server, solange ein Lauf arbeitet (Issue #42): die
# Pflege reiht Tasks zurück, die zu lange OHNE Lebenszeichen in Arbeit liegen.
HERZSCHLAG = 600.0
# Leerlauf-Backoff des Pollens (Issue #41): 5 s → 30 s, nach Arbeit wieder kurz.
POLL_MAX = 30.0
KOSTEN_RUHE = 120.0   # s Pause, wenn der Server die Tagesgrenze meldet (#55)
SESSION_ID_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z_-]{7,63}$")
# Modellname für `claude --model` (Issue #62): Alias (sonnet, opus) oder volle
# ID. Eng gefasst, weil er auf der Kommandozeile landet (cmd.exe, P1-3).
MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
FREIGABE_TOOL_RE = re.compile(r"^mcp__[A-Za-z0-9_-]{1,64}__[A-Za-z0-9_-]{1,64}$")
# Laufzeit-Einstellungen, die nicht durch jede Signatur gereicht werden
# (gesetzt in main()): Freigabe-Tool und dessen Wartefrist (Issue #56).
LAUF_EINST: dict = {}


def modell_ok(name) -> "str | None":
    name = str(name or "").strip()
    return name if MODEL_RE.match(name) else None


def wirksames_modell(agent_model=None, rollen_model=None, task_model=None,
                     erlaubt=None, nachlauf_model=None,
                     ist_nachlauf: bool = False) -> "tuple[str | None, str]":
    """Modell eines Laufs (Issue #62) → (Modell oder None, Herkunft).

    Rangfolge: Task (nur aus der Allowlist des Agenten — sonst bestellte jeder
    Auftraggeber das teuerste Modell) > Rolle > Nachlauf-Modell (Fortsetzung
    nach Rückfrage / Weckruf, falls eingestellt) > Agent-Default > None
    (= was Claude Code auf dem PC selbst eingestellt hat)."""
    erlaubt = {m for m in (modell_ok(e) for e in (erlaubt or [])) if m}
    task_model = modell_ok(task_model)
    if task_model and task_model in erlaubt:
        return task_model, "task"
    if modell_ok(rollen_model):
        return modell_ok(rollen_model), "rolle"
    if ist_nachlauf and modell_ok(nachlauf_model):
        return modell_ok(nachlauf_model), "nachlauf"
    if modell_ok(agent_model):
        return modell_ok(agent_model), "agent"
    return None, ""

# Muss zur Allowlist in app/mailbox.py passen — sender wird in Pfade gejoint.
AGENT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


# Geheimnisse (Issue #64): vom Dashboard über stdin gereicht (`env <base64>`),
# gehen als Umgebungsvariablen an den claude-Prozess und werden in allem, was
# der Watcher zurückmeldet, durch ***NAME*** ersetzt.
GEHEIMNISSE: dict = {}
ENV_DA = threading.Event()


def setze_geheimnisse(zeile: str) -> bool:
    """`env <base64(JSON)>` vom Dashboard übernehmen. True = verstanden."""
    try:
        daten = json.loads(base64.b64decode(zeile.split(None, 1)[1]).decode("utf-8"))
    except Exception:  # noqa: BLE001 — kaputte Zeile: alte Werte behalten
        return False
    if not isinstance(daten, dict):
        return False
    neu = {str(k): str(v) for k, v in daten.items()
           if re.fullmatch(r"[A-Z][A-Z0-9_]{1,63}", str(k)) and "\x00" not in str(v)}
    GEHEIMNISSE.clear()
    GEHEIMNISSE.update(neu)
    ENV_DA.set()
    return True


def schwaerze(text):
    """Bekannte Geheimniswerte unkenntlich machen (längste zuerst)."""
    if not isinstance(text, str) or not text or not GEHEIMNISSE:
        return text
    for name, wert in sorted(list(GEHEIMNISSE.items()), key=lambda kv: -len(kv[1])):
        if len(wert) >= 6 and wert in text:
            text = text.replace(wert, f"***{name}***")
    return text


def sicher_print(text: str) -> None:
    """print, das eine tote stdout-Leitung überlebt.

    stdout ist die SSH-Session zum Dashboard. Wird sie geschlossen (Not-Aus,
    Netzabriss), wirft das nächste print BrokenPipeError — mitten in der
    Ausgabe-Schleife von run_claude würde das den Lauf verwaisen lassen."""
    try:
        print(schwaerze(text), flush=True)
    except (BrokenPipeError, OSError, ValueError):
        pass


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
    # Review N: nur zustellen, wenn die Mailbox des Auftraggebers existiert —
    # atomic_write_json legt sonst per mkdir eine Geister-Mailbox an, die
    # list_agents ab dann als Agenten zählt.
    if not (root / sender / "inbox").is_dir():
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


def eigener_task_hinweis(task_id: "str | None") -> str:
    """Issue #43: Der Watcher hat den Task schon beansprucht und schließt ihn
    nach dem Lauf selbst ab — mit session_id, Kontext, Kosten und Log. Ruft das
    Modell complete_task für DIESEN Task selbst auf, kommt der Watcher als
    Zweiter, und die Tool-Beschreibung von claim_task/complete_task legt genau
    das nahe. Deshalb steht es ausdrücklich im Kontext."""
    if not task_id:
        return ""
    return (
        f"Dein aktueller Auftrag ist der Task '{task_id}' — er ist bereits "
        f"beansprucht, und claim_task/complete_task dafür übernimmt der Watcher "
        f"nach deinem Lauf: rufe beide für DIESEN Task NICHT auf, dein "
        f"Abschlusstext ist die Antwort. (Für Tasks, die du selbst per "
        f"send_task delegierst oder aus deiner Inbox annimmst, gilt das nicht.) "
    )


def mcp_hint(agent: str, task_id: "str | None" = None) -> str:
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
        f"kind='response' in DEINE Inbox zurück. "
        + eigener_task_hinweis(task_id) +
        f"Prüfe zu Beginn deine Inbox und stelle Rückfragen per ask statt zu raten.\n\n"
    )


def frist_hinweis(timeout: "float | None", leerlauf: "float | None") -> str:
    """Issue #38: Der Watcher kann nichts in einen laufenden Lauf hineinrufen —
    also erfährt der Lauf seine Frist vorab und sichert früh."""
    deckel = float(timeout) if timeout and timeout > 0 else CLAUDE_TIMEOUT
    ruhe = CLAUDE_LEERLAUF if leerlauf is None else max(0.0, float(leerlauf))
    return (
        f"[Zeitrahmen] Dieser Lauf wird nach {deckel / 60:.0f} min Gesamtdauer hart "
        f"beendet" + (f", ebenso nach {ruhe / 60:.0f} min ohne jede Aktivität"
                      if ruhe else "")
        + ". Sichere Zwischenstände früh (z.B. Commit auf einen Branch), statt "
          "alles ans Ende zu legen.\n\n"
    )


def mcp_hint_kurz(agent: str, task_id: "str | None" = None) -> str:
    """Hinweis für einen Folge-Auftrag in einer FORTGESETZTEN Sitzung: die
    Regeln stehen dort schon im Verlauf. Vor allem entfällt die Pflichtrunde
    „prüfe zu Beginn deine Inbox" — sie kostete je Task eine Werkzeugrunde mit
    vollem Kontext."""
    return (
        f"[Kontext] Neuer Auftrag in derselben Sitzung — du bist weiter der "
        f"Agent '{agent}', die Dashboard-Regeln von oben gelten. "
        + eigener_task_hinweis(task_id) +
        f"Deine Inbox musst du nur erneut prüfen, wenn du auf eine Antwort "
        f"oder ein Ergebnis wartest.\n\n"
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


def projekt_workdir(basis: Path, projekt) -> tuple[Path | None, str | None]:
    """Arbeitsverzeichnis eines Tasks (Issue #19).

    Das `project`-Feld des Tasks wählt ein Unterverzeichnis der Watcher-Basis —
    so bedient EIN Agent mehrere Repos nebeneinander. Ohne `project` bleibt es
    bei der Basis wie bisher. Gibt (workdir, fehler) zurück; bei fehler soll
    der Task mit Klartext scheitern statt im falschen Verzeichnis zu laufen."""
    if projekt is None or not str(projekt).strip():
        return basis, None
    projekt = str(projekt).strip()
    ziel = (basis / projekt).resolve()
    basis_r = basis.resolve()
    if ziel != basis_r and basis_r not in ziel.parents:
        return None, (f"project {projekt!r} verlässt das Arbeitsverzeichnis "
                      f"{basis} — abgelehnt")
    if not ziel.is_dir():
        vorhanden = unterverzeichnisse(basis)
        return None, (f"project-Verzeichnis fehlt auf dem Agenten-PC: {ziel} "
                      f"(project {projekt!r} unterhalb von {basis})"
                      + (f" — vorhanden: {', '.join(vorhanden)}" if vorhanden else ""))
    return ziel, None


def unterverzeichnisse(basis: Path, grenze: int = 40) -> list[str]:
    """Sichtbare Unterverzeichnisse der Watcher-Basis (für Meldungen, #54)."""
    try:
        namen = sorted(p.name for p in basis.iterdir()
                       if p.is_dir() and not p.name.startswith("."))
    except OSError:
        return []
    return namen[:grenze]


def _projekt_norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


def aehnliche_projekte(basis: Path, projekt: str, anzahl: int = 5) -> list[str]:
    """Die ähnlichsten vorhandenen Verzeichnisnamen zu einem Freitext-`project`."""
    namen = unterverzeichnisse(basis, grenze=500)
    ziel = _projekt_norm(projekt)
    return sorted(
        namen, key=lambda n: -difflib.SequenceMatcher(None, ziel, _projekt_norm(n)).ratio()
    )[:anzahl]


def projekt_aufloesen(basis: Path, projekt, fallback: "str | None" = None,
                      schon_gefragt: bool = False) -> dict:
    """Arbeitsverzeichnis wählen, ohne an einem Tippfehler zu scheitern (#54).

    → {"dir", "fehler", "hinweis", "frage"}: Ausbruch bleibt ein harter Fehler
    (#19). Fehlt das Verzeichnis, greift der Reihe nach: genau EIN Name, der
    ohne Groß-/Kleinschreibung und Sonderzeichen passt („Mess Daten" → mess-daten);
    `project_fallback: basis` oder eine schon beantwortete Rückfrage → Lauf im
    Basisverzeichnis mit Hinweis im Prompt; sonst eine Rückfrage an den
    Auftraggeber (`frage`), der Task wird solange geparkt (#17)."""
    aus = {"dir": None, "fehler": None, "hinweis": None, "frage": None}
    ziel, fehler = projekt_workdir(basis, projekt)
    if not fehler:
        aus["dir"] = ziel
        return aus
    if "verlässt das Arbeitsverzeichnis" in fehler:
        aus["fehler"] = fehler
        return aus
    projekt = str(projekt).strip()
    vorhanden = unterverzeichnisse(basis, grenze=500)
    gleich = [n for n in vorhanden if _projekt_norm(n) == _projekt_norm(projekt)]
    if len(gleich) == 1 and _projekt_norm(projekt):
        aus["dir"] = (basis / gleich[0]).resolve()
        aus["hinweis"] = (f"[Hinweis des Watchers: project {projekt!r} gibt es nicht — "
                          f"gemeint ist offenbar das Verzeichnis {gleich[0]!r}, dort läufst du.]")
        return aus
    liste = ", ".join(vorhanden[:40]) or "(keine Unterverzeichnisse)"
    if schon_gefragt or str(fallback or "").strip().lower() == "basis":
        aus["dir"] = basis
        aus["hinweis"] = (
            f"[Hinweis des Watchers: Das angegebene project {projekt!r} existiert unter "
            f"{basis} nicht. Du läufst im Basisverzeichnis — finde den richtigen Ort selbst "
            f"(Antwort auf die Rückfrage beachten, falls vorhanden) und wechsle dorthin. "
            f"Vorhandene Verzeichnisse: {liste}]")
        return aus
    aehnlich = aehnliche_projekte(basis, projekt)
    aus["fehler"] = fehler
    aus["frage"] = (
        f"Projekt {projekt!r} gibt es unter {basis} nicht. Welches Verzeichnis ist gemeint"
        + (f" — z. B. {', '.join(aehnlich)}" if aehnlich else "")
        + "? Oder soll der Task im Basisverzeichnis laufen? (Hinweis: `project` ist ein "
          "Verzeichnisname unter dem Arbeitsverzeichnis des Agenten, kein Thema — ein "
          "Thema gehört in `thread`.)")
    return aus


def fehlerserie(status: str, dauer: float) -> bool:
    """Fehlschlag-Zähler füttern; True = anhalten (Serie sofortiger Fehler)."""
    global _schnelle_fehler
    if status == "error" and dauer < SCHNELL_SEKUNDEN:
        _schnelle_fehler += 1
    else:
        _schnelle_fehler = 0
    return _schnelle_fehler >= FEHLER_SCHWELLE


# Zeitgrenzen eines Laufs (Issue #38). Bis 09/2026 galt ein fester 1800-s-
# Wanduhr-Deckel — er traf im Echtbetrieb einen Lauf, der durchgehend
# arbeitete. Ein hängender Lauf verrät sich aber nicht durch seine Dauer,
# sondern durch STILLE: deshalb bricht zuerst der Leerlauf-Wächter ab (so
# lange kam kein stream-json-Event), die Wanduhr ist nur noch ein großzügiger
# zweiter Riegel. Beides je Agent einstellbar (--timeout/--leerlauf), ein Task
# darf den Deckel per `timeout` nur SENKEN.
CLAUDE_TIMEOUT = 7200.0   # Sekunden Gesamtdauer je Task-Lauf (Wanduhr)
CLAUDE_LEERLAUF = 900.0   # Sekunden ohne jedes Event; 0 = Leerlauf-Wächter aus
#   15 min: claudes eigene Bash-Grenze liegt bei höchstens 10 min — längere
#   Stille gibt es praktisch nur, wenn wirklich etwas hängt.


def beende_prozessgruppe(proc) -> None:
    """claude SAMT Kindprozessen beenden — eine Funktion für alle Abbrüche.

    Genutzt von Timeout und Not-Aus ("kill" auf stdin). claude startet Tools als
    eigene Prozesse; überlebt auch nur eines davon, hält es die stdout-Pipe
    offen und die Lese-Schleife blockiert für immer.
      POSIX:   killpg auf die eigene Prozessgruppe (start_new_session).
      Windows: `taskkill /F /T` — killpg gibt es dort nicht, und proc.kill()
               allein lässt die Kinder stehen."""
    if proc is None or proc.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=20)
    except Exception:  # noqa: BLE001 — Abbruch darf nie selbst crashen
        pass
    try:
        proc.kill()
    except Exception:  # noqa: BLE001
        pass


def _merke_prozess(proc) -> None:
    global _LAUFENDER
    with _PROZESS_LOCK:
        _LAUFENDER = proc


def _vergiss_prozess(proc) -> None:
    global _LAUFENDER
    with _PROZESS_LOCK:
        if _LAUFENDER is proc:
            _LAUFENDER = None


def abbrechen_laufenden() -> None:
    """Not-Aus von außen: den gerade laufenden Claude-Lauf sofort abschießen."""
    with _PROZESS_LOCK:
        proc = _LAUFENDER
    beende_prozessgruppe(proc)


@contextmanager
def mailbox_lock(base: Path):
    """flock auf <agent>/.lock — DIESELBE Sperre wie die Server-Mailbox.

    Review P1-10: Der Datei-Transport-Watcher änderte Envelopes bisher ohne
    den Mailbox-Lock; der Server konnte einen gerade abgeräumten Task beim
    Rückschreiben (link/resolve_question) wiederbeleben → Zombie-Requeue →
    Doppellauf. Fällt der Lock aus (Netz-Mount ohne flock), arbeiten wir wie
    der Server weiter — atomares Schreiben bleibt als Netz."""
    datei = None
    gesperrt = False
    try:
        datei = open(base / ".lock", "a", encoding="utf-8")
        if os.name == "posix":
            import fcntl
            fcntl.flock(datei.fileno(), fcntl.LOCK_EX)
            gesperrt = True
        else:  # pragma: no cover — Windows
            import msvcrt
            datei.seek(0)
            msvcrt.locking(datei.fileno(), msvcrt.LK_LOCK, 1)
            gesperrt = True
    except (OSError, ImportError):
        pass
    try:
        yield
    finally:
        if datei is not None:
            try:
                if gesperrt and os.name == "posix":
                    import fcntl
                    fcntl.flock(datei.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            try:
                datei.close()
            except OSError:
                pass


def lock_pfad(agent: str) -> Path:
    return Path.home() / ".agent-dashboard" / f"{agent}.lock"


def instanz_lock(agent: str):
    """Exklusiver Lock je Agent und PC (H2) — nur Standardlib.

    Zwei Watcher für denselben Agenten (Netz-Flap: Container startet einen
    neuen, der alte lebt noch; oder ein von Hand gestarteter neben der
    Automatik) würden denselben Task doppelt ausführen. Gibt das offene
    Datei-Objekt zurück (muss bis Prozessende offen bleiben!) oder None, wenn
    schon ein Watcher läuft. Der Lock stirbt mit dem Prozess — auch beim
    Absturz, ohne aufzuräumende Stale-Datei."""
    pfad = lock_pfad(agent)
    try:
        pfad.parent.mkdir(parents=True, exist_ok=True)
        datei = open(pfad, "a+", encoding="utf-8")
    except OSError as exc:
        # Review N: kein Home/kein Schreibrecht heißt NICHT "läuft schon" —
        # ehrlich melden und ohne Lock weiterlaufen (wie der Kommentar es
        # immer versprach; vorher endete der Start mit der falschen Meldung).
        sicher_print(f"[{now()}] WARNUNG: Instanz-Lock nicht möglich ({exc}) — "
                     f"laufe OHNE Doppelstart-Schutz weiter.")
        return "OHNE_LOCK"
    try:
        if os.name == "posix":
            import fcntl
            fcntl.flock(datei.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        else:
            import msvcrt
            datei.seek(0)
            msvcrt.locking(datei.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        datei.close()
        return None
    except ImportError:  # exotische Plattform ohne fcntl/msvcrt
        return datei
    try:
        datei.seek(0)
        datei.truncate()
        datei.write(f"pid={os.getpid()} seit={now()}\n")
        datei.flush()
    except OSError:
        pass
    return datei


def kurz(text: str, n: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1] + "…"


def tool_hinweis(block: dict) -> str:
    """Knappster nützlicher Parameter eines Tool-Aufrufs für die Statuszeile."""
    inp = block.get("input")
    if not isinstance(inp, dict):  # Review P2: kaputtes Event, kein Crash
        return ""
    for key in ("description", "command", "file_path", "path", "pattern",
                "prompt", "query", "url"):
        if inp.get(key):
            return str(inp[key])
    return ""


def tool_eingabe(block: dict) -> str:
    """Wie tool_hinweis, aber der BEFEHL vor der Beschreibung (Issue #39): bei
    einer Verweigerung zählt, was genau abgelehnt wurde — nicht, wie das
    Modell es genannt hat."""
    inp = block.get("input")
    if not isinstance(inp, dict):
        return ""
    for key in ("command", "file_path", "path", "pattern", "url", "query",
                "prompt", "description"):
        if inp.get(key):
            return str(inp[key])
    return ""


def tool_result_text(block: dict) -> str:
    """Text eines tool_result-Blocks (content ist String oder Block-Liste)."""
    inhalt = block.get("content")
    if isinstance(inhalt, str):
        return inhalt
    teile = [c.get("text", "") for c in inhalt or []
             if isinstance(c, dict) and c.get("type") == "text"]
    return " ".join(t for t in teile if t)


def baue_claude_cmd(claude_bin: str,
                    permission_mode: str | None = None,
                    allowed_tools: str | None = None,
                    append_system_prompt: str | None = None,
                    resume_id: str | None = None,
                    model: str | None = None,
                    fallback_model: str | None = None,
                    freigabe_tool: str | None = None) -> list[str]:
    """Kommandozeile für einen headless-Lauf — die instruction kommt über STDIN.

    Review P1-3 (löst zugleich das alte Issue #20 gründlicher): Auf Windows
    ist `claude` ein npm-Shim (`claude.cmd`), und CreateProcess startet
    Batchdateien über cmd.exe, das die Argumentzeile ERNEUT parst — ein
    `&`/`|` im Task-Text konnte dort Kommandos ausführen, an der ganzen
    Rechte-Härtung vorbei. Der Prompt geht darum nicht mehr als Argument mit,
    sondern über stdin des Kindprozesses (das entschärft nebenbei auch das
    ARG_MAX-Limit bei langen Wiederanlauf-Prompts). Der stdin des WATCHERS
    bleibt davon unberührt der Stopp-Kanal (Issue #16).
    """
    cmd = [claude_bin, "--print", "--output-format", "stream-json", "--verbose"]
    if permission_mode:
        cmd += ["--permission-mode", permission_mode]
    if allowed_tools:
        cmd += ["--allowed-tools", allowed_tools]
    if append_system_prompt:
        cmd += ["--append-system-prompt", append_system_prompt]
    # Sitzung fortsetzen. Die ID stammt aus unserer eigenen Buch-Datei, wird
    # aber trotzdem geprüft: auf Windows parst cmd.exe die Argumentzeile
    # erneut (P1-3) — dort darf nichts Freies hinein.
    if resume_id and SESSION_ID_RE.match(resume_id):
        cmd += ["--resume", resume_id]
    # Freigaben über das Dashboard (Issue #56): statt still abzulehnen, ruft
    # Claude Code dieses MCP-Tool und wartet auf die Entscheidung des Menschen.
    if freigabe_tool and FREIGABE_TOOL_RE.match(freigabe_tool):
        cmd += ["--permission-prompt-tool", freigabe_tool]
    # Modell je Agent/Rolle/Task (Issue #62) — nur geprüfte Namen.
    if modell_ok(model):
        cmd += ["--model", modell_ok(model)]
        if modell_ok(fallback_model) and modell_ok(fallback_model) != modell_ok(model):
            cmd += ["--fallback-model", modell_ok(fallback_model)]
    return cmd


# Rangfolge der permission-Modi, vom restriktivsten zum permissivsten — für
# die Rollen-Schnittmenge: eine Rolle darf den Modus nur SENKEN.
RANG_PERMISSION = {"plan": 0, "default": 1, "acceptEdits": 2, "bypassPermissions": 3}


def _tools_liste(text: "str | None") -> list:
    """Komma-Split mit Klammertiefe (Review N): `Bash(echo a,b)` zerfiel beim
    naiven Split in zwei Unsinn-Regeln."""
    teile, tiefe, akt = [], 0, ""
    for zeichen in text or "":
        if zeichen == "," and tiefe == 0:
            if akt.strip():
                teile.append(akt.strip())
            akt = ""
            continue
        akt += zeichen
        if zeichen == "(":
            tiefe += 1
        elif zeichen == ")":
            tiefe = max(0, tiefe - 1)
    if akt.strip():
        teile.append(akt.strip())
    return teile


def wirksame_rechte(agent_mode: "str | None", agent_tools: "str | None",
                    rollen_mode: "str | None", rollen_tools: "list | None"
                    ) -> "tuple[str | None, str | None]":
    """Rechte eines Laufs = SCHNITTMENGE aus Agent (CLI-Args) und Rolle (Task).

    Bewusste Entscheidung (Dashboard-Paket St.1, 02.09.2026): Eine Rolle kann
    nur EINSCHRÄNKEN, nie erweitern — sonst hebelte eine Rollen-Datei die
    Härtung aus agents.yaml aus.

      * permission_mode: der weniger permissive von beiden (RANG_PERMISSION).
        Ohne Agent-Vorgabe gilt claudes Default ("default") als Messlatte —
        die Rolle darf darunter (plan/default), nichts Permissiveres.
        Unbekannte Modus-Namen sind nicht vergleichbar: Agent-Vorgabe gewinnt.
      * allowed_tools: exakte String-Schnittmenge. rollen_tools=None heißt
        "Rolle sagt nichts" (Agent-Liste gilt); eine Liste behält nur, was der
        Agent auch gewährt — auf einem Agenten ohne eigene Liste kann eine
        Rolle also NICHTS freischalten (leer -> Flag ganz weglassen; die
        Auto-Werkzeuge wie Read/Grep brauchen ohnehin keine Freigabe).
    """
    mode = agent_mode or None
    if rollen_mode:
        if agent_mode and agent_mode in RANG_PERMISSION and rollen_mode in RANG_PERMISSION:
            if RANG_PERMISSION[rollen_mode] < RANG_PERMISSION[agent_mode]:
                mode = rollen_mode
        elif not agent_mode and rollen_mode in RANG_PERMISSION \
                and RANG_PERMISSION[rollen_mode] <= RANG_PERMISSION["default"]:
            mode = rollen_mode
    basis = _tools_liste(agent_tools)
    if rollen_tools is None:
        tools = basis
    else:
        gewuenscht = {str(t).strip() for t in rollen_tools if str(t).strip()}
        tools = [t for t in basis if t in gewuenscht]
    return mode, (",".join(tools) if tools else None)


def run_claude(claude_bin: str, instruction: str, workdir: Path, dry_run: bool,
               fortschritt=None, permission_mode: str | None = None,
               allowed_tools: str | None = None,
               append_system_prompt: str | None = None,
               verbrauch_out: dict | None = None,
               resume_id: str | None = None,
               lauf_out: dict | None = None,
               timeout: float | None = None,
               leerlauf: float | None = None,
               model: str | None = None,
               fallback_model: str | None = None) -> tuple[str, str, int]:
    """Gibt (result, log, returncode) zurück.

    `timeout`/`leerlauf` (Issue #38): Wanduhr-Deckel und Leerlauf-Grenze in
    Sekunden; None = Modul-Default, leerlauf=0 schaltet den Wächter ab.

    `resume_id` setzt eine frühere Sitzung fort (`--resume`); `lauf_out`
    bekommt, was das Sitzungsbuch braucht (session_id, kontext, arbeit —
    siehe lauf_mitschreiben). Beides wie verbrauch_out als dict statt im
    Rückgabewert, damit die bestehenden Entpackstellen unberührt bleiben.

    Headless Claude-Code mit --output-format stream-json (Issue #18): die
    Events werden zeilenweise gelesen und als knappe Fortschrittsmeldungen an
    `fortschritt` gereicht — dieselbe Leitung, über die schon "bearbeite …"
    ins Automatik-Panel fließt. Das Endergebnis kommt aus dem result-Event;
    Fallback ist der gesammelte Assistant-Text bzw. die Roh-Ausgabe (falls
    das Binary kein stream-json liefert).

    stdin=DEVNULL (Issue #16): der stdin des Watchers gehört dem Sanft-Stopp
    ("stop"-Zeile) — erbt ihn das Kind, kann claude das Stopp-Kommando
    verschlucken und wartet obendrein 3 s auf Piped-Input.

    permission_mode/allowed_tools (Issue #19): headless kann niemand eine
    Berechtigungs-Rückfrage beantworten — was der Lauf dürfen soll, muss als
    Flag mitkommen. Verweigerte Werkzeuge landen ausdrücklich im log, statt
    nur im Fließtext des Ergebnisses unterzugehen. Die Kommandozeile baut
    `baue_claude_cmd`; die instruction geht über STDIN des Kindes (P1-3).

    Abbruch (Not-Aus, H3): der Prozess ist global registriert, "kill" auf
    stdin schießt ihn samt Kindern ab. Die Lese-Schleife läuft unter
    try/finally — verlässt sie der Lauf auf irgendeinem anderen Weg (z.B.
    tote stdout-Leitung), stirbt die Prozessgruppe trotzdem, statt verwaist
    weiter Dateien zu ändern.
    """
    if dry_run:
        return f"[dry-run] hätte ausgeführt: {instruction}", "", 0
    cmd = baue_claude_cmd(claude_bin, permission_mode, allowed_tools,
                          append_system_prompt, resume_id, model, fallback_model,
                          LAUF_EINST.get("freigabe_tool"))
    # Während Claude Code auf eine Freigabe wartet, kommt kein Event — der
    # Leerlauf-Wächter darf in der Zeit nicht zuschlagen (Issue #56).
    if LAUF_EINST.get("freigabe_tool") and LAUF_EINST.get("freigabe_timeout"):
        mindest = float(LAUF_EINST["freigabe_timeout"]) + 120
        if leerlauf is None:
            leerlauf = max(CLAUDE_LEERLAUF, mindest)
        elif leerlauf > 0:
            leerlauf = max(float(leerlauf), mindest)
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(workdir),
            # Prompt über stdin (Review P1-3, s. baue_claude_cmd) — der
            # Watcher-stdin bleibt der Stopp-Kanal (Issue #16 gewahrt).
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            # Geheimnisse als Umgebungsvariablen (Issue #64) — nie als
            # Argument, sonst stünden sie in `ps`.
            env=({**os.environ, **GEHEIMNISSE} if GEHEIMNISSE else None),
            # UTF-8 erzwingen (Review P2): text=True nähme die Locale-Kodierung
            # — auf Windows (cp1252) wurde claudes UTF-8 zu Mojibake, und
            # undefinierte Bytes warfen UnicodeDecodeError mitten im Lauf.
            encoding="utf-8",
            errors="replace",
            # Eigene Prozessgruppe (POSIX): beim Timeout muss die GANZE Gruppe
            # sterben — claude spawnt Tool-Subprozesse, die sonst die stdout-
            # Pipe offen halten und das Zeilen-Lesen weiter blockieren.
            start_new_session=(os.name == "posix"),
        )
    except FileNotFoundError:
        # errno 2 allein nennt die Datei nicht — hier Klartext liefern (#14).
        return "", f"Claude-Binary nicht ausführbar: {claude_bin}", 127

    # Prompt in einem eigenen Thread schreiben: bei sehr langen Prompts würde
    # ein synchrones write blockieren, bis das Kind liest — und das Kind
    # könnte auf unser stdout-Lesen warten (Deadlock-Klassiker).
    def _prompt_schreiben() -> None:
        try:
            proc.stdin.write(instruction)
            proc.stdin.close()
        except (BrokenPipeError, OSError, ValueError):
            pass  # Kind schon tot — der Lauf endet ohnehin gleich

    threading.Thread(target=_prompt_schreiben, daemon=True).start()

    _merke_prozess(proc)
    if HART.is_set():  # Not-Aus kam zwischen Prüfung und Start
        beende_prozessgruppe(proc)

    def melde(text: str) -> None:
        """Fortschritt melden, ohne den Lauf an einer toten Leitung zu
        verlieren (H3) — der Callback schreibt auf stdout = SSH-Session."""
        if not fortschritt:
            return
        try:
            fortschritt(text)
        except (BrokenPipeError, OSError, ValueError):
            pass

    stderr_teile: list[str] = []

    def _stderr_lesen() -> None:
        try:
            stderr_teile.append(proc.stderr.read())
        except Exception:  # noqa: BLE001 — Pipe beim Abbruch zu: nichts gelesen
            stderr_teile.append("")

    leser = threading.Thread(target=_stderr_lesen, daemon=True)
    leser.start()
    abgelaufen = threading.Event()
    lauf_ende = threading.Event()
    abbruch_grund: list[str] = []
    deckel = float(timeout) if timeout and timeout > 0 else CLAUDE_TIMEOUT
    ruhe = CLAUDE_LEERLAUF if leerlauf is None else max(0.0, float(leerlauf))
    start_zeit = time.monotonic()
    letztes_event = [start_zeit]  # von der Lese-Schleife aufgefrischt

    def _waechter() -> None:
        """Zwei Riegel (Issue #38): Stille und Gesamtdauer. Pollt, statt einen
        Timer neu zu stellen — der Leerlauf-Zeitpunkt wandert mit jedem Event."""
        while not lauf_ende.wait(0.5):
            jetzt = time.monotonic()
            if jetzt - start_zeit >= deckel:
                abbruch_grund.append(f"Timeout nach {deckel:.0f}s Gesamtdauer")
            elif ruhe and jetzt - letztes_event[0] >= ruhe:
                abbruch_grund.append(f"Timeout: {ruhe:.0f}s ohne Lebenszeichen "
                                     f"(Lauf hing nach {jetzt - start_zeit:.0f}s)")
            else:
                continue
            abgelaufen.set()
            beende_prozessgruppe(proc)  # POSIX killpg / Windows taskkill /T (M13)
            return

    threading.Thread(target=_waechter, daemon=True).start()

    ergebnis: str | None = None
    fehler_event = False
    texte: list[str] = []  # Assistant-Texte (Fallback-Ergebnis)
    roh: list[str] = []    # Nicht-JSON-Zeilen (Binary ohne stream-json)
    werkzeug_namen: dict[str, str] = {}  # tool_use_id → Tool-Name
    werkzeug_eingaben: dict[str, str] = {}  # tool_use_id → Befehl/Pfad (Issue #39)
    abgelehnt: list[str] = []            # verweigerte Werkzeuge (Issue #19)
    verweigert: list[dict] = []          # …mit Befehl, dedupliziert (Issue #39)

    def _verweigerung(name: str, eingabe: str) -> None:
        if name not in abgelehnt:
            abgelehnt.append(name)
        eintrag = {"tool": name, "eingabe": kurz(eingabe or "", 200)}
        if eintrag not in verweigert and len(verweigert) < 20:
            verweigert.append(eintrag)
    vollstaendig = False
    try:
        for zeile in proc.stdout:
            letztes_event[0] = time.monotonic()  # Lebenszeichen (Issue #38)
            zeile = zeile.strip()
            if not zeile:
                continue
            try:
                ev = json.loads(zeile)
            except json.JSONDecodeError:
                roh.append(zeile)
                continue
            typ = ev.get("type")
            if lauf_out is not None:
                lauf_mitschreiben(lauf_out, ev)
            if typ == "assistant":
                nachricht = ev.get("message")
                bloecke = nachricht.get("content") if isinstance(nachricht, dict) else None
                for block in bloecke or []:
                    if not isinstance(block, dict):
                        continue  # Review P2: kaputtes Event darf den Lauf nicht killen
                    if block.get("type") == "tool_use":
                        if block.get("id"):
                            werkzeug_namen[block["id"]] = block.get("name", "?")
                            werkzeug_eingaben[block["id"]] = tool_eingabe(block)
                        melde(kurz(f"→ {block.get('name', '?')} "
                                   f"{tool_hinweis(block)}", 100))
                    elif block.get("type") == "text" and block.get("text"):
                        texte.append(block["text"])
                        melde(kurz(block["text"], 100))
            elif typ == "user":
                # Abgelehnte Werkzeuge sichtbar machen (Issue #19): der Lauf
                # endet sonst normal, und der Grund steht nur im Fließtext.
                nachricht = ev.get("message")
                bloecke = nachricht.get("content") if isinstance(nachricht, dict) else None
                for block in bloecke or []:
                    if not (isinstance(block, dict)
                            and block.get("type") == "tool_result"
                            and block.get("is_error")):
                        continue
                    text = tool_result_text(block)
                    if "permission" not in text.lower():
                        continue  # normaler Tool-Fehler, kein Berechtigungs-Thema
                    tuid = block.get("tool_use_id") or ""
                    name = werkzeug_namen.get(tuid, "?")
                    _verweigerung(name, werkzeug_eingaben.get(tuid, ""))
                    melde(kurz(f"✗ {name} abgelehnt: {text}", 100))
            elif typ == "result":
                ergebnis = ev.get("result") or ""
                fehler_event = bool(ev.get("is_error"))
                # Verbrauch (Paket St.3): usage + total_cost_usd stehen im
                # result-Event — in das übergebene dict statt in den
                # Rückgabewert, damit die zehn bestehenden Entpackstellen
                # (Tests, Loops) unangetastet bleiben.
                if verbrauch_out is not None:
                    usage = ev.get("usage") or {}
                    for feld in ("input_tokens", "output_tokens",
                                 "cache_creation_input_tokens",
                                 "cache_read_input_tokens"):
                        if isinstance(usage.get(feld), (int, float)):
                            verbrauch_out[feld] = int(usage[feld])
                    if isinstance(ev.get("total_cost_usd"), (int, float)):
                        verbrauch_out["total_cost_usd"] = float(ev["total_cost_usd"])
                for d in ev.get("permission_denials") or []:
                    if not isinstance(d, dict):
                        continue
                    # Issue #39: nicht nur den Namen — der BEFEHL entscheidet,
                    # ob die Freigabe fehlt oder claude ihn trotzdem ablehnt.
                    eingabe = werkzeug_eingaben.get(d.get("tool_use_id") or "", "")
                    if not eingabe and isinstance(d.get("tool_input"), dict):
                        eingabe = tool_eingabe({"input": d["tool_input"]})
                    _verweigerung(d.get("tool_name") or "?", eingabe)
        vollstaendig = True
    finally:
        lauf_ende.set()  # Wächter beenden
        # Abbruch auf JEDEM Weg (Not-Aus, Ausnahme in der Schleife, tote
        # Leitung): die Prozessgruppe muss sterben, sonst arbeitet claude
        # unbeaufsichtigt weiter (H3).
        if not vollstaendig or HART.is_set():
            beende_prozessgruppe(proc)
        _vergiss_prozess(proc)
    # Review P2: Kind schließt stdout, endet aber nicht (D-State, hängender
    # Tool-Prozess) — ein wait() ohne Deckel hinge hier für immer, und der
    # Wecker ist zu dem Zeitpunkt schon abbestellt.
    try:
        rc = proc.wait(timeout=60)
    except subprocess.TimeoutExpired:
        beende_prozessgruppe(proc)
        try:
            rc = proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            rc = 124
    leser.join(timeout=5)
    for pipe in (proc.stdout, proc.stderr):  # Dauerläufer: keine fds ansammeln
        try:
            pipe.close()
        except Exception:  # noqa: BLE001
            pass
    log = (stderr_teile[0] if stderr_teile else "").strip()
    if HART.is_set():
        log = (log + "\n[watcher] Not-Aus — Lauf abgebrochen (kill)").strip()
        rc = rc or 143
    if abgelaufen.is_set():
        grund = abbruch_grund[0] if abbruch_grund else "Timeout"
        log = (log + f"\n[watcher] {grund} — Prozess abgebrochen").strip()
        rc = rc or 124
        if lauf_out is not None:
            lauf_out["timeout"] = grund
    if ergebnis is None:
        ergebnis = "\n".join(texte) or "\n".join(roh)
    if fehler_event and rc == 0:
        rc = 1
    if abgelehnt:
        log = (log + "\n" + verweigerungs_log(verweigert, allowed_tools)).strip()
        if lauf_out is not None:
            lauf_out["verweigert"] = verweigert
    return ergebnis.strip(), log, rc


def verweigerungs_log(verweigert: list, allowed_tools: "str | None") -> str:
    """Log-Text zu verweigerten Werkzeugen (Issue #39): je Aufruf der Befehl,
    und der Hinweis passend zur Lage. Früher hieß es pauschal „allowed_tools
    setzen" — auch wenn das Werkzeug längst freigegeben war und claude nur
    EINEN Aufruf ablehnte; das schickte die Fehlersuche in die falsche Richtung."""
    frei = _tools_liste(allowed_tools)
    zeilen, hinweise = [], []
    for v in verweigert:
        name, eingabe = str(v.get("tool") or "?"), str(v.get("eingabe") or "")
        zeilen.append(f"[watcher] Berechtigung verweigert: {name}"
                      + (f": {eingabe}" if eingabe else ""))
        if name in frei:
            h = (f"{name} ist pauschal freigegeben und wurde trotzdem abgelehnt — "
                 f"claude lehnt einzelne Aufrufe auch dann ab (z.B. Zugriff "
                 f"außerhalb des Arbeitsverzeichnisses); Befehl siehe oben")
        elif any(str(f).startswith(name + "(") for f in frei):
            muster = ", ".join(str(f) for f in frei if str(f).startswith(name + "("))
            h = f"{name} ist nur eingeschränkt freigegeben ({muster}) — der Aufruf passt nicht dazu"
        else:
            h = (f"{name} ist nicht freigegeben — permission_mode/allowed_tools in "
                 f"agents.yaml setzen oder auf dem Agenten-PC freigeben")
        if h not in hinweise:
            hinweise.append(h)
    return "\n".join(zeilen + ["[watcher] " + h for h in hinweise])


# --- Sitzungsbuch: welcher Lauf setzt welche claude-Sitzung fort -------------

_KONTEXT_FELDER = ("input_tokens", "cache_creation_input_tokens",
                   "cache_read_input_tokens", "output_tokens")


def _zahl(wert) -> float:
    """Zahl aus der Buch-Datei — kaputte Einträge zählen als 0 statt zu werfen."""
    try:
        zahl = float(wert)
    except (TypeError, ValueError):
        return 0.0
    return zahl if zahl == zahl and zahl not in (float("inf"), float("-inf")) else 0.0


def lauf_mitschreiben(lauf: dict, ev: dict) -> None:
    """Aus den stream-json-Events mitnehmen, was das Sitzungsbuch braucht:

      session_id  aus dem init-Event bzw. einem ERFOLGREICHEN result-Event
                  (ein gescheitertes --resume echot die unbekannte ID zurück —
                  die darf nie im Buch landen),
      kontext     Eingabe + Ausgabe der LETZTEN Haupt-Anfrage = so groß ist
                  der Verlauf, den der nächste Lauf mitschleppt,
      arbeit      es kam mindestens eine Assistant-Nachricht — der Lauf hat
                  also wirklich begonnen (Gegenteil: --resume scheiterte sofort).

    Subagenten-Nachrichten (parent_tool_use_id) zählen nicht: deren Kontext
    ist ein eigener und landet nicht im Verlauf der Sitzung."""
    typ = ev.get("type")
    if typ == "system":
        if ev.get("subtype") == "init" and ev.get("session_id"):
            lauf["session_id"] = str(ev["session_id"])
            if ev.get("model"):
                lauf["modell"] = str(ev["model"])
    elif typ == "assistant":
        if ev.get("parent_tool_use_id"):
            return
        erste = not lauf.get("arbeit")
        lauf["arbeit"] = True
        nachricht = ev.get("message")
        usage = nachricht.get("usage") if isinstance(nachricht, dict) else None
        if isinstance(usage, dict) and erste:
            # Issue #55: die ERSTE Anfrage zeigt, ob der Cache noch warm war —
            # gelesen (billig) gegen neu geschrieben (teurer als normale Eingabe).
            lauf["cache_gelesen"] = int(_zahl(usage.get("cache_read_input_tokens")))
            lauf["cache_neu"] = int(_zahl(usage.get("cache_creation_input_tokens")))
        if isinstance(usage, dict):
            kontext = int(sum(_zahl(usage.get(f)) for f in _KONTEXT_FELDER))
            if kontext > 0:
                lauf["kontext"] = kontext
    elif typ == "result":
        if ev.get("session_id") and not ev.get("is_error"):
            lauf["session_id"] = str(ev["session_id"])


def sitzungs_pfad(agent: str) -> Path:
    return Path.home() / ".agent-dashboard" / f"{agent}.sitzungen.json"


def lade_sitzungen(agent: str) -> dict:
    """Buch-Datei lesen; fehlend/kaputt = leeres Buch (dann beginnt eben eine
    neue Sitzung — nie ein Grund, einen Task scheitern zu lassen)."""
    try:
        buch = json.loads(sitzungs_pfad(agent).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        buch = {}
    if not isinstance(buch, dict):
        buch = {}
    for feld in ("sitzungen", "tasks", "uebergaben"):
        if not isinstance(buch.get(feld), dict):
            buch[feld] = {}
    return buch


def speichere_sitzungen(agent: str, buch: dict) -> None:
    pfad = sitzungs_pfad(agent)
    try:
        pfad.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(pfad, buch)
    except OSError:
        pass  # Gedächtnis ist Komfort — ein Schreibfehler kostet keinen Task


def thread_name(wert) -> str:
    """`thread` eines Tasks auf ein harmloses Kürzel bringen (Schlüssel im Buch)."""
    return THREAD_RE.sub("-", str(wert or "")).strip("-")[:64]


def sitzungs_schluessel(task_dir: Path, rolle: "str | None",
                        thread: "str | None" = None,
                        model: "str | None" = None) -> str:
    """Eine Sitzung je Arbeitsverzeichnis UND Rolle: claude findet Sitzungen
    nur im Verzeichnis, in dem sie entstanden; und eine Rolle bringt einen
    eigenen System-Prompt mit — gemischt verwirrte das Modell und Cache.

    Ohne `thread` teilen sich alle Aufträge des Verzeichnisses EINE Sitzung —
    so wie im Handbetrieb eine offene Claude-Code-Sitzung je Box alles
    abarbeitet. Ein `thread` (Issue #37) spaltet bewusst einen eigenen Faden
    ab: Folge-Aufträge desselben Vorgangs finden genau ihr Gedächtnis wieder."""
    basis = f"{task_dir}|{rolle or ''}"
    faden = thread_name(thread)
    schluessel = f"{basis}|#{faden}" if faden else basis
    # Ein vom TASK bestelltes Modell (Issue #62) bekommt eine eigene Sitzung:
    # ein Modellwechsel mitten in der Sitzung macht den ganzen Cache kalt.
    return f"{schluessel}|@{model}" if modell_ok(model) else schluessel


def kontext_fenster(modell: "str | None") -> "int | None":
    """Kontextfenster in Tokens zum Modellnamen aus dem init-Event, None wenn
    unbekannt. Stand 09/2026: die Claude-5-Familie (Opus 5, Sonnet 5, Fable,
    Mythos) und Opus ab 4.6 haben 1M; Haiku 4.5 und ältere 200k; ein
    `[1m]`-Zusatz erzwingt 1M."""
    name = str(modell or "").strip().lower()
    if not name:
        return None
    if "[1m]" in name or "1m" in name.split("-"):
        return KONTEXT_FENSTER_1M
    if "haiku" in name:
        return KONTEXT_FENSTER_200K
    m = re.search(r"(opus|sonnet|fable|mythos)[-_ ]?(\d+)(?:[-_.](\d+))?", name)
    if not m:
        return None
    familie, haupt, neben = m.group(1), int(m.group(2)), int(m.group(3) or 0)
    if haupt >= 5 or familie in ("fable", "mythos"):
        return KONTEXT_FENSTER_1M
    if familie == "opus" and haupt == 4 and neben >= 6:
        return KONTEXT_FENSTER_1M
    return KONTEXT_FENSTER_200K


def kontext_grenze(max_kontext: "int | float | None", modell: "str | None") -> "int | None":
    """Wirksame Kontextgrenze: positiver Wert = hart; sonst relativ zum
    Modellfenster; None = keine Grenze (Modell unbekannt)."""
    fest = int(_zahl(max_kontext))
    if fest > 0:
        return fest
    fenster = kontext_fenster(modell)
    return int(fenster * KONTEXT_FENSTER_ANTEIL) if fenster else None


def sitzung_waehlen(buch: dict, schluessel: str, task_id: str, jetzt: float,
                    max_pause: float = RESUME_MAX_PAUSE,
                    max_kontext: int = RESUME_MAX_KONTEXT,
                    max_tasks: int = RESUME_MAX_TASKS,
                    kalt_s: float = 0.0, kompakt_ab: int = 0,
                    info: "dict | None" = None) -> "tuple[str | None, str]":
    """→ (session_id oder None, Begründung fürs Log). Reine Funktion.

    Vorrang hat der TASK: läuft dieselbe task_id erneut, wurde sie nach einer
    Rückfrage geparkt (Issue #17) — dann zählt das Gedächtnis mehr als jede
    Grenze, sonst fiele genau dort die ganze Vorarbeit doppelt an."""
    info = info if info is not None else {}
    s = buch.get("sitzungen", {}).get(schluessel)
    s = s if isinstance(s, dict) and s.get("session_id") else None

    def kalt_und_gross(pause: float, kontext: int) -> "str | None":
        # Issue #55: kalter Cache UND großer Verlauf → das Fortsetzen kostet
        # mehr als ein Neustart mit kompakter Übergabe.
        if kalt_s > 0 and kompakt_ab > 0 and pause > kalt_s and kontext > kompakt_ab:
            return (f"Cache kalt (Pause {pause / 60:.0f} min) und Kontext "
                    f"{kontext // 1000}k über {kompakt_ab // 1000}k — neue Sitzung "
                    f"mit Übergabe")
        return None

    eintrag = buch.get("tasks", {}).get(task_id)
    if (isinstance(eintrag, dict) and eintrag.get("session_id")
            and jetzt - _zahl(eintrag.get("zeit")) <= RESUME_TASK_MERKDAUER):
        pause = jetzt - _zahl(eintrag.get("zeit"))
        kontext = (int(_zahl(s.get("kontext")))
                   if s and s.get("session_id") == eintrag["session_id"] else 0)
        info.update(pause=pause, kontext=kontext)
        teuer = kalt_und_gross(pause, kontext)
        if teuer:
            info["uebergabe"] = True
            return None, teuer
        return str(eintrag["session_id"]), "derselbe Task geht nach der Rückfrage weiter"
    if s is None:
        return None, "noch keine Sitzung für dieses Verzeichnis"
    info["uebergabe"] = True  # es gab eine Sitzung — wird sie nicht fortgesetzt, Übergabe
    pause = jetzt - _zahl(s.get("zuletzt"))
    info["pause"] = pause
    if pause > max_pause:
        return None, f"letzter Lauf vor {pause / 3600:.1f} h, Grenze {max_pause / 3600:.1f} h"
    kontext = int(_zahl(s.get("kontext")))
    info["kontext"] = kontext
    teuer = kalt_und_gross(pause, kontext)
    if teuer:
        return None, teuer
    grenze = kontext_grenze(max_kontext, s.get("modell"))
    if grenze is not None and kontext > grenze:
        return None, (f"Kontext {kontext // 1000}k über Grenze {grenze // 1000}k"
                      + ("" if int(_zahl(max_kontext)) > 0 else
                         f" ({int(KONTEXT_FENSTER_ANTEIL * 100)} % des Fensters von "
                         f"{s.get('modell')})"))
    anzahl = int(_zahl(s.get("tasks")))
    if anzahl >= max_tasks:
        return None, f"schon {anzahl} Tasks in der Sitzung"
    info["uebergabe"] = False
    return str(s["session_id"]), f"Task {anzahl + 1} der Sitzung, Kontext ~{kontext // 1000}k"


def uebergabe_merken(buch: dict, schluessel: str, task_id: str, ergebnis: str,
                     jetzt: float) -> None:
    """Ergebnis eines Laufs fürs Sitzungsbuch (Issue #55): beginnt später
    eine NEUE Sitzung, bekommt sie die letzten Ergebnisse als Übergabe."""
    text = str(ergebnis or "").strip()
    if not text:
        return
    liste = [u for u in buch.setdefault("uebergaben", {}).get(schluessel, [])
             if isinstance(u, dict) and u.get("task_id") != task_id]
    liste.append({"task_id": task_id, "zeit": jetzt, "text": kurz(text, UEBERGABE_ZEICHEN)})
    buch["uebergaben"][schluessel] = liste[-UEBERGABE_LAEUFE:]
    buch["uebergaben"] = {
        k: v for k, v in buch["uebergaben"].items()
        if isinstance(v, list) and v
        and jetzt - _zahl(v[-1].get("zeit")) <= RESUME_SITZUNG_VERFALL}


def uebergabe_text(buch: dict, schluessel: str, grund: str) -> str:
    """Kompakte Übergabe an eine neue Sitzung — leer, wenn nichts gemerkt ist."""
    liste = [u for u in (buch.get("uebergaben") or {}).get(schluessel, [])
             if isinstance(u, dict) and u.get("text")]
    if not liste:
        return ""
    teile = [f"[Übergabe des Watchers: Die vorige Claude-Sitzung zu diesem Verzeichnis/"
             f"Vorgang wird nicht fortgesetzt ({grund}). Du beginnst frisch — hier die "
             f"Ergebnisse der letzten {len(liste)} Läufe, damit du den Stand kennst:]"]
    for u in liste:
        wann = time.strftime("%d.%m. %H:%M", time.localtime(_zahl(u.get("zeit"))))
        teile.append(f"— {u.get('task_id')} ({wann}):\n{u['text']}")
    teile.append("[Ende der Übergabe. Es folgt der aktuelle Auftrag.]")
    return "\n\n".join(teile) + "\n\n"


def sitzung_merken(buch: dict, schluessel: str, task_id: str, session_id: str,
                   kontext: int, jetzt: float, modell: "str | None" = None) -> None:
    vorher = buch["sitzungen"].get(schluessel)
    anzahl = (int(_zahl(vorher.get("tasks")))
              if isinstance(vorher, dict) and vorher.get("session_id") == session_id else 0)
    eintrag = {"session_id": session_id, "zuletzt": jetzt,
               "kontext": int(kontext or 0), "tasks": anzahl + 1}
    # Modell der Sitzung (Issue #44): daraus leitet sitzung_waehlen das
    # Kontextfenster ab. Fehlt es im neuen Lauf, bleibt das alte stehen.
    modell = modell or (vorher.get("modell") if isinstance(vorher, dict) else None)
    if modell:
        eintrag["modell"] = str(modell)
    buch["sitzungen"][schluessel] = eintrag
    buch["tasks"][task_id] = {"session_id": session_id, "zeit": jetzt}
    # Aufräumen: das Buch darf auf einem Dauerläufer nicht endlos wachsen.
    frisch = {k: v for k, v in buch["tasks"].items()
              if isinstance(v, dict) and jetzt - _zahl(v.get("zeit")) <= RESUME_TASK_MERKDAUER}
    if len(frisch) > RESUME_TASK_MERKZAHL:
        behalten = sorted(frisch, key=lambda k: _zahl(frisch[k].get("zeit")))[-RESUME_TASK_MERKZAHL:]
        frisch = {k: frisch[k] for k in behalten}
    buch["tasks"] = frisch
    buch["sitzungen"] = {k: v for k, v in buch["sitzungen"].items()
                         if isinstance(v, dict)
                         and jetzt - _zahl(v.get("zuletzt")) <= RESUME_SITZUNG_VERFALL}


def sitzung_vergessen(buch: dict, schluessel: str, task_id: str) -> None:
    buch["sitzungen"].pop(schluessel, None)
    buch["tasks"].pop(task_id, None)


def run_claude_sitzung(resume: "dict | None", agent: str, task_id: str,
                       claude_bin: str, instruction: str, task_dir: Path,
                       dry_run: bool, fortschritt=None,
                       permission_mode: "str | None" = None,
                       allowed_tools: "str | None" = None,
                       rollen_prompt: "str | None" = None,
                       rolle: "str | None" = None,
                       verbrauch: "dict | None" = None,
                       with_mcp_hint: bool = False,
                       thread: "str | None" = None,
                       timeout: "float | None" = None,
                       leerlauf: "float | None" = None,
                       lauf_meta: "dict | None" = None,
                       model: "str | None" = None,
                       fallback_model: "str | None" = None,
                       model_quelle: str = "") -> "tuple[str, str, int]":
    """run_claude mit Gedächtnis: setzt — wenn erlaubt und sinnvoll — die
    Sitzung dieses Verzeichnisses (bzw. Vorgangs, `thread`) fort, sonst
    beginnt eine neue.

    `resume` = {"an", "max_pause", "max_kontext"} aus main(); None/aus = jeder
    Task frisch. Der MCP-Hinweis wird HIER vorangestellt, weil sein Wortlaut
    davon abhängt: eine fortgesetzte Sitzung kennt die Regeln schon und
    bekommt nur die Kurzfassung.

    Scheitert das Fortsetzen SOFORT und ohne jede Arbeit (Transkript gelöscht,
    Sitzung unbekannt), läuft der Task einmal frisch — nie nach echter
    Arbeit, sonst würde ein halb erledigter Task doppelt ausgeführt.

    `lauf_meta` (Ausgabe) landet als `lauf` in der Antwort des Tasks: Sitzung
    (neu/fortgesetzt, session_id zum Übernehmen per `claude --resume`),
    Kontextgröße, Timeout-Grund (Issue #38), verweigerte Aufrufe (Issue #39)."""
    if verbrauch is None:
        verbrauch = {}
    if lauf_meta is None:
        lauf_meta = {}

    def melde(text: str) -> None:
        if fortschritt:
            try:
                fortschritt(text)
            except (BrokenPipeError, OSError, ValueError):
                pass

    frist = frist_hinweis(timeout, leerlauf) if with_mcp_hint else ""
    voll = (mcp_hint(agent, task_id) if with_mcp_hint else "") + frist + instruction
    lauf: dict = {}
    aktiv = bool(resume and resume.get("an")) and not dry_run
    sid = None
    result, err, rc = "", "", 1

    def starte(prompt: str, resume_id: "str | None") -> "tuple[str, str, int]":
        return run_claude(claude_bin, prompt, task_dir, dry_run, fortschritt,
                          permission_mode, allowed_tools, rollen_prompt,
                          verbrauch_out=verbrauch, resume_id=resume_id,
                          lauf_out=lauf, timeout=timeout, leerlauf=leerlauf,
                          model=model, fallback_model=fallback_model)

    if not aktiv:
        result, err, rc = starte(voll, None)
    else:
        buch = lade_sitzungen(agent)
        schluessel = sitzungs_schluessel(
            task_dir, rolle, thread, model if model_quelle == "task" else None)
        max_pause = _zahl(resume.get("max_pause")) or RESUME_MAX_PAUSE
        if thread_name(thread):
            max_pause = max(max_pause, RESUME_THREAD_MAX_PAUSE)
        wahl_info: dict = {}
        kalt_min = resume.get("kalt_minuten")
        kompakt = resume.get("kompakt_ab")
        sid, grund = sitzung_waehlen(
            buch, schluessel, task_id, time.time(), max_pause,
            int(_zahl(resume.get("max_kontext"))) or RESUME_MAX_KONTEXT,
            kalt_s=(RESUME_KALT_MINUTEN if kalt_min is None else _zahl(kalt_min)) * 60,
            kompakt_ab=int(RESUME_KOMPAKT_AB if kompakt is None else _zahl(kompakt)),
            info=wahl_info)
        if sid:
            melde(f"setzt Sitzung fort ({grund})")
            kurzfassung = ((mcp_hint_kurz(agent, task_id) if with_mcp_hint else "")
                           + frist + instruction)
            start = time.monotonic()
            result, err, rc = starte(kurzfassung, sid)
            if (rc != 0 and not lauf.get("arbeit") and not HART.is_set()
                    and time.monotonic() - start < SCHNELL_SEKUNDEN):
                sitzung_vergessen(buch, schluessel, task_id)
                speichere_sitzungen(agent, buch)
                grund = "alte Sitzung nicht fortsetzbar: " + kurz(err or result, 120)
                sid = None
                lauf.clear()
                verbrauch.clear()
        if not sid:
            melde(f"neue Sitzung ({grund})")
            uebergabe = (uebergabe_text(buch, schluessel, grund)
                         if wahl_info.get("uebergabe") else "")
            if uebergabe:
                lauf_meta["uebergabe"] = True
            hinweis_teil = (mcp_hint(agent, task_id) if with_mcp_hint else "") + frist
            result, err, rc = starte(hinweis_teil + uebergabe + instruction, None)
        # Behalten: Erfolg — und der TIMEOUT (Issue #37/#38): der Lauf hat
        # gearbeitet, sein Stand steht im Transkript, und wer den Task erneut
        # anstößt (oder den nächsten schickt), soll genau dort weitermachen.
        # Vergessen: jeder andere Fehler und der Not-Aus — da beginnt der
        # nächste Task lieber sauber.
        behalten = bool(lauf.get("session_id")) and not HART.is_set() and (
            rc == 0 or bool(lauf.get("timeout")))
        if behalten:
            sitzung_merken(buch, schluessel, task_id, lauf["session_id"],
                           int(lauf.get("kontext") or 0), time.time(),
                           lauf.get("modell"))
        else:
            sitzung_vergessen(buch, schluessel, task_id)
        if rc == 0:
            uebergabe_merken(buch, schluessel, task_id, result, time.time())
        speichere_sitzungen(agent, buch)
        lauf_meta["sitzung"] = "fortgesetzt" if sid else "neu"
        # Issue #55: Pause seit dem letzten Lauf und der GEMESSENE Cache-Zustand
        # der ersten Anfrage — „kalt" heißt: mehr neu geschrieben als gelesen.
        if wahl_info.get("pause") is not None:
            lauf_meta["pause"] = int(wahl_info["pause"])
        if sid and (lauf.get("cache_gelesen") or lauf.get("cache_neu")):
            lauf_meta["cache_gelesen"] = int(lauf.get("cache_gelesen") or 0)
            lauf_meta["cache_neu"] = int(lauf.get("cache_neu") or 0)
            lauf_meta["cache_kalt"] = lauf_meta["cache_neu"] > lauf_meta["cache_gelesen"]
        # Issue #44: WARUM neu bzw. fortgesetzt — bisher stand das nur im
        # Watcher-stdout; im Panel merkte niemand, dass das Gedächtnis nie griff.
        lauf_meta["sitzung_grund"] = grund
        if behalten and lauf.get("timeout"):
            lauf_meta["fortsetzbar"] = True
    for feld in ("session_id", "kontext", "timeout", "verweigert", "modell"):
        if lauf.get(feld):
            lauf_meta[feld] = lauf[feld]
    if thread_name(thread):
        lauf_meta["thread"] = thread_name(thread)
    if model_quelle:
        lauf_meta["modell_quelle"] = model_quelle
    if lauf.get("timeout") and lauf.get("session_id"):
        err = (err + f"\n[watcher] Stand liegt in der Sitzung {lauf['session_id']} — "
                     f"übernehmen mit: claude --resume {lauf['session_id']}"
               + (" (der Task setzt sie beim erneuten Anstoßen von selbst fort)"
                  if lauf_meta.get("fortsetzbar") else "")).strip()
    # Issue #64: Geheimniswerte verlassen die Maschine nicht im Klartext.
    for v in lauf_meta.get("verweigert") or []:
        if isinstance(v, dict) and v.get("eingabe"):
            v["eingabe"] = schwaerze(v["eingabe"])
    return schwaerze(result), schwaerze(err), rc


def wirksamer_timeout(agent_timeout: "float | None", task_timeout) -> float:
    """Wanduhr-Deckel eines Laufs (Issue #38): der Agent-Wert ist die
    Obergrenze, ein `timeout` im Task darf ihn nur SENKEN."""
    deckel = float(agent_timeout) if agent_timeout and agent_timeout > 0 else CLAUDE_TIMEOUT
    wunsch = _zahl(task_timeout)
    return min(deckel, wunsch) if wunsch > 0 else deckel


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
    """Daemon-Thread: Kommandos auf stdin.

      "stop"  = sanft beenden (laufender Claude-Lauf darf fertig werden);
                stdin-EOF (haltende SSH-Verbindung weg) wirkt genauso.
      "kill"  = Not-Aus: laufenden Lauf sofort samt Kindern abschießen (H3) —
                ein bloßes Schließen der Verbindung erreicht ihn nicht."""
    def _lauscher() -> None:
        try:
            for zeile in sys.stdin:
                if zeile.startswith("env "):
                    # Geheimnisse (Issue #64) — die Zeile selbst nie ausgeben
                    if setze_geheimnisse(zeile.strip()):
                        sicher_print(f"[{now()}] {len(GEHEIMNISSE)} Geheimnis(se) vom "
                                     f"Dashboard übernommen")
                    continue
                kommando = zeile.strip().lower()
                if kommando == "kill":
                    HART.set()
                    sicher_print(f"[{now()}] Not-Aus empfangen — laufenden "
                                 f"Claude-Lauf abbrechen.")
                    abbrechen_laufenden()
                    break
                if kommando == "stop":
                    sicher_print(f"[{now()}] Stop-Kommando empfangen — beende nach laufendem Task.")
                    break
            else:
                sicher_print(f"[{now()}] stdin geschlossen — beende nach laufendem Task.")
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
        # Token-Kanal (Issue #32/Review N): meldet sich der Watcher über
        # HTTPS statt Tunnel, verlangt der Server einen Bearer-Token.
        # MCP_TOKEN direkt oder MCP_TOKEN_FILE (Pfad zur .token-Datei).
        self.token = os.environ.get("MCP_TOKEN") or ""
        if not self.token and os.environ.get("MCP_TOKEN_FILE"):
            try:
                self.token = Path(os.environ["MCP_TOKEN_FILE"]).read_text(
                    encoding="utf-8").strip()
            except OSError:
                self.token = ""

    def _post(self, body: dict, erwarte_antwort: bool = True) -> dict | None:
        daten = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(self.url, data=daten, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json, text/event-stream")
        req.add_header("MCP-Protocol-Version", self.protocol)
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
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


def liefere_ergebnis(client: "McpClient | None", url: str, task_id: str,
                     result: str, status: str, log: str,
                     verbrauch: dict | None = None,
                     lauf: dict | None = None
                     ) -> tuple[object, "McpClient | None", str | None]:
    """complete_task mit eigener Retry-Schleife (M8).

    Läuft bewusst AUCH nach gesetztem Sanft-Stopp weiter — sonst wirft ein
    "stop" kurz vor Schluss das Ergebnis eines halbstündigen Laufs weg und der
    Task bliebe beim Server ewig "running". Nur der Not-Aus (HART) bricht ab.
    Gibt (antwort, client, fehler) zurück; client ist None, wenn die Session
    neu aufgebaut werden muss."""
    fehler: str | None = None
    for versuch in range(ABLIEFER_VERSUCHE):
        try:
            if client is None:
                client = McpClient(url)
                client.connect()
            antwort = client.call("complete_task", {
                "task_id": task_id, "result": result,
                "status": status, "log": log,
                **({"verbrauch": verbrauch} if verbrauch else {}),
                # Lauf-Daten (Sitzung, Timeout, Verweigerungen). Ein älterer
                # Server ignoriert das unbekannte Argument stillschweigend.
                **({"lauf": lauf} if lauf else {}),
            })
            if isinstance(antwort, dict) and antwort.get("error"):
                # Review P2: ein Fehler-Dict (Task per ✕ geschlossen, requeued,
                # ungültige ID) ist KEIN Erfolg — der Zustand ist endgültig,
                # also nicht sinnlos retryn; das Ergebnis meldet der Aufrufer
                # sichtbar statt es als "abgeschlossen" zu verbuchen.
                return None, client, f"Server: {antwort['error']}"
            return antwort, client, None
        except (McpFehler, urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            fehler = f"{type(exc).__name__}: {exc}"
            client = None  # Session neu aufbauen
            if versuch < ABLIEFER_VERSUCHE - 1 and not HART.is_set():
                sicher_print(f"[{now()}] Ablieferung von {task_id} fehlgeschlagen "
                             f"({fehler}) — neuer Versuch in {ABLIEFER_PAUSE:.0f}s")
                HART.wait(ABLIEFER_PAUSE)  # Not-Aus bricht das Warten sofort ab
            if HART.is_set():
                break
    return None, client, fehler


# --- Zustellung in die interaktive Terminal-Sitzung (Issue #67) --------------
#
# Statt eines eigenen `claude --print` tippt der Watcher den Task in die
# laufende Claude-Code-Sitzung des Projekt-Terminals (tmux auf DIESEM
# Rechner). Der Mensch sieht live mit, kann dazwischenschreiben, und es gibt
# nur einen Verlauf. Ablauf je Task:
#   1. Sitzung finden: tmux-Fenster, dessen Ordner == Projektordner und in
#      dem Claude läuft. Keine → headless wie bisher.
#   2. Warten, bis sie frei ist: Hook-Datei (Stop/UserPromptSubmit) UND
#      Bildschirm (leere Eingabezeile, kein „esc to interrupt") sagen frei.
#      Frist = Wanduhr-Deckel des Tasks, danach headless.
#   3. Anspruch auf `sitzung` umschreiben, Text per Bracketed Paste + Enter.
#   4. Beobachten, bis der Task weg ist (complete_task aus der Sitzung).
#      Wird die Sitzung frei, ohne abzuschließen: EINE Erinnerung. Frist um
#      oder Sitzung weg: Fehlschlag „offen in der Sitzung" an den Absender.
#   5. Verbrauch aus dem Transkript nachtragen (zweites complete_task, #43).
# Gemessen am 25.11.2026 mit Claude Code 2.1.332 / tmux 3.4: Hooks in
# .claude/settings.local.json greifen auch in einer schon laufenden Sitzung;
# Esc (Unterbrechen) löst KEINEN Stop-Hook aus; mehrzeiliger Text muss als
# Bracketed Paste kommen (send-keys -l schickte jede Zeile ab).

SITZUNG_DIR = Path(os.environ.get("AGENT_DASHBOARD_SITZUNGEN")
                   or Path.home() / ".agent-dashboard" / "sitzungen")
SITZUNG_TAKT = 5.0          # s zwischen zwei Blicken auf die Sitzung
SITZUNG_BESTAETIGUNG = 15.0  # s bis der UserPromptSubmit-Hook die Zustellung bestätigt
SITZUNG_ERINNERN_NACH = 20.0  # s Ruhe nach dem Stop, bevor erinnert wird
SITZUNG_HAENGT = 60.0       # s Bildschirm frei trotz Hook „arbeitet" (Esc) → frei
HOOK_EREIGNISSE = ("UserPromptSubmit", "Stop", "SessionStart", "SessionEnd")
HOOK_ZUSTAND = {"UserPromptSubmit": "arbeitet", "Stop": "frei",
                "SessionStart": "frei", "SessionEnd": "zu"}
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
# gedimmter Text (Platzhalter/Vorschlag in der Eingabezeile) bis zum Reset
_DIM_RE = re.compile(r"\x1b\[2m.*?(?:\x1b\[(?:0|22)?m|$)")

# Kosten-Schätzung aus dem Transkript (USD je Million Tokens; Eingabe,
# Ausgabe, Cache-Lesen). Cache-Schreiben: 1,25× (5 min) bzw. 2× (1 h) Eingabe.
# Stand 25.11.2026 (Anthropic-Preisliste). Unbekanntes Modell → nur Tokens.
PREISE = (
    ("claude-fable-5-2", 8.0, 40.0, 0.20), ("claude-mythos-5-2", 8.0, 40.0, 0.20),
    ("claude-fable-5", 10.0, 50.0, 1.00), ("claude-mythos-5", 10.0, 50.0, 1.00),
    ("claude-opus-5-5", 4.0, 20.0, 0.20), ("claude-opus-5", 5.0, 25.0, 0.50),
    ("claude-opus-4", 5.0, 25.0, 0.50),
    ("claude-sonnet-5-5", 2.0, 10.0, 0.10), ("claude-sonnet-5", 2.0, 10.0, 0.20),
    ("claude-sonnet-4", 3.0, 15.0, 0.30),
    ("claude-haiku-5-5", 0.10, 0.50, 0.01), ("claude-haiku-4", 1.0, 5.0, 0.10),
)


def sitzung_meldung(zustand: str, text: str, task_id: str | None = None,
                    schwere: str = "info", **details) -> None:
    """Maschinenlesbare Zeile für den Automatik-Manager (→ Ereignis
    `zustellung` + Push) plus der Klartext fürs Panel-Log."""
    daten = {"zustand": zustand, "text": text, "task_id": task_id, "schwere": schwere,
             **{k: v for k, v in details.items() if v is not None}}
    sicher_print("@@zustellung " + json.dumps(daten, ensure_ascii=False))
    sicher_print(f"[{now()}] {(task_id + ' · ') if task_id else ''}{text}")


def sitzung_datei(cwd: "str | os.PathLike") -> Path:
    """Zustandsdatei je Projektordner (realpath), vom Hook geschrieben."""
    import hashlib
    schluessel = hashlib.sha1(os.path.realpath(str(cwd)).encode("utf-8")).hexdigest()[:16]
    return SITZUNG_DIR / f"{schluessel}.json"


def sitzung_hook(roh: str) -> int:
    """`agent_watcher.py --sitzung-hook`: von Claude Code als Hook gerufen
    (JSON auf stdin). Schreibt den Zustand der Sitzung; scheitert nie laut —
    ein Hook-Fehler darf die Sitzung des Menschen nicht stören."""
    try:
        d = json.loads(roh or "{}")
        ereignis = str(d.get("hook_event_name") or "")
        zustand = HOOK_ZUSTAND.get(ereignis)
        if not zustand or not d.get("cwd"):
            return 0
        SITZUNG_DIR.mkdir(parents=True, exist_ok=True)
        atomic_write_json(sitzung_datei(d["cwd"]), {
            "zustand": zustand, "ereignis": ereignis, "zeit": time.time(),
            "session_id": d.get("session_id"), "transcript": d.get("transcript_path"),
            "cwd": d.get("cwd"),
            # nur die ersten Zeichen: reicht, um die eigene Zustellung zu erkennen
            "prompt": str(d.get("prompt") or "")[:300] or None,
        })
    except Exception:  # noqa: BLE001 — Hook bleibt stumm
        pass
    return 0


def hook_befehl() -> str:
    import shlex
    return f"{shlex.quote(sys.executable)} {shlex.quote(str(Path(__file__).resolve()))} --sitzung-hook"


def hooks_einrichten(projekt: Path, befehl: "str | None" = None) -> bool:
    """Die vier Hooks in `<projekt>/.claude/settings.local.json` ergänzen
    (lokal, nicht eingecheckt). Fremde Einträge bleiben, doppelte entstehen
    nicht. True = Datei geändert. Kaputtes JSON wird NICHT überschrieben."""
    befehl = befehl or hook_befehl()
    pfad = projekt / ".claude" / "settings.local.json"
    try:
        daten = json.loads(pfad.read_text(encoding="utf-8")) if pfad.exists() else {}
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(daten, dict):
        return False
    hooks = daten.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        return False
    geaendert = False
    for ereignis in HOOK_EREIGNISSE:
        liste = hooks.setdefault(ereignis, [])
        if not isinstance(liste, list):
            return False
        da = any(isinstance(g, dict) and any(
            isinstance(h, dict) and "--sitzung-hook" in str(h.get("command") or "")
            for h in (g.get("hooks") or [])) for g in liste)
        if not da:
            liste.append({"hooks": [{"type": "command", "command": befehl, "timeout": 10}]})
            geaendert = True
    if geaendert:
        pfad.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(pfad, daten)
    return geaendert


def _tmux(*args: str, eingabe: "str | None" = None, timeout: float = 10.0) -> "str | None":
    try:
        r = subprocess.run(["tmux", *args], input=eingabe, capture_output=True,
                           text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def finde_sitzung(projekt: Path) -> "dict | None":
    """tmux-Fenster auf diesem Rechner, das im Projektordner steht und in
    dem Claude Code läuft (Prozessname `claude`, ältere npm-Fassung `node`)."""
    if not shutil.which("tmux"):
        return None
    aus = _tmux("list-panes", "-a", "-F",
                "#{session_name}\t#{pane_id}\t#{pane_current_path}\t#{pane_current_command}")
    if not aus:
        return None
    ziel = os.path.realpath(str(projekt))
    for zeile in aus.splitlines():
        teile = zeile.split("\t")
        if len(teile) != 4:
            continue
        sitzung, pane, pfad, befehl = teile
        if os.path.realpath(pfad) != ziel:
            continue
        if "claude" in befehl.lower() or befehl == "node":
            return {"sitzung": sitzung, "pane": pane, "pfad": ziel}
    return None


def bildschirm_lesen(text: str) -> dict:
    """Aus `capture-pane -p -e` (mit Farbcodes) lesen, ob Claude Code auf
    Eingabe wartet. Die Eingabe steht zwischen den beiden letzten
    waagerechten Linien; Platzhalter und Vorschläge sind gedimmt (\\e[2m) und
    zählen nicht als getippt. Läuft eine Antwort, steht „esc to interrupt"
    in der Fußzeile; Dialoge (Rechte, Vertrauen) haben keine Eingabebox."""
    zeilen = text.splitlines()
    roh = [_ANSI_RE.sub("", z) for z in zeilen]
    linien = [i for i, z in enumerate(roh) if z.strip() and set(z.strip()) <= {"─"}
              and len(z.strip()) >= 10]
    if len(linien) < 2:
        return {"frei": False, "grund": "keine Eingabezeile (Dialog offen?)"}
    oben, unten = linien[-2], linien[-1]
    fuss = " ".join(roh[unten + 1:]).lower()
    if "esc to interrupt" in fuss or "esc zum unterbrechen" in fuss:
        return {"frei": False, "grund": "Claude arbeitet"}
    eingabe = ""
    for z in zeilen[oben + 1:unten]:
        z = _ANSI_RE.sub("", _DIM_RE.sub("", z))
        eingabe += z.replace("❯", "").replace(">", "", 1).replace("\xa0", " ").strip()
    if not any("❯" in z or z.lstrip().startswith(">") for z in roh[oben + 1:unten]):
        return {"frei": False, "grund": "keine Eingabezeile (Dialog offen?)"}
    if eingabe:
        return {"frei": False, "grund": "Eingabe nicht leer (jemand tippt)"}
    return {"frei": True, "grund": "Eingabezeile leer"}


def hook_lesen(projekt: Path) -> "dict | None":
    try:
        d = json.loads(sitzung_datei(projekt).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def sitzung_pruefen(sitz: dict, projekt: Path, merker: dict) -> dict:
    """Ist die Sitzung frei? Nur wenn Hook UND Bildschirm es sagen.
    Ausnahme: Esc löst keinen Stop-Hook aus — steht der Hook auf „arbeitet",
    der Bildschirm aber seit SITZUNG_HAENGT durchgehend auf frei, gilt frei.
    → {frei, grund, hook} ; grund ist für Menschen."""
    bild_roh = _tmux("capture-pane", "-p", "-e", "-t", sitz["pane"])
    if bild_roh is None:
        return {"frei": False, "grund": "Sitzung weg", "weg": True}
    bild = bildschirm_lesen(bild_roh)
    hook = hook_lesen(projekt)
    if not bild["frei"]:
        merker.pop("bild_frei_seit", None)
        return {"frei": False, "grund": bild["grund"], "hook": hook}
    merker.setdefault("bild_frei_seit", time.monotonic())
    if hook is not None and hook.get("zustand") == "frei":
        return {"frei": True, "grund": "frei", "hook": hook}
    # Esc löst keinen Stop aus, und frisch eingetragene Hooks haben noch nichts
    # gemeldet (gemessen 25.11.2026: Datei fehlt, bis das erste Ereignis kommt)
    # → steht der Bildschirm lange genug durchgehend leer, gilt die Sitzung als frei.
    if (hook is None or hook.get("zustand") == "arbeitet") and \
            time.monotonic() - merker["bild_frei_seit"] >= SITZUNG_HAENGT:
        return {"frei": True, "grund": "frei (Bildschirm ruhig)", "hook": hook}
    return {"frei": False, "hook": hook,
            "grund": "wartet auf Hook-Meldung" if hook is None else f"Hook: {hook.get('zustand')}"}


def zustellen(sitz: dict, text: str) -> bool:
    """Text als Bracketed Paste einfügen, dann Enter. Ein eigener
    Puffername je Zustellung, -d räumt ihn danach weg."""
    puffer = f"dashboard-{uuid.uuid4().hex[:8]}"
    if _tmux("load-buffer", "-b", puffer, "-", eingabe=text) is None:
        return False
    if _tmux("paste-buffer", "-p", "-d", "-b", puffer, "-t", sitz["pane"]) is None:
        return False
    time.sleep(0.8)  # Claude Code verarbeitet das Einfügen, sonst geht Enter unter
    return _tmux("send-keys", "-t", sitz["pane"], "Enter") is not None


def zustell_text(agent: str, task_id: str, sender: "str | None", instruction: str,
                 frist: float) -> str:
    return (f"[Dashboard-Automatik · Task {task_id} von {sender or '?'} · "
            f"Frist {int(frist // 60)} min]\n"
            f"Bearbeite diesen Task und schließe ihn mit complete_task("
            f"task_id=\"{task_id}\", result=…) ab (Rückfrage: ask). "
            f"Du bist {agent}.\n\n{instruction}")


def erinnerung_text(task_id: str) -> str:
    return (f"[Dashboard-Automatik] Task {task_id} ist noch offen. Bitte mit "
            f"complete_task(task_id=\"{task_id}\", result=…) abschließen — oder mit "
            f"ask nachfragen, falls etwas fehlt.")


def _preis(modell: str) -> "tuple | None":
    for praefix, ein, aus, lesen in PREISE:
        if modell.startswith(praefix):
            return ein, aus, lesen
    return None


def transkript_verbrauch(pfad: "str | None", ab: float) -> dict:
    """usage der Assistant-Nachrichten ab Zeitpunkt `ab` (Unix-Zeit) summieren.
    Claude Code schreibt eine API-Antwort als mehrere Zeilen mit DERSELBEN
    usage (je Inhaltsblock) → je message.id nur einmal zählen. Kosten sind
    eine Schätzung nach PREISE (im Abo sind es keine echten Ausgaben)."""
    summe = {"input_tokens": 0, "output_tokens": 0,
             "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
    if not pfad:
        return {}
    je_nachricht: dict = {}
    try:
        with open(pfad, encoding="utf-8") as f:
            for zeile in f:
                try:
                    d = json.loads(zeile)
                except json.JSONDecodeError:
                    continue
                if d.get("type") != "assistant" or not isinstance(d.get("message"), dict):
                    continue
                try:
                    t = datetime.fromisoformat(str(d.get("timestamp")).replace("Z", "+00:00"))
                except ValueError:
                    continue
                if t.timestamp() < ab:
                    continue
                m = d["message"]
                if isinstance(m.get("usage"), dict):
                    je_nachricht[m.get("id") or d.get("uuid")] = (str(m.get("model") or ""),
                                                                  m["usage"])
    except OSError:
        return {}
    kosten, bekannt, modell = 0.0, True, ""
    for modell_n, u in je_nachricht.values():
        modell = modell_n or modell
        werte = {f: int(_zahl(u.get(f))) for f in summe}
        for f, w in werte.items():
            summe[f] += w
        preis = _preis(modell_n)
        if preis is None:
            bekannt = False
            continue
        ein, aus, lesen = preis
        cc = u.get("cache_creation") if isinstance(u.get("cache_creation"), dict) else {}
        eine_std = int(_zahl(cc.get("ephemeral_1h_input_tokens")))
        fuenf = max(0, werte["cache_creation_input_tokens"] - eine_std)
        kosten += (werte["input_tokens"] * ein + werte["output_tokens"] * aus
                   + werte["cache_read_input_tokens"] * lesen
                   + fuenf * ein * 1.25 + eine_std * ein * 2.0) / 1e6
    if not je_nachricht:
        return {}
    erg: dict = dict(summe)
    if bekannt:
        erg["total_cost_usd"] = round(kosten, 6)
        erg["kosten_geschaetzt"] = True
    erg["modell"] = modell or None
    return erg


def _task_offen(client: "McpClient | None", task_id: str) -> "bool | None":
    """Liegt der Task noch beansprucht beim Agenten? None = unklar (Netz)."""
    if client is None:
        return None
    try:
        r = client.call("claim_task", {"task_id": task_id, "erneut": True})
    except Exception:  # noqa: BLE001
        return None
    if isinstance(r, dict) and r.get("error"):
        return False if "liegt nicht" in str(r["error"]) else None
    return isinstance(r, dict)


def in_sitzung_bearbeiten(client: "McpClient", agent: str, task_id: str,
                          projekt: Path, instruction: str, sender: "str | None",
                          frist: float, push: bool = False) -> dict:
    """Ein beanspruchter Task über die Terminal-Sitzung. Ergebnis:
      {"weg": "headless", "grund": …}         → Aufrufer startet den normalen Lauf
      {"weg": "sitzung", "status": "erledigt"|"offen"|"weg"|"stopp", …}
    Bei „offen"/„weg" liefert der Aufrufer den Fehlschlag ab, bei „erledigt"
    trägt er den Verbrauch nach, bei „stopp" gibt er den Task zurück."""
    sitz = finde_sitzung(projekt)
    if sitz is None:
        return {"weg": "headless", "grund": "keine Claude-Sitzung im Projekt-Terminal offen"}
    if hooks_einrichten(projekt):
        sitzung_meldung("hooks", f"Sitzungs-Hooks in {projekt}/.claude/settings.local.json "
                        f"eingetragen", task_id)
    merker: dict = {}
    start = time.monotonic()
    herz = time.monotonic()
    gemeldet = False
    while True:
        if HART.is_set() or STOP.is_set():
            return {"weg": "sitzung", "status": "stopp"}
        lage = sitzung_pruefen(sitz, projekt, merker)
        if lage.get("weg"):
            return {"weg": "headless", "grund": "Sitzung wurde geschlossen"}
        if lage["frei"]:
            break
        if not gemeldet and time.monotonic() - start >= 2 * SITZUNG_TAKT:
            sitzung_meldung("wartet", f"wartet auf freie Sitzung „{sitz['sitzung']}“ "
                            f"({lage['grund']})", task_id, sitzung=sitz["sitzung"])
            gemeldet = True
        if time.monotonic() - start >= frist:
            return {"weg": "headless", "grund": f"Sitzung {int(frist // 60)} min belegt"}
        if time.monotonic() - herz >= HERZSCHLAG:
            herz = time.monotonic()
            try:
                client.call("claim_task", {"task_id": task_id, "erneut": True, "watcher": True})
            except Exception:  # noqa: BLE001
                pass
        STOP.wait(SITZUNG_TAKT)
    # Ab hier gehört der Task der Sitzung (24-h-Regel statt 3-h, Issue #46).
    try:
        client.call("claim_task", {"task_id": task_id, "erneut": True, "sitzung": True})
    except Exception:  # noqa: BLE001 — Anspruch bleibt dann beim Watcher, harmlos
        pass
    zugestellt = time.time()
    if not zustellen(sitz, zustell_text(agent, task_id, sender, instruction, frist)):
        return {"weg": "headless", "grund": "tmux-Zustellung gescheitert"}
    # Bestätigung: der UserPromptSubmit-Hook mit unserer Task-ID
    bestaetigt = False
    for versuch in range(2):
        grenze = time.monotonic() + SITZUNG_BESTAETIGUNG
        while time.monotonic() < grenze and not HART.is_set():
            h = hook_lesen(projekt) or {}
            if h.get("zeit", 0) >= zugestellt - 1 and task_id in str(h.get("prompt") or "") \
                    or (h.get("zeit", 0) >= zugestellt - 1 and h.get("zustand") == "arbeitet"):
                bestaetigt = True
                break
            time.sleep(1.0)
        if bestaetigt or versuch:
            break
        _tmux("send-keys", "-t", sitz["pane"], "Enter")  # Enter ging unter
    hook = hook_lesen(projekt) or {}
    sitzung_meldung("zugestellt" if bestaetigt else "unbestaetigt",
                    (f"in Sitzung „{sitz['sitzung']}“ zugestellt" if bestaetigt else
                     f"in Sitzung „{sitz['sitzung']}“ getippt, aber kein Hook hat es "
                     f"bestätigt — im Terminal nachsehen; ältere Claude-Code-Fassungen "
                     f"lesen neue Hooks erst nach einem Neustart (/exit, claude --continue)"),
                    task_id, schwere="info" if bestaetigt else "warnung",
                    sitzung=sitz["sitzung"], projekt_pfad=str(projekt), push=push or None)
    erinnert = False
    ruhe_seit: "float | None" = None
    ende = time.monotonic() + frist
    while True:
        if HART.is_set():
            return {"weg": "sitzung", "status": "stopp_hart", "ab": zugestellt}
        offen = _task_offen(client, task_id)
        if offen is False:
            hook = hook_lesen(projekt) or hook
            return {"weg": "sitzung", "status": "erledigt", "ab": zugestellt,
                    "session_id": hook.get("session_id"), "transcript": hook.get("transcript")}
        if _tmux("has-session", "-t", "=" + sitz["sitzung"]) is None:
            return {"weg": "sitzung", "status": "weg", "ab": zugestellt}
        if time.monotonic() >= ende:
            return {"weg": "sitzung", "status": "offen", "ab": zugestellt,
                    "session_id": hook.get("session_id"), "transcript": hook.get("transcript")}
        lage = sitzung_pruefen(sitz, projekt, merker)
        h = lage.get("hook") or {}
        nach_stop = lage["frei"] and h.get("zeit", 0) > zugestellt
        if nach_stop and not erinnert:
            ruhe_seit = ruhe_seit or time.monotonic()
            if time.monotonic() - ruhe_seit >= SITZUNG_ERINNERN_NACH and offen is True:
                if zustellen(sitz, erinnerung_text(task_id)):
                    erinnert = True
                    sitzung_meldung("erinnert", "Sitzung frei, Task noch offen — "
                                    "Erinnerung getippt", task_id, schwere="warnung")
        elif not lage["frei"]:
            ruhe_seit = None
        STOP.wait(SITZUNG_TAKT) if not STOP.is_set() else time.sleep(SITZUNG_TAKT)


def sitzung_abschliessen(client: "McpClient | None", url: str, task_id: str, weg: dict,
                         frist: float) -> tuple:
    """Nachlauf einer Sitzungs-Zustellung (Issue #67) → wie liefere_ergebnis
    (antwort, client, fehler)."""
    status = weg["status"]
    if status in ("stopp", "stopp_hart"):
        if status == "stopp" and client is not None:
            # noch nicht zugestellt: zurück in die Inbox statt 3 h verwaist
            try:
                client.call("claim_task", {"task_id": task_id, "zurueckgeben": True})
                sitzung_meldung("zurueck", "Automatik gestoppt — Task zurück in die Inbox",
                                task_id)
            except Exception:  # noqa: BLE001
                pass
        return None, client, None
    verbrauch = transkript_verbrauch(weg.get("transcript"), weg.get("ab") or time.time())
    lauf = {"zustellung": "sitzung", "session_id": weg.get("session_id"),
            "modell": verbrauch.pop("modell", None) if verbrauch else None}
    lauf = {k: v for k, v in lauf.items() if v}
    if status == "erledigt":
        # Zweites complete_task: der Task ist schon abgeschlossen, der Server
        # trägt nur Verbrauch und Lauf-Daten nach (Issue #43).
        antwort, client, fehler = liefere_ergebnis(client, url, task_id, "", "done", "",
                                                   verbrauch or None, lauf)
        kosten = (verbrauch or {}).get("total_cost_usd")
        sitzung_meldung("abgeschlossen", "in der Sitzung abgeschlossen"
                        + (f" · ca. {kosten:.2f} $" if isinstance(kosten, (int, float)) else ""),
                        task_id)
        return antwort, client, None
    grund = ("die Terminal-Sitzung wurde geschlossen" if status == "weg" else
             f"nach {int(frist // 60)} min nicht mit complete_task abgeschlossen")
    result = (f"[watcher] Task in die Terminal-Sitzung zugestellt, aber {grund}. "
              f"Stand bitte in der Sitzung des Agenten prüfen"
              + (f" (claude --resume {weg['session_id']})." if weg.get("session_id") else "."))
    sitzung_meldung("offen", f"offen in der Sitzung — {grund}", task_id, schwere="warnung")
    return liefere_ergebnis(client, url, task_id, result, "error", grund,
                            verbrauch or None, lauf)


def mcp_loop(url: str, agent: str, claude_bin: str, workdir: Path, interval: float,
             dry_run: bool, with_mcp_hint: bool, once: bool = False,
             permission_mode: str | None = None,
             allowed_tools: str | None = None,
             resume: dict | None = None,
             einst: dict | None = None) -> int:
    """Poll-Schleife über MCP: inbox → claim_task → claude → complete_task.

    Verbindungsfehler (Tunnel weg, Server-Neustart) werden mit Abstand erneut
    versucht — der Watcher stirbt nicht, solange ihn niemand stoppt."""
    client: McpClient | None = None
    letzter_fehler: str | None = None
    einst = einst or {}
    pause = interval  # wächst im Leerlauf bis POLL_MAX (Issue #41)
    kosten_ruhe = 0.0  # bis dahin keine Claims (Tagesgrenze, Issue #55)
    while not STOP.is_set():
        bearbeitet = 0
        if time.monotonic() < kosten_ruhe:
            STOP.wait(min(POLL_MAX, max(1.0, kosten_ruhe - time.monotonic())))
            continue
        try:
            if client is None:
                client = McpClient(url)
                client.connect()
                sicher_print(f"[{now()}] MCP verbunden: {url}")
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
                # Geplante Tasks überspringen (Review N): der claim würde eh
                # mit zu_frueh abgelehnt — das verrauschte nur das Kanal-Log.
                nicht_vor = env.get("nicht_vor")
                if nicht_vor:
                    try:
                        ziel = datetime.fromisoformat(
                            str(nicht_vor).replace("Z", "+00:00"))
                        if ziel.tzinfo is None:
                            ziel = ziel.astimezone()
                        if ziel.timestamp() > time.time():
                            continue
                    except ValueError:
                        pass
                task_id = env.get("id") or env.get("task_id")
                if not task_id:
                    continue
                # `watcher` (Issue #46): nur gekennzeichnete Watcher-Ansprüche
                # zählen beim Server für die Aufgabe-Grenze der Pflege. Ein
                # älterer Server ignoriert das unbekannte Argument.
                claimed = client.call("claim_task", {"task_id": task_id, "watcher": True})
                if isinstance(claimed, dict) and claimed.get("kosten_gesperrt"):
                    # Tagesgrenze erreicht (Issue #55): nicht jeden Takt neu
                    # anfragen — der Mensch entscheidet, das dauert.
                    if time.monotonic() >= kosten_ruhe:
                        sicher_print(f"[{now()}] {agent}: Tageskosten-Grenze erreicht — "
                                     f"wartet auf Freigabe im Dashboard")
                    kosten_ruhe = time.monotonic() + KOSTEN_RUHE
                    break
                if not isinstance(claimed, dict) or claimed.get("error"):
                    continue  # schon von jemand anderem beansprucht
                instruction = claimed.get("instruction") or env.get("text") or ""
                projekt = claimed.get("project") or env.get("project")
                # Rollen-Felder kommen verbindlich aus dem claim (inbox()
                # normalisiert sie weg); Schnittmenge mit den Agenten-Rechten
                # aus der eigenen Kommandozeile — nie erweitern (St.1).
                rollen_name = claimed.get("rolle")
                pm, at = wirksame_rechte(permission_mode, allowed_tools,
                                         claimed.get("rollen_permission_mode"),
                                         claimed.get("rollen_tools"))
                sicher_print(f"[{now()}] {agent}: bearbeite {task_id}"
                             + (f" (project {projekt})" if projekt else "")
                             + (f" (rolle {rollen_name})" if rollen_name else ""))
                if claimed.get("rollen_tools") and not at:
                    # Review P2: leere Schnittmenge sichtbar machen — der Lauf
                    # scheiterte sonst opak an "Berechtigung verweigert".
                    sicher_print(f"[{now()}] {agent}: {task_id} — Rolle "
                                 f"{rollen_name or '?'}: kein Agent-Werkzeug in "
                                 f"der Schnittmenge, Lauf nutzt nur Auto-Werkzeuge")
                bearbeitet += 1
                # Der MCP-Hinweis kommt in run_claude_sitzung dazu — sein Wortlaut
                # hängt davon ab, ob eine Sitzung fortgesetzt wird.
                herz = {"zuletzt": time.monotonic()}

                def fortschritt(text: str, _tid: str = task_id) -> None:
                    # Fließt via stdout ins Automatik-Panel (Issue #18); eine
                    # tote Leitung darf den Lauf nicht abbrechen (H3).
                    sicher_print(f"[{now()}] {agent}: {_tid} · {text}")
                    # Lebenszeichen (Issue #42): solange der Lauf Fortschritt
                    # meldet, frischt ein erneuter claim den Anspruch auf —
                    # die Pflege hält den Task dann nicht für verwaist.
                    if time.monotonic() - herz["zuletzt"] >= HERZSCHLAG:
                        herz["zuletzt"] = time.monotonic()
                        try:
                            if client is not None:
                                client.call("claim_task", {"task_id": _tid, "erneut": True,
                                                           "watcher": True})
                        except Exception:  # noqa: BLE001 — nur ein Lebenszeichen
                            pass

                modell, modell_quelle = wirksames_modell(
                    einst.get("model"), claimed.get("rollen_model"), claimed.get("model"),
                    einst.get("modelle_erlaubt"), einst.get("model_nachlauf"),
                    bool(claimed.get("nachtraege") or claimed.get("weckruf")))
                wahl = projekt_aufloesen(workdir, projekt, einst.get("project_fallback"),
                                         bool(claimed.get("nachtraege")))
                task_dir, wd_fehler = wahl["dir"], wahl["fehler"]
                if wahl["hinweis"]:
                    instruction = wahl["hinweis"] + "\n\n" + instruction
                    sicher_print(f"[{now()}] {agent}: {task_id} · {wahl['hinweis']}")
                start = time.monotonic()
                verbrauch: dict = {}  # usage/total_cost_usd aus dem result-Event (St.3)
                lauf_meta: dict = {}  # Sitzung/Timeout/Verweigerungen → Antwort
                gefragt = False
                if wahl["frage"]:
                    # Issue #54: Tippfehler/Freitext im project-Feld → Rückfrage
                    # an den Auftraggeber; der Server parkt den Task (#17) und
                    # reiht ihn nach der Antwort wieder ein.
                    wem = claimed.get("sender") or env.get("sender") or "orchestrator"
                    try:
                        frage = client.call("ask", {"to": wem, "question": wahl["frage"]})
                        gefragt = isinstance(frage, dict) and not frage.get("error")
                    except Exception:  # noqa: BLE001 — dann eben der harte Fehler
                        gefragt = False
                if gefragt:
                    result, err, status = (f"Wartet auf Antwort: {wahl['frage']}", "", "done")
                    lauf_meta["fehlstart"] = "Projektverzeichnis unklar — Rückfrage gestellt"
                    sicher_print(f"[{now()}] {agent}: {task_id} · Rückfrage an {wem}: "
                                 f"project {projekt!r} fehlt")
                elif wd_fehler:  # falsches Verzeichnis wäre schlimmer als Abbruch (#19)
                    result, err, status = "", wd_fehler, "error"
                    lauf_meta["fehlstart"] = wd_fehler
                if not gefragt and not wd_fehler and einst.get("zustellung") == "sitzung":
                    # Issue #67: erst die Terminal-Sitzung, headless nur als Rückfall
                    frist = wirksamer_timeout(einst.get("timeout"), claimed.get("timeout"))
                    weg = in_sitzung_bearbeiten(
                        client, agent, task_id, task_dir, instruction,
                        claimed.get("sender") or env.get("sender"), frist,
                        push=bool(einst.get("zustellung_push")))
                    if weg["weg"] == "headless":
                        sitzung_meldung("rueckfall", f"läuft im Hintergrund: {weg['grund']}",
                                        task_id, schwere="info" if "keine" in weg["grund"]
                                        else "warnung")
                    else:
                        fertig, client, liefer_fehler = sitzung_abschliessen(
                            client, url, task_id, weg, frist)
                        if weg["status"] in ("stopp", "stopp_hart"):
                            break
                        continue
                if gefragt or wd_fehler:
                    pass
                else:
                    try:
                        result, err, rc = run_claude_sitzung(
                            resume, agent, task_id, claude_bin, instruction,
                            task_dir, dry_run, fortschritt, pm, at,
                            claimed.get("rollen_prompt"), rollen_name,
                            verbrauch, with_mcp_hint,
                            thread=claimed.get("thread") or env.get("thread"),
                            timeout=wirksamer_timeout(einst.get("timeout"),
                                                      claimed.get("timeout")),
                            leerlauf=einst.get("leerlauf"),
                            lauf_meta=lauf_meta,
                            model=modell, fallback_model=einst.get("fallback_model"),
                            model_quelle=modell_quelle)
                        status = "done" if rc == 0 else "error"
                    except Exception as exc:  # noqa: BLE001 — alles zurückmelden
                        result, err, status = "", repr(exc), "error"
                dauer = time.monotonic() - start
                if status == "error":
                    result = fehler_result(result, err)
                # Ablieferung vom Poll-Loop entkoppeln (M8): das Ergebnis von
                # bis zu 30 min Arbeit darf nicht verloren gehen, nur weil der
                # Tunnel gerade neu verbindet oder inzwischen "stop" kam.
                fertig, client, liefer_fehler = liefere_ergebnis(
                    client, url, task_id, result, status, err, verbrauch, lauf_meta)
                if liefer_fehler:
                    sicher_print(f"[{now()}] {agent}: {task_id} — Ergebnis konnte nicht "
                                 f"abgeliefert werden ({liefer_fehler}); Task bleibt beim "
                                 f"Server als laufend. Ergebnis: {kurz(result, 500)}")
                if isinstance(fertig, dict) and fertig.get("parked"):
                    # Rückfrage offen (Issue #17): Server hat den Task geparkt,
                    # nach der Antwort landet er automatisch wieder in der Inbox.
                    sicher_print(f"[{now()}] {agent}: {task_id} wartet auf Antwort "
                                 f"einer Rückfrage (geparkt)")
                elif not liefer_fehler:
                    sicher_print(f"[{now()}] {agent}: {task_id} abgeschlossen ({status})")
                if fehlerserie(status, dauer):
                    sicher_print(f"[{now()}] {agent}: {FEHLER_SCHWELLE} Tasks in Folge sofort "
                                 f"gescheitert — Umgebungsproblem vermutet, Watcher hält an. "
                                 f"Letzter Fehler: {err[:300]}")
                    return 1
            if once:
                break
        except (McpFehler, urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            client = None  # Session neu aufbauen
            msg = f"{type(exc).__name__}: {exc}"
            if msg != letzter_fehler:  # nur Zustandswechsel loggen
                sicher_print(f"[{now()}] MCP-Fehler: {msg} — neuer Versuch "
                             f"alle {max(interval, 10):.0f}s")
                letzter_fehler = msg
            if once:
                return 1
            STOP.wait(max(interval, 10))
            continue
        # Leerlauf-Backoff (Issue #41): ein unbeschäftigter Watcher fragte alle
        # 5 s — über 1.200 Aufrufe in zwei Stunden, je zwei Logzeilen. Ohne
        # Arbeit streckt sich der Takt bis POLL_MAX, nach Arbeit ist er kurz.
        pause = interval if bearbeitet else min(max(pause * 1.5, interval), max(POLL_MAX, interval))
        STOP.wait(pause)
    sicher_print(f"[{now()}] Watcher beendet.")
    return 0


def inbox_tasks(inbox: Path) -> list[Path]:
    """Task-Dateien der Inbox in FIFO-Reihenfolge (N1).

    Sortiert nach `created_at`, nicht nach dem zufälligen uuid-Dateinamen —
    sonst bestimmt der Zufall, welcher Auftrag zuerst läuft. Envelopes ohne
    Zeitstempel hängen sich hinten an. Nicht-Tasks (message/question/answer)
    bleiben liegen: die liest der Koordinator bzw. das Dashboard."""
    eintraege: list[tuple[str, str, Path]] = []
    for pfad in inbox.glob("*.json"):
        try:
            env = json.loads(pfad.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            # Review N: unparsbare Task-Datei verschwand lautlos für immer in
            # der Inbox (pflege fasst Tasks nicht an, das UI zeigte nichts).
            # Nach .failed verschieben macht sie sichtbar und beendet die Schleife.
            failed = inbox / ".failed"
            try:
                failed.mkdir(exist_ok=True)
                os.replace(pfad, failed / pfad.name)
                sicher_print(f"[{now()}] kaputte Envelope-Datei nach .failed "
                             f"verschoben: {pfad.name}")
            except OSError:
                pass
            continue
        except OSError:
            continue
        if not isinstance(env, dict) or env.get("kind", "task") != "task":
            continue
        # Geplante Tasks (nicht_vor, Paket St.2) liegen lassen, bis die Zeit
        # kommt — die Inbox ist der Wartepuffer. Kaputte Zeitstempel frieren
        # den Task NICHT ein (dann lieber ausführen).
        nicht_vor = env.get("nicht_vor")
        if nicht_vor:
            try:
                # "Z" normalisieren (P1-5): Python ≤3.10 wirft sonst ValueError
                # und der geplante Task liefe SOFORT. Der Server friert den
                # Zeitpunkt seit P1-5 tz-behaftet ein; naive Altwerte fallen
                # auf die lokale Zeit dieses PCs zurück (best effort).
                ziel = datetime.fromisoformat(
                    str(nicht_vor).replace("Z", "+00:00"))
                if ziel.tzinfo is None:
                    ziel = ziel.astimezone()
                if ziel.timestamp() > time.time():
                    continue
            except ValueError:
                pass
        eintraege.append((str(env.get("created_at") or ""), pfad.name, pfad))
    eintraege.sort(key=lambda e: (e[0] == "", e[0], e[1]))
    return [e[2] for e in eintraege]


def process_once(inbox: Path, processing: Path, outbox: Path,
                 agent: str, claude_bin: str, workdir: Path, dry_run: bool,
                 with_mcp_hint: bool = False,
                 permission_mode: str | None = None,
                 allowed_tools: str | None = None,
                 resume: dict | None = None,
                 einst: dict | None = None) -> int:
    """Gibt die Zahl bearbeiteter Tasks zurück; -1 = Fehlerserie, bitte anhalten."""
    handled = 0
    einst = einst or {}
    for task_path in inbox_tasks(inbox):
        if STOP.is_set():
            # "stop" (oder Not-Aus) wirkt sofort, nicht erst nach dem ganzen
            # Stapel — 5 Tasks à 30 min wären sonst 2,5 h Weiterarbeit (M14).
            break
        claimed = processing / task_path.name
        with mailbox_lock(inbox.parent):  # P1-10: wie der Server sperren
            try:
                os.replace(task_path, claimed)  # atomarer, exklusiver Anspruch
            except FileNotFoundError:
                continue
            try:
                task = json.loads(claimed.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue  # nächster Tick

            # Anspruch stempeln (Review P0-2): os.replace erhält die mtime, und
            # requeue_stale misst ohne `claimed_at` ab Dateialter — ein Task mit
            # >3 h Inbox-Liegezeit (bei nicht_vor der Normalfall) gälte sofort
            # als verwaist und liefe doppelt.
            task["claimed_at"] = now()
            task["claim_von"] = "watcher"   # Issue #46, s. Mailbox.claim_task
            task["status"] = "running"
            try:
                atomic_write_json(claimed, task)
            except OSError:
                pass  # Stempel ist Schutz, kein Grund den Lauf abzubrechen

        task_id = task.get("task_id", claimed.stem)
        sender = task.get("sender") or ""
        root = inbox.parent.parent  # root/<agent>/inbox → Mailbox-Wurzel
        sicher_print(f"[{now()}] {agent}: bearbeite {task_id}")
        start = time.monotonic()
        status, err = "error", ""
        verbrauch: dict = {}  # usage/total_cost_usd aus dem result-Event (St.3)
        lauf_meta: dict = {}  # Sitzung/Timeout/Verweigerungen → Antwort
        if task.get("rollen_tools"):
            _pm_probe, _at_probe = wirksame_rechte(
                permission_mode, allowed_tools,
                task.get("rollen_permission_mode"), task.get("rollen_tools"))
            if not _at_probe:
                sicher_print(f"[{now()}] {agent}: {task_id} — Rolle "
                             f"{task.get('rolle') or '?'}: kein Agent-Werkzeug "
                             f"in der Schnittmenge, Lauf nutzt nur Auto-Werkzeuge")
        try:
            task["instruction"]  # fehlende instruction soll wie bisher scheitern
            instruction = merge_instruction(task)  # MCP-Hinweis: run_claude_sitzung
            modell, modell_quelle = wirksames_modell(
                einst.get("model"), task.get("rollen_model"), task.get("model"),
                einst.get("modelle_erlaubt"), einst.get("model_nachlauf"),
                bool(task.get("nachtraege") or task.get("weckruf")))
            # Datei-Transport: keine Rückfrage möglich — Namens-Treffer und
            # `project_fallback: basis` greifen trotzdem (#54).
            wahl = projekt_aufloesen(workdir, task.get("project"),
                                     einst.get("project_fallback"),
                                     bool(task.get("nachtraege")))
            task_dir, wd_fehler = wahl["dir"], wahl["fehler"]
            if wahl["hinweis"]:
                instruction = wahl["hinweis"] + "\n\n" + instruction
            # Rollen-Felder liegen beim Dateitransport direkt im Envelope;
            # Schnittmenge mit den Agenten-Rechten — nie erweitern (St.1).
            pm, at = wirksame_rechte(permission_mode, allowed_tools,
                                     task.get("rollen_permission_mode"),
                                     task.get("rollen_tools"))
            if wd_fehler:  # falsches Verzeichnis wäre schlimmer als Abbruch (#19)
                err = wd_fehler
                lauf_meta["fehlstart"] = wd_fehler
                result = fehler_result("", err)
            else:
                result, err, rc = run_claude_sitzung(
                    resume, agent, task_id, claude_bin, instruction,
                    task_dir, dry_run,
                    lambda text, _tid=task_id: sicher_print(
                        f"[{now()}] {agent}: {_tid} · {text}"),
                    pm, at, task.get("rollen_prompt"), task.get("rolle"),
                    verbrauch, with_mcp_hint,
                    thread=task.get("thread"),
                    timeout=wirksamer_timeout(einst.get("timeout"), task.get("timeout")),
                    leerlauf=einst.get("leerlauf"),
                    lauf_meta=lauf_meta,
                    model=modell, fallback_model=einst.get("fallback_model"),
                    model_quelle=modell_quelle)
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
                    with mailbox_lock(inbox.parent):  # P1-10
                        env = json.loads(claimed.read_text(encoding="utf-8"))
                        env.update(status="needs_confirm", open_questions=offen)
                        if result:
                            env["zwischenstand"] = result
                        atomic_write_json(claimed, env)
                    sicher_print(f"[{now()}] {agent}: {task_id} wartet auf "
                                 f"Antwort einer Rückfrage (geparkt)")
                    handled += 1
                    continue
                except (json.JSONDecodeError, OSError):
                    pass  # Envelope nicht lesbar — dann regulär abschließen
        antwort = {"task_id": task_id, "agent": agent, "to": sender or None,
                   "result": result, "status": status, "log": err,
                   "responded_at": now()}
        if verbrauch:  # Verbrauchszähler (St.3) liest genau dieses Feld
            antwort["verbrauch"] = verbrauch
        if lauf_meta:
            antwort["lauf"] = lauf_meta
        if status == "error" and task.get("instruction"):
            # Fehlschlag: die einzige Kopie der Aufgabenbeschreibung darf
            # nicht verloren gehen (Issue #15).
            antwort["instruction"] = task["instruction"]
        # Review P0-3: Erst wenn das Ergebnis SICHER in der Outbox liegt, wird
        # der Task abgeräumt. Ein Outbox-Schreibfehler (SSHFS-Blip, ENOSPC)
        # warf über das frühere `finally` 30 min Arbeit spurlos weg — jetzt
        # bleibt der Task in .processing, requeue_stale legt ihn zurück.
        try:
            atomic_write_json(outbox / f"{task_id}-response.json", antwort)
        except OSError as exc:
            sicher_print(f"[{now()}] {agent}: {task_id} — Outbox nicht "
                         f"schreibbar ({exc}); Task bleibt in Arbeit. "
                         f"Ergebnis: {kurz(result, 500)}")
            continue
        deliver_response(root, sender, agent, task_id, result, status,
                         antwort.get("instruction"))
        try:
            with mailbox_lock(inbox.parent):  # P1-10
                if status == "error":
                    failed = inbox / ".failed"
                    failed.mkdir(exist_ok=True)
                    os.replace(claimed, failed / claimed.name)  # Wiederanlauf (#15)
                else:
                    claimed.unlink(missing_ok=True)
        except OSError as exc:  # z.B. PermissionError auf Windows-Mounts
            sicher_print(f"[{now()}] {agent}: {task_id} — Aufräumen "
                         f"fehlgeschlagen ({exc}); Ergebnis liegt in der Outbox.")
        handled += 1
        sicher_print(f"[{now()}] {agent}: {task_id} abgeschlossen ({status})")
        if fehlerserie(status, time.monotonic() - start):
            print(f"[{now()}] {agent}: {FEHLER_SCHWELLE} Tasks in Folge sofort "
                  f"gescheitert — Umgebungsproblem vermutet, Watcher hält an. "
                  f"Letzter Fehler: {err[:300]}", flush=True)
            return -1
    return handled


def main() -> int:
    if "--sitzung-hook" in sys.argv[1:]:
        # Claude-Code-Hook der Terminal-Sitzung (Issue #67): kein Watcher-Start
        try:
            roh = sys.stdin.read()
        except (OSError, ValueError):
            roh = ""
        return sitzung_hook(roh)
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", required=True)
    ap.add_argument("--root", help="Mailbox-Wurzel (enthält <agent>/) — Datei-Transport")
    ap.add_argument("--mcp-url", help="MCP-Endpunkt (http://127.0.0.1:<mcp_port>/mcp) — "
                                      "Transport über den Reverse-Tunnel, kein Mount nötig")
    ap.add_argument("--workdir", default=".", help="Arbeitsverzeichnis für Claude-Code")
    ap.add_argument("--claude-bin", default="claude",
                    help="Claude-Binary (Name oder Pfad); Default: 'claude' im PATH "
                         "bzw. den üblichen Installationsorten (~/.local/bin …)")
    ap.add_argument("--permission-mode",
                    help="an claude --permission-mode durchgereicht (z.B. "
                         "acceptEdits) — headless kann niemand Freigabe-"
                         "Rückfragen beantworten (Issue #19)")
    ap.add_argument("--allowed-tools",
                    help="Komma-getrennte Liste für claude --allowed-tools, "
                         "z.B. 'Edit,Write,Bash(git:*)' (Issue #19)")
    ap.add_argument("--no-resume", action="store_true",
                    help="jeden Task in einer frischen claude-Sitzung ausführen "
                         "(Default: Sitzung je Verzeichnis + Rolle fortsetzen)")
    ap.add_argument("--resume-max-pause", type=float, default=RESUME_MAX_PAUSE,
                    help="Sekunden seit dem letzten Lauf, bis zu denen eine "
                         f"Sitzung fortgesetzt wird (Default {RESUME_MAX_PAUSE:.0f})")
    ap.add_argument("--resume-max-kontext", type=int, default=RESUME_MAX_KONTEXT,
                    help="Kontextgröße in Tokens, ab der eine neue Sitzung "
                         f"beginnt (Default {RESUME_MAX_KONTEXT} = "
                         f"{int(KONTEXT_FENSTER_ANTEIL * 100)} %% des Fensters "
                         "des Sitzungs-Modells; unbekanntes Modell = keine Grenze)")
    ap.add_argument("--timeout", type=float, default=CLAUDE_TIMEOUT,
                    help="Wanduhr-Deckel je Lauf in Sekunden "
                         f"(Default {CLAUDE_TIMEOUT:.0f}; ein Task darf ihn nur senken)")
    ap.add_argument("--leerlauf", type=float, default=CLAUDE_LEERLAUF,
                    help="Abbruch nach so vielen Sekunden ohne stream-json-Event "
                         f"(Default {CLAUDE_LEERLAUF:.0f}; 0 = aus)")
    ap.add_argument("--resume-kalt-minuten", type=float, default=RESUME_KALT_MINUTEN,
                    help="Pause in Minuten, ab der der Prompt-Cache als kalt gilt "
                         "(Issue #55); 0 = Regel aus")
    ap.add_argument("--resume-kompakt-ab", type=int, default=RESUME_KOMPAKT_AB,
                    help="Kontext in Tokens, ab dem eine KALTE Sitzung nicht fortgesetzt "
                         "wird, sondern neu mit Übergabe beginnt; 0 = Regel aus")
    ap.add_argument("--erwartet-env", action="store_true",
                    help="vor dem ersten Task kurz auf die Geheimnisse vom Dashboard "
                         "warten (stdin-Zeile `env …`, Issue #64)")
    ap.add_argument("--freigabe-tool", default=None,
                    help="MCP-Tool für Berechtigungs-Rückfragen, z.B. "
                         "mcp__dashboard__freigabe (Issue #56)")
    ap.add_argument("--freigabe-timeout", type=float, default=900.0,
                    help="so lange wartet das Freigabe-Tool auf den Menschen (s)")
    ap.add_argument("--model", default=None,
                    help="Modell der Läufe (claude --model), z.B. sonnet (Issue #62)")
    ap.add_argument("--fallback-model", default=None,
                    help="Ausweichmodell bei Überlast (claude --fallback-model)")
    ap.add_argument("--modelle-erlaubt", default="",
                    help="Komma-Liste der Modelle, die ein Task per `model` bestellen darf")
    ap.add_argument("--model-nachlauf", default=None,
                    help="günstigeres Modell für Fortsetzungen nach Rückfrage und Weckrufe")
    ap.add_argument("--project-fallback", choices=["basis"], default=None,
                    help="fehlendes project-Verzeichnis: im Basisverzeichnis laufen "
                         "statt Rückfrage (Issue #54)")
    ap.add_argument("--zustellung", choices=["headless", "sitzung"], default="headless",
                    help="sitzung = Tasks in die laufende Claude-Sitzung des Projekt-"
                         "Terminals tippen (tmux), headless nur als Rückfall (Issue #67)")
    ap.add_argument("--zustellung-push", action="store_true",
                    help="Push, wenn ein Task in die Sitzung getippt wurde")
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
    resume = {"an": not args.no_resume, "max_pause": args.resume_max_pause,
              "max_kontext": args.resume_max_kontext,
              "kalt_minuten": args.resume_kalt_minuten,
              "kompakt_ab": args.resume_kompakt_ab}
    if args.freigabe_tool and FREIGABE_TOOL_RE.match(args.freigabe_tool):
        LAUF_EINST.update(freigabe_tool=args.freigabe_tool,
                          freigabe_timeout=args.freigabe_timeout)
    einst = {"timeout": args.timeout, "leerlauf": args.leerlauf,
             "project_fallback": args.project_fallback,
             "model": modell_ok(args.model),
             "fallback_model": modell_ok(args.fallback_model),
             "modelle_erlaubt": [m.strip() for m in args.modelle_erlaubt.split(",")
                                 if m.strip()],
             "model_nachlauf": modell_ok(args.model_nachlauf),
             "zustellung": args.zustellung,
             "zustellung_push": args.zustellung_push}

    # Nur EIN Watcher je Agent und PC (H2) — sonst führen zwei Instanzen
    # denselben Task doppelt aus (z.B. Netz-Flap: der Container startet einen
    # neuen, während der alte noch lebt). Muss bis Prozessende offen bleiben.
    lock = instanz_lock(args.agent)
    if lock is None:
        print(f"[{now()}] Es läuft bereits ein Watcher für '{args.agent}' auf diesem PC "
              f"(Lock: {lock_pfad(args.agent)}) — dieser Start beendet sich, damit kein "
              f"Task doppelt ausgeführt wird.", flush=True)
        return 2

    stdin_stop_waechter()
    if args.erwartet_env and not ENV_DA.wait(15):
        print(f"[{now()}] Geheimnisse vom Dashboard blieben aus — starte ohne.", flush=True)

    # Review P1-2: kill/Reboot/systemd-Stopp beendete Python OHNE finally —
    # die claude-Prozessgruppe (eigene Session!) arbeitete verwaist weiter und
    # änderte Dateien, während der Task 3 h als "running" hing. SIGTERM/SIGHUP
    # lösen jetzt den Not-Aus-Pfad aus; atexit ist der zweite Riegel.
    def _signal_ende(signum, _frame):
        HART.set()
        abbrechen_laufenden()
        sicher_print(f"[{now()}] Signal {signum} — laufenden Lauf abgebrochen, beende.")
        sys.exit(143)

    for _sig in ("SIGTERM", "SIGHUP"):
        if hasattr(signal, _sig):
            try:
                signal.signal(getattr(signal, _sig), _signal_ende)
            except (ValueError, OSError):
                pass  # exotische Umgebung ohne Signal-Support
    atexit.register(abbrechen_laufenden)

    # Preflight VOR dem ersten Claim: kaputte Umgebung → gar nicht erst
    # anfangen, kein einziger Task geht verloren (Issue #14).
    problem = preflight(args.claude_bin, workdir, args.dry_run)
    if problem:
        print(f"[{now()}] PREFLIGHT FEHLGESCHLAGEN: {problem}", flush=True)
        return 1
    claude_bin = args.claude_bin if args.dry_run else finde_claude(args.claude_bin)

    if args.mcp_url:
        print(f"[{now()}] Watcher gestartet für '{args.agent}' "
              f"(dry_run={args.dry_run}, claude={claude_bin}, "
              f"sitzung={'fortsetzen' if resume['an'] else 'je Task neu'}) — "
              f"MCP {args.mcp_url}", flush=True)
        try:
            return mcp_loop(args.mcp_url, args.agent, claude_bin, workdir,
                            args.interval, args.dry_run, args.mcp_hint, args.once,
                            args.permission_mode, args.allowed_tools, resume, einst)
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
                            workdir, args.dry_run, args.mcp_hint,
                            args.permission_mode, args.allowed_tools, resume, einst) < 0:
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
