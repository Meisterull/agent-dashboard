"""Deploy-Panel / Freigabe-Pakete (Issue #61).

Der wichtigste Ablauf zwischen den Agenten war im Dashboard unsichtbar: ein
Agent stellt ein Paket aus („Deploy-Intent": was soll in welchem Stand live
gehen), ein anderer übernimmt und spielt es ein. Wer wissen wollte, was heute
Abend live geht, musste Antwort-JSONs und Dateien auf zwei Maschinen lesen.

Das Dashboard deployt NICHT — es zeigt nur an. Die Agenten (bzw. ihre
Skripte über den Agenten) melden den Stand per MCP-Tool `deploy_status`;
bewusst allgemein gehalten („Paket" mit Token, Repos, Flags, Tasks), damit es
auch für andere Ziele taugt (Konfigurationen, Releases).

  phase   ausgestellt → uebernommen → gelaufen → live      (Erfolgsweg)
          fehler · zurueckgezogen                           (Abbruch)
  token   Schlüssel des Pakets; „ausgestellt" unter demselben Token zählt
          die Ausstellungen hoch („zum 5. Mal ausgestellt")
  repos   [{name, sha, message?, vorhanden?}] — erwarteter Stand je Repo
  flags   freie Kennzeichen, z. B. MIGRATE, IMAGE-NEUBAU, NEUSTART
  tasks / threads   was in dem Paket steckt (Task-IDs, Vorgänge #58)

Ablage: `<workspace>/pakete.json` (API- und MCP-Prozess schreiben beide →
Datei-Lock). Nur Standardlib.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

WORKSPACE = Path(os.environ.get("WORKSPACE_DIR", "/workspace"))
DATEI = "pakete.json"
PHASEN = ("ausgestellt", "uebernommen", "gelaufen", "live", "fehler", "zurueckgezogen")
OFFEN = ("ausgestellt", "uebernommen", "gelaufen")   # noch nicht abgeschlossen
MAX_PAKETE = 300
_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_SHA_RE = re.compile(r"^[0-9a-fA-F]{4,64}$")


class PaketFehler(Exception):
    pass


def _jetzt() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


@contextmanager
def _gesperrt() -> Iterator[Path]:
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    with open(WORKSPACE / ("." + DATEI + ".lock"), "a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield WORKSPACE / DATEI
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _lade(pfad: Path) -> dict[str, dict[str, Any]]:
    try:
        d = json.loads(pfad.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return d if isinstance(d, dict) else {}


def _texte(wert: Any, grenze: int = 50, laenge: int = 120) -> list[str]:
    if isinstance(wert, str):
        wert = re.split(r"[,\n]", wert)
    return [str(x).strip()[:laenge] for x in (wert or []) if str(x).strip()][:grenze]


def _repos(wert: Any) -> list[dict[str, Any]]:
    aus = []
    for r in (wert or [])[:100]:
        if isinstance(r, str):
            name, _, sha = r.partition("@")
            r = {"name": name, "sha": sha}
        if not isinstance(r, dict) or not str(r.get("name") or "").strip():
            continue
        sha = str(r.get("sha") or "").strip()
        if sha and not _SHA_RE.fullmatch(sha):
            raise PaketFehler(f"kein gültiger Commit-SHA für {r.get('name')}: {sha!r}")
        aus.append({"name": str(r["name"]).strip()[:120], "sha": sha or None,
                    "message": str(r.get("message") or "").strip()[:200] or None,
                    "vorhanden": r.get("vorhanden") if isinstance(r.get("vorhanden"), bool) else None})
    return aus


def melde(token: str, phase: str, von: str, title: str | None = None,
          target: str | None = None, repos: Any = None, flags: Any = None,
          tasks: Any = None, threads: Any = None, note: str | None = None) -> dict[str, Any]:
    """Stand eines Pakets melden. Angaben (repos, flags, …) ersetzen die
    bisherigen nur, wenn sie mitkommen — eine reine Phasenmeldung
    („gelaufen") lässt den Inhalt stehen."""
    token = str(token or "").strip()
    phase = str(phase or "").strip().lower().replace("ü", "ue")
    if not _TOKEN_RE.fullmatch(token):
        raise PaketFehler(f"ungültiger Token: {token!r} (Buchstaben, Ziffern, . _ -)")
    if phase not in PHASEN:
        raise PaketFehler(f"unbekannte Phase {phase!r} — erlaubt: {', '.join(PHASEN)}")
    with _gesperrt() as pfad:
        alle = _lade(pfad)
        p = alle.get(token) or {"token": token, "erstellt": _jetzt(), "ausstellungen": 0,
                                "verlauf": [], "repos": [], "flags": [], "tasks": [],
                                "vorgaenge": []}
        if phase == "ausgestellt":
            p["ausstellungen"] = int(p.get("ausstellungen") or 0) + 1
            p["ausgestellt_von"] = von
            p["ausgestellt"] = _jetzt()
        if title is not None:
            p["titel"] = str(title).strip()[:200] or None
        if target is not None:
            p["ziel"] = str(target).strip()[:120] or None
        if repos is not None:
            p["repos"] = _repos(repos)
        if flags is not None:
            p["flags"] = [f.upper() for f in _texte(flags, 20, 40)]
        if tasks is not None:
            p["tasks"] = _texte(tasks)
        if threads is not None:
            p["vorgaenge"] = _texte(threads)
        p["phase"] = phase
        p["geaendert"] = _jetzt()
        p["verlauf"] = (p.get("verlauf") or [])[-49:] + [
            {"zeit": _jetzt(), "phase": phase, "von": von,
             "notiz": str(note or "").strip()[:500] or None}]
        alle[token] = p
        if len(alle) > MAX_PAKETE:  # älteste ABGESCHLOSSENE vergessen
            fertig = sorted((k for k, v in alle.items() if v.get("phase") not in OFFEN),
                            key=lambda k: str(alle[k].get("geaendert")))
            for k in fertig[: len(alle) - MAX_PAKETE]:
                del alle[k]
        tmp = pfad.with_suffix(".tmp")
        tmp.write_text(json.dumps(alle, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, pfad)
    return p


def liste(nur_offene: bool = False, limit: int = 50) -> list[dict[str, Any]]:
    """Offene Pakete zuerst (älteste oben — die blockieren), dann die
    abgeschlossenen, jüngste zuerst."""
    alle = list(_lade(WORKSPACE / DATEI).values())
    offen = sorted((p for p in alle if p.get("phase") in OFFEN),
                   key=lambda p: str(p.get("ausgestellt") or p.get("erstellt")))
    if nur_offene:
        return offen
    rest = sorted((p for p in alle if p.get("phase") not in OFFEN),
                  key=lambda p: str(p.get("geaendert")), reverse=True)
    return offen + rest[: max(0, limit - len(offen))]


def kurztext(p: dict[str, Any]) -> str:
    """Eine Zeile für Push und Tagesbericht: „DV: 7 Repos, MIGRATE + IMAGE-NEUBAU, 4 Vorgänge"."""
    teile = []
    if p.get("repos"):
        teile.append(f"{len(p['repos'])} Repos")
    if p.get("flags"):
        teile.append(" + ".join(p["flags"]))
    n = len(p.get("vorgaenge") or []) or len(p.get("tasks") or [])
    if n:
        teile.append(f"{n} {'Vorgänge' if p.get('vorgaenge') else 'Tasks'}")
    if int(p.get("ausstellungen") or 0) > 1:
        teile.append(f"{p['ausstellungen']}. Ausstellung")
    return f"{p.get('titel') or p['token']}: " + (", ".join(teile) or p.get("phase", ""))
