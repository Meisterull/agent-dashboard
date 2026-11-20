"""Vorgangs-Ansicht (Issue #58): einen Auftrag über alle Agenten verfolgen.

Größere Aufträge laufen über mehrere Agenten und Tage; im Dashboard sah man
davon nur Einzelteile (Tasks in Inboxen, Antworten in Outboxen, Ereignisse je
Agent). Der Schlüssel ist der `thread` eines Tasks (#37). Dieses Modul liest
— nur auf Anforderung, nie im Poll — alle Mailboxen und baut daraus:

  * `liste`: je Vorgang letzte Aktivität, beteiligte Agenten, Status
    (läuft / wartet auf Antwort / wartet auf Freigabe / Fehler / fertig),
    Summe aus Kosten und Laufzeit;
  * `zeitleiste`: agentenübergreifend Task erstellt → übernommen → Rückfrage
    und Antwort → Ergebnis, dazu Freigaben, Dateien und Auffälligkeiten aus
    dem Ereignis-Log.

Damit nichts aus der Ansicht fällt, trägt jeder Task ein Feld `vorgang`:
sein `thread`, sonst der Vorgang des Tasks, aus dem heraus er entstand
(`parent_task_id`, mcp_server.send_task vererbt beides), sonst ein eigener
(`einzel-<task>`). Abgeschlossene Tasks behalten die Angaben im Feld
`vorgang` ihrer Outbox-Antwort (mailbox._write_response).

Notizen je Vorgang („wartet auf Abend-Deploy") liegen in
`<workspace>/vorgaenge.json` — vom Menschen und per MCP `thread_note` pflegbar.

Nur Standardlib.
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from app import ereignisse, freigaben
from app.mailbox import _lese_ordner

NOTIZ_DATEI = "vorgaenge.json"
_SCHLUESSEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")
TEXT_DECKEL = 600
EREIGNIS_ARTEN = ("verweigert", "timeout", "aufgegeben", "fehlstart", "freigabe",
                  "send_file", "kosten", "rueckreihung")


class VorgangFehler(Exception):
    pass


def schluessel_fuer(env: dict[str, Any], task_id: str | None = None) -> str:
    """Vorgang eines Task-Envelopes bzw. einer Antwort."""
    v = env.get("vorgang")
    if isinstance(v, dict):                       # Outbox-Antwort
        v = v.get("vorgang") or v.get("thread")
    lauf = env.get("lauf") if isinstance(env.get("lauf"), dict) else {}
    tid = task_id or env.get("task_id") or env.get("id") or "?"
    return str(v or env.get("thread") or lauf.get("thread") or f"einzel-{str(tid)[-12:]}")


def _zeit(wert: Any) -> float:
    try:
        return datetime.fromisoformat(str(wert)).astimezone().timestamp()
    except (TypeError, ValueError):
        return 0.0


def _kurz(text: Any, n: int = TEXT_DECKEL) -> str:
    text = str(text or "").strip()
    return text if len(text) <= n else text[:n] + " …"


def _sammle(root: str | os.PathLike, seit: float, mit_archiv: bool = False) -> dict[str, dict[str, Any]]:
    """Alle Tasks (offen, in Arbeit, gescheitert, abgeschlossen) → je task_id
    ein Datensatz mit Vorgang, Agenten, Zeiten, Status, Kosten."""
    tasks: dict[str, dict[str, Any]] = {}
    try:
        ordner = [d for d in Path(root).iterdir() if d.is_dir()]
    except OSError:
        return tasks
    for d in ordner:
        agent = d.name
        for unter, zustand in (("inbox", "wartet"), ("inbox/.processing", "läuft"),
                               ("inbox/.failed", "fehler")):
            for pfad, env in _lese_ordner(d / unter):
                if env.get("kind", "task") != "task":
                    continue
                tid = str(env.get("id") or env.get("task_id") or pfad.stem)
                if _zeit(env.get("created_at")) < seit and pfad.stat().st_mtime < seit:
                    continue
                status = zustand
                if zustand == "läuft" and env.get("status") == "needs_confirm":
                    status = "wartet auf Antwort"
                tasks[tid] = {
                    "task_id": tid, "vorgang": schluessel_fuer(env, tid), "an": agent,
                    "von": env.get("sender"), "erstellt": env.get("created_at"),
                    "uebernommen": env.get("claimed_at"), "status": status,
                    "auftrag": _kurz(env.get("instruction")),
                    "parent": env.get("parent_task_id"), "rolle": env.get("rolle"),
                    "fragen": [{"frage": _kurz(f.get("frage"), 300)}
                               for f in env.get("open_questions") or []],
                    "nachtraege": [{"frage": _kurz(n.get("frage"), 300),
                                    "antwort": _kurz(n.get("antwort"), 300)}
                                   for n in env.get("nachtraege") or []],
                    "weckruf": bool(env.get("weckruf")),
                }
        antworten = [(p, e) for p, e in _lese_ordner(d / "outbox")]
        if mit_archiv:
            from app.mailbox import Mailbox
            try:
                antworten += [(None, e) for e in Mailbox(root, agent).archiv_lesen("outbox", 500)]
            except (ValueError, OSError):
                pass
        for pfad, r in antworten:
            tid = str(r.get("task_id") or "")
            if not tid or _zeit(r.get("responded_at")) < seit:
                continue
            v = r.get("vorgang") if isinstance(r.get("vorgang"), dict) else {}
            verbrauch = r.get("verbrauch") if isinstance(r.get("verbrauch"), dict) else {}
            lauf = r.get("lauf") if isinstance(r.get("lauf"), dict) else {}
            offen = tasks.get(tid, {})
            dauer = _zeit(r.get("responded_at")) - _zeit(v.get("claimed_at"))
            tasks[tid] = {
                **offen,
                "task_id": tid, "vorgang": schluessel_fuer(r, tid), "an": agent,
                "von": r.get("to") or v.get("sender") or offen.get("von"),
                "erstellt": v.get("created_at") or offen.get("erstellt"),
                "uebernommen": v.get("claimed_at") or offen.get("uebernommen"),
                "abgeschlossen": r.get("responded_at"),
                # Liegt der Task (wieder) offen, zählt dessen Zustand — sonst die Antwort
                "status": offen.get("status") if offen.get("status") in (
                    "läuft", "wartet", "wartet auf Antwort") else (
                    "fehler" if r.get("status") == "error" else "fertig"),
                "auftrag": offen.get("auftrag") or _kurz(v.get("titel") or r.get("instruction")),
                "ergebnis": _kurz(r.get("result")),
                "parent": v.get("parent_task_id") or offen.get("parent"),
                "kosten": verbrauch.get("total_cost_usd")
                if isinstance(verbrauch.get("total_cost_usd"), (int, float)) else None,
                "dauer": round(dauer) if _zeit(v.get("claimed_at")) and dauer > 0 else None,
                "modell": lauf.get("modell"),
                "eingeschraenkt": bool(lauf.get("verweigert")) or bool(lauf.get("timeout")),
                "aufgegeben": bool(r.get("aufgegeben")),
            }
    return tasks


def _status(teile: list[dict[str, Any]], wartet_freigabe: bool) -> str:
    zustaende = {t["status"] for t in teile}
    if wartet_freigabe:
        return "wartet auf Freigabe"
    if "wartet auf Antwort" in zustaende:
        return "wartet auf Antwort"
    if zustaende & {"läuft", "wartet"}:
        return "läuft"
    letzter = max(teile, key=lambda t: _zeit(t.get("abgeschlossen") or t.get("erstellt")))
    return "Fehler" if letzter["status"] == "fehler" else "fertig"


def notizen(root: str | os.PathLike) -> dict[str, dict[str, Any]]:
    try:
        d = json.loads((Path(root).parent / NOTIZ_DATEI).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return d if isinstance(d, dict) else {}


def setze_notiz(root: str | os.PathLike, vorgang: str, notiz: str, von: str) -> dict[str, Any]:
    if not _SCHLUESSEL_RE.fullmatch(vorgang or ""):
        raise VorgangFehler(f"ungültiger Vorgang: {vorgang!r}")
    alle = notizen(root)
    text = str(notiz or "").strip()[:500]
    if text:
        alle[vorgang] = {"notiz": text, "von": von,
                         "zeit": datetime.now().astimezone().isoformat(timespec="seconds")}
    else:
        alle.pop(vorgang, None)
    # alte Notizen begrenzen
    if len(alle) > 500:
        for k in sorted(alle, key=lambda k: str(alle[k].get("zeit")))[: len(alle) - 500]:
            del alle[k]
    pfad = Path(root).parent / NOTIZ_DATEI
    tmp = pfad.with_suffix(".tmp")
    tmp.write_text(json.dumps(alle, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, pfad)
    return alle.get(vorgang) or {}


def liste(root: str | os.PathLike, tage: float = 14.0, mit_archiv: bool = False) -> list[dict[str, Any]]:
    """Vorgänge der letzten `tage`, jüngste Aktivität zuerst."""
    seit = time.time() - tage * 86400
    tasks = _sammle(root, seit, mit_archiv)
    freigabe_tasks = {a.get("task_id") for a in freigaben.offene(root) if a.get("task_id")}
    merk = notizen(root)
    gruppen: dict[str, list[dict[str, Any]]] = {}
    for t in tasks.values():
        gruppen.setdefault(t["vorgang"], []).append(t)
    aus = []
    for vorgang, teile in gruppen.items():
        teile.sort(key=lambda t: _zeit(t.get("erstellt")))
        zeiten = [_zeit(t.get(f)) for t in teile for f in ("erstellt", "uebernommen", "abgeschlossen")]
        letzte = max(zeiten) if zeiten else 0
        aus.append({
            "vorgang": vorgang,
            "benannt": not vorgang.startswith("einzel-"),
            "titel": teile[0].get("auftrag", "").splitlines()[0][:160] if teile[0].get("auftrag") else "",
            "agenten": sorted({str(a) for t in teile for a in (t.get("von"), t.get("an")) if a}),
            "tasks": len(teile),
            "status": _status(teile, any(t["task_id"] in freigabe_tasks for t in teile)),
            "kosten": round(sum(t.get("kosten") or 0 for t in teile), 2),
            "dauer": sum(t.get("dauer") or 0 for t in teile),
            "letzte_aktivitaet": datetime.fromtimestamp(letzte).astimezone().isoformat(
                timespec="seconds") if letzte else None,
            "notiz": (merk.get(vorgang) or {}).get("notiz"),
        })
    aus.sort(key=lambda v: str(v["letzte_aktivitaet"] or ""), reverse=True)
    return aus


def zeitleiste(root: str | os.PathLike, vorgang: str, tage: float = 60.0) -> dict[str, Any]:
    """Alles zu EINEM Vorgang in zeitlicher Folge."""
    if not _SCHLUESSEL_RE.fullmatch(vorgang or ""):
        raise VorgangFehler(f"ungültiger Vorgang: {vorgang!r}")
    seit = time.time() - tage * 86400
    teile = [t for t in _sammle(root, seit, mit_archiv=True).values() if t["vorgang"] == vorgang]
    if not teile:
        raise VorgangFehler(f"Vorgang {vorgang!r} nicht gefunden (in den letzten {tage:.0f} Tagen).")
    ids = {t["task_id"] for t in teile}
    eintraege: list[dict[str, Any]] = []
    for t in teile:
        basis = {"task_id": t["task_id"], "von": t.get("von"), "an": t.get("an")}
        if t.get("erstellt"):
            eintraege.append({**basis, "zeit": t["erstellt"], "art": "task",
                              "text": t.get("auftrag"), "parent": t.get("parent"),
                              "rolle": t.get("rolle"), "weckruf": t.get("weckruf")})
        if t.get("uebernommen"):
            eintraege.append({**basis, "zeit": t["uebernommen"], "art": "uebernommen",
                              "text": f"{t.get('an')} hat übernommen"})
        for n in t.get("nachtraege") or []:
            eintraege.append({**basis, "zeit": t.get("uebernommen") or t.get("erstellt"),
                              "art": "rueckfrage", "text": n["frage"], "antwort": n["antwort"]})
        for f in t.get("fragen") or []:
            eintraege.append({**basis, "zeit": t.get("uebernommen") or t.get("erstellt"),
                              "art": "rueckfrage", "text": f["frage"], "offen": True})
        if t.get("abgeschlossen"):
            eintraege.append({**basis, "zeit": t["abgeschlossen"], "art": "ergebnis",
                              "status": t["status"], "text": t.get("ergebnis"),
                              "kosten": t.get("kosten"), "dauer": t.get("dauer"),
                              "modell": t.get("modell"),
                              "eingeschraenkt": t.get("eingeschraenkt"),
                              "aufgegeben": t.get("aufgegeben")})
    for agent in sorted({str(t["an"]) for t in teile if t.get("an")}):
        try:
            log = ereignisse.lies(root, agent, limit=2000)["eintraege"]
        except ValueError:
            continue
        for e in log:
            if e.get("task_id") in ids and e.get("art") in EREIGNIS_ARTEN:
                eintraege.append({"zeit": e["zeit"], "art": "ereignis", "an": agent,
                                  "task_id": e["task_id"], "text": e.get("text"),
                                  "schwere": e.get("schwere"), "ereignis": e.get("art")})
    eintraege.sort(key=lambda e: _zeit(e.get("zeit")))
    kopf = next((v for v in liste(root, tage, mit_archiv=True) if v["vorgang"] == vorgang), {})
    return {**kopf, "vorgang": vorgang, "eintraege": eintraege,
            "notiz_von": (notizen(root).get(vorgang) or {}).get("von")}
