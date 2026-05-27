---
name: gog
description: |
  Google Workspace via the gog CLI. Use for Gmail (email/inbox/send/reply/drafts),
  Calendar (events/meetings/availability/RSVP/focus time/OOO), Drive (files/upload/
  download/share), Docs (create/read/write), Sheets (create/read/write/append),
  Contacts, Tasks. Invoke with the `exec` tool. Binary:
  `/sandbox/.config/gogcli/bin/gog` (auto-authed via host-side push daemon).
  All output is JSON on stdout.

  FAST PATH — go straight to the right command, do not pre-explore:
  - "show/list/summarize my last N emails":
      ONE call: `exec /sandbox/.config/gogcli/bin/gog gmail search in:inbox --max N`
      Result already has id, date, from, subject, labels per thread — enough to
      summarize. DO NOT loop `gmail get` per result.
  - "read/summarize THAT one email":
      `exec /sandbox/.config/gogcli/bin/gog gmail thread get <id> --sanitize-content`
      (strips HTML, removes URLs, drops raw SMTP payload — ~1 KB clean JSON).
  - "send an email":
      `exec /sandbox/.config/gogcli/bin/gog gmail send --to a@b.com --subject "S" --body-html "<p>...</p>"`
  - "reply to that email":
      `... gmail send --reply-to-message-id <id> --body "..."` (add `--reply-all` to copy everyone).
  - "what's on my calendar today / this week":
      `exec /sandbox/.config/gogcli/bin/gog calendar events primary --today`
      (other shortcuts: `--tomorrow`, `--week`, `--days N`, or `--from <RFC3339> --to <RFC3339>`).
  - "schedule a meeting":
      `... gog calendar create primary --summary "Sync" --from "2026-05-28T14:00:00-04:00" --to "2026-05-28T14:30:00-04:00" --attendees a@co.com,b@co.com`
  - "create a Google Doc / Sheet from scratch":
      `... gog docs create "Title"` (returns id + webViewLink)
      `... gog sheets create "Title"`
  - "upload a file to Drive (optionally as a native Google Doc/Sheet)":
      `... gog drive upload /tmp/file.pdf` (binary upload)
      `... gog drive upload /tmp/notes.md --name "Notes" --convert` (convert to Google Doc)
  - "read a Google Doc as text" / "read cells from a Sheet":
      `... gog docs cat <docId>` / `... gog sheets get <sheetId> "A1:D10"`

  DO NOT:
  - Call `memory_search` first — Gmail/Calendar/Drive data does not live in memory.
  - Call `gog --help`, `gog status`, or `gog <area> --help` before your first real
    command. Only run `<that command> --help` if a call fails with "unknown flag".
  - Call `gog gmail get` per item in a list — use the `search` output you already have.

  Auth notes: tokens auto-rotate; if a call returns 401, retry once after ~5s.
  The harmless `cannot create /proc/self/oom_score_adj: Permission denied` line on
  stderr can be ignored; the JSON on stdout is the answer. If a Google API returns
  "API is not enabled" (e.g. Docs API), the OAuth client needs that API enabled in
  Google Cloud Console — surface the error to the user, don't retry.
---

# gog — Google Workspace CLI

> **Tool to use:** `exec`
> **Binary:** `/sandbox/.config/gogcli/bin/gog` (always use the full path; not on `$PATH`)
> **Output:** JSON on stdout. Parse it directly.
> **Auth:** access token is auto-rotated by a host-side push daemon; no auth steps needed.

## How to call it

Always call the `exec` tool with the full path. Example tool call:

```json
{
  "tool": "exec",
  "input": { "command": "/sandbox/.config/gogcli/bin/gog gmail search in:inbox --max 5" }
}
```

If a command fails with "unknown flag" or "unknown command", run `<that command> --help` once and read the output. Do not loop on `--help`.

## Plan first, then call

**Before any tool call, ask: "does the answer already exist in what I have?"**
- For Gmail "show / list / summarize my latest N emails", `gmail search` returns subject + from + date per thread. Stop there. Do **not** call `gmail get` per result.
- For Calendar "what is on my calendar", `events list` returns enough; do not fetch each event individually.
- Only fetch a single item's body when the user explicitly asks to read or quote that one item.

## Most common one-liners (verified)

