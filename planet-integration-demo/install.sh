#!/usr/bin/env bash
set -euo pipefail

# ── Planet API Integration Installer for NemoClaw ───────────────
# Tier 1 security: Planet API key stays on the host. The sandbox only
# ever sees a proxy URL on the host's LAN. The proxy injects the key,
# enforces order-creation blocklist, and forwards to api.planet.com.
#
# Layout — auto-detected (with fallback for older openshell):
#   New (openshell ≥ 0.0.44 / openclaw ≥ 2026.5.x):
#     skills: /sandbox/.openclaw/skills/planet/
#     config: /sandbox/.openclaw/openclaw.json   (skill registry + tools profile)
#   Legacy (older builds):
#     skills: /sandbox/.openclaw-data/skills/planet/

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
INSTALL_DIR="$HOME/.nemoclaw/planet"
CREDS_PATH="$HOME/.nemoclaw/credentials.json"
TOKEN_PORT=9201

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
  echo "    --port <N>         Host proxy port (default: 9201)"
  echo "    --update-key       Force prompt for a new Planet API key"
  echo "    --uninstall        Stop proxy, remove skill, drop policy block, clean local files"
  echo "    --status           Show current install + proxy state"
  echo "    -h, --help         Show this help"
  echo ""
  echo "  Env vars:"
  echo "    PLANET_PROXY_HOST  Override auto-detected host IP for the sandbox→host bridge"
  echo ""
  exit 0
}

ssh_sandbox() {
  ssh -o StrictHostKeyChecking=no \
      -o UserKnownHostsFile=/dev/null \
      -o GlobalKnownHostsFile=/dev/null \
      -o LogLevel=ERROR \
      -o ConnectTimeout=10 \
      -o ProxyCommand="$OPENSHELL_BIN ssh-proxy --gateway-name nemoclaw --name $SANDBOX_NAME" \
      "sandbox@openshell-$SANDBOX_NAME" "$@" 2>/dev/null
}

# ── Path detection ────────────────────────────────────────────────
# Sets LAYOUT, SKILLS_BASE, OPENCLAW_JSON based on what exists in the
# sandbox. Prefers the new layout if both are present (which is normal
# for openshell ≥ 0.0.44 — `.openclaw-data/` is often an empty stub).
detect_paths() {
  if ssh_sandbox "[ -f /sandbox/.openclaw/openclaw.json ]"; then
    LAYOUT="new"
    SKILLS_BASE="/sandbox/.openclaw/skills"
    OPENCLAW_JSON="/sandbox/.openclaw/openclaw.json"
  elif ssh_sandbox "[ -d /sandbox/.openclaw-data/skills ]" || \
       ssh_sandbox "[ -d /sandbox/.openclaw-data/agents ]"; then
    LAYOUT="legacy"
    SKILLS_BASE="/sandbox/.openclaw-data/skills"
    OPENCLAW_JSON=""
  else
    LAYOUT="new"
    SKILLS_BASE="/sandbox/.openclaw/skills"
    OPENCLAW_JSON="/sandbox/.openclaw/openclaw.json"
  fi
}

# ── openclaw.json mutation ────────────────────────────────────────
# Enables this skill in the registry and ensures tools.profile is set
# to "coding" so the agent surfaces the `exec` tool (needed to run
# `node …/planet-api.js`). Idempotent. No-op on legacy layouts that
# don't have an openclaw.json.
configure_openclaw_json() {
  [ -z "$OPENCLAW_JSON" ] && return 0
  if ! ssh_sandbox "[ -f $OPENCLAW_JSON ]"; then
    warn "$OPENCLAW_JSON not found; skipping skill-registry + tools-profile update"
    return 0
  fi
  ssh_sandbox "python3 - <<'PYEOF'
import json
p = '$OPENCLAW_JSON'
d = json.load(open(p))
changed = False

entry = d.setdefault('skills', {}).setdefault('entries', {}).setdefault('planet', {})
if entry.get('enabled') is not True:
    entry['enabled'] = True
    changed = True

tools = d.setdefault('tools', {})
if tools.get('profile') is None:
    tools['profile'] = 'coding'
    changed = True
elif tools.get('profile') != 'coding':
    print('WARN: tools.profile is %r; leaving as-is. If the agent never invokes node, set it to \"coding\".' % tools.get('profile'))

if changed:
    json.dump(d, open(p, 'w'), indent=2)
    print('updated')
else:
    print('already configured')
PYEOF"
}

