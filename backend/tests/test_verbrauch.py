"""Verbrauchszähler (Dashboard-Paket St.3) — reine Stdlib:

    cd backend && python -m tests.test_verbrauch

Abgedeckt: Aggregation (heute, rollierendes 5-h-Fenster, 7-Tage-Liste),
tolerantes Parsen (fehlendes responded_at/verbrauch), Schwellen-Flag und der
Datei-Lader (lade/ist_ueber_schwelle) über die Outbox.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

from app import verbrauch  # noqa: E402

JETZT = datetime(2026, 9, 2, 12, 0).astimezone()


def r(vor_stunden: float, tokens_out: int = 10, kosten: float = 0.01,
      **extra) -> dict:
    wann = JETZT - timedelta(hours=vor_stunden)
    return {
        "responded_at": wann.isoformat(timespec="seconds"),
        "verbrauch": {"input_tokens": 100, "output_tokens": tokens_out,
                      "total_cost_usd": kosten},
        **extra,
    }


class AggregationTests(unittest.TestCase):
    def test_fenster_heute_und_tage(self):
        agg = verbrauch.aggregiere(
            [
                r(1),            # im 5-h-Fenster UND heute
                r(6),            # heute, aber außerhalb des Fensters
                r(30),           # gestern
                r(24 * 8),       # älter als die 7-Tage-Anzeige
            ],
            jetzt=JETZT,
        )
        self.assertEqual(agg["fenster5h"]["tasks"], 1)
        self.assertEqual(agg["fenster5h"]["tokens"], 110)
        self.assertEqual(agg["heute"]["tasks"], 2)
        self.assertEqual(agg["heute"]["tokens"], 220)
        self.assertAlmostEqual(agg["heute"]["kosten"], 0.02)
        self.assertEqual(len(agg["tage"]), 7)
        self.assertEqual(agg["tage"][0]["datum"], JETZT.date().isoformat())
        self.assertEqual(agg["tage"][0]["tasks"], 2)
        self.assertEqual(agg["tage"][1]["tasks"], 1)  # gestern
        # der 8 Tage alte Eintrag taucht nirgends auf
        self.assertEqual(sum(t["tasks"] for t in agg["tage"]), 3)

    def test_tolerant_gegen_kaputte_eintraege(self):
        agg = verbrauch.aggregiere(
            [
                {"kein": "zeitstempel"},
                {"responded_at": "unlesbar"},
                {"responded_at": JETZT.isoformat(), },  # ohne verbrauch: zählt als Task
                "kein dict",
            ],
            jetzt=JETZT,
        )
        self.assertEqual(agg["heute"]["tasks"], 1)
        self.assertEqual(agg["heute"]["tokens"], 0)

    def test_schwelle(self):
        agg = verbrauch.aggregiere([r(1, tokens_out=900)], jetzt=JETZT,
                                   schwelle=1000)
        self.assertTrue(agg["ueber_schwelle"])  # 100 + 900 >= 1000
        agg = verbrauch.aggregiere([r(1, tokens_out=800)], jetzt=JETZT,
                                   schwelle=1000)
        self.assertFalse(agg["ueber_schwelle"])
        agg = verbrauch.aggregiere([r(1, tokens_out=900)], jetzt=JETZT)
        self.assertFalse(agg["ueber_schwelle"])  # 0 = aus


class LaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="verbrauch-test-"))
        self._root = verbrauch.MAILBOX_ROOT
        verbrauch.MAILBOX_ROOT = self.tmp

    def tearDown(self) -> None:
        verbrauch.MAILBOX_ROOT = self._root
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_lade_und_schwelle_aus_der_outbox(self):
        outbox = self.tmp / "a" / "outbox"
        outbox.mkdir(parents=True)
        jetzt = datetime.now().astimezone()
        (outbox / "task-1-response.json").write_text(json.dumps({
            "responded_at": jetzt.isoformat(timespec="seconds"),
            "verbrauch": {"output_tokens": 2000},
        }), encoding="utf-8")
        (outbox / "kaputt-response.json").write_text("{", encoding="utf-8")
        agg = verbrauch.lade("a", schwelle=1000)
        self.assertEqual(agg["fenster5h"]["tokens"], 2000)
        self.assertTrue(verbrauch.ist_ueber_schwelle("a", 1000))
        self.assertFalse(verbrauch.ist_ueber_schwelle("a", 0))
        self.assertFalse(verbrauch.ist_ueber_schwelle("gibtsnicht", 1000))


if __name__ == "__main__":
    unittest.main()
