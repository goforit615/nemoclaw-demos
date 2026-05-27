#!/usr/bin/env bash
set -euo pipefail

# ── NemoClaw Wakeup Installer ───────────────────────────────────
# Sets up a host-side cron job that periodically wakes the OpenClaw
# agent inside an OpenShell sandbox via SSH. The agent reads its
# instructions from <workspace>/WAKEUP.md.
#
# Trigger path: host cron → SSH → openclaw agent → reads WAKEUP.md
# SSH is used instead of `openshell sandbox exec` because exec is
# unreliable (hangs/aborts). SSH via openshell ssh-proxy is fast
# (~400ms) and always completes.
#
# Path layout — auto-detected, with fallback for older OpenShell:
#   New (openshell ≥ 0.0.44 / openclaw ≥ 2026.5.x):
#     workspace: /sandbox/.openclaw/workspace
#     skills:    /sandbox/.openclaw/skills
#     config:    /sandbox/.openclaw/openclaw.json   (skill registry + tools profile)
#   Legacy (older builds):
#     workspace: /sandbox/.openclaw-data/workspace
#     skills:    /sandbox/.openclaw-data/skills
#
# Optional flags:
#   --harden    Lock down the sandbox so this host-cron is the ONLY
#               scheduler — disables openclaw's in-gateway
#               `system heartbeat` and adds `cron`/`system` to the
#               tool denylist. Reversible with --unharden.
#   --unharden  Restore the openclaw.json values --harden changed.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
INSTALL_DIR="$HOME/.nemoclaw/wakeup"
SANDBOXES_JSON="$HOME/.nemoclaw/sandboxes.json"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

info()  { echo -e "${CYAN}  ▸ $1${NC}"; }
ok()    { echo -e "${GREEN}  ✓ $1${NC}"; }
warn()  { echo -e "${YELLOW}  ⚠ $1${NC}"; }
fail()  { echo -e "${RED}  ✗ $1${NC}"; exit 1; }

usage_exit() {
  echo ""
  echo "  Usage: ./install.sh [options] [sandbox-name]"
  echo ""
  echo "  Options:"
  echo "    --interval <minutes>  Wakeup interval in minutes (default: 10)"
  echo "    --harden              Disable OpenClaw's in-gateway scheduling so"
  echo "                          host-cron is the only trigger source"
  echo "    --unharden            Reverse --harden (restore in-gateway scheduling)"
  echo "    --uninstall           Remove wakeup cron job and files"
  echo "    --status              Show current wakeup status"
  echo "    -h, --help            Show this help"
  echo ""
  echo "  The agent reads its instructions from WAKEUP.md inside the sandbox."
  echo "  Edit it via the TUI, Telegram, or manually."
  echo ""
  exit 0
}

ssh_sandbox() {
  local sandbox="$1"; shift
  ssh -o StrictHostKeyChecking=no \
      -o UserKnownHostsFile=/dev/null \
      -o GlobalKnownHostsFile=/dev/null \
      -o LogLevel=ERROR \
      -o ConnectTimeout=10 \
      -o ProxyCommand="$OPENSHELL_BIN ssh-proxy --gateway-name nemoclaw --name $sandbox" \
      "sandbox@openshell-$sandbox" "$@" 2>/dev/null
}

