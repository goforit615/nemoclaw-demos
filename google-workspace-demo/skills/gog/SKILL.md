---
name: gog
description: "Google Workspace via the gog CLI. Use when the user asks about Gmail (email, inbox, send, reply, drafts), Calendar (events, meetings, availability, RSVP, focus time, OOO), Drive (files, upload, download, share), Docs (read/write/edit document), Sheets (read/write cells), Contacts (lookup), or Tasks (to-do list). Invoke via the `exec` tool with `command: \"/sandbox/.config/gogcli/bin/gog <subcommand>\"`. Output is JSON."
---

# gog — Google Workspace CLI

> **Tool to use:** `exec`
> **Binary:** `/sandbox/.config/gogcli/bin/gog` (always invoke with the full path; it is not on `$PATH`)
> **Output:** JSON on stdout. Parse it directly.
> **Auth:** token is auto-rotated by a host-side push daemon; you do not need to authenticate.

## How to call it

Always call the `exec` tool with the full path. Example tool call:

```json
{
  "tool": "exec",
  "input": { "command": "/sandbox/.config/gogcli/bin/gog gmail search in:inbox --max 5" }
}
```

If a command fails with "unknown flag" or "unknown command", run `<that command> --help` once and read the output. Do not loop on `--help`; one help call is enough to recover.

## Most common one-liners

These cover ~80% of asks. Copy-paste, substitute, run.

```bash
# Gmail: show N newest inbox messages
/sandbox/.config/gogcli/bin/gog gmail search in:inbox --max 5

# Gmail: search with full Gmail query syntax
/sandbox/.config/gogcli/bin/gog gmail search 'is:unread newer_than:1d' --max 10
/sandbox/.config/gogcli/bin/gog gmail search 'from:alice@example.com has:attachment'

# Gmail: read one message or full thread
/sandbox/.config/gogcli/bin/gog gmail get <messageId>
/sandbox/.config/gogcli/bin/gog gmail thread get <threadId>

# Gmail: send (use --body-html for formatting)
/sandbox/.config/gogcli/bin/gog gmail send --to a@co.com --subject "Hi" --body-html "<p>Hello</p>"

# Gmail: reply in-thread
/sandbox/.config/gogcli/bin/gog gmail send --reply-to-message-id <messageId> --subject "Re: …" --body "Thanks"

# Calendar: today / next 7 days
/sandbox/.config/gogcli/bin/gog calendar events list --time-min now --time-max +1d
/sandbox/.config/gogcli/bin/gog calendar events list --time-min now --time-max +7d

# Calendar: create event
/sandbox/.config/gogcli/bin/gog calendar events create --summary "Sync" --start "2026-05-28T14:00" --end "2026-05-28T14:30" --attendees a@co.com,b@co.com

# Drive: search / list / download / upload
/sandbox/.config/gogcli/bin/gog drive search "quarterly report"
/sandbox/.config/gogcli/bin/gog drive ls
/sandbox/.config/gogcli/bin/gog drive download <fileId> --out /tmp/file.pdf
/sandbox/.config/gogcli/bin/gog drive upload /tmp/file.pdf

# Docs / Sheets
/sandbox/.config/gogcli/bin/gog docs get <docId>
/sandbox/.config/gogcli/bin/gog sheets read <sheetId> --range "A1:D10"

# Identity
/sandbox/.config/gogcli/bin/gog me
```

## Recipes

**"Summarize my last N emails"**
1. `gog gmail search in:inbox --max N` → get list of thread/message ids
2. For the top items, optionally `gog gmail get <messageId>` to fetch body, then summarize.
3. Do **not** call `--help` on subcommands you have already used in this session.

**"Send an email to X about Y"**
1. Compose subject + html body in your head.
2. One `gog gmail send --to X --subject "…" --body-html "<p>…</p>"` call. Done.

**"What's on my calendar today?"**
1. `gog calendar events list --time-min now --time-max +1d` → summarize.

## Notes

- Tokens auto-rotate; if a call returns `401`/`unauthorized`, wait ~5s and retry **once** before reporting failure (the push daemon refreshes every ~55 min plus on demand).
- `gog` prints a harmless `cannot create /proc/self/oom_score_adj: Permission denied` line on stderr inside the sandbox — ignore it, the JSON on stdout is correct.
- Full subcommand tree: `gog --help`, then `gog <area> --help` (e.g. `gog gmail --help`). One level of help is enough; do not recurse.
- This binary talks to Google APIs only. Network egress for other hosts is blocked by sandbox policy.
