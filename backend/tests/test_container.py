"""Container-Fenster (app/container.py, Issue #49) — nur Standardlib:

    cd backend && python -m tests.test_container

Gegen ein Befehls-Doppel statt SSH: `container._ausfuehren` und
`container._prozess` werden ersetzt, das Doppel antwortet je Befehl mit
(rc, stdout, stderr) und protokolliert, was ausgeführt worden wäre.
Abgedeckt:
  * Schalter: aus → ContainerAus (409), je Werkzeug getrennt, Löschen der
    Verbindung nimmt den Schalter mit
  * Parser: docker ps (JSON-Zeilen), pct list (mit/ohne Lock), qm list,
    ls -lan GNU (Zeitstempel) und BusyBox (Datum), Links, . und ..
  * liste: Docker + Proxmox zusammen; kein Proxmox-Host → EINE Meldung,
    Docker-Liste bleibt
  * Prüfungen: Namen/VMIDs mit Shell-Zeichen werden abgelehnt, Pfade gequotet,
    VMs ohne Zugriff ins Innere, Zeilenzahl nur aus der Auswahl
  * sudo-Rückfall: permission denied → einmal `sudo -n`, gemerkt; sudo will
    Passwort → 403 mit Hinweis auf Gruppe/sudoers
  * BusyBox-ls-Rückfall, Textansicht (Binär → 415), Download-Strom + Fehler
  * Shell-sid → Startkommando (docker exec -it / pct enter), gesperrt ohne Haken
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

_TMP = Path(tempfile.mkdtemp(prefix="container-test-"))
os.environ["WORKSPACE_DIR"] = str(_TMP / "workspace")
os.environ["DATA_CONFIG_DIR"] = str(_TMP / "workspace" / "config")

from app import config, container  # noqa: E402
from app.container import ContainerAus, ContainerFehler  # noqa: E402


DOCKER_PS = "\n".join(json.dumps(d) for d in [
    {"Names": "nginx", "Image": "nginx:1.27", "Status": "Up 3 days", "State": "running",
     "Ports": "0.0.0.0:80->80/tcp", "CreatedAt": "2026-11-01 10:00:00 +0100 CET"},
    {"Names": "alt", "Image": "busybox", "Status": "Exited (0) 2 weeks ago", "State": "exited",
     "Ports": "", "CreatedAt": "2026-10-01"},
    {"Names": "ohne-state", "Image": "x", "Status": "Up 1 hour"},
])
PCT_LIST = """VMID       Status     Lock         Name
101        running                 pihole
102        stopped    backup       nextcloud
"""
QM_LIST = """      VMID NAME                 STATUS     MEM(MB)    BOOTDISK(GB) PID
       200 win11                running    8192              64.00 1234
       201 test                 stopped    2048              32.00 0
