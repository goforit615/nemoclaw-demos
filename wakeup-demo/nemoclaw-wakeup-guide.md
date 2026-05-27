# NemoClaw Wakeup

A host-controlled scheduled trigger that periodically wakes the OpenClaw agent inside an OpenShell sandbox to execute a configurable task list. The agent reads its instructions from `WAKEUP.md` inside the sandbox and can modify its own task list when users interact with it — but the **schedule itself is locked down** and controlled entirely from the host, outside the sandbox.

This architecture leverages OpenShell's security model:

- **The agent cannot schedule itself.** OpenShell sandboxes have no host cron, no init system, and no persistent background processes the agent can create. The agent is purely reactive — it only runs when an external trigger fires.
- **The agent cannot escape the sandbox.** All network egress is policy-enforced. The agent can only reach approved endpoints through the OpenShell gateway.
- **The host controls the timer.** The cron schedule runs outside the sandbox and cannot be modified by the agent. If the agent is compromised or misbehaves, it cannot increase its own execution frequency or persist beyond its session.

### Why this still matters with OpenClaw's built-in scheduling

OpenClaw 2026.5.x ships in-gateway scheduling primitives (`openclaw system heartbeat`, `openclaw cron`). Those run **inside** the gateway and the agent itself can reconfigure them. They are great for fast iteration; they are not great when you want a tamper-resistant timer.

The wakeup-demo's host-cron + SSH approach is still the right choice when you want:

- A trigger source the agent **cannot** disable, accelerate, or schedule extra runs against (combine with `--harden`, below).
- A clean audit trail outside the sandbox (`~/.nemoclaw/wakeup/wakeup.log`).
- The ability to keep wakeup behavior stable across sandbox rebuilds — re-running `./install.sh` re-detects paths and re-deploys everything.

If you don't need any of that, prefer the native primitives — they're one config field.

---

## Quick Start

```bash
git clone https://github.com/brevdev/nemoclaw-demos.git
cd nemoclaw-demos/wakeup-demo
./install.sh
```

The installer will:
1. Detect your sandbox (or let you choose).
2. Verify SSH connectivity to the sandbox.
3. **Auto-detect the OpenClaw layout** (`/sandbox/.openclaw/` vs. legacy `/sandbox/.openclaw-data/`).
4. Ask how often to wake the agent (5, 10, 15, 30, 60 min, or custom).
5. Deploy the NemoClaw Wakeup skill into the sandbox.
6. On the new layout: enable the skill in `openclaw.json` and set `tools.profile = "coding"` so the agent can actually use `read`/`exec` to load `WAKEUP.md`.
7. Seed a default `WAKEUP.md` if one doesn't exist.
8. Create the cron job.

### Hardened install

```bash
./install.sh --harden                      # disable in-gateway scheduling
./install.sh --unharden                    # reverse it
./install.sh my-sandbox --interval 10 --harden   # combined
```

`--harden` edits the sandbox's `openclaw.json` to:

- set `agents.defaults.heartbeat.every = ""` so `openclaw system heartbeat` is disabled, and
- add `cron` and `system` to `tools.deny` so the agent cannot re-enable in-gateway scheduling from a chat turn.

The previous values are backed up to `~/.nemoclaw/wakeup/harden-backup.json` and restored exactly by `--unharden`. Restart the OpenClaw gateway (or reconnect the sandbox) for changes to take effect.

---

## How It Works

```
+--- Host (cron) ----------------+     +--- Sandbox (OpenShell) ------------------+
|                                |     |                                          |
|  every N minutes:              |     |  /sandbox/.openclaw/workspace/WAKEUP.md  |
|    SSH into sandbox (~400ms) --|---->|    "Check my email and summarize..."     |
|    fire: openclaw agent        |     |                                          |
|    unique session ID           |     |  Agent reads file fresh, follows it.     |
|    flock prevents overlap      |     |  Uses skills (gog, planet, brave, ...).  |
|                                |     |                                          |
+--------------------------------+     +------------------------------------------+
```

Each pulse:
1. Acquires an exclusive lock (`flock`) — if the previous pulse is still running, this one skips.
2. Generates a unique session ID (`wakeup-<timestamp>-<pid>`) — no context bleed between pulses.
3. SSHs into the sandbox via `openshell ssh-proxy` (~400ms).
4. Sends one message to the agent: **"Read WAKEUP.md and follow the instructions"**.
5. The agent reads the file fresh, executes, and the session ends.

---

## Scheduling options compared

| Option | Owns the timer | Agent can re-arm itself? | Survives sandbox restart | When to pick it |
|---|---|---|---|---|
| **Host cron (this demo)** | Host (you) | No | Yes | Tamper-resistance, audit trail, multi-sandbox fan-out |
| **Host cron + `--harden`** | Host (you) | No, and in-gateway sched is denied | Yes | Production-style lockdown |
| `openclaw system heartbeat` | Gateway | Yes (just edits config) | Yes | Quick iteration, single sandbox, trusted agent |
| `openclaw cron` | Gateway | Yes | Yes | One-off "run X at 9am" tasks set by the agent |