# ── Path detection ────────────────────────────────────────────────
# Sets LAYOUT, WORKSPACE_DIR, SKILLS_DIR, OPENCLAW_JSON, WAKEUP_MD_PATH,
# SKILL_DEST based on what actually exists in the target sandbox.
detect_paths() {
  local sandbox="$1"
  if ssh_sandbox "$sandbox" "[ -d /sandbox/.openclaw/workspace ]"; then
    LAYOUT="new"
    WORKSPACE_DIR="/sandbox/.openclaw/workspace"
    SKILLS_DIR="/sandbox/.openclaw/skills"
    OPENCLAW_JSON="/sandbox/.openclaw/openclaw.json"
  elif ssh_sandbox "$sandbox" "[ -d /sandbox/.openclaw-data/workspace ]"; then
    LAYOUT="legacy"
    WORKSPACE_DIR="/sandbox/.openclaw-data/workspace"
    SKILLS_DIR="/sandbox/.openclaw-data/skills"
    OPENCLAW_JSON=""
  else
    # Brand new sandbox: prefer new layout, create dirs lazily.
    LAYOUT="new"
    WORKSPACE_DIR="/sandbox/.openclaw/workspace"
    SKILLS_DIR="/sandbox/.openclaw/skills"
    OPENCLAW_JSON="/sandbox/.openclaw/openclaw.json"
  fi
  WAKEUP_MD_PATH="$WORKSPACE_DIR/WAKEUP.md"
  SKILL_DEST="$SKILLS_DIR/nemoclaw-wakeup/SKILL.md"
}

# ── openclaw.json mutation helpers ────────────────────────────────
# All updates run a small Python program inside the sandbox over SSH so
# the JSON edit is atomic and we don't need jq.
#
# configure_openclaw_json: enable the nemoclaw-wakeup skill in the
# skill registry and ensure tools.profile is "coding" so the agent can
# actually use `read`/`exec` to load SKILL.md and run commands.
# Idempotent. No-op on legacy layouts.
configure_openclaw_json() {
  local sandbox="$1"
  [ -z "$OPENCLAW_JSON" ] && return 0
  if ! ssh_sandbox "$sandbox" "[ -f $OPENCLAW_JSON ]"; then
    warn "$OPENCLAW_JSON not found; skipping skill-registry + tools-profile update"
    return 0
  fi
  ssh_sandbox "$sandbox" "python3 - <<'PYEOF'
import json, sys
p = '$OPENCLAW_JSON'
d = json.load(open(p))
changed = False

# 1) Enable this skill in the registry so it surfaces in the system prompt.
entry = d.setdefault('skills', {}).setdefault('entries', {}).setdefault('nemoclaw-wakeup', {})
if entry.get('enabled') is not True:
    entry['enabled'] = True
    changed = True

# 2) Ensure the agent has exec/read/write tools surfaced in the prompt.
# 'coding' is the documented OpenClaw profile for sandboxes that run
# binaries. Without it, OpenClaw v2026.5.18+ defaults to compact tool-
# search mode which hides 'read' and the agent never loads SKILL.md.
tools = d.setdefault('tools', {})
if tools.get('profile') is None:
    tools['profile'] = 'coding'
    changed = True
elif tools.get('profile') != 'coding':
    print('WARN: tools.profile is set to %r; leaving as-is. If the agent fails to load SKILL.md, set it to \"coding\".' % tools.get('profile'))

if changed:
    json.dump(d, open(p, 'w'), indent=2)
    print('updated')
else:
    print('already configured')
PYEOF"
}

