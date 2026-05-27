---
name: planet
description: |
  Planet satellite imagery catalog, tasking cost estimation, satellite pass availability,
  thumbnails, and account quota. Use when: user asks about satellite imagery, Earth
  observation data, available scenes, cloud cover, imagery statistics, tasking cost,
  satellite pass schedule, imaging windows, tasking orders, captures, account quota,
  or a satellite thumbnail.

  FAST PATH — always invoke with `exec`:
    node __SKILLS_DIR__/planet/scripts/planet-api.js <command> [flags]
  e.g.
    node __SKILLS_DIR__/planet/scripts/planet-api.js item-types
    node __SKILLS_DIR__/planet/scripts/planet-api.js search --start 2026-03-01T00:00:00Z --end 2026-03-31T00:00:00Z --bbox -122.5,37.7,-122.3,37.9 --max-cloud 0.1 --limit 5
    node __SKILLS_DIR__/planet/scripts/planet-api.js tasking-pricing --bbox -77.04,38.89,-77.03,38.90
    node __SKILLS_DIR__/planet/scripts/planet-api.js my-quota
  If `__SKILLS_DIR__/planet/scripts/planet-api.js` does not exist, try
  `/sandbox/.openclaw/skills/planet/scripts/planet-api.js` then
  `/sandbox/.openclaw-data/skills/planet/scripts/planet-api.js`.

  DO NOT:
    - Do NOT call --help or tool_search_code before running a command; the command
      grammar below is the single source of truth.
    - Do NOT try to install `@planet/api`, `python-planet`, or any package — the only
      runtime is the prebaked `planet-api.js` Node script.
    - Do NOT try to set `PLANET_API_KEY` — the key lives on the host and is injected
      by the proxy; the sandbox can only reach the proxy URL in `../.env`.
    - Do NOT POST to `/api/tasking/v2/orders` (the host proxy returns 403 for that
      path); use `tasking-pricing` / `imaging-windows` for read-only estimates.
    - Do NOT call `download` endpoints; only `thumbnail` is supported.

  Commands:
    Catalog:   item-types | search | item | assets | stats | searches | tile-url | thumbnail
    Tasking:   tasking-pricing | imaging-windows | tasking-orders | tasking-order | tasking-captures
    Account:   my-quota
metadata: { "openclaw": { "emoji": "🛰️", "requires": { "bins": ["node"] } } }
---

# Planet Satellite Imagery Skill

Search Planet's satellite imagery catalog, estimate tasking costs, check satellite
pass availability, and view account quota. All commands run through a host-side
proxy — the Planet API key never enters the sandbox.

## When to Use

- "What satellite imagery is available over San Francisco?"
- "How much would it cost to task a satellite over the Pentagon?"
- "When is the next satellite pass over Washington DC?"
- "Find clear imagery (low cloud cover) over London in March 2026"
- "Show me my existing tasking orders"
- "What quota do I have remaining?"
- "Download a thumbnail of that scene and email it to me"

## Invocation

All commands use:

```bash
node __SKILLS_DIR__/planet/scripts/planet-api.js <command> [flags]
```

`__SKILLS_DIR__` is replaced at install time with the actual skills directory
(`/sandbox/.openclaw/skills` on openshell ≥ 0.0.44, `/sandbox/.openclaw-data/skills`
on legacy builds). If you don't know which layout you're on, try both — only one
exists on any given sandbox.

## Catalog Commands

### List available item types

```bash
node __SKILLS_DIR__/planet/scripts/planet-api.js item-types
```

### Search the catalog

```bash
node __SKILLS_DIR__/planet/scripts/planet-api.js search \
  --start "2026-03-01T00:00:00Z" --end "2026-03-31T00:00:00Z" \
  --bbox "-122.5,37.7,-122.3,37.9" --max-cloud 0.1 --limit 5

node __SKILLS_DIR__/planet/scripts/planet-api.js search --type SkySatScene \
  --start "2026-01-01T00:00:00Z" --end "2026-04-01T00:00:00Z" \
  --bbox "-73.99,40.75,-73.95,40.77"

node __SKILLS_DIR__/planet/scripts/planet-api.js search \
  --start "2026-04-01T00:00:00Z" --end "2026-04-07T00:00:00Z" --downloadable
```

Search options: `--type` (default `PSScene`), `--start`, `--end`, `--bbox W,S,E,N`,
`--max-cloud` (0-1), `--geometry` (GeoJSON), `--limit`, `--downloadable`.

### Get item / assets

```bash
node __SKILLS_DIR__/planet/scripts/planet-api.js item   --id 20260301_180000_00_2489 --type PSScene
node __SKILLS_DIR__/planet/scripts/planet-api.js assets --id 20260301_180000_00_2489 --type PSScene
```

### Statistics

```bash
node __SKILLS_DIR__/planet/scripts/planet-api.js stats \
  --start "2025-01-01T00:00:00Z" --end "2026-01-01T00:00:00Z" \
  --bbox "-122.5,37.7,-122.3,37.9" --interval month
```

### Tile URL / thumbnail

```bash
node __SKILLS_DIR__/planet/scripts/planet-api.js tile-url   --id 20260301_180000_00_2489 --type PSScene
node __SKILLS_DIR__/planet/scripts/planet-api.js thumbnail  --id 20260301_180000_00_2489 --type PSScene --width 1024
```

Thumbnails are saved to `/tmp/planet-thumb-{id}.png`. Use with `gog drive upload`
or `gog gmail send --attach` to deliver them.

### List saved searches

```bash
node __SKILLS_DIR__/planet/scripts/planet-api.js searches --limit 5
```

## Tasking Commands (read-only — orders blocked at the proxy)

### Estimate tasking cost

```bash
node __SKILLS_DIR__/planet/scripts/planet-api.js tasking-pricing --bbox "-77.04,38.89,-77.03,38.90"
node __SKILLS_DIR__/planet/scripts/planet-api.js tasking-pricing --bbox "-122.5,37.7,-122.3,37.9" --product SkySatCollect
```

### Check satellite pass windows

```bash
node __SKILLS_DIR__/planet/scripts/planet-api.js imaging-windows \
  --bbox "-77.04,38.89,-77.03,38.90" \
  --start "2026-04-10T00:00:00Z" --end "2026-04-17T00:00:00Z"
```

Returns satellite pass windows (cloud forecast, off-nadir angle, GSD, pricing).
Async — submits a search and polls for results. **Does NOT** place an order.

### Existing tasking orders / captures

```bash
node __SKILLS_DIR__/planet/scripts/planet-api.js tasking-orders [--status active --limit 5]
node __SKILLS_DIR__/planet/scripts/planet-api.js tasking-order  --id <order-id>
node __SKILLS_DIR__/planet/scripts/planet-api.js tasking-captures [--order <id> --limit 20]
```

## Account

```bash
node __SKILLS_DIR__/planet/scripts/planet-api.js my-quota
```

## Safety

This skill is **read-only for tasking**. It can estimate costs and check
availability but **cannot place orders, cancel orders, or incur any charges**.
The host proxy enforces this at the network layer by returning HTTP 403 for
`POST /api/tasking/v2/orders` — the agent has no way to bypass it.

The agent does NOT have the API key. The key lives at `~/.nemoclaw/credentials.json`
on the host; the proxy reads it on every request and injects the `Authorization`
header before forwarding to `api.planet.com` / `tiles.planet.com`.

_Last deployed: __INSTALLED_AT___