# ── Find openshell binary ─────────────────────────────────────────
OPENSHELL_BIN=""
for candidate in \
  "$(command -v openshell 2>/dev/null || true)" \
  "$HOME/.local/bin/openshell" \
  "/usr/local/bin/openshell" \
  "/usr/bin/openshell"; do
  if [ -n "$candidate" ] && [ -x "$candidate" ]; then
    OPENSHELL_BIN="$candidate"; break
  fi
done

# ── Parse arguments ───────────────────────────────────────────────
SANDBOX_NAME=""
UPDATE_KEY=false
DO_UNINSTALL=false
DO_STATUS=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --port)        TOKEN_PORT="$2"; shift 2 ;;
    --update-key)  UPDATE_KEY=true; shift ;;
    --uninstall)   DO_UNINSTALL=true; shift ;;
    --status)      DO_STATUS=true; shift ;;
    -h|--help)     usage_exit ;;
    -*)            fail "Unknown option: $1" ;;
    *)
      if [ -z "$SANDBOX_NAME" ]; then SANDBOX_NAME="$1"; shift
      else fail "Unknown argument: $1"; fi ;;
  esac
done

# ── Status mode ───────────────────────────────────────────────────
if [ "$DO_STATUS" = true ]; then
  echo ""
  echo -e "${CYAN}  Planet Integration Status${NC}"
  echo ""
  if [ -f "$INSTALL_DIR/config.env" ]; then
    # shellcheck disable=SC1091
    source "$INSTALL_DIR/config.env"
    ok "Installed"
    echo "    Sandbox:   ${PLANET_SANDBOX:-unknown}"
    echo "    Layout:    ${PLANET_LAYOUT:-unknown}"
    echo "    Skills:    ${PLANET_SKILLS_BASE:-unknown}/planet/"
    echo "    Host IP:   ${PLANET_HOST_IP:-unknown}:${PLANET_PORT:-?}"
  else
    warn "Not installed"
  fi
  echo ""
  PROXY_PID=$(pgrep -f "python3.*planet-proxy.py" 2>/dev/null | head -1 || true)
  if [ -n "$PROXY_PID" ]; then
    ok "Proxy running (PID $PROXY_PID)"
    HEALTH=$(curl -sf "http://127.0.0.1:${TOKEN_PORT}/health" 2>/dev/null || true)
    [ "$HEALTH" = "ok" ] && ok "Proxy health check: ok" || warn "Proxy health check failed"
  else
    warn "Proxy NOT running"
  fi
  echo ""
  if [ -f "$CREDS_PATH" ]; then
    HAS=$(python3 -c "import json; print('yes' if json.load(open('$CREDS_PATH')).get('PLANET_API_KEY') else 'no')" 2>/dev/null || echo no)
    [ "$HAS" = "yes" ] && ok "PLANET_API_KEY present in $CREDS_PATH" || warn "PLANET_API_KEY missing"
  else
    warn "$CREDS_PATH does not exist"
  fi
  echo ""
  exit 0
fi

[ -z "$OPENSHELL_BIN" ] && fail "openshell CLI not found. Is NemoClaw installed?"

# ── Uninstall mode ────────────────────────────────────────────────
if [ "$DO_UNINSTALL" = true ]; then
  echo ""
  echo -e "${CYAN}  Removing Planet Integration...${NC}"
  echo ""
  PROXY_PID=$(pgrep -f "python3.*planet-proxy.py" 2>/dev/null || true)
  if [ -n "$PROXY_PID" ]; then
    kill $PROXY_PID 2>/dev/null || true
    sleep 1
    ok "Proxy stopped"
  fi

  if [ -f "$INSTALL_DIR/config.env" ]; then
    # shellcheck disable=SC1091
    source "$INSTALL_DIR/config.env"
    SANDBOX_NAME="${PLANET_SANDBOX:-}"
    if [ -n "$SANDBOX_NAME" ]; then
      detect_paths
      info "Removing skill files from $SKILLS_BASE/planet/..."
      ssh_sandbox "rm -rf $SKILLS_BASE/planet" 2>/dev/null || true
      ok "Skill files removed"

      if [ -n "$OPENCLAW_JSON" ] && ssh_sandbox "[ -f $OPENCLAW_JSON ]"; then
        ssh_sandbox "python3 - <<'PYEOF'
