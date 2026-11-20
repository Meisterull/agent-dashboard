"""Kostendeckel der Automatik (Issue #55).

Zwei optionale Grenzen je Agent in agents.yaml (US-Dollar, aus den
`total_cost_usd` der Läufe — dieselbe Quelle wie der Verbrauchszähler):

  max_kosten_tag   Ist die Tagessumme erreicht, bricht nichts hart ab: die
                   Automatik nimmt keinen NEUEN Task mehr an (claim_task des
                   Watchers wird abgelehnt), und der Mensch bekommt EINE
                   Rückfrage mit den Knöpfen „weiter"/„stopp" (Banner + Push,
                   wie #30). „weiter" gibt ein weiteres Tagesbudget frei,
                   „stopp" hält bis Mitternacht. Der Handbetrieb bleibt frei.
  max_kosten_lauf  Ein Lauf, der mehr kostet, steht als Warnung im
                   Ereignis-Log. Abgebrochen wird er nicht — die Kosten
                   stehen erst im result-Event fest, also nach dem Lauf.

Zustand je Mailbox in `<mailbox>/.kosten.json`. Aufgerufen im Reconcile-Takt
des Automatik-Managers; `gesperrt()` liest der MCP-Prozess beim Claim.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from app import ereignisse
from app.mailbox import ORCHESTRATOR, Mailbox, _lese_ordner, atomic_write_json

JA = ("weiter", "ja", "yes", "ok", "continue")


def zahl(wert: Any) -> float:
    """Grenze aus agents.yaml → positive Zahl oder 0 (= keine Grenze)."""
    try:
        z = float(wert)
    except (TypeError, ValueError):
        return 0.0
    return z if z > 0 and z == z and z != float("inf") else 0.0


def _pfad(root: str | os.PathLike, agent: str) -> Path:
    return Path(root) / agent / ".kosten.json"


def _lade(root: str | os.PathLike, agent: str) -> dict[str, Any]:
    try:
        z = json.loads(_pfad(root, agent).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        z = {}
    return z if isinstance(z, dict) else {}


def gesperrt(root: str | os.PathLike, agent: str) -> dict[str, Any] | None:
    """Zustand der Tagessperre, wenn sie HEUTE gilt — sonst None."""
    z = _lade(root, agent)
    if z.get("gesperrt") and z.get("tag") == datetime.now().astimezone().date().isoformat():
        return z
    return None


def _heute(root: str | os.PathLike, agent: str) -> tuple[float, list[dict[str, Any]]]:
    """Tagessumme und die heutigen Antworten mit Kosten (aus der Outbox)."""
    heute = datetime.now().astimezone().date()
    summe, laeufe = 0.0, []
    outbox = Path(root) / agent / "outbox"
    grenze = time.time() - 2 * 86400
    if not outbox.is_dir():
        return 0.0, []
    for p in outbox.glob("*-response.json"):
        try:
            if p.stat().st_mtime < grenze:
                continue
            r = json.loads(p.read_text(encoding="utf-8"))
            wann = datetime.fromisoformat(str(r.get("responded_at"))).astimezone()
        except (OSError, ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError):
            continue
        kosten = (r.get("verbrauch") or {}).get("total_cost_usd") if isinstance(
            r.get("verbrauch"), dict) else None
        if wann.date() != heute or not isinstance(kosten, (int, float)) or kosten != kosten:
            continue
        summe += float(kosten)
        laeufe.append({"task_id": r.get("task_id"), "kosten": float(kosten)})
    return summe, laeufe


def _antwort_auf(box: Mailbox, frage_id: str) -> tuple[Path, str] | None:
    for ordner in (box.inbox, box.archive):
        for p, env in _lese_ordner(ordner):
            if env.get("kind") == "answer" and env.get("reply_to") == frage_id:
                return p, str(env.get("text") or "")
    return None


def pruefe(root: str | os.PathLike, agent: str, max_tag: Any = None,
           max_lauf: Any = None) -> dict[str, Any]:
    """Einmal je Takt: Tagessumme gegen die Grenze, Antwort auf die Rückfrage
    auswerten, teure Einzelläufe melden. → {"heute", "grenze", "gesperrt"}."""
    max_tag, max_lauf = zahl(max_tag), zahl(max_lauf)
    aus = {"heute": 0.0, "grenze": max_tag, "gesperrt": False}
    if not (max_tag or max_lauf) or not (Path(root) / agent).is_dir():
        return aus
    tag = datetime.now().astimezone().date().isoformat()
    summe, laeufe = _heute(root, agent)
    aus["heute"] = round(summe, 2)
    alt = _lade(root, agent)
    z = alt if alt.get("tag") == tag else {"tag": tag}
    vorher = json.dumps(z, sort_keys=True)
    # --- teure Einzelläufe: je Lauf genau eine Warnung -----------------------
    if max_lauf:
        gemeldet = list(z.get("laeufe_gemeldet") or [])
        for lauf in laeufe:
            if lauf["kosten"] > max_lauf and lauf["task_id"] not in gemeldet:
                gemeldet.append(lauf["task_id"])
                ereignisse.schreibe(
                    root, agent, "kosten",
                    f"Lauf kostete {lauf['kosten']:.2f} $ (Grenze je Lauf {max_lauf:.2f} $)",
                    schwere="warnung", task_id=lauf["task_id"], kosten=lauf["kosten"],
                    grenze=max_lauf)
        z["laeufe_gemeldet"] = gemeldet[-200:]
    # --- Tagesgrenze ---------------------------------------------------------
    if max_tag:
        erlaubt = float(z.get("erlaubt") or max_tag)
        box = Mailbox(root, agent)
        if z.get("gesperrt") and z.get("frage_id") and not z.get("entschieden"):
            antwort = _antwort_auf(box, z["frage_id"])
            if antwort is not None:
                pfad, text = antwort
                weiter = text.strip().lower().startswith(JA)
                if weiter:
                    erlaubt = summe + max_tag
                    z.update(gesperrt=False, frage_id=None)
                else:
                    z["entschieden"] = "stopp"
                ereignisse.schreibe(
                    root, agent, "kosten",
                    (f"Tagesgrenze freigegeben — weiter bis {erlaubt:.2f} $" if weiter
                     else "Tagesgrenze: Automatik bleibt bis Mitternacht angehalten"),
                    schwere="info", heute=round(summe, 2))
                if pfad.parent == box.inbox:
                    try:
                        box.mark_read(pfad.stem)  # sonst liegt die Antwort ewig dort
                    except (ValueError, OSError):
                        pass
        if summe >= erlaubt and not z.get("gesperrt"):
            frage = None
            try:
                frage = Mailbox(root, ORCHESTRATOR).post({
                    "kind": "question", "sender": agent, "to": ORCHESTRATOR,
                    "status": "needs_confirm", "options": ["weiter", "stopp"],
                    "weckt": False, "kosten_frage": True,
                    "text": (f"{agent} hat heute {summe:.2f} $ verbraucht (Tagesgrenze "
                             f"{erlaubt:.2f} $). Die Automatik nimmt keine neuen Tasks an. "
                             f"Weiter (weitere {max_tag:.2f} $ freigeben) oder für heute "
                             f"stoppen?"),
                })
            except (ValueError, OSError):
                pass
            z.update(gesperrt=True, frage_id=(frage or {}).get("id"), entschieden=None)
            ereignisse.schreibe(
                root, agent, "kosten",
                f"Tagesgrenze erreicht: {summe:.2f} $ von {erlaubt:.2f} $ — Automatik "
                f"nimmt keine neuen Tasks an, Rückfrage an den Menschen",
                schwere="warnung", heute=round(summe, 2), grenze=erlaubt)
        z["erlaubt"] = erlaubt
        aus["grenze"] = erlaubt
        aus["gesperrt"] = bool(z.get("gesperrt"))
    if json.dumps(z, sort_keys=True) != vorher or alt.get("tag") != tag:
        try:
            atomic_write_json(_pfad(root, agent), z)
        except OSError:
            pass
    return aus
