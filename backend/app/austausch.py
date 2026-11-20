"""Austausch-Ordner je Maschine: Dateien von Maschine A in den Ordner von B.

Bis hierhin konnten Agenten einander nur TEXT reichen (write_project_file /
read_project_file) — der Inhalt lief dabei durch den LLM-Kontext, Binäres und
Großes ging gar nicht. Das Dashboard hält aber ohnehin zu jeder SSH-Maschine
eine SFTP-Verbindung (remote_files). Dieses Modul nutzt sie als Drehscheibe:
es liest auf Maschine A und schreibt auf Maschine B, blockweise, ohne dass je
ein Modell den Inhalt sieht.

Regeln, die hier durchgesetzt werden:

  * Eine Maschine empfängt nur, wenn für sie ein Austausch-Ordner
    eingeschaltet ist (settings.json `austausch: {name: {ordner, pfad}}`,
    gepflegt über /api/austausch — nach dem Muster der Automatik, damit es
    für agents.yaml- UND UI-Maschinen geht).
  * Ziel ist IMMER `<ordner>/von-<absender>/<dateiname>` — nur der reine
    Dateiname des Absenders zählt, kein Pfadanteil.
  * Nie überschreiben: gleicher Name bekommt -2, -3 …
  * Geschrieben wird in eine `.<name>.teil`-Datei (exklusiv angelegt = Anspruch
    auf den Namen), am Ende umbenannt. Der Empfänger sieht keine halbe Datei,
    ein Abbruch räumt die Teil-Datei weg.
  * Token-Maschinen (#32) erreicht das Dashboard nicht per SFTP: an sie geht
    es über den Zwischenspeicher (app/zwischenspeicher.py, sie holt per
    fetch_file ab), von ihnen per upload_url (Issue #57).
  * Ausschalten löscht NICHTS auf der Maschine.

Aufrufer: die API (Datei-Panel, nutzt die gecachten Verbindungen aus
remote_files) und das MCP-Tool `send_file` (eigener Prozess, eigener Thread
→ `uebergib_sync` mit frischen Verbindungen; der Verbindungs-Cache von
remote_files hängt am Event-Loop des API-Prozesses und ist dort tabu).

Import bleibt Standardlib (asyncssh erst beim Verbinden) — die Tests laufen
gegen ein SFTP-Doppel.
"""
from __future__ import annotations

import asyncio
import ntpath
import os
import posixpath
import re
import stat
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Callable

from app import config, zwischenspeicher
from app.mailbox import AGENT_NAME_RE, ORCHESTRATOR, Mailbox

MAILBOX_ROOT = Path(os.environ.get("WORKSPACE_DIR", "/workspace")) / "mailboxes"

MAX_MB = max(1, int(os.environ.get("AUSTAUSCH_MAX_MB", "200")))
MAX_BYTES = MAX_MB * 1024 * 1024
MAX_DATEIEN = 20
# asyncssh zerlegt große Lese-/Schreibaufrufe selbst in parallele Requests —
# ein großzügiger Block hält die Leitung voll, ohne viel RAM zu binden.
BLOCK = 1024 * 1024
STANDARD_ORDNER = "austausch"
TEIL_ENDUNG = ".teil"
# Wie viele Namen (-2, -3 …) probiert werden, bevor aufgegeben wird.
MAX_NAMENSVERSUCHE = 200

_STEUERZEICHEN = re.compile(r"[\x00-\x1f\x7f]")
_WINDOWS_ABSOLUT = re.compile(r"^[A-Za-z]:[\\/]")
# Auf Windows-Zielen unzulässig; auf POSIX harmlos, aber der Absender weiß
# nicht, wohin er liefert — also überall ersetzen.
_WINDOWS_VERBOTEN = re.compile(r'[<>:"|?*\\/]')


class AustauschError(Exception):
    """Fachlicher Fehler mit Klartext — geht so ans Modell bzw. ins Panel."""


# --- Namen und Pfade ---------------------------------------------------------

