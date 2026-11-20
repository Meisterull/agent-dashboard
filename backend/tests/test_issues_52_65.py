"""Server-Seite der Issues #52–#65 (20.11.2026), reine Stdlib:

    cd backend && python -m tests.test_issues_52_65

  #54  Fehlstart des Watchers steht als `fehlstart` im Ereignis-Log, nicht als
       Handbetrieb; claim_task liefert sender + Zahl der Nachträge
  #65  Post, die einen Automatik-Agenten nicht weckt: Hinweis an den Absender,
       EIN Ereignis je Eintrag, Wahl aus dem Dashboard gewinnt gegen agents.yaml
"""
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from tests.test_mcp_tools import _mcp_server_laden, _tools


def _boxen(ws: Path, *namen: str) -> None:
    for n in namen:
        (ws / "mailboxes" / n / "inbox").mkdir(parents=True, exist_ok=True)


def _inbox(ws: Path, agent: str) -> list[dict]:
    ordner = ws / "mailboxes" / agent / "inbox"
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(ordner.glob("*.json"))]


def _settings(ws: Path, **werte) -> None:
    (ws / "config").mkdir(parents=True, exist_ok=True)
    (ws / "config" / "settings.json").write_text(json.dumps(werte), encoding="utf-8")


def test_65_absender_erfaehrt_dass_seine_post_nicht_weckt(ws: Path) -> None:
    ms = _mcp_server_laden(ws)
    from app import weckruf
    _boxen(ws, "dev", "erp")
    t = _tools(ms)
    # Automatik aus: jemand liest von Hand — kein Hinweis
    assert "hinweis" not in t["send_message"](to="dev", text="hallo", sender="erp")
    _settings(ws, automatik={"dev": True})
    gesendet = t["send_message"](to="dev", text="eilig", sender="erp")
    assert "send_task" in gesendet["hinweis"], gesendet
    assert "hinweis" in t["ask"](to="dev", question="ok?", sender="erp")
    # genau einmal je Eintrag melden
    neu = weckruf.melde_ungeweckt(ws / "mailboxes", "dev", [])
    assert len(neu) == 3, neu
    assert weckruf.melde_ungeweckt(ws / "mailboxes", "dev", []) == []
    # Wahl aus dem Dashboard gewinnt gegen agents.yaml
    _settings(ws, automatik={"dev": True}, automatik_weckt={"dev": ["message"]})
    assert "hinweis" not in t["send_message"](to="dev", text="nochmal", sender="erp")
    assert "hinweis" in t["ask"](to="dev", question="und?", sender="erp")
    cfg = {"name": "dev", "automatik_weckt": ["message", "response", "question"]}
    assert weckruf.weck_arten(cfg, {"automatik_weckt": {"dev": []}}) == []
    assert weckruf.weck_arten(cfg, {}) == ["message", "question", "response"]


def test_54_fehlstart_ist_kein_handbetrieb(ws: Path) -> None:
    ms = _mcp_server_laden(ws)
    from app import ereignisse
    _boxen(ws, "dev", "erp")
    t = _tools(ms)
    g = t["send_task"](to="dev", instruction="x", sender="erp", project="Mess Daten")
    claim = t["claim_task"](task_id=g["id"], agent="dev", watcher=True)
    assert (claim["sender"], claim["nachtraege"]) == ("erp", 0), claim
    t["complete_task"](task_id=g["id"], result="FEHLER", status="error", agent="dev",
                       lauf={"fehlstart": "project-Verzeichnis fehlt: /home/x/Mess Daten"})
    log = ereignisse.lies(ws / "mailboxes", "dev")["eintraege"]
    assert [e["art"] for e in log] == ["fehlstart"], log
    assert log[0]["schwere"] == "fehler" and "Start abgebrochen" in log[0]["text"], log
    assert "Mess Daten" in log[0]["text"], log
    # Sitzung (kein Watcher-Anspruch) ohne Laufdaten bleibt Handbetrieb
    g2 = t["send_task"](to="dev", instruction="y", sender="erp")
    t["claim_task"](task_id=g2["id"], agent="dev")
    t["complete_task"](task_id=g2["id"], result="ok", agent="dev")
    neu = ereignisse.lies(ws / "mailboxes", "dev")["eintraege"][0]
    assert neu["art"] == "lauf" and neu["details"].get("handbetrieb") is True, neu
    # Watcher-Anspruch ohne Zahlen (Kind schloss selbst ab): Lauf, kein Handbetrieb
    g3 = t["send_task"](to="dev", instruction="z", sender="erp")
    t["claim_task"](task_id=g3["id"], agent="dev", watcher=True)
    t["complete_task"](task_id=g3["id"], result="ok", agent="dev")
    neu = ereignisse.lies(ws / "mailboxes", "dev")["eintraege"][0]
    assert neu["art"] == "lauf" and not neu["details"].get("handbetrieb"), neu


TESTS = [test_54_fehlstart_ist_kein_handbetrieb,
         test_65_absender_erfaehrt_dass_seine_post_nicht_weckt]


def main() -> None:
    for test in TESTS:
        tmp = Path(tempfile.mkdtemp(prefix="issues-52-65-"))
        try:
            test(tmp)
            print(f"OK  {test.__name__}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    print(f"alle {len(TESTS)} Tests zu den Issues #52–#65 grün")


if __name__ == "__main__":
    main()
