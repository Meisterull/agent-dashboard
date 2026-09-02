# Agent Dashboard

**Dirigiere deine Claude-Code-Agenten von überall — selbst gehosteter
Leitstand in einem einzigen Container, orchestriert von Claude oder einem
komplett lokalen LLM.**

English version: **[README.md](README.md)** · Lizenz: **AGPL-3.0**

<!-- TODO: 30-Sekunden-Demo aufnehmen (Handy-Ansicht: Chat → Task landet beim
     Agenten → Ergebnis kommt zurück), als docs/demo.gif ablegen, dann:
<p align="center"><img src="docs/demo.gif" alt="Agent-Dashboard-Demo" width="720"></p>
-->

Du chattest im Browser mit einem Orchestrator-LLM. Es plant — und delegiert
echte Arbeit an **Claude Code auf deinen eigenen Rechnern**: Desktop,
Build-Kiste, das Notebook im anderen Zimmer. Ergebnisse, Rückfragen und
Fortschritt laufen in einem Dashboard zusammen, das auf dem Handy genauso gut
funktioniert wie am Schreibtisch. Kein SaaS, keine Telemetrie, ein gehärteter
Docker-Container auf deinem eigenen Server.

## Highlights

- **Orchestrator-Chat** — ein LLM plant Aufgaben und delegiert sie über
  MCP-Tools. Läuft mit der **Claude-API** oder **jedem tool-fähigen
  Ollama-Modell** (komplett lokal, kein API-Key).
- **Datei-Mailbox als Transport** — jeder Task und jede Antwort ist eine
  JSON-Datei in `inbox/`/`outbox/`: robust, offline-tolerant, atomar — und
  mit `cat` debugbar.
- **Agenten fragen zurück** — braucht ein Worker Klärung, parkt sein Task; du
  antwortest im Banner, der Task läuft mit deiner Antwort im Kontext weiter.
- **Rollen für Läufe** — Rollen wie ein Nur-Lese-`review` zentral definieren
  (Prompt + Rechte-Teilmenge) und jedem Task mitgeben; eine Rolle kann die
  Rechte eines Agenten nur einschränken, nie erweitern.
- **Automatikmodus** — je Agent hält das Dashboard einen Watcher auf dem
  entfernten Rechner, der die Inbox selbständig abarbeitet; ein globaler
  Not-Aus stoppt alles auf einmal.
- **Echte SSH-Terminals im Browser** — Tabs je Host, mehrere Terminals pro
  Verbindung, Sessions überleben Verbindungsabbrüche (später wieder andocken,
  auch von einem anderen Gerät), Wisch-Scrollen, das auf Touchscreens
  wirklich funktioniert.
- **Arbeitsflächen-Ansichten** — Chat, Dateien, Terminals und Agenten-Monitor
  frei anordnen und als benannte Layouts speichern.
- **Agenten ohne SSH** — Rechner hinter NAT (etwa ein Windows-Notebook mit
  Claude Desktop) melden sich per Token-HTTPS statt über einen Tunnel an.
- **Identität aus dem Kanal** — jeder Agent bekommt seinen eigenen MCP-Port
  oder Token; *wer da ruft* leitet der Server aus dem Kanal selbst ab, und
  optionale Tool-Allowlists je Agent begrenzen, was ein Kanal darf.
- **Config-getriebene Integrationen** — benannte HTTP-APIs (Ticketsystem,
  ERP, Hausautomatisierung, …) per YAML als aufrufbare Tools anhängen, ohne
  Code.
- **Oberfläche auf Deutsch und Englisch** — umschaltbar in den Einstellungen,
  über alle Geräte hinweg abgeglichen.
- **Ein gehärteter Container** — nginx + FastAPI + MCP-Server unter
  supervisord: non-root, `cap_drop: ALL`, kein `docker.sock`,
  Path-Traversal-sichere Dateizugriffe, passwortgeschützte Oberfläche.

## So funktioniert es

```
Browser / Handy
      │ HTTPS
      ▼
┌──────────────────────────────┐
│  ein Container               │
│  nginx → FastAPI → MCP-Tools │   Orchestrator-LLM (Claude oder Ollama)
└──────────────┬───────────────┘
               ▼
   /workspace/mailboxes/<agent>/     inbox/*.json · outbox/*.json
               ▲
               │ SSH-Tunnel oder Token-HTTPS
┌──────────────┴───────────────┐
│  deine Rechner               │
│  agent_watcher.py            │   zieht Tasks → startet Claude Code → antwortet
└──────────────────────────────┘
```

Der Orchestrator führt auf deinen Rechnern **keinen Code** aus — er schreibt
nur Aufgaben. Ein kleiner Watcher (reine Python-**Standardbibliothek**, nichts
zu installieren) beansprucht jeden Task atomar, startet Claude Code und
schreibt das Ergebnis zurück.

## Schnellstart

```bash
git clone https://github.com/Meisterull/agent-dashboard && cd agent-dashboard
cp .env.example .env
# Provider in .env wählen:
#   ORCH_PROVIDER=anthropic  + ANTHROPIC_API_KEY=sk-ant-...     (Claude)
#   ORCH_PROVIDER=ollama     + OLLAMA_BASE_URL=...              (lokal, kein Key)
docker compose up --build
# → https://localhost:8443
```

Einen Rechner als Agent anschließen (Trockenlauf, ohne echtes Claude):

```bash
python3 scripts/agent_watcher.py --agent frontend \
  --root /pfad/zum/workspace/mailboxes --dry-run
```

Schritt-für-Schritt-Anleitung, Remote-Agenten und Troubleshooting:
**[START.md](START.md)**.

## Dokumentation

| Dokument | Inhalt |
|---|---|
| [START.md](START.md) | Setup Schritt für Schritt: `.env`, Konfig, erster Lauf, Remote-Agenten |
| [docs/REFERENZ.md](docs/REFERENZ.md) | vollständige Architektur- und API-Referenz |
| [PROJECT.md](PROJECT.md) | Designentscheidungen und Projektgeschichte |

## Sicherheitsmodell in einem Absatz

Secrets liegen nur in `.env`/Docker-Secrets und erreichen nie das Frontend.
Jeder Workspace-Zugriff wird aufgelöst und gegen die Workspace-Wurzel geprüft.
Die Mailbox schreibt `tmp` + `fsync` + `os.replace` — keine halb geschriebenen
Tasks; ein `.processing/`-Claim verhindert doppeltes Abholen. Die Identität
eines Agenten hängt am Transportkanal (eigener Port oder Token je Agent,
Prüfung in konstanter Zeit, Sperre nach wiederholten Fehlversuchen) — kein
Aufrufer kann sich per Parameter als ein anderer Agent ausgeben.

## Lizenz

[AGPL-3.0](LICENSE). Bau gern darauf auf — aber halt es offen.