def sicherer_name(name: str) -> str:
    """Reiner Dateiname fürs Ziel — kein Pfad-Schmuggel, auf jedem OS gültig."""
    roh = ntpath.basename(posixpath.basename(str(name or "").strip()))
    roh = _STEUERZEICHEN.sub("", roh)
    roh = _WINDOWS_VERBOTEN.sub("_", roh).rstrip(" .")
    if roh in ("", ".", ".."):
        raise AustauschError(f"kein brauchbarer Dateiname: {name!r}")
    if len(roh) > 200:
        stamm, endung = posixpath.splitext(roh)
        roh = stamm[: 200 - len(endung)] + endung
    return roh


def normalisiere_quellpfad(pfad: str) -> str:
    """Pfad auf der Absender-Maschine in SFTP-Schreibweise.

    Absolut bleibt absolut, `~/x` und relative Pfade gelten ab dem Home (dort
    startet die SFTP-Sitzung). Windows-Pfade (`C:\\Users\\…`) werden zu
    `/C:/Users/…` — so adressiert OpenSSH für Windows sie über SFTP.
    """
    p = str(pfad or "").strip()
    if not p or _STEUERZEICHEN.search(p):
        raise AustauschError(f"ungültiger Pfad: {pfad!r}")
    if _WINDOWS_ABSOLUT.match(p) or p.startswith("\\\\"):
        p = p.replace("\\", "/")
        return p if p.startswith("//") else "/" + p
    if p == "~":
        raise AustauschError("das Home-Verzeichnis ist keine Datei")
    if p.startswith("~/"):
        p = p[2:]
    return p


def pruefe_ordner(text: str) -> str:
    """Austausch-Ordner aus dem Dialog prüfen und vereinheitlichen.

    Relativ = ab dem Home der Maschine (klappt auf Linux wie Windows gleich),
    absolut ist erlaubt. `..` nicht — der Ordner soll dort liegen, wo er
    hingeschrieben wurde.
    """
    p = str(text or "").strip()
    if _STEUERZEICHEN.search(p) or len(p) > 200:
        raise AustauschError("ungültiger Ordner-Pfad")
    if _WINDOWS_ABSOLUT.match(p):
        p = "/" + p
    p = p.replace("\\", "/")
    if p.startswith("~/"):
        p = p[2:]
    absolut = p.startswith("/")
    teile = [t for t in p.split("/") if t not in ("", ".")]
    if not teile or p == "~":
        raise AustauschError(
            "bitte einen eigenen Ordner angeben (z. B. »austausch«), nicht das Home selbst"
        )
    if ".." in teile:
        raise AustauschError("»..« ist im Ordner-Pfad nicht erlaubt")
    return ("/" if absolut else "") + "/".join(teile)


def anzeige_pfad(pfad: str) -> str:
    """`/C:/Users/x` → `C:/Users/x` — so kennt es, wer an der Maschine sitzt."""
    return pfad[1:] if re.match(r"^/[A-Za-z]:/", pfad or "") else pfad


def _kandidat(name: str, nummer: int) -> str:
    if nummer <= 1:
        return name
    stamm, endung = posixpath.splitext(name)
    return f"{stamm}-{nummer}{endung}"


# --- Wer kann mitmachen ------------------------------------------------------

def _maschinen() -> dict[str, dict[str, Any]]:
    """Konfigurierte Maschinen: name → {"typ": ssh|token, "ssh": erreichbar?}."""
    out: dict[str, dict[str, Any]] = {}
    for a in config.load_agents_full():
        name = a.get("name")
        if not name:
            continue
        conn = a.get("connection") or {}
        typ = conn.get("type") or "ssh"
        out[name] = {"typ": typ, "ssh": typ == "ssh" and bool(conn.get("host"))}
    return out


