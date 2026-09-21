"""send_file ÜBER den MCP-Tool-Layer (FastMCP-Stub wie test_mcp_tools):

    cd backend && python -m tests.test_mcp_send_file

Getestet wird, was nur hier entschieden wird — der Kern hat sein eigenes
Modul (test_austausch):
  * gebundener Kanal: gelesen wird IMMER von der eigenen Maschine, ein fremdes
    `source` wird abgelehnt; der Agent steht als Absender in der Nachricht
  * freier Kanal: `source` ist Pflicht, Absender der Nachricht = orchestrator
  * fachliche Fehler kommen als {"error": …} zurück (das Modell soll sie lesen)
  * das Tool hängt an der Allowlist und fehlt in den Token-Grundtools
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

from tests.sftp_doppel import SftpDoppel  # noqa: E402
from tests.test_mcp_tools import _mcp_server_laden, _tools  # noqa: E402

AGENTS_YAML = """\
agents:
  - name: erp
    connection: {type: ssh, host: 192.168.1.10, user: u, key_file: /nix}
  - name: deverp
    connection: {type: ssh, host: 192.168.1.11, user: u, key_file: /nix}
"""


def _aufbau():
    ws = Path(tempfile.mkdtemp(prefix="mcp-sendfile-"))
    (ws / "config").mkdir(parents=True)
    (ws / "config" / "agents.yaml").write_text(AGENTS_YAML, encoding="utf-8")
    mcp_server = _mcp_server_laden(ws)
    platten = {n: SftpDoppel(ws / "platten" / n) for n in ("erp", "deverp")}

    @asynccontextmanager
    async def verbinde(name):
        yield platten[name]

    # Das Tool ruft uebergib_sync OHNE Verbinder → der Kern greift zur
    # frischen SSH-Verbindung. Genau die wird hier durch das Doppel ersetzt.
    mcp_server.austausch.frische_verbindung = verbinde
    import asyncio

    asyncio.run(mcp_server.austausch.richte_ein("deverp", "austausch"))
    quelle = platten["erp"]._lokal("export/kunden.csv")
    quelle.parent.mkdir(parents=True)
    quelle.write_bytes(b"a;b\n")
    return ws, mcp_server, platten


def _post(mcp_server, ws: Path, agent: str) -> list[dict]:
    return mcp_server.Mailbox(ws / "mailboxes", agent).read_inbox()


def test_gebundener_kanal_liest_nur_die_eigene_maschine() -> None:
    ws, mcp_server, platten = _aufbau()
    try:
        t = _tools(mcp_server, identity="erp")
        r = t["send_file"](to="deverp", paths=["~/export/kunden.csv"], note="für den Import")
        assert "error" not in r, r
        assert r["von"] == "erp" and r["gemeldet"], r
        assert platten["deverp"]._lokal("austausch/von-erp/kunden.csv").read_bytes() == b"a;b\n"
        env = _post(mcp_server, ws, "deverp")[0]
        assert env["sender"] == "erp", env
        assert "für den Import" in env["text"], env

        fremd = t["send_file"](to="deverp", paths=["x"], source="deverp")
        assert fremd.get("error"), fremd
        assert len(_post(mcp_server, ws, "deverp")) == 1, "abgelehnter Aufruf darf nichts zustellen"
    finally:
        shutil.rmtree(ws, ignore_errors=True)
    print("OK test_gebundener_kanal_liest_nur_die_eigene_maschine")


def test_freier_kanal_braucht_source() -> None:
    ws, mcp_server, _ = _aufbau()
    try:
        t = _tools(mcp_server)
        ohne = t["send_file"](to="deverp", paths=["export/kunden.csv"])
        assert "source" in ohne.get("error", ""), ohne
        r = t["send_file"](to="deverp", paths="export/kunden.csv", source="erp")  # str statt Liste
        assert "error" not in r, r
        env = _post(mcp_server, ws, "deverp")[0]
        assert env["sender"] == "orchestrator", env
        assert "von erp" in env["text"], env
    finally:
        shutil.rmtree(ws, ignore_errors=True)
    print("OK test_freier_kanal_braucht_source")


def test_fachfehler_als_wert() -> None:
    ws, mcp_server, _ = _aufbau()
    try:
        t = _tools(mcp_server, identity="deverp")
        r = t["send_file"](to="erp", paths=["egal.txt"])  # erp hat keinen Ordner
        assert "keinen Austausch-Ordner" in r.get("error", ""), r
        assert mcp_server.fehler_im_ergebnis(r), "muss im Log als Fehler zählen (#40)"
        nichts = _tools(mcp_server, identity="erp")["send_file"](to="deverp", paths=["fehlt.txt"])
        assert "keine Datei zugestellt" in nichts.get("error", ""), nichts
    finally:
        shutil.rmtree(ws, ignore_errors=True)
    print("OK test_fachfehler_als_wert")


def test_allowlist_und_token_grundmenge() -> None:
    ws, mcp_server, _ = _aufbau()
    try:
        assert "send_file" in mcp_server.mcp_scope.KNOWN_TOOLS
        assert "send_file" not in mcp_server.mcp_scope.TOKEN_GRUNDTOOLS
        assert "send_file" not in _tools(mcp_server, identity="erp", allowed={"inbox"})
        assert "send_file" in _tools(mcp_server, identity="erp", allowed={"inbox", "send_file"})
    finally:
        shutil.rmtree(ws, ignore_errors=True)
    print("OK test_allowlist_und_token_grundmenge")


if __name__ == "__main__":
    test_gebundener_kanal_liest_nur_die_eigene_maschine()
    test_freier_kanal_braucht_source()
    test_fachfehler_als_wert()
    test_allowlist_und_token_grundmenge()
    print("alle send_file-Tests grün")
