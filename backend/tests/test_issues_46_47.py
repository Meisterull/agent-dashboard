"""Issues #46/#47 (02.10.2026), reine Stdlib:

    cd backend && python -m tests.test_issues_46_47

  #46  Die Pflege gibt nur auf, was ein WATCHER beansprucht hat. Ansprüche
       einer interaktiven Sitzung werden erst nach der längeren Frist
       zurückgereiht, zählen nicht und setzen den Zähler zurück; beim Aufgeben
       erfährt es auch der Bearbeiter; ein späteres echtes complete_task
       ersetzt die Abbruch-Antwort und wird neu zugestellt.
  #47  Ereignis-Log: `rueckreihung` (ein Eintrag je Runde) und `aufgegeben`
       statt eines `lauf`-Eintrags; `aufgegeben` pusht trotz Bündel-Sperre;
       Handbetrieb heißt „abgeschlossen · lag …"; ein gebundener Kanal liest
       fremde Ereignisse nur mit `darf_ereignisse_lesen`.
"""
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from app import ereignisse
from app.mailbox import CLAIM_SITZUNG, CLAIM_WATCHER, LOG_AUFGEGEBEN, Mailbox, Task
from tests.test_mcp_tools import _mcp_server_laden, _tools

H = 3600.0
URALT = "2020-01-01T00:00:00+01:00"


def _task(root: Path, task_id: str, agent: str = "worker", sender: str = "chef") -> Mailbox:
    mb = Mailbox(root, agent)
    mb.put_task(Task(task_id=task_id, agent=agent, instruction=f"auftrag {task_id}",
                     sender=sender))
    return mb


def _altern(mb: Mailbox, task_id: str, stempel: str = URALT) -> dict:
    p = mb.processing / f"{task_id}.json"
    env = json.loads(p.read_text(encoding="utf-8"))
    env["claimed_at"] = stempel
    env.pop("zuletzt_aktiv", None)
    p.write_text(json.dumps(env), encoding="utf-8")
    return env


def _inbox(root: Path, agent: str, kind: str | None = None) -> list[dict]:
    ordner = Path(root) / agent / "inbox"
    alle = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(ordner.glob("*.json"))]
    return [e for e in alle if kind is None or e.get("kind") == kind]


def _arten(root: Path, agent: str) -> list[str]:
    return [e["art"] for e in reversed(ereignisse.lies(root, agent, 200)["eintraege"])]


# --- #46 --------------------------------------------------------------------

def test_sitzung_wird_nie_aufgegeben(root: Path) -> None:
    """Der Fall aus dem Issue: eine Sitzung beansprucht, baut stundenlang,
    wird zurückgereiht, beansprucht erneut — beliebig oft, ohne Fehlschlag."""
    mb = _task(root, "task-s")
    for _ in range(6):
        env = mb.claim_task("task-s")
        assert env["claim_von"] == CLAIM_SITZUNG and "requeues" not in env, env
        _altern(mb, "task-s")
        ergebnis = mb.requeue_stale(3 * H, max_versuche=3, max_alter_sitzung=24 * H)
        assert ergebnis == {"requeued": ["task-s"], "aufgegeben": []}, ergebnis
        zurueck = json.loads((mb.inbox / "task-s.json").read_text(encoding="utf-8"))
        assert "requeues" not in zurueck and "claim_von" not in zurueck, zurueck
    assert not (mb.outbox / "task-s-response.json").exists()
    assert not _inbox(root, "chef", "response"), "kein Fehlschlag an den Auftraggeber"


def test_sitzung_hat_die_laengere_frist(root: Path) -> None:
    """5 h Ruhe: der Watcher-Anspruch ist verwaist (3 h), der Sitzungs-Anspruch
    nicht (24 h) — eine Nacht kostet nichts mehr."""
    from datetime import datetime, timedelta
    vor_5h = (datetime.now().astimezone() - timedelta(hours=5)).isoformat(timespec="seconds")
    mb = _task(root, "task-w")
    _task(root, "task-s")
    mb.claim_task("task-w", von=CLAIM_WATCHER)
    mb.claim_task("task-s")
    _altern(mb, "task-w", vor_5h)
    _altern(mb, "task-s", vor_5h)
    ergebnis = mb.requeue_stale(3 * H, max_alter_sitzung=24 * H)
    assert ergebnis["requeued"] == ["task-w"], ergebnis
    assert (mb.processing / "task-s.json").exists()
    # ohne eigene Frist gilt für beide dieselbe (Verhalten wie vor #46)
    assert mb.requeue_stale(3 * H)["requeued"] == ["task-s"]