# apply_harden: disable in-gateway scheduling so host-cron is the only
# source of agent turns. Saves the prior values so --unharden can
# restore them exactly. Idempotent.
apply_harden() {
  local sandbox="$1"
  [ -z "$OPENCLAW_JSON" ] && { warn "harden: needs new layout (openclaw.json); skipping"; return 0; }
  if ! ssh_sandbox "$sandbox" "[ -f $OPENCLAW_JSON ]"; then
    warn "harden: $OPENCLAW_JSON not found; skipping"
    return 0
  fi
  local backup_path="$INSTALL_DIR/harden-backup.json"
  mkdir -p "$INSTALL_DIR"
  if [ -f "$backup_path" ]; then
    ok "Already hardened (backup at $backup_path); nothing to do"
    return 0
  fi
  ssh_sandbox "$sandbox" "python3 - <<'PYEOF'
import json, sys
p = '$OPENCLAW_JSON'
d = json.load(open(p))
backup = {}

# 1) Disable native heartbeat (record prior value).
agents = d.setdefault('agents', {}).setdefault('defaults', {})
hb = agents.setdefault('heartbeat', {})
backup['heartbeat.every'] = hb.get('every', None)
hb['every'] = ''   # empty string disables

# 2) Add cron + system to tools.deny so the agent cannot re-enable
#    in-gateway scheduling from a chat turn. Track what we added so
#    unharden only removes those, not pre-existing entries.
tools = d.setdefault('tools', {})
deny = list(tools.get('deny') or [])
added = []
for t in ('cron', 'system'):
    if t not in deny:
        deny.append(t)
        added.append(t)
tools['deny'] = deny
backup['tools.deny.added'] = added

# Mark the file as hardened by this script so reapplying is safe.
d.setdefault('_nemoclaw_wakeup', {})['hardened'] = True

json.dump(d, open(p, 'w'), indent=2)
print(json.dumps(backup))
PYEOF" > "$backup_path"
  ok "Hardened sandbox (backup saved to $backup_path)"
  info "Native scheduling disabled: agents.defaults.heartbeat.every='' and tools.deny += [cron, system]"
  info "Restart the openclaw gateway (or reconnect the sandbox) for changes to take effect"
}

remove_harden() {
  local sandbox="$1"
  [ -z "$OPENCLAW_JSON" ] && { warn "unharden: needs new layout (openclaw.json); skipping"; return 0; }
  if ! ssh_sandbox "$sandbox" "[ -f $OPENCLAW_JSON ]"; then
    warn "unharden: $OPENCLAW_JSON not found; skipping"
    return 0
  fi
  local backup_path="$INSTALL_DIR/harden-backup.json"
  if [ ! -f "$backup_path" ]; then
    warn "No harden-backup.json found at $backup_path"
    warn "Falling back to best-effort cleanup (restore default heartbeat + drop cron/system from deny)"
    echo '{"heartbeat.every": null, "tools.deny.added": ["cron", "system"]}' > "$backup_path"
  fi
  local backup_json
  backup_json=$(cat "$backup_path")
  ssh_sandbox "$sandbox" "python3 - <<PYEOF
import json
p = '$OPENCLAW_JSON'
d = json.load(open(p))
backup = json.loads('''$backup_json''')

# Restore heartbeat.every
agents = d.get('agents', {}).get('defaults', {})
if 'heartbeat' in agents:
    prev = backup.get('heartbeat.every', None)
    if prev is None:
        agents['heartbeat'].pop('every', None)
        if not agents['heartbeat']:
            agents.pop('heartbeat', None)
    else:
        agents['heartbeat']['every'] = prev

# Drop the deny entries we added.
tools = d.get('tools', {})
deny = list(tools.get('deny') or [])
for t in (backup.get('tools.deny.added') or []):
    while t in deny:
        deny.remove(t)
if deny:
    tools['deny'] = deny
elif 'deny' in tools:
    tools.pop('deny', None)

if 'tools' in d and not d['tools']:
    d.pop('tools', None)

d.get('_nemoclaw_wakeup', {}).pop('hardened', None)
if d.get('_nemoclaw_wakeup') == {}:
    d.pop('_nemoclaw_wakeup', None)

json.dump(d, open(p, 'w'), indent=2)
print('unhardened')
PYEOF"
  rm -f "$backup_path"
  ok "Restored in-gateway scheduling (heartbeat re-enabled, cron/system removed from deny)"
  info "Restart the openclaw gateway (or reconnect the sandbox) for changes to take effect"
}

# ── Parse arguments ───────────────────────────────────────────────
SANDBOX_NAME=""
INTERVAL=""
DO_UNINSTALL=false
DO_STATUS=false
DO_HARDEN=false
DO_UNHARDEN=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --interval)  INTERVAL="$2"; shift 2 ;;
    --uninstall) DO_UNINSTALL=true; shift ;;
    --status)    DO_STATUS=true; shift ;;
    --harden)    DO_HARDEN=true; shift ;;
    --unharden)  DO_UNHARDEN=true; shift ;;
    -h|--help)   usage_exit ;;
    -*)          fail "Unknown option: $1" ;;
    *)
      if [ -z "$SANDBOX_NAME" ]; then
        SANDBOX_NAME="$1"; shift
      else
        fail "Unknown argument: $1"
      fi
      ;;
  esac
