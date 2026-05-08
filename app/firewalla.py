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
    return bool(_ip())


def _get(path: str):
    conn = http.client.HTTPConnection(_ip(), 8834, timeout=8)
    headers = {'Accept': 'application/json'}
    tok = _token()
    if tok:
        headers['Authorization'] = f'Token {tok}'
    conn.request('GET', path, headers=headers)
    resp = conn.getresponse()
    body = resp.read()
    conn.close()
    if resp.status != 200:
        raise RuntimeError(f'HTTP {resp.status}: {body[:200]}')
    return json.loads(body)


def test_connection() -> tuple[bool, str]:
    if not _ip():
        return False, 'Firewalla IP not configured'
    try:
        data = _get('/v1/host/all')
        hosts = data.get('hosts', []) if isinstance(data, dict) else data
        return True, f'Connected — {len(hosts)} devices visible'
    except Exception as e:
        return False, str(e)


def get_devices() -> list:
    """Returns list of host dicts: {ip, mac, name, macVendor, lastActive, ...}"""
    try:
        data = _get('/v1/host/all')
        if isinstance(data, dict):
            return data.get('hosts', [])
        return data or []
    except Exception:
        return []


def get_flows(begin: int, end: int, count: int = 500) -> list:
    """Returns list of flow dicts. begin/end are Unix timestamps."""
    try:
        data = _get(f'/v1/flow?begin={begin}&end={end}&count={count}')
        if isinstance(data, dict):
            return data.get('flows', data.get('result', []))
        return data or []
    except Exception:
        return []


def get_stats(begin: int, end: int) -> dict:
    try:
        return _get(f'/v1/stats?begin={begin}&end={end}') or {}
    except Exception:
        return {}