import json
p = '$OPENCLAW_JSON'
d = json.load(open(p))
entries = d.get('skills', {}).get('entries', {})
if 'planet' in entries:
    del entries['planet']
    json.dump(d, open(p, 'w'), indent=2)
    print('removed')
else:
    print('not present')
PYEOF" >/dev/null
        ok "Skill removed from openclaw.json registry"
      fi

      info "Removing planet_proxy block from network policy..."
      CURRENT_POLICY=$("$OPENSHELL_BIN" policy get "$SANDBOX_NAME" --full 2>/dev/null | sed '1,/^---$/d' || true)
      if echo "$CURRENT_POLICY" | grep -q "planet_proxy:"; then
        NEW_POLICY_FILE=$(mktemp /tmp/planet-policy-XXXX.yaml)
        echo "$CURRENT_POLICY" | python3 -c "
import sys, re
pol = sys.stdin.read()
pol = re.sub(r'  planet_proxy:\n    name: planet_proxy\n(?:    .*\n)*?(?=  \S|\Z)', '', pol)
print(pol)
" > "$NEW_POLICY_FILE"
        "$OPENSHELL_BIN" policy set "$SANDBOX_NAME" --policy "$NEW_POLICY_FILE" --wait >/dev/null 2>&1 || warn "Could not auto-apply cleaned policy"
        rm -f "$NEW_POLICY_FILE"
        ok "planet_proxy block removed from policy"
      fi
    fi
  fi

  rm -rf "$INSTALL_DIR"
  ok "Local config removed"
  echo ""
  echo -e "${GREEN}  Planet Integration uninstalled.${NC}"
  echo ""
  echo "  Your PLANET_API_KEY in $CREDS_PATH was NOT removed (other tools may use it)."
  echo ""
  exit 0
fi

# ── Main install ──────────────────────────────────────────────────
echo ""
echo -e "${CYAN}  ╔══════════════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}  ║  Planet API Integration Installer for NemoClaw          ║${NC}"
echo -e "${CYAN}  ║  Tier 1 Security — Host-Side API Proxy                  ║${NC}"
echo -e "${CYAN}  ╚══════════════════════════════════════════════════════════╝${NC}"
echo ""

