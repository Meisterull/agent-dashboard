"""Mailbox-Lebenszyklus: Fehlschläge (#15), Rückfragen-Parken (#17), der
Ausgang aus einer Rückfrage (#23: schliesse_frage/verwerfe_frage), der Vermerk
"wer hat geantwortet" (#22) und das Inbox-Aufräumen (#21: alle_gelesen +
Rotation alter response/answer).

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


def test_frage_schliessen_laesst_task_scheitern(root: Path) -> None:
    """#23: Frage ohne Antwort schließen → Frage ins Archiv, Task nach .failed."""
    mb = _neu(root, "task-c1")                      # worker-Mailbox, Auftrag vom chef
    mb.claim_task("task-c1")
    chef = Mailbox(root, "chef")
    frage = chef.post({
        "kind": "question", "sender": "worker", "to": "chef",
        "text": "Prod oder Staging?", "status": "needs_confirm",
    })
    mb.link_question(frage["id"], frage["text"])
    assert mb.park_wenn_offene_fragen("task-c1", "warte auf Antwort") is not None

    ergebnis = chef.schliesse_frage(frage["id"], "hat sich erledigt")
    assert ergebnis["to"] == "worker" and ergebnis["gescheiterte_tasks"] == ["task-c1"]

    # Frage: archiviert, mit Grund — nicht als beantwortet getarnt
    assert not (chef.inbox / f"{frage['id']}.json").exists()
    archiviert = json.loads(
        (chef.archive / f"{frage['id']}.json").read_text(encoding="utf-8"))
    assert archiviert["closed_reason"] == "hat sich erledigt"
    assert archiviert.get("answered_at") is None

    # Task: raus aus .processing, mit instruction in .failed (#15), Klartext im Ergebnis
    assert not (mb.processing / "task-c1.json").exists()
    gescheitert = json.loads((mb.failed / "task-c1.json").read_text(encoding="utf-8"))
    assert gescheitert["instruction"] == "auftrag task-c1"
    assert "ohne Antwort geschlossen" in gescheitert["nachtraege"][0]["antwort"]
    resp = json.loads((mb.outbox / "task-c1-response.json").read_text(encoding="utf-8"))
    assert resp["status"] == "error" and "hat sich erledigt" in resp["result"]
    assert resp["instruction"] == "auftrag task-c1"

    # Wiederanlauf möglich: der Prompt trägt den Vermerk statt einer Antwort
    assert "ohne Antwort geschlossen" in merged_instruction(gescheitert)


def test_frage_schliessen_laesst_task_mit_weiterer_frage_geparkt(root: Path) -> None:
    """#23: hängt noch eine zweite Frage am Task, bleibt er geparkt."""
    mb = _neu(root, "task-c2")
    mb.claim_task("task-c2")
    chef = Mailbox(root, "chef")
    fragen = [chef.post({"kind": "question", "sender": "worker", "to": "chef",
                         "text": t, "status": "needs_confirm"})
              for t in ("Port?", "Host?")]
    for f in fragen:
        mb.link_question(f["id"], f["text"])
    mb.park_wenn_offene_fragen("task-c2", "beides offen")

    assert chef.schliesse_frage(fragen[0]["id"], "egal")["gescheiterte_tasks"] == []
    env = json.loads((mb.processing / "task-c2.json").read_text(encoding="utf-8"))
    assert env["status"] == "needs_confirm"
    assert [f["id"] for f in env["open_questions"]] == [fragen[1]["id"]]

    # die zweite Frage schließt ihn dann doch ab
    assert chef.schliesse_frage(fragen[1]["id"], "auch egal")["gescheiterte_tasks"] == ["task-c2"]
    assert (mb.failed / "task-c2.json").exists()


