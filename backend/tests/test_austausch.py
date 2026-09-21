"""Austausch-Ordner je Maschine (app/austausch.py) — Stdlib + PyYAML:

    cd backend && python -m tests.test_austausch

Gegen ein SFTP-Doppel (ein lokales Verzeichnis je „Maschine"), asyncssh wird
nicht gebraucht. Abgedeckt:
  * Namen/Pfade: kein Pfad-Schmuggel, Windows-Zeichen, `~`, `C:\\…`, `..`
  * kopiere: Inhalt gleich, landet unter von-<absender>, keine Teil-Datei
    bleibt liegen; Kollision → -2/-3 (nie überschreiben); Deckel; Ordner als
    Quelle; Abbruch mitten im Lesen räumt auf; zwei gleichzeitige Sendungen
    desselben Namens wählen nie dasselbe Ziel
  * uebergib: Ziel ohne Ordner (mit Liste der möglichen Empfänger),
    Token-Maschine, an sich selbst, unbekannt, zu viele Dateien, Teilerfolg,
    Benachrichtigung in der Inbox samt `austausch`-Feld, nichts zugestellt =
    `error` und KEINE Nachricht
  * richte_ein / schalte_aus: Settings + Ordner, Ausschalten löscht nichts
"""
from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

_TMP = Path(tempfile.mkdtemp(prefix="austausch-test-"))
os.environ["WORKSPACE_DIR"] = str(_TMP / "workspace")
os.environ["DATA_CONFIG_DIR"] = str(_TMP / "workspace" / "config")

from app import austausch, config  # noqa: E402
from app.austausch import AustauschError  # noqa: E402
from app.mailbox import Mailbox  # noqa: E402
from tests.sftp_doppel import SftpDoppel  # noqa: E402

AGENTS_YAML = """\
agents:
  - name: erp
    connection: {type: ssh, host: 192.168.1.10, user: u, key_file: /nix}
  - name: deverp
    connection: {type: ssh, host: 192.168.1.11, user: u, key_file: /nix}
  - name: werkbank
    connection: {type: ssh, host: 192.168.1.12, user: u, key_file: /nix}
  - name: notebook
    connection: {type: token}
"""


def lauf(coro):
    return asyncio.run(coro)


class Basis(unittest.TestCase):
    def setUp(self):
        self.ws = Path(os.environ["WORKSPACE_DIR"])
        shutil.rmtree(self.ws, ignore_errors=True)
        cfg = Path(os.environ["DATA_CONFIG_DIR"])
        cfg.mkdir(parents=True)
        (cfg / "agents.yaml").write_text(AGENTS_YAML, encoding="utf-8")
        self.platten = {
            n: SftpDoppel(_TMP / "platten" / self.id() / n)
            for n in ("erp", "deverp", "werkbank")
        }

        @asynccontextmanager
        async def verbinde(name):
            yield self.platten[name]

        self.verbinde = verbinde
        self.mailboxen = self.ws / "mailboxes"

    def lege(self, maschine: str, pfad: str, inhalt: bytes) -> None:
        ziel = self.platten[maschine]._lokal(pfad)
        ziel.parent.mkdir(parents=True, exist_ok=True)
        ziel.write_bytes(inhalt)

    def ordner_an(self, *namen: str) -> None:
        for n in namen:
            lauf(austausch.richte_ein(n, "austausch", self.verbinde))

    def sende(self, von, pfade, an, **kw):
        return lauf(
            austausch.uebergib(
                von, pfade, an, verbinde=self.verbinde, mailbox_root=self.mailboxen, **kw
            )
        )


