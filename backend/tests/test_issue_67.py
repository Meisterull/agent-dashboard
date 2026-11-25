"""Issue #67 — Automatik-Tasks in die laufende Terminal-Sitzung zustellen.

    cd backend && python -m tests.test_issue_67

Watcher-Teil (scripts/agent_watcher.py):
  - Bildschirm lesen an ECHTEN Aufnahmen von Claude Code 2.1.332 in tmux
    (tests/daten_67: leer mit Vorschlag, halb getippt, arbeitet, fertig)
  - Hooks in .claude/settings.local.json ergänzen: fremde bleiben, nichts
    doppelt, kaputtes JSON wird nicht überschrieben
  - Hook schreibt den Zustand je Projektordner
  - Frei-Regel: Hook UND Bildschirm; Esc-Ausnahme nach SITZUNG_HAENGT
  - Verbrauch aus dem Transkript: je message.id einmal, ab Zustellzeit, Kosten
Server-Teil:
  - claim_task(erneut, sitzung) schreibt den Anspruch auf `sitzung` um,
    claim_task(zurueckgeben) legt den Task zurück in die Inbox
  - zweites complete_task trägt den Verbrauch des Sitzungs-Laufs nach
  - Zustell-Zeilen → Ereignis `zustellung`, Push nur mit Schalter, Sprung-URL
  - Einstellung je Agent + Watcher-Flags

Den Ablauf gegen echtes tmux prüft tests/test_issue_67_tmux.py.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from tests.test_mcp_tools import _mcp_server_laden, _tools

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
import agent_watcher as aw  # noqa: E402

DATEN = Path(__file__).parent / "daten_67"


def test_bildschirm_echte_aufnahmen(tmp: Path) -> None:
    lies = lambda n: aw.bildschirm_lesen((DATEN / f"{n}.txt").read_text(encoding="utf-8"))
    assert lies("leer_vorschlag")["frei"], "gedimmter Platzhalter ist keine Eingabe"
    assert lies("fertig")["frei"]
    g = lies("getippt")
    assert not g["frei"] and "tippt" in g["grund"], g
    a = lies("arbeitet")
    assert not a["frei"] and "arbeitet" in a["grund"], a
    # Vertrauens-Dialog: keine Eingabebox → nie frei
    d = aw.bildschirm_lesen(" ❯ No, exit\n   Yes, I trust this folder\n\n Enter to confirm")
    assert not d["frei"], d
    # Verlaufszeilen mit ❯ oberhalb der Box zählen nicht als Eingabe
    bild = ("❯ alte Frage\n● Antwort\n" + "─" * 40 + "\n❯\xa0\n" + "─" * 40 +
            "\n  ⏵⏵ auto mode on\n")
    assert aw.bildschirm_lesen(bild)["frei"]


def test_hooks_einrichten(tmp: Path) -> None:
    projekt = tmp / "proj"
    (projekt / ".claude").mkdir(parents=True)
    datei = projekt / ".claude" / "settings.local.json"
    fremd = {"permissions": {"allow": ["Bash(ls:*)"]},
             "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo fremd"}]}]}}
    datei.write_text(json.dumps(fremd), encoding="utf-8")
    assert aw.hooks_einrichten(projekt, "python3 /x/agent_watcher.py --sitzung-hook")
    d = json.loads(datei.read_text(encoding="utf-8"))
    assert d["permissions"] == fremd["permissions"]
    assert len(d["hooks"]["Stop"]) == 2 and d["hooks"]["Stop"][0]["hooks"][0]["command"] == "echo fremd"
    for ereignis in aw.HOOK_EREIGNISSE:
        assert any("--sitzung-hook" in h["command"] for g in d["hooks"][ereignis]
                   for h in g["hooks"]), ereignis
    # zweimal → nichts doppelt
    assert not aw.hooks_einrichten(projekt, "python3 /y/agent_watcher.py --sitzung-hook")
    assert len(json.loads(datei.read_text(encoding="utf-8"))["hooks"]["Stop"]) == 2
    # kaputtes JSON bleibt, wie es ist
    datei.write_text("{kaputt", encoding="utf-8")
    assert not aw.hooks_einrichten(projekt)
    assert datei.read_text(encoding="utf-8") == "{kaputt"
    # ohne Datei: wird angelegt
    neu = tmp / "neu"
    neu.mkdir()
    assert aw.hooks_einrichten(neu)
    assert (neu / ".claude" / "settings.local.json").exists()


def test_hook_und_frei_regel(tmp: Path) -> None:
    aw.SITZUNG_DIR = tmp / "sitzungen"
    projekt = tmp / "proj"
    projekt.mkdir()
    assert aw.hook_lesen(projekt) is None
    # Noch keine Hook-Meldung (frisch eingetragen): frei erst nach ruhigem Bildschirm
    leer0 = (DATEN / "leer_vorschlag.txt").read_text(encoding="utf-8")
    aw._tmux = lambda *a, **k: leer0
    m0: dict = {}
    erst = aw.sitzung_pruefen({"pane": "%1"}, projekt, m0)
    assert not erst["frei"] and "Hook-Meldung" in erst["grund"], erst
    m0["bild_frei_seit"] -= aw.SITZUNG_HAENGT + 1
    assert aw.sitzung_pruefen({"pane": "%1"}, projekt, m0)["frei"]
    aw.sitzung_hook(json.dumps({"hook_event_name": "UserPromptSubmit", "cwd": str(projekt),
                                "session_id": "s1", "transcript_path": "/t.jsonl",
                                "prompt": "[Dashboard-Automatik · Task t-1 …"}))
    h = aw.hook_lesen(projekt)
    assert h["zustand"] == "arbeitet" and h["session_id"] == "s1" and "t-1" in h["prompt"], h
    # unbekanntes Ereignis / Müll → still, nichts kaputt
    assert aw.sitzung_hook("kein json") == 0
    assert aw.sitzung_hook(json.dumps({"hook_event_name": "PreToolUse", "cwd": str(projekt)})) == 0
    assert aw.hook_lesen(projekt)["zustand"] == "arbeitet"

    leer = (DATEN / "leer_vorschlag.txt").read_text(encoding="utf-8")
    belegt = (DATEN / "arbeitet.txt").read_text(encoding="utf-8")
    bild = {"t": leer}
    aw._tmux = lambda *a, **k: bild["t"] if a and a[0] == "capture-pane" else ""
    sitz = {"pane": "%1", "sitzung": "proj"}
    merker: dict = {}
    # Hook „arbeitet" + Bildschirm frei → noch nicht frei
    assert not aw.sitzung_pruefen(sitz, projekt, merker)["frei"]
    # … nach SITZUNG_HAENGT durchgehend freiem Bildschirm doch (Esc ohne Stop-Hook)
    merker["bild_frei_seit"] = time.monotonic() - aw.SITZUNG_HAENGT - 1
    assert aw.sitzung_pruefen(sitz, projekt, merker)["frei"]
    # belegter Bildschirm setzt die Uhr zurück
    bild["t"] = belegt
    assert not aw.sitzung_pruefen(sitz, projekt, merker)["frei"]
    assert "bild_frei_seit" not in merker
    # Stop-Hook + leerer Bildschirm → frei
    aw.sitzung_hook(json.dumps({"hook_event_name": "Stop", "cwd": str(projekt)}))
    bild["t"] = leer
    assert aw.sitzung_pruefen(sitz, projekt, {})["frei"]
    # Stop-Hook, aber jemand tippt → belegt
    bild["t"] = (DATEN / "getippt.txt").read_text(encoding="utf-8")
    assert not aw.sitzung_pruefen(sitz, projekt, {})["frei"]
    # Sitzung weg
    aw._tmux = lambda *a, **k: None
    assert aw.sitzung_pruefen(sitz, projekt, {}).get("weg")


def _zeit(sek: float) -> str:
    return datetime.fromtimestamp(sek, timezone.utc).isoformat().replace("+00:00", "Z")


def test_transkript_verbrauch(tmp: Path) -> None:
    jetzt = time.time()
    u = {"input_tokens": 10, "output_tokens": 100, "cache_read_input_tokens": 1_000_000,
         "cache_creation_input_tokens": 1000,
         "cache_creation": {"ephemeral_1h_input_tokens": 1000, "ephemeral_5m_input_tokens": 0}}
    zeilen = [
        # vor der Zustellung → zählt nicht
        {"type": "assistant", "timestamp": _zeit(jetzt - 100),
         "message": {"id": "m0", "model": "claude-opus-5-5", "usage": u}},
        # dieselbe Nachricht zweimal (je Inhaltsblock) → einmal zählen
        {"type": "assistant", "timestamp": _zeit(jetzt + 1),
         "message": {"id": "m1", "model": "claude-opus-5-5", "usage": u}},
        {"type": "assistant", "timestamp": _zeit(jetzt + 2),
         "message": {"id": "m1", "model": "claude-opus-5-5", "usage": u}},
        {"type": "user", "timestamp": _zeit(jetzt + 3), "message": {"content": "x"}},
    ]
    pfad = tmp / "t.jsonl"
    pfad.write_text("\n".join(json.dumps(z) for z in zeilen) + "\nkaputt\n", encoding="utf-8")
    v = aw.transkript_verbrauch(str(pfad), jetzt)
    assert v["input_tokens"] == 10 and v["output_tokens"] == 100, v
    assert v["cache_read_input_tokens"] == 1_000_000, v
    # 10·4 + 100·20 + 1e6·0.20 + 1000·4·2 (1-h-Cache) = 210_040 /1e6
    assert abs(v["total_cost_usd"] - 0.21004) < 1e-6, v
    assert v["kosten_geschaetzt"] and v["modell"] == "claude-opus-5-5"
    # unbekanntes Modell → Tokens ja, Kosten nein
    zeilen[1]["message"]["model"] = zeilen[2]["message"]["model"] = "irgendwas"
    pfad.write_text("\n".join(json.dumps(z) for z in zeilen), encoding="utf-8")
    v = aw.transkript_verbrauch(str(pfad), jetzt)
    assert "total_cost_usd" not in v and v["output_tokens"] == 100, v
    assert aw.transkript_verbrauch(None, jetzt) == {}
    assert aw.transkript_verbrauch(str(tmp / "fehlt.jsonl"), jetzt) == {}


def test_server_anspruch_zurueckgeben_nachtrag(tmp: Path) -> None:
    mcp_server = _mcp_server_laden(tmp)
    for n in ("worker", "chef"):
        (tmp / "mailboxes" / n).mkdir(parents=True)
    t = _tools(mcp_server)
    box = tmp / "mailboxes" / "worker"

    # zurückgeben: Watcher gestoppt, bevor er zustellen konnte
    a = t["send_task"](to="worker", instruction="A", sender="chef")
    t["claim_task"](task_id=a["id"], agent="worker", watcher=True)
    r = t["claim_task"](task_id=a["id"], agent="worker", zurueckgeben=True)
    assert r["zurueckgegeben"], r
    env = json.loads((box / "inbox" / f"{a['id']}.json").read_text(encoding="utf-8"))
    assert env["status"] == "pending" and "claim_von" not in env and "claimed_at" not in env, env
    assert not (box / "inbox" / ".processing" / f"{a['id']}.json").exists()

    # an die Sitzung übergeben: claim_von wechselt, requeues fällt weg
    t["claim_task"](task_id=a["id"], agent="worker", watcher=True)
    p = box / "inbox" / ".processing" / f"{a['id']}.json"
    env = json.loads(p.read_text(encoding="utf-8"))
    env["requeues"] = 2
    p.write_text(json.dumps(env), encoding="utf-8")
    t["claim_task"](task_id=a["id"], agent="worker", erneut=True, sitzung=True)
    env = json.loads(p.read_text(encoding="utf-8"))
    assert env["claim_von"] == "sitzung" and env["zustellung"] == "sitzung", env
    assert "requeues" not in env
    # sitzung OHNE erneut ändert nichts (nur der Watcher setzt es nach dem Claim)
    b = t["send_task"](to="worker", instruction="B", sender="chef")
    t["claim_task"](task_id=b["id"], agent="worker", watcher=True, sitzung=True)
    env_b = json.loads((box / "inbox" / ".processing" / f"{b['id']}.json").read_text(encoding="utf-8"))
    assert env_b["claim_von"] == "watcher", env_b

    # Sitzung schließt ab, Watcher trägt den Verbrauch nach (#43-Weg)
    fertig = t["complete_task"](task_id=a["id"], result="aus der Sitzung", agent="worker")
    assert fertig["status"] == "done" and not fertig.get("already"), fertig
    # Die Probe des Watchers sieht den Task jetzt als erledigt
    probe = t["claim_task"](task_id=a["id"], agent="worker", erneut=True)
    assert "liegt nicht" in probe.get("error", ""), probe
    nach = t["complete_task"](task_id=a["id"], result="", agent="worker",
                              verbrauch={"output_tokens": 5, "total_cost_usd": 0.5},
                              lauf={"zustellung": "sitzung", "session_id": "s1"})
    assert nach.get("already") and "verbrauch" in nach.get("ergaenzt", []), nach
    antwort = json.loads((box / "outbox" / f"{a['id']}-response.json").read_text(encoding="utf-8"))
    assert antwort["result"] == "aus der Sitzung" and antwort["verbrauch"]["total_cost_usd"] == 0.5
    assert antwort["lauf"]["zustellung"] == "sitzung"


def test_zustell_zeilen_push_und_einstellung(tmp: Path) -> None:
    os.environ["DATA_CONFIG_DIR"] = str(tmp / "config")
    os.environ["WORKSPACE_DIR"] = str(tmp)
    import importlib
    from app import config
    importlib.reload(config)
    from app import events, projekte, zustellung
    importlib.reload(zustellung)
    root = tmp / "mailboxes"
    (root / "worker").mkdir(parents=True)

    assert zustellung.einstellung("worker") == {"sitzung": False, "push": False}
    assert zustellung.flags("worker") == ""
    zustellung.setze_einstellung("worker", sitzung=True)
    assert zustellung.flags("worker") == " --zustellung sitzung"
    zustellung.setze_einstellung("worker", push=True)
    assert zustellung.flags("worker") == " --zustellung sitzung --zustellung-push"
    try:
        zustellung.setze_einstellung("../x", sitzung=True)
        raise AssertionError("ungültiger Name angenommen")
    except zustellung.ZustellFehler:
        pass

    push = events.EreignisPush(sperre_s=600)
    push.baseline(root)
    assert zustellung.verarbeite_zeile(root, "worker", "normale Zeile") is None
    assert zustellung.verarbeite_zeile(root, "worker", "@@zustellung {kaputt") == ""
    assert zustellung.verarbeite_zeile(
        root, "worker", '@@zustellung {"zustand": "erfunden", "text": "x"}') == ""
    zeile = "@@zustellung " + json.dumps({
        "zustand": "zugestellt", "text": "in Sitzung „zeitalter“ zugestellt",
        "task_id": "t-1", "schwere": "info", "sitzung": "zeitalter", "push": True})
    assert "zugestellt" in zustellung.verarbeite_zeile(root, "worker", zeile)
    roh = (root / "worker" / "ereignisse.jsonl").read_text(encoding="utf-8").splitlines()
    e = json.loads(roh[-1])
    assert e["art"] == "zustellung" and e["task_id"] == "t-1" and e["details"]["push"] is True, e

    # Sprung-URL: Projekt bekannt → Verbindung + Projekt
    projekte.speichere("werkstatt", [{"name": "zeitalter", "pfad": "/home/x/zeitalter"}])
    meldungen = push.verarbeite(root, {"worker"})
    m = [m for m in meldungen if m.get("art") == "zustellung"]
    assert len(m) == 1 and m[0]["url"] == "/?tab=terminal&verbindung=werkstatt&projekt=zeitalter", m
    # ohne Push-Schalter → kein Push; „offen" (warnung) läuft über den Bündler
    zustellung.verarbeite_zeile(root, "worker", "@@zustellung " + json.dumps(
        {"zustand": "zugestellt", "text": "zugestellt", "task_id": "t-2", "sitzung": "zeitalter"}))
    zustellung.verarbeite_zeile(root, "worker", "@@zustellung " + json.dumps(
        {"zustand": "offen", "text": "offen in der Sitzung", "task_id": "t-2",
         "schwere": "warnung"}))
    meldungen = push.verarbeite(root, {"worker"})
    assert not [m for m in meldungen if m.get("art") == "zustellung"], meldungen
    assert [m for m in meldungen if m.get("art") == "ereignis"], meldungen


class _Client:
    def __init__(self):
        self.aufrufe = []

    def call(self, tool, args):
        self.aufrufe.append((tool, dict(args)))
        return {"task_id": args.get("task_id"), "already": True}


def test_abschluss_nachlauf(tmp: Path) -> None:
    jetzt = time.time()
    t = tmp / "t.jsonl"
    t.write_text(json.dumps({"type": "assistant", "timestamp": _zeit(jetzt + 1), "message": {
        "id": "m", "model": "claude-sonnet-5-5", "usage": {"output_tokens": 1000}}}), encoding="utf-8")
    c = _Client()
    aw.sitzung_abschliessen(c, "u", "t-1", {"weg": "sitzung", "status": "erledigt", "ab": jetzt,
                                           "session_id": "s1", "transcript": str(t)}, 600)
    tool, args = c.aufrufe[-1]
    assert tool == "complete_task" and args["status"] == "done" and args["result"] == "", args
    assert args["verbrauch"]["output_tokens"] == 1000 and args["verbrauch"]["total_cost_usd"] == 0.01
    assert args["lauf"] == {"zustellung": "sitzung", "session_id": "s1",
                            "modell": "claude-sonnet-5-5"}, args
    c = _Client()
    aw.sitzung_abschliessen(c, "u", "t-2", {"weg": "sitzung", "status": "offen", "ab": jetzt,
                                           "session_id": "s2"}, 600)
    tool, args = c.aufrufe[-1]
    assert args["status"] == "error" and "10 min" in args["result"] and "resume s2" in args["result"]
    c = _Client()
    aw.sitzung_abschliessen(c, "u", "t-3", {"weg": "sitzung", "status": "stopp"}, 600)
    assert c.aufrufe == [("claim_task", {"task_id": "t-3", "zurueckgeben": True})], c.aufrufe
    c = _Client()
    aw.sitzung_abschliessen(c, "u", "t-4", {"weg": "sitzung", "status": "stopp_hart"}, 600)
    assert c.aufrufe == []


TESTS = [test_bildschirm_echte_aufnahmen, test_hooks_einrichten, test_hook_und_frei_regel,
         test_transkript_verbrauch, test_server_anspruch_zurueckgeben_nachtrag,
         test_zustell_zeilen_push_und_einstellung, test_abschluss_nachlauf]


def main() -> None:
    for test in TESTS:
        tmp = Path(tempfile.mkdtemp(prefix="issue-67-"))
        alt_tmux = aw._tmux
        try:
            test(tmp)
            print(f"OK  {test.__name__}")
        finally:
            aw._tmux = alt_tmux
            shutil.rmtree(tmp, ignore_errors=True)
    print(f"alle {len(TESTS)} Tests zu Issue #67 grün")


if __name__ == "__main__":
    main()
