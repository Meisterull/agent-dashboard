"""Rollen für Task-Läufe (Dashboard-Paket, Stufe 1).

Eine Rolle ist eine Markdown-Datei unter DATA_CONFIG_DIR/rollen/<name>.md:
YAML-Frontmatter (beschreibung, optional permission_mode/allowed_tools),
darunter der Rollen-Prompt, den der Watcher per --append-system-prompt an
den Claude-Lauf hängt.

AUFGELÖST WIRD SERVERSEITIG beim send_task: Prompt und Rechte werden in den
Task-Envelope eingebettet (rolle/rollen_prompt/rollen_permission_mode/
rollen_tools). So bekommen BEIDE Watcher-Transporte (MCP-claim wie
Datei-Mailbox) dieselben Felder, der Watcher bleibt dumm, und die Rolle ist
zum Sendezeitpunkt eingefroren — eine später editierte Rollen-Datei ändert
keinen bereits eingereihten Task.

RECHTE-SEMANTIK (bewusste Entscheidung, 02.09.2026): Eine Rolle kann die
Rechte des Agenten (agents.yaml) nur EINSCHRÄNKEN, nie erweitern — die
Schnittmenge rechnet der Watcher (wirksame_rechte in agent_watcher.py),
denn nur er kennt seine Prozess-Defaults. Hier werden die Rollen-Werte
lediglich unverändert eingebettet.
"""
from __future__ import annotations

import re
from typing import Any

from app.config import DATA_CONFIG_DIR, _atomic_write_text

ROLLEN_DIR = DATA_CONFIG_DIR / "rollen"

# Rollennamen landen als Dateiname und im Envelope — dieselbe Strenge wie
# bei Agentennamen (Path-Traversal), zusätzlich klein gehalten, damit
# "Review" und "review" nicht zwei Rollen werden.
ROLLEN_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)


class RollenFehler(ValueError):
    """Unbekannte Rolle oder ungültige Rollen-Datei — mit Klartext."""


def _parse(text: str) -> dict[str, Any]:
    """Frontmatter + Prompt aus dem Dateitext; tolerant, aber ehrlich.

    Ohne Frontmatter ist die ganze Datei der Prompt (Rolle ohne
    Rechte-Wirkung). Kaputtes YAML wirft RollenFehler — eine Rolle, deren
    Rechte-Angabe still ignoriert würde, wäre ein Sicherheitsproblem.
    """
    meta: dict[str, Any] = {}
    prompt = text
    m = _FRONTMATTER_RE.match(text)
    if m is None and text.lstrip().startswith("---"):
        # Review 02.09. (P0-7): Frontmatter-Beginn ohne schließendes `---`
        # hieße „alles ist Prompt" — die Rechte-Angabe würde STILL ignoriert
        # und der Lauf bekäme die vollen Agenten-Rechte. Lieber ablehnen.
        raise RollenFehler(
            "Frontmatter beginnt mit ---, wird aber nie geschlossen — "
            "Rechte-Angaben würden sonst still ignoriert")
    if m:
        try:
            import yaml  # wie in config.py: lazy, PyYAML ist im Container da

            geladen = yaml.safe_load(m.group(1)) or {}
        except Exception as exc:  # noqa: BLE001 — Klartext statt Stille
            raise RollenFehler(f"Frontmatter ist kein gültiges YAML: {exc}") from exc
        if not isinstance(geladen, dict):
            raise RollenFehler("Frontmatter muss ein YAML-Objekt sein")
        meta = geladen
        prompt = text[m.end():]
    tools = meta.get("allowed_tools")
    if isinstance(tools, str):
        tools = [t.strip() for t in tools.split(",") if t.strip()]
    elif isinstance(tools, (list, tuple)):
        tools = [str(t).strip() for t in tools if str(t).strip()]
    elif tools is not None:
        raise RollenFehler("allowed_tools muss Liste oder Komma-Text sein")
    mode = meta.get("permission_mode")
    if mode is not None and not isinstance(mode, str):
        raise RollenFehler("permission_mode muss ein Text sein")
    return {
        "beschreibung": str(meta.get("beschreibung") or "").strip(),
        "permission_mode": mode,
        "allowed_tools": tools,
        "prompt": prompt.strip(),
    }


def _pfad(name: str):
    if not ROLLEN_NAME_RE.fullmatch(name or ""):
        raise RollenFehler(
            f"ungültiger Rollenname: {name!r} (erlaubt: kleinbuchstaben, "
            f"ziffern, - und _)"
        )
    return ROLLEN_DIR / f"{name}.md"


def liste_rollen() -> list[dict[str, Any]]:
    """Alle Rollen (Name + Metadaten, ohne Prompt-Volltext) — fürs UI und
    als Fehlermeldungs-Futter. Kaputte Dateien erscheinen mit `fehler`,
    statt still zu fehlen — sonst sucht man eine 'verschwundene' Rolle."""
    out: list[dict[str, Any]] = []
    if not ROLLEN_DIR.is_dir():
        return out
    for p in sorted(ROLLEN_DIR.glob("*.md")):
        name = p.stem
        if not ROLLEN_NAME_RE.fullmatch(name):
            continue
        eintrag: dict[str, Any] = {"name": name}
        try:
            geparst = _parse(p.read_text(encoding="utf-8"))
            eintrag.update(
                beschreibung=geparst["beschreibung"],
                permission_mode=geparst["permission_mode"],
                allowed_tools=geparst["allowed_tools"],
                hat_prompt=bool(geparst["prompt"]),
            )
        except (OSError, RollenFehler) as exc:
            eintrag["fehler"] = str(exc)
        out.append(eintrag)
    return out


def roher_text(name: str) -> str | None:
    """Dateitext einer Rolle (fürs Bearbeiten im Dialog); None wenn fehlt."""
    p = _pfad(name)
    try:
        return p.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None


def lade_rolle(name: str) -> dict[str, Any] | None:
    p = _pfad(name)
    try:
        text = p.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    return {"name": name, **_parse(text)}


def rolle_fuer_task(name: str) -> dict[str, Any]:
    """Die Felder, die send_task in den Envelope einbettet.

    Unbekannte Rolle wirft mit der Liste der verfügbaren — derselbe Schutz
    wie bei unbekannten Agenten: ein Tippfehler des LLM darf keinen Task
    ohne die gemeinte Rolle losschicken.
    """
    rolle = lade_rolle(name)
    if rolle is None:
        verfuegbar = ", ".join(r["name"] for r in liste_rollen()) or "(keine)"
        raise RollenFehler(f"unbekannte Rolle {name!r} — verfügbar: {verfuegbar}")
    return {
        "rolle": name,
        "rollen_prompt": rolle["prompt"] or None,
        "rollen_permission_mode": rolle["permission_mode"],
        "rollen_tools": rolle["allowed_tools"],
    }


def speichere_rolle(name: str, text: str) -> dict[str, Any]:
    """Rollen-Datei schreiben (atomar) — vorher parsen, damit kaputtes
    Frontmatter beim Speichern auffällt und nicht erst beim Delegieren."""
    p = _pfad(name)
    _parse(text)  # wirft RollenFehler bei kaputtem Kopf
    ROLLEN_DIR.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(p, text)
    return {"name": name, **_parse(text)}


def loesche_rolle(name: str) -> bool:
    p = _pfad(name)
    try:
        p.unlink()
        return True
    except FileNotFoundError:
        return False