class NamenTests(unittest.TestCase):
    def test_kein_pfad_schmuggel(self):
        self.assertEqual(austausch.sicherer_name("../../etc/passwd"), "passwd")
        self.assertEqual(austausch.sicherer_name("C:\\Users\\x\\bericht.pdf"), "bericht.pdf")
        self.assertEqual(austausch.sicherer_name("a\\..\\b.txt"), "b.txt")

    def test_windows_zeichen_und_raender(self):
        self.assertEqual(austausch.sicherer_name('plan: "neu"?.txt'), "plan_ _neu__.txt")
        self.assertEqual(austausch.sicherer_name("ende. "), "ende")

    def test_unbrauchbar(self):
        for roh in ("", "..", "/", "dir/", " . "):
            with self.assertRaises(AustauschError, msg=roh):
                austausch.sicherer_name(roh)

    def test_ueberlang_behaelt_endung(self):
        name = austausch.sicherer_name("x" * 400 + ".tar.gz")
        self.assertEqual(len(name), 200)
        self.assertTrue(name.endswith(".gz"))

    def test_quellpfad(self):
        n = austausch.normalisiere_quellpfad
        self.assertEqual(n("~/export/a.csv"), "export/a.csv")
        self.assertEqual(n("/var/log/x.log"), "/var/log/x.log")
        self.assertEqual(n("C:\\Users\\x\\a.txt"), "/C:/Users/x/a.txt")
        self.assertEqual(n("d:/daten/b.bin"), "/d:/daten/b.bin")
        self.assertEqual(n("relativ/c.txt"), "relativ/c.txt")
        for roh in ("", "  ", "~", "a\x00b"):
            with self.assertRaises(AustauschError, msg=repr(roh)):
                n(roh)

    def test_ordner(self):
        p = austausch.pruefe_ordner
        self.assertEqual(p("austausch"), "austausch")
        self.assertEqual(p("~/daten/austausch/"), "daten/austausch")
        self.assertEqual(p("/srv/austausch"), "/srv/austausch")
        self.assertEqual(p("C:\\Austausch"), "/C:/Austausch")
        for roh in ("", "~", ".", "/", "a/../b", "../x", "x\x07"):
            with self.assertRaises(AustauschError, msg=repr(roh)):
                p(roh)

    def test_anzeige(self):
        self.assertEqual(austausch.anzeige_pfad("/C:/Users/x/a"), "C:/Users/x/a")
        self.assertEqual(austausch.anzeige_pfad("/home/x/a"), "/home/x/a")


class KopierTests(Basis):
    def kopiere(self, quelle, max_bytes=austausch.MAX_BYTES):
        ziel = "/home/austausch/von-erp"
        self.platten["deverp"]._lokal(ziel).mkdir(parents=True, exist_ok=True)
        return lauf(
            austausch.kopiere(self.platten["erp"], self.platten["deverp"], quelle, ziel, max_bytes)
        )

    def zielordner(self) -> list[str]:
        return sorted(p.name for p in self.platten["deverp"]._lokal("/home/austausch/von-erp").iterdir())

    def test_inhalt_und_keine_teildatei(self):
        inhalt = os.urandom(3 * austausch.BLOCK + 17)  # mehrere Blöcke, binär
        self.lege("erp", "export/daten.bin", inhalt)
        r = self.kopiere("export/daten.bin")
        self.assertEqual(r["name"], "daten.bin")
        self.assertEqual(r["bytes"], len(inhalt))
        self.assertEqual(r["pfad"], "/home/austausch/von-erp/daten.bin")
        self.assertEqual(self.platten["deverp"]._lokal(r["pfad"]).read_bytes(), inhalt)
        self.assertEqual(self.zielordner(), ["daten.bin"])

    def test_nie_ueberschreiben(self):
        self.lege("erp", "a.txt", b"eins")
        self.assertEqual(self.kopiere("a.txt")["name"], "a.txt")
        self.lege("erp", "a.txt", b"zwei")
        self.assertEqual(self.kopiere("a.txt")["name"], "a-2.txt")
        self.assertEqual(self.kopiere("a.txt")["name"], "a-3.txt")
        ziel = self.platten["deverp"]._lokal("/home/austausch/von-erp")
        self.assertEqual((ziel / "a.txt").read_bytes(), b"eins")
        self.assertEqual((ziel / "a-2.txt").read_bytes(), b"zwei")

    def test_deckel(self):
        self.lege("erp", "gross.bin", b"x" * 2048)
        with self.assertRaises(AustauschError) as ctx:
            self.kopiere("gross.bin", max_bytes=1024)
        self.assertIn("zu groß", str(ctx.exception))
        self.assertEqual(self.zielordner(), [])

    def test_ordner_als_quelle(self):
        self.platten["erp"]._lokal("projekt").mkdir(parents=True)
        with self.assertRaises(AustauschError) as ctx:
            self.kopiere("projekt")
        self.assertIn("keine Datei", str(ctx.exception))

    def test_fehlende_quelle(self):
        with self.assertRaises(AustauschError) as ctx:
            self.kopiere("gibt/es/nicht.txt")
        self.assertIn("nicht lesbar", str(ctx.exception))

    def test_abbruch_raeumt_teildatei_weg(self):
        self.lege("erp", "lang.bin", b"y" * (2 * austausch.BLOCK))
        self.platten["erp"].lese_stoerung = True
        with self.assertRaises(AustauschError) as ctx:
            self.kopiere("lang.bin")
        self.assertIn("fehlgeschlagen", str(ctx.exception))
        self.assertEqual(self.zielordner(), [])  # weder Ziel noch .teil

    def test_gleichzeitig_selber_name(self):
        self.lege("erp", "bericht.pdf", b"A" * 5000)
        ziel = "/home/austausch/von-erp"
        self.platten["deverp"]._lokal(ziel).mkdir(parents=True)

        async def beide():
            return await asyncio.gather(
                *[
                    austausch.kopiere(self.platten["erp"], self.platten["deverp"], "bericht.pdf", ziel)
                    for _ in range(4)
                ]
            )

        namen = sorted(r["name"] for r in lauf(beide()))
        self.assertEqual(namen, ["bericht-2.pdf", "bericht-3.pdf", "bericht-4.pdf", "bericht.pdf"])
        self.assertEqual(self.zielordner(), namen)


