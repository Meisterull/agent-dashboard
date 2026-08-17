"""Config-getriebene HTTP-Integrationen — workflow-agnostisch.

Statt domänenspezifische Tools hart zu verdrahten, definiert jeder Workflow
seine eigenen benannten HTTP-Endpunkte in DATA_CONFIG_DIR/integrations.yaml. Der
MCP-Server stellt EIN generisches Tool `call_integration(name, method, path, body)`
bereit; der LLM darf nur konfigurierte Integrationen + erlaubte Methoden nutzen.

Beispiel integrations.yaml:

    integrations:
      gitea:                           # nur ein Anwendungsfall
        base_url: http://gitea:3000/api/v1
        auth_env: GITEA_TOKEN          # Wert kommt aus .env, nie ins Frontend
        auth_header: Authorization
        auth_prefix: "token "
        allowed_methods: [GET]         # read-only als Default
        timeout: 900                   # optional, Sekunden (sonst INTEGRATION_TIMEOUT/600)
      jira:
        base_url: https://firma.atlassian.net
        auth_env: JIRA_TOKEN
        allowed_methods: [GET, POST]

Sicherheit: nur benannte Integrationen (Allowlist), Methoden-Allowlist,
Auth serverseitig injiziert, `path` wird nur an base_url angehängt (kein
absoluter URL-Override → kein SSRF auf beliebige Hosts).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

DATA_CONFIG_DIR = Path(os.environ.get("DATA_CONFIG_DIR", "/workspace/config"))
INTEGRATIONS_YAML = DATA_CONFIG_DIR / "integrations.yaml"
MAX_BODY_CHARS = 100_000
# Manche Integrationen (z. B. Browser-Automationen) laufen minutenlang —
# Default darum großzügig. Global per env INTEGRATION_TIMEOUT, je Integration
# per `timeout:` in integrations.yaml übersteuerbar (Sekunden).
INTEGRATION_TIMEOUT = float(os.environ.get("INTEGRATION_TIMEOUT", "600"))
CONNECT_TIMEOUT = 15  # Verbindungsaufbau bleibt kurz — toter Host soll schnell scheitern


class IntegrationError(Exception):
    """Unbekannte Integration, nicht erlaubte Methode oder ungültiger Pfad."""


def load_integrations() -> dict[str, dict[str, Any]]:
    if not INTEGRATIONS_YAML.exists():
        return {}
    try:
        import yaml

        data = yaml.safe_load(INTEGRATIONS_YAML.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001
        # Nicht still schlucken: sonst sieht der LLM einfach eine leere Liste
        # und niemand erfährt, dass die Datei kaputt ist.
        print(f"[integrations] {INTEGRATIONS_YAML} nicht lesbar: {exc}", flush=True)
        return {}
    return data.get("integrations", {}) or {}


def list_integrations() -> list[dict[str, Any]]:
    """Für UI/Tool-Beschreibung: Namen + erlaubte Methoden, KEINE Secrets."""
    out = []
    for name, cfg in load_integrations().items():
        out.append(
            {
                "name": name,
                "base_url": cfg.get("base_url"),
                "allowed_methods": [m.upper() for m in cfg.get("allowed_methods", ["GET"])],
            }
        )
    return out


def call_integration(
    name: str, method: str = "GET", path: str = "/", body: Any | None = None
) -> dict[str, Any]:
    """Einen konfigurierten Endpunkt aufrufen. Gibt {status, body} zurück."""
    integrations = load_integrations()
    cfg = integrations.get(name)
    if not cfg:
        raise IntegrationError(f"unbekannte Integration: {name}")

    method = method.upper()
    allowed = [m.upper() for m in cfg.get("allowed_methods", ["GET"])]
    if method not in allowed:
        raise IntegrationError(f"Methode {method} für '{name}' nicht erlaubt (erlaubt: {allowed})")

    if "://" in path:
        raise IntegrationError("path muss relativ sein (kein absoluter URL-Override)")

    import httpx

    headers = {}
    auth_env = cfg.get("auth_env")
    if auth_env and os.environ.get(auth_env):
        header = cfg.get("auth_header", "Authorization")
        prefix = cfg.get("auth_prefix", "")
        headers[header] = f"{prefix}{os.environ[auth_env]}"

    url = cfg["base_url"].rstrip("/") + "/" + path.lstrip("/")
    timeout = httpx.Timeout(float(cfg.get("timeout", INTEGRATION_TIMEOUT)), connect=CONNECT_TIMEOUT)
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.request(method, url, headers=headers, json=body if body else None)
    except Exception as exc:  # noqa: BLE001
        raise IntegrationError(f"Aufruf fehlgeschlagen: {exc}") from exc

    voll = resp.text
    gekuerzt = len(voll) > MAX_BODY_CHARS
    text = voll[:MAX_BODY_CHARS]
    if gekuerzt:
        # Sichtbar kürzen: ein stumm abgeschnittener Body sieht für das Modell
        # aus wie eine vollständige (und damit falsch interpretierte) Antwort.
        text += f"\n…[gekürzt auf {MAX_BODY_CHARS} von {len(voll)} Zeichen]"
    return {"status": resp.status_code, "body": text, "truncated": gekuerzt}
