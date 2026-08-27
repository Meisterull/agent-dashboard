"""Token-Kanäle über HTTPS und angereicherte Push-Meldungen (Issues #30/#32).

Beides ist sicherheits- bzw. verhaltensrelevant und lässt sich rein prüfen:
die Token-Anmeldung ohne laufenden Server, die Meldungen ohne Push-Dienst.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_TMP = tempfile.mkdtemp(prefix="dashboard-token-")
os.environ.setdefault("WORKSPACE_DIR", _TMP)

from app import mcp_token  # noqa: E402
from app.mcp_scope import TOKEN_GRUNDTOOLS, compute_scopes  # noqa: E402
from app.events import neue_meldungen  # noqa: E402


class TestTokenKanaele(unittest.TestCase):
    def test_token_agent_bekommt_kanal_und_grundtools(self):
        scopes, warnungen = compute_scopes(
            [{"name": "NB1", "connection": {"type": "token", "token_file": "/x"}}]
        )
        self.assertIn("NB1", scopes)
        self.assertEqual(scopes["NB1"]["tools"], TOKEN_GRUNDTOOLS)
        self.assertEqual(warnungen, [])

    def test_eigene_allowlist_schlaegt_die_grundmenge(self):
        scopes, _ = compute_scopes(
            [{
                "name": "NB1",
                "tools": ["inbox"],
                "connection": {"type": "token", "token_file": "/x"},
            }]
        )
        self.assertEqual(scopes["NB1"]["tools"], ["inbox"])

    def test_ssh_agent_bleibt_ohne_allowlist_unbeschraenkt(self):
        # Ein SSH-Schlüssel liegt auf einem bekannten Host — dort gilt weiter
        # "alles erlaubt, solange nichts eingeschränkt wurde".
        scopes, _ = compute_scopes(
            [{"name": "erp", "connection": {"type": "ssh", "host": "h"}}]
        )
        self.assertIsNone(scopes["erp"].get("tools"))

    def test_agent_ohne_kanaltyp_bekommt_keinen_port(self):
        scopes, _ = compute_scopes([{"name": "x", "connection": {"type": "none"}}])
        self.assertEqual(scopes, {})


class TestTokenPruefung(unittest.TestCase):
    def setUp(self):
        self.ordner = tempfile.mkdtemp(prefix="token-")
        self.datei = Path(self.ordner) / "nb.token"
        self.gueltig = "z" * 40
        self.datei.write_text(self.gueltig + "\n", encoding="utf-8")
        mcp_token._FEHLVERSUCHE.clear()
        self._echte_agenten = mcp_token.load_agents_full
        mcp_token.load_agents_full = lambda: [
            {"name": "NB1", "connection": {"type": "token", "token_file": str(self.datei)}},
            {"name": "kurz", "connection": {"type": "token", "token_file": str(self.kurz())}},
            {"name": "erp", "connection": {"type": "ssh", "host": "h"}},
        ]

    def kurz(self):
        p = Path(self.ordner) / "kurz.token"
        p.write_text("zu-kurz", encoding="utf-8")
        return p

    def tearDown(self):
        mcp_token.load_agents_full = self._echte_agenten

    def test_richtiger_token_wird_angenommen(self):
        mcp_token.pruefe("NB1", f"Bearer {self.gueltig}")  # wirft nicht

    def test_falscher_token_wird_abgelehnt(self):
        with self.assertRaises(mcp_token.TokenFehler):
            mcp_token.pruefe("NB1", "Bearer " + "y" * 40)

    def test_ohne_kopfzeile_abgelehnt(self):
        with self.assertRaises(mcp_token.TokenFehler):
            mcp_token.pruefe("NB1", None)

    def test_ssh_agent_hat_keinen_token_zugang(self):
        # Sonst wäre ein SSH-Agent über HTTPS ohne jede Prüfung erreichbar.
        with self.assertRaises(mcp_token.TokenFehler):
            mcp_token.pruefe("erp", f"Bearer {self.gueltig}")

    def test_zu_kurzer_token_wird_nicht_akzeptiert(self):
        # Auch wenn er stimmt: geraten ist geraten.
        with self.assertRaises(mcp_token.TokenFehler):
            mcp_token.pruefe("kurz", "Bearer zu-kurz")

    def test_sperre_nach_vielen_fehlversuchen(self):
        for _ in range(mcp_token._SPERRE_AB):
            with self.assertRaises(mcp_token.TokenFehler):
                mcp_token.pruefe("NB1", "Bearer falsch")
        # Jetzt scheitert sogar der richtige Token — Durchprobieren lohnt nicht.
        with self.assertRaises(mcp_token.TokenFehler):
            mcp_token.pruefe("NB1", f"Bearer {self.gueltig}")


class TestMeldungen(unittest.TestCase):
    def leer(self):
        return {"fragen": {}, "antworten": {}}

    def test_frage_traegt_alles_zum_beantworten(self):
        neu = {
            "fragen": {
                "q1": {"sender": "erp", "text": "löschen?", "options": ["ja", "nein"]}
            },
            "antworten": {},
        }
        (m,) = neue_meldungen(self.leer(), neu)
        self.assertEqual(m["art"], "frage")
        self.assertEqual(m["qid"], "q1")
        self.assertEqual(m["optionen"], ["ja", "nein"])
        self.assertEqual(m["offen"], 1)
        self.assertIn("frage=q1", m["url"])

    def test_frage_ohne_optionen_bietet_keine_knoepfe(self):
        neu = {"fragen": {"q1": {"sender": "erp", "text": "was nun?"}}, "antworten": {}}
        (m,) = neue_meldungen(self.leer(), neu)
        self.assertEqual(m["optionen"], [])

    def test_bestand_meldet_nicht_erneut(self):
        stand = {
            "fragen": {"q1": {"sender": "erp", "text": "x", "options": []}},
            "antworten": {},
        }
        self.assertEqual(neue_meldungen(stand, stand), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
