"""Rollen + nicht_vor ÜBER den MCP-Tool-Layer (Review-Testlücke: E2E).

Nutzt denselben FastMCP-Stub wie test_mcp_tools — getestet wird der Weg, den
das Orchestrator-LLM wirklich geht: send_task(rolle=…) friert die Rollen-
Felder serverseitig in den Envelope, nicht_vor wird tz-behaftet eingefroren
(P1-5) und claim_task lehnt Geplantes mit einem Fehler-Dict ab.

    cd backend && python -m tests.test_mcp_rollen_zeit
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

from tests.test_mcp_tools import _mcp_server_laden, _tools  # noqa: E402

ROLLE = """---
beschreibung: Nur prüfen
permission_mode: plan
allowed_tools: [Read]
---
Prüfe alles doppelt.
"""


def _aufbau():
    ws = Path(tempfile.mkdtemp(prefix="mcp-rollen-"))
    (ws / "mailboxes" / "worker").mkdir(parents=True)
    (ws / "config" / "rollen").mkdir(parents=True)
    (ws / "config" / "rollen" / "review.md").write_text(ROLLE, encoding="utf-8")
    t = _tools(_mcp_server_laden(ws))
    return ws, t


def _inbox_env(ws: Path, task_id: str) -> dict:
    return json.loads((ws / "mailboxes" / "worker" / "inbox" / f"{task_id}.json")
                      .read_text(encoding="utf-8"))


def test_rolle_wird_serverseitig_eingefroren() -> None:
    ws, t = _aufbau()
    try:
        r = t["send_task"](to="worker", instruction="bau", rolle="review")
        assert "error" not in r, r
        env = _inbox_env(ws, r["id"])
        assert env["rolle"] == "review"
        assert env["rollen_prompt"] == "Prüfe alles doppelt."
        rollen_felder = {k: v for k, v in env.items() if k.startswith("rollen_")}
        assert "plan" in json.dumps(rollen_felder), rollen_felder
        assert "Read" in json.dumps(rollen_felder), rollen_felder
    finally:
        shutil.rmtree(ws, ignore_errors=True)
    print("OK test_rolle_wird_serverseitig_eingefroren")


def test_unbekannte_rolle_ist_fehler() -> None:
    ws, t = _aufbau()
    try:
        r = t["send_task"](to="worker", instruction="bau", rolle="gibtsnicht")
        assert r.get("error"), r
        assert not list((ws / "mailboxes" / "worker" / "inbox").glob("task-*.json"))
    finally:
        shutil.rmtree(ws, ignore_errors=True)
    print("OK test_unbekannte_rolle_ist_fehler")


def test_nicht_vor_wird_tz_eingefroren_und_claim_lehnt_ab() -> None:
    ws, t = _aufbau()
    try:
        r = t["send_task"](to="worker", instruction="später",
                           nicht_vor="2099-01-01T00:00")  # NAIV
        assert "error" not in r, r
        env = _inbox_env(ws, r["id"])
        geparst = datetime.fromisoformat(env["nicht_vor"])
        assert geparst.tzinfo is not None, env["nicht_vor"]  # P1-5: eingefroren
        c = t["claim_task"](task_id=r["id"], agent="worker")
        assert isinstance(c, dict) and c.get("error"), c
        # liegt weiter unclaimt in der Inbox
        assert (ws / "mailboxes" / "worker" / "inbox" / f"{r['id']}.json").exists()
    finally:
        shutil.rmtree(ws, ignore_errors=True)
    print("OK test_nicht_vor_wird_tz_eingefroren_und_claim_lehnt_ab")


def test_nicht_vor_vergangenheit_laeuft_sofort() -> None:
    ws, t = _aufbau()
    try:
        r = t["send_task"](to="worker", instruction="jetzt",
                           nicht_vor="2020-01-01T00:00")
        c = t["claim_task"](task_id=r["id"], agent="worker")
        assert not (isinstance(c, dict) and c.get("error")), c
        kaputt = t["send_task"](to="worker", instruction="x", nicht_vor="morgen früh")
        assert kaputt.get("error"), kaputt
    finally:
        shutil.rmtree(ws, ignore_errors=True)
    print("OK test_nicht_vor_vergangenheit_laeuft_sofort")


if __name__ == "__main__":
    test_rolle_wird_serverseitig_eingefroren()
    test_unbekannte_rolle_ist_fehler()
    test_nicht_vor_wird_tz_eingefroren_und_claim_lehnt_ab()
    test_nicht_vor_vergangenheit_laeuft_sofort()
    print("alle 4 Rollen/nicht_vor-MCP-Tests grün")
