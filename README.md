# Agent Dashboard

**Command your Claude Code agents from anywhere — self-hosted mission control
in a single container, orchestrated by Claude or a fully local LLM.**

Deutsche Fassung: **[README.de.md](README.de.md)** · License: **AGPL-3.0**

<!-- TODO: record a 30-second demo (phone view: chat → task lands on agent →
     result comes back) and drop it in docs/demo.gif, then uncomment:
<p align="center"><img src="docs/demo.gif" alt="Agent Dashboard demo" width="720"></p>
-->

You chat with an orchestrator LLM in your browser. It plans, then delegates
real work to **Claude Code running on your own machines** — your desktop, your
build box, the laptop in the other room. Results, questions and progress flow
back into one dashboard that works just as well on a phone as on a desktop.
No SaaS, no telemetry, one hardened Docker container on your own server.

## Highlights

- **Orchestrator chat** — an LLM plans tasks and delegates them via MCP tools.
  Works with the **Claude API** or **any tool-capable Ollama model** (fully
  local, no API key).
- **File-mailbox transport** — every task and reply is a plain JSON file in an
  `inbox/`/`outbox/` pair: robust, offline-tolerant, atomic, and trivially
  debuggable with `cat`.
- **Agents can ask back** — a worker that needs clarification parks its task,
  you answer in a banner, the task resumes with your answer in context.
- **Automatic mode** — per agent, the dashboard keeps a watcher running on the
  remote machine that works through its inbox on its own; a global emergency
  stop halts everything at once.
- **Real SSH terminals in the browser** — tabs per host, several terminals per
  connection, sessions survive disconnects (reattach later, even from another
  device), swipe-scrolling that actually works on touch screens.
- **Workspace views** — arrange chat, files, terminals and the agent monitor
  freely and save named layouts.
- **Agents without SSH** — machines behind NAT (say, a Windows laptop with
  Claude Desktop) join via token-authenticated HTTPS instead of a tunnel.
- **Identity from the channel** — each agent gets its own MCP port or token;
  the server derives *who is calling* from the channel itself, and optional
  per-agent tool allowlists limit what a channel may do.
- **Config-driven integrations** — attach named HTTP APIs (ticketing, ERP,
  home automation, …) as callable tools via a YAML file, no code.
- **English and German UI** — switchable in the settings, synced across your
  devices.
- **One hardened container** — nginx + FastAPI + MCP server under supervisord:
  non-root, `cap_drop: ALL`, no `docker.sock`, path-traversal-safe file access,
  password-protected UI.

## How it works

```
Browser / phone
      │ HTTPS
      ▼
┌──────────────────────────────┐
│  one container               │
│  nginx → FastAPI → MCP tools │   orchestrator LLM (Claude or Ollama)
└──────────────┬───────────────┘
               ▼
   /workspace/mailboxes/<agent>/     inbox/*.json · outbox/*.json
               ▲
               │ SSH tunnel or token HTTPS
┌──────────────┴───────────────┐
│  your machines               │
│  agent_watcher.py            │   pulls tasks → runs Claude Code → replies
└──────────────────────────────┘
```

The orchestrator never executes code on your machines — it only writes tasks.
A small watcher (Python **standard library only**, nothing to install) claims
each task atomically, runs Claude Code, and writes the result back.

## Quickstart

```bash
git clone https://github.com/Meisterull/agent-dashboard && cd agent-dashboard
cp .env.example .env
# pick a provider in .env:
#   ORCH_PROVIDER=anthropic  + ANTHROPIC_API_KEY=sk-ant-...     (Claude)
#   ORCH_PROVIDER=ollama     + OLLAMA_BASE_URL=...              (local, no key)
docker compose up --build
# → https://localhost:8443
```

Hook up a machine as an agent (dry run, no Claude needed):

```bash
python3 scripts/agent_watcher.py --agent frontend \
  --root /path/to/workspace/mailboxes --dry-run
```

Step-by-step setup, remote agents and troubleshooting: **[START.md](START.md)**.

## Documentation

| Document | Content |
|---|---|
| [START.md](START.md) | setup walkthrough: `.env`, config, first run, remote agents |
| [docs/REFERENZ.md](docs/REFERENZ.md) | full architecture and API reference |
| [PROJECT.md](PROJECT.md) | design decisions and project history |

The in-depth documentation is currently written in German — translations
welcome. The code and configuration are English-friendly throughout.

## Security model in one paragraph

Secrets live only in `.env`/Docker secrets and never reach the frontend. Every
workspace file access is resolved and checked against the workspace root. The
mailbox uses `tmp` + `fsync` + `os.replace`, so there are no half-written
tasks, and a `.processing/` claim prevents double pickup. Agent identity is
bound to the transport channel (per-agent port or token, constant-time check,
lockout after repeated failures) — a caller cannot impersonate another agent
by passing a parameter.

## License

[AGPL-3.0](LICENSE). Build something on top of it — just keep it open.
