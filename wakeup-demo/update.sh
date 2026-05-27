#!/usr/bin/env bash
set -euo pipefail

# ── NemoClaw Wakeup Updater ─────────────────────────────────────
# Re-deploys the wakeup script and skill WITHOUT touching WAKEUP.md.
# Use this after pulling repo updates.
#
# Path-aware: re-detects the OpenClaw layout on every run so a sandbox
# image upgrade (legacy → new) is picked up automatically.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
INSTALL_DIR="$HOME/.nemoclaw/wakeup"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

info()  { echo -e "${CYAN}  ▸ $1${NC}"; }
ok()    { echo -e "${GREEN}  ✓ $1${NC}"; }
warn()  { echo -e "${YELLOW}  ⚠ $1${NC}"; }
fail()  { echo -e "${RED}  ✗ $1${NC}"; exit 1; }

[ ! -f "$INSTALL_DIR/config.env" ] && fail "NemoClaw Wakeup not installed. Run install.sh first."
# shellcheck disable=SC1091
source "$INSTALL_DIR/config.env"

OPENSHELL_BIN="${WAKEUP_OPENSHELL:-$(command -v openshell 2>/dev/null || true)}"
[ -z "$OPENSHELL_BIN" ] && fail "openshell not found"

ssh_sandbox() {
  ssh -o StrictHostKeyChecking=no \
      -o UserKnownHostsFile=/dev/null \
      -o GlobalKnownHostsFile=/dev/null \
      -o LogLevel=ERROR \
      -o ConnectTimeout=10 \
      -o ProxyCommand="$OPENSHELL_BIN ssh-proxy --gateway-name nemoclaw --name $WAKEUP_SANDBOX" \
      "sandbox@openshell-$WAKEUP_SANDBOX" "$@" 2>/dev/null
}

# Re-detect paths (the layout may have changed if the sandbox was rebuilt
# against a newer openshell/openclaw between install runs).
detect_paths() {
  if ssh_sandbox "[ -d /sandbox/.openclaw/workspace ]"; then
    LAYOUT="new"
    WORKSPACE_DIR="/sandbox/.openclaw/workspace"
    SKILLS_DIR="/sandbox/.openclaw/skills"
    OPENCLAW_JSON="/sandbox/.openclaw/openclaw.json"
  elif ssh_sandbox "[ -d /sandbox/.openclaw-data/workspace ]"; then
    LAYOUT="legacy"
    WORKSPACE_DIR="/sandbox/.openclaw-data/workspace"
    SKILLS_DIR="/sandbox/.openclaw-data/skills"
    OPENCLAW_JSON=""
  else
    LAYOUT="new"
    WORKSPACE_DIR="/sandbox/.openclaw/workspace"
    SKILLS_DIR="/sandbox/.openclaw/skills"
    OPENCLAW_JSON="/sandbox/.openclaw/openclaw.json"
  fi
  WAKEUP_MD_PATH="$WORKSPACE_DIR/WAKEUP.md"
  SKILL_DEST="$SKILLS_DIR/nemoclaw-wakeup/SKILL.md"
}

echo ""
echo -e "${CYAN}  NemoClaw Wakeup — Updating...${NC}"
echo ""
info "Sandbox: $WAKEUP_SANDBOX"

detect_paths
info "Layout:   $LAYOUT (workspace: $WORKSPACE_DIR)"
[ "$LAYOUT" != "${WAKEUP_LAYOUT:-$LAYOUT}" ] && \
  warn "Layout changed since install (${WAKEUP_LAYOUT:-unknown} → $LAYOUT); re-running install will rebake paths"

# Re-deploy skill with current config values.
SKILL_FILE="$SCRIPT_DIR/skill/SKILL.md"
if [ -f "$SKILL_FILE" ]; then
  INSTALLED_AT="$(date +%Y-%m-%dT%H:%M:%S)"
  ssh_sandbox "mkdir -p $(dirname $SKILL_DEST)" 2>/dev/null || true
  sed -e "s/__INTERVAL__/$WAKEUP_INTERVAL/g" \
      -e "s/__INSTALLED_AT__/$INSTALLED_AT/g" \
      -e "s|__WAKEUP_MD_PATH__|$WAKEUP_MD_PATH|g" \
      "$SKILL_FILE" | ssh_sandbox "cat > $SKILL_DEST"
  ok "Skill updated at $SKILL_DEST (interval: every ${WAKEUP_INTERVAL}m)"
else
  fail "skill/SKILL.md not found"
fi

# On the new layout, ensure registry + tools.profile are still configured.
if [ "$LAYOUT" = "new" ] && [ -n "$OPENCLAW_JSON" ]; then
  if ssh_sandbox "[ -f $OPENCLAW_JSON ]"; then
    ssh_sandbox "python3 - <<'PYEOF'
import json
p = '$OPENCLAW_JSON'
d = json.load(open(p))
changed = False
e = d.setdefault('skills', {}).setdefault('entries', {}).setdefault('nemoclaw-wakeup', {})
if e.get('enabled') is not True:
    e['enabled'] = True; changed = True
t = d.setdefault('tools', {})
if t.get('profile') is None:
    t['profile'] = 'coding'; changed = True
if changed:
    json.dump(d, open(p, 'w'), indent=2); print('updated')
else:
    print('already configured')
PYEOF" >/dev/null
    ok "openclaw.json verified (skill enabled, tools.profile present)"
  fi
fi

ok "Update complete (WAKEUP.md was NOT modified)"
echo ""
