"""MCP-Tools auf Funktionsebene: Task-Roundtrip, Identität, Empfängerprüfung.

Nagelt die Befunde H1 (project geht über MCP verloren), H2 (Doppel-Claim),
M3 (Antwort-Drift) und M5 (Geister-Mailbox) fest.

Das echte `mcp`-Paket steckt nur im Container — hier wird `FastMCP` durch ein
Doppel ersetzt, das die registrierten Funktionen einsammelt. Getestet wird
damit exakt der Code, der auch produktiv registriert wird, aber ohne Netzwerk
und ohne Abhängigkeiten:
    cd backend && python -m tests.test_mcp_tools
"""
from __future__ import annotations

import asyncio
import inspect
import shutil
import sys
import tempfile
import time
import types
from pathlib import Path


class _FastMCPDoppel:
    """Sammelt die per @mcp.tool() registrierten Funktionen ein."""

    def __init__(self, *args, **kwargs) -> None:
        self.tools: dict[str, object] = {}   # synchroner Kern (zum Aufrufen)
        self.roh: dict[str, object] = {}     # wie registriert (async-Hülle)

    def tool(self):
        def deco(fn):
            # Seit Issue #34 registriert mcp_server eine async-Hülle um jedes
            # Tool (Thread-Auslagerung). Die Fachtests rufen den synchronen
            # Kern darunter auf — genau den, den auch die Hülle ausführt.
            self.roh[fn.__name__] = fn
            self.tools[fn.__name__] = getattr(fn, "__wrapped__", fn)
            return fn
        return deco


def _mcp_server_laden(workspace: Path):
    """mcp_server mit gestubbtem FastMCP und eigenem Workspace importieren."""
    for name in ("mcp", "mcp.server", "mcp.server.fastmcp"):
        sys.modules.pop(name, None)
    paket = types.ModuleType("mcp")
    server = types.ModuleType("mcp.server")
    fastmcp = types.ModuleType("mcp.server.fastmcp")
    fastmcp.FastMCP = _FastMCPDoppel
    paket.server = server
    server.fastmcp = fastmcp
    sys.modules.update({"mcp": paket, "mcp.server": server, "mcp.server.fastmcp": fastmcp})

    import os
    os.environ["WORKSPACE_DIR"] = str(workspace)
    os.environ["DATA_CONFIG_DIR"] = str(workspace / "config")
    for name in [m for m in list(sys.modules) if m == "mcp_server" or m.startswith("app.")]:
        sys.modules.pop(name, None)
    import mcp_server  # noqa: PLC0415 — bewusst nach dem Stub
    return mcp_server


def _tools(mcp_server, identity=None, allowed=None) -> dict:
    doppel = _FastMCPDoppel()
    mcp_server.register_tools(doppel, identity, allowed)
    return doppel.tools


def test_project_ueberlebt_den_mcp_weg(ws: Path) -> None:
    """H1: claim_task MUSS project liefern — sonst arbeitet der Watcher falsch."""
    mcp_server = _mcp_server_laden(ws)
    (ws / "mailboxes" / "worker").mkdir(parents=True)
    t = _tools(mcp_server)

    gesendet = t["send_task"](to="worker", instruction="baue X", sender="chef",
                              project="kunde-a", files=["a.py"])
    assert "error" not in gesendet, gesendet

    # (1) inbox() reicht project mit durch
    eingang = t["inbox"](agent="worker", kind="task")
    assert eingang[0]["project"] == "kunde-a", eingang
    assert eingang[0]["files"] == ["a.py"], eingang

    # (2) claim_task ebenfalls — das ist die Quelle, aus der der Watcher liest
    beansprucht = t["claim_task"](task_id=gesendet["id"], agent="worker")
    assert beansprucht["project"] == "kunde-a", beansprucht
    assert beansprucht["files"] == ["a.py"], beansprucht
    assert beansprucht["instruction"] == "baue X"

    # (3) Abschluss stellt beim Auftraggeber zu
    fertig = t["complete_task"](task_id=gesendet["id"], result="erledigt", agent="worker")
    assert fertig["status"] == "done", fertig
    antworten = t["inbox"](agent="chef")
    assert any(a["kind"] == "response" and a["text"] == "erledigt" for a in antworten), antworten

    # (4) Der Watcher liefert bei Verbindungsabriss erneut ab — das ist kein
    #     Fehler und darf die Antwort nicht doppelt zustellen.
    nochmal = t["complete_task"](task_id=gesendet["id"], result="erledigt", agent="worker")
    assert nochmal.get("already") is True, nochmal
    responses = [a for a in t["inbox"](agent="chef") if a["kind"] == "response"]
    assert len(responses) == 1, responses
    # Ein nie existierender Task bleibt ein ehrlicher Fehler
    unbekannt = t["complete_task"](task_id="task-nie", result="x", agent="worker")
    assert "error" in unbekannt, unbekannt


