import os
import re
import socket
import struct
import threading
import time

from apscheduler.schedulers.background import BackgroundScheduler

import app.database as db

NET_BASE     = os.environ.get('NET_BASE', '/host/net')
NET_DEV      = f'{NET_BASE}/dev'
NET_CONNTRACK = f'{NET_BASE}/nf_conntrack'
NET_ROUTE    = f'{NET_BASE}/route'

_lock              = threading.Lock()
_last_iface_bytes  = {}   # iface -> (rx, tx, ts)
_last_conn_bytes   = {}   # (proto, src, dst, dport) -> (tx, rx)
_current_rates     = {}   # iface -> (rx_rate, tx_rate)

_scheduler = None


# ── bandwidth ─────────────────────────────────────────────────────────────────

def _read_iface_bytes() -> dict:
    result = {}
    try:
        with open(NET_DEV) as f:
            for line in f.readlines()[2:]:
                parts = line.split()
                iface = parts[0].rstrip(':')
                if iface == 'lo':
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
                    _current_rates[iface] = (rx_rate, tx_rate)
            _last_iface_bytes[iface] = (rx, tx, now)

    if samples:
        db.insert_bw_raw(samples)


# ── routing / interface lookup ─────────────────────────────────────────────────

def _read_routes() -> list:
    """Return list of (iface, dest_int, mask_int) from /proc/net/route."""
    routes = []
    try:
        with open(NET_ROUTE) as f:
            next(f)
            for line in f:
                parts = line.split()
                if len(parts) < 8:
                    continue
                # values are little-endian hex
                routes.append((parts[0], int(parts[1], 16), int(parts[7], 16)))
    except OSError:
        pass
    return routes


def _iface_for_ip(ip: str, routes: list) -> str:
    try:
        ip_int = struct.unpack('>I', socket.inet_aton(ip))[0]
    except OSError:
        return 'unknown'

    best_iface  = 'unknown'
    best_prefix = -1
    for iface, dest_le, mask_le in routes:
        # /proc/net/route stores values little-endian; convert to host order
        dest_n = struct.unpack('>I', struct.pack('<I', dest_le))[0]
        mask_n = struct.unpack('>I', struct.pack('<I', mask_le))[0]
        if (ip_int & mask_n) == (dest_n & mask_n):
            prefix_len = bin(mask_n).count('1')
            if prefix_len > best_prefix:
                best_prefix = prefix_len
                best_iface  = iface
    return best_iface


# ── conntrack ─────────────────────────────────────────────────────────────────

# Matches two consecutive src/dst/sport/dport/bytes blocks in a conntrack line.
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
    family = parts[0]
    proto  = parts[2]
    if family not in ('ipv4', 'ipv6') or proto not in ('tcp', 'udp'):
        return None

    m = _CT_RE.search(line)
    if not m:
        return None

    src, dst, _sport, dport, orig_bytes, reply_bytes = m.groups()
    return {
        'proto':       proto,
        'local_ip':    src,
        'remote_ip':   dst,
        'remote_port': int(dport),
        'tx_bytes':    int(orig_bytes),
        'rx_bytes':    int(reply_bytes),
        'key':         (proto, src, dst, int(dport)),
    }


def collect_connections():
    now      = int(time.time())
    hour_ts  = (now // 3600) * 3600
    routes   = _read_routes()

    try:
        with open(NET_CONNTRACK) as f:
            lines = f.readlines()
    except OSError:
        return

    new_state = {}
    deltas    = []

    with _lock:
        for line in lines:
            entry = _parse_ct_line(line)
            if not entry:
                continue

            key    = entry['key']
            new_tx = entry['tx_bytes']
            new_rx = entry['rx_bytes']
            new_state[key] = (new_tx, new_rx)

            if key in _last_conn_bytes:
                last_tx, last_rx = _last_conn_bytes[key]
                tx_delta = max(0, new_tx - last_tx)
                rx_delta = max(0, new_rx - last_rx)
                if tx_delta > 0 or rx_delta > 0:
                    iface = _iface_for_ip(entry['remote_ip'], routes)
                    deltas.append((
                        hour_ts, iface, entry['proto'],
                        entry['remote_ip'], entry['remote_port'],
                        tx_delta, rx_delta,
                    ))

        _last_conn_bytes.clear()
        _last_conn_bytes.update(new_state)

    for args in deltas:
        db.upsert_conn_delta(*args)


# ── public ─────────────────────────────────────────────────────────────────────

def current_rates() -> dict:
    with _lock:
        return {iface: {'rx': rx, 'tx': tx} for iface, (rx, tx) in _current_rates.items()}


def start():
    global _scheduler
    db.init_db()
    collect_bandwidth()   # seed _last_iface_bytes so first real read has a baseline

    _scheduler = BackgroundScheduler(daemon=True)
    _scheduler.add_job(collect_bandwidth,   'interval', seconds=10, id='bw')
    _scheduler.add_job(collect_connections, 'interval', seconds=60, id='conn')
    _scheduler.add_job(db.aggregate_hourly, 'interval', hours=1,   id='agg')
    _scheduler.start()
