"""SQLite-Persistenz für Chat-Sessions (/workspace/chat.db).

Speichert die komplette neutrale LLM-History (user/assistant+tool_calls/tool)
pro Session — damit überleben Gespräche Container-Neustarts und der
Orchestrator behält seinen Kontext. Nach jedem Turn wird die Session komplett
neu geschrieben (Sessions sind klein, das ist robuster als Delta-Tracking).
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

DB_PATH = Path(os.environ.get("WORKSPACE_DIR", "/workspace")) / "chat.db"

_lock = threading.Lock()
_init_done = False


def _conn() -> sqlite3.Connection:
    global _init_done
    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA journal_mode=WAL")
    if not _init_done:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
              id      TEXT PRIMARY KEY,
              title   TEXT NOT NULL DEFAULT '',
              created REAL NOT NULL,
              updated REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS messages (
              session_id TEXT NOT NULL,
              idx        INTEGER NOT NULL,
              payload    TEXT NOT NULL,
              PRIMARY KEY (session_id, idx)
            );
            """
        )
        _init_done = True
    return con


def load(session_id: str) -> list[dict[str, Any]]:
    with _lock, _conn() as con:
        rows = con.execute(
            "SELECT payload FROM messages WHERE session_id=? ORDER BY idx",
            (session_id,),
        ).fetchall()
    return [json.loads(r[0]) for r in rows]


def save(session_id: str, messages: list[dict[str, Any]]) -> None:
    title = ""
    for m in messages:
        if m.get("role") == "user":
            title = str(m.get("content", ""))[:80]
            break
    now = time.time()
    with _lock, _conn() as con:
        con.execute(
            "INSERT INTO sessions (id, title, created, updated) VALUES (?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET updated=excluded.updated, title=excluded.title",
            (session_id, title, now, now),
        )
        con.execute("DELETE FROM messages WHERE session_id=?", (session_id,))
        con.executemany(
            "INSERT INTO messages (session_id, idx, payload) VALUES (?,?,?)",
            [
                (session_id, i, json.dumps(m, ensure_ascii=False))
                for i, m in enumerate(messages)
            ],
        )


def list_sessions(limit: int = 50) -> list[dict[str, Any]]:
    with _lock, _conn() as con:
        rows = con.execute(
            "SELECT s.id, s.title, s.updated, "
            "(SELECT COUNT(*) FROM messages m WHERE m.session_id = s.id) "
            "FROM sessions s ORDER BY s.updated DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [
        {"id": r[0], "title": r[1], "updated": r[2], "messages": r[3]} for r in rows
    ]


def delete_session(session_id: str) -> bool:
    with _lock, _conn() as con:
        cur = con.execute("DELETE FROM sessions WHERE id=?", (session_id,))
        con.execute("DELETE FROM messages WHERE session_id=?", (session_id,))
    return cur.rowcount > 0


def display_messages(session_id: str) -> list[dict[str, Any]]:
    """History in das Anzeige-Format des Frontends übersetzen.

    Tool-Ergebnisse (role=tool) bleiben außen vor; bei Assistant-Nachrichten
    interessieren Text + Namen der Tool-Calls (wie ChatOut.tool_calls).
    """
    out = []
    for m in load(session_id):
        role = m.get("role")
        if role == "user":
            out.append({"role": "user", "text": m.get("content", "")})
        elif role == "assistant":
            entry: dict[str, Any] = {"role": "assistant", "text": m.get("content") or ""}
            if m.get("tool_calls"):
                entry["toolCalls"] = [
                    {"name": tc.get("name", "?")} for tc in m["tool_calls"]
                ]
            out.append(entry)
    return out