def test_sitzungs_anspruch_setzt_den_zaehler_zurueck(root: Path) -> None:
    mb = _task(root, "task-z")
    for erwartet in (1, 2):
        mb.claim_task("task-z", von=CLAIM_WATCHER)
        _altern(mb, "task-z")
        mb.requeue_stale(3 * H)
        assert json.loads((mb.inbox / "task-z.json").read_text(encoding="utf-8"))["requeues"] == erwartet
    # jetzt übernimmt ein Mensch: sein Anspruch beweist, dass jemand lebt
    env = mb.claim_task("task-z")
    assert "requeues" not in env and env["claim_von"] == CLAIM_SITZUNG, env
    # Herzschlag (erneut) ändert die Zuordnung nicht
    mb.claim_task("task-z", erneut=True, von=CLAIM_WATCHER)
    assert json.loads((mb.processing / "task-z.json").read_text(encoding="utf-8"))["claim_von"] == CLAIM_SITZUNG


def test_aufgeben_meldet_beiden_und_spaetes_ergebnis_ersetzt(root: Path) -> None:
    """Giftiger Watcher-Task: Auftraggeber UND Bearbeiter erfahren es; kommt
    danach doch ein echtes Ergebnis, ersetzt es die Abbruch-Antwort."""
    mb = _task(root, "task-g")
    for _ in range(4):
        mb.claim_task("task-g", erneut=True, von=CLAIM_WATCHER)
        _altern(mb, "task-g")
        ergebnis = mb.requeue_stale(3 * H, max_versuche=3)
    assert ergebnis["aufgegeben"] == ["task-g"], ergebnis
    resp = json.loads((mb.outbox / "task-g-response.json").read_text(encoding="utf-8"))
    assert resp["status"] == "error" and resp["aufgegeben"] is True, resp
    assert resp["log"] == LOG_AUFGEGEBEN and "4× länger als 3 h" in resp["result"], resp
    assert "bricht offenbar reproduzierbar ab" not in resp["result"]
    assert (mb.failed / "task-g.json").exists()
    beim_chef = _inbox(root, "chef", "response")
    assert len(beim_chef) == 1 and beim_chef[0]["status"] == "error", beim_chef
    notizen = [e for e in _inbox(root, "worker", "message") if "aufgegeben" in e["text"]]
    assert len(notizen) == 1 and notizen[0]["weckt"] is False, notizen
    assert "complete_task('task-g'" in notizen[0]["text"]

    # Ereignis-Log (#47): aufgegeben statt lauf, davor drei Rückreih-Runden
    arten = _arten(root, "worker")
    assert arten == ["rueckreihung"] * 3 + ["aufgegeben"], arten
    auf = ereignisse.lies(root, "worker", art="aufgegeben")["eintraege"][0]
    assert auf["schwere"] == "fehler" and auf["task_id"] == "task-g", auf
    assert "von der Pflege aufgegeben: 4× länger als 3 h" in auf["text"], auf

    # spätes echtes Ergebnis
    assert mb.nach_aufgabe_oeffnen("task-g") is True
    assert mb.nach_aufgabe_oeffnen("task-unbekannt") is False
    mb.write_response("task-g", "doch fertig", "done")
    resp = json.loads((mb.outbox / "task-g-response.json").read_text(encoding="utf-8"))
    assert resp["status"] == "done" and resp["result"] == "doch fertig", resp
    assert "aufgegeben" not in resp and resp["to"] == "chef", resp
    assert not (mb.failed / "task-g.json").exists() and not mb.task_offen("task-g")
    beim_chef = {e["status"]: e for e in _inbox(root, "chef", "response")}
    assert sorted(beim_chef) == ["done", "error"], beim_chef
    assert "ersetzt die Abbruchmeldung" in beim_chef["done"]["hinweis"], beim_chef["done"]
    # ein normal abgeschlossener Task lässt sich NICHT wieder öffnen
    assert mb.nach_aufgabe_oeffnen("task-g") is False


