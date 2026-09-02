"""Rollen für Task-Läufe (Dashboard-Paket St.1) — reine Stdlib + PyYAML:

    cd backend && python -m tests.test_rollen

Abgedeckt:
  * Parsen: ohne Frontmatter, Felder-Normalisierung (tools als Liste UND
    Komma-Text), kaputtes YAML wirft statt Rechte still zu ignorieren
  * Namensprüfung (Path-Traversal, Großschreibung)
  * liste_rollen zeigt kaputte Dateien mit `fehler` statt sie zu verschlucken
  * rolle_fuer_task: unbekannte Rolle nennt die verfügbaren
  * Envelope-Roundtrip: Rollen-Felder überleben put_task → claim_task
    (der Dateitransport des Watchers liest genau diese Felder)
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

from app import rollen  # noqa: E402
from app.mailbox import Mailbox, Task  # noqa: E402


class RollenTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="rollen-test-"))
        self._alt = rollen.ROLLEN_DIR
        rollen.ROLLEN_DIR = self.tmp / "rollen"

    def tearDown(self) -> None:
        rollen.ROLLEN_DIR = self._alt
        shutil.rmtree(self.tmp, ignore_errors=True)

    # --- Parsen -------------------------------------------------------------

    def test_ohne_frontmatter_ist_alles_prompt(self):
        rollen.speichere_rolle("nur_prompt", "Du bist gründlich.\n")
        r = rollen.lade_rolle("nur_prompt")
        self.assertEqual(r["prompt"], "Du bist gründlich.")
        self.assertIsNone(r["permission_mode"])
        self.assertIsNone(r["allowed_tools"])

    def test_felder_und_tools_normalisierung(self):
        text = ("---\nbeschreibung: Nur lesen\npermission_mode: default\n"
                "allowed_tools: [Read, Grep]\n---\nPrüfe den Code.\n")
        r = rollen.speichere_rolle("review", text)
        self.assertEqual(r["beschreibung"], "Nur lesen")
        self.assertEqual(r["allowed_tools"], ["Read", "Grep"])
        # Komma-Text statt Liste funktioniert genauso
        rollen.speichere_rolle("review2",
                               "---\nallowed_tools: Edit, Write\n---\nx")
        self.assertEqual(rollen.lade_rolle("review2")["allowed_tools"],
                         ["Edit", "Write"])

    def test_kaputtes_frontmatter_wirft(self):
        """Eine still ignorierte Rechte-Angabe wäre ein Sicherheitsproblem."""
        with self.assertRaises(rollen.RollenFehler):
            rollen.speichere_rolle("kaputt", "---\nallowed_tools: [ohne_ende\n---\nx")
        with self.assertRaises(rollen.RollenFehler):
            rollen.speichere_rolle("kaputt2", "---\n- nur: eine liste\n---\nx")

    # --- Namen --------------------------------------------------------------

    def test_namenspruefung(self):
        for boese in ("../raus", "a/b", "Review", "", "ä"):
            with self.assertRaises(rollen.RollenFehler, msg=boese):
                rollen.speichere_rolle(boese, "x")

    # --- Liste + Auflösung --------------------------------------------------

    def test_liste_zeigt_kaputte_datei_mit_fehler(self):
        rollen.speichere_rolle("gut", "---\nbeschreibung: ok\n---\nx")
        rollen.ROLLEN_DIR.mkdir(parents=True, exist_ok=True)
        (rollen.ROLLEN_DIR / "boese.md").write_text(
            "---\nallowed_tools: [kaputt\n---\nx", encoding="utf-8")
        eintraege = {r["name"]: r for r in rollen.liste_rollen()}
        self.assertIn("gut", eintraege)
        self.assertIn("fehler", eintraege["boese"])

    def test_unbekannte_rolle_nennt_verfuegbare(self):
        rollen.speichere_rolle("review", "x")
        with self.assertRaises(rollen.RollenFehler) as ctx:
            rollen.rolle_fuer_task("gibtsnicht")
        self.assertIn("review", str(ctx.exception))

    def test_rolle_fuer_task_felder(self):
        rollen.speichere_rolle(
            "review",
            "---\npermission_mode: default\nallowed_tools: []\n---\nNur prüfen.")
        felder = rollen.rolle_fuer_task("review")
        self.assertEqual(felder["rolle"], "review")
        self.assertEqual(felder["rollen_prompt"], "Nur prüfen.")
        self.assertEqual(felder["rollen_permission_mode"], "default")
        self.assertEqual(felder["rollen_tools"], [])

    # --- Envelope-Roundtrip (Dateitransport des Watchers) --------------------

    def test_rollen_felder_ueberleben_put_und_claim(self):
        box = Mailbox(self.tmp / "mailboxes", "werkstatt")
        box.put_task(Task(task_id="task-1", agent="werkstatt", instruction="mach",
                          rolle="review", rollen_prompt="Nur prüfen.",
                          rollen_permission_mode="default", rollen_tools=[]))
        env = box.claim_task("task-1")
        self.assertEqual(env["rolle"], "review")
        self.assertEqual(env["rollen_prompt"], "Nur prüfen.")
        self.assertEqual(env["rollen_permission_mode"], "default")
        self.assertEqual(env["rollen_tools"], [])


if __name__ == "__main__":
    unittest.main()
