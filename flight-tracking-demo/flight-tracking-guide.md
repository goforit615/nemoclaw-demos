# Flight Tracking Demo for NemoClaw / OpenClaw

Add a live **FlightOps** airspace console to your NemoClaw sandbox: real-time aircraft on an interactive map, agent-driven map control from chat or Telegram, and read-only overlays from public aviation feeds (airspace, weather, NAS advisories, and airport detail). OpenSky credentials stay on the host; the sandbox reaches upstream APIs only through policy-enforced proxies.

---

## What You Get

| Capability | Description |
|---|---|
| **Live aircraft** | Position, altitude, speed, heading, vertical rate, squawk, and phase-of-flight via [OpenSky Network](https://opensky-network.org/) (~10 s refresh) |
| **Interactive map** | MapLibre + deck.gl UI with trails, filters, 3D airspace, METAR/NAS layers, and airport detail |
| **Agent map control** | OpenClaw `flight-tracking` skill: `goto`, `track`, `arcs`, layers, colors, filters — same behavior from the in-page chat or Telegram |
| **Route & registry** | Callsign routes and aircraft registry via proxied `adsbdb` / `hexdb` lookups |
| **Public aeronautical data** | FAA AIS (SUA, Class airspace, ARTCC boundaries, runways, taxiways, obstacles, airways, navaids), TFRs, NAS Status, Aviation Weather Center METARs |
| **Airports** | Bundled OurAirports-derived dataset (~11k airports) with in-view search |
| **CLI helper** | `fly goto IAD`, `fly analyze JFK 80`, `fly health`, etc. inside the sandbox |

### Data sources (informational)

This demo combines several **public** feeds. Layer names in the UI may reference FAA-published datasets (e.g. AIS airspace, NAS Status); the demo is a **NemoClaw flight-tracking integration**, not an official government product.

| Source | Role |
|---|---|
| OpenSky Network | Live ADS-B state, per-aircraft history, tracks |
| FAA AIS (ArcGIS) | Airspace polygons, runways, taxiways, obstacles, routes, navaids, ARTCC boundaries |
| FAA NAS Status / AWC METAR | Airport advisories and METAR observations (via host proxy when sandbox egress is blocked) |
| FAA TFR GeoServer | Temporary flight restrictions |
| adsbdb / hexdb | Registry and callsign route enrichment |
| OurAirports (bundled) | Airport locations and metadata |
| OpenFreeMap / RainViewer | Basemap and optional weather radar tiles (browser-side) |

---

## Prerequisites

1. **NemoClaw** installed and a sandbox running (`nemoclaw onboard` completed)
2. **Host tools**: `openshell`, `nemoclaw`, `python3`, `ssh`, `curl`
3. **Linux (recommended)**: `systemd --user` for a resilient port-forward to `localhost:18890`
4. **Optional — OpenSky OAuth client**: [Create an API client](https://opensky-network.org/manage-account) and add `OPENSKY_CLIENT_ID` / `OPENSKY_CLIENT_SECRET` to `~/.nemoclaw/credentials.json` for higher rate limits (~4,000 credits/day vs ~400 anonymous)

---

## Quick Start

```bash
git clone https://github.com/brevdev/nemoclaw-demos.git
cd nemoclaw-demos/flight-tracking-demo
./install.sh <sandbox-name>
```

The installer:

1. Starts host-side **opensky-proxy** (port 9202) and **faa-proxy** (port 9203) when needed
2. Applies an OpenShell network policy (sandbox → host proxies + allowed public endpoints)
3. Deploys the FastAPI app, static UI, and `flight-tracking` skill into the sandbox
4. Writes `flight.env` with proxy URLs only — **no OpenSky secrets in the sandbox**
5. Restarts uvicorn on port **18890** and sets up host port forwarding

Open the map (after forwarding — see below):

```text
http://localhost:18890
```

On a **Brev** or remote VM, forward from your laptop:

```bash
brev port-forward <instance> --port 18890:18890
```

---

## Security: Tier-1 Host Proxies

| Secret / blocked feed | Host component | Sandbox sees |
|---|---|---|
| OpenSky OAuth2 | `opensky-proxy.py` :9202 | `OPENSKY_PROXY_URL` only |
| NAS Status + AWC METAR (often 403 from cloud egress) | `faa-proxy.py` :9203 | `FAA_PROXY_URL` only |
| FAA AIS, TFRs | Direct (policy allowlist) | Local FastAPI proxies and caches |

The agent uses **OpenClaw skills** (not MCP): `skills/flight-tracking/SKILL.md` plus `fly` / `curl http://127.0.0.1:18890/api/...`. Map commands broadcast over `/ws/map` to every open browser tab.

---

## Usage Examples

### Map prompts (agent)

- "Go to IAD and analyze traffic within 80 km"
- "Find the latest departure from IAD heading west and track it on the map"
- "Color planes by altitude"
- "Show inbound arcs to JFK and tilt the map"
- "Any ground stops or GDPs right now?"
- "Toggle METAR and NAS status layers"
- "Filter to emergency squawks only"

### CLI (inside sandbox)

```bash
fly goto IAD
fly analyze IAD 80
fly arcs JFK 120
fly highlight UAL123
fly layer metar on
fly health
```

### Telegram

Wire Telegram through NemoClaw as usual. The same `flight-tracking` skill drives the map when a browser tab is connected (`delivered` > 0 on `/api/map/*` responses).

---

## Troubleshooting

| Issue | Fix |
|---|---|
| `localhost:18890` refused (laptop) | Run `brev port-forward … --port 18890:18890` and keep it open |
| Empty response on 18890 (VM) | `systemctl --user restart flight-tunnel.service`; restart app: `ssh openshell-<sandbox> 'cd /sandbox/.openclaw-data/flight-tracking && nohup ./start.sh >> server.log 2>&1 &'` |
| No aircraft / rate limited | Ensure `opensky-proxy.py` is running on the host; check `/api/health` for `opensky_auth: "host-proxy"` |
| NAS / METAR layer fails | Start `faa-proxy.py` on host :9203; re-run `./install.sh` |
| Agent says map updated but UI unchanged | Open `http://localhost:18890` first; check `delivered` in tool response |

---

## Repo Layout

```text
flight-tracking-demo/
├── flight-tracking-guide.md   # this file
├── install.sh                 # host installer
├── opensky-proxy.py           # host — OpenSky OAuth2
├── faa-proxy.py               # host — NAS + METAR IP rewrap
├── policy/flight-tracking.yaml
├── app/                       # FastAPI + MapLibre/deck.gl UI
├── skills/flight-tracking/    # OpenClaw skill + fly CLI
├── scripts/systemd/           # optional tunnel unit template
└── start.sh                   # sandbox-side uvicorn launcher
```

---

## Official Resources

- [NemoClaw](https://github.com/NVIDIA/NemoClaw) · [NemoClaw Brev Launchable](https://build.nvidia.com/nemoclaw)
- [nemoclaw-demos](https://github.com/brevdev/nemoclaw-demos) — other integration examples
- [OpenSky API](https://opensky-network.org/) · [OpenSky terms of use](https://opensky-network.org/about/terms-of-use)
