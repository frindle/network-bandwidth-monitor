import http.client
import json
import os
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import app.database as db

DOCKER_SOCK = os.environ.get('DOCKER_SOCK', '/var/run/docker.sock')

_lock = threading.Lock()
_last_bytes   = {}   # container_id -> (rx, tx, ts)
_current_rates = {}  # container_id -> {name, rx, tx}


class _UnixConn(http.client.HTTPConnection):
    def __init__(self):
        super().__init__('localhost')

    def connect(self):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(5)
        s.connect(DOCKER_SOCK)
        self.sock = s


def _get(path: str):
    conn = _UnixConn()
    try:
        conn.request('GET', path)
        resp = conn.getresponse()
        return json.loads(resp.read())
    finally:
        conn.close()


def _list_containers() -> list:
    try:
        return [
            {'id': c['Id'][:12], 'name': c['Names'][0].lstrip('/')}
            for c in _get('/containers/json')
        ]
    except Exception:
        return []


def _fetch_stats(container: dict):
    try:
        # one-shot=true returns immediately without waiting for CPU tick
        stats = _get(f'/containers/{container["id"]}/stats?stream=false&one-shot=true')
        nets  = stats.get('networks', {})
        rx    = sum(n.get('rx_bytes', 0) for n in nets.values())
        tx    = sum(n.get('tx_bytes', 0) for n in nets.values())
        return container, rx, tx
    except Exception:
        return container, None, None


def collect_docker_stats():
    now        = int(time.time())
    containers = _list_containers()
    if not containers:
        return

    results = []
    workers = min(10, len(containers))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_fetch_stats, c): c for c in containers}
        for f in as_completed(futures):
            results.append(f.result())

    samples = []
    seen    = set()

    with _lock:
        for container, rx, tx in results:
            if rx is None:
                continue
            cid  = container['id']
            name = container['name']
            seen.add(cid)

            if cid in _last_bytes:
                last_rx, last_tx, last_ts = _last_bytes[cid]
                dt = now - last_ts
                if dt > 0:
                    rx_rate = max(0, rx - last_rx) / dt
                    tx_rate = max(0, tx - last_tx) / dt
                    samples.append((now, cid, name, rx_rate, tx_rate))
                    _current_rates[cid] = {'name': name, 'rx': rx_rate, 'tx': tx_rate}

            _last_bytes[cid] = (rx, tx, now)

        for cid in list(_last_bytes):
            if cid not in seen:
                del _last_bytes[cid]
                _current_rates.pop(cid, None)

    if samples:
        db.insert_container_bw_raw(samples)


def get_container_ips(name: str) -> list:
    """Return all IPv4 addresses assigned to a container (by name or id)."""
    try:
        info  = _get(f'/containers/{name}/json')
        nets  = info.get('NetworkSettings', {}).get('Networks', {})
        return [n['IPAddress'] for n in nets.values() if n.get('IPAddress')]
    except Exception:
        return []


def available() -> bool:
    return os.path.exists(DOCKER_SOCK)


def list_running() -> dict:
    """Returns {container_id: name} for all containers currently running in Docker."""
    containers = _list_containers()
    return {c['id']: c['name'] for c in containers}


def current_rates() -> dict:
    with _lock:
        return dict(_current_rates)
