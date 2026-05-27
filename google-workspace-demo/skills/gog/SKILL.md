---
name: gog
description: |
  Google Workspace via the gog CLI. Use for Gmail (email/inbox/send/reply/drafts),
  Calendar (events/meetings/availability/RSVP/focus time/OOO), Drive (files/upload/
  download/share), Docs (read/write/edit), Sheets (read/write cells), Contacts, Tasks.
  Invoke with the `exec` tool. Binary: `/sandbox/.config/gogcli/bin/gog` (auto-authed
  via host-side push daemon). All output is JSON on stdout.

  FAST PATH — go straight to the right command, do not pre-explore:
  - "show/list/summarize my last N emails":
      ONE call: `exec /sandbox/.config/gogcli/bin/gog gmail search in:inbox --max N`
      The result already has id, date, from, subject, labels per thread — that is
      enough to summarize. DO NOT loop `gmail get` per result.
  - "read/summarize THAT one email":
      ONE call: `exec /sandbox/.config/gogcli/bin/gog gmail thread get <id> --sanitize-content`
      (sanitize-content strips HTML, removes URLs, drops the raw SMTP payload —
      ~1 KB clean JSON instead of ~10 KB of noise).
  - "send email": ONE `gog gmail send --to … --subject … --body-html "<p>…</p>"`.
  - "what's on my calendar today/week":
      ONE `gog calendar events list --time-min now --time-max +1d` (or +7d).

  DO NOT:
  - Call `memory_search` first — Gmail/Calendar/Drive queries do not live in memory.
  - Call `gog --help`, `gog status`, or `gog <area> --help` before your first real
    command. Read SKILL.md only if a command fails with "unknown flag/command".
  - Call `gog gmail get` per item in a list — use the `search` output you already have.

  Auth notes: tokens auto-rotate; if a call returns 401, retry once after ~5s. The
  harmless `cannot create /proc/self/oom_score_adj: Permission denied` line on
  stderr can be ignored; the JSON on stdout is the answer.
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

## Plan first, then call

**Before any tool call, ask: "does the answer already exist in what I have?"**
- For Gmail "show / list / summarize my latest N emails", `gmail search` returns subject + from + date for each. Stop there. Do **not** call `gmail get` or `gmail thread get` per result.
- For Calendar "what is on my calendar", `events list` returns enough; do not fetch each event individually.
- Only fetch a single item's body when the user explicitly asks to read or quote that one item.

## Most common one-liners

These cover ~80% of asks. Copy-paste, substitute, run. **Stop at the first command that has enough information to answer.**

```bash
# Gmail: list / overview — search alone returns id+date+from+subject+labels for each
# thread. That is ENOUGH for "what are my latest emails / show me my inbox /
# summarize my last N emails by subject". Do not call `gmail get` per item.
/sandbox/.config/gogcli/bin/gog gmail search in:inbox --max 10
/sandbox/.config/gogcli/bin/gog gmail search 'is:unread newer_than:1d' --max 10
/sandbox/.config/gogcli/bin/gog gmail search 'from:alice@example.com has:attachment'

# Gmail: read ONE specific message body (only when the user asks "what does
# email X say" or "read me email X"). ALWAYS use --sanitize-content for
# agent consumption: it strips HTML, removes URLs, and returns a small
# clean JSON (~1KB) instead of the raw Gmail payload (~5-10KB of noise).
/sandbox/.config/gogcli/bin/gog gmail thread get <threadId> --sanitize-content

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

## Recipes (do this, not that)

**"What are my latest N emails / show me my inbox / summarize my last N emails"**
- **One** call: `gog gmail search in:inbox --max N`. The response already contains
  subject, from, date, and labels per thread — that is enough to summarize.
- **DO NOT** loop `gog gmail thread get <id>` for each result. That turns one
  fast call into N slow calls and floods context with header noise.

**"Summarize THAT email" / "read me email X in detail"**
- One `gog gmail thread get <id> --sanitize-content` for the specific id. Read
  the `body` field; ignore `payload.headers` (it is just SMTP metadata).

**"Find emails about <topic> and tell me the key points"**
- `gog gmail search '<topic>' --max 5` to pick candidates from subjects.
- Then `gog gmail thread get <id> --sanitize-content` only for the 1-2 most
  relevant; do not fetch all matches.

**"Send an email to X about Y"**
- One `gog gmail send --to X --subject "…" --body-html "<p>…</p>"` call. Done.
- No discovery calls needed first.

**"What's on my calendar today / this week?"**
- One `gog calendar events list --time-min now --time-max +1d` (or `+7d`). Summarize.

## Output-size rules of thumb

- `gmail search` → small (~1-2KB for 10 results). Always safe.
- `gmail thread get <id> --sanitize-content` → small (~1KB per message). Use for bodies.
- `gmail thread get <id>` (no flags) → large (~5-10KB, includes raw SMTP headers). Avoid.
- `gmail get <id>` → large (~5-10KB raw payload). Prefer `thread get --sanitize-content`.
- `--results-only` strips envelope fields like `nextPageToken` from any JSON command.

## Help discovery

- `gog --help`, then `gog <area> --help` (e.g. `gog gmail --help`). One level of help is enough; do not recurse on subcommands you have already used successfully in this session.

## Notes

- Tokens auto-rotate; if a call returns `401`/`unauthorized`, wait ~5s and retry **once** before reporting failure (the push daemon refreshes every ~55 min plus on demand).
- `gog` prints a harmless `cannot create /proc/self/oom_score_adj: Permission denied` line on stderr inside the sandbox — ignore it, the JSON on stdout is correct.
- This binary talks to Google APIs only. Network egress for other hosts is blocked by sandbox policy.

## Notes

- Tokens auto-rotate; if a call returns `401`/`unauthorized`, wait ~5s and retry **once** before reporting failure (the push daemon refreshes every ~55 min plus on demand).
- `gog` prints a harmless `cannot create /proc/self/oom_score_adj: Permission denied` line on stderr inside the sandbox — ignore it, the JSON on stdout is correct.
- Full subcommand tree: `gog --help`, then `gog <area> --help` (e.g. `gog gmail --help`). One level of help is enough; do not recurse.
- This binary talks to Google APIs only. Network egress for other hosts is blocked by sandbox policy.
