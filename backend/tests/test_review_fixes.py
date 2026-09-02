"""Regressionen aus dem Voll-Review 02.09.2026 (docs/REVIEW-2026-09-02.md).

Nagelt die P0-Fixes fest:
  P0-1  Task-/Envelope-IDs werden in den Mailbox-PRIMITIVEN geprüft —
        der reproduzierte Ausbruch (complete_task über ../../) ist zu
  P0-3  Outbox-Schreibfehler wirft das Ergebnis nicht mehr weg und
        killt den Watcher nicht (Task bleibt in .processing)
  P0-5  Ein kaputter Nachbar-Plan blockiert den Termin-Stempel nicht
  P0-6  Der Client-`letzter_lauf` aus dem Dialog wird ignoriert
  P0-7  Rollen-Frontmatter ohne schließendes --- wird abgelehnt
  P1-8  Umlaute in Passwort/Login geben False statt TypeError/500

    cd backend && python -m tests.test_review_fixes
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))
sys.path.insert(0, str(REPO / "scripts"))

from app import auth, rollen, zeitplaene  # noqa: E402
from app.mailbox import Mailbox, Task  # noqa: E402
import agent_watcher as aw  # noqa: E402


class IdTraversalTests(unittest.TestCase):
    """P0-1: kein Ausbruch aus der eigenen Mailbox über Task-/Envelope-IDs."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="idcheck-"))
        self.opfer = Mailbox(self.tmp, "opfer")
        self.angreifer = Mailbox(self.tmp, "angreifer")
        self.opfer.put_task(Task(task_id="task-1", agent="opfer",
                                 instruction="geheim", sender="chef"))

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_write_response_lehnt_fremde_pfade_ab(self):
        boese = "../../opfer/inbox/task-1"
        with self.assertRaises(ValueError):
            self.angreifer.write_response(boese, "gefaelscht", "error")
        # Der fremde Task liegt unangetastet in der Opfer-Inbox:
        self.assertTrue((self.opfer.inbox / "task-1.json").exists())
        self.assertEqual(list(self.angreifer.failed.glob("*")), [])

    def test_claim_task_offen_mark_read_pruefen_ids(self):
        for boese in ("../../opfer/inbox/task-1", "a/b", "", "task\n1"):
            with self.assertRaises(ValueError, msg=boese):
                self.angreifer.claim_task(boese)
            with self.assertRaises(ValueError, msg=boese):
                self.angreifer.task_offen(boese)
            with self.assertRaises(ValueError, msg=boese):
                self.angreifer.mark_read(boese)

    def test_gueltige_ids_funktionieren_weiter(self):
        env = self.opfer.claim_task("task-1")
        self.assertEqual(env["instruction"], "geheim")
        self.opfer.write_response("task-1", "fertig", "done")
        self.assertTrue((self.opfer.outbox / "task-1-response.json").exists())


