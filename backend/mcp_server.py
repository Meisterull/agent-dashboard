"""MCP-Server: das Tool-Belt des Orchestrators und der Agenten.

Designentscheidung (siehe PROJECT.md → MCP-Server):
  (1) MCP = Werkzeugkasten des Orchestrators/Koordinators.  <-- DIESE.
      Eine Tool-Schicht für Claude/OpenRouter/Ollama gleichermaßen.
  (2) MCP als Transport zu den Agenten = später; aktuell macht das die Mailbox.

Tool-Gruppen:
  - Delegation:    list_agents, send_task (create_task als Alias), read_responses
  - Agent-↔-Agent: send_message, ask, answer, inbox  (killt das Fenster-Wechseln)
  - Projektdateien: write_project_file, read_project_file
  - Integrationen:  list_integrations, call_integration  (config-getrieben, generisch)

Transport: Streamable-HTTP, 127.0.0.1:9000 — intern hinter nginx, nicht
veröffentlicht. Alle Pfade gegen WORKSPACE_DIR gehärtet (kein Traversal).
"""
from __future__ import annotations

import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from app import integrations
from app.mailbox import Mailbox, Task, new_id, normalize_envelope

WORKSPACE = Path(os.environ.get("WORKSPACE_DIR", "/workspace")).resolve()
MAILBOX_ROOT = WORKSPACE / "mailboxes"
PROJECTS_ROOT = WORKSPACE / "projects"

mcp = FastMCP(
    "agent-dashboard",
    host=os.environ.get("MCP_HOST", "127.0.0.1"),
    port=int(os.environ.get("MCP_PORT", "9000")),
)


def _safe(root: Path, *parts: str) -> Path:
    p = (root.joinpath(*parts)).resolve()
    if not (p == root or root in p.parents):
        raise ValueError(f"Pfad verlässt erlaubten Bereich: {p}")
    return p


# --- Delegation ------------------------------------------------------------

@mcp.tool()
def list_agents() -> list[str]:
    """Listet die konfigurierten Agenten (anhand der Mailbox-Ordner)."""
    if not MAILBOX_ROOT.exists():
        return []
    return sorted(p.name for p in MAILBOX_ROOT.iterdir() if p.is_dir())


@mcp.tool()
def send_task(
    to: str,
    instruction: str,
    sender: str = "orchestrator",
    project: str | None = None,
    files: list[str] | None = None,
) -> dict:
    """Einen Arbeitsauftrag in die Inbox eines Agenten legen.

    `sender` ist, wer delegiert (z.B. ein Koordinator-Agent). Nur diese
    task-Envelopes führt der Watcher auf der Agent-Seite tatsächlich aus.
    """
    task = Task(
        task_id=new_id("task"),
        agent=to,
        instruction=instruction,
        project=project,
        files=files or [],
        sender=sender,
    )
    Mailbox(MAILBOX_ROOT, to).put_task(task)
    return {"id": task.task_id, "to": to, "status": "pending"}


@mcp.tool()
def create_task(agent: str, instruction: str, project: str | None = None) -> dict:
    """Alias für send_task mit sender=orchestrator (Rückwärtskompatibilität)."""
    return send_task(to=agent, instruction=instruction, sender="orchestrator", project=project)


@mcp.tool()
def read_responses(agent: str) -> list[dict]:
    """Liest alle Rückmeldungen aus der Outbox eines Agenten (erledigte Tasks)."""
    return Mailbox(MAILBOX_ROOT, agent).read_responses()


# --- Agent-↔-Agent-Kommunikation ------------------------------------------

@mcp.tool()
def send_message(to: str, text: str, sender: str = "orchestrator") -> dict:
    """Informativen Hinweis an einen anderen Agenten schicken (keine Aufgabe)."""
    return Mailbox(MAILBOX_ROOT, to).post(
        {"kind": "message", "sender": sender, "to": to, "text": text}
    )


@mcp.tool()
def ask(to: str, question: str, sender: str = "orchestrator", reply_to: str | None = None) -> dict:
    """Eine Rückfrage stellen, die eine Antwort braucht (Status needs_confirm).

    Damit fragt ein Worker z.B. den Koordinator nach Klärung — oder der
    Koordinator den Nutzer. Im Dashboard erscheint das als offene Rückfrage.
    """
    return Mailbox(MAILBOX_ROOT, to).post(
        {
            "kind": "question",
            "sender": sender,
            "to": to,
            "text": question,
            "status": "needs_confirm",
            "reply_to": reply_to,
        }
    )


@mcp.tool()
def answer(to: str, text: str, sender: str = "orchestrator", reply_to: str | None = None) -> dict:
    """Eine Rückfrage beantworten. `reply_to` = id der beantworteten question."""
    return Mailbox(MAILBOX_ROOT, to).post(
        {"kind": "answer", "sender": sender, "to": to, "text": text, "reply_to": reply_to}
    )


@mcp.tool()
def inbox(agent: str, kind: str | None = None) -> list[dict]:
    """Eingehende Envelopes eines Agenten lesen (Tasks + Nachrichten + Rückfragen).

    Ein Koordinator nutzt das, um zu sehen, was Worker ihm geschickt haben —
    statt dass der Mensch zwischen Fenstern hin- und herkopiert.
    """
    return [normalize_envelope(e) for e in Mailbox(MAILBOX_ROOT, agent).read_inbox(kind)]


# --- Projektdateien --------------------------------------------------------

@mcp.tool()
def write_project_file(project: str, relpath: str, content: str) -> dict:
    """Schreibt eine Datei unter /workspace/projects/<project>/<relpath>."""
    target = _safe(PROJECTS_ROOT, project, relpath)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return {"path": str(target), "bytes": len(content.encode("utf-8"))}


@mcp.tool()
def read_project_file(project: str, relpath: str) -> str:
    """Liest eine Datei unter /workspace/projects/<project>/<relpath>."""
    return _safe(PROJECTS_ROOT, project, relpath).read_text(encoding="utf-8")


# --- Integrationen (config-getrieben, generisch) ---------------------------

@mcp.tool()
def list_integrations() -> list[dict]:
    """Verfügbare Integrationen (Name + erlaubte Methoden), ohne Secrets."""
    return integrations.list_integrations()


@mcp.tool()
def call_integration(name: str, method: str = "GET", path: str = "/", body: dict | None = None) -> dict:
    """Einen konfigurierten HTTP-Endpunkt aufrufen (z.B. eine interne API abfragen).

    Nur in integrations.yaml definierte Integrationen + erlaubte Methoden.
    Auth wird serverseitig injiziert. Gibt {status, body} zurück.
    """
    try:
        return integrations.call_integration(name, method, path, body)
    except integrations.IntegrationError as exc:
        return {"error": str(exc)}


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