```bash
# ---------- Gmail ----------
# List / overview — search alone returns id+date+from+subject+labels per thread.
# That is enough for "what are my latest emails / summarize my last N by subject".
/sandbox/.config/gogcli/bin/gog gmail search in:inbox --max 10
/sandbox/.config/gogcli/bin/gog gmail search 'is:unread newer_than:1d' --max 10
/sandbox/.config/gogcli/bin/gog gmail search 'from:alice@example.com has:attachment'

# Read ONE specific message body (use --sanitize-content for ~1 KB clean output)
/sandbox/.config/gogcli/bin/gog gmail thread get <threadId> --sanitize-content

# Send
/sandbox/.config/gogcli/bin/gog gmail send --to a@co.com --subject "Hi" --body-html "<p>Hello</p>"
/sandbox/.config/gogcli/bin/gog gmail send --to a@co.com --cc b@co.com --bcc c@co.com --subject "Update" --body "Plain text"
/sandbox/.config/gogcli/bin/gog gmail send --to user@example.com --subject "Report" --body "See attached" --attach /tmp/file.pdf

# Reply (in-thread, preserves headers)
/sandbox/.config/gogcli/bin/gog gmail send --reply-to-message-id <messageId> --body "Thanks"
/sandbox/.config/gogcli/bin/gog gmail send --thread-id <threadId> --reply-all --body "Agreed"

# Drafts
/sandbox/.config/gogcli/bin/gog gmail drafts list
/sandbox/.config/gogcli/bin/gog gmail drafts create --to a@co.com --subject "Draft" --body "WIP"

# Organize
/sandbox/.config/gogcli/bin/gog gmail archive <messageId>
/sandbox/.config/gogcli/bin/gog gmail mark-read <messageId>
/sandbox/.config/gogcli/bin/gog gmail trash <messageId>

# ---------- Calendar ----------
# List events — natural-language time windows
/sandbox/.config/gogcli/bin/gog calendar events primary --today
/sandbox/.config/gogcli/bin/gog calendar events primary --tomorrow
/sandbox/.config/gogcli/bin/gog calendar events primary --week
/sandbox/.config/gogcli/bin/gog calendar events primary --days 14
# Explicit window (RFC3339, dates also accepted)
/sandbox/.config/gogcli/bin/gog calendar events primary --from "2026-05-28" --to "2026-06-04"

# Create event (note: --from / --to, NOT --start / --end)
/sandbox/.config/gogcli/bin/gog calendar create primary \
  --summary "Project sync" \
  --from "2026-05-28T14:00:00-04:00" --to "2026-05-28T14:30:00-04:00" \
  --attendees a@co.com,b@co.com \
  --description "Weekly status" \
  --location "Zoom"

# All-day event
/sandbox/.config/gogcli/bin/gog calendar create primary --summary "Holiday" --from "2026-07-04" --to "2026-07-05" --all-day

# Focus time / OOO / availability
/sandbox/.config/gogcli/bin/gog calendar focus-time --from "2026-05-29T13:00:00-04:00" --to "2026-05-29T15:00:00-04:00"
/sandbox/.config/gogcli/bin/gog calendar out-of-office --from "2026-06-10" --to "2026-06-12"
/sandbox/.config/gogcli/bin/gog calendar freebusy --from "2026-05-28T09:00" --to "2026-05-28T17:00"

# Get / delete / respond
/sandbox/.config/gogcli/bin/gog calendar event primary <eventId>
/sandbox/.config/gogcli/bin/gog calendar delete primary <eventId> -y
/sandbox/.config/gogcli/bin/gog calendar respond primary <eventId> --response accepted

# ---------- Drive ----------
/sandbox/.config/gogcli/bin/gog drive ls
/sandbox/.config/gogcli/bin/gog drive search "quarterly report"
/sandbox/.config/gogcli/bin/gog drive download <fileId> --out /tmp/file.pdf
/sandbox/.config/gogcli/bin/gog drive upload /tmp/file.pdf
# Upload a local markdown file as a native Google Doc:
/sandbox/.config/gogcli/bin/gog drive upload /tmp/notes.md --name "Project notes" --convert
# Convert to a specific Google format (doc|sheet|slides):
/sandbox/.config/gogcli/bin/gog drive upload /tmp/data.csv --convert-to sheet
# Folders, share, delete
/sandbox/.config/gogcli/bin/gog drive mkdir "Reports"
/sandbox/.config/gogcli/bin/gog drive share <fileId> --role reader --type user --email a@co.com
/sandbox/.config/gogcli/bin/gog drive delete <fileId> -y

# ---------- Docs (requires Docs API enabled on the OAuth client) ----------
/sandbox/.config/gogcli/bin/gog docs create "My new doc"           # returns id + webViewLink
/sandbox/.config/gogcli/bin/gog docs cat <docId>                   # read as plain text
/sandbox/.config/gogcli/bin/gog docs write <docId> --text "Hello"  # write content (--file path also works)
/sandbox/.config/gogcli/bin/gog docs find-replace <docId> "old" "new"
/sandbox/.config/gogcli/bin/gog docs export <docId> --format pdf --out /tmp/out.pdf

# ---------- Sheets ----------
/sandbox/.config/gogcli/bin/gog sheets create "My sheet"
/sandbox/.config/gogcli/bin/gog sheets get <sheetId> "A1:D10"
/sandbox/.config/gogcli/bin/gog sheets update <sheetId> "A1" "Hello"
/sandbox/.config/gogcli/bin/gog sheets append <sheetId> "Sheet1!A:C" '[["row","of","values"]]'
/sandbox/.config/gogcli/bin/gog sheets clear <sheetId> "A1:Z100"

# ---------- Contacts / Tasks / Identity ----------
/sandbox/.config/gogcli/bin/gog contacts search "Sarah"
/sandbox/.config/gogcli/bin/gog tasks lists
/sandbox/.config/gogcli/bin/gog tasks create <tasklistId> --title "Follow up with client" --due "2026-05-30"
/sandbox/.config/gogcli/bin/gog me
```

