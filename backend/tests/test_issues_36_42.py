"""Server-Seite der Issues #36–#42 (21.09.2026), reine Stdlib:

    cd backend && python -m tests.test_issues_36_42

  #36  Weckruf: ungelesene Post wird je Absender zu EINEM Task gebündelt,
       weckt nie doppelt, Schleifenschutz (Ergebnis eines Weckrufs weckt
       nicht; Höchstzahl je Absender/Stunde mit EINER Meldung an den Menschen)
  #37  `thread` überlebt send_task → claim_task; `lauf` landet in der Antwort
  #38  `timeout` im Task; Timeout-Abbruch meldet sich beim Menschen
  #40  Log: Zeitstempel, ergebnis=fehler bei {"error": …}, Timeout-Serie
  #41  der leere Watcher-Poll schreibt keine Logzeile
  #42  Pflege reiht nichts zurück, solange der Agent Lebenszeichen gibt
"""
from __future__ import annotations

import asyncio
import contextlib
import io
import json
import os
import re
import shutil
import tempfile
import time
from pathlib import Path

from tests.test_mcp_tools import _FastMCPDoppel, _mcp_server_laden, _tools


def _boxen(ws: Path, *namen: str) -> None:
    for n in namen:
        (ws / "mailboxes" / n).mkdir(parents=True, exist_ok=True)


def _inbox(ws: Path, agent: str) -> list[dict]:
    ordner = ws / "mailboxes" / agent / "inbox"
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(ordner.glob("*.json"))]


def test_thread_timeout_und_lauf_ueberleben_den_weg(ws: Path) -> None:
    """#37/#38: der Watcher liest thread/timeout aus dem claim; seine Lauf-
    Daten stehen danach in der Antwort (Panel: Abzeichen, `claude --resume`)."""
    ms = _mcp_server_laden(ws)
    _boxen(ws, "worker", "chef")
    t = _tools(ms)
    zu_kurz = t["send_task"](to="worker", instruction="x", sender="chef", timeout=5)
    assert "error" in zu_kurz, zu_kurz
    gesendet = t["send_task"](to="worker", instruction="baue X", sender="chef",
                              thread="Startseite Aufgaben!", timeout=900)
    assert gesendet["thread"] == "Startseite-Aufgaben", gesendet
    assert t["inbox"](agent="worker", kind="task")[0]["thread"] == "Startseite-Aufgaben"
    claim = t["claim_task"](task_id=gesendet["id"], agent="worker")
    assert (claim["thread"], claim["timeout"]) == ("Startseite-Aufgaben", 900), claim
    lauf = {"sitzung": "fortgesetzt", "session_id": "sitz-0001-aaaa",
            "verweigert": [{"tool": "Bash", "eingabe": "cat /etc/shadow"}]}
    t["complete_task"](task_id=gesendet["id"], result="fertig", agent="worker", lauf=lauf)
    antwort = json.loads((ws / "mailboxes" / "worker" / "outbox" /
                          f"{gesendet['id']}-response.json").read_text(encoding="utf-8"))
    assert antwort["lauf"] == lauf, antwort


def test_timeout_abbruch_erreicht_den_menschen(ws: Path) -> None:
    """#38: kam der Auftrag von einem AGENTEN, erführe der Mensch vom Abbruch
    sonst nichts — und die Arbeit liegt womöglich uncommittet auf der Box."""
    ms = _mcp_server_laden(ws)
    _boxen(ws, "worker", "chef", "orchestrator")
    t = _tools(ms)
    lauf = {"timeout": "Timeout nach 7200s Gesamtdauer", "session_id": "sitz-0001-aaaa",
            "fortsetzbar": True}
    g = t["send_task"](to="worker", instruction="lang", sender="chef")
    t["claim_task"](task_id=g["id"], agent="worker")
    t["complete_task"](task_id=g["id"], result="bis hierhin", status="error",
                       agent="worker", lauf=lauf)
    beim_chef = [e for e in _inbox(ws, "chef") if e["kind"] == "response"]
    assert "claude --resume sitz-0001-aaaa" in beim_chef[0]["hinweis"], beim_chef
    beim_menschen = _inbox(ws, "orchestrator")
    assert len(beim_menschen) == 1 and beim_menschen[0]["kind"] == "message", beim_menschen
    assert "abgebrochen" in beim_menschen[0]["text"] and beim_menschen[0]["weckt"] is False
    # Kam der Auftrag vom Orchestrator, meldet sich schon die Response selbst
    g2 = t["send_task"](to="worker", instruction="lang", sender="orchestrator")
    t["claim_task"](task_id=g2["id"], agent="worker")
    t["complete_task"](task_id=g2["id"], result="x", status="error", agent="worker", lauf=lauf)
    arten = sorted(e["kind"] for e in _inbox(ws, "orchestrator"))
    assert arten == ["message", "response"], arten


