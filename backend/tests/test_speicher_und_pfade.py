"""Chat-Persistenz, gesperrte Workspace-Bereiche, atomare Settings.

Deckt N6 (SQLite-Verbindungen), N17 (Keys/chat.db nicht im Datei-Panel) und
N11 (Settings atomar) ab. Nur Standardlib:
    cd backend && python -m tests.test_speicher_und_pfade
"""
from __future__ import annotations

import importlib
import json
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path


def _mit_workspace(ws: Path, modulname: str):
    """Modul mit frisch gesetztem WORKSPACE_DIR laden."""
    os.environ["WORKSPACE_DIR"] = str(ws)
    os.environ["DATA_CONFIG_DIR"] = str(ws / "config")
    modul = importlib.import_module(modulname)
    return importlib.reload(modul)


def test_chat_store_roundtrip(ws: Path) -> None:
    """N6: speichern, laden, anzeigen, löschen — und keine offenen Verbindungen."""
    store = _mit_workspace(ws, "app.chat_store")
    store._init_done = False
    verlauf = [
        {"role": "user", "content": "hallo"},
        {"role": "assistant", "content": "moin", "tool_calls": [
            {"id": "c1", "name": "inbox", "input": {}}]},
        {"role": "tool", "tool_call_id": "c1", "name": "inbox", "content": "leer"},
    ]
    store.save("s1", verlauf)
    assert store.load("s1") == verlauf

    sessions = store.list_sessions()
    assert sessions and sessions[0]["id"] == "s1" and sessions[0]["title"] == "hallo"
    assert sessions[0]["messages"] == 3

    anzeige = store.display_messages("s1")
    assert [m["role"] for m in anzeige] == ["user", "assistant"], anzeige
    assert anzeige[1]["toolCalls"] == [{"name": "inbox"}], anzeige

    # Erneutes Speichern ersetzt, es wächst nichts an
    store.save("s1", verlauf[:1])
    assert len(store.load("s1")) == 1

    assert store.delete_session("s1") is True
    assert store.load("s1") == []
    assert store.delete_session("s1") is False

    # Die DB darf nicht gesperrt zurückbleiben (offene Verbindungen)
    con = sqlite3.connect(store.DB_PATH, timeout=1)
    con.execute("BEGIN IMMEDIATE").close()
    con.rollback()
    con.close()


def test_gesperrte_bereiche(ws: Path) -> None:
    """N17: keys/, ssl/ und chat.db sind im Datei-Panel weder sicht- noch lesbar."""
    files = _mit_workspace(ws, "app.files")
    (ws / "keys").mkdir()
    (ws / "keys" / "server_key").write_text("PRIVATER SCHLÜSSEL", encoding="utf-8")
    (ws / "ssl").mkdir()
    (ws / "chat.db").write_text("db", encoding="utf-8")
    (ws / "projects").mkdir()
    (ws / "projects" / "a.txt").write_text("hallo", encoding="utf-8")

    namen = {e["name"] for e in files.list_dir("")["entries"]}
    assert namen == {"projects"}, namen

    for pfad in ("keys", "keys/server_key", "ssl", "chat.db"):
        try:
            files.read_file(pfad)
        except files.FilesError as exc:
            assert "gesperrt" in str(exc), exc
        else:  # pragma: no cover
            raise AssertionError(f"{pfad} war lesbar!")
    # Schreiben/Löschen ebenso
    for aufruf in (lambda: files.write_file("keys/server_key", "x"),
                   lambda: files.delete("keys"),
                   lambda: files.save_upload("keys", "x.txt", b"x")):
        try:
            aufruf()
        except files.FilesError:
            pass
        else:  # pragma: no cover
            raise AssertionError("gesperrter Bereich war beschreibbar!")
    assert (ws / "keys" / "server_key").read_text(encoding="utf-8") == "PRIVATER SCHLÜSSEL"

    # Normale Pfade funktionieren weiter
    assert files.read_file("projects/a.txt")["content"] == "hallo"


def test_settings_atomar(ws: Path) -> None:
    """N11: settings.json wird ersetzt, nie halb geschrieben."""
    config = _mit_workspace(ws, "app.config")
    config.save_settings({"language": "en", "automatik": {"worker": True}})
    gelesen = json.loads(config.SETTINGS_PATH.read_text(encoding="utf-8"))
    assert gelesen["language"] == "en" and gelesen["automatik"] == {"worker": True}
    # Unbekannte Keys werden verworfen (Whitelist)
    config.save_settings({"boeser_key": 1})
    assert "boeser_key" not in json.loads(config.SETTINGS_PATH.read_text(encoding="utf-8"))
    # Keine .tmp-Reste
    assert list(config.DATA_CONFIG_DIR.glob("*.tmp")) == []


def test_ext_ziele_sind_eine_allowlist(ws: Path) -> None:
    """M16: nur eingetragene Fenster dürfen über den /ext/-Proxy laufen."""
    config = _mit_workspace(ws, "app.config")
    config.save_settings({"external_windows": [
        {"name": "vnc", "url": "192.168.1.50:6080/vnc.html"},
        {"name": "http", "url": "http://192.168.1.51:8080"},
        {"name": "extern", "url": "https://beispiel.invalid/app"},  # kein /ext/
        {"name": "kaputt", "url": ""},
    ]})
    ziele = config.erlaubte_ext_ziele()
    assert ziele == {"192.168.1.50:6080", "192.168.1.51:8080"}, ziele

    # Geprüft wird das echte Proxy-Ziel aus den nginx-Captures (X-Ext-Ziel)
    assert config.ist_erlaubtes_ext_ziel("192.168.1.50:6080") is True
    assert config.ist_erlaubtes_ext_ziel("192.168.1.51:8080") is True

    # Nicht eingetragenes LAN-Gerät, Unsinn und fehlender Header: alles nein.
    # (Der Traversal-Fall — normalisierte URI zeigt woanders hin als die rohe —
    # kann hier gar nicht mehr auftreten: wir zerlegen keine URI mehr, sondern
    # bekommen von nginx dasselbe ip:port, das proxy_pass anspricht.)
    for boese in (None, "", "192.168.1.99:80", "192.168.1.50:6081", "beispiel.invalid:80",
                  "192.168.1.50", "192.168.1.50:6080/../x", " 192.168.1.50:6080",
                  "/ext/192.168.1.50/6080/x"):
        assert config.ist_erlaubtes_ext_ziel(boese) is False, boese


def main() -> None:
    tests = [test_chat_store_roundtrip, test_gesperrte_bereiche, test_settings_atomar,
             test_ext_ziele_sind_eine_allowlist]
    alt_ws = os.environ.get("WORKSPACE_DIR")
    alt_cfg = os.environ.get("DATA_CONFIG_DIR")
    try:
        for test in tests:
            tmp = Path(tempfile.mkdtemp(prefix="speicher-test-"))
            try:
                test(tmp)
                print(f"OK  {test.__name__}")
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
    finally:
        for schluessel, wert in (("WORKSPACE_DIR", alt_ws), ("DATA_CONFIG_DIR", alt_cfg)):
            if wert is None:
                os.environ.pop(schluessel, None)
            else:
                os.environ[schluessel] = wert
    print(f"alle {len(tests)} Speicher-/Pfad-Tests grün")


if __name__ == "__main__":
    main()
