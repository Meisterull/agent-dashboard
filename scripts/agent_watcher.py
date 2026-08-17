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
import signal
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

# Muss zur Allowlist in app/mailbox.py passen — sender wird in Pfade gejoint.
AGENT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def sicher_print(text: str) -> None:
    """print, das eine tote stdout-Leitung überlebt.

    stdout ist die SSH-Session zum Dashboard. Wird sie geschlossen (Not-Aus,
    Netzabriss), wirft das nächste print BrokenPipeError — mitten in der
    Ausgabe-Schleife von run_claude würde das den Lauf verwaisen lassen."""
    try:
        print(text, flush=True)
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
        return None, (f"project-Verzeichnis fehlt auf dem Agenten-PC: {ziel} "
                      f"(project {projekt!r} unterhalb von {basis})")
    return ziel, None


def fehlerserie(status: str, dauer: float) -> bool:
    """Fehlschlag-Zähler füttern; True = anhalten (Serie sofortiger Fehler)."""
    global _schnelle_fehler
    if status == "error" and dauer < SCHNELL_SEKUNDEN:
        _schnelle_fehler += 1
    else:
        _schnelle_fehler = 0
    return _schnelle_fehler >= FEHLER_SCHWELLE


CLAUDE_TIMEOUT = 1800.0  # Sekunden je Task-Lauf


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
    except OSError:
        return None  # kein Home/kein Schreibrecht: lieber laufen als blockieren
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
    inp = block.get("input") or {}
    for key in ("description", "command", "file_path", "path", "pattern",
                "prompt", "query", "url"):
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


def baue_claude_cmd(claude_bin: str, instruction: str,
                    permission_mode: str | None = None,
                    allowed_tools: str | None = None) -> list[str]:
    """Kommandozeile für einen headless-Lauf — die instruction IMMER hinter "--".

    Issue #20: `--allowed-tools` ist variadisch (`--allowed-tools <tools...>`)
    und verschluckt jedes folgende Positional. Ohne Trenner landet die
    instruction als weiterer Tool-Name in der Option, danach ist kein Prompt
    mehr übrig und claude bricht ab mit "Input must be provided either through
    stdin or as a prompt argument when using --print". Eine Komma-Liste hilft
    dagegen NICHT — sie verhindert nur, dass mehrere Tool-Namen als getrennte
    Argumente aufgefasst werden. "--" beendet die Optionsauswertung und ist
    auch für sich genommen richtig: eine instruction, die mit "-" beginnt,
    wäre sonst ebenfalls eine Option.
    """
    cmd = [claude_bin, "--print", "--output-format", "stream-json", "--verbose"]
    if permission_mode:
        cmd += ["--permission-mode", permission_mode]
    if allowed_tools:
        cmd += ["--allowed-tools", allowed_tools]
    return cmd + ["--", instruction]


def run_claude(claude_bin: str, instruction: str, workdir: Path, dry_run: bool,
               fortschritt=None, permission_mode: str | None = None,
               allowed_tools: str | None = None) -> tuple[str, str, int]:
    """Gibt (result, log, returncode) zurück.

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
    `baue_claude_cmd` — das "--" vor der instruction ist Pflicht (Issue #20).

    Abbruch (Not-Aus, H3): der Prozess ist global registriert, "kill" auf
    stdin schießt ihn samt Kindern ab. Die Lese-Schleife läuft unter
    try/finally — verlässt sie der Lauf auf irgendeinem anderen Weg (z.B.
    tote stdout-Leitung), stirbt die Prozessgruppe trotzdem, statt verwaist
    weiter Dateien zu ändern.
    """
    if dry_run:
        return f"[dry-run] hätte ausgeführt: {instruction}", "", 0
    cmd = baue_claude_cmd(claude_bin, instruction, permission_mode, allowed_tools)
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(workdir),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            # Eigene Prozessgruppe (POSIX): beim Timeout muss die GANZE Gruppe
            # sterben — claude spawnt Tool-Subprozesse, die sonst die stdout-
            # Pipe offen halten und das Zeilen-Lesen weiter blockieren.
            start_new_session=(os.name == "posix"),
        )
    except FileNotFoundError:
        # errno 2 allein nennt die Datei nicht — hier Klartext liefern (#14).
        return "", f"Claude-Binary nicht ausführbar: {claude_bin}", 127

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

    def _abbrechen() -> None:
        abgelaufen.set()
        beende_prozessgruppe(proc)  # POSIX killpg / Windows taskkill /T (M13)

    wecker = threading.Timer(CLAUDE_TIMEOUT, _abbrechen)
    wecker.start()

    ergebnis: str | None = None
    fehler_event = False
    texte: list[str] = []  # Assistant-Texte (Fallback-Ergebnis)
    roh: list[str] = []    # Nicht-JSON-Zeilen (Binary ohne stream-json)
    werkzeug_namen: dict[str, str] = {}  # tool_use_id → Tool-Name
    abgelehnt: list[str] = []            # verweigerte Werkzeuge (Issue #19)
    vollstaendig = False
    try:
        for zeile in proc.stdout:
            zeile = zeile.strip()
            if not zeile:
                continue
            try:
                ev = json.loads(zeile)
            except json.JSONDecodeError:
                roh.append(zeile)
                continue
            typ = ev.get("type")
            if typ == "assistant":
                for block in (ev.get("message") or {}).get("content") or []:
                    if block.get("type") == "tool_use":
                        if block.get("id"):
                            werkzeug_namen[block["id"]] = block.get("name", "?")
                        melde(kurz(f"→ {block.get('name', '?')} "
                                   f"{tool_hinweis(block)}", 100))
                    elif block.get("type") == "text" and block.get("text"):
                        texte.append(block["text"])
                        melde(kurz(block["text"], 100))
            elif typ == "user":
                # Abgelehnte Werkzeuge sichtbar machen (Issue #19): der Lauf
                # endet sonst normal, und der Grund steht nur im Fließtext.
                for block in (ev.get("message") or {}).get("content") or []:
                    if not (isinstance(block, dict)
                            and block.get("type") == "tool_result"
                            and block.get("is_error")):
                        continue
                    text = tool_result_text(block)
                    if "permission" not in text.lower():
                        continue  # normaler Tool-Fehler, kein Berechtigungs-Thema
                    name = werkzeug_namen.get(block.get("tool_use_id") or "", "?")
                    if name not in abgelehnt:
                        abgelehnt.append(name)
                    melde(kurz(f"✗ {name} abgelehnt: {text}", 100))
            elif typ == "result":
                ergebnis = ev.get("result") or ""
                fehler_event = bool(ev.get("is_error"))
                for d in ev.get("permission_denials") or []:
                    name = (d.get("tool_name") if isinstance(d, dict) else None) or "?"
                    if name not in abgelehnt:
                        abgelehnt.append(name)
        vollstaendig = True
    finally:
        wecker.cancel()
        # Abbruch auf JEDEM Weg (Not-Aus, Ausnahme in der Schleife, tote
        # Leitung): die Prozessgruppe muss sterben, sonst arbeitet claude
        # unbeaufsichtigt weiter (H3).
        if not vollstaendig or HART.is_set():
            beende_prozessgruppe(proc)
        _vergiss_prozess(proc)
    rc = proc.wait()
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
        log = (log + f"\n[watcher] Timeout nach {CLAUDE_TIMEOUT:.0f}s — "
                     f"Prozess abgebrochen").strip()
        rc = rc or 124
    if ergebnis is None:
        ergebnis = "\n".join(texte) or "\n".join(roh)
    if fehler_event and rc == 0:
        rc = 1
    if abgelehnt:
        log = (log + "\n[watcher] Berechtigung verweigert: " + ", ".join(abgelehnt) +
               " — permission_mode/allowed_tools in agents.yaml setzen oder auf "
               "dem Agenten-PC freigeben").strip()
    return ergebnis.strip(), log, rc


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


