# Changelog

## [0.10.2] - 2026-05-14

### Fixed
- Devices view now falls back to Firewalla `fw_conn_hourly` data when `conn_hourly` is empty, and further falls back to `fw_devices` metadata for devices with zero traffic
- DNS resolution now covers all Firewalla device IPs, not just IPs in `conn_hourly`
- `/api/device_bandwidth` now falls back to `fw_conn_hourly` when `conn_hourly` has no data for a device; also returns aggregated all-device data when no specific IP is requested
- Interface sub-select now only shows the 3 intended interfaces (Cox WAN, Starlink WAN, StorageDemon) — previously showed every interface ever recorded including bond0, eth0, eth1
- Live throughput header (top of page) is now view-independent — always shows all 3 WAN interfaces regardless of which view (Interfaces/Containers/Devices/CF Tunnel) is active
- `INTERFACE_DISPLAY_NAMES` corrected — `bond0` is no longer aliased to "StorageDemon" (br0 is the correct interface name)
- Sub-select rebuilt from scratch by `refreshHeader` instead of appended to, eliminating duplicate entries from accumulating
- `aggregate_hourly` now uses ADD for rx_bytes/tx_bytes instead of MAX, preventing data loss if the scheduler runs twice for the same hour boundary
- Devices chart in Devices view now shows aggregated "All Devices" data when no specific device is selected
- `range=all` capped at 1 year (was 10 years, causing unbounded queries on large datasets)

## [0.10.1] - 2026-05-13

### Fixed
- Connection tracker now fires immediately on startup to seed state (was previously only triggered on the 60s interval, causing the first collection cycle to be lost)
- Remove unreachable dead-code branch in `container_purge_inactive` that prevented the endpoint from ever working
- `fw_flows_collector.start()` now holds `_lock` around the running check to prevent duplicate collector threads from racing
- DNS resolver replaced unbounded thread-per-IP spawning with `ThreadPoolExecutor(max_workers=10)`

### Changed
- Graceful shutdown: collectors now stop cleanly on SIGTERM/SIGINT (`collector.stop()`, `fw_collector.stop()`, `fw_flows_collector.stop()`, `starlink_collector.stop()`)
- `starlink_collector` SSH calls now retry 3 times with 1s/2s backoff before failing a sample cycle
- SSH key path in `starlink_collector` is now configurable via `STARLINK_SSH_KEY` env var (defaults to `/root/.ssh/id_firewalla`)

## [0.10.0] - 2026-05-08

### Added
- Firewalla flow-based destinations (`fw_conn_hourly`) — all LAN devices tracked, not just Unraid containers, via `/v1/flow` API polled every 5 min
- `fw_conn_hourly` table with per-destination attribution across all LAN clients
- Connections table now shows all LAN devices (Firewalla) or Unraid containers only (conntrack), with scope indicator
- `source_count` and `source_names` in connections API — shows which LAN devices contacted each destination
- `domain` field captured from Firewalla flows (DNS/SNI hostname)
- CDN service detection now shows "(via CDN)" suffix for identified CDN traffic
- Column alignment fixes in connections table (total_bytes right-aligned, port/protocol centered)

### Changed
- Connections API (`/api/connections`) now prefers Firewalla flow data when available; falls back to conntrack
- `conn_hourly` source attribution still available for non-Firewalla setups

## [0.9.0] - 2026-05-08

### Added
- Cox WAN (eth0) tracked alongside Starlink (eth3) in WAN collector — both accessible via `fw_wan_eth0` / `fw_wan_eth3` sub-select
- Firewalla device groups displayed as badge in Devices table
- `fw_devices` table now includes `group_name` field from Firewalla API
- Destinations table limited to top 10 by default with expand/collapse controls

### Changed
- WAN sub-select shows both "Cox WAN" and "Starlink WAN" as separate options in the Interfaces view
- Firewalla WAN (eth0) used as authoritative total for the "All" interfaces view when Starlink collector is available

## [0.8.0] - 2026-05-08

### Added
- `cf_tunnel_hourly` table with protocol and port breakdown for Cloudflare Tunnel traffic
- Sortable connections and devices tables with ascending/descending toggle
- Totals section shows last 24h, 7d, 14d, 30d in a single compact table
- `local_subnet` setting to configure RFC1918 subnet for local/external classification

### Fixed
- 172.x.x.x Docker internal IPs correctly excluded from device tracking
- Collection reliability: conntrack now handles IPv6 connections gracefully
- `iface_for_ip()` routing table parser fixed for newer kernel route formats

## [0.7.0] - 2026-05-08

### Added
- LAN device bytes from Firewalla (`fw_rx_bytes`, `fw_tx_bytes`) shown for devices not yet seen in conntrack
- Container deduplication by name — rebuilt containers that keep the same name consolidate history
- "Purged inactive containers" button to clean up stale container history
- Auto-close settings modal 1.2s after successful save

### Changed
- Device display: Firewalla device name shown when no user label is set, before falling back to DNS hostname
- Active container detection fixed — `docker stats` now properly identifies currently-running vs stopped containers
- Settings modal now shows separate fields for Firewalla API IP and SSH IP

