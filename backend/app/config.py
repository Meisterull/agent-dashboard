"""Editierbare Settings + Agenten-Verbindungen.

Settings liegen in DATA_CONFIG_DIR/settings.json (beschreibbar, vom Dashboard
gepflegt). SSH-Verbindungen kommen aus agents.yaml — Credentials werden NIE
ans Frontend gegeben, nur Name/Host/User/Modus.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any


def _atomic_write_text(path: Path, text: str) -> None:
    """Datei atomar ersetzen (tmp + fsync + replace) — Muster aus der Mailbox."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


DATA_CONFIG_DIR = Path(os.environ.get("DATA_CONFIG_DIR", "/workspace/config"))
SETTINGS_PATH = DATA_CONFIG_DIR / "settings.json"
AGENTS_YAML = DATA_CONFIG_DIR / "agents.yaml"
# Über das Dashboard angelegte Verbindungen — getrennt von der handgepflegten
# agents.yaml, damit deren Kommentare/Struktur nie maschinell zerschrieben werden.
AGENTS_UI_YAML = DATA_CONFIG_DIR / "agents_ui.yaml"
# Private Keys der UI-Verbindungen (Container-intern, 0600).
KEYS_DIR = Path(os.environ.get("WORKSPACE_DIR", "/workspace")) / "keys"

# Nicht-geheime UI-Settings. API-Keys/Tokens bleiben in .env / Secrets.
DEFAULT_SETTINGS: dict[str, Any] = {
    "language": "de",
    # Leer = Env-Default (OLLAMA_MODEL / ORCH_MODEL). Gesetzt = Live-Override
    # des Orchestrator-Modells über das Dashboard.
    "orch_model": "",
    # Externe Fenster im Workspace (z. B. noVNC): [{"name": …, "url": …}].
    # url = "IP:Port[/pfad]" (läuft über den nginx-Proxy /ext/, auch WebSocket)
    # oder eine volle https://-URL, die direkt eingebettet wird.
    "external_windows": [],
    # Automatikmodus (Issue #12): GEWÜNSCHTER Zustand je Agent ({name: true})
    # plus globaler Not-Aus. Gepflegt über /api/automatik, nicht das
    # Settings-Formular; app/auto_watcher.py stellt ihn nach Neustarts wieder her.
    "automatik": {},
    "automatik_notaus": False,
}

# Editierbare Felder (Whitelist) — verhindert, dass das UI beliebige Keys setzt.
ALLOWED_KEYS = set(DEFAULT_SETTINGS)