def test_doppelter_claim_meldet_fehler(ws: Path) -> None:
    """H2: der zweite Claimer bekommt einen Fehler, keinen Auftrag."""
    mcp_server = _mcp_server_laden(ws)
    (ws / "mailboxes" / "worker").mkdir(parents=True)
    t = _tools(mcp_server)
    task = t["send_task"](to="worker", instruction="einmal", sender="chef")

    erster = t["claim_task"](task_id=task["id"], agent="worker")
    assert "error" not in erster, erster
    zweiter = t["claim_task"](task_id=task["id"], agent="worker")
    assert zweiter.get("already_claimed") is True, zweiter
    assert "erneut=True" in zweiter["error"], zweiter
    # Der Watcher prüft genau darauf:
    assert zweiter.get("error"), "Watcher überspringt nur bei gesetztem error"

    nochmal = t["claim_task"](task_id=task["id"], agent="worker", erneut=True)
    assert nochmal["instruction"] == "einmal", nochmal


def test_unbekannter_empfaenger_wird_abgelehnt(ws: Path) -> None:
    """M5: ein Tippfehler darf keine Geister-Mailbox anlegen."""
    mcp_server = _mcp_server_laden(ws)
    (ws / "mailboxes" / "worker").mkdir(parents=True)
    t = _tools(mcp_server)

    for aufruf in (
        lambda: t["send_task"](to="wroker", instruction="x", sender="chef"),
        lambda: t["send_message"](to="wroker", text="x", sender="chef"),
        lambda: t["ask"](to="wroker", question="x?", sender="chef"),
    ):
        antwort = aufruf()
        assert "error" in antwort and "unbekannter Agent" in antwort["error"], antwort
    assert not (ws / "mailboxes" / "wroker").exists(), "Geister-Mailbox angelegt!"

    # Pfad-Traversal im Namen ebenso
    boese = t["send_task"](to="../../etc", instruction="x", sender="chef")
    assert "error" in boese, boese

    # Der Orchestrator gilt immer als bekannt, auch ohne eigene Mailbox —
    # sonst käme keine Rückfrage eines Agenten mehr bei ihm an.
    assert not (ws / "mailboxes" / "orchestrator").exists()
    an_orchestrator = t["ask"](to="orchestrator", question="weiter?", sender="worker")
    assert "error" not in an_orchestrator, an_orchestrator

    # list_agents und die Empfängerprüfung müssen dieselbe Menge sehen:
    # wer gelistet wird, muss auch ansprechbar sein.
    gelistet = t["list_agents"]()
    assert "worker" in gelistet and "orchestrator" in gelistet, gelistet
    for name in gelistet:
        assert t["send_message"](to=name, text="ping", sender="chef").get("error") is None


def test_antwort_raeumt_die_frage_ab(ws: Path) -> None:
    """M3: answer() archiviert die Frage in der eigenen Inbox."""
    mcp_server = _mcp_server_laden(ws)
    for a in ("worker", "chef"):
        (ws / "mailboxes" / a).mkdir(parents=True)
    t = _tools(mcp_server)

    frage = t["ask"](to="chef", question="Welcher Branch?", sender="worker")
    offen = [e for e in t["inbox"](agent="chef") if e["kind"] == "question"]
    assert offen and offen[0]["status"] == "needs_confirm", offen

    t["answer"](to="worker", text="main", sender="chef", reply_to=frage["id"])
    danach = [e for e in t["inbox"](agent="chef") if e["kind"] == "question"]
    assert danach == [], f"beantwortete Frage liegt weiter in der Inbox: {danach}"
    antworten = [e for e in t["inbox"](agent="worker") if e["kind"] == "answer"]
    assert antworten and antworten[0]["text"] == "main", antworten


