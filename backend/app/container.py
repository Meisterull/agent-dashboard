"""Container-Fenster (Issue #49): Docker- und Proxmox-Container eines Rechners
ansehen — alles über die vorhandene SSH-Verbindung, kein Docker-Socket im
Dashboard.

Erste Ausbaustufe (per /ausfragen abgenommen 10.11.2026): NUR lesend —
auflisten, Logs (letzte N Zeilen), Dateien ansehen/herunterladen, Shell im
Container (die öffnet ssh_bridge mit einer sid aus `shell_sid`). Kein Start,
Stopp, Neustart, kein Hochladen. Volle Proxmox-VMs (`qm`) erscheinen nur in
der Liste.

Schalter je Rechner: settings.json `container` = {verbindung: {docker: bool,
proxmox: bool}} — wie der Austausch-Ordner, nicht in agents.yaml (die ist im
Container schreibgeschützt eingebunden, und so gilt es für YAML- und
UI-Verbindungen gleich). Ohne Haken antwortet alles mit ContainerAus (→ 409).

Rechte: Docker braucht die Gruppe `docker`, pct/qm root. Scheitert ein
Befehl an „permission denied", wird er EINMAL mit `sudo -n` wiederholt (nur
ohne Passwortabfrage); klappt das, merkt sich das Modul es je Rechner und
Werkzeug. Sonst gibt es eine Fehlermeldung mit dem, was fehlt.

Alles, was in eine Befehlszeile kommt, ist entweder streng geprüft (Name,
VMID, Zeilenzahl) oder mit shlex.quote gequotet (Pfade).
"""
from __future__ import annotations

import asyncio
import json
import posixpath
import re
import shlex
from contextlib import asynccontextmanager
from typing import AsyncIterator

from app.config import load_settings, save_settings

QUELLEN = ("docker", "lxc", "vm")
LOG_ZEILEN = (200, 500, 1000)
MAX_LESEN = 512 * 1024          # Textansicht; Größeres nur als Download
FRIST = 20.0                    # Sekunden je Befehl (Liste, ls, Logs)
CHUNK = 64 * 1024

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
VMID_RE = re.compile(r"^[0-9]{1,9}$")
_VERWEIGERT = re.compile(
    r"permission denied|must be root|only root|are you root|not permitted|zugriff verweigert",
    re.I,
)
_NICHT_DA = re.compile(r"command not found|not found|no such file or directory", re.I)
_SUDO_PASSWORT = re.compile(r"password is required|a terminal is required|sudo:.*not allowed", re.I)


class ContainerFehler(Exception):
    """Befehl gescheitert / Eingabe ungültig — vom Router als 4xx/502 gemeldet."""

    def __init__(self, text: str, status: int = 502) -> None:
        super().__init__(text)
        self.status = status


class ContainerAus(ContainerFehler):
    def __init__(self, verbindung: str, art: str) -> None:
        super().__init__(
            f"{'Docker' if art == 'docker' else 'Proxmox'} ist für „{verbindung}“ nicht "
            "eingeschaltet (Verbindungen → Haken setzen).",
            409,
        )


# --- Schalter je Rechner ----------------------------------------------------

def schalter(verbindung: str | None = None) -> dict:
    alle = load_settings().get("container") or {}
    alle = {k: {"docker": bool(v.get("docker")), "proxmox": bool(v.get("proxmox"))}
            for k, v in alle.items() if isinstance(v, dict)}
    return alle if verbindung is None else alle.get(verbindung, {"docker": False, "proxmox": False})


def setze_schalter(verbindung: str, docker: bool | None = None, proxmox: bool | None = None) -> dict:
    alle = schalter()
    eintrag = dict(alle.get(verbindung, {"docker": False, "proxmox": False}))
    if docker is not None:
        eintrag["docker"] = bool(docker)
    if proxmox is not None:
        eintrag["proxmox"] = bool(proxmox)
    if eintrag["docker"] or eintrag["proxmox"]:
        alle[verbindung] = eintrag
    else:
        alle.pop(verbindung, None)
    save_settings({"container": alle})
    _sudo.pop((verbindung, "docker"), None)
    _sudo.pop((verbindung, "proxmox"), None)
    return eintrag


def entferne_verbindung(verbindung: str) -> None:
    """Verbindung gelöscht → Schalter fällt mit (eine später gleich benannte
    andere Maschine soll nicht sofort Container zeigen)."""
    alle = schalter()
    if alle.pop(verbindung, None) is not None:
        save_settings({"container": alle})


