"""Provider-neutrale LLM-Schicht (Multi-Provider).

Ein Agentic-Loop (`run_turn`), zwei Backends:
  - ollama    : lokal, über reines HTTP (urllib, KEINE Extra-Abhängigkeit)
  - anthropic : Claude über das offizielle SDK (lazy importiert)

Die History wird in einem **neutralen** Format gehalten und erst beim Aufruf
ins Provider-Format übersetzt. So teilen sich Claude und Ollama denselben Loop
und dieselbe Mailbox-/Tool-Anbindung.

Neutrales Nachrichtenformat:
  {"role": "user", "content": str}
  {"role": "assistant", "content": str, "tool_calls": [{"id","name","input"}],
   "_anthropic": [<rohe Content-Blöcke>]}   # nur auf dem Anthropic-Pfad
  {"role": "tool", "tool_call_id": str, "name": str, "content": str}

`_anthropic` hält die Provider-rohen Blöcke einer Assistant-Antwort (inklusive
Thinking samt Signatur). Beim Fortsetzen einer Tool-Runde müssen sie unverändert
zurückgereicht werden — aus Text + tool_calls neu zusammengebaute Nachrichten
verlieren die Signatur und die API weist die Fortsetzung ab. Andere Provider
ignorieren das Feld.

Tools (neutral): [{"name","description","input_schema"}]  (= MCP-Form).
"""
from __future__ import annotations

import asyncio
import json
import os
import urllib.request
from typing import Any, Awaitable, Callable

DEFAULT_MAX_TOKENS = 16000

# Obergrenze für LLM-Runden pro Nutzer-Eingabe. Ohne Cap dreht ein Modell,
# das immer weiter Tools ruft, endlos (jede Runde = ein LLM-Call).
MAX_TOOL_ROUNDS = int(os.environ.get("ORCH_MAX_TOOL_ROUNDS", "25"))

# Obergrenze je Tool-Ergebnis in der History. Ein großes read_project_file
# fährt sonst in JEDER weiteren Runde erneut mit — Kosten, Latenz und
# irgendwann ein hartes Kontextlimit.
MAX_TOOL_RESULT_CHARS = int(os.environ.get("ORCH_MAX_TOOL_RESULT", "30000"))

# Ollama-Antwortzeit: Cloud-Modelle über den lokalen Ollama-Proxy können lange
# brauchen, lokale Modelle auf schwacher Hardware auch.
OLLAMA_TIMEOUT = float(os.environ.get("OLLAMA_TIMEOUT", "180"))


def kappe_tool_ergebnis(text: str, grenze: int = MAX_TOOL_RESULT_CHARS) -> str:
    """Zu lange Tool-Ausgaben kürzen — sichtbar, nicht heimlich."""
    if len(text) <= grenze:
        return text
    return text[:grenze] + f"\n…[gekürzt: {len(text)} Zeichen insgesamt]"


def repariere_history(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """History nach einem abgebrochenen Turn wieder gültig machen.

    Ein assistant-Turn mit tool_calls braucht zu JEDEM Call ein tool-Result —
    bricht die Runde vorher ab, lehnt der nächste Provider-Call die Nachricht
    ab und die Session wäre dauerhaft kaputt. Statt die Runde (und damit das
    Wissen um bereits ausgeführte Tools, etwa ein verschicktes send_task)
    wegzuwerfen, tragen wir die fehlenden Ergebnisse als "abgebrochen" nach.
    """
    beantwortet = {m.get("tool_call_id") for m in messages if m.get("role") == "tool"}
    out: list[dict[str, Any]] = []
    for m in messages:
        out.append(m)
        if m.get("role") != "assistant":
            continue
        for tc in m.get("tool_calls") or []:
            if tc.get("id") in beantwortet:
                continue
            out.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.get("id"),
                    "name": tc.get("name"),
                    "content": "[Abgebrochen: Fehler im Orchestrator-Turn — Ergebnis "
                               "unbekannt, das Tool wurde ggf. bereits ausgeführt.]",
                }
            )
    return out


def provider_from_env() -> dict[str, Any]:
    """Provider-Konfiguration aus Umgebungsvariablen.

    ORCH_PROVIDER = ollama | anthropic (Default: anthropic).
    """
    provider = os.environ.get("ORCH_PROVIDER", "anthropic").lower()
    if provider == "ollama":
        return {
            "provider": "ollama",
            "base_url": os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"),
            "model": os.environ.get("OLLAMA_MODEL", "gpt-oss:120b-cloud"),
        }
    return {
        "provider": "anthropic",
        "model": os.environ.get("ORCH_MODEL", "claude-opus-4-8"),
    }


def needs_api_key(cfg: dict[str, Any]) -> bool:
    return cfg["provider"] == "anthropic"


