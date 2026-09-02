"""Geplante Tasks (Dashboard-Paket, Stufe 2).

Pläne liegen in DATA_CONFIG_DIR/zeitplaene.yaml und werden über den
Zeitpläne-Dialog im Agenten-Panel gepflegt (PUT ersetzt die Liste). Der
Planer läuft als Loop im API-Prozess (Muster: Mailbox-Pflege in main.py —
bewusst NICHT im Watcher, der ist der Prozess, der stirbt) und postet zur
fälligen Zeit einen GANZ NORMALEN Task in die Mailbox des Agenten: Automatik,
Rückfragen, Push und der Verbrauchszähler greifen damit von selbst. Absender
ist der `orchestrator` — das Ergebnis landet also als response beim Menschen.

VERPASSTE TERMINE (bewusste Entscheidung, 02.09.2026): verfallen als Default.
War der Server zur Sollzeit aus, läuft der Termin NICHT nach (Kulanz:
PLANER_KULANZ, Default 600 s — deckt Tick-Takt und kurze Neustarts). Je Plan
lässt sich `nachholen: true` setzen: dann läuft höchstens EIN Nachzügler
(der jüngste verpasste Soll-Termin), nie eine Salve.

Der globale Not-Aus stoppt Watcher, nicht den Planer: er postet nur — ohne
Automatik bleibt der Task in der Inbox liegen (gewollt: die Mailbox IST der
Puffer).

Ein Plan:
    - name: nachtreview          # eindeutig, wie Rollennamen (kleinbuchstaben)
      agent: werkstatt
      rolle: review              # optional; beim Posten aufgelöst (rollen.py)
      project: repo              # optional (Unterverzeichnis, Issue #19)
      instruction: |
        Sieh dir die Commits seit gestern an …
      zeit: "07:00"              # lokale Serverzeit (TZ in docker-compose!)
      tage: [mo, di, mi, do, fr] # leer/weg = täglich
      an: true
      nachholen: false
      letzter_lauf: …            # stempelt der Planer (Soll-Zeitpunkt)
"""
from __future__ import annotations

import asyncio
import os
import re
from datetime import datetime, time as dt_time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app import rollen, verbrauch
from app.config import (DATA_CONFIG_DIR, _atomic_write_text, load_agents_full,
                        load_settings)
from app.mailbox import AGENT_NAME_RE, ORCHESTRATOR, Mailbox, Task, new_id

ZEITPLAENE_YAML = DATA_CONFIG_DIR / "zeitplaene.yaml"
WORKSPACE = Path(os.environ.get("WORKSPACE_DIR", "/workspace"))
MAILBOX_ROOT = WORKSPACE / "mailboxes"

PLANER_INTERVALL = float(os.environ.get("PLANER_INTERVALL", "30"))
# Wie weit ein Termin ohne `nachholen` in der Vergangenheit liegen darf und
# trotzdem noch läuft — deckt Tick-Takt und kurze Container-Neustarts ab.
PLANER_KULANZ = float(os.environ.get("PLANER_KULANZ", "600"))

PLAN_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
ZEIT_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
TAGE = ["mo", "di", "mi", "do", "fr", "sa", "so"]  # Index = datetime.weekday()


class ZeitplanFehler(ValueError):
    """Ungültiger Plan — mit Klartext fürs UI."""


def lade_plaene() -> tuple[list[dict[str, Any]], str | None]:
    """(plaene, fehler) — eine kaputte Datei liefert [] plus den Grund,
    statt still leer zu wirken (sonst 'verschwinden' alle Pläne)."""
    if not ZEITPLAENE_YAML.exists():
        return [], None
    try:
        import yaml  # lazy wie in config.py

        data = yaml.safe_load(ZEITPLAENE_YAML.read_text(encoding="utf-8")) or {}
        plaene = [p for p in (data.get("plaene", []) or []) if isinstance(p, dict)]
        # Milde Normalisierung (Review P1-4): handgepflegtes YAML darf
        # `tage:` leer lassen — `None` statt Liste crashte das Frontend.
        for p in plaene:
            if not isinstance(p.get("tage"), list):
                p["tage"] = []
        return plaene, None
    except Exception as exc:  # noqa: BLE001
        return [], f"zeitplaene.yaml nicht lesbar: {exc}"