done

if [ "$DO_HARDEN" = true ] && [ "$DO_UNHARDEN" = true ]; then
  fail "Cannot combine --harden and --unharden"
fi

# ── Detect openshell path ─────────────────────────────────────────
OPENSHELL_BIN=""
for candidate in \
  "$(command -v openshell 2>/dev/null || true)" \
  "$HOME/.local/bin/openshell" \
  "/usr/local/bin/openshell" \
  "/usr/bin/openshell"; do
  if [ -n "$candidate" ] && [ -x "$candidate" ]; then
    OPENSHELL_BIN="$candidate"
    break
  fi
done
[ -z "$OPENSHELL_BIN" ] && fail "openshell CLI not found. Is NemoClaw installed?"

# ── Status mode ───────────────────────────────────────────────────
if [ "$DO_STATUS" = true ]; then
  echo ""
  echo -e "${CYAN}  NemoClaw Wakeup Status${NC}"
  echo ""

  if [ -f "$INSTALL_DIR/config.env" ]; then
    # shellcheck disable=SC1091
    source "$INSTALL_DIR/config.env"
    ok "Installed"
    echo "    Sandbox:   ${WAKEUP_SANDBOX:-unknown}"
    echo "    Interval:  every ${WAKEUP_INTERVAL:-?} minutes"
    echo "    Layout:    ${WAKEUP_LAYOUT:-unknown}"
    echo "    WAKEUP.md: ${WAKEUP_MD_PATH:-unknown}"
    echo "    SKILL.md:  ${WAKEUP_SKILL_DEST:-unknown}"
    echo "    Hardened:  ${WAKEUP_HARDENED:-false}"
    echo "    Trigger:   SSH (via openshell ssh-proxy)"
    echo "    Log:       $INSTALL_DIR/wakeup.log"
  else
    warn "Not installed"
  fi

  CRON_LINE=$(crontab -l 2>/dev/null | grep "nemoclaw-wakeup" || true)
  if [ -n "$CRON_LINE" ]; then
    ok "Cron job active"
    echo "    $CRON_LINE"
  else
    warn "No cron job found"
  fi

  if [ -f "$INSTALL_DIR/wakeup.log" ]; then
    echo ""
    echo "  Last 5 log entries:"
    tail -10 "$INSTALL_DIR/wakeup.log" | grep "^[0-9]" | tail -5 | while read -r line; do
      echo "    $line"
    done
  fi

  echo ""
  exit 0
fi

# ── Uninstall mode ────────────────────────────────────────────────
if [ "$DO_UNINSTALL" = true ]; then
  echo ""
  echo -e "${CYAN}  Removing NemoClaw Wakeup...${NC}"
  echo ""

  # Try to restore openclaw.json if a harden backup is present.
  if [ -f "$INSTALL_DIR/config.env" ] && [ -f "$INSTALL_DIR/harden-backup.json" ]; then
    # shellcheck disable=SC1091
    source "$INSTALL_DIR/config.env"
    if [ -n "${WAKEUP_SANDBOX:-}" ]; then
      detect_paths "$WAKEUP_SANDBOX"
      remove_harden "$WAKEUP_SANDBOX" || true
    fi
  fi

  EXISTING=$(crontab -l 2>/dev/null | grep -v "nemoclaw-wakeup" || true)
  if [ -n "$EXISTING" ]; then
    echo "$EXISTING" | crontab -
  else
    crontab -r 2>/dev/null || true
  fi
  ok "Cron job removed"

  # Also remove old heartbeat cron entries
  EXISTING2=$(crontab -l 2>/dev/null | grep -v "nemoclaw-heartbeat" || true)
  if [ -n "$EXISTING2" ]; then
    echo "$EXISTING2" | crontab -
  fi

  rm -rf "$INSTALL_DIR"
  rm -rf "$HOME/.nemoclaw/heartbeat" 2>/dev/null || true
  ok "Wakeup files removed"

  echo ""
  echo -e "${GREEN}  NemoClaw Wakeup uninstalled.${NC}"
  echo ""
  exit 0