def apply_settings(cfg: dict[str, Any], settings: dict[str, Any] | None) -> dict[str, Any]:
    """Dashboard-Settings über die Env-Defaults legen (Live-Modellwahl).

    Aktuell nur das Modell — der Provider bleibt env-bestimmt, damit Keys/Base-URL
    eine Wahrheit haben. Leeres `orch_model` = Env-Default.
    """
    model = (settings or {}).get("orch_model")
    if model:
        return {**cfg, "model": model}
    return cfg


def list_models(cfg: dict[str, Any]) -> list[str]:
    """Verfügbare Modelle des aktiven Providers (best-effort, [] bei Fehler).

    Ollama: /api/tags (listet auch :cloud-Modelle). Anthropic: keine Auflistung.
    """
    if cfg["provider"] != "ollama":
        return []
    try:
        req = urllib.request.Request(cfg["base_url"].rstrip("/") + "/api/tags")
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return sorted(m["name"] for m in data.get("models", []) if m.get("name"))
    except Exception:  # noqa: BLE001 — Listing ist optional, nie chat-blockierend
        return []


# --- Ollama (Standardlib-HTTP) --------------------------------------------

def _ollama_messages(system: str, messages: list[dict]) -> list[dict]:
    out = [{"role": "system", "content": system}]
    for m in messages:
        if m["role"] == "assistant":
            msg: dict[str, Any] = {"role": "assistant", "content": m.get("content") or ""}
            if m.get("tool_calls"):
                msg["tool_calls"] = [
                    {"function": {"name": tc["name"], "arguments": tc["input"]}}
                    for tc in m["tool_calls"]
                ]
            out.append(msg)
        elif m["role"] == "tool":
            out.append({"role": "tool", "tool_name": m.get("name"), "content": m["content"]})
        else:
            out.append({"role": "user", "content": m["content"]})
    return out


def _ollama_tools(tools: list[dict]) -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t["input_schema"],
            },
        }
        for t in tools
    ]


def _ollama_call_sync(cfg: dict, system: str, messages: list[dict], tools: list[dict]) -> dict:
    payload = {
        "model": cfg["model"],
        "messages": _ollama_messages(system, messages),
        "tools": _ollama_tools(tools),
        "stream": False,
    }
    req = urllib.request.Request(
        cfg["base_url"].rstrip("/") + "/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"content-type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=OLLAMA_TIMEOUT) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    msg = data.get("message", {})
    tool_calls = []
    for i, tc in enumerate(msg.get("tool_calls", []) or []):
        fn = tc.get("function", {})
        args = fn.get("arguments", {})
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}
        tool_calls.append({"id": f"call_{i}", "name": fn.get("name"), "input": args})
    return {"text": msg.get("content") or "", "tool_calls": tool_calls}


# --- Anthropic (SDK, lazy) -------------------------------------------------

def _anthropic_blocks(messages: list[dict]) -> list[dict]:
    out: list[dict] = []
    for m in messages:
        if m["role"] == "user":
            out.append({"role": "user", "content": [{"type": "text", "text": m["content"]}]})
        elif m["role"] == "assistant":
            roh = m.get("_anthropic")
            if roh:
                # Unverändert zurückreichen: Thinking-Blöcke tragen eine
                # Signatur, die beim Neubau aus Text+tool_calls verloren ginge.
                out.append({"role": "assistant", "content": roh})
                continue
            blocks: list[dict] = []
            if m.get("content"):
                blocks.append({"type": "text", "text": m["content"]})
            for tc in m.get("tool_calls", []) or []:
                blocks.append(
                    {"type": "tool_use", "id": tc["id"], "name": tc["name"], "input": tc["input"]}
                )
            if not blocks:
                # Leere Assistant-Nachricht: die API lehnt sie ab und die
                # Session wäre ab da dauerhaft kaputt — lieber auslassen.
                continue
            out.append({"role": "assistant", "content": blocks})
        elif m["role"] == "tool":
            out.append(
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": m["content"]}
                    ],
                }
            )
    return _verschmelze_user(out)


def _verschmelze_user(msgs: list[dict]) -> list[dict]:
    """Aufeinanderfolgende user-Nachrichten zu einer zusammenfassen.

    Tool-Ergebnisse sind in Anthropic-Form ebenfalls user-Nachrichten; nach
    einem abgebrochenen Turn (siehe main._reparierte_history) können ein
    tool_result und die nächste Nutzereingabe direkt aufeinander folgen.
    tool_result-Blöcke müssen dabei vorn stehen.
    """
    out: list[dict] = []
    for m in msgs:
        if out and m["role"] == "user" and out[-1]["role"] == "user":
            zusammen = list(out[-1]["content"]) + list(m["content"])
            zusammen.sort(key=lambda b: 0 if b.get("type") == "tool_result" else 1)
            out[-1] = {"role": "user", "content": zusammen}
        else:
            out.append(m)
    return out