class UebergabeTests(Basis):
    def test_ziel_ohne_ordner_nennt_die_moeglichen(self):
        self.ordner_an("werkbank")
        self.lege("erp", "a.txt", b"x")
        with self.assertRaises(AustauschError) as ctx:
            self.sende("erp", ["a.txt"], "deverp")
        text = str(ctx.exception)
        self.assertIn("keinen Austausch-Ordner", text)
        self.assertIn("werkbank", text)

    def test_token_maschine(self):
        self.ordner_an("deverp")
        for von, an in (("notebook", "deverp"), ("erp", "notebook")):
            with self.assertRaises(AustauschError) as ctx:
                self.sende(von, ["a.txt"], an)
            self.assertIn("Token-Maschine", str(ctx.exception))

    def test_token_maschine_laesst_sich_nicht_einschalten(self):
        with self.assertRaises(AustauschError):
            lauf(austausch.richte_ein("notebook", "austausch", self.verbinde))

    def test_an_sich_selbst_unbekannt_zu_viele_leer(self):
        self.ordner_an("erp", "deverp")
        faelle = [
            (("erp", ["a"], "erp"), "dieselbe Maschine"),
            (("erp", ["a"], "gibtsnicht"), "unbekannte Maschine"),
            (("../x", ["a"], "deverp"), "ungültiger Maschinenname"),
            (("erp", ["a"] * 21, "deverp"), "höchstens 20"),
            (("erp", [], "deverp"), "keine Datei"),
            (("erp", ["", "  "], "deverp"), "keine Datei"),
        ]
        for args, erwartet in faelle:
            with self.assertRaises(AustauschError, msg=erwartet) as ctx:
                self.sende(*args)
            self.assertIn(erwartet, str(ctx.exception))

    def test_zustellung_meldung_und_teilerfolg(self):
        self.ordner_an("deverp")
        self.lege("erp", "export/kunden.csv", b"a;b\n1;2\n")
        self.lege("erp", "/var/tmp/plan.pdf", b"%PDF-")
        r = self.sende(
            "erp",
            ["~/export/kunden.csv", "/var/tmp/plan.pdf", "fehlt.txt"],
            "deverp",
            melder="erp",
            nachricht="  bitte  \n einspielen ",
        )
        self.assertNotIn("error", r)
        self.assertTrue(r["gemeldet"])
        self.assertEqual([z["name"] for z in r["zugestellt"]], ["kunden.csv", "plan.pdf"])
        self.assertEqual([f["pfad"] for f in r["fehler"]], ["fehlt.txt"])
        self.assertEqual(
            self.platten["deverp"]._lokal("austausch/von-erp/kunden.csv").read_bytes(),
            b"a;b\n1;2\n",
        )
        post = Mailbox(self.mailboxen, "deverp").read_inbox()
        self.assertEqual(len(post), 1)
        env = post[0]
        self.assertEqual((env["kind"], env["sender"], env["to"]), ("message", "erp", "deverp"))
        self.assertIn("/home/austausch/von-erp/kunden.csv", env["text"])
        self.assertIn("Hinweis des Absenders: bitte einspielen", env["text"])
        self.assertNotIn("fehlt.txt", env["text"])
        self.assertEqual(env["austausch"]["von"], "erp")
        self.assertEqual(len(env["austausch"]["dateien"]), 2)

    def test_melder_default_ist_der_mensch(self):
        self.ordner_an("deverp")
        self.lege("erp", "a.txt", b"x")
        self.sende("erp", "a.txt", "deverp")  # einzelner Pfad als str geht auch
        env = Mailbox(self.mailboxen, "deverp").read_inbox()[0]
        self.assertEqual(env["sender"], "orchestrator")
        self.assertIn("von erp", env["text"])

    def test_nichts_zugestellt_kein_laerm(self):
        self.ordner_an("deverp")
        r = self.sende("erp", ["fehlt.txt"], "deverp")
        self.assertIn("keine Datei zugestellt", r["error"])
        self.assertFalse(r["gemeldet"])
        self.assertFalse((self.mailboxen / "deverp").exists())  # keine Geister-Mailbox

    def test_meldungstext_windows(self):
        text = austausch.meldungstext(
            "erp", [{"pfad": "/C:/Users/x/austausch/von-erp/a.bin", "bytes": 3 * 1024 * 1024}], None
        )
        self.assertIn("- C:/Users/x/austausch/von-erp/a.bin (3,0 MB)", text)
        self.assertNotIn("Hinweis", text)