fi

# ── --unharden only (no install) ──────────────────────────────────
if [ "$DO_UNHARDEN" = true ] && [ -z "$SANDBOX_NAME" ] && [ -z "$INTERVAL" ]; then
  if [ ! -f "$INSTALL_DIR/config.env" ]; then
    fail "Wakeup not installed. Run ./install.sh first."
  fi
  # shellcheck disable=SC1091
  source "$INSTALL_DIR/config.env"
  SANDBOX_NAME="${WAKEUP_SANDBOX:?Missing WAKEUP_SANDBOX in config.env}"
  detect_paths "$SANDBOX_NAME"
  remove_harden "$SANDBOX_NAME"
  # Update config.env
  sed -i.bak '/^WAKEUP_HARDENED=/d' "$INSTALL_DIR/config.env" && rm -f "$INSTALL_DIR/config.env.bak"
  echo 'WAKEUP_HARDENED=false' >> "$INSTALL_DIR/config.env"
  echo ""
  ok "Sandbox unhardened. Native heartbeat + cron tools restored."
  echo ""
  exit 0
fi

# ── Main install ──────────────────────────────────────────────────
echo ""
echo -e "${CYAN}  ╔══════════════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}  ║  NemoClaw Wakeup Installer                             ║${NC}"
echo -e "${CYAN}  ║  Host-Side Cron → SSH → Wakes OpenClaw Agent           ║${NC}"
echo -e "${CYAN}  ╚══════════════════════════════════════════════════════════╝${NC}"
echo ""

# ── Step 1: Detect sandbox ────────────────────────────────────────
if [ -z "$SANDBOX_NAME" ]; then
  SANDBOX_NAME=$(python3 -c "
import json
try:
    d = json.load(open('$SANDBOXES_JSON'))
    print(d.get('defaultSandbox',''))
except: pass
" 2>/dev/null || true)

  if [ -z "$SANDBOX_NAME" ]; then
    SANDBOX_LIST=$("$OPENSHELL_BIN" sandbox list 2>/dev/null | tail -n +2 | awk '{print $1}' | head -5)
    if [ -n "$SANDBOX_LIST" ]; then
      echo "  Available sandboxes:"
      echo "$SANDBOX_LIST" | while read -r s; do echo "    - $s"; done
      echo ""
    fi
    echo -n "  Sandbox name: "
    read -r SANDBOX_NAME
  fi
fi

[ -z "$SANDBOX_NAME" ] && fail "No sandbox name provided."
info "Sandbox: $SANDBOX_NAME"

# ── Step 1b: Verify SSH connectivity ──────────────────────────────
info "Testing SSH connection to sandbox..."
SSH_TEST=$(ssh_sandbox "$SANDBOX_NAME" "echo OK" 2>/dev/null || echo "FAIL")
if [ "$SSH_TEST" != "OK" ]; then
  fail "Cannot SSH into sandbox '$SANDBOX_NAME'. Is it running?"
fi
ok "SSH connection verified"

# ── Step 1c: Detect OpenClaw layout (path-aware install) ──────────
detect_paths "$SANDBOX_NAME"
info "OpenClaw layout: $LAYOUT"
info "  workspace: $WORKSPACE_DIR"
info "  skills:    $SKILLS_DIR"
[ -n "$OPENCLAW_JSON" ] && info "  config:    $OPENCLAW_JSON"

# ── Step 2: Set interval ─────────────────────────────────────────
if [ -z "$INTERVAL" ]; then
  echo ""
  echo "  How often should the wakeup trigger?"
  echo ""
  echo "    1) Every 5 minutes"
  echo "    2) Every 10 minutes (recommended)"
  echo "    3) Every 15 minutes"
  echo "    4) Every 30 minutes"
  echo "    5) Every hour"
  echo "    6) Custom"
  echo ""
  echo -n "  Choice (1-6) [2]: "
  read -r CHOICE

  case "${CHOICE:-2}" in
    1) INTERVAL=5 ;;
    2) INTERVAL=10 ;;
    3) INTERVAL=15 ;;
    4) INTERVAL=30 ;;
    5) INTERVAL=60 ;;
    6)
      echo -n "  Minutes between wakeups: "
      read -r INTERVAL
      ;;
    *) INTERVAL=10 ;;
  esac