def liefere_ergebnis(client: "McpClient | None", url: str, task_id: str,
                     result: str, status: str, log: str
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
            })
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


def mcp_loop(url: str, agent: str, claude_bin: str, workdir: Path, interval: float,
             dry_run: bool, with_mcp_hint: bool, once: bool = False,
             permission_mode: str | None = None,
             allowed_tools: str | None = None) -> int:
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
                projekt = claimed.get("project") or env.get("project")
                print(f"[{now()}] {agent}: bearbeite {task_id}"
                      + (f" (project {projekt})" if projekt else ""), flush=True)
                if with_mcp_hint:
                    instruction = mcp_hint(agent) + instruction
                def fortschritt(text: str, _tid: str = task_id) -> None:
                    # Fließt via stdout ins Automatik-Panel (Issue #18); eine
                    # tote Leitung darf den Lauf nicht abbrechen (H3).
                    sicher_print(f"[{now()}] {agent}: {_tid} · {text}")

                task_dir, wd_fehler = projekt_workdir(workdir, projekt)
                start = time.monotonic()
                if wd_fehler:  # falsches Verzeichnis wäre schlimmer als Abbruch (#19)
                    result, err, status = "", wd_fehler, "error"
                else:
                    try:
                        result, err, rc = run_claude(claude_bin, instruction, task_dir,
                                                     dry_run, fortschritt,
                                                     permission_mode, allowed_tools)
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
                    client, url, task_id, result, status, err)
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
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(env, dict) or env.get("kind", "task") != "task":
            continue
        eintraege.append((str(env.get("created_at") or ""), pfad.name, pfad))
    eintraege.sort(key=lambda e: (e[0] == "", e[0], e[1]))
    return [e[2] for e in eintraege]


def process_once(inbox: Path, processing: Path, outbox: Path,
                 agent: str, claude_bin: str, workdir: Path, dry_run: bool,
                 with_mcp_hint: bool = False,
                 permission_mode: str | None = None,
                 allowed_tools: str | None = None) -> int:
    """Gibt die Zahl bearbeiteter Tasks zurück; -1 = Fehlerserie, bitte anhalten."""
    handled = 0
    for task_path in inbox_tasks(inbox):
        if STOP.is_set():
            # "stop" (oder Not-Aus) wirkt sofort, nicht erst nach dem ganzen
            # Stapel — 5 Tasks à 30 min wären sonst 2,5 h Weiterarbeit (M14).
            break
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
            task_dir, wd_fehler = projekt_workdir(workdir, task.get("project"))
            if wd_fehler:  # falsches Verzeichnis wäre schlimmer als Abbruch (#19)
                err = wd_fehler
                result = fehler_result("", err)
            else:
                result, err, rc = run_claude(
                    claude_bin, instruction, task_dir, dry_run,
                    lambda text, _tid=task_id: sicher_print(
                        f"[{now()}] {agent}: {_tid} · {text}"),
                    permission_mode, allowed_tools)
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
    ap.add_argument("--permission-mode",
                    help="an claude --permission-mode durchgereicht (z.B. "
                         "acceptEdits) — headless kann niemand Freigabe-"
                         "Rückfragen beantworten (Issue #19)")
    ap.add_argument("--allowed-tools",
                    help="Komma-getrennte Liste für claude --allowed-tools, "
                         "z.B. 'Edit,Write,Bash(git:*)' (Issue #19)")
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
                            args.interval, args.dry_run, args.mcp_hint, args.once,
                            args.permission_mode, args.allowed_tools)
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
                            args.permission_mode, args.allowed_tools) < 0:
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