def _werkzeug(quelle: str) -> str:
    return "docker" if quelle == "docker" else "proxmox"


def pruefe(verbindung: str, quelle: str, ziel: str | None = None) -> None:
    if quelle not in QUELLEN:
        raise ContainerFehler(f"unbekannte Quelle „{quelle}“", 400)
    if not schalter(verbindung)[_werkzeug(quelle)]:
        raise ContainerAus(verbindung, _werkzeug(quelle))
    if ziel is not None:
        muster = NAME_RE if quelle == "docker" else VMID_RE
        if not muster.match(ziel):
            raise ContainerFehler(
                "ungültiger Container-Name" if quelle == "docker" else "ungültige VMID", 400
            )


# --- Ausführen (mit sudo-Rückfall) -----------------------------------------
# Tests ersetzen `_ausfuehren` und `_prozess` durch Doppel (tests/test_container.py).

_sudo: dict[tuple[str, str], bool] = {}


async def _ausfuehren(verbindung: str, befehl: str, frist: float) -> tuple[int, bytes, bytes]:
    from app import remote_files

    async with remote_files._nutze(verbindung) as v:
        try:
            r = await asyncio.wait_for(
                v.conn.run(befehl, check=False, encoding=None), frist
            )
        except asyncio.TimeoutError as exc:
            raise ContainerFehler(f"keine Antwort nach {int(frist)} s") from exc
        rc = r.exit_status if r.exit_status is not None else 1
        return rc, r.stdout or b"", r.stderr or b""


@asynccontextmanager
async def _prozess(verbindung: str, befehl: str):
    """Langlaufender Prozess mit Byte-Strömen (Download)."""
    from app import remote_files

    async with remote_files._nutze(verbindung) as v:
        proc = await v.conn.create_process(befehl, encoding=None)
        try:
            yield proc
        finally:
            for schritt in (proc.kill, proc.close):
                try:
                    schritt()
                except Exception:  # noqa: BLE001
                    pass


def _text(b: bytes | str) -> str:
    return b if isinstance(b, str) else b.decode("utf-8", "replace")


def _praefix(verbindung: str, werkzeug: str) -> str:
    return "sudo -n " if _sudo.get((verbindung, werkzeug)) else ""


async def laufen(verbindung: str, werkzeug: str, befehl: str, frist: float = FRIST) -> str:
    """`befehl` beginnt mit docker/pct/qm. Liefert stdout oder wirft
    ContainerFehler mit einer Meldung, die sagt, was fehlt."""
    rc, out, err = await _ausfuehren(verbindung, _praefix(verbindung, werkzeug) + befehl, frist)
    if rc == 0:
        return _text(out)
    fehler = _text(err).strip() or _text(out).strip()
    if _VERWEIGERT.search(fehler) and not _sudo.get((verbindung, werkzeug)):
        rc2, out2, err2 = await _ausfuehren(verbindung, "sudo -n " + befehl, frist)
        if rc2 == 0:
            _sudo[(verbindung, werkzeug)] = True
            return _text(out2)
        fehler2 = _text(err2).strip()
        if _SUDO_PASSWORT.search(fehler2) or _VERWEIGERT.search(fehler2):
            raise ContainerFehler(_rechte_hinweis(werkzeug, fehler), 403)
        fehler = fehler2 or fehler
    # „Werkzeug fehlt" nur, wenn die SHELL das Werkzeug selbst nicht findet —
    # `docker exec` liefert Exit 127 auch, wenn IM Container ein Befehl fehlt
    # (distroless ohne ls), das ist etwas anderes.
    werkzeug_name = befehl.split()[0]
    if re.search(rf"\b{re.escape(werkzeug_name)}: (command )?not found", fehler):
        raise ContainerFehler(
            f"„{befehl.split()[0]}“ gibt es auf diesem Rechner nicht"
            + (" — ist das ein Proxmox-Host?" if werkzeug == "proxmox" else "."),
            404,
        )
    raise ContainerFehler(_kurz(fehler) or f"Befehl scheiterte (Exit {rc})")


