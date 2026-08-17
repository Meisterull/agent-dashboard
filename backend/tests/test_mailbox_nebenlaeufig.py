"""Mailbox: exklusiver Anspruch, verwaiste Tasks, Antwort-Primitive, FIFO.

Deckt die Befunde des Reviews vom 16.08.2026 ab (H2, M1, M3, M9, N1, N2).
Nur Standardlib — läuft überall:
    cd backend && python -m tests.test_mailbox_nebenlaeufig
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import time
from pathlib import Path

from app.mailbox import AlreadyClaimed, Mailbox, Task, new_id, pflege


def _task(root: Path, task_id: str, agent: str = "worker", sender: str = "chef",
          project: str | None = None) -> Mailbox:
    mb = Mailbox(root, agent)
    mb.put_task(Task(task_id=task_id, agent=agent, instruction=f"auftrag {task_id}",
                     sender=sender, project=project))
    return mb


def test_zweiter_claim_wird_abgelehnt(root: Path) -> None:
    """H2: ein bereits beanspruchter Task darf NICHT erneut ausgeliefert werden."""
    mb = _task(root, "task-a")
    env = mb.claim_task("task-a")
    assert env is not None and env["instruction"] == "auftrag task-a"
    assert env.get("claimed_at"), "Anspruch muss gestempelt werden"

    # Zweiter Watcher versucht denselben Task
    try:
        Mailbox(root, "worker").claim_task("task-a")
    except AlreadyClaimed as exc:
        assert exc.task_id == "task-a"
        assert exc.seit, "Meldung soll sagen, seit wann er läuft"
    else:  # pragma: no cover
        raise AssertionError("zweiter Claim hätte AlreadyClaimed werfen müssen")

    # Der eigene Bearbeiter kommt mit erneut=True weiter an seinen Auftrag
    wieder = Mailbox(root, "worker").claim_task("task-a", erneut=True)
    assert wieder is not None and wieder["instruction"] == "auftrag task-a"

    # Unbekannter Task bleibt None (kein Fehler)
    assert Mailbox(root, "worker").claim_task("task-gibtsnicht") is None


def test_verwaisten_task_wieder_einreihen(root: Path) -> None:
    """M9: stirbt der Bearbeiter, muss der Task zurück in die Warteschlange."""
    mb = _task(root, "task-v")
    mb.claim_task("task-v")

    # Frisch beansprucht: nichts passiert
    assert mb.requeue_stale(3600)["requeued"] == []

    # Anspruch künstlich altern lassen
    p = mb.processing / "task-v.json"
    env = json.loads(p.read_text(encoding="utf-8"))
    env["claimed_at"] = "2020-01-01T00:00:00+01:00"
    p.write_text(json.dumps(env), encoding="utf-8")

    ergebnis = mb.requeue_stale(3600)
    assert ergebnis["requeued"] == ["task-v"], ergebnis
    assert (mb.inbox / "task-v.json").exists()
    assert not (mb.processing / "task-v.json").exists()
    assert json.loads((mb.inbox / "task-v.json").read_text(encoding="utf-8"))["requeues"] == 1

    # Geparkte Rückfragen bleiben unangetastet
    mb2 = _task(root, "task-p")
    mb2.claim_task("task-p")
    pp = mb2.processing / "task-p.json"
    env = json.loads(pp.read_text(encoding="utf-8"))
    env["status"] = "needs_confirm"
    env["claimed_at"] = "2020-01-01T00:00:00+01:00"
    pp.write_text(json.dumps(env), encoding="utf-8")
    assert mb2.requeue_stale(3600)["requeued"] == []
    assert pp.exists(), "wartende Rückfrage darf nicht requeued werden"


def test_giftiger_task_wird_aufgegeben(root: Path) -> None:
    """M9: nach zu vielen Anläufen wird abgeschlossen statt ewig gekreist."""
    mb = _task(root, "task-g")
    for _ in range(4):
        mb.claim_task("task-g", erneut=True)
        p = mb.processing / "task-g.json"
        env = json.loads(p.read_text(encoding="utf-8"))
        env["claimed_at"] = "2020-01-01T00:00:00+01:00"
        p.write_text(json.dumps(env), encoding="utf-8")
        ergebnis = mb.requeue_stale(3600, max_versuche=3)
    assert ergebnis["aufgegeben"] == ["task-g"], ergebnis
    resp = json.loads((mb.outbox / "task-g-response.json").read_text(encoding="utf-8"))
    assert resp["status"] == "error"
    assert (mb.failed / "task-g.json").exists()


def test_antwort_archiviert_die_frage(root: Path) -> None:
    """M3: eine Wahrheit für Dashboard und MCP — Frage weg, Task angestoßen."""
    worker = _task(root, "task-f")
    worker.claim_task("task-f")
    chef = Mailbox(root, "chef")
    frage = chef.post({"kind": "question", "sender": "worker", "to": "chef",
                       "text": "Welcher Branch?", "status": "needs_confirm"})
    worker.link_question(frage["id"], "Welcher Branch?")
    worker.park_wenn_offene_fragen("task-f", "halb fertig")

    ergebnis = chef.beantworte_frage(frage["id"], "main")
    assert ergebnis["to"] == "worker"
    assert ergebnis["frage_archiviert"] is True
    assert ergebnis["wieder_angestossen"] == ["task-f"], ergebnis
    # Frage nicht mehr in der Inbox, sondern im Archiv
    assert not (chef.inbox / f"{frage['id']}.json").exists()
    assert (chef.archive / f"{frage['id']}.json").exists()
    # Antwort liegt beim Fragesteller, Task wieder in dessen Inbox
    antworten = [e for e in worker.read_inbox("answer")]
    assert antworten and antworten[0]["text"] == "main"
    assert (worker.inbox / "task-f.json").exists()


def test_reihenfolge_ist_fifo(root: Path) -> None:
    """N1: Tasks werden in Eingangsreihenfolge geliefert, nicht nach Zufalls-ID."""
    mb = Mailbox(root, "worker")
    for i, stamp in enumerate(["2026-01-01T10:00:00+01:00",
                               "2026-01-01T09:00:00+01:00",
                               "2026-01-01T11:00:00+01:00"]):
        t = Task(task_id=f"task-{i}", agent="worker", instruction=f"i{i}")
        t.created_at = stamp
        mb.put_task(t)
    reihenfolge = [e["task_id"] for e in mb.read_inbox("task")]
    assert reihenfolge == ["task-1", "task-0", "task-2"], reihenfolge


def test_ids_sind_lang_und_kollisionsfrei(root: Path) -> None:
    """N2: 16 Hex-Zeichen, und eine belegte ID überschreibt nichts."""
    assert len(new_id("msg").split("-", 1)[1]) == 16
    mb = Mailbox(root, "worker")
    erste = mb.post({"kind": "message", "sender": "chef", "text": "eins"})
    # Kollision erzwingen: dieselbe ID noch einmal anbieten
    zweite = mb.post({"kind": "message", "sender": "chef", "text": "zwei",
                      "id": erste["id"]})
    assert zweite["id"] != erste["id"], "ID musste neu gewürfelt werden"
    texte = sorted(e["text"] for e in mb.read_inbox("message"))
    assert texte == ["eins", "zwei"], texte


def test_lock_verklemmt_sich_nicht_selbst(root: Path) -> None:
    """M1: flock ist an die offene Datei gebunden — verschachtelte Aufrufe im
    selben Thread dürfen NICHT blockieren, sonst steht die ganze API."""
    mb = _task(root, "task-l")
    fertig = threading.Event()

    def arbeit() -> None:
        with mb._lock():
            with mb._lock():  # zweite Ebene
                mb.claim_task("task-l")  # nimmt ihn intern ein drittes Mal
                mb.write_response("task-l", "fertig", "done")
        fertig.set()

    t = threading.Thread(target=arbeit, daemon=True)
    t.start()
    t.join(timeout=15)
    assert fertig.is_set(), "Mailbox-Lock hat sich selbst verklemmt (Deadlock)"
    assert (mb.outbox / "task-l-response.json").exists()


def test_pflege_raeumt_und_reiht_ein(root: Path) -> None:
    """M4/M9: pflege() läuft über alle Mailboxen und rotiert alte Ablagen."""
    mb = _task(root, "task-x")
    mb.claim_task("task-x")
    p = mb.processing / "task-x.json"
    env = json.loads(p.read_text(encoding="utf-8"))
    env["claimed_at"] = "2020-01-01T00:00:00+01:00"
    p.write_text(json.dumps(env), encoding="utf-8")
    # alte Archivdatei
    alt = mb.archive / "msg-alt.json"
    alt.write_text("{}", encoding="utf-8")
    os.utime(alt, (time.time() - 40 * 86400, time.time() - 40 * 86400))

    bericht = pflege(root, stale_alter=3600, archiv_tage=30)
    assert bericht["requeued"] == ["worker/task-x"], bericht
    assert bericht["geloescht"] == 1, bericht
    assert not alt.exists()


def main() -> None:
    tests = [test_zweiter_claim_wird_abgelehnt,
             test_verwaisten_task_wieder_einreihen,
             test_giftiger_task_wird_aufgegeben,
             test_antwort_archiviert_die_frage,
             test_reihenfolge_ist_fifo,
             test_lock_verklemmt_sich_nicht_selbst,
             test_ids_sind_lang_und_kollisionsfrei,
             test_pflege_raeumt_und_reiht_ein]
    for test in tests:
        tmp = Path(tempfile.mkdtemp(prefix="mailbox-nl-"))
        try:
            test(tmp)
            print(f"OK  {test.__name__}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    print(f"alle {len(tests)} Nebenläufigkeits-Tests grün")


if __name__ == "__main__":
    main()