## [0.6.0] - 2026-05-08

### Added
- Firewalla device groups (Family, Work, etc.) shown as a filterable badge in Devices view
- Compact sub-select dropdown for interface/container/device selection
- `fw_devices` table synced from Firewalla Gold Plus local API every 5 minutes

### Changed
- Settings modal clarified: separate fields for Unraid IP (API endpoint) and Firewalla IP (direct SSH)
- Firewalla port defaults to 18834 for SSH-tunnelled access on Unraid, 8834 for direct access

## [0.5.0] - 2026-05-08

### Added
- Cloudflare Zero Trust tunnel detection — CF tunnel container traffic separated from outbound connections
- `cf_tunnel_hourly` aggregates tunnel traffic with service IP/port breakdown
- `is_cloudflare` badge on connections, containers, and devices when traffic routes through Cloudflare
- Cloudflare IP range checks (7 CIDR ranges) to identify CDN and tunnel destinations

### Changed
- Connection tracking now correctly attributes Cloudflare tunnel traffic to `cf_tunnel_hourly` instead of `conn_hourly`

## [0.4.0] - 2026-05-08

### Added
- Firewalla Gold Plus local API integration (port 8834, no auth required)
- `/v1/host/all` polling every 5 min — device names, MACs, MAC vendors synced to `fw_devices` table
- `fw_devices` SQLite table with automatic migration from v0.3 databases
- `app/fw_collector.py` — background poller, also callable on-demand via "Sync Devices" button
- `/api/fw_devices` endpoint listing all Firewalla-known devices
- `/api/settings/fw_sync` POST endpoint for manual on-demand sync
- Device display name priority: user label → Firewalla name → DNS hostname → IP
- MAC address and vendor (e.g. "Apple, Inc") shown in Devices table subtitle row
- "Sync Devices" button in Settings modal — pulls latest device list immediately
- Firewalla token field is now optional (local API is unauthenticated)
- `fw_name`, `fw_mac`, `fw_vendor` fields returned by `/api/devices`

### Changed
- `firewalla.py` port corrected 8833 → 8834
- `firewalla.available()` no longer requires a token — only IP is needed
- `get_devices()` correctly unwraps `{"hosts": [...]}` response envelope
- Settings modal "Test Connection" saves IP before testing (no need to hit Save first)

## [0.3.0] - 2026-05-08

### Added
- Per-device traffic breakdown — all source IPs tracked in `conn_hourly`, new Devices view
- Inline device labelling — click any IP to name it (Plex, Deluge, etc.), stored in SQLite
- CF Tunnel view — tracks connections CloudflareTunnel forwards to local services, separate from outbound traffic
- `cf_tunnel_hourly` table with bandwidth chart and sortable services table
- `device_labels` table persisted in SQLite, editable from UI without page reload
- `IGNORE_INTERFACES` env var to hide specific interfaces (VLANs, unused NICs, VM bridges)
- `CF_TUNNEL_CONTAINER` and `LOCAL_SUBNET` env vars for configuration
- `.env` / `.env.example` pattern — all site-specific values kept out of git (repo is public-safe)
- `_migrate()` — auto-upgrades v0.2 databases to v0.3 schema without data loss
- `/api/devices`, `/api/device_bandwidth`, `/api/cf_tunnel`, `/api/cf_tunnel_bandwidth`, `/api/label`, `/api/labels` endpoints
- 172.x.x.x Docker internal traffic excluded from `conn_hourly` (already filtered by `_is_external`)

### Changed
- `conn_hourly` PRIMARY KEY now includes `source_ip` for per-device attribution
- `docker-compose.yml` uses `env_file: .env` — no personal data committed
- `upsert_conn_delta` updated to include `source_ip`
- CF tunnel connections to local services routed to `cf_tunnel_hourly` instead of `conn_hourly`

## [0.2.0] - 2026-05-08

### Added
- Per-Docker-container bandwidth tracking via Docker stats API (mounted socket)
- Containers view in dashboard with live rates, chart, and per-container history
- Cloudflare Zero Trust tunnel detection — CF IP ranges tagged with a badge in connections view
- `cloudflared` container highlighted in container list
- Known port labels: CF-QUIC (7844), WireGuard (51820), and others
- `/api/containers` and `/api/container_bandwidth` API endpoints
- Version reported in `/api/status` and dashboard footer

### Changed
- `docker-compose.yml` mounts `/var/run/docker.sock` read-only for container stats
- Hourly aggregation now covers container bandwidth tables as well
- `veth`, `docker0`, `br-*`, `shim-*` interfaces filtered from collector

## [0.1.0] - 2026-05-08

### Added
- Initial release
- Per-interface bandwidth tracking via `/proc/net/dev` (10 s samples)
- Connection destination tracking via `/proc/net/nf_conntrack` (60 s deltas)
- Interface attribution via routing table (`/proc/net/route`)
- Background reverse DNS resolution with 24 h cache
- SQLite storage: 7-day raw retention, permanent hourly aggregates
- Flask + Chart.js dark dashboard with interface tabs and time-range selector
- Docker deployment with macvlan networking on Unraid's `br0`
