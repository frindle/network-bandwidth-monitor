"""
Reads Firewalla WAN interface counters via SSH (eth0=Cox, eth3=Starlink).
Samples every 30 seconds; stores per-interface rate deltas in starlink_bw_raw.
"""
import os
import subprocess
import threading
import time

import app.database as db

_SSH_KEY  = os.environ.get('STARLINK_SSH_KEY', '/root/.ssh/id_firewalla')
_SSH_OPTS = [
    '-i', _SSH_KEY,
    '-o', 'StrictHostKeyChecking=no',
    '-o', 'UserKnownHostsFile=/dev/null',
    '-o', 'ConnectTimeout=5',
    '-o', 'BatchMode=yes',
]

_WAN_IFACES = ('eth0', 'eth3')   # eth0=Cox, eth3=Starlink

_prev: dict          = {}   # iface -> (ts, rx_bytes, tx_bytes)
_current_rates: dict = {}   # iface -> {rx, tx}  (Bps)
_lock    = threading.Lock()
_running = False


def _setting(key):
    try:
        return db.get_setting(key) or ''
    except Exception:
        return ''


def _fw_ip() -> str:
    return _setting('firewalla_ssh_ip') or _setting('firewalla_ip')


_last_error: str = ''

def _read_wan() -> dict:
    """Returns {iface: (rx_bytes, tx_bytes)} for WAN interfaces, via one SSH call."""
    global _last_error
    ip = _fw_ip()
    if not ip:
        _last_error = 'No Firewalla SSH IP configured'
        return {}
    pattern = '|'.join(f'{i}:' for i in _WAN_IFACES)
    last_err = ''
    for attempt in range(3):
        try:
            result = subprocess.run(
                ['ssh'] + _SSH_OPTS + [f'pi@{ip}', f'grep -E "{pattern}" /proc/net/dev'],
                capture_output=True, text=True, timeout=8
            )
            if result.returncode == 0:
                out = {}
                for line in result.stdout.splitlines():
                    line = line.strip()
                    if ':' not in line:
                        continue
                    iface, rest = line.split(':', 1)
                    iface = iface.strip()
                    if iface not in _WAN_IFACES:
                        continue
                    parts = rest.split()
                    if len(parts) < 9:
                        continue
                    out[iface] = (int(parts[0]), int(parts[8]))
                if not out:
                    _last_error = f'No matching interfaces in output: {result.stdout[:200]}'
                else:
                    _last_error = ''
                return out
            else:
                last_err = f'SSH exit {result.returncode}: {result.stderr.strip()[:200]}'
        except Exception as e:
            last_err = str(e)
        if attempt < 2:
            time.sleep(1 * (attempt + 1))
    _last_error = last_err
    return {}


def last_error() -> str:
    return _last_error


def _sample():
    now  = int(time.time())
    data = _read_wan()
    if not data:
        return

    with _lock:
        for iface, (rx_bytes, tx_bytes) in data.items():
            if iface in _prev:
                prev_ts, prev_rx, prev_tx = _prev[iface]
                elapsed = now - prev_ts
                if elapsed > 0 and rx_bytes >= prev_rx and tx_bytes >= prev_tx:
                    rx_rate = (rx_bytes - prev_rx) / elapsed
                    tx_rate = (tx_bytes - prev_tx) / elapsed
                    _current_rates[iface] = {'rx': rx_rate, 'tx': tx_rate}
                    db.insert_starlink_raw(now, iface, rx_rate, tx_rate)
            _prev[iface] = (now, rx_bytes, tx_bytes)


def _loop():
    global _running
    while _running:
        _sample()
        time.sleep(30)


def available() -> bool:
    import os
    return os.path.exists(_SSH_KEY) and bool(_fw_ip())


def current_rates() -> dict:
    """Returns {iface: {rx, tx}} in Bps for all tracked WAN interfaces."""
    with _lock:
        return dict(_current_rates)


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
