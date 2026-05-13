import os
import re
import socket
import struct
import threading
import time

from apscheduler.schedulers.background import BackgroundScheduler

import app.database as db
import app.docker_stats as docker_stats

NET_BASE      = os.environ.get('NET_BASE', '/host/net')
NET_DEV       = f'{NET_BASE}/dev'
NET_CONNTRACK = f'{NET_BASE}/nf_conntrack'
NET_ROUTE     = f'{NET_BASE}/route'

_CF_CONTAINER = os.environ.get('CF_TUNNEL_CONTAINER', 'CloudflareTunnel')

# Comma-separated interface names to exclude from bandwidth tracking
_IGNORE_IFACES = set(
    i.strip() for i in os.environ.get('IGNORE_INTERFACES', '').split(',') if i.strip()
)

# Parse LOCAL_SUBNET env var (default 10.0.0.0/20)
_subnet_str, _prefix_str = os.environ.get('LOCAL_SUBNET', '10.0.0.0/20').split('/')
_local_prefix = int(_prefix_str)
_local_net    = struct.unpack('>I', socket.inet_aton(_subnet_str))[0]
_local_mask   = (0xFFFFFFFF << (32 - _local_prefix)) & 0xFFFFFFFF

_lock              = threading.Lock()
_last_iface_bytes  = {}
_last_conn_bytes   = {}
_current_rates     = {}
_scheduler         = None
_last_bw_ts        = 0
_last_conn_ts      = 0


# ── helpers ────────────────────────────────────────────────────────────────────

def skip_iface(name: str) -> bool:
    if name in _IGNORE_IFACES:
        return True
    skip_prefixes = ('lo', 'veth', 'docker', 'br-', 'shim-', 'tunl0')
    return any(name.startswith(p) for p in skip_prefixes)


def _is_local(ip: str) -> bool:
    try:
        v = struct.unpack('>I', socket.inet_aton(ip))[0]
        return (v & _local_mask) == (_local_net & _local_mask)
    except OSError:
        return False


def _is_external(ip: str) -> bool:
    """True if ip is not RFC1918, loopback, or link-local."""
    try:
        v = struct.unpack('>I', socket.inet_aton(ip))[0]
    except OSError:
        return False
    private = [
        (0x0A000000, 8),    # 10.0.0.0/8
        (0xAC100000, 12),   # 172.16.0.0/12
        (0xC0A80000, 16),   # 192.168.0.0/16
        (0x7F000000, 8),    # 127.0.0.0/8
        (0xA9FE0000, 16),   # 169.254.0.0/16
    ]
    for net, prefix in private:
        mask = (0xFFFFFFFF << (32 - prefix)) & 0xFFFFFFFF
        if (v & mask) == (net & mask):
            return False
    return True


# ── bandwidth ──────────────────────────────────────────────────────────────────

def _read_iface_bytes() -> dict:
    result = {}
    try:
        with open(NET_DEV) as f:
            for line in f.readlines()[2:]:
                parts = line.split()
                iface = parts[0].rstrip(':')
                if skip_iface(iface):
                    continue
                result[iface] = (int(parts[1]), int(parts[9]))
    except OSError:
        pass
    return result


def collect_bandwidth():
    now     = int(time.time())
    current = _read_iface_bytes()
    samples = []

    with _lock:
        for iface, (rx, tx) in current.items():
            if iface in _last_iface_bytes:
                last_rx, last_tx, last_ts = _last_iface_bytes[iface]
                dt = now - last_ts
                if dt > 0:
                    rx_rate = max(0, rx - last_rx) / dt
                    tx_rate = max(0, tx - last_tx) / dt
                    samples.append((now, iface, rx_rate, tx_rate))
                    _current_rates[iface] = {'rx': rx_rate, 'tx': tx_rate}
            _last_iface_bytes[iface] = (rx, tx, now)

    if samples:
        db.insert_bw_raw(samples)


# ── routing ────────────────────────────────────────────────────────────────────

def _read_routes() -> list:
    routes = []
    try:
        with open(NET_ROUTE) as f:
            next(f)
            for line in f:
                parts = line.split()
                if len(parts) < 8:
                    continue
                routes.append((parts[0], int(parts[1], 16), int(parts[7], 16)))
    except OSError:
        pass
    return routes


def _iface_for_ip(ip: str, routes: list) -> str:
    try:
        ip_int = struct.unpack('>I', socket.inet_aton(ip))[0]
    except OSError:
        return 'unknown'
    best, best_prefix = 'unknown', -1
    for iface, dest_le, mask_le in routes:
        dest_n = struct.unpack('>I', struct.pack('<I', dest_le))[0]
        mask_n = struct.unpack('>I', struct.pack('<I', mask_le))[0]
        if (ip_int & mask_n) == (dest_n & mask_n):
            prefix = bin(mask_n).count('1')
            if prefix > best_prefix:
                best_prefix, best = prefix, iface
    return best


