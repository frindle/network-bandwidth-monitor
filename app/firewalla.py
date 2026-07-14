import json
import os
import subprocess

# SSH into Firewalla and curl the local API (app-local.js on 127.0.0.1:8834).
# Each call is a fresh SSH connection — no persistent tunnel to maintain.

_SSH_KEY  = os.environ.get('FIREWALLA_SSH_KEY',
                           os.environ.get('STARLINK_SSH_KEY', '/root/.ssh/id_firewalla'))
_SSH_OPTS = [
    '-i', _SSH_KEY,
    '-o', 'StrictHostKeyChecking=no',
    '-o', 'UserKnownHostsFile=/dev/null',
    '-o', 'ConnectTimeout=5',
    '-o', 'BatchMode=yes',
]


def _setting(key: str) -> str:
    try:
        import app.database as db
        return db.get_setting(key) or os.environ.get(key.upper(), '')
    except Exception:
        return os.environ.get(key.upper(), '')


def _ip() -> str:
    return _setting('firewalla_ssh_ip') or _setting('firewalla_ip')


def available() -> bool:
    return bool(_ip()) and os.path.exists(_SSH_KEY)


def _ssh_curl(path: str, timeout: int = 10) -> dict | list:
    ip = _ip()
    if not ip:
        raise RuntimeError('Firewalla IP not configured')
    result = subprocess.run(
        ['ssh'] + _SSH_OPTS + [f'pi@{ip}', f'curl -s http://127.0.0.1:8834{path}'],
        capture_output=True, text=True, timeout=timeout
    )
    if result.returncode != 0:
        raise RuntimeError(f'SSH exit {result.returncode}: {result.stderr.strip()[:200]}')
    return json.loads(result.stdout)


def test_connection() -> tuple[bool, str]:
    if not _ip():
        return False, 'Firewalla IP not configured'
    if not os.path.exists(_SSH_KEY):
        return False, f'SSH key not found: {_SSH_KEY}'
    try:
        data = _ssh_curl('/v1/host/all')
        hosts = data.get('hosts', []) if isinstance(data, dict) else data
        return True, f'Connected via SSH — {len(hosts)} devices visible'
    except Exception as e:
        return False, str(e)


def get_devices() -> list:
    try:
        data = _ssh_curl('/v1/host/all')
        if isinstance(data, dict):
            return data.get('hosts', [])
        return data or []
    except Exception:
        return []


def get_flows(begin: int, end: int, count: int = 500) -> list:
    try:
        data = _ssh_curl(f'/v1/flow?begin={begin}&end={end}&count={count}', timeout=15)
        if isinstance(data, dict):
            return data.get('flows', data.get('result', []))
        return data or []
    except Exception:
        return []


def get_stats(begin: int, end: int) -> dict:
    try:
        return _ssh_curl(f'/v1/stats?begin={begin}&end={end}') or {}
    except Exception:
        return {}