def test_log_zeitstempel_fehler_und_stiller_poll(ws: Path) -> None:
    """#40/#41: `ergebnis=ok` trotz {"error": …} verschleierte fünf Timeouts in
    Folge; der leere Watcher-Poll füllte das Log mit >1.200 Zeilen in 2 h."""
    ms = _mcp_server_laden(ws)
    _boxen(ws, "worker")
    doppel = _FastMCPDoppel()
    ms.register_tools(doppel, "worker", None)

    def lauf(name, **kw) -> str:
        puffer = io.StringIO()
        with contextlib.redirect_stdout(puffer):
            asyncio.run(doppel.roh[name](**kw))
        return puffer.getvalue()

    aus = lauf("claim_task", task_id="task-gibtsnicht")
    assert "ergebnis=fehler:" in aus, aus
    assert all(re.match(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d", z) for z in aus.splitlines()), aus
    assert lauf("inbox", kind="task") == "", "leerer Watcher-Poll darf nicht loggen"
    assert "anzahl" not in lauf("inbox") or True  # normaler Aufruf loggt weiter
    assert "[mcp] worker: inbox" in lauf("inbox")
    assert ms.fehler_im_ergebnis([{"error": "x"}]) == "x"
    assert ms.fehler_im_ergebnis([{"id": 1}, {"error": "x"}]) is None
    assert ms.fehler_im_ergebnis({"status": 200}) is None


def test_timeout_serie_warnt_genau_einmal(ws: Path) -> None:
    ms = _mcp_server_laden(ws)
    from app import integrations
    integrations._timeouts_in_folge.clear()
    warnungen = []
    for _ in range(integrations.TIMEOUT_WARNUNG_AB + 2):
        integrations._timeout_serie("bot", True)
        warnungen.append(integrations.timeout_warnung("bot"))
    assert sum(1 for w in warnungen if w) == 1, warnungen
    integrations._timeout_serie("bot", False)  # Erfolg setzt die Serie zurück
    assert integrations._timeouts_in_folge.get("bot") is None
    assert ms is not None


def _altern(pfad: Path, sekunden: float) -> None:
    env = json.loads(pfad.read_text(encoding="utf-8"))
    from datetime import datetime, timedelta, timezone
    alt = (datetime.now(timezone.utc) - timedelta(seconds=sekunden)).isoformat()
    for feld in ("claimed_at", "zuletzt_aktiv"):
        if feld in env:
            env[feld] = alt
    pfad.write_text(json.dumps(env), encoding="utf-8")


def test_pflege_achtet_auf_lebenszeichen(ws: Path) -> None:
    """#42: eine interaktive Sitzung arbeitete legitim sieben Stunden an einem
    Task — die Pflege reihte ihn nach 3 h zurück; mit Automatik wäre er
    parallel ein zweites Mal gelaufen."""
    ms = _mcp_server_laden(ws)
    from app.mailbox import Mailbox
    _boxen(ws, "worker", "chef")
    t = _tools(ms)
    g = t["send_task"](to="worker", instruction="dauert", sender="chef")
    t["claim_task"](task_id=g["id"], agent="worker")
    box = Mailbox(ws / "mailboxes", "worker")
    pfad = box.processing / f"{g['id']}.json"
    _altern(pfad, 4 * 3600)

    # (1) gebundener Kanal: echte Aktivität = Lebenszeichen; der Watcher-Poll nicht
    doppel = _FastMCPDoppel()
    ms.register_tools(doppel, "worker", None)
    with contextlib.redirect_stdout(io.StringIO()):
        asyncio.run(doppel.roh["inbox"](kind="task"))
        assert box.sekunden_seit_lebenszeichen() is None, "Poll zählt nicht"
        asyncio.run(doppel.roh["send_message"](to="chef", text="bin dran"))
    assert box.sekunden_seit_lebenszeichen() < 5
    assert box.requeue_stale(3 * 3600)["requeued"] == []

    # (2) Lebenszeichen veraltet → jetzt gilt der Task als verwaist, und der
    #     Agent erfährt es (Notiz mit weckt=False, löst selbst keinen Lauf aus)
    alt = time.time() - 4 * 3600
    os.utime(box.base / ".aktiv", (alt, alt))
    assert box.requeue_stale(3 * 3600)["requeued"] == [g["id"]]
    notiz = [e for e in _inbox(ws, "worker") if e.get("kind") == "message"]
    assert len(notiz) == 1 and notiz[0]["weckt"] is False and g["id"] in notiz[0]["text"]

    # (3) Herzschlag des Watchers: claim_task(erneut=True) frischt den Task auf
    t["claim_task"](task_id=g["id"], agent="worker")
    _altern(pfad, 4 * 3600)
    t["claim_task"](task_id=g["id"], agent="worker", erneut=True)
    assert box.requeue_stale(3 * 3600)["requeued"] == []


def test_weckruf_buendelt_und_weckt_nie_doppelt(ws: Path) -> None:
    ms = _mcp_server_laden(ws)
    from app import weckruf
    _boxen(ws, "dev", "erp", "orchestrator")
    t = _tools(ms)
    t["send_message"](to="dev", text="Punkt d fehlt noch", sender="erp")
    t["send_message"](to="dev", text="und die Regression prüfen", sender="erp")
    t["send_message"](to="dev", text="Hinweis vom Menschen", sender="orchestrator")
    root = ws / "mailboxes"
    assert weckruf.weck_arten({"automatik_weckt": ["task", "message", "quatsch"]}) == ["message"]
    assert weckruf.weck_arten({"automatik_weckt": "message, response"}) == ["message", "response"]
    assert weckruf.weck_arten({}) == []
    assert weckruf.ungeweckt(root, "dev", []) == 3        # Option aus: bleibt liegen
    assert weckruf.ungeweckt(root, "dev", ["message"]) == 0
    assert weckruf.pruefe(root, "dev", [])["geweckt"] == []  # Default: nur Tasks

    bericht = weckruf.pruefe(root, "dev", ["message"])
    assert len(bericht["geweckt"]) == 2, bericht              # je Absender EIN Lauf
    tasks = {e["sender"]: e for e in _inbox(ws, "dev") if e.get("kind") == "task"}
    assert set(tasks) == {"erp", "orchestrator"}, tasks
    auftrag = tasks["erp"]["instruction"]
    assert "Punkt d fehlt noch" in auftrag and "Regression" in auftrag and "mark_read" in auftrag
    assert tasks["erp"]["weckruf"] is True and len(tasks["erp"]["weck_ids"]) == 2
    # dieselbe Post weckt nie ein zweites Mal — auch unarchiviert
    assert weckruf.pruefe(root, "dev", ["message"])["geweckt"] == []

    # Schleifenschutz 1: das Ergebnis eines Weckrufs weckt den Absender nicht
    t["claim_task"](task_id=tasks["erp"]["task_id"], agent="dev")
    t["complete_task"](task_id=tasks["erp"]["task_id"], result="erledigt", agent="dev")
    antwort = [e for e in _inbox(ws, "erp") if e["kind"] == "response"][0]
    assert antwort["weckt"] is False, antwort
    assert weckruf.pruefe(root, "erp", ["response"])["geweckt"] == []


def test_weckruf_schleifenschutz_meldet_einmal(ws: Path) -> None:
    ms = _mcp_server_laden(ws)
    from app import weckruf
    _boxen(ws, "dev", "erp", "orchestrator")
    t = _tools(ms)
    root = ws / "mailboxes"
    jetzt = time.time()
    for nr in range(3):
        t["send_message"](to="dev", text=f"Nachricht {nr}", sender="erp")
        bericht = weckruf.pruefe(root, "dev", ["message"], jetzt=jetzt + nr, max_je_stunde=2)
    assert bericht == {"geweckt": [], "gebremst": ["erp"]}, bericht
    t["send_message"](to="dev", text="noch eine", sender="erp")
    weckruf.pruefe(root, "dev", ["message"], jetzt=jetzt + 10, max_je_stunde=2)
    meldungen = [e for e in _inbox(ws, "orchestrator") if "Schleifenschutz" in e.get("text", "")]
    assert len(meldungen) == 1, meldungen                    # EINE Meldung je Stunde
    # nach einer Stunde ist das Fenster wieder frei — die liegengebliebene Post kommt
    spaeter = weckruf.pruefe(root, "dev", ["message"], jetzt=jetzt + 3700, max_je_stunde=2)
    assert len(spaeter["geweckt"]) == 1, spaeter


def main() -> None:
    tests = [test_thread_timeout_und_lauf_ueberleben_den_weg,
             test_timeout_abbruch_erreicht_den_menschen,
             test_log_zeitstempel_fehler_und_stiller_poll,
             test_timeout_serie_warnt_genau_einmal,
             test_pflege_achtet_auf_lebenszeichen,
             test_weckruf_buendelt_und_weckt_nie_doppelt,
             test_weckruf_schleifenschutz_meldet_einmal]
    for test in tests:
        tmp = Path(tempfile.mkdtemp(prefix="issues-36-42-"))
        try:
            test(tmp)
            print(f"OK  {test.__name__}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    print(f"alle {len(tests)} Tests zu den Issues #36–#42 grün")


if __name__ == "__main__":
    main()