# ── conntrack ──────────────────────────────────────────────────────────────────

_CT_RE = re.compile(
    r'src=(\S+)\s+dst=(\S+)\s+sport=(\d+)\s+dport=(\d+)'
    r'\s+packets=\d+\s+bytes=(\d+)'
    r'\s+src=\S+\s+dst=\S+\s+sport=\d+\s+dport=\d+'
    r'\s+packets=\d+\s+bytes=(\d+)'
)


def _parse_ct_line(line: str):
    parts = line.split()
    if len(parts) < 3:
        return None
    family, proto = parts[0], parts[2]
    if family not in ('ipv4', 'ipv6') or proto not in ('tcp', 'udp'):
        return None
    m = _CT_RE.search(line)
    if not m:
        return None
    src, dst, _sp, dport, orig_bytes, reply_bytes = m.groups()
    return {
        'proto':    proto,
        'src':      src,
        'dst':      dst,
        'dport':    int(dport),
        'tx_bytes': int(orig_bytes),
        'rx_bytes': int(reply_bytes),
        'key':      (proto, src, dst, int(dport)),
    }


def collect_connections():
    now     = int(time.time())
    hour_ts = (now // 3600) * 3600
    routes  = _read_routes()

    # Get CloudflareTunnel container IPs (refresh each cycle in case container restarts)
    cf_ips = set(docker_stats.get_container_ips(_CF_CONTAINER)) if docker_stats.available() else set()

    try:
        with open(NET_CONNTRACK) as f:
            lines = f.readlines()
    except OSError:
        return

    new_state  = {}
    conn_deltas = []
    cf_deltas   = []

    with _lock:
        for line in lines:
            entry = _parse_ct_line(line)
            if not entry:
                continue

            key    = entry['key']
            new_tx = entry['tx_bytes']
            new_rx = entry['rx_bytes']
            new_state[key] = (new_tx, new_rx)

            if key not in _last_conn_bytes:
                continue  # new connection — seed state, don't inflate first period

            last_tx, last_rx = _last_conn_bytes[key]
            tx_delta = max(0, new_tx - last_tx)
            rx_delta = max(0, new_rx - last_rx)
            if tx_delta == 0 and rx_delta == 0:
                continue

            src, dst, dport, proto = entry['src'], entry['dst'], entry['dport'], entry['proto']

            if src in cf_ips and not _is_external(dst):
                # Traffic forwarded by CloudflareTunnel to a local service
                # (dst may be 10.x, 172.x, or 192.168.x depending on service placement)
                cf_deltas.append((hour_ts, dst, dport, proto, tx_delta, rx_delta))
            elif _is_external(dst):
                # Regular outbound connection from any LAN device
                iface = _iface_for_ip(dst, routes)
                conn_deltas.append((hour_ts, iface, src, proto, dst, dport, tx_delta, rx_delta))

        _last_conn_bytes.clear()
        _last_conn_bytes.update(new_state)

    for args in conn_deltas:
        db.upsert_conn_delta(*args)
    for args in cf_deltas:
        db.upsert_cf_tunnel(*args)


# ── public ─────────────────────────────────────────────────────────────────────

def is_local(ip: str) -> bool:
    return _is_local(ip)


def current_rates() -> dict:
    with _lock:
        return {iface: dict(v) for iface, v in _current_rates.items()}


def last_collection_times() -> dict:
    return {'bw': _last_bw_ts, 'conn': _last_conn_ts}


def _safe_collect_bandwidth():
    global _last_bw_ts
    try:
        collect_bandwidth()
        _last_bw_ts = int(time.time())
    except Exception:
        import traceback
        traceback.print_exc()


def _safe_collect_connections():
    global _last_conn_ts
    try:
        collect_connections()
        _last_conn_ts = int(time.time())
    except Exception:
        import traceback
        traceback.print_exc()


def _safe_aggregate():
    try:
        db.aggregate_hourly()
    except Exception:
        import traceback
        traceback.print_exc()


def _safe_docker():
    try:
        docker_stats.collect_docker_stats()
    except Exception:
        import traceback
        traceback.print_exc()


def stop():
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)


def start():
    global _scheduler
    db.init_db()
    _safe_collect_bandwidth()

    _scheduler = BackgroundScheduler(daemon=True)
    _scheduler.add_job(_safe_collect_bandwidth,   'interval', seconds=10, id='bw')
    _scheduler.add_job(_safe_collect_connections, 'interval', seconds=60, id='conn')
    _scheduler.add_job(_safe_aggregate,           'interval', hours=1,   id='agg')
    if docker_stats.available():
        _safe_docker()
        _scheduler.add_job(_safe_docker, 'interval', seconds=10, id='docker')
    _scheduler.start()