## Recipes (do this, not that)

**"What are my latest N emails / show me my inbox / summarize my last N emails"**
- **One** call: `gog gmail search in:inbox --max N`. Summarize from subjects/from.
- **DO NOT** loop `gog gmail thread get <id>` for each result.

**"Summarize THAT email" / "read me email X in detail"**
- One `gog gmail thread get <id> --sanitize-content` for the specific id.

**"Find emails about <topic> and tell me the key points"**
- `gog gmail search '<topic>' --max 5` for candidates.
- Then `gog gmail thread get <id> --sanitize-content` for only the 1-2 most relevant.

**"Send an email to X about Y"**
- One `gog gmail send --to X --subject "…" --body-html "<p>…</p>"` call. No discovery first.

**"Schedule a meeting with X next Friday at 2pm"**
- Resolve "next Friday 2pm" to RFC3339 in your head, then one `gog calendar create primary --summary "…" --from "…" --to "…" --attendees X`.
- Use `gog calendar freebusy` first only if the user said "find a time that works for everyone".

**"Create a Google Doc / Sheet with these notes"**
- Doc from scratch: `gog docs create "Title"` → write with `gog docs write <id> --text "..."`.
- Doc from local markdown: `gog drive upload notes.md --name "Title" --convert` (single call).
- Sheet: `gog sheets create "Title"` → `gog sheets update <id> "A1" "data"`.

## Output-size rules of thumb

| Command | Approx size | When to use |
|---|---|---|
| `gmail search` | ~1-2 KB / 10 results | List, overview, summarize |
| `gmail thread get <id> --sanitize-content` | ~1 KB per message | Read one body |
| `gmail thread get <id>` (no flags) | ~5-10 KB (raw SMTP headers) | Avoid |
| `gmail get <id>` | ~5-10 KB raw payload | Prefer `thread get --sanitize-content` |
| `calendar events --today` | ~0.5-2 KB | Day/week view |
| `drive ls` / `drive search` | ~1-3 KB | List files |
| `docs cat <id>` | doc size | Reading full doc text |
| `sheets get <id> "A1:Z100"` | range size | Reading cells |

Flags that help any JSON command:
- `--results-only` — drop envelope fields like `nextPageToken`
- `--select fields,csv` — pick specific output fields (best-effort)
- `--fields` — typed projection on most commands

## Help discovery

- `gog --help`, then `gog <area> --help` (e.g. `gog gmail --help`). One level is enough; do not recurse on subcommands you have already used successfully.

## Notes

- **Token rotation:** access token rotates ~55 min via the host-side push daemon; on a 401, retry once after 5 s. Never write `~/.gogcli` or `credentials.json` yourself.
- **Read-only mode for safety:** the wrapper supports `--gmail-no-send` to block Gmail send (useful for review-only demos).
- **Docs/Sheets/Slides APIs:** the OAuth client must have each API enabled in Google Cloud Console for those subcommands to work. Gmail / Calendar / Drive are enabled by default in most OAuth projects.
- **Stderr noise:** `/bin/bash: 1: cannot create /proc/self/oom_score_adj: Permission denied` is harmless container chatter; the JSON on stdout is the answer.
- **Network scope:** sandbox network policy restricts egress to Google API hosts only.
