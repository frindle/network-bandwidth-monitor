import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

import app.database as db

# Cloudflare GraphQL Analytics API — edge-side view of tunnel traffic.
#
# The local conntrack collector (collector.py) already records BYTES forwarded
# by the cloudflared container to each local service. What it cannot see is the
# public-hostname mapping, HTTP request COUNTS, or edge-served/cached traffic
# that never reaches the origin. This module fills that gap by polling the
# zone-scoped httpRequestsAdaptiveGroups dataset once an hour: per public
# hostname, per hour, it returns request count and edgeResponseBytes.
#
# Auth: an API token with "Analytics -> Read" on the zone(s), plus the zone ID.
# No account ID needed for zone-scoped HTTP analytics. Settings keys:
#   cf_api_token  — Bearer token
#   cf_zone_id    — one zone tag, or several comma-separated

_GRAPHQL_URL = 'https://api.cloudflare.com/client/v4/graphql'

_QUERY = """
query($zoneTag:String!,$since:Time!,$until:Time!){
  viewer{
    zones(filter:{zoneTag:$zoneTag}){
      httpRequestsAdaptiveGroups(
        limit:1000,
        filter:{datetime_geq:$since,datetime_leq:$until},
        orderBy:[datetimeHour_ASC]
      ){
        count
        sum{edgeResponseBytes}
        dimensions{clientRequestHTTPHost datetimeHour}
      }
    }
  }
}
"""


def _setting(key: str) -> str:
    try:
        return db.get_setting(key) or ''
    except Exception:
        return ''


def _token() -> str:
    return _setting('cf_api_token')


def _zones() -> list:
    raw = _setting('cf_zone_id')
    return [z.strip() for z in raw.split(',') if z.strip()]


def available() -> bool:
    return bool(_token()) and bool(_zones())


def _graphql(zone_tag: str, since_iso: str, until_iso: str, timeout: int = 15) -> list:
    """Run the query for one zone. Returns the httpRequestsAdaptiveGroups list.
    Raises RuntimeError with a human-readable message on any failure."""
    body = json.dumps({
        'query': _QUERY,
        'variables': {'zoneTag': zone_tag, 'since': since_iso, 'until': until_iso},
    }).encode()
    req = urllib.request.Request(
        _GRAPHQL_URL, data=body,
        headers={
            'Authorization': f'Bearer {_token()}',
            'Content-Type': 'application/json',
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            payload = json.loads(r.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors='replace')[:300]
        raise RuntimeError(f'HTTP {e.code}: {detail}')
    except (urllib.error.URLError, TimeoutError) as e:
        raise RuntimeError(f'Connection failed: {e}')
    except json.JSONDecodeError as e:
        raise RuntimeError(f'Bad JSON from Cloudflare: {e}')

    if payload.get('errors'):
        msg = '; '.join(e.get('message', '?') for e in payload['errors'])
        raise RuntimeError(f'GraphQL error: {msg[:300]}')
    zones = (payload.get('data') or {}).get('viewer', {}).get('zones', [])
    if not zones:
        raise RuntimeError('Zone not found or token lacks access to it')
    return zones[0].get('httpRequestsAdaptiveGroups', []) or []


def _hour_ts(iso: str) -> int:
    # datetimeHour looks like "2026-07-17T14:00:00Z"
    dt = datetime.fromisoformat(iso.replace('Z', '+00:00'))
    return int(dt.replace(tzinfo=timezone.utc).timestamp())


def test_connection() -> tuple[bool, str]:
    if not _token():
        return False, 'API token not configured'
    if not _zones():
        return False, 'Zone ID not configured'
    now = int(time.time())
    since = datetime.fromtimestamp(now - 3600, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    until = datetime.fromtimestamp(now, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    try:
        total_hosts = set()
        for zone in _zones():
            groups = _graphql(zone, since, until)
            for g in groups:
                total_hosts.add(g['dimensions']['clientRequestHTTPHost'])
        return True, f'Connected — {len(_zones())} zone(s), {len(total_hosts)} hostname(s) with traffic in the last hour'
    except Exception as e:
        return False, str(e)


def poll_once() -> int:
    """Fetch the last few hours (to catch the just-closed hour) for every zone
    and upsert per-hostname hourly totals. GraphQL returns authoritative totals
    per hour, so we REPLACE rather than accumulate. Returns rows written."""
    if not available():
        return 0
    now = int(time.time())
    # Look back 3h so a delayed run still fills the previous complete hour.
    since = datetime.fromtimestamp(now - 3 * 3600, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    until = datetime.fromtimestamp(now, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    written = 0
    for zone in _zones():
        try:
            groups = _graphql(zone, since, until)
        except Exception:
            import traceback
            traceback.print_exc()
            continue
        for g in groups:
            host = g['dimensions']['clientRequestHTTPHost'] or '(unknown)'
            hour_ts = _hour_ts(g['dimensions']['datetimeHour'])
            requests = int(g.get('count', 0))
            edge_bytes = int((g.get('sum') or {}).get('edgeResponseBytes', 0))
            db.upsert_cf_edge(hour_ts, zone, host, requests, edge_bytes)
            written += 1
    return written