"""
LS_GNU = """total 12
drwxr-xr-x    1 0 0 4096 1762780000 .
drwxr-xr-x    1 0 0 4096 1762780000 ..
-rw-r--r--    1 0 0  615 1762770000 index.html
drwxr-xr-x    2 0 0 4096 1762760000 conf.d
lrwxrwxrwx    1 0 0   11 1762750000 aktuell -> /etc/nginx
-rw-r--r--    1 0 0   10 1762750000 mit leerzeichen.txt
"""
LS_BUSYBOX = """total 8
drwxr-xr-x    2 0        0             4096 Nov  9 10:00 .
-rw-r--r--    1 0        0              615 Nov  9 10:00 index.html
drwxr-xr-x    2 0        0             4096 Nov  9  2025 alt
"""


class Doppel:
    """Antwortet je Befehl nach Regeln (Präfix → (rc, out, err))."""

    def __init__(self):
        self.befehle: list[str] = []
        self.regeln: list[tuple[str, tuple[int, str, str]]] = []

    def wenn(self, praefix: str, rc: int = 0, out: str = "", err: str = ""):
        self.regeln.insert(0, (praefix, (rc, out, err)))
        return self

    async def ausfuehren(self, verbindung, befehl, frist):
        self.befehle.append(befehl)
        for praefix, (rc, out, err) in self.regeln:
            if befehl.startswith(praefix):
                return rc, out.encode(), err.encode()
        return 127, b"", f"sh: {befehl.split()[0]}: command not found".encode()


class Basis(unittest.TestCase):
    def setUp(self):
        config.save_settings({"container": {}})
        container._sudo.clear()
        self.d = Doppel()
        self._alt = container._ausfuehren
        container._ausfuehren = self.d.ausfuehren

    def tearDown(self):
        container._ausfuehren = self._alt

    def lauf(self, coro):
        return asyncio.run(coro)


class TestSchalter(Basis):
    def test_aus_ist_409(self):
        with self.assertRaises(ContainerAus) as ctx:
            self.lauf(container.liste("server"))
        self.assertEqual(ctx.exception.status, 409)
        self.assertEqual(self.d.befehle, [], "ohne Haken darf nichts ausgeführt werden")

    def test_werkzeuge_getrennt(self):
        container.setze_schalter("server", docker=True)
        container.pruefe("server", "docker", "nginx")
        with self.assertRaises(ContainerAus):
            container.pruefe("server", "lxc", "101")

    def test_beide_aus_entfernt_eintrag(self):
        container.setze_schalter("server", docker=True, proxmox=True)
        container.setze_schalter("server", docker=False, proxmox=False)
        self.assertNotIn("server", config.load_settings()["container"])

    def test_verbindung_loeschen_nimmt_schalter_mit(self):
        container.setze_schalter("server", docker=True)
        container.entferne_verbindung("server")
        self.assertEqual(container.schalter("server"), {"docker": False, "proxmox": False})


class TestParser(unittest.TestCase):
    def test_docker_ps(self):
        c = container.parse_docker_ps(DOCKER_PS + "\nkaputt\n")
        self.assertEqual([x["name"] for x in c], ["nginx", "ohne-state", "alt"])  # laufende zuerst
        self.assertTrue(c[1]["laeuft"], "ohne State: Status 'Up' zählt als läuft")
        self.assertEqual(c[0]["ports"], "0.0.0.0:80->80/tcp")

    def test_pct_list(self):
        c = container.parse_pct_list(PCT_LIST)
        self.assertEqual([(x["id"], x["name"], x["laeuft"]) for x in c],
                         [("101", "pihole", True), ("102", "nextcloud", False)])
        self.assertIn("backup", c[1]["status"])

    def test_qm_list(self):
        c = container.parse_qm_list(QM_LIST)
        self.assertEqual([(x["id"], x["name"], x["laeuft"]) for x in c],
                         [("200", "win11", True), ("201", "test", False)])
        self.assertTrue(all(x["nur_ansicht"] for x in c))
        self.assertIn("8192 MB", c[0]["image"])

    def test_ls_gnu(self):
        e = container.parse_ls(LS_GNU, True)
        self.assertEqual([x["name"] for x in e], ["conf.d", "aktuell", "index.html", "mit leerzeichen.txt"])
        link = next(x for x in e if x["name"] == "aktuell")
        self.assertEqual((link["typ"], link["ziel"]), ("link", "/etc/nginx"))
        self.assertEqual(next(x for x in e if x["name"] == "index.html")["mtime"], 1762770000)

    def test_ls_busybox(self):
        e = container.parse_ls(LS_BUSYBOX, False)
        self.assertEqual([(x["name"], x["typ"], x["groesse"]) for x in e],
                         [("alt", "dir", 4096), ("index.html", "file", 615)])


class TestListe(Basis):
    def test_docker_und_proxmox(self):
        container.setze_schalter("server", docker=True, proxmox=True)
        self.d.wenn("docker ps", out=DOCKER_PS).wenn("pct list", out=PCT_LIST).wenn("qm list", out=QM_LIST)
        r = self.lauf(container.liste("server"))
        self.assertEqual(r["fehler"], {})
        self.assertEqual([c["quelle"] for c in r["container"]].count("vm"), 2)
        self.assertEqual(r["container"][0]["quelle"], "docker")

    def test_kein_proxmox_host_eine_meldung(self):
        container.setze_schalter("server", docker=True, proxmox=True)
        self.d.wenn("docker ps", out=DOCKER_PS)
        r = self.lauf(container.liste("server"))
        self.assertEqual(len(r["container"]), 3, "Docker-Liste bleibt trotz Proxmox-Fehler")
        self.assertEqual(list(r["fehler"]), ["proxmox"])
        self.assertIn("Proxmox-Host", r["fehler"]["proxmox"])

    def test_nur_docker_fragt_kein_pct(self):
        container.setze_schalter("server", docker=True)
        self.d.wenn("docker ps", out=DOCKER_PS)
        self.lauf(container.liste("server"))
        self.assertFalse(any(b.startswith(("pct", "qm")) for b in self.d.befehle))


class TestPruefungen(Basis):
    def setUp(self):
        super().setUp()
        container.setze_schalter("server", docker=True, proxmox=True)

    def test_boese_namen(self):
        for name in ["nginx;rm -rf /", "$(id)", "-it", "a b", "../x", ""]:
            with self.assertRaises(ContainerFehler, msg=name) as ctx:
                container.pruefe("server", "docker", name)
            self.assertEqual(ctx.exception.status, 400)
        for vmid in ["101;id", "abc", "1 2"]:
            with self.assertRaises(ContainerFehler):
                container.pruefe("server", "lxc", vmid)
        self.assertEqual(self.d.befehle, [])

    def test_pfad_gequotet(self):
        self.d.wenn("docker exec nginx ls", out=LS_GNU)
        self.lauf(container.dateien("server", "docker", "nginx", "/tmp/$(reboot) x"))
        self.assertIn("-- '/tmp/$(reboot) x'", self.d.befehle[-1])

    def test_vm_kein_zugriff(self):
        for f in (container.dateien("server", "vm", "200", "/"), container.logs("server", "vm", "200")):
            with self.assertRaises(ContainerFehler) as ctx:
                self.lauf(f)
            self.assertEqual(ctx.exception.status, 400)

    def test_zeilen_nur_aus_auswahl(self):
        self.d.wenn("docker logs", out="zeile\n")
        r = self.lauf(container.logs("server", "docker", "nginx", 99999))
        self.assertEqual(r["zeilen"], 200)
        self.assertIn("--tail 200", self.d.befehle[-1])

    def test_lxc_logs_mit_rueckfall(self):
        self.d.wenn("pct exec 101", out="Nov 10 log\n")
        r = self.lauf(container.logs("server", "lxc", "101", 500))
        self.assertIn("journalctl -n 500", self.d.befehle[-1])
        self.assertIn("/var/log/syslog", self.d.befehle[-1])
        self.assertEqual(r["text"], "Nov 10 log\n")


class TestSudo(Basis):
    def setUp(self):
        super().setUp()
        container.setze_schalter("server", docker=True)

    def test_rueckfall_und_merken(self):
        self.d.wenn("docker ps", rc=1, err="permission denied while trying to connect to the Docker daemon socket")
        self.d.wenn("sudo -n docker ps", out=DOCKER_PS)
        r = self.lauf(container.liste("server"))
        self.assertEqual(len(r["container"]), 3)
        self.lauf(container.liste("server"))
        self.assertEqual(self.d.befehle[-1][:7], "sudo -n", "zweiter Aufruf gleich mit sudo")
        self.assertEqual(sum(b.startswith("docker ps") for b in self.d.befehle), 1)

    def test_sudo_will_passwort(self):
        self.d.wenn("docker ps", rc=1, err="Got permission denied while trying to connect")
        self.d.wenn("sudo -n docker", rc=1, err="sudo: a password is required")
        r = self.lauf(container.liste("server"))
        self.assertIn("Gruppe „docker“", r["fehler"]["docker"])

    def test_direkter_aufruf_403(self):
        self.d.wenn("docker logs", rc=1, err="permission denied")
        self.d.wenn("sudo -n docker", rc=1, err="sudo: a password is required")
        with self.assertRaises(ContainerFehler) as ctx:
            self.lauf(container.logs("server", "docker", "nginx"))
        self.assertEqual(ctx.exception.status, 403)


class TestDateien(Basis):
    def setUp(self):
        super().setUp()
        container.setze_schalter("server", docker=True)

    def test_distroless_ohne_ls(self):
        # docker exec liefert 127, wenn IM Container ls fehlt — das ist nicht
        # „docker gibt es nicht" (so gesehen an einem echten distroless-Image).
        fehlt = 'OCI runtime exec failed: exec failed: unable to start container process: exec: "ls": executable file not found in $PATH: unknown'
        self.d.wenn("docker exec matrix ls", rc=127, err=fehlt)
        with self.assertRaises(ContainerFehler) as ctx:
            self.lauf(container.dateien("server", "docker", "matrix", "/"))
        self.assertEqual(ctx.exception.status, 422)
        self.assertIn("kein „ls“", str(ctx.exception))

    def test_busybox_rueckfall(self):
        self.d.wenn("docker exec alpine ls -lan --time-style", rc=1, err="ls: unrecognized option: time-style=+%s")
        self.d.wenn("docker exec alpine ls -lan -- ", out=LS_BUSYBOX)
        r = self.lauf(container.dateien("server", "docker", "alpine", "/usr/share"))
        self.assertEqual([x["name"] for x in r["eintraege"]], ["alt", "index.html"])
        self.assertIsNone(r["eintraege"][1]["mtime"])

    def test_pfad_normalisiert(self):
        self.d.wenn("docker exec nginx ls", out=LS_GNU)
        r = self.lauf(container.dateien("server", "docker", "nginx", "etc/../etc//nginx/"))
        self.assertEqual(r["pfad"], "/etc/nginx")

    def test_lesen_text_und_binaer(self):
        self.d.wenn("docker exec nginx head -c", out="server {}\n")
        self.assertEqual(self.lauf(container.lesen("server", "docker", "nginx", "/etc/x.conf"))["text"], "server {}\n")
        self.d.wenn("docker exec nginx head -c", out="ELF\x00\x01")
        with self.assertRaises(ContainerFehler) as ctx:
            self.lauf(container.lesen("server", "docker", "nginx", "/bin/sh"))
        self.assertEqual(ctx.exception.status, 415)

    def _download(self, bloecke, stderr=b""):
        class Strom:
            def __init__(self, teile):
                self.teile = list(teile)

            async def read(self, n=-1):
                return self.teile.pop(0) if self.teile else b""

        proc = SimpleNamespace(stdout=Strom(bloecke), stderr=Strom([stderr] if stderr else []),
                               kill=lambda: None, close=lambda: None)
        gesehen = []

        @asynccontextmanager
        async def prozess(verbindung, befehl):
            gesehen.append(befehl)
            yield proc

        alt = container._prozess
        container._prozess = prozess
        try:
            async def alles():
                return [b async for b in container.herunterladen("server", "docker", "nginx", "/etc/a b")]
            return self.lauf(alles()), gesehen
        finally:
            container._prozess = alt

    def test_download_strom(self):
        daten, befehle = self._download([b"abc", b"def"])
        self.assertEqual(b"".join(daten), b"abcdef")
        self.assertEqual(befehle, ["docker exec nginx cat -- '/etc/a b'"])

    def test_download_fehler(self):
        with self.assertRaises(ContainerFehler) as ctx:
            self._download([], stderr=b"cat: can't open '/etc/a b': No such file or directory")
        self.assertEqual(ctx.exception.status, 404)

    def test_leere_datei_ist_kein_fehler(self):
        daten, _ = self._download([])
        self.assertEqual(daten, [])


class TestShell(Basis):
    def test_docker_und_lxc(self):
        container.setze_schalter("server", docker=True, proxmox=True)
        b = container.shell_befehl("server", "c:docker:nginx")
        self.assertTrue(b.startswith("docker exec -it nginx sh -c "), b)
        self.assertIn("exec bash || exec sh", b)
        self.assertTrue(container.shell_befehl("server", "c:lxc:101").startswith("pct enter 101 "))

    def test_gesperrt_und_boese(self):
        with self.assertRaises(ContainerAus):
            container.shell_befehl("server", "c:docker:nginx")
        container.setze_schalter("server", docker=True, proxmox=True)
        for sid in ["c:docker:x;id", "c:vm:200", "c:quatsch:1", "c:docker:"]:
            with self.assertRaises(ContainerFehler, msg=sid):
                container.shell_befehl("server", sid)

    def test_sudo_auch_in_der_shell(self):
        container.setze_schalter("server", docker=True)
        container._sudo[("server", "docker")] = True
        self.assertTrue(container.shell_befehl("server", "c:docker:nginx").startswith("sudo -n docker exec"))


if __name__ == "__main__":
    unittest.main(verbosity=1)