def test_gebundener_kanal_erzwingt_identitaet(ws: Path) -> None:
    """#13 bleibt intakt: fremde agent-Werte werden abgelehnt, Allowlist greift."""
    mcp_server = _mcp_server_laden(ws)
    for a in ("worker", "chef"):
        (ws / "mailboxes" / a).mkdir(parents=True)
    t = _tools(mcp_server, identity="worker", allowed={"inbox", "claim_task"})
    assert set(t) == {"inbox", "claim_task"}, set(t)

    fremd = t["inbox"](agent="chef")
    assert fremd and "error" in fremd[0], fremd
    eigen = t["inbox"]()
    assert isinstance(eigen, list) and not (eigen and "error" in eigen[0]), eigen


def test_kein_tool_blockiert_den_loop(ws: Path) -> None:
    """#34: Tools laufen im Thread — ein langer Aufruf legt nicht alles lahm.

    Das mcp-SDK ruft synchrone Tools direkt im Event-Loop auf; damit fror ein
    minutenlanges call_integration jeden anderen Kanal ein. Geprüft wird beides:
    dass alles als Coroutine registriert ist, und dass ein blockierender Aufruf
    den Loop tatsächlich nicht mehr anhält.
    """
    mcp_server = _mcp_server_laden(ws)
    doppel = _FastMCPDoppel()
    mcp_server.register_tools(doppel, None, None)
    nicht_async = sorted(
        n for n, fn in doppel.roh.items() if not inspect.iscoroutinefunction(fn)
    )
    assert not nicht_async, f"nicht im Thread: {nicht_async}"
    # Signatur/Doku müssen die der Originalfunktion bleiben — daraus baut
    # FastMCP das Tool-Schema. Ginge das verloren, hätte jedes Tool plötzlich
    # nur noch (**kwargs) als Parameter.
    assert inspect.signature(doppel.roh["send_task"]) == inspect.signature(
        doppel.tools["send_task"]
    )
    assert doppel.roh["send_task"].__doc__ == doppel.tools["send_task"].__doc__

    mcp_server.integrations.call_integration = lambda *a, **k: (
        time.sleep(0.6),
        {"status": 200},
    )[1]
    verzug: list[float] = []

    async def probe() -> float:
        async def herzschlag(stop: asyncio.Event) -> None:
            letzt = time.monotonic()
            while not stop.is_set():
                await asyncio.sleep(0.02)
                jetzt = time.monotonic()
                verzug.append(jetzt - letzt - 0.02)
                letzt = jetzt

        stop = asyncio.Event()
        hb = asyncio.create_task(herzschlag(stop))
        await asyncio.sleep(0.05)
        t0 = time.monotonic()
        lang = asyncio.create_task(doppel.roh["call_integration"](name="x"))
        await doppel.roh["list_agents"]()
        dauer = time.monotonic() - t0
        await lang
        stop.set()
        await hb
        return dauer

    dauer = asyncio.run(probe())
    assert dauer < 0.3, f"list_agents wartete {dauer:.2f}s auf den langen Aufruf"
    assert max(verzug) < 0.3, f"Loop stand {max(verzug):.2f}s still"


def main() -> None:
    tests = [test_project_ueberlebt_den_mcp_weg,
             test_doppelter_claim_meldet_fehler,
             test_unbekannter_empfaenger_wird_abgelehnt,
             test_antwort_raeumt_die_frage_ab,
             test_gebundener_kanal_erzwingt_identitaet,
             test_kein_tool_blockiert_den_loop]
    for test in tests:
        tmp = Path(tempfile.mkdtemp(prefix="mcp-tools-"))
        try:
            test(tmp)
            print(f"OK  {test.__name__}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    print(f"alle {len(tests)} MCP-Tool-Tests grün")


if __name__ == "__main__":
    main()