def _pruefe_plan(plan: dict[str, Any]) -> dict[str, Any]:
    name = str(plan.get("name") or "")
    if not PLAN_NAME_RE.fullmatch(name):
        raise ZeitplanFehler(
            f"ungültiger Plan-Name: {name!r} (kleinbuchstaben, ziffern, - und _)")
    agent = str(plan.get("agent") or "")
    if not AGENT_NAME_RE.fullmatch(agent):
        raise ZeitplanFehler(f"{name}: ungültiger Agent {agent!r}")
    if not str(plan.get("instruction") or "").strip():
        raise ZeitplanFehler(f"{name}: instruction fehlt")
    zeit = str(plan.get("zeit") or "")
    if not ZEIT_RE.fullmatch(zeit):
        raise ZeitplanFehler(f"{name}: zeit muss HH:MM sein, nicht {zeit!r}")
    tage = plan.get("tage") or []
    if not isinstance(tage, (list, tuple)):
        raise ZeitplanFehler(f"{name}: tage muss eine Liste sein")
    tage = [str(t).lower() for t in tage]
    fremd = [t for t in tage if t not in TAGE]
    if fremd:
        raise ZeitplanFehler(f"{name}: unbekannte Tage {fremd} (erlaubt: {', '.join(TAGE)})")
    sauber = {
        "name": name,
        "agent": agent,
        "instruction": str(plan["instruction"]),
        "zeit": zeit,
        "tage": tage,
        "an": bool(plan.get("an", True)),
        "nachholen": bool(plan.get("nachholen", False)),
    }
    for feld in ("rolle", "project", "letzter_lauf"):
        if plan.get(feld):
            sauber[feld] = str(plan[feld])
    return sauber


_DATEI_KOPF = (
    "# Geplante Tasks — gepflegt über das Dashboard (Agenten-Panel → Zeitpläne).\n"
    "# `letzter_lauf` stempelt der Planer; Doku: docs/REFERENZ.md.\n"
)


def _schreibe_roh(plaene: list[dict[str, Any]]) -> None:
    """Liste OHNE Nachbar-Validierung schreiben — der Stempel-Pfad (Review
    P0-5): ein von Hand zerschriebener Nachbar-Plan darf den Stempel eines
    gerade gelaufenen Plans nicht blockieren, sonst feuert der bei jedem
    Tick erneut (Task-Salve)."""
    import yaml

    # Über den Dateipfad statt die Konstante: bleibt korrekt, wenn Tests
    # ZEITPLAENE_YAML umbiegen.
    ZEITPLAENE_YAML.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(
        ZEITPLAENE_YAML,
        _DATEI_KOPF + yaml.safe_dump({"plaene": plaene}, allow_unicode=True,
                                     sort_keys=False),
    )