class WatcherOutboxTests(unittest.TestCase):
    """P0-3: Outbox-Fehler = Task bleibt in .processing, kein Crash."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="outboxfail-"))
        base = self.tmp / "werkstatt"
        self.inbox = base / "inbox"
        self.processing = self.inbox / ".processing"
        self.outbox = base / "outbox"
        for d in (self.inbox, self.processing, self.outbox):
            d.mkdir(parents=True)
        (self.inbox / "task-x.json").write_text(json.dumps({
            "task_id": "task-x", "kind": "task", "instruction": "mach",
            "created_at": "2026-09-02T07:00:00",
        }), encoding="utf-8")
        self._orig = aw.atomic_write_json

    def tearDown(self) -> None:
        aw.atomic_write_json = self._orig
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_outbox_fehler_laesst_task_in_arbeit(self):
        def kaputt(path, data):
            if str(path).endswith("-response.json"):
                raise OSError("Platte voll")
            return self._orig(path, data)

        aw.atomic_write_json = kaputt
        handled = aw.process_once(self.inbox, self.processing, self.outbox,
                                  "werkstatt", "egal", self.tmp, True)
        # Nicht abgeschlossen, nicht verloren, nicht gecrasht:
        self.assertEqual(handled, 0)
        self.assertTrue((self.processing / "task-x.json").exists())
        self.assertEqual(list(self.outbox.glob("*")), [])
        # Der Anspruch trägt den P0-2-Stempel (requeue misst ab Claim):
        env = json.loads((self.processing / "task-x.json").read_text())
        self.assertTrue(env.get("claimed_at"))


class ZeitplanStempelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="stempel-"))
        self._yaml = zeitplaene.ZEITPLAENE_YAML
        zeitplaene.ZEITPLAENE_YAML = self.tmp / "zeitplaene.yaml"

    def tearDown(self) -> None:
        zeitplaene.ZEITPLAENE_YAML = self._yaml
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_kaputter_nachbar_blockiert_stempel_nicht(self):
        """P0-5: sonst feuert der gültige Plan bei jedem Tick (Task-Salve)."""
        gut = {"name": "gut", "agent": "a", "instruction": "x",
               "zeit": "07:00", "tage": [], "an": True, "nachholen": True}
        kaputt = {"name": "kaputt", "agent": "a", "instruction": "",
                  "zeit": "07:00", "tage": [], "an": True}
        zeitplaene._schreibe_roh([gut, kaputt])
        zeitplaene._stempel("gut", "2026-09-02T07:00:00")
        plaene, fehler = zeitplaene.lade_plaene()
        self.assertIsNone(fehler)
        self.assertEqual(
            next(p for p in plaene if p["name"] == "gut")["letzter_lauf"],
            "2026-09-02T07:00:00")

    def test_client_stempel_wird_ignoriert(self):
        """P0-6: der Dialog schickt seinen alten Stand zurück — Doppelfeuer."""
        plan = {"name": "n1", "agent": "a", "instruction": "x", "zeit": "07:00"}
        zeitplaene.speichere_plaene([plan])
        zeitplaene._stempel("n1", "2026-09-02T07:00:00")
        ergebnis = zeitplaene.speichere_plaene(
            [{**plan, "letzter_lauf": "1999-01-01T00:00:00"}])
        self.assertEqual(ergebnis[0]["letzter_lauf"], "2026-09-02T07:00:00")


class SettingsKeysTests(unittest.TestCase):
    """Review N: der Kommentar an SettingsIn ('neue Felder auch in
    ALLOWED_KEYS') wird hier zum Test — save_settings verwirft unbekannte
    Keys STILL, der Drift fiele sonst erst dem Nutzer auf."""

    def test_settingsin_felder_sind_erlaubt(self):
        import re
        from app import config
        quelle = (Path(__file__).resolve().parents[1] / "main.py").read_text(
            encoding="utf-8")
        block = quelle.split("class SettingsIn", 1)[1]
        block = block.split("\n\n\n", 1)[0]
        felder = re.findall(r"^    (\w+):", block, re.MULTILINE)
        self.assertTrue(felder, "SettingsIn-Felder nicht gefunden")
        fehlend = [f for f in felder if f not in config.ALLOWED_KEYS]
        self.assertEqual(fehlend, [],
                         f"SettingsIn-Felder fehlen in ALLOWED_KEYS: {fehlend}")


class RollenFrontmatterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="frontmatter-"))
        self._dir = rollen.ROLLEN_DIR
        rollen.ROLLEN_DIR = self.tmp / "rollen"

    def tearDown(self) -> None:
        rollen.ROLLEN_DIR = self._dir
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_unvollstaendiges_frontmatter_wird_abgelehnt(self):
        """P0-7: Rechte-Angaben dürfen nie still als Prompt durchrutschen."""
        with self.assertRaises(rollen.RollenFehler):
            rollen.speichere_rolle(
                "halb", "---\npermission_mode: plan\nallowed_tools: [Read]\nPrompt ohne Abschluss")


class AuthUmlautTests(unittest.TestCase):
    """P1-8: compare_digest über Bytes — kein TypeError bei Nicht-ASCII."""

    def setUp(self) -> None:
        self._pw = os.environ.get("ADMIN_INITIAL_PASSWORD")
        auth._abgeleitet = None

    def tearDown(self) -> None:
        if self._pw is None:
            os.environ.pop("ADMIN_INITIAL_PASSWORD", None)
        else:
            os.environ["ADMIN_INITIAL_PASSWORD"] = self._pw
        auth._abgeleitet = None

    def test_umlaute_geben_false_statt_typeerror(self):
        os.environ["ADMIN_INITIAL_PASSWORD"] = "geheim"
        self.assertFalse(auth.verify_password("gehäim"))
        os.environ["ADMIN_INITIAL_PASSWORD"] = "pässwört"
        self.assertTrue(auth.verify_password("pässwört"))
        self.assertFalse(auth.verify_password("passwort"))


if __name__ == "__main__":
    unittest.main()
