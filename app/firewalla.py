import json
import os
import subprocess
import time

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


def _log_poll(success: bool, latency_ms: int, error_type: str = '', error: str = ''):
    # Never let poll-health logging break the actual poll.
    try:
        import app.database as db
        db.insert_fw_poll(int(time.time()), success, latency_ms, error_type, error[:300])
    except Exception:
        pass


def _classify_ssh_error(returncode: int, stderr: str) -> str:
    s = stderr.lower()
    if 'connection refused' in s:
        return 'connection_refused'
    if 'timed out' in s or 'timeout' in s:
        return 'timeout'
    if 'permission denied' in s or 'authentication' in s:
        return 'auth_failed'
    if 'no route to host' in s or 'network is unreachable' in s:
        return 'unreachable'
    if 'could not resolve' in s or 'name or service not known' in s:
        return 'dns_error'
    return f'ssh_error_{returncode}'


def _ssh_curl(path: str, timeout: int = 10) -> dict | list:
    ip = _ip()
    if not ip:
        raise RuntimeError('Firewalla IP not configured')
    start = time.monotonic()
    try:
        result = subprocess.run(
            ['ssh'] + _SSH_OPTS + [f'pi@{ip}', f'curl -s http://127.0.0.1:8834{path}'],
            capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        latency_ms = int((time.monotonic() - start) * 1000)
        _log_poll(False, latency_ms, 'timeout', f'SSH timed out after {timeout}s')
        raise RuntimeError(f'SSH timeout after {timeout}s')
    latency_ms = int((time.monotonic() - start) * 1000)
    if result.returncode != 0:
        stderr    = result.stderr.strip()[:300]
        err_type  = _classify_ssh_error(result.returncode, stderr)
        _log_poll(False, latency_ms, err_type, stderr)
        raise RuntimeError(f'SSH exit {result.returncode}: {stderr[:200]}')
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        _log_poll(False, latency_ms, 'bad_response', f'JSON decode failed: {e}')
        raise RuntimeError(f'Bad response from Firewalla: {e}')
    _log_poll(True, latency_ms)
    return data


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


# ── quarantine / DAP policy ──────────────────────────────────────────────────
# Firewalla's local API has no endpoint for this — it lives only in the
# box's own redis. Confirmed by hand (2026-07-18): a device's "quarantine"
# boolean is a red herring (stays false); the thing that actually isolates a
# new device is policy:mac:<MAC>.dap.localAclState == "learning". Approving a
# device in the Firewalla app doesn't touch that dap blob at all — it just
# reassigns the device's "tags" field to a real group ID. So automation here
# means writing "tags", not trying to reconstruct the dap JSON.

def _ssh_run(remote_cmd: str, timeout: int = 15) -> str:
    ip = _ip()
    if not ip:
        raise RuntimeError('Firewalla IP not configured')
    result = subprocess.run(
        ['ssh'] + _SSH_OPTS + [f'pi@{ip}', remote_cmd],
        capture_output=True, text=True, timeout=timeout
    )
    if result.returncode != 0:
        raise RuntimeError(f'SSH exit {result.returncode}: {result.stderr.strip()[:300]}')
    return result.stdout


_QUARANTINE_SCAN = (
    "for k in $(redis-cli --scan --pattern 'policy:mac:*'); do "
    "mac=${k#policy:mac:}; "
    "tags=$(redis-cli hget \"$k\" tags); "
    "acl=$(redis-cli hget \"$k\" dap | grep -o '\"localAclState\":\"[a-z]*\"' | head -1 | cut -d'\"' -f4); "
    "echo \"${mac}|${tags}|${acl}\"; "
    "done"
)


def list_quarantined_devices() -> list[dict]:
    """Devices still in Firewalla's default DAP 'learning' (isolated) state."""
    try:
        out = _ssh_run(_QUARANTINE_SCAN)
    except Exception:
        return []
    result = []
    for line in out.splitlines():
        parts = line.split('|', 2)
        if len(parts) != 3:
            continue
        mac, tags_raw, acl = parts
        if acl != 'learning':
            continue
        try:
            tags = json.loads(tags_raw) if tags_raw else []
        except json.JSONDecodeError:
            tags = []
        result.append({'mac': mac, 'tags': tags, 'acl_state': acl})
    return result


_TAG_SCAN = (
    "for k in $(redis-cli --scan --pattern 'tag:uid:*'); do "
    "id=${k#tag:uid:}; "
    "name=$(redis-cli hget \"$k\" name); "
    "echo \"${id}|${name}\"; "
    "done"
)


def _redis_unescape(s: str) -> str:
    # redis-cli quotes + \xNN-escapes any reply containing non-ASCII bytes
    # (e.g. a curly apostrophe in "Penn's Devices" comes back as \xe2\x80\x99).
    s = s.strip()
    if s.startswith('"') and s.endswith('"'):
        s = s[1:-1]
    out = bytearray()
    i = 0
    while i < len(s):
        if s[i] == '\\' and s[i:i + 2] == '\\x' and i + 4 <= len(s):
            try:
                out.append(int(s[i + 2:i + 4], 16))
                i += 4
                continue
            except ValueError:
                pass
        out.extend(s[i].encode('utf-8'))
        i += 1
    try:
        return out.decode('utf-8')
    except UnicodeDecodeError:
        return s


def list_tags() -> list[dict]:
    """All Firewalla device groups (tags), for the approve-device group picker."""
    try:
        out = _ssh_run(_TAG_SCAN)
    except Exception:
        return []
    result = []
    for line in out.splitlines():
        if '|' not in line:
            continue
        tid, name = line.split('|', 1)
        name = _redis_unescape(name)
        if name.strip().lower() == 'quarantine':
            continue  # never offer the quarantine group itself as a target
        result.append({'id': tid.strip(), 'name': name})
    return result


def approve_device(mac: str, tag_id: str) -> tuple[bool, str]:
    """Move a device out of the default 'new device' group by reassigning
    its tag — this is what the Firewalla app's own approve action does,
    confirmed by diffing policy:mac:<MAC> before/after in-app."""
    mac = mac.strip().upper()
    if not mac:
        return False, 'MAC required'
    tag_id = (tag_id or '').strip()
    if not tag_id:
        return False, 'No trusted tag ID configured (Settings)'
    tags_json = json.dumps([tag_id])
    try:
        _ssh_run(f"redis-cli hset policy:mac:{mac} tags '{tags_json}'")
        return True, f'{mac} moved to tag {tag_id}'
    except Exception as e:
        return False, str(e)
