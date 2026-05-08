# network-bandwidth-monitor

Docker app for tracking network bandwidth usage and connection destinations over time on all Unraid interfaces.

## What it does

- Reads `/proc/net/dev` every 10 s → per-interface RX/TX rates stored in SQLite
- Reads `/proc/net/nf_conntrack` every 60 s → active connections (remote IP, port, protocol, bytes delta)
- Maps connections to interfaces via the host routing table (`/proc/net/route`)
- Reverse-DNS resolves destination IPs in the background
- Rolls up raw 10-s samples into hourly aggregates; raw kept 7 days, hourly kept forever
- Web dashboard at the container IP with bandwidth charts and top-destinations table

## Unraid setup

### 1. Find your NIC name

SSH into Unraid and run:

```bash
ip link | grep -E '^[0-9]+:' | awk '{print $2}'
```

Common values: `eth0`, `eth1`, `bond0`. Edit `docker-compose.yml` and set `parent:` to match.

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

## Notes

- **conntrack availability**: Connection tracking requires the `nf_conntrack` kernel module (loaded automatically by Docker/iptables on Unraid). If the file doesn't exist, bandwidth stats still work fine — the connections panel will just be empty.
- **macvlan limitation**: The container has its own MAC/IP on your LAN. The Unraid host itself cannot reach the container IP directly (macvlan isolation), but any other device on the network can. Access from Unraid by routing through another machine or use `--network=host` instead (remove the `networks:` section and `ipv4_address`).
- **Data location**: SQLite database is in a named Docker volume (`netmon_data`). Back it up with `docker run --rm -v netmon_data:/data -v $(pwd):/out alpine tar czf /out/netmon_backup.tar.gz /data`.

## Environment variables

| Variable   | Default          | Description                        |
|------------|------------------|------------------------------------|
| `DB_PATH`  | `/data/netmon.db`| SQLite database path               |
| `NET_BASE` | `/host/net`      | Base path for `/proc/net` files    |