def test_complete_task_nach_pflege_abbruch(ws: Path) -> None:
    """Der MCP-Weg: früher `already: true` und das Ergebnis war weg."""
    ms = _mcp_server_laden(ws)
    root = ws / "mailboxes"
    for n in ("worker", "chef"):
        (root / n).mkdir(parents=True, exist_ok=True)
    t = _tools(ms)
    g = t["send_task"](to="worker", instruction="mehrtägiger Sammelauftrag", sender="chef")
    box = Mailbox(root, "worker")
    for _ in range(4):
        claim = t["claim_task"](task_id=g["id"], agent="worker", watcher=True, erneut=True)
        assert "error" not in claim, claim
        _altern(box, g["id"])
        (box.base / ".aktiv").unlink(missing_ok=True)   # der Agent schweigt
        bericht = box.requeue_stale(3 * H)
    assert bericht["aufgegeben"] == [g["id"]], bericht

    antwort = t["complete_task"](task_id=g["id"], result="echtes Ergebnis", agent="worker")
    assert antwort.get("ersetzt_aufgabe") is True and "already" not in antwort, antwort
    resp = json.loads((box.outbox / f"{g['id']}-response.json").read_text(encoding="utf-8"))
    assert (resp["status"], resp["result"]) == ("done", "echtes Ergebnis"), resp
    assert sorted(e["status"] for e in _inbox(root, "chef", "response")) == ["done", "error"]
    # zweiter Abschluss bleibt wiederholbar wie eh und je
    nochmal = t["complete_task"](task_id=g["id"], result="echtes Ergebnis", agent="worker")
    assert nochmal.get("already") is True, nochmal


def test_claim_task_kennzeichnet_den_watcher(ws: Path) -> None:
    ms = _mcp_server_laden(ws)
    root = ws / "mailboxes"
    for n in ("worker", "chef"):
        (root / n).mkdir(parents=True, exist_ok=True)
    t = _tools(ms)
    box = Mailbox(root, "worker")
    a = t["send_task"](to="worker", instruction="a", sender="chef")
    b = t["send_task"](to="worker", instruction="b", sender="chef")
    t["claim_task"](task_id=a["id"], agent="worker", watcher=True)
    t["claim_task"](task_id=b["id"], agent="worker")
    lies = lambda tid: json.loads((box.processing / f"{tid}.json").read_text(encoding="utf-8"))
    assert lies(a["id"])["claim_von"] == CLAIM_WATCHER
    assert lies(b["id"])["claim_von"] == CLAIM_SITZUNG


# --- #47 --------------------------------------------------------------------

def test_rueckreihung_ist_ein_eintrag_je_runde(root: Path) -> None:
    mb = Mailbox(root, "worker")
    for tid in ("task-a", "task-b", "task-c"):
        _task(root, tid)
    mb.claim_task("task-a", von=CLAIM_WATCHER)
    mb.claim_task("task-b", von=CLAIM_WATCHER)
    mb.claim_task("task-c")
    for tid in ("task-a", "task-b", "task-c"):
        _altern(mb, tid)
    mb.requeue_stale(3 * H)
    eintraege = ereignisse.lies(root, "worker")["eintraege"]
    assert len(eintraege) == 1, eintraege
    e = eintraege[0]
    assert (e["art"], e["schwere"]) == ("rueckreihung", "warnung"), e
    assert e["text"].startswith("3 Tasks zurückgereiht: 3 h ohne Lebenszeichen"), e
    assert e["details"]["tasks"] == {"task-a": "1/3", "task-b": "1/3", "task-c": "sitzung"}, e
    # nichts zurückgereiht → kein Eintrag
    mb.requeue_stale(3 * H)
    assert len(ereignisse.lies(root, "worker")["eintraege"]) == 1
    # mit bekanntem letzten Lebenszeichen steht die Uhrzeit dabei
    import os
    import time
    mb.claim_task("task-a", von=CLAIM_WATCHER)
    _altern(mb, "task-a")
    marke = mb.base / ".aktiv"
    marke.touch()
    os.utime(marke, (time.time() - 4 * H, time.time() - 4 * H))
    mb.requeue_stale(3 * H)
    assert "(letztes " in ereignisse.lies(root, "worker")["eintraege"][0]["text"]