def test_antwort_vermerkt_wer_geantwortet_hat(root: Path) -> None:
    """#22: antwortet der Mensch anstelle des Agenten, steht das im Envelope."""
    chef = Mailbox(root, "chef")
    frage = chef.post({"kind": "question", "sender": "worker", "to": "chef",
                       "text": "Port?", "status": "needs_confirm"})
    chef.beantworte_frage(frage["id"], "8080", answered_by="dashboard")
    antworten = [e for e in Mailbox(root, "worker").read_inbox("answer")
                 if e["reply_to"] == frage["id"]]
    assert antworten and antworten[0]["answered_by"] == "dashboard", antworten

    # ohne Angabe (MCP-`answer` eines Agenten) bleibt das Feld weg
    frage2 = chef.post({"kind": "question", "sender": "worker", "to": "chef",
                        "text": "Host?", "status": "needs_confirm"})
    chef.beantworte_frage(frage2["id"], "localhost")
    antwort2 = [e for e in Mailbox(root, "worker").read_inbox("answer")
                if e["reply_to"] == frage2["id"]][0]
    assert "answered_by" not in antwort2


def test_alles_gelesen_raeumt_nur_erledigtes(root: Path) -> None:
    """#21: der 'alles gelesen'-Knopf archiviert Protokoll, nie Arbeitsvorrat."""
    mb = _neu(root, "task-offen")                      # offener Task
    mb.post({"kind": "response", "sender": "worker", "to": "chef",
             "text": "fertig", "reply_to": "task-alt"})
    mb.post({"kind": "message", "sender": "chef", "to": "worker", "text": "hallo"})
    mb.post({"kind": "question", "sender": "worker", "to": "chef",
             "text": "welcher Port?", "status": "needs_confirm"})

    assert mb.alle_gelesen() == 2                      # response + message

    uebrig = {e.get("kind", "task") for e in mb.read_inbox()}
    assert uebrig == {"task", "question"}, uebrig      # beides bleibt liegen
    assert len(list(mb.archive.glob("*.json"))) == 2


def test_inbox_rotation_nimmt_nur_alte_antworten(root: Path) -> None:
    """#21: aufraeumen(inbox_tage) verschiebt alte response/answer ins Archiv."""
    import os
    import time

    mb = _neu(root, "task-jung")
    alt = mb.post({"kind": "response", "sender": "worker", "to": "chef", "text": "alt"})
    neu = mb.post({"kind": "response", "sender": "worker", "to": "chef", "text": "neu"})
    vorgestern = time.time() - 20 * 86400
    os.utime(mb.inbox / f"{alt['id']}.json", (vorgestern, vorgestern))
    # Auch ein ALTER Task darf nicht angefasst werden — Arbeitsvorrat.
    os.utime(mb.inbox / "task-jung.json", (vorgestern, vorgestern))

    assert mb.aufraeumen(30, inbox_tage=14) == 1
    ids = {e.get("id") for e in mb.read_inbox()}   # Tasks haben kein 'id'
    assert neu["id"] in ids and alt["id"] not in ids
    assert (mb.inbox / "task-jung.json").exists()
    assert (mb.archive / f"{alt['id']}.json").exists()  # verschoben, nicht gelöscht

    # Ohne inbox_tage bleibt die Inbox unangetastet (Altverhalten).
    os.utime(mb.inbox / f"{neu['id']}.json", (vorgestern, vorgestern))
    assert mb.aufraeumen(30) == 0


def main() -> None:
    for test in (test_error_behaelt_beschreibung,
                 test_rueckfrage_parkt_und_stoesst_wieder_an,
                 test_antwort_waehrend_lauf_parkt_nicht,
                 test_frage_schliessen_laesst_task_scheitern,
                 test_frage_schliessen_laesst_task_mit_weiterer_frage_geparkt,
                 test_antwort_vermerkt_wer_geantwortet_hat,
                 test_alles_gelesen_raeumt_nur_erledigtes,
                 test_inbox_rotation_nimmt_nur_alte_antworten):
        tmp = Path(tempfile.mkdtemp(prefix="mailbox-test-"))
        try:
            test(tmp)
            print(f"OK  {test.__name__}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    print("alle Mailbox-Lifecycle-Tests grün")


if __name__ == "__main__":
    main()
