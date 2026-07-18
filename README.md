# network-bandwidth-monitor

Docker app for tracking network bandwidth usage and connection destinations over time on all Unraid interfaces.

**Current version:** 0.14.0

## What it does

- Reads `/proc/net/dev` every 10 s → per-interface RX/TX rates stored in SQLite
- Reads `/proc/net/nf_conntrack` every 60 s → active connections (remote IP, port, protocol, bytes delta)
- Maps connections to interfaces via the host routing table (`/proc/net/route`)
- Reverse-DNS resolves destination IPs in the background
- Rolls up raw 10-s samples into hourly aggregates; raw kept 7 days, hourly kept forever
- Web dashboard at the container IP with bandwidth charts and top-destinations table
- **Docker container tracking** — per-container bandwidth via Docker stats API
- **Cloudflare Zero Trust tunnel detection** — separate tracking for tunnel-forwarded traffic
- **Firewalla Gold Plus integration** — all LAN devices tracked via flow API, not just Unraid containers
- **Device labeling** — name your devices (Plex, Deluge, etc.) for easier identification
- **Starlink/Cox WAN tracking** — SSH into Firewalla to read WAN interface counters

## Unraid setup

### 1. Set the macvlan parent interface

The `docker-compose.yml` defaults to `parent: br0` (Unraid's main bridge). If your `10.0.0.0/20` network is on **VLAN 10**, change it to `parent: br0.10`.

Unraid creates a `shim-br0` interface automatically, so the host **can** reach the container IP — no workaround needed.

### 2. Set your gateway

In `docker-compose.yml`, update the `gateway:` field under `ipam.config` to match your router's IP.

### 3. Verify the static IP is free

```bash
ping -c 1 10.0.9.47
```

If it replies, pick a different IP in the `10.0.0.0/20` range and update `ipv4_address` in `docker-compose.yml`.

### 4. Clone and start

```bash
cd /mnt/user/appdata   # or wherever you keep Docker app data
git clone https://github.com/frindle/network-bandwidth-monitor
cd network-bandwidth-monitor
docker compose up -d --build
```

### 5. Open the dashboard

Navigate to `http://10.0.9.47:8080` in your browser.

## Environment variables

| Variable                | Default                    | Description                                    |
|-------------------------|----------------------------|------------------------------------------------|
| `DB_PATH`               | `/data/netmon.db`          | SQLite database path                           |
| `NET_BASE`              | `/host/net`                | Base path for `/proc/net` files                |
| `LOCAL_SUBNET`          | `10.0.0.0/20`             | RFC1918 subnet for local/external classification|
| `IGNORE_INTERFACES`     | (empty)                    | Comma-separated interface names to skip         |
| `CF_TUNNEL_CONTAINER`   | `CloudflareTunnel`         | Exact Docker container name for cloudflared     |
| `STARLINK_SSH_KEY`      | `/root/.ssh/id_firewalla`  | SSH key path for Firewalla WAN counter access   |

## Firewalla integration (optional)

If you have a Firewalla Gold Plus device, you can enable:

- **All LAN devices** — connection tracking for every device on your network, not just Unraid containers
- **WAN bandwidth** — Cox (eth0) and Starlink (eth3) counters via SSH
- **Device names** — friendly names from Firewalla's device database
- **Device groups** — Family, Work, etc. shown as badges
- **SSH poll health** — Settings shows success rate and latency for the SSH-per-poll connection over the last 24h/7d, to diagnose flaky connections

To enable, open the dashboard → Settings → enter your Firewalla IP and API token.

## Cloudflare edge analytics (optional)

If your tunnel hostnames sit on a domain in your Cloudflare account, the dashboard can also show **edge-side** traffic that local conntrack cannot see: HTTP request counts and bytes served *per public hostname* (including cached/edge-served responses).

This is polled hourly from Cloudflare's GraphQL Analytics API (`httpRequestsAdaptiveGroups`) and stored in `cf_edge_hourly`. It complements the existing conntrack-based CF Tunnel view (which shows bytes forwarded to each *local* service).

To enable, create a Cloudflare API token with **Zone -> Analytics -> Read** permission, then open the dashboard -> Settings -> **Cloudflare Edge Analytics** -> paste the token and your Zone ID (from the domain's Overview page; comma-separate several zones). Use **Test Connection** to verify. Data appears in the **CF Tunnel** view after the first hourly poll.

Notes:
- Works on the **Free** plan. Cloudflare's own retention for this dataset is short, but the app stores its own hourly history in SQLite, so history accumulates from when you enable it.
- Rate limits are a non-issue: Cloudflare allows ~300 GraphQL queries per 5 min; this polls once per hour per zone.

## Notes

- **conntrack availability**: Connection tracking requires the `nf_conntrack` kernel module (loaded automatically by Docker/iptables on Unraid). If the file doesn't exist, bandwidth stats still work fine — the connections panel will just be empty.
- **macvlan + shim**: Unraid automatically creates a `shim-br0` interface so the host can reach macvlan container IPs directly. No extra configuration needed.
- **Data location**: SQLite database is in a named Docker volume (`netmon_data`). Back it up with `docker run --rm -v netmon_data:/data -v $(pwd):/out alpine tar czf /out/netmon_backup.tar.gz /data`.
- **Graceful shutdown**: The app now handles SIGTERM/SIGINT signals properly — collectors stop cleanly before the container exits.