---

## Changing What the Agent Does

Tell the agent to update its wakeup tasks. You never need to touch the cron job or reinstall.

### Via TUI or Telegram (recommended)

Connect to your sandbox and tell the agent:

```
Update my nemoclaw wakeup to check my email and respond to anything from boss@company.com
```

```
Add an auto-reply rule to my nemoclaw wakeup:
Reply to emails from boss@company.com confirming I received the message
```

```
Show me what my nemoclaw wakeup is currently set to do
```

```
Update my nemoclaw wakeup to also check my Google Calendar and warn me about
conflicts in the next 2 hours
```

### Manual editing (optional)

The install script handles deploying `WAKEUP.md` automatically. If you prefer to edit it manually, the actual path depends on which OpenClaw layout your sandbox uses (the installer prints it). Default is the new layout:

```bash
# SSH into sandbox
openshell sandbox connect <sandbox-name>
nano /sandbox/.openclaw/workspace/WAKEUP.md          # new layout (openshell ≥ 0.0.44)
# or
nano /sandbox/.openclaw-data/workspace/WAKEUP.md     # legacy layout
```

`./install.sh --status` always shows the actual path in use.

---

## Changing the Schedule

The timer is controlled by the host cron job, not by the agent. The agent knows this and will direct users to run these commands if asked.

```bash
# Change interval
./install.sh --interval 30

# Check current status (prints sandbox, layout, paths, hardened state, last log lines)
./install.sh --status
```

---

## Commands

| Command | Description |
|---|---|
| `./install.sh` | Install (interactive) |
| `./install.sh my-sandbox --interval 15` | Install with flags |
| `./install.sh --harden` | Lock down: disable in-gateway scheduling |
| `./install.sh --unharden` | Reverse `--harden` (restore in-gateway scheduling) |
| `./install.sh --status` | Show current status (sandbox, layout, paths, hardened state) |
| `./install.sh --interval 30` | Change interval |
| `./install.sh --uninstall` | Remove everything (also unhardens if needed) |
| `./update.sh` | Re-deploy skill after a `git pull` (preserves WAKEUP.md and config) |
| `~/.nemoclaw/wakeup/wakeup.sh` | Test manually |
| `tail -f ~/.nemoclaw/wakeup/wakeup.log` | Watch logs |

---

## File Structure

```
~/.nemoclaw/wakeup/
├── wakeup.sh             # Script that cron runs (SSH trigger)
├── config.env            # Settings (sandbox, interval, openshell path, layout, paths, hardened)
├── harden-backup.json    # Created only when --harden is active; consumed by --unharden
├── wakeup.lock           # flock file (prevents overlapping runs)
└── wakeup.log            # Output log (auto-rotated at 1000 lines)

Inside the sandbox (paths depend on auto-detected layout):

  New layout (openshell ≥ 0.0.44):
    /sandbox/.openclaw/workspace/WAKEUP.md              # Agent reads this
    /sandbox/.openclaw/skills/nemoclaw-wakeup/SKILL.md  # Agent skill
    /sandbox/.openclaw/openclaw.json                    # Skill registry + tools.profile

  Legacy layout:
    /sandbox/.openclaw-data/workspace/WAKEUP.md
    /sandbox/.openclaw-data/skills/nemoclaw-wakeup/SKILL.md
```

---

## Troubleshooting

| Issue | Fix |
|---|---|
| `openshell: command not found` | Installer auto-detects. Check `which openshell`. |
| SSH test fails | Is the sandbox running? `openshell sandbox list` |
| Agent says "I don't have a `read` tool" | The new layout needs `tools.profile = "coding"` in `openclaw.json`. Re-run `./install.sh` (it sets this automatically) or `./update.sh`. |
| Agent doesn't do anything | Edit `WAKEUP.md` with clearer instructions. |
| Agent sends Telegram messages | Add "Do NOT send Telegram messages" to WAKEUP.md rules. |
| Log shows SKIP | Previous pulse still running. Increase interval or simplify tasks. |
| Cron not firing (WSL) | Run `sudo service cron start` after opening WSL. |
| Agent created its own `openclaw system heartbeat` | Run `./install.sh --harden` to deny `cron`/`system` tools in the sandbox. |

---

## Compatibility

Works on **WSL**, **Brev**, and any Linux host with SSH. Uses `openshell ssh-proxy` for connectivity.

Supported OpenClaw layouts:

- **New** — `/sandbox/.openclaw/` (openshell ≥ 0.0.44, openclaw ≥ 2026.5.x). Auto-enables the skill in `openclaw.json` and ensures `tools.profile = "coding"`.
- **Legacy** — `/sandbox/.openclaw-data/`. Older OpenShell builds. Skill is deployed by file; no `openclaw.json` mutation is needed.

The installer detects the layout on every run, so a sandbox image upgrade between installs is picked up by re-running `./install.sh`.

> **WSL note:** Cron may not start automatically. Run `sudo service cron start` after opening WSL.

---

Created by **Tim Klawa** (tklawa@nvidia.com)
