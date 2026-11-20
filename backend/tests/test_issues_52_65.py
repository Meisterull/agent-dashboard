"""Server-Seite der Issues #52–#65 (20.11.2026), reine Stdlib:

    cd backend && python -m tests.test_issues_52_65

  #52  Erinnerungen: nur fürs eigene Postfach, fällig → Task/Nachricht,
       Verspätetes wird nachgeholt, löschen/verschieben
  #54  Fehlstart des Watchers steht als `fehlstart` im Ereignis-Log, nicht als
       Handbetrieb; claim_task liefert sender + Zahl der Nachträge
  #55  Tageskosten-Grenze: Rückfrage an den Menschen, Watcher-Claim gesperrt,
       „weiter" gibt frei; teurer Lauf wird einmal gemeldet; Ereignis-Texte
  #56  Freigabe: Muster, nie/immer ohne Anfrage, Warten auf die Entscheidung,
       „immer erlauben" merkt das Muster, Frist/Not-Aus = Ablehnung, Push
  #57  fetch_file nur für den Empfänger, upload_url prüft das Ziel vorab
  #53  deliver_upload nur auf dem freien Kanal und nur aus uploads/
  #60  Pflege archiviert ins Monatsarchiv statt zu löschen; laufende Tasks
       behalten ihre Antwort; read_responses(archiv=True) findet Altes
  #62  Modell je Task nur aus der Allowlist des Empfängers; Rolle friert ihr
       Modell ein; claim reicht beides an den Watcher; Kosten je Modell
  #64  Geheimnisse: verschlüsselt abgelegt, nur Namen lesbar, Schwärzen,
       Übergabe-Zeile für den Watcher, Systemvariablen verboten
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


def test_57_53_datei_tools(ws: Path) -> None:
    (ws / "config").mkdir(parents=True, exist_ok=True)
    (ws / "config" / "agents.yaml").write_text(
        "agents:\n  - name: notebook\n    connection: {type: token}\n"
        "  - name: tablet\n    connection: {type: token}\n"
        "  - name: erp\n    connection: {type: ssh, host: 192.168.1.10, user: u, key_file: /nix}\n",
        encoding="utf-8")
    ms = _mcp_server_laden(ws)
    try:
        import yaml  # noqa: F401
    except ImportError:
        print("    (ohne PyYAML übersprungen)")
        return
    from app import zwischenspeicher
    _boxen(ws, "notebook", "tablet", "erp")
    frei, nb, tab = _tools(ms), _tools(ms, identity="notebook"), _tools(ms, identity="tablet")
    assert "deliver_upload" in frei and "deliver_upload" not in nb
    # Chat-Anhang → Token-Maschine: landet im Zwischenspeicher, sie holt ab
    anhang = ws / "uploads" / "chat" / "2026-11-20" / "plan.pdf"
    anhang.parent.mkdir(parents=True)
    anhang.write_bytes(b"%PDF-1.7 inhalt")
    assert "error" in frei["deliver_upload"](upload="../config/agents.yaml", to="notebook")
    erg = frei["deliver_upload"](upload="uploads/chat/2026-11-20/plan.pdf", to="notebook")
    datei_id = erg["zugestellt"][0]["abholen"]
    assert not anhang.exists(), "Anhang wird nach dem Zustellen aufgeräumt"
    post = [e for e in _inbox(ws, "notebook") if e["kind"] == "message"]
    assert datei_id in post[0]["text"], post
    assert "error" in tab["fetch_file"](file_id=datei_id), "nur der Empfänger"
    zwischenspeicher.merke_basis("notebook", "https://dash.example:8443")
    holen = nb["fetch_file"](file_id=datei_id)
    assert holen["url"].startswith(f"https://dash.example:8443/mcp/notebook/datei/{datei_id}?schluessel=")
    assert holen["name"] == "plan.pdf" and "curl" in holen["befehl"], holen
    # Hochladen: Ziel wird vorab geprüft (erp hat keinen Austausch-Ordner)
    nein = nb["upload_url"](to="erp", name="wolke.ply")
    assert "keinen Austausch-Ordner" in nein.get("error", ""), nein
    ja = nb["upload_url"](to="tablet", name="../../wolke.ply", note="Scan 3")
    assert ja["methode"] == "PUT" and ja["name"] == "wolke.ply", ja
    meta = zwischenspeicher.lies(ja["id"])
    assert (meta["status"], meta["an"], meta["weiter_an"]) == ("erwartet", "notebook", "tablet")
    assert zwischenspeicher.liste("notebook") and not zwischenspeicher.liste("tablet")


def test_56_freigaben(ws: Path) -> None:
    import threading
    import time
    ms = _mcp_server_laden(ws)
    from app import ereignisse, events, freigaben
    root = ws / "mailboxes"
    _boxen(ws, "dev")
    # Muster
    p = freigaben.passt
    bash = {"command": "docker exec backend-1 grep -n x"}
    assert p("Bash", "Bash", bash) and p("Bash(docker exec:*)", "Bash", bash)
    assert p("Bash(docker exec backend-1 grep -n x)", "Bash", bash)
    assert p("Bash(docker * grep*)", "Bash", bash)
    assert not p("Bash(docker execute:*)", "Bash", bash) and not p("Edit", "Bash", bash)
    assert not p("Bash(dock:*)", "Bash", bash), "Präfix endet an der Wortgrenze"
    assert freigaben.vorschlag("Bash", bash) == "Bash(docker exec:*)"
    assert freigaben.vorschlag("Edit", {"file_path": "/x"}) == "Edit"
    assert freigaben.sieht_nach_geheimnis("curl -H 'Authorization: token abc123def456:0123456789abcdef'")
    assert not freigaben.sieht_nach_geheimnis("git status")
    # Tool: ohne Schalter taub
    t = _tools(ms, identity="dev")
    assert json.loads(t["freigabe"](tool_name="Bash", input=bash))["behavior"] == "deny"
    assert freigaben.offene(root) == []
    freigaben.setze_einstellung("dev", an=True, timeout=30, nie=["Bash(./deploy-prod.sh:*)"],
                                immer=["Bash(git status:*)"])
    # nie / immer: sofort, ohne Anfrage
    nie = json.loads(t["freigabe"](tool_name="Bash", input={"command": "./deploy-prod.sh --jetzt"}))
    assert nie["behavior"] == "deny" and "untersagt" in nie["message"], nie
    immer = json.loads(t["freigabe"](tool_name="Bash", input={"command": "git status -s"}))
    assert immer == {"behavior": "allow", "updatedInput": {"command": "git status -s"}}, immer
    assert freigaben.offene(root) == []
    # Anfrage: wartet, bis der Mensch entscheidet — „immer erlauben" merkt das Muster
    ergebnis = {}
    faden = threading.Thread(target=lambda: ergebnis.update(freigaben.pruefe(
        root, "dev", "Bash", bash, "toolu_1", takt=0.05)))
    faden.start()
    for _ in range(100):
        offen = freigaben.offene(root)
        if offen:
            break
        time.sleep(0.02)
    assert len(offen) == 1 and offen[0]["vorschlag"] == "Bash(docker exec:*)", offen
    try:
        freigaben.entscheide(root, "dev", offen[0]["id"], True, immer="Bash(rm:*)")
        raise AssertionError("fremdes Muster hätte abgelehnt werden müssen")
    except freigaben.FreigabeFehler:
        pass
    freigaben.entscheide(root, "dev", offen[0]["id"], True, immer="Bash(docker exec:*)")
    faden.join(5)
    assert ergebnis == {"behavior": "allow", "updatedInput": bash}, ergebnis
    assert "Bash(docker exec:*)" in freigaben.einstellung("dev")["immer"]
    # Frist bzw. Not-Aus = Ablehnung
    aus = freigaben.pruefe(root, "dev", "Write", {"file_path": "/etc/x"}, takt=0.01,
                           abbruch=lambda: True)
    assert aus["behavior"] == "deny" and "Not-Aus" in aus["message"], aus
    zustaende = [e["details"]["zustand"] for e in
                 ereignisse.lies(root, "dev", art="freigabe")["eintraege"]]
    assert zustaende.count("angefragt") == 2 and "abgelaufen" in zustaende, zustaende
    # Push: jede Anfrage eine eigene Meldung mit Knöpfen, nie gebündelt
    bund = events.EreignisPush()
    meldungen = bund.verarbeite(root, {"dev"})
    frei = [m for m in meldungen if m["art"] == "freigabe"]
    assert len(frei) == 2 and frei[0]["optionen"] == ["erlauben", "ablehnen"] and frei[0]["fid"]


def test_64_geheimnisse(ws: Path) -> None:
    ms = _mcp_server_laden(ws)
    from app import geheimnisse
    if not geheimnisse.verfuegbar():
        print("    (ohne cryptography übersprungen)")
        return
    import base64
    _boxen(ws, "dev", "erp")
    geheimnisse.setze("dev", "ERP_DEV_TOKEN", "abc123def456:0123456789abcdef")
    geheimnisse.setze("dev", "KURZ", "xy")
    roh = (ws / "keys" / "geheimnisse.json").read_text(encoding="utf-8")
    assert "abc123def456" not in roh and "ERP_DEV_TOKEN" in roh, "Wert liegt verschlüsselt"
    assert oct((ws / "keys" / "geheimnisse.json").stat().st_mode & 0o777) == "0o600"
    assert geheimnisse.namen("dev") == ["ERP_DEV_TOKEN", "KURZ"]
    assert geheimnisse.werte("dev")["ERP_DEV_TOKEN"].startswith("abc123")
    for boese in ("PATH", "klein", "LD_PRELOAD", "A B"):
        try:
            geheimnisse.setze("dev", boese, "x" * 10)
            raise AssertionError(boese)
        except geheimnisse.GeheimnisFehler:
            pass
    text = "curl -H 'Authorization: token abc123def456:0123456789abcdef' xy"
    assert geheimnisse.schwaerze("dev", text) == "curl -H 'Authorization: token ***ERP_DEV_TOKEN***' xy"
    assert geheimnisse.schwaerze("erp", text) == text
    zeile = geheimnisse.env_zeile("dev")
    assert zeile.startswith("env ") and "abc123" not in zeile
    assert json.loads(base64.b64decode(zeile.split()[1]))["KURZ"] == "xy"
    assert "export ERP_DEV_TOKEN='abc123def456:0123456789abcdef'" in geheimnisse.env_datei("dev")
    sig = geheimnisse.signatur("dev")
    geheimnisse.loesche("dev", "KURZ")
    assert geheimnisse.signatur("dev") != sig and geheimnisse.signatur("erp") == ""
    # Abschluss über MCP: Antwort und Log tragen nur den Namen
    t = _tools(ms)
    g = t["send_task"](to="dev", instruction="x", sender="erp")
    t["claim_task"](task_id=g["id"], agent="dev")
    t["complete_task"](task_id=g["id"], agent="dev", result="Token war abc123def456:0123456789abcdef")
    antwort = t["read_responses"](worker="dev")[0]
    assert antwort["result"] == "Token war ***ERP_DEV_TOKEN***", antwort
    beim_chef = [e for e in _inbox(ws, "erp") if e["kind"] == "response"][0]
    assert "abc123" not in json.dumps(beim_chef)


def test_52_erinnerungen(ws: Path) -> None:
    from datetime import datetime, timedelta
    ms = _mcp_server_laden(ws)
    from app import ereignisse, erinnerungen
    root = ws / "mailboxes"
    _boxen(ws, "zabbix", "erp")
    t = _tools(ms, identity="zabbix")
    assert "error" in t["remind"](when="morgen früh", text="x")
    assert "error" in t["remind"](when="2026-11-26", text=" ")
    assert "error" in t["remind"](when="2026-11-26", text="x", agent="erp"), "nur eigenes Postfach"
    e1 = t["remind"](when="2026-11-26", text="Trigger X prüfen – Ticket #123")
    assert e1["faellig"].startswith("2026-11-26T08:00") and e1["status"] == "offen", e1
    e2 = t["remind"](when="2026-11-27T14:30", text="nur Hinweis", als="message")
    e3 = t["remind"](when="2026-12-01", text="später")
    assert [e["id"] for e in t["reminders"]()] == [e1["id"], e2["id"], e3["id"]]
    assert t["snooze_reminder"](reminder_id=e3["id"], when="2027-01-05")["faellig"].startswith("2027-01-05")
    assert t["cancel_reminder"](reminder_id=e3["id"])["status"] == "geloescht"
    assert "error" in t["cancel_reminder"](reminder_id=e3["id"])
    # vor dem Termin passiert nichts
    vorher = datetime.fromisoformat(e1["faellig"]) - timedelta(hours=1)
    assert erinnerungen.stelle_faellige_zu(root, lambda n: True, vorher) == []
    # Tage später (Server war aus): beide werden nachgeholt
    spaeter = datetime.fromisoformat(e2["faellig"]) + timedelta(days=3)
    zu = erinnerungen.stelle_faellige_zu(root, lambda n: True, spaeter)
    assert sorted(z["art"] for z in zu) == ["message", "task"], zu
    post = _inbox(ws, "zabbix")
    aufgabe = [e for e in post if e.get("kind", "task") == "task"][0]
    assert "Trigger X prüfen" in aufgabe["instruction"] and aufgabe["sender"] == "orchestrator"
    assert any(e.get("kind") == "message" and "nur Hinweis" in e["text"] for e in post)
    assert erinnerungen.stelle_faellige_zu(root, lambda n: True, spaeter) == [], "nur einmal"
    assert t["reminders"]() == []
    assert len(t["reminders"](include_done=True)) == 3
    # ohne Automatik wird aus dem Default eine Nachricht
    e4 = t["remind"](when="2026-11-20", text="ohne Automatik")
    zu = erinnerungen.stelle_faellige_zu(root, lambda n: False, spaeter)
    assert [z["art"] for z in zu] == ["message"] and zu[0]["id"] == e4["id"]
    zustaende = [e["details"]["zustand"] for e in ereignisse.lies(root, "zabbix", art="erinnerung")["eintraege"]]
    assert zustaende.count("angelegt") == 4 and zustaende.count("zugestellt") == 3, zustaende


TESTS = [test_52_erinnerungen,
         test_64_geheimnisse,
         test_56_freigaben,
         test_57_53_datei_tools,
         test_55_kostendeckel_und_sichtbarkeit,
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