def _rechte_hinweis(werkzeug: str, fehler: str) -> str:
    if werkzeug == "docker":
        rat = ("Der SSH-Benutzer braucht die Gruppe „docker“ (sudo usermod -aG docker <user>, "
               "danach neu anmelden) oder ein sudo ohne Passwort für docker.")
    else:
        rat = ("pct/qm brauchen root: als root verbinden oder für den SSH-Benutzer "
               "„<user> ALL=(root) NOPASSWD: /usr/sbin/pct, /usr/sbin/qm“ in die sudoers.")
    return f"Keine Berechtigung ({_kurz(fehler)}). {rat}"


def _kurz(text: str, n: int = 300) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1] + "…"


# --- Auflisten -----------------------------------------------------------------

def parse_docker_ps(text: str) -> list[dict]:
    raus = []
    for zeile in text.splitlines():
        zeile = zeile.strip()
        if not zeile:
            continue
        try:
            d = json.loads(zeile)
        except json.JSONDecodeError:
            continue
        name = (d.get("Names") or "").split(",")[0]
        if not name:
            continue
        status = d.get("Status") or ""
        state = (d.get("State") or "").lower() or ("running" if status.startswith("Up") else "exited")
        raus.append({
            "quelle": "docker",
            "id": name,
            "name": name,
            "image": d.get("Image") or "",
            "status": status,
            "laeuft": state == "running",
            "zustand": state,
            "ports": d.get("Ports") or "",
            "erstellt": d.get("CreatedAt") or "",
        })
    return sorted(raus, key=lambda c: (not c["laeuft"], c["name"].lower()))


def parse_pct_list(text: str) -> list[dict]:
    """`pct list`: VMID Status [Lock] Name — Lock ist meist leer."""
    raus = []
    for zeile in text.splitlines()[1:]:
        teile = zeile.split()
        if len(teile) < 2 or not VMID_RE.match(teile[0]):
            continue
        status = teile[1].lower()
        name = teile[-1] if len(teile) >= 3 else teile[0]
        lock = teile[2] if len(teile) >= 4 else ""
        raus.append({
            "quelle": "lxc",
            "id": teile[0],
            "name": name,
            "image": f"LXC {teile[0]}",
            "status": status + (f" · gesperrt ({lock})" if lock else ""),
            "laeuft": status == "running",
            "zustand": status,
            "ports": "",
            "erstellt": "",
        })
    return raus


def parse_qm_list(text: str) -> list[dict]:
    """`qm list`: VMID NAME STATUS MEM(MB) BOOTDISK(GB) PID."""
    raus = []
    for zeile in text.splitlines()[1:]:
        teile = zeile.split()
        if len(teile) < 3 or not VMID_RE.match(teile[0]):
            continue
        status = teile[2].lower()
        mem = teile[3] if len(teile) > 3 else ""
        disk = teile[4] if len(teile) > 4 else ""
        raus.append({
            "quelle": "vm",
            "id": teile[0],
            "name": teile[1],
            "image": "VM " + " · ".join(x for x in (f"{mem} MB RAM" if mem else "", f"{disk} GB Disk" if disk else "") if x),
            "status": status,
            "laeuft": status == "running",
            "zustand": status,
            "ports": "",
            "erstellt": "",
            "nur_ansicht": True,
        })
    return raus


async def liste(verbindung: str) -> dict:
    """Alle Container des Rechners. Fehler je Quelle stehen in `fehler` — eine
    fehlende Proxmox-Installation verdirbt die Docker-Liste nicht."""
    s = schalter(verbindung)
    if not (s["docker"] or s["proxmox"]):
        raise ContainerAus(verbindung, "docker")
    eintraege: list[dict] = []
    fehler: dict[str, str] = {}
    fehlt: set[str] = set()   # Quellen, deren Werkzeug es auf dem Rechner nicht gibt

    async def docker():
        try:
            eintraege.extend(parse_docker_ps(await laufen(
                verbindung, "docker", "docker ps -a --no-trunc --format '{{json .}}'")))
        except ContainerFehler as exc:
            fehler["docker"] = str(exc)

    async def lxc():
        try:
            eintraege.extend(parse_pct_list(await laufen(verbindung, "proxmox", "pct list")))
        except ContainerFehler as exc:
            fehler["lxc"] = str(exc)
            if exc.status == 404:
                fehlt.add("lxc")

    async def vm():
        try:
            eintraege.extend(parse_qm_list(await laufen(verbindung, "proxmox", "qm list")))
        except ContainerFehler as exc:
            fehler["vm"] = str(exc)
            if exc.status == 404:
                fehlt.add("vm")

    aufgaben = ([docker()] if s["docker"] else []) + ([lxc(), vm()] if s["proxmox"] else [])
    await asyncio.gather(*aufgaben)
    # pct und qm scheitern auf einem Nicht-Proxmox-Host beide — eine Meldung reicht.
    if fehlt == {"lxc", "vm"}:
        fehler.pop("lxc")
        fehler.pop("vm")
        fehler["proxmox"] = "pct/qm gibt es auf diesem Rechner nicht — ist das ein Proxmox-Host?"
    reihenfolge = {"docker": 0, "lxc": 1, "vm": 2}
    eintraege.sort(key=lambda c: (reihenfolge[c["quelle"]], not c["laeuft"], c["name"].lower()))
    return {"verbindung": verbindung, "schalter": s, "container": eintraege, "fehler": fehler}