fi

[ -z "$INTERVAL" ] && INTERVAL=10
info "Interval: every $INTERVAL minutes"

# ── Step 3: Deploy skill ──────────────────────────────────────────
echo ""
info "Deploying NemoClaw Wakeup skill..."

SKILL_FILE="$SCRIPT_DIR/skill/SKILL.md"
if [ ! -f "$SKILL_FILE" ]; then
  fail "skill/SKILL.md not found in repo. Re-clone the repository."
fi

INSTALLED_AT="$(date +%Y-%m-%dT%H:%M:%S)"
ssh_sandbox "$SANDBOX_NAME" "mkdir -p $(dirname $SKILL_DEST)" 2>/dev/null || true
sed -e "s/__INTERVAL__/$INTERVAL/g" \
    -e "s/__INSTALLED_AT__/$INSTALLED_AT/g" \
    -e "s|__WAKEUP_MD_PATH__|$WAKEUP_MD_PATH|g" \
    "$SKILL_FILE" | ssh_sandbox "$SANDBOX_NAME" "cat > $SKILL_DEST"
ok "Skill deployed at $SKILL_DEST (interval: every ${INTERVAL}m)"

# ── Step 3b: Enable skill in OpenClaw registry + set tools.profile ─
if [ "$LAYOUT" = "new" ]; then
  info "Configuring openclaw.json (skill registry + tools.profile)..."
  if configure_openclaw_json "$SANDBOX_NAME"; then
    ok "openclaw.json updated"
  else
    warn "Could not update openclaw.json; agent may not surface SKILL.md"
    warn "Manual fix: edit $OPENCLAW_JSON and add:"
    warn '  "skills": { "entries": { "nemoclaw-wakeup": { "enabled": true } } }'
    warn '  "tools":  { "profile": "coding" }'
  fi
fi

# ── Step 4: Seed WAKEUP.md if missing ────────────────────────────
info "Checking for WAKEUP.md in sandbox..."

ssh_sandbox "$SANDBOX_NAME" "mkdir -p $WORKSPACE_DIR" 2>/dev/null || true
HB_EXISTS=$(ssh_sandbox "$SANDBOX_NAME" "[ -f $WAKEUP_MD_PATH ] && echo yes || echo no")

if [ "$HB_EXISTS" = "no" ]; then
  info "Seeding default WAKEUP.md at $WAKEUP_MD_PATH..."

  ssh_sandbox "$SANDBOX_NAME" "cat > $WAKEUP_MD_PATH" << 'WKMD'
# Wakeup Instructions

This file is read by the OpenClaw agent every time the host-side wakeup
triggers. Edit these instructions to control what the agent does on each pulse.

## Current Tasks

1. Check my Gmail inbox for unread emails. Summarize any important messages.

## Rules

- Do NOT send emails or replies unless a rule below explicitly says to.
- Do NOT create calendar events unless instructed below.
- Do NOT send Telegram, Discord, or Slack messages unless instructed below.
- If there is nothing to do, simply end your turn with no output.
- Keep all output concise and within the session — do not deliver to channels.

