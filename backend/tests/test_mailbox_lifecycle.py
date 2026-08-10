"""Mailbox-Lebenszyklus: Fehlschläge (#15) und Rückfragen-Parken (#17).

Nur Standardlib — läuft überall:  cd backend && python -m tests.test_mailbox_lifecycle
"""
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from app.mailbox import Mailbox, Task, merged_instruction


def _neu(root: Path, task_id: str, agent: str = "worker", sender: str = "chef") -> Mailbox:
    mb = Mailbox(root, agent)
    mb.put_task(Task(task_id=task_id, agent=agent, instruction=f"auftrag {task_id}",
                     sender=sender))
    return mb


def test_error_behaelt_beschreibung(root: Path) -> None:
    """#15: error → Task nach .failed/, instruction in Response und Envelope."""
    mb = _neu(root, "task-e1")
    mb.claim_task("task-e1")
    mb.write_response("task-e1", "", "error", log="explodiert")

    resp = json.loads((mb.outbox / "task-e1-response.json").read_text(encoding="utf-8"))
    assert resp["instruction"] == "auftrag task-e1", resp
    assert (mb.failed / "task-e1.json").exists()
    assert not (mb.processing / "task-e1.json").exists()
    envs = [json.loads(p.read_text(encoding="utf-8"))
            for p in (root / "chef" / "inbox").glob("*.json")]
    antworten = [e for e in envs if e.get("kind") == "response"]
    assert antworten and antworten[0]["instruction"] == "auftrag task-e1", envs

    # done unverändert: weg ist weg, nichts in .failed
    mb2 = _neu(root, "task-e2")
    mb2.write_response("task-e2", "fertig", "done")
    assert not (mb2.failed / "task-e2.json").exists()
    assert not (mb2.inbox / "task-e2.json").exists()


def test_rueckfrage_parkt_und_stoesst_wieder_an(root: Path) -> None:
    """#17: ask → link, done → parken, answer → Nachtrag + zurück in die Inbox."""
    mb = _neu(root, "task-q1")
    mb.claim_task("task-q1")

    # ask(): Frage geht an den chef, wird an den laufenden Task geheftet
    frage = Mailbox(root, "chef").post({
        "kind": "question", "sender": "worker", "to": "chef",
        "text": "Prod oder Staging?", "status": "needs_confirm",
    })
    mb.link_question(frage["id"], frage["text"])

    # --print-Lauf endet, Watcher meldet done → parken statt abschließen
    offen = mb.park_wenn_offene_fragen("task-q1", "Ich müsste erst wissen, wo.")
    assert offen and offen[0]["id"] == frage["id"], offen
    env = json.loads((mb.processing / "task-q1.json").read_text(encoding="utf-8"))
    assert env["status"] == "needs_confirm"
    assert env["zwischenstand"].startswith("Ich müsste")
    assert not (mb.outbox / "task-q1-response.json").exists()

    # Antwort → Frage abgeräumt, Task zurück in der Inbox, Kontext im Prompt
    wieder = mb.resolve_question(frage["id"], "Staging.")
    assert wieder == ["task-q1"], wieder
    assert not (mb.processing / "task-q1.json").exists()
    env = json.loads((mb.inbox / "task-q1.json").read_text(encoding="utf-8"))
    assert env["status"] == "pending" and not env["open_questions"]
    prompt = merged_instruction(env)
    assert "auftrag task-q1" in prompt
    assert "Prod oder Staging?" in prompt and "Staging." in prompt
    assert "Zwischenstand" in prompt

    # erneuter Abschluss ohne offene Fragen → ganz normal done
    mb.claim_task("task-q1")
    assert mb.park_wenn_offene_fragen("task-q1", "egal") is None
    mb.write_response("task-q1", "erledigt auf Staging", "done")
    assert (mb.outbox / "task-q1-response.json").exists()


def test_antwort_waehrend_lauf_parkt_nicht(root: Path) -> None:
    """#17: kommt die Antwort noch WÄHREND des Laufs, wird normal abgeschlossen."""
    mb = _neu(root, "task-q2")
    mb.claim_task("task-q2")
    frage = Mailbox(root, "chef").post({
        "kind": "question", "sender": "worker", "to": "chef",
        "text": "Wirklich?", "status": "needs_confirm",
    })
    mb.link_question(frage["id"], frage["text"])
    # Antwort landet in der Worker-Inbox, bevor der Lauf endet
    mb.post({"kind": "answer", "sender": "chef", "to": "worker",
             "text": "Ja.", "reply_to": frage["id"]})
    assert mb.park_wenn_offene_fragen("task-q2", "done-text") is None

    # resolve während der Task noch läuft: nur Nachtrag, kein Verschieben
    mb2 = _neu(root, "task-q3")
    mb2.claim_task("task-q3")
    frage2 = Mailbox(root, "chef").post({
        "kind": "question", "sender": "worker", "to": "chef",
        "text": "Port?", "status": "needs_confirm",
    })
    mb2.link_question(frage2["id"], frage2["text"])
    assert mb2.resolve_question(frage2["id"], "8080") == []
    env = json.loads((mb2.processing / "task-q3.json").read_text(encoding="utf-8"))
    assert env["nachtraege"][0]["antwort"] == "8080" and not env["open_questions"]


def main() -> None:
    for test in (test_error_behaelt_beschreibung,
                 test_rueckfrage_parkt_und_stoesst_wieder_an,
                 test_antwort_waehrend_lauf_parkt_nicht):
        tmp = Path(tempfile.mkdtemp(prefix="mailbox-test-"))
        try:
            test(tmp)
            print(f"OK  {test.__name__}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    print("alle Mailbox-Lifecycle-Tests grün")


if __name__ == "__main__":
    main()