# --- Im Container: Befehlsvorsatz ------------------------------------------

def _im(quelle: str, ziel: str) -> str:
    """Vorsatz, der einen Befehl IM Container ausführt (ohne TTY)."""
    if quelle == "docker":
        return f"docker exec {shlex.quote(ziel)} "
    if quelle == "lxc":
        return f"pct exec {shlex.quote(ziel)} -- "
    raise ContainerFehler("VMs sind nur zur Ansicht — kein Zugriff ins Innere", 400)


def _pfad(pfad: str | None) -> str:
    pfad = (pfad or "/").strip() or "/"
    if not pfad.startswith("/"):
        pfad = "/" + pfad
    if "\x00" in pfad or "\n" in pfad:
        raise ContainerFehler("ungültiger Pfad", 400)
    return posixpath.normpath(pfad) if pfad != "/" else "/"


# --- Dateien ---------------------------------------------------------------------

_LS_ZEILE = re.compile(
    r"^(?P<rechte>[-dlcbps][-rwxsStTl@+.]{9,}[@+.]?)\s+\d+\s+\S+\s+\S+\s+"
    r"(?P<groesse>\d+|\d+,\s*\d+)\s+(?P<rest>.+)$"
)


def parse_ls(text: str, mit_zeitstempel: bool) -> list[dict]:
    """`ls -lan` (GNU mit --time-style=+%s oder BusyBox mit Datum in 3 Feldern)."""
    raus = []
    for zeile in text.splitlines():
        m = _LS_ZEILE.match(zeile.rstrip())
        if not m:
            continue
        rest = m.group("rest")
        if mit_zeitstempel:
            zeit, _, name = rest.partition(" ")
            mtime = int(zeit) if zeit.isdigit() else None
        else:
            teile = rest.split(None, 3)
            if len(teile) < 4:
                continue
            name, mtime = teile[3], None
        art = m.group("rechte")[0]
        ziel = None
        if art == "l" and " -> " in name:
            name, ziel = name.split(" -> ", 1)
        if name in (".", ".."):
            continue
        groesse = m.group("groesse")
        raus.append({
            "name": name,
            "typ": "dir" if art == "d" else ("link" if art == "l" else "file"),
            "groesse": int(groesse) if groesse.isdigit() else 0,
            "mtime": mtime,
            "ziel": ziel,
        })
    return sorted(raus, key=lambda e: (e["typ"] != "dir", e["name"].lower()))


async def dateien(verbindung: str, quelle: str, ziel: str, pfad: str | None) -> dict:
    pruefe(verbindung, quelle, ziel)
    pfad = _pfad(pfad)
    werkzeug = _werkzeug(quelle)
    vorsatz = _im(quelle, ziel)
    q = shlex.quote(pfad)
    try:
        text = await laufen(verbindung, werkzeug, f"{vorsatz}ls -lan --time-style=+%s -- {q}")
        mit_zeit = True
    except ContainerFehler as exc:
        # BusyBox (Alpine & Co.) kennt --time-style nicht → ohne Zeitstempel.
        if exc.status in (403, 409):
            raise
        try:
            text = await laufen(verbindung, werkzeug, f"{vorsatz}ls -lan -- {q}")
        except ContainerFehler as exc2:
            meldung = str(exc2)
            if "executable file not found" in meldung or '"ls"' in meldung:
                raise ContainerFehler("Im Container gibt es kein „ls“ (z. B. distroless-Image).", 422) from exc2
            raise
        mit_zeit = False
    return {"pfad": pfad, "eintraege": parse_ls(text, mit_zeit)}


