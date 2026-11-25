"""Issue #67 im Prüfstand: echtes tmux, nachgebautes Claude Code.

    cd backend && python -m tests.test_issue_67_tmux     (überspringt ohne tmux)

Das Doppel (`FAKE`) zeichnet wie Claude Code 2.1.332 eine Eingabebox
zwischen zwei Linien, schaltet Bracketed Paste ein, ruft die Hooks aus
`.claude/settings.local.json` (UserPromptSubmit/Stop/SessionStart) mit JSON
auf stdin und zeigt „esc to interrupt", solange es „arbeitet". Läuft unter
argv0 `claude`, damit tmux den Prozess so meldet. Steht `SCHLIESSE_AB` im
Prompt, legt es eine Marke ab — das gefakte MCP meldet den Task dann als
abgeschlossen (wie ein complete_task aus der Sitzung).

Fälle: frei → zugestellt → erledigt (mehrzeiliger Text kommt heil an);
jemand tippt → wartet → Frist → headless; keine Sitzung → headless;
nicht abgeschlossen → genau EINE Erinnerung → offen; Sitzung geschlossen → weg.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
import agent_watcher as aw  # noqa: E402

FAKE = r'''
import json, os, subprocess, sys, termios, time, tty, uuid
cwd = os.getcwd()
sid = str(uuid.uuid4())
log = os.path.join(cwd, "prompts.log")
buf = sys.argv[1] if len(sys.argv) > 1 else ""
arbeitet = False

def hooks(ereignis, **mehr):
    try:
        d = json.load(open(os.path.join(cwd, ".claude", "settings.local.json")))
    except Exception:
        return
    for g in d.get("hooks", {}).get(ereignis, []):
        for h in g.get("hooks", []):
            daten = dict(hook_event_name=ereignis, cwd=cwd, session_id=sid,
                         transcript_path=os.path.join(cwd, "t.jsonl"), **mehr)
            subprocess.run(h["command"], shell=True, input=json.dumps(daten), text=True)

def zeichne():
    out = "\x1b[H\x1b[2J\x1b[?2004h Fake Claude\r\n" + "─" * 40 + "\r\n"
    out += "❯\xa0" + (buf.replace("\n", "\r\n  ") if buf else "\x1b[2mTry something\x1b[0m") + "\r\n"
    out += "─" * 40 + "\r\n"
    out += "  ⏵⏵ auto mode on" + (" · esc to interrupt" if arbeitet else " (shift+tab)") + "\r\n"
    sys.stdout.write(out); sys.stdout.flush()

hooks("SessionStart")
fd = sys.stdin.fileno()
alt = termios.tcgetattr(fd)
tty.setraw(fd)
try:
    zeichne()
    paste = False
    while True:
        ch = os.read(fd, 4096).decode("utf-8", "replace")
        i = 0
        while i < len(ch):
            if ch.startswith("\x1b[200~", i):
                paste = True; i += 6; continue
            if ch.startswith("\x1b[201~", i):
                paste = False; i += 6; continue
            c = ch[i]; i += 1
            if paste:
                buf += "\n" if c == "\r" else c
            elif c == "\r" and buf:
                prompt, buf = buf, ""
                open(log, "a").write(json.dumps(prompt) + "\n")
                arbeitet = True; zeichne()
                hooks("UserPromptSubmit", prompt=prompt)
                time.sleep(1.0)
                if "SCHLIESSE_AB" in prompt:
                    open(os.path.join(cwd, "abgeschlossen"), "w").write("1")
                arbeitet = False
                hooks("Stop")
            elif c == "\x15":
                buf = ""
            elif c == "\x03":
                raise SystemExit
            elif c >= " ":
                buf += c
        zeichne()
finally:
    termios.tcsetattr(fd, termios.TCSADRAIN, alt)
'''


class FakeClient:
    """MCP-Doppel: Task ist offen, bis das Fake-Claude die Marke ablegt."""

    def __init__(self, projekt: Path):
        self.projekt = projekt
        self.aufrufe: list = []

    def call(self, tool, args):
        self.aufrufe.append((tool, dict(args)))
        if tool == "claim_task" and args.get("erneut") and not args.get("sitzung") \
                and not args.get("watcher"):
            if (self.projekt / "abgeschlossen").exists():
                return {"error": f"Task {args['task_id']} liegt nicht (mehr) bei x."}
            return {"task_id": args["task_id"]}
        return {"ok": True}


def _starte(projekt: Path, name: str, vorbelegt: str = "") -> None:
    skript = projekt.parent / "fake_claude.py"
    skript.write_text(FAKE, encoding="utf-8")
    befehl = (f"export AGENT_DASHBOARD_SITZUNGEN={aw.SITZUNG_DIR}; "
              f"exec -a claude {sys.executable} {skript}") + (f" {json.dumps(vorbelegt)}" if vorbelegt else "")
    subprocess.run(["tmux", "new-session", "-d", "-s", name, "-x", "100", "-y", "30",
                    "-c", str(projekt), befehl], check=True)
    for _ in range(50):
        if aw.finde_sitzung(projekt):
            return
        time.sleep(0.1)
    raise AssertionError("Fake-Claude nicht gefunden")


def _ende(name: str) -> None:
    subprocess.run(["tmux", "kill-session", "-t", "=" + name], capture_output=True)


def _projekt(tmp: Path, name: str) -> Path:
    p = tmp / name
    p.mkdir()
    # Hooks vorab, damit die Sitzung beim Start schon „frei" meldet
    aw.hooks_einrichten(p)
    return p


def test_frei_zugestellt_erledigt(tmp: Path) -> None:
    p = _projekt(tmp, "proj-a")
    _starte(p, "pruef67a")
    try:
        c = FakeClient(p)
        text = "Zeile eins\nZeile zwei mit ‚Umlauten‘ äöü\nSCHLIESSE_AB"
        r = aw.in_sitzung_bearbeiten(c, "worker", "t-1", p, text, "chef", 30)
        assert r["weg"] == "sitzung" and r["status"] == "erledigt", r
        prompts = [json.loads(z) for z in (p / "prompts.log").read_text(encoding="utf-8").splitlines()]
        assert len(prompts) == 1, prompts
        assert "Task t-1 von chef" in prompts[0] and text in prompts[0], prompts[0]
        assert ("claim_task", {"task_id": "t-1", "erneut": True, "sitzung": True}) in c.aufrufe
    finally:
        _ende("pruef67a")


def test_jemand_tippt_dann_headless(tmp: Path) -> None:
    p = _projekt(tmp, "proj-b")
    _starte(p, "pruef67b", vorbelegt="ich tippe gerade")
    try:
        r = aw.in_sitzung_bearbeiten(FakeClient(p), "worker", "t-2", p, "x", "chef", 3)
        assert r["weg"] == "headless" and "belegt" in r["grund"], r
        assert not (p / "prompts.log").exists(), "nichts in die halbe Eingabe getippt"
    finally:
        _ende("pruef67b")


def test_keine_sitzung(tmp: Path) -> None:
    p = _projekt(tmp, "proj-c")
    r = aw.in_sitzung_bearbeiten(FakeClient(p), "worker", "t-3", p, "x", "chef", 5)
    assert r["weg"] == "headless" and "keine" in r["grund"], r


def test_erinnerung_dann_offen(tmp: Path) -> None:
    p = _projekt(tmp, "proj-d")
    _starte(p, "pruef67d")
    try:
        r = aw.in_sitzung_bearbeiten(FakeClient(p), "worker", "t-4", p, "mach was", "chef", 8)
        assert r["weg"] == "sitzung" and r["status"] == "offen", r
        prompts = [json.loads(z) for z in (p / "prompts.log").read_text(encoding="utf-8").splitlines()]
        assert len(prompts) == 2, prompts
        assert "t-4 ist noch offen" in prompts[1], prompts
    finally:
        _ende("pruef67d")


def test_sitzung_geschlossen(tmp: Path) -> None:
    p = _projekt(tmp, "proj-e")
    _starte(p, "pruef67e")
    try:
        import threading
        threading.Timer(3.0, _ende, args=("pruef67e",)).start()
        r = aw.in_sitzung_bearbeiten(FakeClient(p), "worker", "t-5", p, "mach was", "chef", 30)
        assert r["weg"] == "sitzung" and r["status"] == "weg", r
    finally:
        _ende("pruef67e")


TESTS = [test_frei_zugestellt_erledigt, test_jemand_tippt_dann_headless, test_keine_sitzung,
         test_erinnerung_dann_offen, test_sitzung_geschlossen]


def main() -> None:
    if not shutil.which("tmux"):
        print("übersprungen: tmux fehlt")
        return
    aw.SITZUNG_TAKT = 0.5
    aw.SITZUNG_ERINNERN_NACH = 1.0
    aw.SITZUNG_BESTAETIGUNG = 5.0
    tmp = Path(tempfile.mkdtemp(prefix="issue-67-tmux-"))
    aw.SITZUNG_DIR = tmp / "sitzungen"
    try:
        for test in TESTS:
            (tmp / test.__name__).mkdir()
            test(tmp / test.__name__)
            print(f"OK  {test.__name__}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"alle {len(TESTS)} tmux-Prüfstandtests zu Issue #67 grün")


if __name__ == "__main__":
    main()
