# Changelog

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
