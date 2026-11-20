"""Server-Seite der Issues #52–#65 (20.11.2026), reine Stdlib:

    cd backend && python -m tests.test_issues_52_65

  #54  Fehlstart des Watchers steht als `fehlstart` im Ereignis-Log, nicht als
       Handbetrieb; claim_task liefert sender + Zahl der Nachträge
  #55  Tageskosten-Grenze: Rückfrage an den Menschen, Watcher-Claim gesperrt,
       „weiter" gibt frei; teurer Lauf wird einmal gemeldet; Ereignis-Texte
  #60  Pflege archiviert ins Monatsarchiv statt zu löschen; laufende Tasks
       behalten ihre Antwort; read_responses(archiv=True) findet Altes
  #62  Modell je Task nur aus der Allowlist des Empfängers; Rolle friert ihr
       Modell ein; claim reicht beides an den Watcher; Kosten je Modell
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


def test_60_monatsarchiv_statt_loeschen(ws: Path) -> None:
    import os
    import time
    ms = _mcp_server_laden(ws)
    from app import ereignisse
    from app.mailbox import Mailbox, pflege
    _boxen(ws, "dev", "erp")
    t = _tools(ms)
    ids = []
    for text in ("alt", "neu", "laeuft"):
        g = t["send_task"](to="dev", instruction=text, sender="erp")
        t["claim_task"](task_id=g["id"], agent="dev")
        t["complete_task"](task_id=g["id"], result=f"ergebnis {text}", agent="dev")
        ids.append(g["id"])
    box = Mailbox(ws / "mailboxes", "dev")
    frueher = time.time() - 40 * 86400
    for tid in (ids[0], ids[2]):
        os.utime(box.outbox / f"{tid}-response.json", (frueher, frueher))
    # zu ids[2] liegt der Task (wieder) in Arbeit — Antwort bleibt aktiv
    (box.processing / f"{ids[2]}.json").write_text(json.dumps({"id": ids[2], "kind": "task"}))
    gescheitert = box.failed / "task-kaputt.json"
    gescheitert.write_text(json.dumps({"id": "task-kaputt", "instruction": "wichtig"}))
    os.utime(gescheitert, (frueher, frueher))
    bericht = pflege(ws / "mailboxes", stale_alter=10**9, archiv_tage=30, failed_tage=90)
    assert bericht["geloescht"] == 1, bericht
    assert gescheitert.exists(), ".failed hat die längere Frist"
    assert not (box.outbox / f"{ids[0]}-response.json").exists()
    assert (box.outbox / f"{ids[2]}-response.json").exists(), "Task läuft noch"
    archiv = box.archiv_lesen("outbox")
    assert [e["task_id"] for e in archiv] == [ids[0]] and archiv[0]["result"] == "ergebnis alt"
    aktiv = t["read_responses"](worker="dev")
    assert ids[0] not in [r["task_id"] for r in aktiv], aktiv
    mit = t["read_responses"](worker="dev", archiv=True)
    alt = [r for r in mit if r["task_id"] == ids[0]]
    assert alt and alt[0]["archiviert"] is True, mit
    assert any(e["art"] == "pflege" for e in ereignisse.lies(ws / "mailboxes", "dev")["eintraege"])
    # zweiter Pflegelauf hängt ans selbe Monatsarchiv an (gzip-Mitglieder)
    os.utime(box.outbox / f"{ids[1]}-response.json", (frueher, frueher))
    pflege(ws / "mailboxes", stale_alter=10**9, archiv_tage=30)
    assert len(box.archiv_lesen("outbox")) == 2
    assert box.speicher()["archiv"] > 0


def test_62_modell_je_task_rolle(ws: Path) -> None:
    (ws / "config" / "rollen").mkdir(parents=True)
    (ws / "config" / "rollen" / "doku.md").write_text(
        "---\nbeschreibung: Doku\nmodel: sonnet\n---\nSchreibe knapp.\n", encoding="utf-8")
    (ws / "config" / "agents.yaml").write_text(
        "agents:\n  - name: dev\n    modelle_erlaubt: [sonnet, haiku]\n"
        "    connection: {type: token}\n", encoding="utf-8")
    ms = _mcp_server_laden(ws)
    from app import verbrauch
    _boxen(ws, "dev", "erp")
    t = _tools(ms)
    try:
        import yaml  # noqa: F401
    except ImportError:
        print("    (ohne PyYAML: Allowlist-/Rollen-Teil übersprungen)")
        return
    nein = t["send_task"](to="dev", instruction="x", sender="erp", model="opus")
    assert "nicht freigegeben" in nein.get("error", "") and "sonnet" in nein["error"], nein
    assert "error" in t["send_task"](to="dev", instruction="x", sender="erp", model="so nnet; rm")
    g = t["send_task"](to="dev", instruction="x", sender="erp", model="haiku", rolle="doku")
    claim = t["claim_task"](task_id=g["id"], agent="dev", watcher=True)
    assert (claim["model"], claim["rollen_model"]) == ("haiku", "sonnet"), claim
    t["complete_task"](task_id=g["id"], result="ok", agent="dev",
                       verbrauch={"input_tokens": 10, "total_cost_usd": 0.5},
                       lauf={"modell": "claude-haiku-4-5", "session_id": "sitz-0001-aaaa"})
    antworten = t["read_responses"](worker="dev")
    agg = verbrauch.aggregiere(antworten)
    assert agg["modelle"] == [{"modell": "claude-haiku-4-5", "tasks": 1, "tokens": 10,
                               "kosten": 0.5}], agg["modelle"]


def test_55_kostendeckel_und_sichtbarkeit(ws: Path) -> None:
    ms = _mcp_server_laden(ws)
    from app import ereignisse, kosten
    from app.mailbox import Mailbox
    root = ws / "mailboxes"
    _boxen(ws, "dev", "erp", "orchestrator")
    t = _tools(ms)

    def lauf(kosten_usd: float, **lauf_felder) -> str:
        g = t["send_task"](to="dev", instruction="x", sender="erp")
        claim = t["claim_task"](task_id=g["id"], agent="dev", watcher=True)
        assert "error" not in claim, claim
        t["complete_task"](task_id=g["id"], result="ok", agent="dev",
                           verbrauch={"input_tokens": 5, "total_cost_usd": kosten_usd},
                           lauf={"session_id": "sitz-0001-aaaa", **lauf_felder})
        return g["id"]

    lauf(4.0, sitzung="fortgesetzt", pause=5400, kontext=225000, cache_kalt=True)
    texte = [e["text"] for e in ereignisse.lies(root, "dev")["eintraege"]]
    assert any("Pause 90 min" in x and "Cache kalt" in x for x in texte), texte
    assert any("auffällig teuer" in x for x in texte), texte
    stand = kosten.pruefe(root, "dev", max_tag=10, max_lauf=3)
    assert stand == {"heute": 4.0, "grenze": 10.0, "gesperrt": False}, stand
    teuer = [e for e in ereignisse.lies(root, "dev", art="kosten")["eintraege"]]
    assert len(teuer) == 1 and "Grenze je Lauf" in teuer[0]["text"], teuer
    kosten.pruefe(root, "dev", max_tag=10, max_lauf=3)
    assert len(ereignisse.lies(root, "dev", art="kosten")["eintraege"]) == 1, "nur einmal melden"
    lauf(7.0)
    stand = kosten.pruefe(root, "dev", max_tag=10)
    assert stand["gesperrt"] is True, stand
    fragen = [e for e in _inbox(ws, "orchestrator") if e["kind"] == "question"]
    assert len(fragen) == 1 and fragen[0]["options"] == ["weiter", "stopp"], fragen
    kosten.pruefe(root, "dev", max_tag=10)
    assert len([e for e in _inbox(ws, "orchestrator") if e["kind"] == "question"]) == 1
    # Automatik bekommt nichts Neues, der Handbetrieb schon
    g = t["send_task"](to="dev", instruction="noch einer", sender="erp")
    nein = t["claim_task"](task_id=g["id"], agent="dev", watcher=True)
    assert nein.get("kosten_gesperrt") is True, nein
    # Mensch antwortet „weiter" → weiteres Tagesbudget
    Mailbox(root, "orchestrator").beantworte_frage(fragen[0]["id"], "weiter")
    stand = kosten.pruefe(root, "dev", max_tag=10)
    assert stand == {"heute": 11.0, "grenze": 21.0, "gesperrt": False}, stand
    assert "error" not in t["claim_task"](task_id=g["id"], agent="dev", watcher=True)
    assert not [e for e in _inbox(ws, "dev") if e["kind"] == "answer"], "Antwort archiviert"


TESTS = [test_55_kostendeckel_und_sichtbarkeit,
         test_62_modell_je_task_rolle,
         test_60_monatsarchiv_statt_loeschen,
         test_54_fehlstart_ist_kein_handbetrieb,
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
