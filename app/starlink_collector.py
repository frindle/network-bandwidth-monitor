"""
Reads eth3 (Starlink WAN) byte counters from the Firewalla via SSH.
Samples every 30 seconds; stores rate deltas in starlink_bw_raw.
"""
import subprocess
import threading
import time

import app.database as db

_SSH_KEY  = '/root/.ssh/id_firewalla'
_SSH_OPTS = [
    '-i', _SSH_KEY,
    '-o', 'StrictHostKeyChecking=no',
    '-o', 'UserKnownHostsFile=/dev/null',
    '-o', 'ConnectTimeout=5',
    '-o', 'BatchMode=yes',
]

_prev: dict | None = None
_lock    = threading.Lock()
_running = False


def _setting(key):
    try:
        return db.get_setting(key) or ''
    except Exception:
        return ''


def _fw_ip() -> str:
    return _setting('firewalla_ip')


def _read_eth3() -> tuple[int, int] | None:
    """Returns (rx_bytes, tx_bytes) for eth3 from the Firewalla, or None."""
    ip = _fw_ip()
    if not ip:
        return None
    try:
        result = subprocess.run(
            ['ssh'] + _SSH_OPTS + [f'pi@{ip}', 'grep "eth3:" /proc/net/dev'],
            capture_output=True, text=True, timeout=8
        )
        line = result.stdout.strip()
        if not line:
            return None
        # format: eth3: rx_bytes packets ... | tx_bytes packets ...
        parts = line.split()
        return int(parts[1]), int(parts[9])
    except Exception:
        return None


def _sample():
    global _prev
    now = int(time.time())
    cur = _read_eth3()
    if cur is None:
        return
    rx_bytes, tx_bytes = cur
    with _lock:
        if _prev is not None:
            prev_ts, prev_rx, prev_tx = _prev
            elapsed = now - prev_ts
            if elapsed > 0 and rx_bytes >= prev_rx and tx_bytes >= prev_tx:
                rx_rate = (rx_bytes - prev_rx) / elapsed
                tx_rate = (tx_bytes - prev_tx) / elapsed
                db.insert_starlink_raw(now, rx_rate, tx_rate)
        _prev = (now, rx_bytes, tx_bytes)


def _loop():
    global _running
    while _running:
        _sample()
        time.sleep(30)


def available() -> bool:
    import os
    return os.path.exists(_SSH_KEY) and bool(_fw_ip())


def start():
    global _running
    if _running or not available():
        return
    _running = True
    t = threading.Thread(target=_loop, daemon=True, name='starlink_collector')
    t.start()


def stop():
    global _running
    _running = False
