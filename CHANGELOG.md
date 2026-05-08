# Changelog

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