async def lesen(verbindung: str, quelle: str, ziel: str, pfad: str) -> dict:
    """Kleine Textdatei zum Ansehen (nur lesend)."""
    pruefe(verbindung, quelle, ziel)
    pfad = _pfad(pfad)
    roh_text = await laufen(
        verbindung, _werkzeug(quelle),
        f"{_im(quelle, ziel)}head -c {MAX_LESEN + 1} -- {shlex.quote(pfad)}",
    )
    roh = roh_text.encode("utf-8", "replace")
    if "\x00" in roh_text:
        raise ContainerFehler("Binärdatei — bitte herunterladen.", 415)
    zu_gross = len(roh) > MAX_LESEN
    return {"pfad": pfad, "text": roh_text[:MAX_LESEN], "gekuerzt": zu_gross}


async def herunterladen(verbindung: str, quelle: str, ziel: str, pfad: str) -> AsyncIterator[bytes]:
    """Datei als Byte-Strom (`cat` im Container). Der erste Block wird vom
    Router VOR dem Response-Start geholt — scheitert schon der, gibt es einen
    Fehler statt einer leeren 200-Antwort."""
    pruefe(verbindung, quelle, ziel)
    pfad = _pfad(pfad)
    befehl = _praefix(verbindung, _werkzeug(quelle)) + f"{_im(quelle, ziel)}cat -- {shlex.quote(pfad)}"
    async with _prozess(verbindung, befehl) as proc:
        erster = True
        while True:
            stueck = await proc.stdout.read(CHUNK)
            if not stueck:
                break
            erster = False
            yield stueck
        if erster:
            # Nichts gelesen: leere Datei oder Fehler? stderr entscheidet.
            fehler = _text(await proc.stderr.read(4096) or b"").strip()
            if fehler:
                raise ContainerFehler(_kurz(fehler), 404 if _NICHT_DA.search(fehler) else 502)


# --- Logs ------------------------------------------------------------------------

async def logs(verbindung: str, quelle: str, ziel: str, zeilen: int = 200) -> dict:
    pruefe(verbindung, quelle, ziel)
    if zeilen not in LOG_ZEILEN:
        zeilen = LOG_ZEILEN[0]
    q = shlex.quote(ziel)
    if quelle == "docker":
        text = await laufen(verbindung, "docker", f"docker logs --tail {zeilen} -t {q} 2>&1")
        herkunft = "docker logs"
    elif quelle == "lxc":
        innen = (f"journalctl -n {zeilen} --no-pager 2>/dev/null || "
                 f"tail -n {zeilen} /var/log/syslog 2>/dev/null || "
                 f"tail -n {zeilen} /var/log/messages")
        text = await laufen(verbindung, "proxmox", f"pct exec {q} -- sh -c {shlex.quote(innen)}")
        herkunft = "journalctl / syslog im Container"
    else:
        raise ContainerFehler("VMs sind nur zur Ansicht — keine Logs", 400)
    return {"text": text, "zeilen": zeilen, "herkunft": herkunft}


# --- Shell (ssh_bridge) ------------------------------------------------------

SID_PRAEFIX = "c:"


def ist_container_sid(sid: str) -> bool:
    return isinstance(sid, str) and sid.startswith(SID_PRAEFIX)


def shell_befehl(verbindung: str, sid: str) -> str:
    """sid "c:docker:<name>" / "c:lxc:<vmid>" → Startkommando für die PTY-Shell.
    Prüft Schalter und Namen; wirft ContainerFehler."""
    _, _, rest = sid.partition(SID_PRAEFIX)
    quelle, _, ziel = rest.partition(":")
    pruefe(verbindung, quelle, ziel)
    sudo = _praefix(verbindung, _werkzeug(quelle))
    if quelle == "docker":
        innen = "command -v bash >/dev/null 2>&1 && exec bash || exec sh"
        return (f"{sudo}docker exec -it {shlex.quote(ziel)} sh -c {shlex.quote(innen)} "
                f"|| {{ echo; echo '[Keine Shell im Container — läuft er, hat das Image sh?]'; sleep 2; }}")
    if quelle == "lxc":
        return (f"{sudo}pct enter {shlex.quote(ziel)} "
                f"|| {{ echo; echo '[pct enter gescheitert — läuft der Container, darf der Benutzer pct?]'; sleep 2; }}")
    raise ContainerFehler("VMs sind nur zur Ansicht — keine Shell", 400)
