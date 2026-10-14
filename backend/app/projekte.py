"""Projekt-Terminals (11.10.2026): je SSH-Verbindung eine Liste von Projekten,
hinter denen im Terminal-Panel eine dauerhafte Claude-Code-Sitzung steckt.

Ein Projekt ist {name, pfad, befehl}. Ein Tipp im Panel öffnet ein Terminal mit
der Sitzungskennung ``p:<name>``; die SSH-Brücke startet dafür keine nackte
Shell, sondern auf dem Zielrechner eine **tmux**-Sitzung gleichen Namens im
Projektpfad und tippt beim Anlegen den Startbefehl hinein (Default
``claude --continue``). Die tmux-Sitzung lebt auf dem Zielrechner — sie
überlebt Dashboard-Deploys und die SSH_GRACE_SECONDS-Frist; beim Wieder-
anhängen zeichnet tmux den Bildschirm sauber neu (Replay-Puffer wird dafür
nicht gebraucht). Fehlt tmux (Windows-Agenten-PC), fällt die Brücke auf
``cd <pfad> && <befehl>`` in der normalen Shell zurück — dann ohne Dauer-
haftigkeit, das sagt das Terminal beim Start.

Gepflegt wird die Liste über den Dialog „Verbindungen verwalten"
(PUT /api/projekte/{verbindung}); sie liegt maschinenverwaltet in
DATA_CONFIG_DIR/projekte.yaml::

    werkstatt:
      - name: zeitalter
        pfad: /home/user/zeitalter
        befehl: claude --continue

Reine Standardlib (+ PyYAML wie config.py); Tests in tests/test_projekte.py.
"""
from __future__ import annotations

import re
import shlex
from typing import Any

from app.config import DATA_CONFIG_DIR, _atomic_write_text

PROJEKTE_YAML = DATA_CONFIG_DIR / "projekte.yaml"
SID_PRAEFIX = "p:"
STANDARD_BEFEHL = "claude --continue"
MAX_JE_VERBINDUNG = 30
HISTORY_LIMIT = 50000

# Der Name wird tmux-Sitzungsname UND Teil der Terminal-sid — tmux verbietet
# ":" und "." im Namen, und ein Punkt im Namen wäre ein Kopfschmerz in der
# Tab-Beschriftung. Gleiche Regel wie _CONN_NAME_RE in main.py.
_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,32}$")
_STEUERZEICHEN = re.compile(r"[\x00-\x1f\x7f]")


class ProjektFehler(ValueError):
    pass


def ist_projekt_sid(sid: str) -> bool:
    return isinstance(sid, str) and sid.startswith(SID_PRAEFIX) and len(sid) > len(SID_PRAEFIX)


def projekt_name_aus_sid(sid: str) -> str:
    return sid[len(SID_PRAEFIX):] if ist_projekt_sid(sid) else ""


def pruefe_projekt(roh: Any) -> dict[str, str]:
    """Ein Eintrag aus Datei oder Request → {name, pfad, befehl} oder ProjektFehler."""
    if not isinstance(roh, dict):
        raise ProjektFehler("Projekt muss ein Objekt mit name/pfad sein")
    name = str(roh.get("name") or "").strip()
    pfad = str(roh.get("pfad") or "").strip()
    befehl = str(roh.get("befehl") or "").strip() or STANDARD_BEFEHL
    if not _NAME_RE.match(name):
        raise ProjektFehler(
            f"Projektname '{name}': nur Buchstaben/Zahlen/_/-, max. 32 Zeichen"
        )
    if not pfad or _STEUERZEICHEN.search(pfad):
        raise ProjektFehler(f"Projekt '{name}': Pfad fehlt oder enthält Steuerzeichen")
    if _STEUERZEICHEN.search(befehl):
        raise ProjektFehler(f"Projekt '{name}': Startbefehl darf keine Zeilenumbrüche enthalten")
    return {"name": name, "pfad": pfad, "befehl": befehl}


def pruefe_liste(roh: Any) -> list[dict[str, str]]:
    if roh is None:
        return []
    if not isinstance(roh, list):
        raise ProjektFehler("Projektliste muss eine Liste sein")
    if len(roh) > MAX_JE_VERBINDUNG:
        raise ProjektFehler(f"höchstens {MAX_JE_VERBINDUNG} Projekte je Verbindung")
    out: list[dict[str, str]] = []
    gesehen: set[str] = set()
    for eintrag in roh:
        p = pruefe_projekt(eintrag)
        if p["name"].lower() in gesehen:
            raise ProjektFehler(f"Projektname '{p['name']}' doppelt")
        gesehen.add(p["name"].lower())
        out.append(p)
    return out


def _lies_datei() -> dict[str, Any]:
    if not PROJEKTE_YAML.exists():
        return {}
    import yaml

    try:
        daten = yaml.safe_load(PROJEKTE_YAML.read_text(encoding="utf-8")) or {}
    except (yaml.YAMLError, OSError):
        return {}
    return daten if isinstance(daten, dict) else {}


def lade_alle() -> dict[str, list[dict[str, str]]]:
    """{verbindung: [projekte]} — kaputte Einträge werden still ausgelassen,
    damit eine von Hand verschriebene Datei nicht das ganze Panel leert."""
    out: dict[str, list[dict[str, str]]] = {}
    for verbindung, liste in _lies_datei().items():
        if not isinstance(liste, list):
            continue
        sauber = []
        for eintrag in liste:
            try:
                sauber.append(pruefe_projekt(eintrag))
            except ProjektFehler:
                continue
        out[str(verbindung)] = sauber
    return out


