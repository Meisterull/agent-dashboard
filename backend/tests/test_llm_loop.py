"""Agentic-Loop + Anthropic-Übersetzung, ohne echten Provider.

Deckt H4 (Thinking-Blöcke überleben die History), M2/M4 (Tool-Fehler und
Kappung) und N4 (leere Assistant-Nachricht) ab. Nur Standardlib:
    cd backend && python -m tests.test_llm_loop
"""
from __future__ import annotations

import asyncio

from app import llm


def _lauf(antworten: list[dict], call_tool) -> tuple[dict, list[dict]]:
    """run_turn mit vorgegebenen Modell-Antworten laufen lassen."""
    messages: list[dict] = [{"role": "user", "content": "los"}]
    folge = list(antworten)

    async def fake_complete(cfg, system, msgs, tools):
        return folge.pop(0)

    original = llm._complete
    llm._complete = fake_complete
    try:
        ergebnis = asyncio.run(
            llm.run_turn({"provider": "test"}, "sys", messages, [], call_tool)
        )
    finally:
        llm._complete = original
    return ergebnis, messages


def test_tool_fehler_beendet_den_turn_nicht() -> None:
    """M2: ein gescheitertes Tool wird zum Ergebnis, nicht zum Abbruch."""
    async def call_tool(name, inp):
        raise RuntimeError("MCP weg")

    ergebnis, messages = _lauf(
        [
            {"text": "", "tool_calls": [{"id": "c1", "name": "inbox", "input": {}}]},
            {"text": "konnte nichts lesen", "tool_calls": []},
        ],
        call_tool,
    )
    assert ergebnis["text"] == "konnte nichts lesen", ergebnis
    tool_msgs = [m for m in messages if m["role"] == "tool"]
    assert tool_msgs and tool_msgs[0]["content"].startswith("[TOOL-FEHLER]"), tool_msgs
    assert "MCP weg" in tool_msgs[0]["content"]
    assert ergebnis["tool_calls"][0]["error"] == "MCP weg", ergebnis


def test_langes_tool_ergebnis_wird_gekappt() -> None:
    """M4: Riesen-Ausgaben fahren nicht ungebremst in jeder Runde mit."""
    async def call_tool(name, inp):
        return "x" * (llm.MAX_TOOL_RESULT_CHARS + 5000)

    _, messages = _lauf(
        [
            {"text": "", "tool_calls": [{"id": "c1", "name": "read", "input": {}}]},
            {"text": "fertig", "tool_calls": []},
        ],
        call_tool,
    )
    inhalt = [m for m in messages if m["role"] == "tool"][0]["content"]
    assert len(inhalt) < llm.MAX_TOOL_RESULT_CHARS + 200, len(inhalt)
    assert "gekürzt" in inhalt


def test_rundenlimit_laesst_keine_offenen_tool_calls() -> None:
    """Abbruch am Limit muss jedem tool_call ein Ergebnis geben."""
    async def call_tool(name, inp):
        return "ok"

    antworten = [
        {"text": "", "tool_calls": [{"id": f"c{i}", "name": "inbox", "input": {}}]}
        for i in range(llm.MAX_TOOL_ROUNDS)
    ]
    ergebnis, messages = _lauf(antworten, call_tool)
    assert "Tool-Runden-Limit" in ergebnis["text"]
    offen = {tc["id"] for m in messages if m["role"] == "assistant"
             for tc in m.get("tool_calls") or []}
    beantwortet = {m["tool_call_id"] for m in messages if m["role"] == "tool"}
    assert offen == beantwortet, (offen - beantwortet)


def test_thinking_bloecke_gehen_unveraendert_zurueck() -> None:
    """H4: Rohblöcke (inkl. Signatur) verbatim, sonst lehnt die API die Runde ab."""
    roh = [
        {"type": "thinking", "thinking": "denk", "signature": "sig-abc"},
        {"type": "tool_use", "id": "c1", "name": "inbox", "input": {}},
    ]
    messages = [
        {"role": "user", "content": "los"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "name": "inbox", "input": {}}], "_anthropic": roh},
        {"role": "tool", "tool_call_id": "c1", "name": "inbox", "content": "leer"},
    ]
    blocks = llm._anthropic_blocks(messages)
    assistant = [b for b in blocks if b["role"] == "assistant"]
    assert assistant[0]["content"] == roh, assistant
    assert assistant[0]["content"][0]["signature"] == "sig-abc"


def test_leere_assistant_nachricht_faellt_raus() -> None:
    """N4: eine leere Nachricht würde die Session dauerhaft vergiften."""
    messages = [
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "", "tool_calls": []},
        {"role": "user", "content": "b"},
    ]
    blocks = llm._anthropic_blocks(messages)
    assert all(b["role"] != "assistant" for b in blocks), blocks
    # ... und die beiden user-Nachrichten sind verschmolzen
    assert len(blocks) == 1 and len(blocks[0]["content"]) == 2, blocks


def test_tool_result_steht_vorn_beim_verschmelzen() -> None:
    """Nach einem reparierten Abbruch treffen tool_result und Nutzertext aufeinander."""
    messages = [
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "name": "x", "input": {}}]},
        {"role": "tool", "tool_call_id": "c1", "name": "x", "content": "erg"},
        {"role": "user", "content": "und weiter"},
    ]
    blocks = llm._anthropic_blocks(messages)
    letzte = blocks[-1]
    assert letzte["role"] == "user"
    assert [b["type"] for b in letzte["content"]] == ["tool_result", "text"], letzte


def test_reparierte_history_bleibt_gueltig() -> None:
    """M2: abgebrochener Turn — offene tool_calls bekommen ein Ersatz-Ergebnis."""
    messages = [
        {"role": "user", "content": "los"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "name": "send_task", "input": {}},
            {"id": "c2", "name": "inbox", "input": {}}]},
        {"role": "tool", "tool_call_id": "c1", "name": "send_task", "content": "ok"},
    ]
    repariert = llm.repariere_history(messages)
    ids = {m["tool_call_id"] for m in repariert if m["role"] == "tool"}
    assert ids == {"c1", "c2"}, repariert
    # Das echte Ergebnis von c1 bleibt erhalten (Wissen um den Seiteneffekt!)
    c1 = [m for m in repariert if m.get("tool_call_id") == "c1"][0]
    assert c1["content"] == "ok", c1
    # Und die reparierte History lässt sich übersetzen, ohne dass etwas fehlt
    blocks = llm._anthropic_blocks(repariert)
    assert blocks[-1]["role"] == "user", blocks


def main() -> None:
    tests = [test_reparierte_history_bleibt_gueltig,
             test_tool_fehler_beendet_den_turn_nicht,
             test_langes_tool_ergebnis_wird_gekappt,
             test_rundenlimit_laesst_keine_offenen_tool_calls,
             test_thinking_bloecke_gehen_unveraendert_zurueck,
             test_leere_assistant_nachricht_faellt_raus,
             test_tool_result_steht_vorn_beim_verschmelzen]
    for test in tests:
        test()
        print(f"OK  {test.__name__}")
    print(f"alle {len(tests)} LLM-Loop-Tests grün")


if __name__ == "__main__":
    main()
