"""Editierbare Settings + Agenten-Verbindungen.

Settings liegen in DATA_CONFIG_DIR/settings.json (beschreibbar, vom Dashboard
gepflegt). SSH-Verbindungen kommen aus agents.yaml — Credentials werden NIE
ans Frontend gegeben, nur Name/Host/User/Modus.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

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
    "llm_provider": "claude-api",
    "language": "de",
    "telegram_enabled": False,
    # Leer = Env-Default (OLLAMA_MODEL / ORCH_MODEL). Gesetzt = Live-Override
    # des Orchestrator-Modells über das Dashboard.
    "orch_model": "",
    # Externe Fenster im Workspace (z. B. noVNC): [{"name": …, "url": …}].
    # url = "IP:Port[/pfad]" (läuft über den nginx-Proxy /ext/, auch WebSocket)
    # oder eine volle https://-URL, die direkt eingebettet wird.
    "external_windows": [],
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
    SETTINGS_PATH.write_text(
        json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return settings


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
    AGENTS_UI_YAML.write_text(
        "# Vom Dashboard verwaltete Verbindungen — NICHT von Hand editieren,\n"
        "# handgepflegte Einträge gehören in agents.yaml.\n"
        + yaml.safe_dump({"agents": agents}, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def remove_ui_connection(name: str) -> dict[str, Any] | None:
    """UI-verwaltete Verbindung entfernen; liefert den entfernten Eintrag."""
    import yaml

    agents = _yaml_agents(AGENTS_UI_YAML)
    removed = next((a for a in agents if a.get("name") == name), None)
    if removed is None:
        return None
    agents = [a for a in agents if a.get("name") != name]
    AGENTS_UI_YAML.write_text(
        "# Vom Dashboard verwaltete Verbindungen — NICHT von Hand editieren,\n"
        "# handgepflegte Einträge gehören in agents.yaml.\n"
        + yaml.safe_dump({"agents": agents}, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return removed