def _eintraege(settings: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    roh = (settings or config.load_settings()).get("austausch")
    if not isinstance(roh, dict):
        return {}
    return {
        str(n): e for n, e in roh.items()
        if isinstance(e, dict) and str(e.get("ordner") or "").strip()
    }


def ziele(settings: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    """Maschinen, die gerade empfangen können: Ordner an UND per SSH erreichbar.

    Ein Eintrag zu einer inzwischen gelöschten Verbindung zählt nicht — er
    bleibt in den Settings liegen, stört aber niemanden.
    """
    maschinen = _maschinen()
    return {
        n: e for n, e in _eintraege(settings).items()
        if maschinen.get(n, {}).get("ssh")
    }


def uebersicht() -> list[dict[str, Any]]:
    """Fürs Datei-Panel: je Maschine, ob Austausch geht und wo der Ordner liegt."""
    eintraege = _eintraege()
    out = []
    for name, m in _maschinen().items():
        e = eintraege.get(name) or {}
        out.append(
            {
                "name": name,
                "moeglich": m["ssh"],
                "aktiv": bool(m["ssh"] and e),
                "ordner": e.get("ordner"),
                "pfad": e.get("pfad"),
            }
        )
    return out


def ist_token_maschine(name: str) -> bool:
    return (_maschinen().get(name) or {}).get("typ") == "token"


class LokalSftp:
    """Lokale Platte des Dashboards hinter den SFTP-Aufrufen, die `kopiere`
    braucht (stat/open) — Quelle für Zwischenspeicher und Chat-Uploads
    (Issues #57/#53). Dateizugriffe laufen im Thread."""

    async def stat(self, pfad: str):
        return await asyncio.to_thread(os.stat, pfad)

    def open(self, pfad: str, modus: str = "rb"):
        return _LokalDatei(pfad, modus)


class _LokalDatei:
    def __init__(self, pfad: str, modus: str) -> None:
        self._pfad, self._modus, self._f = pfad, modus, None

    async def __aenter__(self):
        self._f = await asyncio.to_thread(open, self._pfad, self._modus)
        return self

    async def __aexit__(self, *exc) -> None:
        if self._f is not None:
            await asyncio.to_thread(self._f.close)

    async def read(self, n: int) -> bytes:
        return await asyncio.to_thread(self._f.read, n)


async def in_speicher(sftp_von, quelle: str, an: str, von: str,
                      max_bytes: int = MAX_BYTES, name: str | None = None) -> dict[str, Any]:
    """EINE Datei in den Zwischenspeicher legen (Empfänger = Token-Maschine,
    Issue #57). Rückgabe wie `kopiere`, mit `abholen` statt Zielpfad."""
    try:
        attrs = await sftp_von.stat(quelle)
    except Exception as exc:  # noqa: BLE001
        raise AustauschError(f"nicht lesbar: {quelle} ({exc})") from exc
    perms = getattr(attrs, "permissions", None) or getattr(attrs, "st_mode", None)
    if perms is not None and not stat.S_ISREG(perms):
        raise AustauschError(f"{quelle} ist keine Datei — Ordner bitte vorher packen (zip/tar).")
    meta = zwischenspeicher.lege_an(an, von, sicherer_name(name or quelle))
    ziel = zwischenspeicher.inhalt(meta["id"])
    geschrieben = 0
    try:
        fh = await asyncio.to_thread(open, ziel, "wb")
        try:
            async with sftp_von.open(quelle, "rb") as lese:
                while True:
                    block = await lese.read(BLOCK)
                    if not block:
                        break
                    geschrieben += len(block)
                    if geschrieben > max_bytes:
                        raise AustauschError(
                            f"{quelle} überschreitet {max_bytes // (1024 * 1024)} MB.")
                    await asyncio.to_thread(fh.write, block)
        finally:
            await asyncio.to_thread(fh.close)
    except BaseException as exc:
        zwischenspeicher.loesche(meta["id"])
        if isinstance(exc, AustauschError) or not isinstance(exc, Exception):
            raise
        raise AustauschError(f"Übertragung von {quelle} fehlgeschlagen: {exc}") from exc
    zwischenspeicher.aktualisiere(meta["id"], bytes=geschrieben)
    return {"name": meta["name"], "abholen": meta["id"], "bytes": geschrieben, "quelle": quelle}


def _pruefe_ssh(name: str, rolle: str) -> None:
    if not AGENT_NAME_RE.fullmatch(name or ""):
        raise AustauschError(f"ungültiger Maschinenname ({rolle}): {name!r}")
    m = _maschinen().get(name)
    if m is None:
        raise AustauschError(f"unbekannte Maschine ({rolle}): {name!r}")
    if m["typ"] == "token":
        raise AustauschError(
            f"{name} ist eine Token-Maschine ohne SSH — der Dateiaustausch geht "
            "in dieser Ausbaustufe nur zwischen SSH-Maschinen."
        )
    if not m["ssh"]:
        raise AustauschError(f"{name} hat keine SSH-Verbindung ({rolle}).")


# --- Verbindungen ------------------------------------------------------------

@asynccontextmanager
async def frische_verbindung(name: str):
    """Kurzlebige SSH+SFTP-Sitzung — für Aufrufer außerhalb des API-Loops."""
    from app.ssh_connect import connect_ssh

    cfg = config.agent_connection(name) or {}
    if not cfg.get("host"):
        raise AustauschError(f"Keine SSH-Konfiguration für '{name}'.")
    try:
        conn = await connect_ssh(cfg)
    except Exception as exc:  # noqa: BLE001
        raise AustauschError(f"SSH-Verbindung zu {name} fehlgeschlagen: {exc}") from exc
    try:
        try:
            sftp = await conn.start_sftp_client()
        except Exception as exc:  # noqa: BLE001
            raise AustauschError(f"SFTP auf {name} nicht verfügbar: {exc}") from exc
        try:
            yield sftp
        finally:
            sftp.exit()
    finally:
        conn.close()
        try:
            await asyncio.wait_for(conn.wait_closed(), 5)
        except Exception:  # noqa: BLE001
            pass


Verbinder = Callable[[str], Any]  # name → async-Kontextmanager, der ein SFTP liefert


# --- Der Kopiervorgang -------------------------------------------------------

async def _oeffne_teil(sftp, ordner: str, name: str) -> tuple[Any, str, str]:
    """Freien Zielnamen finden und über die Teil-Datei beanspruchen.

    Das exklusive Anlegen ('x') ist der Anspruch: zwei gleichzeitige
    Sendungen desselben Namens können so nie dieselbe Zieldatei wählen.
    """
    for nummer in range(1, MAX_NAMENSVERSUCHE + 1):
        kandidat = _kandidat(name, nummer)
        ziel = posixpath.join(ordner, kandidat)
        teil = posixpath.join(ordner, f".{kandidat}{TEIL_ENDUNG}")
        if await sftp.exists(ziel) or await sftp.exists(teil):
            continue
        try:
            datei = await sftp.open(teil, "xb")
        except Exception:  # noqa: BLE001 — jemand war schneller: nächster Name
            if await sftp.exists(teil):
                continue
            raise
        return datei, teil, ziel
    raise AustauschError(f"kein freier Name für {name!r} im Zielordner")


async def kopiere(
    sftp_von, sftp_an, quelle: str, ziel_ordner: str, max_bytes: int = MAX_BYTES
) -> dict[str, Any]:
    """EINE Datei von der Absender- in den Ordner der Empfänger-Maschine."""
    try:
        attrs = await sftp_von.stat(quelle)
    except Exception as exc:  # noqa: BLE001
        raise AustauschError(f"nicht lesbar: {quelle} ({exc})") from exc
    perms = getattr(attrs, "permissions", None)
    if perms is not None and not stat.S_ISREG(perms):
        raise AustauschError(
            f"{quelle} ist keine Datei — Ordner bitte vorher packen (zip/tar)."
        )
    groesse = getattr(attrs, "size", None) or 0
    if groesse > max_bytes:
        raise AustauschError(
            f"{quelle} ist zu groß ({groesse // (1024 * 1024)} MB, "
            f"erlaubt {max_bytes // (1024 * 1024)} MB je Datei)."
        )
    name = sicherer_name(quelle)

    datei, teil, ziel = await _oeffne_teil(sftp_an, ziel_ordner, name)
    geschrieben = 0
    try:
        try:
            async with sftp_von.open(quelle, "rb") as lese:
                while True:
                    block = await lese.read(BLOCK)
                    if not block:
                        break
                    geschrieben += len(block)
                    if geschrieben > max_bytes:  # Datei wuchs während des Kopierens
                        raise AustauschError(
                            f"{quelle} überschreitet {max_bytes // (1024 * 1024)} MB."
                        )
                    await datei.write(block)
        finally:
            await datei.close()
        # rename OHNE posix-Erweiterung: scheitert, wenn das Ziel inzwischen
        # existiert — lieber ein ehrlicher Fehler als eine überschriebene Datei.
        await sftp_an.rename(teil, ziel)
    except BaseException as exc:
        try:
            await sftp_an.remove(teil)
        except Exception:  # noqa: BLE001
            pass
        if isinstance(exc, AustauschError) or not isinstance(exc, Exception):
            raise
        raise AustauschError(f"Übertragung von {quelle} fehlgeschlagen: {exc}") from exc
    return {"name": posixpath.basename(ziel), "pfad": ziel, "bytes": geschrieben, "quelle": quelle}


def _groesse(n: int) -> str:
    if n >= 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} MB".replace(".", ",")
    if n >= 1024:
        return f"{n / 1024:.0f} KB"
    return f"{n} B"


def meldungstext(von: str, zugestellt: list[dict[str, Any]], nachricht: str | None) -> str:
    kopf = (
        f"📥 Neue Datei von {von} in deinem Austausch-Ordner:"
        if len(zugestellt) == 1
        else f"📥 {len(zugestellt)} neue Dateien von {von} in deinem Austausch-Ordner:"
    )
    if zugestellt and all(z.get("abholen") for z in zugestellt):
        # Token-Maschine (Issue #57): nichts liegt auf ihrer Platte — sie holt ab.
        kopf = (f"📥 {len(zugestellt)} Datei(en) von {von} liegen für dich zum Abholen "
                f"bereit ({zwischenspeicher.ABHOL_TAGE:.0f} Tage). Je Datei fetch_file(<id>) "
                f"aufrufen und die genannte URL mit curl auf die Platte laden:")
    zeilen = [kopf] + [
        (f"- {z['name']} — id {z['abholen']} ({_groesse(z['bytes'])})" if z.get("abholen")
         else f"- {anzeige_pfad(z['pfad'])} ({_groesse(z['bytes'])})")
        for z in zugestellt
    ]
    hinweis = " ".join(str(nachricht or "").split())
    if hinweis:
        zeilen += ["", f"Hinweis des Absenders: {hinweis}"]
    return "\n".join(zeilen)


async def uebergib(
    von: str,
    pfade: list[str],
    an: str,
    *,
    melder: str | None = None,
    nachricht: str | None = None,
    verbinde: Verbinder | None = None,
    mailbox_root=None,
    max_bytes: int = MAX_BYTES,
) -> dict[str, Any]:
    """Dateien von Maschine `von` in den Austausch-Ordner von `an` legen.

    `melder` = wer in der Benachrichtigung als Absender steht (der Agent auf
    seinem gebundenen Kanal, sonst der Orchestrator = Mensch am Dashboard).
    Fachliche Fehler VOR dem ersten Byte werfen AustauschError; Fehler an
    einzelnen Dateien landen in `fehler`, der Rest wird trotzdem zugestellt.
    """
    if isinstance(pfade, str):
        pfade = [pfade]
    pfade = [p for p in (pfade or []) if str(p or "").strip()]
    if not pfade:
        raise AustauschError("keine Datei angegeben")
    if len(pfade) > MAX_DATEIEN:
        raise AustauschError(f"höchstens {MAX_DATEIEN} Dateien je Aufruf (waren {len(pfade)})")
    if ist_token_maschine(von):
        raise AustauschError(
            f"{von} ist eine Token-Maschine — das Dashboard kann dort nicht lesen. "
            f"Stattdessen upload_url(to, name) aufrufen und die Datei per PUT hochladen.")
    _pruefe_ssh(von, "Absender")
    an_token = ist_token_maschine(an)
    if not an_token:
        _pruefe_ssh(an, "Empfänger")
    elif not AGENT_NAME_RE.fullmatch(an or ""):
        raise AustauschError(f"ungültiger Maschinenname (Empfänger): {an!r}")
    if von == an:
        raise AustauschError("Absender und Empfänger sind dieselbe Maschine.")
    offen = ziele()
    if an_token:
        # Issue #57: Empfänger ohne SSH — Datei in den Zwischenspeicher, er holt ab.
        verbinde = verbinde or frische_verbindung
        zugestellt, fehler = [], []
        async with verbinde(von) as sftp_von:
            for pfad in pfade:
                try:
                    zugestellt.append(await in_speicher(
                        sftp_von, normalisiere_quellpfad(pfad), an, von, max_bytes))
                except AustauschError as exc:
                    fehler.append({"pfad": str(pfad), "fehler": str(exc)})
        return await _melde(von, an, zugestellt, fehler, melder, nachricht, mailbox_root)
    if an not in offen:
        moeglich = ", ".join(sorted(n for n in offen if n != von)) or "(keine)"
        raise AustauschError(
            f"{an} hat keinen Austausch-Ordner — einschalten im Datei-Panel "
            f"(📥 auf dem Tab der Maschine). Mögliche Empfänger: {moeglich}"
        )
    ordner = offen[an]["ordner"]

    verbinde = verbinde or frische_verbindung
    zugestellt: list[dict[str, Any]] = []
    fehler: list[dict[str, str]] = []
    async with verbinde(von) as sftp_von, verbinde(an) as sftp_an:
        try:
            unter = posixpath.join(ordner, f"von-{von}")
            await sftp_an.makedirs(unter, exist_ok=True)
            unter = str(await sftp_an.realpath(unter))
        except Exception as exc:  # noqa: BLE001
            raise AustauschError(
                f"Austausch-Ordner auf {an} nicht nutzbar ({ordner}): {exc}"
            ) from exc
        for pfad in pfade:
            try:
                quelle = normalisiere_quellpfad(pfad)
                zugestellt.append(await kopiere(sftp_von, sftp_an, quelle, unter, max_bytes))
            except AustauschError as exc:
                fehler.append({"pfad": str(pfad), "fehler": str(exc)})

    return await _melde(von, an, zugestellt, fehler, melder, nachricht, mailbox_root)


async def _melde(von: str, an: str, zugestellt: list[dict[str, Any]],
                 fehler: list[dict[str, str]], melder: str | None,
                 nachricht: str | None, mailbox_root=None) -> dict[str, Any]:
    """Ergebnis zusammenstellen und den Empfänger benachrichtigen."""
    ergebnis: dict[str, Any] = {
        "von": von,
        "an": an,
        "zugestellt": zugestellt,
        "fehler": fehler,
        "gemeldet": False,
    }
    if not zugestellt:
        ergebnis["error"] = "keine Datei zugestellt: " + "; ".join(f["fehler"] for f in fehler)
        return ergebnis

    # Normale Nachricht in die Inbox des Empfängers: erscheint im Panel unter
    # „Nachrichten", und ob sie einen Automatik-Agenten weckt, regelt wie bei
    # jeder Nachricht dessen `automatik_weckt` (#36) — kein Sonderweg.
    try:
        wurzel = mailbox_root or MAILBOX_ROOT
        await asyncio.to_thread(
            Mailbox(wurzel, an).post,
            {
                "kind": "message",
                "sender": melder or ORCHESTRATOR,
                "to": an,
                "text": meldungstext(von, zugestellt, nachricht),
                "austausch": {
                    "von": von,
                    "dateien": [
                        {"name": z["name"], "pfad": z.get("pfad"), "bytes": z["bytes"],
                         **({"abholen": z["abholen"]} if z.get("abholen") else {})}
                        for z in zugestellt
                    ],
                },
            },
        )
        ergebnis["gemeldet"] = True
    except Exception as exc:  # noqa: BLE001 — die Dateien liegen; das zählt
        ergebnis["melde_fehler"] = str(exc)
    return ergebnis


# --- Zustellen aus dem Dashboard heraus (Issues #53/#57) ----------------------

def _ablage_ziel(an: str, dest_dir: str | None) -> str:
    """Zielordner auf der Maschine für `stelle_zu`: relativ = unter dem
    `workdir` des Agenten (sonst Home); absolut nur innerhalb eines Eintrags
    aus agents.yaml `ablage_erlaubt`. `..` ist nie erlaubt."""
    cfg = next((a for a in config.load_agents_full() if a.get("name") == an), None) or {}
    conn = cfg.get("connection") or {}
    roh = str(dest_dir or "").strip().replace("\\", "/")
    if _STEUERZEICHEN.search(roh) or len(roh) > 400:
        raise AustauschError("ungültiger Zielordner")
    if _WINDOWS_ABSOLUT.match(roh):
        roh = "/" + roh
    if roh.startswith("~/"):
        roh = roh[2:]
    teile = [t for t in roh.split("/") if t not in ("", ".")]
    if ".." in teile:
        raise AustauschError("»..« ist im Zielordner nicht erlaubt")
    if roh.startswith("/"):
        ziel = "/" + "/".join(teile)
        erlaubt = cfg.get("ablage_erlaubt") or conn.get("ablage_erlaubt") or []
        if isinstance(erlaubt, str):
            erlaubt = [erlaubt]
        for e in erlaubt:
            wurzel = "/" + "/".join(t for t in str(e).replace("\\", "/").split("/") if t)
            if ziel == wurzel or ziel.startswith(wurzel + "/"):
                return ziel
        raise AustauschError(
            f"absoluter Zielordner {ziel} ist für {an} nicht freigegeben — relativ zum "
            f"Arbeitsverzeichnis angeben oder in agents.yaml `ablage_erlaubt` eintragen.")
    basis = str(cfg.get("workdir") or conn.get("workdir") or "").strip()
    if basis.startswith("~/"):
        basis = basis[2:]
    return posixpath.join(basis, *teile) if basis else "/".join(teile) or "."


async def stelle_zu(
    lokale_datei: str | os.PathLike, an: str, dest_dir: str | None = None,
    name: str | None = None, *, von: str = ORCHESTRATOR, nachricht: str | None = None,
    in_austausch_ordner: bool = False, verbinde: Verbinder | None = None,
    mailbox_root=None, max_bytes: int = MAX_BYTES, melden: bool = True,
) -> dict[str, Any]:
    """Eine Datei von der Platte des DASHBOARDS auf eine Maschine legen.

    Quelle ist ein Chat-Upload (#53) oder ein hochgeladener Zwischenspeicher-
    Eintrag (#57). SSH-Maschine: per SFTP nach `dest_dir` (bzw. mit
    `in_austausch_ordner` nach <Austausch-Ordner>/von-<von>/), nie
    überschreibend, über die Teil-Datei. Token-Maschine: in den
    Zwischenspeicher, sie holt ab. → {"an", "zugestellt": [...], "fehler": [...]}"""
    quelle = str(lokale_datei)
    if not AGENT_NAME_RE.fullmatch(an or "") or an not in _maschinen():
        raise AustauschError(f"unbekannte Maschine: {an!r}")
    lokal = LokalSftp()
    zugestellt: list[dict[str, Any]] = []
    if ist_token_maschine(an):
        zugestellt.append(await in_speicher(lokal, quelle, an, von, max_bytes, name=name))
    else:
        _pruefe_ssh(an, "Empfänger")
        if in_austausch_ordner:
            offen = ziele()
            if an not in offen:
                raise AustauschError(
                    f"{an} hat keinen Austausch-Ordner — einschalten im Datei-Panel (📥).")
            ordner = posixpath.join(offen[an]["ordner"], f"von-{von}")
        else:
            ordner = _ablage_ziel(an, dest_dir)
        async with (verbinde or frische_verbindung)(an) as sftp_an:
            try:
                await sftp_an.makedirs(ordner, exist_ok=True)
                ordner = str(await sftp_an.realpath(ordner))
            except Exception as exc:  # noqa: BLE001
                raise AustauschError(f"Zielordner auf {an} nicht nutzbar ({ordner}): {exc}") from exc
            zugestellt.append(await _kopiere_als(lokal, sftp_an, quelle, ordner, max_bytes,
                                                 name))
    if not melden:
        return {"von": von, "an": an, "zugestellt": zugestellt, "fehler": [], "gemeldet": False}
    return await _melde(von, an, zugestellt, [], von, nachricht, mailbox_root)


async def _kopiere_als(sftp_von, sftp_an, quelle: str, ordner: str, max_bytes: int,
                       name: str | None) -> dict[str, Any]:
    """`kopiere` mit frei wählbarem Zielnamen (der Speicher-Inhalt heißt
    intern »inhalt«, der Chat-Upload trägt evtl. einen Zeitstempel)."""
    if not name:
        return await kopiere(sftp_von, sftp_an, quelle, ordner, max_bytes)
    zielname = sicherer_name(name)
    try:
        attrs = await sftp_von.stat(quelle)
    except Exception as exc:  # noqa: BLE001
        raise AustauschError(f"nicht lesbar: {quelle} ({exc})") from exc
    if (getattr(attrs, "size", None) or getattr(attrs, "st_size", 0) or 0) > max_bytes:
        raise AustauschError(f"{zielname} ist zu groß (erlaubt "
                             f"{max_bytes // (1024 * 1024)} MB je Datei).")
    datei, teil, ziel = await _oeffne_teil(sftp_an, ordner, zielname)
    geschrieben = 0
    try:
        try:
            async with sftp_von.open(quelle, "rb") as lese:
                while True:
                    block = await lese.read(BLOCK)
                    if not block:
                        break
                    geschrieben += len(block)
                    if geschrieben > max_bytes:
                        raise AustauschError(
                            f"{zielname} überschreitet {max_bytes // (1024 * 1024)} MB.")
                    await datei.write(block)
        finally:
            await datei.close()
        await sftp_an.rename(teil, ziel)
    except BaseException as exc:
        try:
            await sftp_an.remove(teil)
        except Exception:  # noqa: BLE001
            pass
        if isinstance(exc, AustauschError) or not isinstance(exc, Exception):
            raise
        raise AustauschError(f"Übertragung von {zielname} fehlgeschlagen: {exc}") from exc
    return {"name": posixpath.basename(ziel), "pfad": ziel, "bytes": geschrieben,
            "quelle": quelle}


def stelle_zu_sync(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return asyncio.run(stelle_zu(*args, **kwargs))


def uebergib_sync(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Für Aufrufer ohne laufenden Event-Loop (MCP-Tool im Thread)."""
    return asyncio.run(uebergib(*args, **kwargs))


# --- Ein- und Ausschalten ----------------------------------------------------

async def richte_ein(name: str, ordner: str, verbinde: Verbinder | None = None) -> dict[str, Any]:
    """Austausch-Ordner auf der Maschine anlegen und einschalten."""
    _pruefe_ssh(name, "Maschine")
    sauber = pruefe_ordner(ordner or STANDARD_ORDNER)
    async with (verbinde or frische_verbindung)(name) as sftp:
        try:
            await sftp.makedirs(sauber, exist_ok=True)
            pfad = str(await sftp.realpath(sauber))
        except Exception as exc:  # noqa: BLE001
            raise AustauschError(f"Ordner auf {name} nicht anlegbar ({sauber}): {exc}") from exc
    eintraege = dict(_eintraege())
    eintraege[name] = {"ordner": sauber, "pfad": pfad}
    await asyncio.to_thread(config.save_settings, {"austausch": eintraege})
    return {"name": name, "aktiv": True, "ordner": sauber, "pfad": pfad}


def schalte_aus(name: str) -> dict[str, Any]:
    """Nur den Schalter umlegen — Ordner und Dateien bleiben auf der Maschine."""
    if not AGENT_NAME_RE.fullmatch(name or ""):
        raise AustauschError(f"ungültiger Maschinenname: {name!r}")
    eintraege = dict(_eintraege())
    eintraege.pop(name, None)
    config.save_settings({"austausch": eintraege})
    return {"name": name, "aktiv": False}
