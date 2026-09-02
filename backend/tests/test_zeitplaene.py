"""Zeitpläne + geplante Tasks (Dashboard-Paket St.2) — Stdlib + PyYAML:

    cd backend && python -m tests.test_zeitplaene

Abgedeckt:
  * ist_faellig: pünktlich (Kulanz), verfallen ohne nachholen, EIN Nachzügler
    mit nachholen, Wochentagsfilter, schon-gelaufen-Stempel, an=false
  * Plan-Validierung (Name, Zeit, Tage, Doppelte) und der erhaltene
    letzter_lauf beim erneuten Speichern ohne Stempel
  * jetzt_ausfuehren: postet einen echten Task (inkl. Rollen-Feldern) und
    stempelt den Plan
  * nicht_vor: claim_tasks überspringt Geplantes, claim_task wirft ZuFrueh,
    der Datei-Watcher (inbox_tasks) lässt Geplantes liegen — kaputte
    Zeitstempel frieren nichts ein
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))
sys.path.insert(0, str(REPO / "scripts"))

from app import rollen, verbrauch, zeitplaene  # noqa: E402
from app.mailbox import Mailbox, Task, ZuFrueh, zu_frueh  # noqa: E402
import agent_watcher as aw  # noqa: E402


from zoneinfo import ZoneInfo  # noqa: E402

ZONE = ZoneInfo("Europe/Berlin")  # der Planer rechnet in dieser Zone (P1-6)


def lokal(*args) -> datetime:
    return datetime(*args, tzinfo=ZONE)


# 2026-09-02 ist ein Mittwoch — feste Daten statt "heute", damit der
# Wochentagsfilter deterministisch bleibt.
MI_0700 = lokal(2026, 9, 2, 7, 0)


class FaelligTests(unittest.TestCase):
    def plan(self, **extra):
        return {"name": "p", "agent": "a", "instruction": "x",
                "zeit": "07:00", "tage": [], "an": True, **extra}

    def test_puenktlich_innerhalb_der_kulanz(self):
        soll = zeitplaene.ist_faellig(self.plan(), MI_0700 + timedelta(seconds=90))
        self.assertEqual(soll, MI_0700)

    def test_verfallen_ohne_nachholen(self):
        self.assertIsNone(
            zeitplaene.ist_faellig(self.plan(), MI_0700 + timedelta(hours=2)))

    def test_nachholen_holt_genau_den_juengsten(self):
        jetzt = lokal(2026, 9, 2, 9, 0)
        soll = zeitplaene.ist_faellig(self.plan(nachholen=True), jetzt)
        self.assertEqual(soll, MI_0700)
        # Nach dem Stempel auf den jüngsten Termin ist Ruhe — keine Salve.
        gelaufen = self.plan(nachholen=True,
                             letzter_lauf=soll.isoformat(timespec="seconds"))
        self.assertIsNone(zeitplaene.ist_faellig(gelaufen, jetzt))

    def test_wochentagsfilter(self):
        # Nur samstags; jetzt Mittwoch: ohne nachholen nichts, mit nachholen
        # der letzte Samstag (29.08.2026).
        plan = self.plan(tage=["sa"])
        jetzt = lokal(2026, 9, 2, 9, 0)
        self.assertIsNone(zeitplaene.ist_faellig(plan, jetzt))
        soll = zeitplaene.ist_faellig({**plan, "nachholen": True}, jetzt)
        self.assertEqual(soll, lokal(2026, 8, 29, 7, 0))

    def test_schon_gelaufen_und_aus(self):
        gelaufen = self.plan(letzter_lauf=MI_0700.isoformat(timespec="seconds"))
        self.assertIsNone(
            zeitplaene.ist_faellig(gelaufen, MI_0700 + timedelta(seconds=90)))
        self.assertIsNone(
            zeitplaene.ist_faellig(self.plan(an=False),
                                   MI_0700 + timedelta(seconds=90)))

    # --- Sommerzeit (Review P1-6, beide Kanten verifiziert gemeldet) -------

    def test_rueckstellung_laeuft_nur_einmal(self):
        """25.10.2026: 02:30 existiert zweimal — nach dem Stempel ist Ruhe
        (ein Plan läuft höchstens einmal je Kalendertag)."""
        plan = self.plan(zeit="02:30", nachholen=True)
        jetzt = lokal(2026, 10, 25, 9, 0)
        soll = zeitplaene.ist_faellig(plan, jetzt)
        self.assertIsNotNone(soll)
        self.assertEqual(soll.date().isoformat(), "2026-10-25")
        gelaufen = {**plan, "letzter_lauf": soll.isoformat(timespec="seconds")}
        self.assertIsNone(zeitplaene.ist_faellig(gelaufen, jetzt))

    def test_vorstellung_verfaellt_nicht_still(self):
        """29.03.2026: 02:30 existiert nicht — mit nachholen läuft trotzdem
        genau EIN Termin, statt still auszufallen."""
        plan = self.plan(zeit="02:30", nachholen=True)
        soll = zeitplaene.ist_faellig(plan, lokal(2026, 3, 29, 9, 0))
        self.assertIsNotNone(soll)
        self.assertEqual(soll.date().isoformat(), "2026-03-29")


class VerwaltungTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="zeitplaene-test-"))
        self._yaml = zeitplaene.ZEITPLAENE_YAML
        self._root = zeitplaene.MAILBOX_ROOT
        self._rollen = rollen.ROLLEN_DIR
        zeitplaene.ZEITPLAENE_YAML = self.tmp / "zeitplaene.yaml"
        zeitplaene.MAILBOX_ROOT = self.tmp / "mailboxes"
        rollen.ROLLEN_DIR = self.tmp / "rollen"
        self._vroot = verbrauch.MAILBOX_ROOT
        verbrauch.MAILBOX_ROOT = self.tmp / "mailboxes"

    def tearDown(self) -> None:
        zeitplaene.ZEITPLAENE_YAML = self._yaml
        zeitplaene.MAILBOX_ROOT = self._root
        rollen.ROLLEN_DIR = self._rollen
        verbrauch.MAILBOX_ROOT = self._vroot
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_validierung(self):
        gut = {"name": "n1", "agent": "a", "instruction": "x", "zeit": "07:00"}
        for kaputt in (
            {**gut, "name": "Groß"},
            {**gut, "zeit": "25:00"},
            {**gut, "zeit": "7:00"},
            {**gut, "tage": ["montag"]},
            {**gut, "instruction": "  "},
        ):
            with self.assertRaises(zeitplaene.ZeitplanFehler):
                zeitplaene.speichere_plaene([kaputt])
        with self.assertRaises(zeitplaene.ZeitplanFehler):
            zeitplaene.speichere_plaene([gut, dict(gut)])  # doppelter Name

    def test_letzter_lauf_bleibt_beim_speichern_erhalten(self):
        plan = {"name": "n1", "agent": "a", "instruction": "x", "zeit": "07:00"}
        zeitplaene.speichere_plaene([plan])
        zeitplaene._stempel("n1", "2026-09-02T07:00:00")
        # Client schickt den Plan OHNE Stempel zurück (Dialog-Roundtrip):
        gespeichert = zeitplaene.speichere_plaene([plan])
        self.assertEqual(gespeichert[0]["letzter_lauf"], "2026-09-02T07:00:00")

    def test_jetzt_ausfuehren_postet_task_mit_rolle_und_stempelt(self):
        rollen.speichere_rolle(
            "review", "---\npermission_mode: default\nallowed_tools: []\n---\nPrüfe.")
        Mailbox(zeitplaene.MAILBOX_ROOT, "werkstatt")  # Mailbox anlegen
        zeitplaene.speichere_plaene([{
            "name": "n1", "agent": "werkstatt", "instruction": "mach",
            "zeit": "07:00", "rolle": "review",
        }])
        bericht = zeitplaene.jetzt_ausfuehren("n1")
        self.assertEqual(bericht["agent"], "werkstatt")
        inbox = list((zeitplaene.MAILBOX_ROOT / "werkstatt" / "inbox").glob("task-*.json"))
        self.assertEqual(len(inbox), 1)
        env = json.loads(inbox[0].read_text(encoding="utf-8"))
        self.assertEqual(env["rolle"], "review")
        self.assertEqual(env["rollen_prompt"], "Prüfe.")
        plaene, _ = zeitplaene.lade_plaene()
        self.assertTrue(plaene[0].get("letzter_lauf"))

    def test_verbrauchsschwelle_pausiert_geplante_tasks(self):
        box = Mailbox(zeitplaene.MAILBOX_ROOT, "werkstatt")
        (box.outbox / "task-alt-response.json").write_text(json.dumps({
            "responded_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "verbrauch": {"output_tokens": 2000},
        }), encoding="utf-8")
        zeitplaene.speichere_plaene([{
            "name": "n1", "agent": "werkstatt", "instruction": "mach",
            "zeit": "07:00",
        }])
        plan = zeitplaene.lade_plaene()[0][0]
        jetzt = datetime.now().astimezone()
        bericht = zeitplaene._poste(plan, jetzt, schwelle=1000)
        self.assertIn("pausiert", bericht.get("fehler", ""))
        self.assertEqual(
            len(list((zeitplaene.MAILBOX_ROOT / "werkstatt" / "inbox").glob("task-*.json"))), 0)
        # Ohne Schwelle läuft derselbe Plan:
        bericht = zeitplaene._poste(plan, jetzt, schwelle=0)
        self.assertNotIn("fehler", bericht)


class NichtVorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="nichtvor-test-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _task(self, task_id: str, nicht_vor: str | None) -> Task:
        return Task(task_id=task_id, agent="a", instruction="x",
                    nicht_vor=nicht_vor)

    def test_zu_frueh_helfer(self):
        zukunft = (datetime.now().astimezone() + timedelta(hours=1)).isoformat()
        self.assertTrue(zu_frueh({"nicht_vor": zukunft}))
        self.assertFalse(zu_frueh({"nicht_vor": "2020-01-01T00:00"}))
        self.assertFalse(zu_frueh({"nicht_vor": "kaputt"}))
        self.assertFalse(zu_frueh({}))

    def test_claim_laesst_geplantes_liegen(self):
        box = Mailbox(self.tmp, "a")
        zukunft = (datetime.now().astimezone() + timedelta(hours=1)).isoformat()
        box.put_task(self._task("task-plan", zukunft))
        box.put_task(self._task("task-jetzt", None))
        geliefert = [tid for _, env in box.claim_tasks()
                     for tid in [env["task_id"]]]
        self.assertEqual(geliefert, ["task-jetzt"])
        with self.assertRaises(ZuFrueh):
            box.claim_task("task-plan")
        # Vergangenes nicht_vor liefert normal aus:
        box2 = Mailbox(self.tmp, "b")
        box2.put_task(self._task("task-alt", "2020-01-01T00:00"))
        self.assertIsNotNone(box2.claim_task("task-alt"))

    def test_datei_watcher_ueberspringt_geplantes(self):
        inbox = self.tmp / "inbox"
        inbox.mkdir()
        zukunft = (datetime.now().astimezone() + timedelta(hours=1)).isoformat()
        for name, nicht_vor in (("task-plan", zukunft), ("task-jetzt", None),
                                ("task-kaputt", "unlesbar")):
            (inbox / f"{name}.json").write_text(json.dumps({
                "task_id": name, "kind": "task", "instruction": "x",
                "created_at": "2026-09-02T07:00:00",
                **({"nicht_vor": nicht_vor} if nicht_vor else {}),
            }), encoding="utf-8")
        namen = [p.stem for p in aw.inbox_tasks(inbox)]
        self.assertIn("task-jetzt", namen)
        self.assertIn("task-kaputt", namen)  # kaputter Stempel friert nicht ein
        self.assertNotIn("task-plan", namen)


if __name__ == "__main__":
    unittest.main()
