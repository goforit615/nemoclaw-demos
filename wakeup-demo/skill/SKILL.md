---
name: nemoclaw-wakeup
description: |
  Host-side scheduled wakeup trigger for NemoClaw. Activates whenever the agent is
  woken by a cron pulse OR when the user asks about wakeup, heartbeat, scheduled
  tasks, cron, timer, interval, periodic checks, how often the agent runs, changing
  the schedule, what happens on wakeup, or editing WAKEUP.md.

  FAST PATH on every wakeup pulse:
    1. Read __WAKEUP_MD_PATH__ with the `read` tool. If that path does not exist,
       try /sandbox/.openclaw/workspace/WAKEUP.md then
       /sandbox/.openclaw-data/workspace/WAKEUP.md.
    2. Do EXACTLY what it says. Nothing more.
    3. End the turn. If WAKEUP.md says nothing actionable, end with no output.

  DO NOT:
    - Do NOT call memory_search, tool_search_code, or explore the filesystem
      before reading WAKEUP.md. Read it first.
    - Do NOT use cached or remembered instructions from previous sessions.
    - Do NOT try to run `crontab`, install schedulers, or modify the wakeup
      interval from inside the sandbox — host-cron owns the schedule.
    - Do NOT send Telegram, Discord, Slack, or email messages unless WAKEUP.md
      explicitly tells you to in this turn.

  The wakeup interval (__INTERVAL__ minutes) is controlled by a host-side cron job
  outside the sandbox. To change it, the user must run on the host:
    cd ~/nemoclaw-demos/wakeup-demo && ./install.sh --interval <minutes>
---

# NemoClaw Wakeup

You are being woken by a host-side scheduled trigger called **NemoClaw Wakeup**.
This is NOT a skill you invoke — it is the reason you are running right now.

## Current Schedule

- **Wakeup interval:** every **__INTERVAL__ minutes**
- **Last configured:** __INSTALLED_AT__
- **Controlled by:** host-side cron (outside the sandbox — you cannot change this)

## What just happened

A cron job on the **host machine** (outside the sandbox) fired and sent you a
message. You must now read your task file and follow the instructions inside it.

## Your task file

**Path:** `__WAKEUP_MD_PATH__`

If that file doesn't exist (e.g., the sandbox was rebuilt against a different
OpenClaw version), try in order:

1. `/sandbox/.openclaw/workspace/WAKEUP.md` (current layout)
2. `/sandbox/.openclaw-data/workspace/WAKEUP.md` (legacy layout)

Read this file **right now** with the `read` tool and follow every instruction
in it. Do NOT rely on memory from previous sessions. The file may have been
edited since the last time you ran.

## What you CAN do

- Read `__WAKEUP_MD_PATH__` to get your current instructions.
- Edit `__WAKEUP_MD_PATH__` when a user asks you to change your wakeup tasks
  (e.g., "add checking my calendar to your wakeup tasks").
- Use any installed skills (gog, planet, brave, etc.) as directed by WAKEUP.md.
- Report results in the current session.

## What you CANNOT do

**CRITICAL: Do NOT attempt any of the following. They will all fail or be unsafe.**

- Do NOT run `crontab` — it does not exist in the sandbox.
- Do NOT try to install cron or any scheduler — you cannot install packages.
- Do NOT try to create any timer, scheduler, background process, or daemon.
- Do NOT try to modify the wakeup interval from inside the sandbox.
- Do NOT try to stop or start the wakeup schedule from inside the sandbox.
- Do NOT call `openclaw system heartbeat` or `openclaw cron` to "fix" your
  schedule — if the deployment is `--harden`ed, those tools are denied; even
  when they exist, they would race with the host-cron and double-fire pulses.
- Do NOT send Telegram/Discord/Slack messages unless WAKEUP.md explicitly says to.
- Do NOT repeat actions from previous sessions — always read the file fresh.

## When a user asks to change the schedule

If a user asks you to change how often you wake up (the timer/interval),
respond with the current setting and direct them to the host:

> The wakeup is currently set to trigger every **__INTERVAL__ minutes**.
> This schedule is controlled by a host-side cron job outside this sandbox.
> I cannot change it from here. To modify the interval, run on the host:
>
> ```
> cd ~/nemoclaw-demos/wakeup-demo && ./install.sh --interval <minutes>
> ```
>
> For example, to change to every 30 minutes:
>
> ```
> cd ~/nemoclaw-demos/wakeup-demo && ./install.sh --interval 30
> ```
>
> Or check the current schedule with:
>
> ```
> cd ~/nemoclaw-demos/wakeup-demo && ./install.sh --status
> ```

## When a user asks to change wakeup tasks

If a user asks you to change **what** you do when you wake up, edit the
`__WAKEUP_MD_PATH__` file with their requested changes. Confirm the changes
were saved. The next wakeup pulse will use the updated file.
