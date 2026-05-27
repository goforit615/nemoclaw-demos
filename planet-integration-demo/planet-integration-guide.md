# Planet Integration Guide for NemoClaw / OpenClaw

Add [Planet Insights Platform](https://docs.planet.com/develop/apis/) satellite imagery capabilities to your NemoClaw agent. Search Planet's catalog, estimate tasking costs, check satellite pass availability, view account quota, and download thumbnails — all through the secure OpenShell sandbox.

---

## What You Get

| Capability | Description |
|---|---|
| **Catalog Search** | Search Planet's archive by location, date, cloud cover, and item type |
| **Statistics** | Histogram counts of available imagery over time |
| **Item Details** | Full metadata for any scene (geometry, properties, acquisition time) |
| **Asset Listing** | Check available asset types and activation status |
| **Thumbnails** | Download scene thumbnails to `/tmp/` |
| **Tile URLs** | Generate XYZ tile URLs for visualization |
| **Tasking Pricing** | Estimate tasking cost for an area (read-only, no orders) |
| **Imaging Windows** | Check satellite pass availability and feasibility |
| **Tasking Orders** | View existing orders and their status |
| **Tasking Captures** | List captures associated with orders |
| **Account Quota** | Show products with remaining quota |

### Safety

All tasking commands are **read-only**. The proxy blocks `POST` requests to the order-creation endpoint at the network level, making it physically impossible to place orders or incur charges — even if the agent is compromised.

---

## Prerequisites

1. **NemoClaw** installed and a sandbox running (`nemoclaw onboard` completed).
2. **Planet account** with an API key from [planet.com/account](https://www.planet.com/account/#/user-settings).
3. **Python 3** on the host (for the proxy service).
4. **Node.js** inside the sandbox (included by default at `/usr/local/bin/node`).

---

## Quick Start

```bash
cd planet-integration-demo
./install.sh
```

The script:

1. Auto-detects your sandbox and the OpenClaw layout (`/sandbox/.openclaw/` for openshell ≥ 0.0.44, falls back to `/sandbox/.openclaw-data/`).
2. Verifies SSH connectivity to the sandbox.
3. Prompts for your Planet API key (saved to `~/.nemoclaw/credentials.json` with `0600` permissions).
4. Starts the host-side proxy service on the detected host IP.
5. Applies the network policy (or replaces an outdated `planet_proxy` block if the host IP changed).
6. Deploys the skill files to the sandbox under the detected `skills/` directory.
7. On the new layout, enables the `planet` skill in `openclaw.json` and sets `tools.profile = "coding"` so the agent surfaces the `exec` tool needed to run `node`.
8. Writes the proxy URL to the skill's `.env` (the API key never enters the sandbox).

Restart the OpenClaw TUI after install so the gateway re-reads `openclaw.json` and picks up the new skill.

---

## Security: Tier 1 (Host-Side Proxy)

Your Planet API key **never enters the sandbox**.

```
+----------------------------------+
|  OpenShell Sandbox               |
|                                  |
|  planet-api.js (node)            |
|    GET /api/data/v1/item-types   |
|    (no credentials)              |
|         |                        |
+---------|------------------------+
          | HTTP (policy-enforced)
          v
+----------------------------------+
|  Host: planet-proxy.py (:9201)   |
|                                  |
|  Reads ~/.nemoclaw/credentials   |
|  Injects Authorization header    |
|  Blocks order creation (403)     |
|         |                        |
+---------|------------------------+
          | HTTPS
          v
+----------------------------------+
|  api.planet.com                  |
|  tiles.planet.com                |
+----------------------------------+
```

| Layer | Protection |
|---|---|
| **API Key Isolation** | Key on host only, never in the sandbox |
| **Host-Side Proxy** | Injects credentials and forwards requests |
| **Order Blocklist** | `POST /api/tasking/v2/orders` returns 403 |
| **Network Policy** | L7 proxy restricts sandbox to the proxy endpoint only |
| **Binary Scoping** | Only `node` can make outbound requests |
| **No Docker Rebuild** | Works on a live sandbox |
| **Hot-Updatable Keys** | Edit `credentials.json`, changes are instant |

---

## Usage Examples

### Catalog prompts

- "What satellite imagery types does Planet offer?"
- "Search for clear imagery over San Francisco from last month"
- "How many PlanetScope scenes cover London this year?"
- "Show me details for that scene"
- "Download a thumbnail of that scene"

### Tasking prompts (read-only)

- "How much would it cost to task a satellite over the Pentagon?"
- "When is the next satellite pass over Washington DC?"
- "Show me my existing tasking orders"
- "What's my Planet quota?"

### CLI usage (inside the sandbox)

Replace `<SKILLS_DIR>` with `/sandbox/.openclaw/skills` (new layout) or `/sandbox/.openclaw-data/skills` (legacy):

```bash
node <SKILLS_DIR>/planet/scripts/planet-api.js item-types
node <SKILLS_DIR>/planet/scripts/planet-api.js search \
  --start "2026-03-01T00:00:00Z" --end "2026-03-31T00:00:00Z" \
  --bbox "-122.5,37.7,-122.3,37.9" --max-cloud 0.1 --limit 5
node <SKILLS_DIR>/planet/scripts/planet-api.js stats \
  --start "2025-01-01T00:00:00Z" --end "2026-01-01T00:00:00Z" \
  --bbox "-122.5,37.7,-122.3,37.9" --interval month
node <SKILLS_DIR>/planet/scripts/planet-api.js thumbnail --id <item-id> --width 1024

node <SKILLS_DIR>/planet/scripts/planet-api.js tasking-pricing --bbox "-77.04,38.89,-77.03,38.90"
node <SKILLS_DIR>/planet/scripts/planet-api.js imaging-windows \
  --bbox "-77.04,38.89,-77.03,38.90" --start "2026-04-10T00:00:00Z" --end "2026-04-17T00:00:00Z"
node <SKILLS_DIR>/planet/scripts/planet-api.js my-quota
```

---

## File Structure

```
planet-integration-demo/
├── install.sh                         # Auto-detects layout, manages proxy + policy + skill
├── planet-proxy.py                    # Host-side API proxy (Tier 1)
├── planet-integration-guide.md        # This guide
├── policy/
│   └── planet.yaml                    # Policy template (reference only)
└── skills/
    └── planet/
        ├── SKILL.md                   # Agent skill definition (FAST PATH in frontmatter)
        └── scripts/
            └── planet-api.js          # Node.js API client

Host-side state (created by install.sh):
~/.nemoclaw/planet/config.env          # sandbox, layout, paths, host IP, port
~/.nemoclaw/credentials.json           # PLANET_API_KEY (chmod 600)

Inside the sandbox (paths depend on auto-detected layout):

  New layout (openshell ≥ 0.0.44):
    /sandbox/.openclaw/skills/planet/SKILL.md
    /sandbox/.openclaw/skills/planet/scripts/planet-api.js
    /sandbox/.openclaw/skills/planet/.env       (PLANET_PROXY_URL only)
    /sandbox/.openclaw/openclaw.json            (skills.entries.planet.enabled=true,
                                                 tools.profile="coding")

  Legacy layout:
    /sandbox/.openclaw-data/skills/planet/...   (no openclaw.json edits)
```

---

## Commands

| Command | Description |
|---|---|
| `./install.sh` | Install (interactive sandbox + key prompt) |
| `./install.sh my-sandbox` | Install against a specific sandbox |
| `./install.sh --update-key` | Force re-prompt for the Planet API key |
| `./install.sh --port 9202` | Use a custom host proxy port |
| `./install.sh --status` | Show current install + proxy state |
| `./install.sh --uninstall` | Stop proxy, remove skill files, drop policy block, clean local files |

---

## Compatibility

Supported OpenClaw layouts (auto-detected):

- **New** — `/sandbox/.openclaw/` (openshell ≥ 0.0.44, openclaw ≥ 2026.5.x). Auto-enables the skill in `openclaw.json` and sets `tools.profile = "coding"`.
- **Legacy** — `/sandbox/.openclaw-data/`. Older OpenShell builds. Skill files are deployed; no `openclaw.json` mutation is needed (skill discovery worked differently in older gateways).

Re-running `./install.sh` is safe and idempotent — it re-detects the layout, replaces the `planet_proxy` policy block if the host IP changed, and restarts the proxy.

---

## Troubleshooting

| Issue | Solution |
|---|---|
| Agent says "I don't have an exec tool" / can't run node | New layout needs `tools.profile = "coding"` in `openclaw.json`. Re-run `./install.sh` (it sets this automatically). |
| Agent doesn't find the skill | Restart the OpenClaw TUI so the gateway re-reads `openclaw.json`. |
| `Planet proxy unreachable` | `./install.sh --status` will show whether the proxy is running. If not, `./install.sh` to restart it. |
| `Credential load failed` | Check `PLANET_API_KEY` in `~/.nemoclaw/credentials.json`. |
| `401 Unauthorized` | API key invalid — run `./install.sh --update-key`. |
| `403 Blocked` on tasking order | Expected — order creation is blocked at the proxy for safety. |
| Host IP changed (laptop changed networks) | Re-run `./install.sh`; the script detects the new IP and replaces the `planet_proxy` policy block. |
| No search results | Expand date range, increase `--max-cloud`, try broader `--bbox`. |

---

Created by **Tim Klawa** (tklawa@nvidia.com)
