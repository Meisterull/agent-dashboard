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
  * Sitzung fortsetzen (21.09.2026): Sitzungsbuch als reine Funktionen
    (Grenzen, geparkter Task, kaputtes Buch) und run_claude_sitzung gegen das
    gefakte Binary (--resume, Kurz-Hinweis, Rückfall auf frisch ohne
    Doppellauf, Vergessen nach Fehler, Rolle, abgeschaltet)
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
elif "sitzung" in szenario:
    # Sitzungs-Szenarien (--resume): jeden Aufruf protokollieren, damit der
    # Test Kommandozeile UND Prompt je Lauf prüfen kann.
    argv = sys.argv[1:]
    alt = argv[argv.index("--resume") + 1] if "--resume" in argv else None
    with open("aufrufe.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"resume": alt, "prompt": szenario}) + "\n")
    if alt and alt.startswith("verloren"):
        # Wie das echte Binary (nachgemessen mit 2.1.270): result-Fehler, der
        # die UNBEKANNTE ID zurückechot, keine Assistant-Nachricht, rc 1.
        ev({"type": "result", "subtype": "error_during_execution",
            "is_error": True, "num_turns": 0, "session_id": alt})
        sys.stderr.write("No conversation found with session ID: " + alt + "\n")
        sys.exit(1)
    sid = alt or "sitzung-neu-0001"
    ev({"type": "system", "subtype": "init", "session_id": sid})
    ev({"type": "assistant", "parent_tool_use_id": "sub1",
        "message": {"content": [], "usage": {"input_tokens": 999999}}})
    ev({"type": "assistant", "message": {
        "content": [{"type": "text", "text": "arbeite"}],
        "usage": {"input_tokens": 10, "cache_read_input_tokens": 40000,
                  "cache_creation_input_tokens": 2000, "output_tokens": 90}}})
    if "haengen" in szenario:
        time.sleep(120)  # Timeout-/Leerlauf-Tests: der Wächter muss zuschlagen
    if "scheitert" in szenario:
        ev({"type": "result", "result": "kaputt", "is_error": True, "session_id": sid})
    else:
        ev({"type": "result", "result": "OK " + sid, "is_error": False,
            "session_id": sid, "usage": {"input_tokens": 10, "output_tokens": 90}})
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


class TestSitzungsbuch(unittest.TestCase):
    """Reine Funktionen rund ums Fortsetzen der claude-Sitzung (21.09.2026)."""

    def buch(self, **sitzung):
        basis = {"session_id": "sitz-0001-aaaa", "zuletzt": 1000.0,
                 "kontext": 60_000, "tasks": 2}
        basis.update(sitzung)
        return {"sitzungen": {"/w|": basis}, "tasks": {}}

    def test_frische_sitzung_wird_fortgesetzt(self):
        sid, grund = aw.sitzung_waehlen(self.buch(), "/w|", "t9", 1000.0 + 600)
        self.assertEqual(sid, "sitz-0001-aaaa")
        self.assertIn("Task 3", grund)

    def test_grenzen_pause_kontext_tasks(self):
        for buch, jetzt in (
            (self.buch(), 1000.0 + aw.RESUME_MAX_PAUSE + 1),
            # Kontext über der relativen Grenze (85 % des 1M-Fensters)
            (self.buch(kontext=900_000, modell="claude-opus-5"), 1001.0),
            (self.buch(tasks=aw.RESUME_MAX_TASKS), 1001.0),
        ):
            sid, grund = aw.sitzung_waehlen(buch, "/w|", "t9", jetzt)
            self.assertIsNone(sid, grund)
            self.assertTrue(grund)

    def test_kontextgrenze_relativ_zum_modellfenster(self):
        """Issue #44: 150k waren auf einer Bash-lastigen Box nach EINEM Task
        erreicht — bei einem 1M-Modell greift das Gedächtnis jetzt weiter."""
        # 164k (der Fall aus dem Echtbetrieb) bei einem 1M-Modell: fortsetzen
        sid, grund = aw.sitzung_waehlen(
            self.buch(kontext=164_274, modell="claude-opus-5"), "/w|", "t9", 1001.0)
        self.assertEqual(sid, "sitz-0001-aaaa", grund)
        # Haiku 4.5 hat 200k → 85 % = 170k: 164k geht noch, 175k nicht mehr
        sid, _ = aw.sitzung_waehlen(
            self.buch(kontext=164_274, modell="claude-haiku-4-5-20251001"), "/w|", "t9", 1001.0)
        self.assertEqual(sid, "sitz-0001-aaaa")
        sid, grund = aw.sitzung_waehlen(
            self.buch(kontext=175_000, modell="claude-haiku-4-5-20251001"), "/w|", "t9", 1001.0)
        self.assertIsNone(sid)
        self.assertIn("170k", grund)
        self.assertIn("85 %", grund)
        # unbekanntes Modell (altes Buch ohne `modell`): keine Kontextgrenze —
        # die Kompaktierung übernimmt Claude Code selbst
        sid, _ = aw.sitzung_waehlen(self.buch(kontext=5_000_000), "/w|", "t9", 1001.0)
        self.assertEqual(sid, "sitz-0001-aaaa")
        # ein expliziter Wert bleibt eine harte Grenze
        sid, grund = aw.sitzung_waehlen(
            self.buch(kontext=164_274, modell="claude-opus-5"), "/w|", "t9", 1001.0,
            max_kontext=150_000)
        self.assertIsNone(sid)
        self.assertIn("150k", grund)
        self.assertNotIn("%", grund)

    def test_kontext_fenster_je_modell(self):
        f = aw.kontext_fenster
        self.assertEqual(f("claude-opus-5-5"), 1_000_000)
        self.assertEqual(f("claude-sonnet-5"), 1_000_000)
        self.assertEqual(f("claude-fable-5-1"), 1_000_000)
        self.assertEqual(f("claude-opus-4-6"), 1_000_000)
        self.assertEqual(f("claude-opus-4-8-20260401"), 1_000_000)
        self.assertEqual(f("claude-opus-4-1"), 200_000)
        self.assertEqual(f("claude-sonnet-4-5[1m]"), 1_000_000)
        self.assertEqual(f("claude-sonnet-4-5"), 200_000)
        self.assertEqual(f("claude-haiku-4-5-20251001"), 200_000)
        self.assertIsNone(f(None))
        self.assertIsNone(f("gpt-irgendwas"))
        self.assertEqual(aw.kontext_grenze(0, "claude-opus-5"), 850_000)
        self.assertIsNone(aw.kontext_grenze(0, None))
        self.assertEqual(aw.kontext_grenze(123, None), 123)

    def test_sitzung_merken_behaelt_modell(self):
        buch = {"sitzungen": {}, "tasks": {}}
        aw.sitzung_merken(buch, "/w|", "t1", "sitz-1", 1000, 1.0, "claude-opus-5")
        self.assertEqual(buch["sitzungen"]["/w|"]["modell"], "claude-opus-5")
        # Folgelauf ohne init-Modell (z.B. altes Binary): das Modell bleibt
        aw.sitzung_merken(buch, "/w|", "t2", "sitz-1", 2000, 2.0, None)
        self.assertEqual(buch["sitzungen"]["/w|"]["modell"], "claude-opus-5")
        self.assertEqual(buch["sitzungen"]["/w|"]["tasks"], 2)

    def test_lauf_mitschreiben_nimmt_modell_aus_init(self):
        lauf = {}
        aw.lauf_mitschreiben(lauf, {"type": "system", "subtype": "init",
                                    "session_id": "s1", "model": "claude-opus-5"})
        self.assertEqual(lauf["modell"], "claude-opus-5")
        self.assertEqual(lauf["session_id"], "s1")

    def test_mcp_hint_nennt_eigenen_task(self):
        """Issue #43: das Kind soll claim/complete für SEINEN Task nicht rufen."""
        for text in (aw.mcp_hint("erp", "task-2f2fc6bc"),
                     aw.mcp_hint_kurz("erp", "task-2f2fc6bc")):
            self.assertIn("task-2f2fc6bc", text)
            self.assertIn("NICHT", text)
            self.assertIn("complete_task", text)
        # ohne task_id bleibt der Hinweis weg (rückwärtskompatibel)
        self.assertNotIn("complete_task", aw.mcp_hint("erp"))

    def test_anderes_verzeichnis_oder_rolle_beginnt_neu(self):
        self.assertIsNone(aw.sitzung_waehlen(self.buch(), "/anders|", "t9", 1001.0)[0])
        self.assertIsNone(aw.sitzung_waehlen(self.buch(), "/w|pruefer", "t9", 1001.0)[0])
        self.assertNotEqual(aw.sitzungs_schluessel(Path("/w"), None),
                            aw.sitzungs_schluessel(Path("/w"), "pruefer"))

    def test_geparkter_task_findet_seine_sitzung_trotz_grenzen(self):
        """Nach einer Rückfrage (Issue #17) läuft dieselbe task_id erneut —
        dort zählt das Gedächtnis mehr als Pause und Kontextgrenze."""
        buch = self.buch(kontext=5_000_000, modell="claude-opus-5")
        buch["tasks"]["t-park"] = {"session_id": "sitz-park-0007", "zeit": 1000.0}
        sid, _ = aw.sitzung_waehlen(buch, "/w|", "t-park", 1000.0 + 3 * 86400)
        self.assertEqual(sid, "sitz-park-0007")
        sid, _ = aw.sitzung_waehlen(buch, "/w|", "t-park",
                                    1000.0 + aw.RESUME_TASK_MERKDAUER + 1)
        self.assertIsNone(sid)

    def test_kaputtes_buch_wirft_nicht(self):
        buch = {"sitzungen": {"/w|": {"session_id": "sitz-0001-aaaa",
                                      "zuletzt": "gestern", "kontext": None,
                                      "tasks": float("inf")}},
                "tasks": {"t1": "quatsch"}}
        # echte Uhrzeit: "gestern" zählt als 0 → Pause riesig → neue Sitzung
        sid, _ = aw.sitzung_waehlen(buch, "/w|", "t1", time.time())
        self.assertIsNone(sid)

    def test_merken_zaehlt_hoch_und_raeumt_auf(self):
        buch = self.buch()
        buch["tasks"]["uralt"] = {"session_id": "x", "zeit": 0.0}
        jetzt = aw.RESUME_TASK_MERKDAUER + 5000.0
        buch["sitzungen"]["/w|"]["zuletzt"] = jetzt - 10
        aw.sitzung_merken(buch, "/w|", "t3", "sitz-0001-aaaa", 70_000, jetzt)
        self.assertEqual(buch["sitzungen"]["/w|"]["tasks"], 3)
        self.assertEqual(buch["sitzungen"]["/w|"]["kontext"], 70_000)
        self.assertIn("t3", buch["tasks"])
        self.assertNotIn("uralt", buch["tasks"])
        # andere Sitzungs-ID = Zähler beginnt neu
        aw.sitzung_merken(buch, "/w|", "t4", "sitz-0002-bbbb", 5, jetzt)
        self.assertEqual(buch["sitzungen"]["/w|"]["tasks"], 1)

    def test_mitschreiben_ignoriert_fehler_id_und_subagenten(self):
        lauf: dict = {}
        aw.lauf_mitschreiben(lauf, {"type": "result", "is_error": True,
                                    "session_id": "verloren-0001"})
        self.assertEqual(lauf, {})
        aw.lauf_mitschreiben(lauf, {"type": "assistant", "parent_tool_use_id": "s",
                                    "message": {"usage": {"input_tokens": 9}}})
        self.assertEqual(lauf, {})
        aw.lauf_mitschreiben(lauf, {"type": "assistant", "message": {
            "usage": {"input_tokens": 5, "cache_read_input_tokens": 100,
                      "output_tokens": 7}}})
        self.assertEqual(lauf, {"arbeit": True, "kontext": 112})

    def test_resume_id_nur_geprueft_auf_die_kommandozeile(self):
        cmd = aw.baue_claude_cmd("claude", resume_id="9a2d20b8-b893-43c0-8867-a80963446b17")
        self.assertEqual(cmd[cmd.index("--resume") + 1],
                         "9a2d20b8-b893-43c0-8867-a80963446b17")
        for boese in ("x & calc.exe", "--dangerously-skip-permissions", "kurz", ""):
            self.assertNotIn("--resume", aw.baue_claude_cmd("claude", resume_id=boese))


class TestVerweigerungUndFristen(unittest.TestCase):
    def test_pauschal_freigegeben_nennt_befehl_statt_falschem_rat(self):
        """Issue #39: `Bash` WAR freigegeben — der alte Hinweis „allowed_tools
        setzen" führte in die Irre, und der abgelehnte Befehl fehlte ganz."""
        text = aw.verweigerungs_log(
            [{"tool": "Bash", "eingabe": "cat /etc/shadow"}], "Bash,mcp__dashboard")
        self.assertIn("Berechtigung verweigert: Bash: cat /etc/shadow", text)
        self.assertIn("trotzdem abgelehnt", text)
        self.assertNotIn("nicht freigegeben", text)

    def test_nicht_oder_nur_teilweise_freigegeben(self):
        text = aw.verweigerungs_log([{"tool": "Write", "eingabe": "/x"}], "Bash")
        self.assertIn("Write ist nicht freigegeben", text)
        text = aw.verweigerungs_log([{"tool": "Bash", "eingabe": "rm -rf x"}],
                                    "Bash(git:*),Edit")
        self.assertIn("nur eingeschränkt freigegeben (Bash(git:*))", text)

    def test_run_claude_liefert_befehl_der_verweigerung(self):
        tmp = Path(tempfile.mkdtemp(prefix="watcher-verw-"))
        try:
            lauf: dict = {}
            _, log, _ = aw.run_claude(_fake_claude(tmp), "verweigert", tmp, False,
                                      allowed_tools="Bash", lauf_out=lauf)
            self.assertIn("Berechtigung verweigert: Bash: rm -rf /", log)
            self.assertIn({"tool": "Bash", "eingabe": "rm -rf /"}, lauf["verweigert"])
            self.assertIn("Write ist nicht freigegeben", log)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_task_timeout_kann_nur_senken(self):
        self.assertEqual(aw.wirksamer_timeout(3600, None), 3600)
        self.assertEqual(aw.wirksamer_timeout(3600, 600), 600)
        self.assertEqual(aw.wirksamer_timeout(3600, 99999), 3600)
        self.assertEqual(aw.wirksamer_timeout(None, "quatsch"), aw.CLAUDE_TIMEOUT)


class TestLaufMitSitzung(unittest.TestCase):
    """run_claude_sitzung gegen das gefakte Binary: Fortsetzen, Rückfall auf
    frisch, Vergessen nach Fehler, Kurz-Hinweis."""

    AN = {"an": True, "max_pause": aw.RESUME_MAX_PAUSE,
          "max_kontext": aw.RESUME_MAX_KONTEXT}

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="watcher-sitzung-"))
        self.claude = _fake_claude(self.tmp)
        self._pfad = aw.sitzungs_pfad
        aw.sitzungs_pfad = lambda agent: self.tmp / "buch" / f"{agent}.sitzungen.json"
        aw.HART.clear()
        aw.STOP.clear()

    def tearDown(self) -> None:
        aw.sitzungs_pfad = self._pfad
        shutil.rmtree(self.tmp, ignore_errors=True)

    def lauf(self, task_id, text="sitzung", resume=None, rolle=None, hint=False,
             verbrauch=None, meldungen=None, meta=None, **mehr):
        return aw.run_claude_sitzung(
            self.AN if resume is None else resume, "erp", task_id, self.claude,
            text, self.tmp, False,
            meldungen.append if meldungen is not None else None,
            None, None, None, rolle, verbrauch, hint, lauf_meta=meta, **mehr)

    def aufrufe(self):
        datei = self.tmp / "aufrufe.jsonl"
        if not datei.exists():
            return []
        return [json.loads(z) for z in datei.read_text(encoding="utf-8").splitlines()]

    def test_zweiter_task_setzt_die_sitzung_fort(self):
        v2, m1, m2, meldungen = {}, {}, {}, []
        r1, _, rc1 = self.lauf("t1", meta=m1, meldungen=meldungen)
        r2, _, rc2 = self.lauf("t2", verbrauch=v2, meta=m2, meldungen=meldungen)
        self.assertEqual((rc1, rc2), (0, 0))
        self.assertEqual([a["resume"] for a in self.aufrufe()], [None, "sitzung-neu-0001"])
        self.assertEqual((m1["sitzung"], m2["sitzung"]), ("neu", "fortgesetzt"))
        # Issue #44: der Grund steht in den Lauf-Daten, nicht nur im stdout
        self.assertIn("noch keine Sitzung", m1["sitzung_grund"])
        self.assertIn("Task 2", m2["sitzung_grund"])
        # session_id steht in der Antwort: ein Mensch kann den Faden mit
        # `claude --resume <id>` im Terminal übernehmen (Issue #37)
        self.assertEqual(m2["session_id"], "sitzung-neu-0001")
        # Kontext = letzte HAUPT-Anfrage; die Subagenten-Nachricht zählt nicht
        self.assertEqual(m2["kontext"], 42_100)
        # der Verbrauchszähler bekommt NUR seine Token-Felder
        self.assertEqual(v2, {"input_tokens": 10, "output_tokens": 90})
        buch = aw.lade_sitzungen("erp")
        eintrag = buch["sitzungen"][aw.sitzungs_schluessel(self.tmp, None)]
        self.assertEqual((eintrag["session_id"], eintrag["tasks"]), ("sitzung-neu-0001", 2))
        self.assertTrue(any("neue Sitzung" in m for m in meldungen), meldungen)
        self.assertTrue(any("setzt Sitzung fort" in m for m in meldungen), meldungen)

    def test_kurzer_hinweis_nur_beim_fortsetzen(self):
        self.lauf("t1", hint=True)
        self.lauf("t2", hint=True)
        erster, zweiter = (a["prompt"] for a in self.aufrufe())
        self.assertIn("Prüfe zu Beginn deine Inbox", erster)
        self.assertNotIn("Prüfe zu Beginn deine Inbox", zweiter)
        self.assertIn("Neuer Auftrag in derselben Sitzung", zweiter)
        self.assertTrue(zweiter.endswith("sitzung"))

    def test_verlorene_sitzung_faellt_auf_frisch_zurueck(self):
        schluessel = aw.sitzungs_schluessel(self.tmp, None)
        aw.speichere_sitzungen("erp", {"sitzungen": {schluessel: {
            "session_id": "verloren-0001", "zuletzt": time.time(),
            "kontext": 10, "tasks": 1}}, "tasks": {}})
        meta, meldungen = {}, []
        result, _, rc = self.lauf("t1", meta=meta, meldungen=meldungen)
        self.assertEqual(rc, 0)
        self.assertEqual(result, "OK sitzung-neu-0001")
        self.assertEqual([a["resume"] for a in self.aufrufe()], ["verloren-0001", None])
        self.assertEqual(meta["sitzung"], "neu")
        self.assertEqual(meta["session_id"], "sitzung-neu-0001")  # nie die verlorene
        self.assertTrue(any("nicht fortsetzbar" in m for m in meldungen), meldungen)
        buch = aw.lade_sitzungen("erp")
        self.assertEqual(buch["sitzungen"][schluessel]["session_id"], "sitzung-neu-0001")

    def test_fehler_nach_echter_arbeit_laeuft_nicht_doppelt(self):
        self.lauf("t1")
        _, _, rc = self.lauf("t2", text="sitzung scheitert")
        self.assertEqual(rc, 1)
        self.assertEqual(len(self.aufrufe()), 2, "gescheiterter Lauf wurde wiederholt")
        # …und der nächste Task beginnt sauber in einer neuen Sitzung
        self.assertEqual(aw.lade_sitzungen("erp")["sitzungen"], {})
        self.lauf("t3")
        self.assertIsNone(self.aufrufe()[-1]["resume"])

    def test_geparkter_task_setzt_seine_eigene_sitzung_fort(self):
        self.lauf("t-park")
        buch = aw.lade_sitzungen("erp")
        # inzwischen lief ein anderer Task in einer anderen Sitzung weiter
        buch["sitzungen"][aw.sitzungs_schluessel(self.tmp, None)]["session_id"] = "sitzung-andere-02"
        aw.speichere_sitzungen("erp", buch)
        self.lauf("t-park")
        self.assertEqual(self.aufrufe()[-1]["resume"], "sitzung-neu-0001")

    def test_rolle_bekommt_eigene_sitzung(self):
        self.lauf("t1")
        self.lauf("t2", rolle="pruefer")
        self.assertEqual([a["resume"] for a in self.aufrufe()], [None, None])

    def test_abgeschaltet_bleibt_alles_wie_frueher(self):
        aus = {"an": False}
        meta = {}
        self.lauf("t1", resume=aus, meta=meta)
        self.lauf("t2", resume=aus)
        self.assertEqual([a["resume"] for a in self.aufrufe()], [None, None])
        self.assertNotIn("sitzung", meta)
        self.assertEqual(meta["session_id"], "sitzung-neu-0001")  # zum Übernehmen
        self.assertFalse((self.tmp / "buch").exists())

    def test_thread_bekommt_eigenen_faden_und_laengere_pause(self):
        """Issue #37: `thread` im Task = eigener Vorgang. Folge-Aufträge finden
        ihr Gedächtnis auch nach Tagen wieder; Aufträge ohne thread teilen sich
        weiter die Sitzung des Verzeichnisses."""
        self.lauf("t1", thread="Startseite Aufgaben")
        self.lauf("t2")                                   # ohne thread: eigener Schlüssel
        meta = {}
        self.lauf("t3", thread="Startseite Aufgaben", meta=meta)
        self.assertEqual([a["resume"] for a in self.aufrufe()],
                         [None, None, "sitzung-neu-0001"])
        self.assertEqual(meta["thread"], "Startseite-Aufgaben")
        # drei Tage Pause: ohne thread neu, mit thread weiter
        buch = aw.lade_sitzungen("erp")
        for eintrag in buch["sitzungen"].values():
            eintrag["zuletzt"] -= 3 * 86400
        buch["tasks"] = {}
        aw.speichere_sitzungen("erp", buch)
        self.lauf("t4")
        self.lauf("t5", thread="Startseite Aufgaben")
        self.assertEqual([a["resume"] for a in self.aufrufe()][-2:],
                         [None, "sitzung-neu-0001"])

    def test_timeout_behaelt_die_sitzung_zum_fortsetzen(self):
        """Issue #38: ein Timeout trifft einen ARBEITENDEN Lauf — sein Stand
        steht im Transkript. Der erneute Anstoß desselben Tasks setzt genau
        dort fort, und die Antwort nennt die Sitzung zum Übernehmen."""
        meta = {}
        _, log, rc = self.lauf("t1", text="sitzung haengen", meta=meta, timeout=1.0)
        self.assertNotEqual(rc, 0)
        self.assertIn("Gesamtdauer", meta["timeout"])
        self.assertTrue(meta["fortsetzbar"])
        self.assertIn("claude --resume sitzung-neu-0001", log)
        self.lauf("t1")
        self.assertEqual(self.aufrufe()[-1]["resume"], "sitzung-neu-0001")

    def test_leerlauf_waechter_bricht_stille_ab_nicht_arbeit(self):
        """Issue #38: abgebrochen wird, wer SCHWEIGT — nicht, wer lange arbeitet."""
        meta = {}
        start = time.monotonic()
        _, log, rc = self.lauf("t1", text="sitzung haengen", meta=meta,
                               timeout=600.0, leerlauf=1.0)
        self.assertLess(time.monotonic() - start, 30)
        self.assertNotEqual(rc, 0)
        self.assertIn("ohne Lebenszeichen", meta["timeout"])
        self.assertIn("ohne Lebenszeichen", log)

    def test_frist_steht_im_hinweis(self):
        self.lauf("t1", hint=True, timeout=1800.0, leerlauf=600.0)
        prompt = self.aufrufe()[0]["prompt"]
        self.assertIn("nach 30 min Gesamtdauer", prompt)
        self.assertIn("10 min ohne jede Aktivität", prompt)


if __name__ == "__main__":
    unittest.main(verbosity=2)