def load_settings() -> dict[str, Any]:
    settings = dict(DEFAULT_SETTINGS)
    if SETTINGS_PATH.exists():
        try:
            settings.update(json.loads(SETTINGS_PATH.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            pass
    return settings


def save_settings(patch: dict[str, Any]) -> dict[str, Any]:
    settings = load_settings()
    for key, value in patch.items():
        if key in ALLOWED_KEYS:
            settings[key] = value
    DATA_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    # Atomar schreiben wie die Mailbox: hier stehen auch die Automatik-Schalter
    # und der Not-Aus. Ein Crash mitten im Schreiben hinterließe kaputtes JSON,
    # load_settings fiele still auf die Defaults zurück — der Not-Aus wäre weg.
    _atomic_write_text(
        SETTINGS_PATH, json.dumps(settings, ensure_ascii=False, indent=2)
    )
    return settings


# --- Externe Fenster (nginx-Proxy /ext/) ----------------------------------
# Der Proxy erlaubt nginx-seitig JEDE private IPv4. Was er ausliefert, läuft
# unter der Origin des Dashboards und könnte dessen API mit dem Session-Cookie
# bedienen — deshalb prüft /api/auth/verify zusätzlich gegen diese Liste.
_EXT_ZIEL_RE = re.compile(r"^(\d{1,3}(?:\.\d{1,3}){3}):(\d{1,5})(?:/|$)")


def erlaubte_ext_ziele(settings: dict[str, Any] | None = None) -> set[str]:
    """`ip:port` aller eingetragenen externen Fenster (ohne volle https-URLs)."""
    ziele: set[str] = set()
    for fenster in (settings or load_settings()).get("external_windows") or []:
        roh = str((fenster or {}).get("url") or "").strip()
        if not roh or roh.lower().startswith("https://"):
            continue  # volle https-URLs landen direkt im iframe, nicht über /ext/
        m = _EXT_ZIEL_RE.match(re.sub(r"^http://", "", roh, flags=re.IGNORECASE))
        if m:
            ziele.add(f"{m.group(1)}:{m.group(2)}")
    return ziele


def ist_erlaubtes_ext_ziel(ziel: str | None) -> bool:
    """Darf der /ext/-Proxy dieses `ip:port` ansprechen?

    `ziel` kommt als Header X-Ext-Ziel aus den nginx-Captures — also aus der
    NORMALISIERTEN URI und damit aus genau dem Wert, den proxy_pass benutzt.
    Die URI selbst zu zerlegen wäre falsch: nginx normalisiert `..`-Segmente
    vor dem Location-Matching, `$request_uri` kann also ein anderes (erlaubtes)
    Ziel vortäuschen als das, was am Ende wirklich angesprochen wird.
    """
    if not ziel or not _EXT_ZIEL_RE.match(ziel):
        return False
    return ziel in erlaubte_ext_ziele()


def _yaml_agents(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        import yaml  # PyYAML; nur hier importiert, damit fehlend nicht alles bricht

        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return []
    return list(data.get("agents", []) or [])


def load_agents_full() -> list[dict[str, Any]]:
    """Alle Agenten (agents.yaml + agents_ui.yaml) inkl. Verbindungsdaten.

    Nur serverseitig verwenden — enthält key_file-Pfade. agents.yaml gewinnt
    bei Namenskonflikten. Jeder Eintrag bekommt `source`: "yaml" | "ui".
    """
    agents = []
    names = set()
    for source, path in (("yaml", AGENTS_YAML), ("ui", AGENTS_UI_YAML)):
        for a in _yaml_agents(path):
            if a.get("name") and a["name"] not in names:
                names.add(a["name"])
                agents.append({**a, "source": source})
    return agents


def agent_connection(name: str) -> dict[str, Any] | None:
    """Vollständige Verbindungsdaten (inkl. Key-Datei) für einen Agenten."""
    for a in load_agents_full():
        if a.get("name") == name:
            return a.get("connection", {}) or {}
    return None


def load_connections() -> list[dict[str, Any]]:
    """SSH-Verbindungen fürs Frontend — ohne Credentials."""
    out = []
    for agent in load_agents_full():
        conn = agent.get("connection", {}) or {}
        out.append(
            {
                "name": agent.get("name"),
                "description": agent.get("description"),
                "host": conn.get("host"),
                "port": conn.get("port", 22),
                "user": conn.get("user"),
                "mode": agent.get("mode"),
                "source": agent.get("source", "yaml"),
            }
        )
    return out


def add_ui_connection(
    name: str, host: str, port: int, user: str, key_file: str, description: str = ""
) -> None:
    """Verbindung in agents_ui.yaml eintragen (maschinenverwaltete Datei)."""
    import yaml

    agents = _yaml_agents(AGENTS_UI_YAML)
    agents.append(
        {
            "name": name,
            "description": description or f"über das Dashboard angelegt",
            "role": "worker",
            "mode": "mailbox",
            "connection": {
                "type": "ssh",
                "host": host,
                "port": port,
                "user": user,
                "auth": "key",
                "key_file": key_file,
            },
        }
    )
    DATA_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(
        AGENTS_UI_YAML,
        "# Vom Dashboard verwaltete Verbindungen — NICHT von Hand editieren,\n"
        "# handgepflegte Einträge gehören in agents.yaml.\n"
        + yaml.safe_dump({"agents": agents}, allow_unicode=True, sort_keys=False),
    )


def remove_ui_connection(name: str) -> dict[str, Any] | None:
    """UI-verwaltete Verbindung entfernen; liefert den entfernten Eintrag."""
    import yaml

    agents = _yaml_agents(AGENTS_UI_YAML)
    removed = next((a for a in agents if a.get("name") == name), None)
    if removed is None:
        return None
    agents = [a for a in agents if a.get("name") != name]
    _atomic_write_text(
        AGENTS_UI_YAML,
        "# Vom Dashboard verwaltete Verbindungen — NICHT von Hand editieren,\n"
        "# handgepflegte Einträge gehören in agents.yaml.\n"
        + yaml.safe_dump({"agents": agents}, allow_unicode=True, sort_keys=False),
    )
    return removed