## Auto-Reply Rules

(Add rules here if you want the agent to automatically reply to messages)

<!-- Example:
- Reply to emails from boss@company.com confirming receipt.
- Forward emails with "URGENT" in subject to backup@company.com.
-->
WKMD

  ok "Default WAKEUP.md deployed"
else
  ok "WAKEUP.md already exists in sandbox"
fi

# ── Step 5: Create wakeup.sh ─────────────────────────────────────
info "Installing wakeup script..."

mkdir -p "$INSTALL_DIR"

cat > "$INSTALL_DIR/wakeup.sh" << WKEOF
#!/bin/bash
# NemoClaw Wakeup — fires the OpenClaw agent via SSH.
# Uses flock to prevent overlapping runs. Uses unique session IDs
# to prevent context bleed between pulses.

CONFIG="\$HOME/.nemoclaw/wakeup/config.env"
source "\$CONFIG" 2>/dev/null || {
  echo "\$(date +%Y-%m-%dT%H:%M:%S) ERROR config.env missing" >> "\$HOME/.nemoclaw/wakeup/wakeup.log"
  exit 1
}

LOG="\$HOME/.nemoclaw/wakeup/wakeup.log"
LOCK="\$HOME/.nemoclaw/wakeup/wakeup.lock"
MAX_LOG=1000

# ── Concurrency guard (flock) ────────────────────────────────────
exec 9>"\$LOCK"
if ! flock -n 9; then
  echo "\$(date +%Y-%m-%dT%H:%M:%S) SKIP previous wakeup still running" >> "\$LOG"
  exit 0
fi

# ── Unique session ID ────────────────────────────────────────────
SESSION_ID="wakeup-\$(date +%s)-\$\$"

# ── Agent message ────────────────────────────────────────────────
# Baked-in path is the one detected at install time. Agent is told to
# try the new path first and fall back to the legacy path so wakeup
# pulses survive a sandbox-image upgrade between install runs.
AGENT_MSG="NemoClaw Wakeup triggered. You MUST read the file \${WAKEUP_MD_PATH} right now and follow ONLY the instructions in that file. If that file does not exist, try /sandbox/.openclaw/workspace/WAKEUP.md then /sandbox/.openclaw-data/workspace/WAKEUP.md. Do not use cached or remembered instructions from previous sessions. Do not send messages to Telegram, Discord, or Slack unless WAKEUP.md explicitly tells you to."

echo "\$(date +%Y-%m-%dT%H:%M:%S) START session=\$SESSION_ID sandbox=\$WAKEUP_SANDBOX" >> "\$LOG"

# ── Fire agent via SSH (fire-and-forget with timeout) ────────────
ssh -o StrictHostKeyChecking=no \\
    -o UserKnownHostsFile=/dev/null \\
    -o GlobalKnownHostsFile=/dev/null \\
    -o LogLevel=ERROR \\
    -o ConnectTimeout=10 \\
    -o ServerAliveInterval=30 \\
    -o ServerAliveCountMax=4 \\
    -o ProxyCommand="\$WAKEUP_OPENSHELL ssh-proxy --gateway-name nemoclaw --name \$WAKEUP_SANDBOX" \\
    "sandbox@openshell-\$WAKEUP_SANDBOX" \\
    "openclaw agent --agent main --message \\"\$AGENT_MSG\\" --session-id \\"\$SESSION_ID\\"" >> "\$LOG" 2>&1
EXIT_CODE=\$?

if [ \$EXIT_CODE -eq 0 ]; then
  echo "\$(date +%Y-%m-%dT%H:%M:%S) DONE session=\$SESSION_ID exit=0" >> "\$LOG"
else
  echo "\$(date +%Y-%m-%dT%H:%M:%S) FAIL session=\$SESSION_ID exit=\$EXIT_CODE" >> "\$LOG"
fi