def test_handbetrieb_heisst_liegezeit(root: Path) -> None:
    """Ohne Lauf-Daten vom Watcher ist Claim → Abschluss eine Liegezeit."""
    e = ereignisse.lauf_ereignisse(root, "dev", "t1", "done",
                                   "2026-09-30T08:00:00+02:00", "2026-09-30T20:45:00+02:00",
                                   None, None)
    assert [x["text"] for x in e] == ["abgeschlossen · lag 765 min"], e
    assert e[0]["details"] == {"status": "done", "liegezeit": 45900.0, "handbetrieb": True}, e
    f = ereignisse.lauf_ereignisse(root, "dev", "t2", "error", None, None, None, None)
    assert f[0]["text"] == "abgeschlossen (error)" and f[0]["schwere"] == "fehler", f
    # mit Watcher-Daten bleibt es ein Lauf
    w = ereignisse.lauf_ereignisse(root, "dev", "t3", "done",
                                   "2026-09-30T08:00:00+02:00", "2026-09-30T08:03:00+02:00",
                                   {"input_tokens": 2000, "total_cost_usd": 0.5},
                                   {"sitzung": "neu"})
    assert w[0]["text"].startswith("Lauf done · 3 min"), w


def test_aufgegeben_pusht_trotz_sperre(root: Path) -> None:
    from app import events
    b = events.EreignisPush(sperre_s=600)
    ereignisse.schreibe(root, "dev", "rueckreihung", "5 Tasks zurückgereiht", schwere="warnung")
    assert len(b.verarbeite(root, {"dev"}, 1000.0)) == 1
    ereignisse.schreibe(root, "dev", "rueckreihung", "5 Tasks zurückgereiht", schwere="warnung")
    assert b.verarbeite(root, {"dev"}, 1100.0) == [], "Rückreihung bleibt gebündelt"
    ereignisse.schreibe(root, "dev", "aufgegeben", "von der Pflege aufgegeben: 4× …",
                        schwere="fehler", task_id="t1")
    m = b.verarbeite(root, {"dev"}, 1200.0)
    assert len(m) == 1 and "von der Pflege aufgegeben" in m[0]["text"], m


def test_agent_events_fremder_agent_nur_mit_freigabe(ws: Path) -> None:
    ms = _mcp_server_laden(ws)
    root = ws / "mailboxes"
    for n in ("lokal", "deverp", "erp"):
        (root / n).mkdir(parents=True, exist_ok=True)
    ereignisse.schreibe(root, "deverp", "aufgegeben", "von der Pflege aufgegeben", schwere="fehler")
    agenten = [{"name": "lokal"}, {"name": "deverp"}, {"name": "erp"}]
    ms.load_agents_full = lambda: agenten
    t = _tools(ms, identity="lokal")
    abgelehnt = t["agent_events"](agent="deverp")
    assert "darf_ereignisse_lesen" in abgelehnt.get("error", ""), abgelehnt
    agenten[0]["darf_ereignisse_lesen"] = ["deverp"]
    ok = t["agent_events"](agent="deverp", nur_probleme=True)
    assert ok["agent"] == "deverp" and ok["eintraege"][0]["art"] == "aufgegeben", ok
    assert "error" in t["agent_events"](agent="erp"), "nur die freigegebenen"
    agenten[0]["darf_ereignisse_lesen"] = "*"
    assert t["agent_events"](agent="erp")["agent"] == "erp"
    # das eigene Log geht immer; schreibende Tools bleiben gebunden
    assert t["agent_events"]()["agent"] == "lokal"
    assert "error" in t["complete_task"](task_id="x", result="y", agent="deverp")


def main() -> None:
    tests = [test_sitzung_wird_nie_aufgegeben,
             test_sitzung_hat_die_laengere_frist,
             test_sitzungs_anspruch_setzt_den_zaehler_zurueck,
             test_aufgeben_meldet_beiden_und_spaetes_ergebnis_ersetzt,
             test_complete_task_nach_pflege_abbruch,
             test_claim_task_kennzeichnet_den_watcher,
             test_rueckreihung_ist_ein_eintrag_je_runde,
             test_handbetrieb_heisst_liegezeit,
             test_aufgegeben_pusht_trotz_sperre,
             test_agent_events_fremder_agent_nur_mit_freigabe]
    for test in tests:
        tmp = Path(tempfile.mkdtemp(prefix="issues-46-47-"))
        try:
            test(tmp)
            print(f"OK  {test.__name__}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    print(f"alle {len(tests)} Tests zu den Issues #46/#47 grün")


if __name__ == "__main__":
    main()