class SchalterTests(Basis):
    def test_einschalten_legt_an_und_merkt_sich(self):
        r = lauf(austausch.richte_ein("deverp", "~/daten/austausch", self.verbinde))
        self.assertEqual(r["ordner"], "daten/austausch")
        self.assertEqual(r["pfad"], "/home/daten/austausch")
        self.assertTrue(self.platten["deverp"]._lokal("daten/austausch").is_dir())
        self.assertEqual(config.load_settings()["austausch"]["deverp"]["ordner"], "daten/austausch")
        self.assertIn("deverp", austausch.ziele())
        zeile = next(z for z in austausch.uebersicht() if z["name"] == "deverp")
        self.assertTrue(zeile["aktiv"])
        token = next(z for z in austausch.uebersicht() if z["name"] == "notebook")
        self.assertFalse(token["moeglich"])

    def test_leerer_pfad_ist_der_standard(self):
        r = lauf(austausch.richte_ein("erp", "", self.verbinde))
        self.assertEqual(r["ordner"], "austausch")

    def test_ausschalten_loescht_nichts(self):
        self.ordner_an("deverp", "werkbank")
        self.lege("deverp", "austausch/von-erp/alt.txt", b"bleibt")
        austausch.schalte_aus("deverp")
        self.assertNotIn("deverp", austausch.ziele())
        self.assertIn("werkbank", austausch.ziele())
        self.assertEqual(
            self.platten["deverp"]._lokal("austausch/von-erp/alt.txt").read_bytes(), b"bleibt"
        )

    def test_geloeschte_verbindung_zaehlt_nicht(self):
        self.ordner_an("deverp")
        cfg = Path(os.environ["DATA_CONFIG_DIR"]) / "agents.yaml"
        cfg.write_text(AGENTS_YAML.replace("name: deverp", "name: andere"), encoding="utf-8")
        self.assertNotIn("deverp", austausch.ziele())

    def test_settings_schluessel_ist_erlaubt(self):
        # sonst verwirft save_settings den Eintrag still (ALLOWED_KEYS)
        self.assertIn("austausch", config.ALLOWED_KEYS)


if __name__ == "__main__":
    try:
        unittest.main(verbosity=1)
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