def lade(verbindung: str) -> list[dict[str, str]]:
    return lade_alle().get(verbindung, [])


def finde(verbindung: str, name: str) -> dict[str, str] | None:
    for p in lade(verbindung):
        if p["name"] == name:
            return p
    return None


def speichere(verbindung: str, liste: Any) -> list[dict[str, str]]:
    """Liste einer Verbindung ersetzen (geprüft, atomar geschrieben)."""
    import yaml

    sauber = pruefe_liste(liste)
    daten = _lies_datei()
    if sauber:
        daten[verbindung] = sauber
    else:
        daten.pop(verbindung, None)
    kopf = (
        "# Projekt-Terminals je SSH-Verbindung — vom Dashboard gepflegt\n"
        "# (Terminal-Panel → + → Projekte). Von Hand ändern geht, Kommentare\n"
        "# überleben das nächste Speichern aber nicht.\n"
    )
    _atomic_write_text(
        PROJEKTE_YAML,
        kopf + yaml.safe_dump(daten, allow_unicode=True, sort_keys=True, default_flow_style=False),
    )
    return sauber


def entferne_verbindung(verbindung: str) -> None:
    """Beim Löschen einer UI-Verbindung ihre Projekte mitnehmen."""
    if PROJEKTE_YAML.exists() and verbindung in _lies_datei():
        speichere(verbindung, [])


# --- Startkommandos für die SSH-Brücke --------------------------------------

def tmux_sitzung(name: str) -> str:
    """tmux-Sitzungsname. Beim Nachsehen (has-session/kill-session) steht ein
    '=' davor = exakter Treffer statt Präfix-Match ("bot" dürfte sonst "bot-2"
    finden). Für send-keys/set-option/capture-pane/attach nimmt tmux 3.4 das
    '=' NICHT an (nachgestellt: „can't find pane") — dort der nackte Name;
    weil die exakte Sitzung dann schon existiert, gewinnt sie vor Präfixen."""
    return name


def posix_startkommando(projekt: dict[str, str]) -> str:
    """Eine Shell-Zeile für den Zielrechner: tmux-Sitzung anlegen oder anhängen,
    ohne tmux Rückfall auf cd + Befehl in der normalen Shell.

    - ``new-session`` nur, wenn es die Sitzung noch nicht gibt; der Startbefehl
      wird dann per ``send-keys -l`` (literal, kein tmux-Key-Parsing) in die
      frische Shell getippt — die Eingabe wartet im PTY, bis die Shell liest.
    - ``attach -d`` wirft andere Clients ab (gewählt: eindeutige Größe).
    - history-limit wirkt erst für NEUE Fenster, darum global vor dem Anlegen;
      status/mouse hängen an der Sitzung.
    - Endet der Befehl, bleibt die Shell im Projektordner stehen (gewählt).
    """
    s = shlex.quote(tmux_sitzung(projekt["name"]))
    exakt = shlex.quote("=" + tmux_sitzung(projekt["name"]))
    pfad = shlex.quote(projekt["pfad"])
    befehl = projekt["befehl"]
    befehl_q = shlex.quote(befehl)
    return (
        f"if command -v tmux >/dev/null 2>&1; then "
        f"if ! tmux has-session -t {exakt} 2>/dev/null; then "
        f"tmux set-option -g history-limit {HISTORY_LIMIT} 2>/dev/null; "
        f"tmux new-session -d -s {s} -c {pfad} || exit 1; "
        f"tmux send-keys -t {s} -l {befehl_q} && tmux send-keys -t {s} Enter; "
        f"fi; "
        f"tmux set-option -t {s} status off; tmux set-option -t {s} mouse on; "
        f"exec tmux attach-session -d -t {s}; "
        f"else "
        f"printf '%s\\n' '[tmux fehlt auf dieser Maschine — Sitzung ohne Dauerhaftigkeit]'; "
        f"cd {pfad} || exit 1; {befehl}; exec \"${{SHELL:-sh}}\" -l; "
        f"fi"
    )


def windows_startkommando(projekt: dict[str, str]) -> str:
    """Windows-OpenSSH (Standardshell cmd.exe): kein tmux, nur cd + Befehl."""
    pfad = projekt["pfad"].replace('"', "")
    return f'cd /d "{pfad}" && {projekt["befehl"]}'


def tmux_kill_kommando(projekt: dict[str, str]) -> str:
    return f"tmux kill-session -t {shlex.quote('=' + tmux_sitzung(projekt['name']))}"


def tmux_verlauf_kommando(projekt: dict[str, str]) -> str:
    """Ganzer Verlauf (Scrollback + Bildschirm) als Klartext, Farben entfernt —
    nur wenn es die Sitzung exakt gibt (sonst griffe capture-pane per Präfix)."""
    s = shlex.quote(tmux_sitzung(projekt["name"]))
    exakt = shlex.quote("=" + tmux_sitzung(projekt["name"]))
    return f"tmux has-session -t {exakt} 2>/dev/null && tmux capture-pane -p -J -S - -t {s}"
