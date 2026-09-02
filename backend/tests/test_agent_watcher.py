"""Tests für scripts/agent_watcher.py — reine Stdlib, laufen ohne pip:

    cd backend && python -m tests.test_agent_watcher

Abgedeckt (T5/T6 der Review-Liste):
  * run_claude gegen ein gefaktes claude-Binary: stream-json-Parsing,
    is_error, permission_denials, Nicht-JSON-Ausgabe, stderr,
    Timeout mit killpg (Kind stirbt mit!), Not-Aus über "kill" (H3),
    BrokenPipeError im Fortschritts-Callback
  * projekt_workdir (Ausbruch, fehlendes Verzeichnis), fehlerserie,
    inbox_tasks (FIFO nach created_at, N1), instanz_lock (H2)
  * baue_claude_cmd: instruction immer hinter "--" (Issue #20 — das
    variadische --allowed-tools verschluckt sie sonst)
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import agent_watcher as aw  # noqa: E402

# Gefaktes claude-Binary: liest das Szenario aus der instruction (letztes
# Argument) und spielt die passenden stream-json-Events ab.
FAKE_BODY = r'''
import json
import subprocess
import sys
import time


def ev(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


# Seit Review P1-3 kommt die instruction über STDIN (nicht mehr als Argument).
szenario = sys.stdin.read().strip()

if szenario.startswith("normal"):
    ev({"type": "assistant", "message": {"content": [
        {"type": "text", "text": "Ich sehe mir das an"},
        {"type": "tool_use", "id": "t1", "name": "Bash",
         "input": {"command": "ls -la"}}]}})
    ev({"type": "result", "result": "FERTIG", "is_error": False,
        "usage": {"input_tokens": 100, "output_tokens": 7,
                  "cache_read_input_tokens": 3},
        "total_cost_usd": 0.05})
elif szenario.startswith("fehler"):
    ev({"type": "result", "result": "ging schief", "is_error": True})
elif szenario.startswith("verweigert"):
    ev({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "t1", "name": "Bash",
         "input": {"command": "rm -rf /"}}]}})
    ev({"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "t1", "is_error": True,
         "content": [{"type": "text", "text": "Permission to use Bash denied"}]},
        {"type": "tool_result", "tool_use_id": "t9", "is_error": True,
         "content": "normaler Toolfehler ohne Berechtigungsthema"}]}})
    ev({"type": "result", "result": "teilweise", "is_error": False,
        "permission_denials": [{"tool_name": "Write"}]})
elif szenario.startswith("roh"):
    sys.stdout.write("kein json hier\n")
    sys.stdout.write("und noch eine zeile\n")
    sys.stdout.flush()
elif szenario.startswith("stderr"):
    sys.stderr.write("etwas ist schiefgelaufen\n")
    sys.stderr.flush()
    ev({"type": "result", "result": "ok", "is_error": False})
    sys.exit(3)
elif szenario.startswith("haengt"):
    # Kindprozess erbt stdout: ohne killpg/taskkill bliebe die Pipe offen und
    # die Lese-Schleife des Watchers hinge für immer.
    pidfile = szenario.split(" ", 1)[1]
    kind = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    with open(pidfile, "w") as f:
        f.write(str(kind.pid))
    ev({"type": "assistant", "message": {"content": [
        {"type": "text", "text": "arbeite lange"}]}})
    time.sleep(120)
else:
    ev({"type": "result", "result": "unbekanntes Szenario", "is_error": True})
'''


def _fake_claude(ordner: Path) -> str:
    pfad = ordner / "claude-fake.py"
    pfad.write_text("#!" + sys.executable + "\n" + FAKE_BODY, encoding="utf-8")
    pfad.chmod(0o755)
    return str(pfad)


class TestRunClaude(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="watcher-test-"))
        self.claude = _fake_claude(self.tmp)
        aw.HART.clear()
        aw.STOP.clear()

    def tearDown(self) -> None:
        aw.HART.clear()
        aw.STOP.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_dry_run_ohne_prozess(self):
        result, log, rc = aw.run_claude("egal", "mach was", self.tmp, True)
        self.assertIn("mach was", result)
        self.assertEqual((log, rc), ("", 0))

    def test_stream_json_result_und_fortschritt(self):
        meldungen: list[str] = []
        result, log, rc = aw.run_claude(self.claude, "normal", self.tmp, False,
                                        meldungen.append)
        self.assertEqual(result, "FERTIG")
        self.assertEqual(rc, 0)
        self.assertEqual(log, "")
        self.assertTrue(any(m.startswith("→ Bash") and "ls -la" in m for m in meldungen),
                        meldungen)
        self.assertTrue(any("Ich sehe mir das an" in m for m in meldungen), meldungen)

    def test_is_error_macht_rc_ungleich_null(self):
        result, _log, rc = aw.run_claude(self.claude, "fehler", self.tmp, False)
        self.assertEqual(result, "ging schief")
        self.assertEqual(rc, 1)  # Exitcode 0, aber is_error im result-Event

    def test_verbrauch_kommt_aus_dem_result_event(self):
        """St.3: usage/total_cost_usd landen im übergebenen dict — die
        Rückgabe bleibt ein 3-Tupel (zehn Entpackstellen unangetastet)."""
        verbrauch: dict = {}
        result, _log, rc = aw.run_claude(self.claude, "normal", self.tmp, False,
                                         verbrauch_out=verbrauch)
        self.assertEqual((result, rc), ("FERTIG", 0))
        self.assertEqual(verbrauch["input_tokens"], 100)
        self.assertEqual(verbrauch["output_tokens"], 7)
        self.assertEqual(verbrauch["cache_read_input_tokens"], 3)
        self.assertAlmostEqual(verbrauch["total_cost_usd"], 0.05)

    def test_permission_denials_landen_im_log(self):
        _result, log, _rc = aw.run_claude(self.claude, "verweigert", self.tmp, False)
        self.assertIn("Berechtigung verweigert", log)
        self.assertIn("Bash", log)      # aus dem tool_result
        self.assertIn("Write", log)     # aus permission_denials
        self.assertNotIn("t9", log)     # normaler Toolfehler zählt nicht

    def test_nicht_json_wird_zum_ergebnis(self):
        result, _log, rc = aw.run_claude(self.claude, "roh", self.tmp, False)
        self.assertEqual(result, "kein json hier\nund noch eine zeile")
        self.assertEqual(rc, 0)

    def test_stderr_und_exitcode(self):
        result, log, rc = aw.run_claude(self.claude, "stderr", self.tmp, False)
        self.assertEqual(result, "ok")
        self.assertIn("schiefgelaufen", log)
        self.assertEqual(rc, 3)

    def test_fehlendes_binary_gibt_klartext(self):
        result, log, rc = aw.run_claude(str(self.tmp / "gibtsnicht"), "normal",
                                        self.tmp, False)
        self.assertEqual((result, rc), ("", 127))
        self.assertIn("nicht ausführbar", log)

    def test_kaputter_fortschritt_bricht_den_lauf_nicht_ab(self):
        """H3: stdout ist die SSH-Leitung — stirbt sie, darf der Lauf nicht
        mitten in der Schleife mit BrokenPipeError herausfallen."""
        def boese(_text: str) -> None:
            raise BrokenPipeError(32, "Broken pipe")

        result, _log, rc = aw.run_claude(self.claude, "normal", self.tmp, False, boese)
        self.assertEqual((result, rc), ("FERTIG", 0))

    @unittest.skipIf(os.name != "posix", "killpg-Verhalten nur auf POSIX prüfbar")
    def test_timeout_killt_die_ganze_prozessgruppe(self):
        pidfile = self.tmp / "kind.pid"
        alt = aw.CLAUDE_TIMEOUT
        aw.CLAUDE_TIMEOUT = 1.0
        try:
            start = time.monotonic()
            _result, log, rc = aw.run_claude(self.claude, f"haengt {pidfile}",
                                             self.tmp, False)
        finally:
            aw.CLAUDE_TIMEOUT = alt
        self.assertLess(time.monotonic() - start, 30, "Timeout hat nicht gegriffen")
        self.assertIn("Timeout", log)
        self.assertNotEqual(rc, 0)
        kind_pid = int(pidfile.read_text(encoding="utf-8"))
        self.assertTrue(self._gestorben(kind_pid),
                        "Kindprozess lebt noch — killpg hat nicht gegriffen")

    @unittest.skipIf(os.name != "posix", "killpg-Verhalten nur auf POSIX prüfbar")
    def test_notaus_bricht_laufenden_lauf_ab(self):
        """H3: 'kill' auf stdin muss den laufenden claude wirklich beenden."""
        pidfile = self.tmp / "kind2.pid"
        ergebnis: dict = {}

        def lauf() -> None:
            ergebnis["wert"] = aw.run_claude(self.claude, f"haengt {pidfile}",
                                             self.tmp, False)

        t = threading.Thread(target=lauf, daemon=True)
        t.start()
        frist = time.monotonic() + 15
        while not pidfile.exists() and time.monotonic() < frist:
            time.sleep(0.05)
        self.assertTrue(pidfile.exists(), "Fake-claude ist nicht angelaufen")
        aw.HART.set()
        aw.abbrechen_laufenden()
        t.join(timeout=15)
        self.assertFalse(t.is_alive(), "run_claude hängt trotz Not-Aus")
        _result, log, rc = ergebnis["wert"]
        self.assertIn("Not-Aus", log)
        self.assertNotEqual(rc, 0)
        self.assertTrue(self._gestorben(int(pidfile.read_text(encoding="utf-8"))))
        self.assertIsNone(aw._LAUFENDER, "Prozess wurde nicht abgemeldet")

    @staticmethod
    def _gestorben(pid: int, frist: float = 5.0) -> bool:
        ende = time.monotonic() + frist
        while time.monotonic() < ende:
            try:
                os.kill(pid, 0)
            except (ProcessLookupError, PermissionError):
                return True
            time.sleep(0.1)
        try:
            os.kill(pid, 9)  # Test soll keine Waisen hinterlassen
        except OSError:
            pass
        return False


class TestReineFunktionen(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="watcher-test-"))
        aw._schnelle_fehler = 0

    def tearDown(self) -> None:
        aw._schnelle_fehler = 0
        shutil.rmtree(self.tmp, ignore_errors=True)

    # --- baue_claude_cmd (Review P1-3: instruction über STDIN) -------------

    def test_instruction_nie_auf_der_kommandozeile(self):
        """P1-3: Auf Windows parst cmd.exe die Argumentzeile des claude.cmd-
        Shims erneut — der Task-Text darf NIE als Argument mitfahren."""
        cmd = aw.baue_claude_cmd("claude",
                                 permission_mode="acceptEdits",
                                 allowed_tools="Bash,mcp__dashboard")
        self.assertNotIn("sag nur OK", " ".join(cmd))
        self.assertEqual(cmd[cmd.index("--allowed-tools") + 1], "Bash,mcp__dashboard")
        self.assertEqual(cmd[cmd.index("--permission-mode") + 1], "acceptEdits")

    def test_ohne_optionen_nur_grundkommando(self):
        cmd = aw.baue_claude_cmd("claude")
        self.assertEqual(cmd[:2], ["claude", "--print"])
        self.assertNotIn("--allowed-tools", cmd)
        self.assertNotIn("--permission-mode", cmd)

    def test_rollen_prompt_als_option(self):
        cmd = aw.baue_claude_cmd("claude",
                                 append_system_prompt="Du bist Reviewer")
        i = cmd.index("--append-system-prompt")
        self.assertEqual(cmd[i + 1], "Du bist Reviewer")

    # --- wirksame_rechte (Rollen, Dashboard-Paket St.1) ---------------------
    # Eine Rolle darf die Agenten-Rechte nur EINSCHRÄNKEN, nie erweitern.

    def test_rolle_senkt_permission_mode(self):
        mode, _ = aw.wirksame_rechte("acceptEdits", None, "default", None)
        self.assertEqual(mode, "default")

    def test_rolle_kann_mode_nie_heben(self):
        mode, _ = aw.wirksame_rechte("default", None, "bypassPermissions", None)
        self.assertEqual(mode, "default")
        # Ohne Agent-Vorgabe gilt claudes Default als Messlatte:
        self.assertIsNone(aw.wirksame_rechte(None, None, "acceptEdits", None)[0])
        self.assertEqual(aw.wirksame_rechte(None, None, "plan", None)[0], "plan")

    def test_unbekannter_rollen_modus_agent_gewinnt(self):
        mode, _ = aw.wirksame_rechte("acceptEdits", None, "superduper", None)
        self.assertEqual(mode, "acceptEdits")

    def test_tools_exakte_schnittmenge(self):
        _, tools = aw.wirksame_rechte(None, "Edit,Write,Bash(git:*)", None,
                                      ["Write", "WebSearch"])
        self.assertEqual(tools, "Write")

    def test_rolle_ohne_tools_laesst_agentliste(self):
        _, tools = aw.wirksame_rechte(None, "Edit,Write", None, None)
        self.assertEqual(tools, "Edit,Write")

    def test_rolle_schaltet_auf_leerem_agenten_nichts_frei(self):
        _, tools = aw.wirksame_rechte(None, None, None, ["Edit", "Write"])
        self.assertIsNone(tools)

    def test_leere_rollenliste_nimmt_alle_tools(self):
        _, tools = aw.wirksame_rechte(None, "Edit,Write", None, [])
        self.assertIsNone(tools)

    # --- projekt_workdir (Issue #19) ---------------------------------------

    def test_ohne_projekt_bleibt_basis(self):
        for wert in (None, "", "   "):
            self.assertEqual(aw.projekt_workdir(self.tmp, wert), (self.tmp, None))

    def test_projekt_unterverzeichnis(self):
        (self.tmp / "repo").mkdir()
        ziel, fehler = aw.projekt_workdir(self.tmp, "repo")
        self.assertIsNone(fehler)
        self.assertEqual(ziel, (self.tmp / "repo").resolve())

    def test_ausbruch_wird_abgelehnt(self):
        for boese in ("../woanders", "/etc", "repo/../../raus"):
            ziel, fehler = aw.projekt_workdir(self.tmp, boese)
            self.assertIsNone(ziel, boese)
            self.assertIn("verlässt das Arbeitsverzeichnis", fehler or "")

    def test_fehlendes_projektverzeichnis(self):
        ziel, fehler = aw.projekt_workdir(self.tmp, "gibtsnicht")
        self.assertIsNone(ziel)
        self.assertIn("fehlt auf dem Agenten-PC", fehler or "")

    # --- fehlerserie (Issue #14) -------------------------------------------

    def test_fehlerserie_erst_nach_schwelle(self):
        for _ in range(aw.FEHLER_SCHWELLE - 1):
            self.assertFalse(aw.fehlerserie("error", 1.0))
        self.assertTrue(aw.fehlerserie("error", 1.0))

    def test_erfolg_und_langsamer_fehler_setzen_zurueck(self):
        aw.fehlerserie("error", 1.0)
        aw.fehlerserie("error", 1.0)
        self.assertFalse(aw.fehlerserie("done", 1.0))       # Erfolg -> zurück
        self.assertFalse(aw.fehlerserie("error", 1.0))
        aw.fehlerserie("error", 1.0)
        # langsamer Fehler = echte Arbeit, kein Umgebungsproblem
        self.assertFalse(aw.fehlerserie("error", aw.SCHNELL_SEKUNDEN + 1))
        self.assertFalse(aw.fehlerserie("error", 1.0))

    # --- inbox_tasks (N1) --------------------------------------------------

    def test_inbox_tasks_fifo_nach_created_at(self):
        inbox = self.tmp / "inbox"
        inbox.mkdir()
        def schreibe(name: str, **felder) -> None:
            (inbox / name).write_text(json.dumps(felder), encoding="utf-8")

        schreibe("zzz.json", kind="task", created_at="2026-08-16T10:00:00+02:00")
        schreibe("aaa.json", kind="task", created_at="2026-08-16T12:00:00+02:00")
        schreibe("mmm.json", kind="task")                      # ohne Zeitstempel
        schreibe("bbb.json", kind="message", created_at="2026-08-16T09:00:00+02:00")
        (inbox / "kaputt.json").write_text("{kein json", encoding="utf-8")

        namen = [p.name for p in aw.inbox_tasks(inbox)]
        self.assertEqual(namen, ["zzz.json", "aaa.json", "mmm.json"])

    # --- instanz_lock (H2) -------------------------------------------------

    def test_instanz_lock_verhindert_zweiten_watcher(self):
        original = aw.lock_pfad
        aw.lock_pfad = lambda agent: self.tmp / "locks" / f"{agent}.lock"
        try:
            erste = aw.instanz_lock("erp")
            self.assertIsNotNone(erste)
            self.assertIsNone(aw.instanz_lock("erp"), "zweiter Lock wurde vergeben")
            # anderer Agent auf demselben PC bleibt möglich
            zweite = aw.instanz_lock("frontend")
            self.assertIsNotNone(zweite)
            inhalt = (self.tmp / "locks" / "erp.lock").read_text(encoding="utf-8")
            self.assertIn(f"pid={os.getpid()}", inhalt)
            erste.close()
            wieder = aw.instanz_lock("erp")   # nach Prozessende wieder frei
            self.assertIsNotNone(wieder)
            wieder.close()
            zweite.close()
        finally:
            aw.lock_pfad = original


if __name__ == "__main__":
    unittest.main(verbosity=2)