# ── Log rotation ─────────────────────────────────────────────────
LINES=\$(wc -l < "\$LOG" 2>/dev/null || echo 0)
if [ "\$LINES" -gt "\$MAX_LOG" ]; then
  tail -n 500 "\$LOG" > "\$LOG.tmp" && mv "\$LOG.tmp" "\$LOG"
fi
WKEOF

chmod +x "$INSTALL_DIR/wakeup.sh"
ok "Wakeup script: $INSTALL_DIR/wakeup.sh"

# ── Step 6: Save config ──────────────────────────────────────────
cat > "$INSTALL_DIR/config.env" << CFGEOF
WAKEUP_SANDBOX="$SANDBOX_NAME"
WAKEUP_INTERVAL="$INTERVAL"
WAKEUP_OPENSHELL="$OPENSHELL_BIN"
WAKEUP_LAYOUT="$LAYOUT"
WAKEUP_MD_PATH="$WAKEUP_MD_PATH"
WAKEUP_SKILL_DEST="$SKILL_DEST"
WAKEUP_HARDENED="$DO_HARDEN"
CFGEOF
ok "Config: $INSTALL_DIR/config.env"

# ── Step 7: Install cron job ─────────────────────────────────────
info "Setting up cron job..."

CRON_ENTRY="*/$INTERVAL * * * * $INSTALL_DIR/wakeup.sh  # nemoclaw-wakeup"

# Remove old heartbeat AND wakeup entries
EXISTING_CRON=$(crontab -l 2>/dev/null | grep -v "nemoclaw-wakeup" | grep -v "nemoclaw-heartbeat" || true)
if [ -n "$EXISTING_CRON" ]; then
  (echo "$EXISTING_CRON"; echo "$CRON_ENTRY") | crontab -
else
  echo "$CRON_ENTRY" | crontab -
fi

ok "Cron job installed (every $INTERVAL minutes)"

# ── Step 8: Apply --harden / --unharden ──────────────────────────
if [ "$DO_HARDEN" = true ]; then
  echo ""
  info "Applying --harden: disabling OpenClaw's in-gateway scheduling..."
  apply_harden "$SANDBOX_NAME"
elif [ "$DO_UNHARDEN" = true ]; then
  echo ""
  info "Applying --unharden: restoring OpenClaw's in-gateway scheduling..."
  remove_harden "$SANDBOX_NAME"
fi

# ── Done ──────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}  ╔══════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}  ║  NemoClaw Wakeup installed!                             ║${NC}"
echo -e "${GREEN}  ╚══════════════════════════════════════════════════════════╝${NC}"
echo ""
echo "  Sandbox:    $SANDBOX_NAME"
echo "  Layout:     $LAYOUT (workspace: $WORKSPACE_DIR)"
echo "  Interval:   every $INTERVAL minutes"
echo "  Trigger:    SSH (via openshell ssh-proxy, ~400ms)"
echo "  Hardened:   $DO_HARDEN"
echo "  Log file:   $INSTALL_DIR/wakeup.log"
echo ""
echo "  The agent reads $WAKEUP_MD_PATH for its instructions."
echo "  To change what the agent does:"
echo ""
echo "    Via TUI or Telegram:"
echo "      \"Update my $WAKEUP_MD_PATH to also check my calendar\""
echo ""
echo "    Via SSH:"
echo "      openshell sandbox connect $SANDBOX_NAME"
echo "      nano $WAKEUP_MD_PATH"
echo ""
echo "  Commands:"
echo "    Test now:         $INSTALL_DIR/wakeup.sh"
echo "    View log:         tail -f $INSTALL_DIR/wakeup.log"
echo "    Check status:     ./install.sh --status"
echo "    Change interval:  ./install.sh --interval 30"
echo "    Harden sandbox:   ./install.sh --harden       (disable in-gateway sched)"
echo "    Reverse harden:   ./install.sh --unharden"
echo "    Uninstall:        ./install.sh --uninstall"
echo ""