# ── Step 0: Detect sandbox name ──────────────────────────────────
if [ -z "$SANDBOX_NAME" ]; then
  SANDBOX_NAME=$(python3 -c "
import json
try:
    d = json.load(open('$HOME/.nemoclaw/sandboxes.json'))
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

[ -z "$SANDBOX_NAME" ] && fail "No sandbox name provided. Usage: ./install.sh <sandbox-name>"
info "Target sandbox: $SANDBOX_NAME"

# ── Step 1: Prerequisites ────────────────────────────────────────
info "Checking prerequisites..."
command -v nemoclaw >/dev/null 2>&1 || fail "nemoclaw CLI not found. Is NemoClaw installed?"
command -v python3 >/dev/null 2>&1 || fail "python3 not found."
"$OPENSHELL_BIN" sandbox list 2>/dev/null | grep -q "$SANDBOX_NAME" || fail "Sandbox '$SANDBOX_NAME' not found. Run 'nemoclaw onboard' first."
ok "Prerequisites OK"

# ── Step 1b: SSH connectivity ─────────────────────────────────────
info "Testing SSH connection to sandbox..."
SSH_TEST=$(ssh_sandbox "echo OK" 2>/dev/null || echo "FAIL")
[ "$SSH_TEST" != "OK" ] && fail "Cannot SSH into sandbox '$SANDBOX_NAME'. Is it running?"
ok "SSH connection verified"

# ── Step 1c: Detect OpenClaw layout ───────────────────────────────
detect_paths
info "OpenClaw layout: $LAYOUT (skills: $SKILLS_BASE)"

# ── Step 2: Planet API Key ───────────────────────────────────────
echo ""
mkdir -p "$(dirname "$CREDS_PATH")"
HAS_PLANET_KEY=false
if [ -f "$CREDS_PATH" ]; then
  HAS_KEY=$(python3 -c "
import json
d = json.load(open('$CREDS_PATH'))
print('yes' if d.get('PLANET_API_KEY') else 'no')
" 2>/dev/null || echo "no")
  [ "$HAS_KEY" = "yes" ] && HAS_PLANET_KEY=true
fi

if [ "$HAS_PLANET_KEY" = true ] && [ "$UPDATE_KEY" = false ]; then
  ok "Planet API key found in $CREDS_PATH"
  echo ""
  echo -n "  Update API key? (y/N): "
  read -r UPDATE_PROMPT
  [[ "${UPDATE_PROMPT:-}" =~ ^[Yy] ]] && UPDATE_KEY=true
fi

if [ "$HAS_PLANET_KEY" = false ] || [ "$UPDATE_KEY" = true ]; then
  info "Get your API key from: https://www.planet.com/account/#/user-settings"
  echo ""
  echo -n "  Planet API Key: "
  read -r NEW_KEY
  [ -z "$NEW_KEY" ] && fail "API key is required."
  python3 -c "
import json
try: d = json.load(open('$CREDS_PATH'))
except: d = {}
d['PLANET_API_KEY'] = '$NEW_KEY'
json.dump(d, open('$CREDS_PATH', 'w'), indent=2)
" && chmod 600 "$CREDS_PATH"
  ok "API key saved to $CREDS_PATH"
fi

PLANET_API_KEY=$(python3 -c "import json; print(json.load(open('$CREDS_PATH')).get('PLANET_API_KEY',''))")
[ -z "$PLANET_API_KEY" ] && fail "Planet API key is empty."

# ── Step 3: Detect host IP ───────────────────────────────────────
echo ""
HOST_IP="${PLANET_PROXY_HOST:-}"
if [ -z "$HOST_IP" ]; then
  HOST_IP=$( (hostname -I 2>/dev/null || true) | awk '{print $1}')
fi
if [ -z "$HOST_IP" ]; then
  HOST_IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || true)
fi
[ -z "$HOST_IP" ] && fail "Could not detect host IP. Set PLANET_PROXY_HOST env var."
info "Host IP: $HOST_IP (proxy will listen on 0.0.0.0:$TOKEN_PORT)"

# ── Step 4: Start/restart planet proxy ───────────────────────────
echo ""
info "Starting Planet API proxy on host..."

EXISTING_PID=$(pgrep -f "python3.*planet-proxy.py" 2>/dev/null || true)
if [ -n "$EXISTING_PID" ]; then
  info "Stopping existing proxy (PID $EXISTING_PID)..."
  kill $EXISTING_PID 2>/dev/null || true
  sleep 1
fi

nohup python3 "$SCRIPT_DIR/planet-proxy.py" --port "$TOKEN_PORT" \
  > /tmp/planet-proxy.log 2>&1 &
PROXY_PID=$!
sleep 2

if kill -0 "$PROXY_PID" 2>/dev/null; then
  ok "Planet proxy started (PID $PROXY_PID, port $TOKEN_PORT)"
else
  fail "Planet proxy failed to start. Check /tmp/planet-proxy.log"
fi

HEALTH=$(curl -sf "http://127.0.0.1:${TOKEN_PORT}/health" 2>/dev/null || true)
if [ "$HEALTH" = "ok" ]; then
  ok "Proxy health check passed"
else
  warn "Proxy health check failed (may still be starting)"
fi

# ── Step 5: Apply network policy ─────────────────────────────────
echo ""
info "Applying network policy..."

CURRENT_POLICY=$("$OPENSHELL_BIN" policy get "$SANDBOX_NAME" --full 2>/dev/null | sed '1,/^---$/d')
POLICY_FILE=$(mktemp /tmp/planet-policy-XXXX.yaml)

NEEDS_PROXY_BLOCK=true
if echo "$CURRENT_POLICY" | grep -q "planet_proxy:"; then
  # Already present — check if host/port match
  CURRENT_HOST=$(echo "$CURRENT_POLICY" | awk '/planet_proxy:/,/^  [a-z]/{print}' | grep -E "^      host:" | head -1 | awk '{print $2}' | tr -d "'\"")
  CURRENT_PORT=$(echo "$CURRENT_POLICY" | awk '/planet_proxy:/,/^  [a-z]/{print}' | grep -E "^      port:" | head -1 | awk '{print $2}')
  if [ "$CURRENT_HOST" = "$HOST_IP" ] && [ "$CURRENT_PORT" = "$TOKEN_PORT" ]; then
    NEEDS_PROXY_BLOCK=false
  fi
fi

HAS_OLD_PLANET_BLOCK=false
if echo "$CURRENT_POLICY" | grep -q "planet_data_api:"; then
  HAS_OLD_PLANET_BLOCK=true
fi

if [ "$NEEDS_PROXY_BLOCK" = true ] || [ "$HAS_OLD_PLANET_BLOCK" = true ]; then
  echo "$CURRENT_POLICY" | python3 -c "
import sys, re

host_ip = '$HOST_IP'
token_port = $TOKEN_PORT
remove_old = '$HAS_OLD_PLANET_BLOCK' == 'true'

policy = sys.stdin.read()

# Drop any legacy planet_data_api block.
if remove_old:
    policy = re.sub(
        r'  planet_data_api:\n    name: planet_data_api\n(?:    .*\n)*?(?=  \S|\Z)',
        '',
        policy
    )

# Drop any existing planet_proxy block so we can re-add with the
# current host/port (handles host-IP drift across restarts).
policy = re.sub(
    r'  planet_proxy:\n    name: planet_proxy\n(?:    .*\n)*?(?=  \S|\Z)',
    '',
    policy
)

proxy_block = '''  planet_proxy:
    name: planet_proxy
    endpoints:
    - host: '{host}'
      port: {port}
      protocol: rest
      tls: passthrough
      enforcement: enforce
      rules:
      - allow:
          method: GET
          path: /**
      - allow:
          method: POST
          path: /**
    binaries:
    - path: /usr/local/bin/node
'''.format(host=host_ip, port=token_port)

# Inject under network_policies: if present, otherwise append.
if 'network_policies:' in policy and 'planet_proxy:' not in policy:
    policy = policy.rstrip() + '\n' + proxy_block
else:
    policy = policy.rstrip() + '\n' + proxy_block

print(policy)
" > "$POLICY_FILE"

  "$OPENSHELL_BIN" policy set "$SANDBOX_NAME" --policy "$POLICY_FILE" --wait 2>&1
  ok "Policy applied (planet_proxy block: $HOST_IP:$TOKEN_PORT)"
  rm -f "$POLICY_FILE"
else
  ok "Policy already contains correct planet_proxy block"
fi

# ── Step 6: Deploy skill to sandbox ──────────────────────────────
echo ""
info "Deploying Planet skill to $SKILLS_BASE/planet/..."

ssh_sandbox "mkdir -p $SKILLS_BASE/planet/scripts"

# Templated SKILL.md so the absolute paths in the body match the
# detected layout (new vs legacy).
TMP_SKILL=$(mktemp /tmp/planet-skill-XXXX.md)
sed -e "s|__SKILLS_DIR__|$SKILLS_BASE|g" \
    -e "s|__INSTALLED_AT__|$(date +%Y-%m-%dT%H:%M:%S)|g" \
    "$SCRIPT_DIR/skills/planet/SKILL.md" > "$TMP_SKILL"
cat "$TMP_SKILL" | ssh_sandbox "cat > $SKILLS_BASE/planet/SKILL.md"
rm -f "$TMP_SKILL"

cat "$SCRIPT_DIR/skills/planet/scripts/planet-api.js" | \
  ssh_sandbox "cat > $SKILLS_BASE/planet/scripts/planet-api.js"
ssh_sandbox "chmod +x $SKILLS_BASE/planet/scripts/planet-api.js"
ok "Skill files deployed"

# ── Step 6b: Enable skill in OpenClaw registry + tools.profile ─────
if [ "$LAYOUT" = "new" ]; then
  info "Configuring openclaw.json (skill registry + tools.profile)..."
  REGISTRY_OUT=$(configure_openclaw_json || echo "fail")
  if [ "$REGISTRY_OUT" != "fail" ]; then
    ok "openclaw.json updated"
  else
    warn "Could not update openclaw.json; the agent may not surface the skill."
    warn "Manual fix: edit $OPENCLAW_JSON and add:"
    warn '  "skills": { "entries": { "planet": { "enabled": true } } }'
    warn '  "tools":  { "profile": "coding" }'
  fi
fi

# ── Step 7: Write proxy URL to sandbox .env ──────────────────────
info "Writing proxy URL to sandbox..."
PROXY_URL="http://${HOST_IP}:${TOKEN_PORT}"
ssh_sandbox "cat > $SKILLS_BASE/planet/.env << ENVEOF
PLANET_PROXY_URL=${PROXY_URL}
ENVEOF"
ssh_sandbox "chmod 600 $SKILLS_BASE/planet/.env"
ok "Proxy URL deployed (no API key in sandbox)"

# ── Step 8: Save host-side config ─────────────────────────────────
mkdir -p "$INSTALL_DIR"
cat > "$INSTALL_DIR/config.env" << CFGEOF
PLANET_SANDBOX="$SANDBOX_NAME"
PLANET_LAYOUT="$LAYOUT"
PLANET_SKILLS_BASE="$SKILLS_BASE"
PLANET_HOST_IP="$HOST_IP"
PLANET_PORT="$TOKEN_PORT"
PLANET_OPENSHELL="$OPENSHELL_BIN"
CFGEOF

# ── Step 9: Verify ───────────────────────────────────────────────
echo ""
info "Verifying installation..."

SKILL_CHECK=$(ssh_sandbox "[ -f $SKILLS_BASE/planet/scripts/planet-api.js ] && echo ok" || true)
ENV_CHECK=$(ssh_sandbox "grep -q PLANET_PROXY_URL $SKILLS_BASE/planet/.env 2>/dev/null && echo ok" || true)
PROXY_CHECK=$(curl -sf "http://127.0.0.1:${TOKEN_PORT}/health" 2>/dev/null || true)

[ "$SKILL_CHECK" = "ok" ] && ok "Planet skill deployed" || warn "Planet skill not found"
[ "$ENV_CHECK" = "ok" ] && ok "Proxy URL configured (key stays on host)" || warn "Proxy URL not found"
[ "$PROXY_CHECK" = "ok" ] && ok "Planet proxy running on host" || warn "Planet proxy not responding"

# ── Done ──────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}  ╔══════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}  ║  Installation complete! (Tier 1 Security)               ║${NC}"
echo -e "${GREEN}  ╚══════════════════════════════════════════════════════════╝${NC}"
echo ""
echo "  Sandbox:    $SANDBOX_NAME"
echo "  Layout:     $LAYOUT (skills: $SKILLS_BASE/planet/)"
echo "  Proxy:      http://$HOST_IP:$TOKEN_PORT  (PID $PROXY_PID)"
echo ""
echo "  Security: Planet API key stays on host. The sandbox only has the proxy URL."
echo "  Rotate the key: edit $CREDS_PATH (takes effect immediately)."
echo ""
echo "  Next steps:"
echo "    1. Connect: nemoclaw $SANDBOX_NAME connect"
echo "    2. Try: \"What satellite imagery types does Planet offer?\""
echo "    3. Try: \"Search for clear imagery over San Francisco from last month\""
echo "    4. Try: \"How much would it cost to task a satellite over the Pentagon?\""
echo "    5. Try: \"What's my Planet quota?\""
echo ""
echo "  Commands:"
echo "    Status:     ./install.sh --status"
echo "    Update key: ./install.sh --update-key"
echo "    Uninstall:  ./install.sh --uninstall"
echo ""
echo -e "  ${YELLOW}If the agent doesn't recognize the skill, restart the openclaw TUI${NC}"
echo -e "  ${YELLOW}so the gateway re-reads openclaw.json.${NC}"
echo ""