def speichere_plaene(plaene: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Liste validieren und atomar schreiben.

    `letzter_lauf` ist SERVER-Wahrheit (Review P0-6): Der Dialog hält die
    Liste im Browser-State und schickt sie samt — womöglich veraltetem —
    Stempel zurück; ein alter Stempel ließe den Termin erneut feuern
    (Doppelfeuer-Race, reproduziert). Client-Werte werden darum verworfen
    und immer aus dem gespeicherten Stand übernommen."""
    sauber = [_pruefe_plan(p) for p in plaene]
    namen = [p["name"] for p in sauber]
    doppelt = {n for n in namen if namen.count(n) > 1}
    if doppelt:
        raise ZeitplanFehler(f"doppelte Plan-Namen: {', '.join(sorted(doppelt))}")
    alt, _ = lade_plaene()
    letzter = {p.get("name"): p.get("letzter_lauf") for p in alt}
    for p in sauber:
        p.pop("letzter_lauf", None)
        if letzter.get(p["name"]):
            p["letzter_lauf"] = letzter[p["name"]]
    _schreibe_roh(sauber)
    return sauber


# --- Fälligkeit -------------------------------------------------------------

def _zone():
    """Kalender-Zeitzone des Planers (Review P1-6): eine ECHTE ZoneInfo statt
    des fixen UTC-Offsets von astimezone() — sonst rechnete der Rückblick über
    eine Sommerzeit-Umstellung mit dem falschen Offset (Pläne zwischen 02:00
    und 02:59 liefen bei der Rückstellung doppelt, bei der Vorstellung nie)."""
    try:
        return ZoneInfo(os.environ.get("TZ") or "Europe/Berlin")
    except Exception:  # noqa: BLE001 — kaputte TZ-Angabe: fixer Offset als Netz
        return datetime.now().astimezone().tzinfo


def _juengster_soll(plan: dict[str, Any], jetzt: datetime) -> datetime | None:
    """Der jüngste Soll-Zeitpunkt <= jetzt an einem erlaubten Tag (max. 7 Tage
    zurück — weiter reicht der Wochentagsfilter nicht). Gerechnet wird über
    KALENDERTAGE in der Planer-Zone (P1-6), nicht über Offset-Arithmetik."""
    m = ZEIT_RE.fullmatch(str(plan.get("zeit") or ""))
    if not m:
        return None
    stunde, minute = int(m.group(1)), int(m.group(2))
    tage = [str(t).lower() for t in plan.get("tage") or []] or TAGE
    zone = _zone()
    jetzt = jetzt.astimezone(zone)
    for zurueck in range(8):
        tag = (jetzt - timedelta(days=zurueck)).date()
        soll = datetime.combine(tag, dt_time(stunde, minute), tzinfo=zone)
        if TAGE[soll.weekday()] not in tage:
            continue
        if soll <= jetzt:
            return soll
    return None


def ist_faellig(plan: dict[str, Any], jetzt: datetime,
                kulanz: float = PLANER_KULANZ) -> datetime | None:
    """Soll-Zeitpunkt, wenn der Plan JETZT laufen soll — sonst None.

    Verfallen als Default: ohne `nachholen` läuft nur ein Termin, der
    höchstens `kulanz` Sekunden zurückliegt. Mit `nachholen` läuft der
    jüngste verpasste Termin nach — genau EINER, nie eine Salve (ältere
    verpasste sind durch den Stempel auf den jüngsten mit erledigt)."""
    if not plan.get("an", True):
        return None
    soll = _juengster_soll(plan, jetzt)
    if soll is None:
        return None
    letzter = plan.get("letzter_lauf")
    if letzter:
        try:
            lz = datetime.fromisoformat(str(letzter))
            if lz.tzinfo is None:
                lz = lz.replace(tzinfo=soll.tzinfo)
            # Schon gelaufen? Absolutzeit-Vergleich PLUS Kalendertag (P1-6):
            # Bei der Uhr-Rückstellung existiert 02:xx zweimal — absolut liegt
            # der zweite Soll NACH dem Stempel, aber ein Plan läuft höchstens
            # einmal pro Kalendertag.
            if lz >= soll or lz.astimezone(soll.tzinfo).date() == soll.date():
                return None
        except ValueError:
            pass  # kaputter Stempel: lieber laufen als für immer schweigen
    if (jetzt - soll).total_seconds() <= kulanz:
        return soll
    return soll if plan.get("nachholen") else None


# --- Ausführen --------------------------------------------------------------

def _bekannte_agenten() -> set[str]:
    namen = set()
    if MAILBOX_ROOT.exists():
        namen |= {p.name for p in MAILBOX_ROOT.iterdir() if p.is_dir()}
    try:
        namen |= {a["name"] for a in load_agents_full() if a.get("name")}
    except Exception:  # noqa: BLE001
        pass
    return namen


def _stempel(name: str, soll_iso: str) -> None:
    """Termin-Stempel setzen — über den ROHEN Schreibpfad (Review P0-5):
    speichere_plaene() validiert die ganze Liste, und ein kaputter
    Nachbar-Plan hätte den Stempel verschluckt → Task-Salve. Scheitert das
    Schreiben trotzdem (Platte, Rechte), wird es LAUT gemeldet."""
    plaene, fehler = lade_plaene()
    if fehler:
        print(f"[planer] Stempel für {name} nicht möglich: {fehler}", flush=True)
        return
    for p in plaene:
        if p.get("name") == name:
            p["letzter_lauf"] = soll_iso
    try:
        _schreibe_roh(plaene)
    except OSError as exc:
        print(f"[planer] Stempel für {name} fehlgeschlagen: {exc}", flush=True)


def _poste(plan: dict[str, Any], soll: datetime,
           schwelle: int = 0) -> dict[str, Any]:
    """Einen fälligen Plan als normalen Task posten und den Termin stempeln.

    Gestempelt wird AUCH bei Fehlern (unbekannter Agent, kaputte Rolle,
    erreichte Verbrauchsschwelle): ein Termin = höchstens ein Versuch —
    sonst feuert das Log alle 30 s."""
    soll_iso = soll.isoformat(timespec="seconds")
    name = plan.get("name") or "?"
    agent = str(plan.get("agent") or "")
    _stempel(name, soll_iso)
    if agent not in _bekannte_agenten():
        print(f"[planer] {name}: Agent {agent!r} unbekannt — Termin übersprungen",
              flush=True)
        return {"fehler": f"Agent {agent!r} unbekannt"}
    # Verbrauchsschwelle (St.3): über dem 5-h-Budget pausieren GEPLANTE Tasks
    # dieses Agenten — der Termin verfällt (Stempel steht schon). Der
    # ▶-Sofort-Knopf umgeht die Schwelle bewusst (jetzt_ausfuehren).
    if schwelle > 0 and verbrauch.ist_ueber_schwelle(agent, schwelle):
        print(f"[planer] {name}: Verbrauchsschwelle erreicht ({agent}, "
              f"{schwelle} Tokens/5 h) — Termin pausiert", flush=True)
        return {"fehler": "Verbrauchsschwelle erreicht — geplanter Task pausiert"}
    rollen_felder: dict[str, Any] = {}
    if plan.get("rolle"):
        try:
            rollen_felder = rollen.rolle_fuer_task(str(plan["rolle"]))
        except rollen.RollenFehler as exc:
            print(f"[planer] {name}: {exc} — Termin übersprungen", flush=True)
            return {"fehler": str(exc)}
    task = Task(
        task_id=new_id("task"),
        agent=agent,
        instruction=str(plan.get("instruction") or ""),
        project=plan.get("project"),
        sender=ORCHESTRATOR,
        **rollen_felder,
    )
    Mailbox(MAILBOX_ROOT, agent).put_task(task)
    print(f"[planer] {name}: Task {task.task_id} an {agent} gepostet "
          f"(Termin {soll_iso})", flush=True)
    return {"task_id": task.task_id, "agent": agent, "termin": soll_iso}


def jetzt_ausfuehren(name: str) -> dict[str, Any]:
    """Einen Plan sofort laufen lassen (Test-Knopf im Dialog)."""
    plaene, fehler = lade_plaene()
    if fehler:
        raise ZeitplanFehler(fehler)
    plan = next((p for p in plaene if p.get("name") == name), None)
    if plan is None:
        raise ZeitplanFehler(f"kein Plan namens {name!r}")
    return _poste(plan, datetime.now().astimezone())


def tick(jetzt: datetime | None = None) -> list[dict[str, Any]]:
    """Ein Planer-Durchlauf — separat aufrufbar (Tests)."""
    jetzt = jetzt or datetime.now().astimezone()
    plaene, fehler = lade_plaene()
    if fehler:
        print(f"[planer] {fehler}", flush=True)
        return []
    schwelle = int(load_settings().get("verbrauch_schwelle_5h") or 0)
    berichte = []
    for plan in plaene:
        try:
            soll = ist_faellig(plan, jetzt)
        except Exception as exc:  # noqa: BLE001 — ein kaputter Plan killt nicht alle
            print(f"[planer] {plan.get('name')}: {exc}", flush=True)
            continue
        if soll is not None:
            berichte.append(_poste(plan, soll, schwelle))
    return berichte


_task: asyncio.Task | None = None


async def _schleife() -> None:
    print("[planer] gestartet", flush=True)
    while True:
        await asyncio.sleep(PLANER_INTERVALL)
        try:
            # Kleine Dateien, synchron im Loop: serialisiert damit von selbst
            # gegen die PUT-/jetzt-Endpunkte (gleicher Event-Loop, kein Race
            # auf zeitplaene.yaml).
            tick()
        except Exception as exc:  # noqa: BLE001 — der Planer darf nie sterben
            print(f"[planer] Fehler: {exc}", flush=True)


def start() -> None:
    global _task
    if _task is None:
        _task = asyncio.get_event_loop().create_task(_schleife())


def stop() -> None:
    global _task
    if _task is not None:
        _task.cancel()
        _task = None
