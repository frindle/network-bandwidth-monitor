import http.client
import json
import os

# Settings are read from DB at call time so UI changes take effect immediately.
# Env vars are fallbacks for headless/automated setups.


def _setting(key: str) -> str:
    try:
        import app.database as db
        return db.get_setting(key) or os.environ.get(key.upper(), '')
    except Exception:
        return os.environ.get(key.upper(), '')


def _ip()    -> str: return _setting('firewalla_ip')
def _token() -> str: return _setting('firewalla_token')


def available() -> bool:
    return bool(_ip() and _token())


def _get(path: str):
    conn = http.client.HTTPConnection(_ip(), 8833, timeout=8)
    conn.request('GET', path, headers={
        'Authorization': f'Token {_token()}',
        'Accept': 'application/json',
    })
    resp = conn.getresponse()
    body = resp.read()
    conn.close()
    if resp.status != 200:
        raise RuntimeError(f'HTTP {resp.status}: {body[:200]}')
    return json.loads(body)


def test_connection() -> tuple[bool, str]:
    if not _ip():
        return False, 'Firewalla IP not configured'
    if not _token():
        return False, 'Firewalla token not configured'
    try:
        data = _get('/v1/box')
        name = data.get('name') or data.get('boxName') or 'Firewalla'
        return True, f'Connected to {name}'
    except Exception as e:
        return False, str(e)


def get_devices() -> list:
    try:
        return _get('/v1/device') or []
    except Exception:
        return []


def get_flows(begin: int, end: int, count: int = 500) -> list:
    try:
        return _get(f'/v1/flow?begin={begin}&end={end}&count={count}') or []
    except Exception:
        return []


def get_stats(begin: int, end: int) -> dict:
    try:
        return _get(f'/v1/stats?begin={begin}&end={end}') or {}
    except Exception:
        return {}
