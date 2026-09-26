"""Tests für app/ereignisse.py (Ereignis-Log je Agent, 26.09.2026):

    cd backend && python -m tests.test_ereignisse

Abgedeckt: Schreiben/Lesen (neueste zuerst, Filter art/seit/vor/nur_probleme,
„mehr"), kaputte Zeilen, Namensprüfung, Zeilen-Deckel bei Riesen-Details,
inkrementelles Lesen ab Offset (halbe Zeile bleibt liegen, Schrumpfen nach
Rotation), Rotation (Tage + max_zeilen, nichts schreiben wenn nichts weg muss),
lauf_ereignisse (Lauf/Sitzung/Verweigerung/Timeout, Kontext-Neustart =
warnung, Nachtragen nur mit Zahlen).
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import ereignisse as er  # noqa: E402


class TestSchreibenLesen(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="ereignisse-"))

    def test_roundtrip_und_reihenfolge(self):
        e1 = er.schreibe(self.root, "erp", "lauf", "Lauf done", task_id="t1", dauer=12.5)
        e2 = er.schreibe(self.root, "erp", "verweigert", "1 verweigert", schwere="warnung")
        self.assertEqual(e1["details"], {"dauer": 12.5})
        erg = er.lies(self.root, "erp")
        self.assertEqual([e["art"] for e in erg["eintraege"]], ["verweigert", "lauf"])
        self.assertEqual(erg["gesamt"], 2)
        self.assertFalse(erg["mehr"])
        self.assertEqual(erg["eintraege"][1]["task_id"], "t1")
        self.assertIsNone(erg["eintraege"][0]["task_id"])
        # jede Zeile ist für sich gültiges JSON
        zeilen = er.pfad(self.root, "erp").read_text(encoding="utf-8").splitlines()
        self.assertEqual([json.loads(z)["art"] for z in zeilen], ["lauf", "verweigert"])
        self.assertEqual(json.loads(zeilen[1])["zeit"], e2["zeit"])

    def test_filter_limit_mehr(self):
        for i in range(6):
            er.schreibe(self.root, "erp", "lauf", f"L{i}", task_id=f"t{i}")
        er.schreibe(self.root, "erp", "timeout", "T", schwere="warnung")
        er.schreibe(self.root, "erp", "fehlserie", "F", schwere="fehler")
        erg = er.lies(self.root, "erp", limit=3)
        self.assertEqual(len(erg["eintraege"]), 3)
        self.assertTrue(erg["mehr"])
        self.assertEqual(erg["eintraege"][0]["art"], "fehlserie")
        # nur Probleme
        erg = er.lies(self.root, "erp", nur_probleme=True)
        self.assertEqual([e["art"] for e in erg["eintraege"]], ["fehlserie", "timeout"])
        # art als Liste
        erg = er.lies(self.root, "erp", art=["timeout", "lauf"], limit=100)
        self.assertEqual(len(erg["eintraege"]), 7)
        # „mehr laden": vor = zeit des ältesten sichtbaren → nur echt Älteres
        alle = er.lies(self.root, "erp", limit=100)["eintraege"]
        vor = alle[2]["zeit"]
        aeltere = er.lies(self.root, "erp", vor=vor, limit=100)["eintraege"]
        self.assertTrue(all(e["zeit"] < vor for e in aeltere))
        # seit in der Zukunft → leer
        zukunft = (datetime.now().astimezone() + timedelta(days=1)).isoformat(timespec="seconds")
        self.assertEqual(er.lies(self.root, "erp", seit=zukunft)["eintraege"], [])

    def test_kaputte_zeilen_und_leere_datei(self):
        self.assertEqual(er.lies(self.root, "nix"), {"eintraege": [], "mehr": False, "gesamt": 0})
        p = er.pfad(self.root, "erp")
        p.parent.mkdir(parents=True)
        p.write_text('{"zeit":"2026-09-26T10:00:00+02:00","art":"lauf","schwere":"info","text":"ok"}\n'
                     "kaputt\n{\"ohne\":\"zeit\"}\n", encoding="utf-8")
        erg = er.lies(self.root, "erp")
        self.assertEqual(len(erg["eintraege"]), 1)

    def test_namen_arten_schweren(self):
        with self.assertRaises(ValueError):
            er.pfad(self.root, "../x")
        with self.assertRaises(ValueError):
            er.schreibe(self.root, "erp", "quatsch", "x")
        with self.assertRaises(ValueError):
            er.schreibe(self.root, "erp", "lauf", "x", schwere="egal")
        self.assertEqual(er.lies(self.root, "a/b")["eintraege"], [])

    def test_riesige_details_werden_gekappt(self):
        e = er.schreibe(self.root, "erp", "lauf", "x", riesig="y" * 10_000)
        self.assertEqual(e["details"], {})
        zeile = er.pfad(self.root, "erp").read_text(encoding="utf-8")
        self.assertLess(len(zeile.encode()), er.MAX_ZEILE_BYTES + 2)


class TestInkrementellUndRotation(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="ereignisse-"))

    def test_lies_ab_offset(self):
        self.assertEqual(er.lies_ab(self.root, "erp", 0), ([], 0))
        er.schreibe(self.root, "erp", "lauf", "eins")
        neu, off = er.lies_ab(self.root, "erp", 0)
        self.assertEqual([e["text"] for e in neu], ["eins"])
        self.assertEqual(er.lies_ab(self.root, "erp", off), ([], off))
        er.schreibe(self.root, "erp", "lauf", "zwei")
        # halbe Zeile am Ende bleibt liegen
        p = er.pfad(self.root, "erp")
        with open(p, "a", encoding="utf-8") as f:
            f.write('{"zeit":"2026-')
        neu, off2 = er.lies_ab(self.root, "erp", off)
        self.assertEqual([e["text"] for e in neu], ["zwei"])
        with open(p, "a", encoding="utf-8") as f:
            f.write('09-26T10:00:00+02:00","art":"timeout","schwere":"warnung","text":"drei"}\n')
        neu, off3 = er.lies_ab(self.root, "erp", off2)
        self.assertEqual([e["text"] for e in neu], ["drei"])
        self.assertEqual(off3, p.stat().st_size)
        # Datei schrumpft (Rotation) → Offset ans Ende, kein Bestand als „neu"
        p.write_text("", encoding="utf-8")
        self.assertEqual(er.lies_ab(self.root, "erp", off3), ([], 0))

    def test_rotation_tage_und_deckel(self):
        p = er.pfad(self.root, "erp")
        p.parent.mkdir(parents=True)
        alt = (datetime.now().astimezone() - timedelta(days=40)).isoformat(timespec="seconds")
        frisch = datetime.now().astimezone().isoformat(timespec="seconds")
        zeilen = [json.dumps({"zeit": alt, "art": "lauf", "schwere": "info", "text": "alt"})]
        zeilen += [json.dumps({"zeit": frisch, "art": "lauf", "schwere": "info", "text": f"n{i}"})
                   for i in range(10)]
        zeilen.append("kaputt")
        p.write_text("\n".join(zeilen) + "\n", encoding="utf-8")
        self.assertEqual(er.rotiere(self.root, "erp", tage=30, max_zeilen=4), 8)
        rest = er.lies(self.root, "erp", limit=100)["eintraege"]
        self.assertEqual([e["text"] for e in rest], ["n9", "n8", "n7", "n6"])
        # nichts zu tun → 0, Datei unverändert (mtime bleibt)
        vorher = p.stat().st_mtime_ns
        self.assertEqual(er.rotiere(self.root, "erp", tage=30, max_zeilen=100), 0)
        self.assertEqual(p.stat().st_mtime_ns, vorher)
        # rotiere_alle findet die Datei über das Mailbox-Wurzelverzeichnis
        (self.root / "ohne").mkdir()
        self.assertEqual(er.rotiere_alle(self.root, tage=0.0), 0)
        er.schreibe(self.root, "zwei", "lauf", "x")
        p2 = er.pfad(self.root, "zwei")
        p2.write_text(json.dumps({"zeit": alt, "art": "lauf", "schwere": "info", "text": "alt"}) + "\n")
        self.assertEqual(er.rotiere_alle(self.root, tage=30), 1)


class TestLaufEreignisse(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="ereignisse-"))

    def test_voller_abschluss(self):
        lauf = {"sitzung": "neu", "sitzung_grund": "Kontext 164k über Grenze 150k",
                "kontext": 164274, "modell": "claude-opus-5", "session_id": "s1",
                "verweigert": [{"tool": "Bash", "eingabe": "rm -rf /"}, {"tool": "Bash"}],
                "timeout": "Timeout: 900s ohne Lebenszeichen", "fortsetzbar": True}
        verbrauch = {"input_tokens": 1000, "output_tokens": 500,
                     "cache_read_input_tokens": 3500, "total_cost_usd": 0.1234}
        alle = er.lauf_ereignisse(self.root, "erp", "t1", "error",
                                  "2026-09-26T07:49:11+02:00", "2026-09-26T07:55:30+02:00",
                                  verbrauch, lauf)
        self.assertEqual([e["art"] for e in alle], ["lauf", "sitzung", "verweigert", "timeout"])
        lauf_e = alle[0]
        self.assertEqual(lauf_e["schwere"], "fehler")
        self.assertEqual(lauf_e["details"]["dauer"], 379.0)
        self.assertEqual(lauf_e["details"]["tokens"], 5000)
        self.assertEqual(lauf_e["details"]["kosten"], 0.1234)
        self.assertEqual(lauf_e["details"]["kontext"], 164274)
        self.assertIn("6 min", lauf_e["text"])
        self.assertIn("0.12 $", lauf_e["text"])
        self.assertEqual(alle[1]["schwere"], "warnung")            # Kontext-Neustart
        self.assertTrue(alle[1]["details"]["kontext_neustart"])
        self.assertIn("über Grenze", alle[1]["text"])
        self.assertEqual(alle[2]["details"]["anzahl"], 2)
        self.assertEqual(alle[2]["details"]["werkzeuge"], ["Bash"])
        self.assertEqual(alle[2]["details"]["beispiel"], "rm -rf /")
        self.assertIn("fortsetzbar", alle[3]["text"])
        # und alles steht in der Datei
        self.assertEqual(er.lies(self.root, "erp")["gesamt"], 4)

    def test_neue_sitzung_ohne_grenze_ist_info(self):
        alle = er.lauf_ereignisse(self.root, "erp", "t1", "done", None, None, None,
                                  {"sitzung": "neu", "sitzung_grund": "noch keine Sitzung für dieses Verzeichnis"})
        self.assertEqual([e["schwere"] for e in alle], ["info", "info"])
        self.assertIsNone(alle[0]["details"].get("dauer"))
        fort = er.lauf_ereignisse(self.root, "erp", "t2", "done", None, None, None,
                                  {"sitzung": "fortgesetzt", "sitzung_grund": "Task 2 der Sitzung"})
        self.assertEqual(fort[1]["text"], "Sitzung fortgesetzt: Task 2 der Sitzung")

    def test_nachtragen_nur_mit_zahlen(self):
        # Kind schließt ohne lauf ab → EIN Lauf-Eintrag ohne Zahlen
        erst = er.lauf_ereignisse(self.root, "erp", "t1", "done", "a", "b", None, None)
        self.assertEqual([e["art"] for e in erst], ["lauf"])
        # Watcher trägt nur die Sitzung nach → kein zweiter Lauf-Eintrag
        nach = er.lauf_ereignisse(self.root, "erp", "t1", "done", None, None, None,
                                  {"sitzung": "fortgesetzt"}, nachgetragen=True)
        self.assertEqual([e["art"] for e in nach], ["sitzung"])
        # …mit Kosten → Lauf-Eintrag „nachgetragen"
        nach = er.lauf_ereignisse(self.root, "erp", "t1", "done", None, None,
                                  {"total_cost_usd": 0.5}, {"kontext": 42000}, nachgetragen=True)
        self.assertEqual(nach[0]["art"], "lauf")
        self.assertTrue(nach[0]["text"].startswith("Lauf-Daten nachgetragen"))
        self.assertTrue(nach[0]["details"]["nachgetragen"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