_anthropic_client = None


async def _anthropic_call(cfg: dict, system: str, messages: list[dict], tools: list[dict]) -> dict:
    global _anthropic_client
    if _anthropic_client is None:
        from anthropic import AsyncAnthropic

        _anthropic_client = AsyncAnthropic()
    resp = await _anthropic_client.messages.create(
        model=cfg["model"],
        max_tokens=DEFAULT_MAX_TOKENS,
        thinking={"type": "adaptive"},
        output_config={"effort": "high"},
        # Cache-Punkt hinter System-Prompt + Tool-Liste: beides ist über eine
        # Session byte-stabil und fährt in jeder der bis zu 25 Runden erneut mit.
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        tools=tools,
        messages=_anthropic_blocks(messages),
    )
    text_parts, tool_calls = [], []
    for block in resp.content:
        if block.type == "text":
            text_parts.append(block.text)
        elif block.type == "tool_use":
            tool_calls.append({"id": block.id, "name": block.name, "input": block.input})
    if resp.stop_reason == "max_tokens" and not tool_calls:
        text_parts.append(
            f"\n[Abgeschnitten: Antwortlimit von {DEFAULT_MAX_TOKENS} Tokens erreicht.]"
        )
    return {
        "text": "\n".join(text_parts),
        "tool_calls": tool_calls,
        # Rohblöcke für die Fortsetzung aufheben (Thinking + Signatur).
        "raw": _rohbloecke(resp),
    }


def _rohbloecke(resp) -> list[dict] | None:
    """Anthropic-Content-Blöcke als JSON-taugliche Dicts (für die History)."""
    try:
        return [b.model_dump(mode="json", exclude_none=True) for b in resp.content]
    except Exception:  # noqa: BLE001 — ohne Rohblöcke bauen wir sie eben neu
        return None


# --- Gemeinsamer Loop ------------------------------------------------------

async def _complete(cfg: dict, system: str, messages: list[dict], tools: list[dict]) -> dict:
    if cfg["provider"] == "ollama":
        return await asyncio.to_thread(_ollama_call_sync, cfg, system, messages, tools)
    return await _anthropic_call(cfg, system, messages, tools)


async def run_turn(
    cfg: dict,
    system: str,
    messages: list[dict],
    tools: list[dict],
    call_tool: Callable[[str, dict], Awaitable[str]],
) -> dict[str, Any]:
    """Eine Nutzer-Eingabe vollständig abarbeiten (inkl. aller Tool-Calls).

    `messages` wird in-place (neutral) fortgeschrieben. `call_tool(name, input)`
    führt ein Tool aus und liefert den Ergebnistext. Rückgabe:
    {"text": <Antwort>, "tool_calls": [{name, input}, ...]}.
    """
    texts: list[str] = []
    log: list[dict] = []
    for round_no in range(1, MAX_TOOL_ROUNDS + 1):
        result = await _complete(cfg, system, messages, tools)
        if result["text"]:
            texts.append(result["text"])
        eintrag = {
            "role": "assistant",
            "content": result["text"],
            "tool_calls": result["tool_calls"],
        }
        if result.get("raw"):
            eintrag["_anthropic"] = result["raw"]
        messages.append(eintrag)
        if not result["tool_calls"]:
            return {"text": "\n".join(texts), "tool_calls": log}
        # Letzte Runde: Tools nicht mehr ausführen, aber trotzdem tool_results
        # anhängen — sonst hinterlässt der Abbruch dangling tool_calls in der
        # History und der nächste Provider-Call (Anthropic) schlägt fehl.
        aborted = round_no == MAX_TOOL_ROUNDS
        for tc in result["tool_calls"]:
            log.append({"name": tc["name"], "input": tc["input"], "skipped": aborted})
            if aborted:
                output = (
                    f"Abgebrochen: Tool-Runden-Limit ({MAX_TOOL_ROUNDS}) erreicht — nicht ausgeführt."
                )
            else:
                try:
                    output = await call_tool(tc["name"], tc["input"] or {})
                except Exception as exc:  # noqa: BLE001
                    # Ein gescheiterter Tool-Call beendet nicht den ganzen Turn:
                    # das Modell bekommt den Fehler als Ergebnis und kann darauf
                    # reagieren (anderes Tool, Rückfrage, Abbruch mit Erklärung).
                    output = f"[TOOL-FEHLER] {type(exc).__name__}: {exc}"
                    log[-1]["error"] = str(exc)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "name": tc["name"],
                    "content": kappe_tool_ergebnis(output),
                }
            )
    texts.append(f"[Abbruch: Tool-Runden-Limit ({MAX_TOOL_ROUNDS}) erreicht — Aufgabe evtl. unvollständig.]")
    return {"text": "\n".join(texts), "tool_calls": log}
