import atexit
import os
import signal
import time

from flask import Flask, jsonify, render_template, request

import app.cloudflare as cloudflare
import app.collector as collector
import app.database as db
import app.fw_collector as fw_collector
import app.fw_flows_collector as fw_flows_collector
import app.firewalla as firewalla
import app.resolver as resolver
import app.starlink_collector as starlink_collector

VERSION = '0.13.0'

app = Flask(__name__)

_RANGES = {
    '1h':  3600,        '6h':  21600,
    '24h': 86400,       '7d':  7  * 86400,
    '30d': 30 * 86400,  'all': 365 * 86400,   # capped at 1 year for hourly data
}
_PERIOD_SECONDS = {
    '1d': 86400, '7d': 7*86400, '14d': 14*86400, '30d': 30*86400,
}


_SERVICE_PATTERNS = [
    # (domain_fragment, service_name)
    ('googlevideo.com',       'YouTube'),
    ('youtube.com',           'YouTube'),
    ('ytimg.com',             'YouTube'),
    ('ggpht.com',             'YouTube'),
    ('netflix.com',           'Netflix'),
    ('nflxvideo.net',         'Netflix'),
    ('nflximg.net',           'Netflix'),
    ('nflxext.com',           'Netflix'),
    ('spotify.com',           'Spotify'),
    ('scdn.co',               'Spotify'),
    ('icloud.com',            'Apple iCloud'),
    ('apple.com',             'Apple'),
    ('mzstatic.com',          'Apple'),
    ('aaplimg.com',           'Apple'),
    ('amazonaws.com',         'AWS'),
    ('cloudfront.net',        'AWS CloudFront'),
    ('microsoft.com',         'Microsoft'),
    ('windows.com',           'Microsoft'),
    ('microsoftonline.com',   'Microsoft'),
    ('live.com',              'Microsoft'),
    ('office.com',            'Microsoft 365'),
    ('office365.com',         'Microsoft 365'),
    ('twitch.tv',             'Twitch'),
    ('twitchsvc.net',         'Twitch'),
    ('jtvnw.net',             'Twitch'),
    ('hulu.com',              'Hulu'),
    ('disneyplus.com',        'Disney+'),
    ('bamgrid.com',           'Disney+'),
    ('dssott.com',            'Disney+'),
    ('facebook.com',          'Facebook'),
    ('fbcdn.net',             'Facebook'),
    ('instagram.com',         'Instagram'),
    ('twitter.com',           'Twitter/X'),
    ('twimg.com',             'Twitter/X'),
    ('x.com',                 'Twitter/X'),
    ('discord.com',           'Discord'),
    ('discordapp.com',        'Discord'),
    ('plex.tv',               'Plex'),
    ('plex.direct',           'Plex'),
    ('plexapp.com',           'Plex'),
    ('steampowered.com',      'Steam'),
    ('steamcontent.com',      'Steam'),
    ('steamgames.com',        'Steam'),
    ('valve.net',             'Steam'),
    ('akamai.net',            'Akamai CDN'),
    ('akamaized.net',         'Akamai CDN'),
    ('akamaitechnologies.com','Akamai CDN'),
    ('akamaihd.net',          'Akamai CDN'),
    ('fastly.net',            'Fastly CDN'),
    ('fastlylb.net',          'Fastly CDN'),
    ('cloudflare.com',        'Cloudflare'),
    ('cloudflare-dns.com',    'Cloudflare'),
    ('1dot1dot1dot1.cloudflare.com', 'Cloudflare DNS'),
    ('googleapis.com',        'Google'),
    ('gstatic.com',           'Google'),
    ('google.com',            'Google'),
    ('googleusercontent.com', 'Google'),
    ('gvt1.com',              'Google'),
    ('gvt2.com',              'Google'),
    ('reddit.com',            'Reddit'),
    ('redd.it',               'Reddit'),
    ('redditmedia.com',       'Reddit'),
    ('redditstatic.com',      'Reddit'),
    ('github.com',            'GitHub'),
    ('githubusercontent.com', 'GitHub'),
    ('github.io',             'GitHub'),
    ('dropbox.com',           'Dropbox'),
    ('dropboxstatic.com',     'Dropbox'),
    ('zoom.us',               'Zoom'),
    ('zoomgov.com',           'Zoom'),
    ('slack.com',             'Slack'),
    ('slack-msgs.com',        'Slack'),
    ('slackb.com',            'Slack'),
    ('sony.com',              'Sony'),
    ('playstation.com',       'PlayStation'),
    ('playstation.net',       'PlayStation'),
    ('nintendo.com',          'Nintendo'),
    ('nintendo.net',          'Nintendo'),
    ('xbox.com',              'Xbox'),
    ('xboxlive.com',          'Xbox Live'),
    ('amazon.com',            'Amazon'),
    ('primevideo.com',        'Prime Video'),
    ('sling.com',             'Sling TV'),
    ('hbo.com',               'HBO/Max'),
    ('hbomax.com',            'HBO/Max'),
    ('max.com',               'HBO/Max'),
    ('phicdn.net',            'Paramount+'),
    ('paramount.com',         'Paramount+'),
    ('peacocktv.com',         'Peacock'),
    ('nbcuni.com',            'Peacock'),
    ('vudu.com',              'Vudu'),
    ('tubi.tv',               'Tubi'),
    ('ebay.com',              'eBay'),
    ('paypal.com',            'PayPal'),
    ('cloudimage.io',         'CDN'),
    ('cloudinary.com',        'Cloudinary'),
    ('imgix.net',             'Imgix CDN'),
    ('cdn77.org',             'CDN77'),
    ('b-cdn.net',             'BunnyCDN'),
]

_CDN_FRAGMENTS = ('akamai', 'cloudfront', 'fastly', 'edgekey', 'edgesuite',
                   'akamaitechnologies', 'akamaihd', 'akamaized',
                   'llnwd', 'llnwd.net', 'cdn77', 'b-cdn.net')

# Services that route through CDNs — match on the subdomain prefix before .cdn-domain.com
_CDN_SERVICE_HINTS = [
    ('netflix',    'Netflix'),
    ('nflx',       'Netflix'),
    ('hulu',       'Hulu'),
    ('twitch',     'Twitch'),
    ('twitchsvc',  'Twitch'),
    ('jtvnw',      'Twitch'),
    ('disney',     'Disney+'),
    ('bamgrid',    'Disney+'),
    ('hbo',        'HBO/Max'),
    ('spotify',    'Spotify'),
    ('scdn',       'Spotify'),
    ('apple',      'Apple'),
    ('icloud',     'Apple iCloud'),
    ('steam',      'Steam'),
    ('valve',      'Steam'),
    ('youtube',    'YouTube'),
    ('googlevideo','YouTube'),
    ('ytimg',      'YouTube'),
    ('xbox',       'Xbox'),
    ('xboxlive',   'Xbox Live'),
    ('playstation','PlayStation'),
    ('sonyentertainment', 'PlayStation'),
    ('paramount',  'Paramount+'),
    ('peacock',    'Peacock'),
    ('slack',      'Slack'),
    ('zoom',       'Zoom'),
    ('discord',    'Discord'),
    ('github',     'GitHub'),
    ('dropbox',    'Dropbox'),
    ('plex',       'Plex'),
    ('reddit',     'Reddit'),
    ('fbcdn',      'Facebook'),
    ('instagram',  'Instagram'),
    ('twitter',    'Twitter/X'),
    ('microsoft',  'Microsoft'),
    ('office',     'Microsoft 365'),
]

def _detect_service(hostname: str) -> str | None:
    if not hostname:
        return None
    h = hostname.lower()
    # Direct domain match first
    for frag, name in _SERVICE_PATTERNS:
        if frag in h:
            return name
    # For CDN hostnames, look for service hints in the subdomain parts
    is_cdn = any(cdn in h for cdn in _CDN_FRAGMENTS)
    if is_cdn:
        for hint, name in _CDN_SERVICE_HINTS:
            if hint in h:
                return f'{name} (via CDN)'
    return None


def _since(r: str) -> int:
    return int(time.time()) - _RANGES.get(r, 86400)

def _since_hour(r: str) -> int:
    return (_since(r) // 3600) * 3600


# ── dashboard ──────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('index.html')


# ── interfaces ─────────────────────────────────────────────────────────────────

@app.route('/api/interfaces')
def interfaces():
    rates = collector.current_rates()
    known = db.known_interfaces()
    seen  = set()
    result = []
    for iface in list(rates) + known:
        if iface in seen: continue
        seen.add(iface)
        if collector.skip_iface(iface): continue
        r = rates.get(iface, {'rx': 0, 'tx': 0})
        result.append({'name': iface,
                        'rx_mbps': round(r['rx']*8/1e6, 3),
                        'tx_mbps': round(r['tx']*8/1e6, 3)})
    result.sort(key=lambda x: x['name'])
    return jsonify(result)


@app.route('/api/bandwidth')
def bandwidth():
    iface     = request.args.get('iface', 'bond0')
    range_str = request.args.get('range', '24h')
    use_raw   = range_str in ('1h', '6h', '24h')
    if use_raw:
        rows = db.query_bw_raw(iface, _since(range_str))
        data = [{'ts': r['ts'],
                 'rx': round(r['rx_rate']*8/1e6, 3),
                 'tx': round(r['tx_rate']*8/1e6, 3)} for r in rows]
    else:
        rows = db.query_bw_hourly(iface, _since(range_str))
        data = [{'ts': r['hour_ts'],
                 'rx': round(r['rx_bytes']*8/3600/1e6, 3),
                 'tx': round(r['tx_bytes']*8/3600/1e6, 3)} for r in rows]
    return jsonify(data)


@app.route('/api/totals')
def totals():
    result  = {}
    for label, seconds in _PERIOD_SECONDS.items():
        sh = ((int(time.time()) - seconds) // 3600) * 3600
        rows = [dict(r) for r in db.query_totals_by_iface(sh)]
        rows += db.query_totals_by_fw_wan(sh)   # add Cox WAN / Starlink WAN rows
        result[label] = rows
    return jsonify(result)


# ── connections ────────────────────────────────────────────────────────────────

@app.route('/api/connections')
def connections():
    iface     = request.args.get('iface', 'all')
    source_ip = request.args.get('source') or None
    range_str = request.args.get('range', '24h')
    since     = _since_hour(range_str)
    labels    = db.get_all_labels()
    result    = []

    # Use Firewalla flow data (all LAN devices) when available; fall back to conntrack
    use_fw = firewalla.available() and db.has_fw_connections(since)
    if use_fw:
        # Build device name map: IP -> friendly name (label > fw_name > IP)
        fw_devices  = {d['ip']: d for d in db.get_all_fw_devices() if d['ip']}
        device_name = lambda ip: (labels.get(ip)
                                  or (fw_devices[ip]['name'] if ip in fw_devices else None)
                                  or ip)
        rows = db.query_fw_connections(since, source_ip=source_ip)
        for r in rows:
            ip       = r['remote_ip']
            hostname = r['domain'] or db.get_dns(ip) or ip
            svc      = _detect_service(r['domain'] or hostname)
            # Build source device list for refinement tooltip
            src_ips  = (r['source_ips'] or '').split(',') if r['source_ips'] else []
            src_names = [device_name(s) for s in src_ips if s]
            result.append({
                'remote_ip':     ip,
                'hostname':      hostname,
                'label':         labels.get(ip),
                'service':       svc,
                'remote_port':   r['remote_port'],
                'protocol':      r['protocol'],
                'tx_bytes':      r['tx_bytes'],
                'rx_bytes':      r['rx_bytes'],
                'total_bytes':   r['total_bytes'],
                'hits':          r['hit_count'],
                'is_cloudflare': cloudflare.is_cloudflare(ip),
                'source_count':  r['source_count'],
                'source_names':  src_names,
                'data_source':   'fw',
            })
    else:
        rows = db.query_connections(iface, since, source_ip=source_ip)
        fw_devices  = {d['ip']: d for d in db.get_all_fw_devices() if d['ip']}
        for r in rows:
            hostname  = db.get_dns(r['remote_ip']) or r['remote_ip']
            src       = r.get('source_ip', '')
            src_names = ([labels.get(src) or (fw_devices[src]['name'] if src in fw_devices else src)]
                         if src else [])
            result.append({
                'remote_ip':     r['remote_ip'],
                'hostname':      hostname,
                'label':         labels.get(r['remote_ip']),
                'service':       _detect_service(hostname),
                'remote_port':   r['remote_port'],
                'protocol':      r['protocol'],
                'tx_bytes':      r['tx_bytes'],
                'rx_bytes':      r['rx_bytes'],
                'total_bytes':   r['total_bytes'],
                'hits':          r['hit_count'],
                'is_cloudflare': cloudflare.is_cloudflare(r['remote_ip']),
                'source_count':  1,
                'source_names':  src_names,
                'data_source':   'conntrack',
            })

    ips   = [r['remote_ip'] for r in rows[:50]]
    stale = db.stale_ips(ips)
    if stale:
        resolver.resolve_batch_async(stale)
    return jsonify({'data': result, 'source': 'fw' if use_fw else 'conntrack'})


# ── devices ────────────────────────────────────────────────────────────────────

@app.route('/api/devices')
def devices():
    range_str = request.args.get('range', '24h')
    since      = _since_hour(range_str)
    rows       = db.query_devices(since)
    labels     = db.get_all_labels()
    fw_all     = {d['ip']: d for d in db.get_all_fw_devices() if d['ip']}
    result     = []
    seen_ips   = set()
    fw_used    = set()

    # Primary: devices from conn_hourly (conntrack)
    for r in rows:
        ip      = r['source_ip']
        if not collector.is_local(ip):
            continue
        seen_ips.add(ip)
        fw_info = fw_all.get(ip)
        result.append({
            'ip':          ip,
            'hostname':    db.get_dns(ip) or ip,
            'label':       labels.get(ip),
            'fw_name':     fw_info['name']       if fw_info else None,
            'fw_mac':      fw_info['mac']        if fw_info else None,
            'fw_vendor':   fw_info['mac_vendor'] if fw_info else None,
            'fw_group':    fw_info['group_name'] if fw_info else None,
            'fw_rx_bytes': fw_info['fw_rx_bytes'] if fw_info else 0,
            'fw_tx_bytes': fw_info['fw_tx_bytes'] if fw_info else 0,
            'tx_bytes':    r['tx_bytes'],
            'rx_bytes':    r['rx_bytes'],
            'total_bytes': r['total_bytes'],
            'hits':        r['hit_count'],
            'last_seen':   r['last_seen'],
        })

    # Secondary: pull device traffic from fw_conn_hourly when conn_hourly is empty
    if not rows and firewalla.available() and db.has_fw_connections(since):
        fw_rows = db.query_fw_connections(since, limit=2000)
        device_traffic = {}
        for r in fw_rows:
            for src in (r['source_ips'] or '').split(','):
                src = src.strip()
                if not src or not collector.is_local(src):
                    continue
                if src not in device_traffic:
                    device_traffic[src] = {'tx': 0, 'rx': 0, 'hits': 0}
                device_traffic[src]['tx']   += r['tx_bytes'] or 0
                device_traffic[src]['rx']   += r['rx_bytes'] or 0
                device_traffic[src]['hits'] += r['hit_count'] or 0

        for ip, d in device_traffic.items():
            if ip in seen_ips:
                continue
            seen_ips.add(ip)
            fw_used.add(ip)
            fw_info = fw_all.get(ip)
            result.append({
                'ip':          ip,
                'hostname':    db.get_dns(ip) or ip,
                'label':       labels.get(ip),
                'fw_name':     fw_info['name']       if fw_info else None,
                'fw_mac':      fw_info['mac']        if fw_info else None,
                'fw_vendor':   fw_info['mac_vendor'] if fw_info else None,
                'fw_group':    fw_info['group_name'] if fw_info else None,
                'fw_rx_bytes': fw_info['fw_rx_bytes'] if fw_info else 0,
                'fw_tx_bytes': fw_info['fw_tx_bytes'] if fw_info else 0,
                'tx_bytes':    d['tx'],
                'rx_bytes':    d['rx'],
                'total_bytes': d['tx'] + d['rx'],
                'hits':        d['hits'],
                'last_seen':   fw_info['last_active'] if fw_info else 0,
            })

    # Tertiary: Firewalla-known devices with no traffic (show last_active)
    for ip, fw in fw_all.items():
        if ip in seen_ips:
            continue
        result.append({
            'ip':          ip,
            'hostname':    db.get_dns(ip) or ip,
            'label':       labels.get(ip),
            'fw_name':     fw['name'] or None,
            'fw_mac':      fw['mac'],
            'fw_vendor':   fw['mac_vendor'] or None,
            'fw_group':    fw['group_name'] or None,
            'fw_rx_bytes': fw['fw_rx_bytes'],
            'fw_tx_bytes': fw['fw_tx_bytes'],
            'tx_bytes':    0,
            'rx_bytes':    0,
            'total_bytes': 0,
            'hits':        0,
            'last_seen':   fw['last_active'],
        })

    # Resolve DNS for all device IPs we know about (both conntrack + Firewalla)
    all_ips = list(seen_ips)
    stale   = db.stale_ips(all_ips)
    if stale:
        resolver.resolve_batch_async(stale)
    return jsonify(result)


@app.route('/api/device_bandwidth')
def device_bandwidth():
    ip        = request.args.get('ip', '')
    range_str = request.args.get('range', '24h')
    since     = _since_hour(range_str)
    if ip:
        rows = db.query_device_hourly(ip, since)
        if not rows and firewalla.available() and db.has_fw_connections(since):
            rows = db.query_device_hourly_fw(ip, since)
    else:
        rows = db.query_all_devices_hourly(since)
        if not rows and firewalla.available() and db.has_fw_connections(since):
            rows = db.query_all_devices_hourly_fw(since)
    data = [{'ts': r['hour_ts'],
             'rx': round(r['rx_bytes']*8/3600/1e6, 3),
             'tx': round(r['tx_bytes']*8/3600/1e6, 3)} for r in rows]
    return jsonify(data)


# ── CF tunnel ──────────────────────────────────────────────────────────────────

@app.route('/api/cf_tunnel')
def cf_tunnel():
    range_str = request.args.get('range', '24h')
    rows      = db.query_cf_tunnel(_since_hour(range_str))
    labels    = db.get_all_labels()
    result    = []
    for r in rows:
        ip       = r['service_ip']
        hostname = db.get_dns(ip) or ip
        result.append({
            'service_ip':   ip,
            'hostname':     hostname,
            'label':        labels.get(ip),
            'service_port': r['service_port'],
            'protocol':     r['protocol'],
            'tx_bytes':     r['tx_bytes'],
            'rx_bytes':     r['rx_bytes'],
            'total_bytes':  r['total_bytes'],
            'hits':         r['hit_count'],
        })
    return jsonify(result)


@app.route('/api/cf_tunnel_bandwidth')
def cf_tunnel_bandwidth():
    range_str = request.args.get('range', '24h')
    rows      = db.query_cf_tunnel_hourly(_since_hour(range_str))
    data = [{'ts': r['hour_ts'],
             'rx': round(r['rx_bytes']*8/3600/1e6, 3),
             'tx': round(r['tx_bytes']*8/3600/1e6, 3)} for r in rows]
    return jsonify(data)


# ── device labels ──────────────────────────────────────────────────────────────

@app.route('/api/label', methods=['POST'])
def set_label():
    body  = request.get_json(force=True)
    ip    = body.get('ip', '').strip()
    label = body.get('label', '').strip()
    if not ip:
        return jsonify({'error': 'ip required'}), 400
    db.set_label(ip, label)
    return jsonify({'ok': True})


@app.route('/api/labels')
def get_labels():
    return jsonify(db.get_all_labels())


# ── settings ──────────────────────────────────────────────────────────────────

_SETTING_KEYS = ['firewalla_ip', 'firewalla_ssh_ip', 'local_subnet', 'cf_tunnel_ip']

@app.route('/api/settings', methods=['GET'])
def get_settings():
    vals = db.get_all_settings(_SETTING_KEYS)
    return jsonify(vals)

@app.route('/api/settings', methods=['POST'])
def save_settings():
    body = request.get_json(force=True)
    for key in _SETTING_KEYS:
        if key in body:
            val = body[key].strip() if body[key] else None
            db.set_setting(key, val)
    return jsonify({'ok': True})

@app.route('/api/settings/fw_test')
def fw_test():
    import app.firewalla as fw
    ok, msg = fw.test_connection()
    return jsonify({'ok': ok, 'message': msg})

@app.route('/api/settings/fw_sync', methods=['POST'])
def fw_sync():
    import app.firewalla as fw
    if not fw.available():
        return jsonify({'ok': False, 'message': 'Firewalla IP not configured'})
    ok, msg = fw.test_connection()
    if not ok:
        return jsonify({'ok': False, 'message': msg})
    fw_collector.poll_once()
    count = len(db.get_all_fw_devices())
    return jsonify({'ok': True, 'message': f'Synced — {count} devices in database'})

@app.route('/api/fw_devices')
def fw_devices_list():
    rows   = db.get_all_fw_devices()
    labels = db.get_all_labels()
    result = []
    for d in rows:
        result.append({
            'mac':        d['mac'],
            'ip':         d['ip'],
            'name':       d['name'],
            'mac_vendor': d['mac_vendor'],
            'group_name': d['group_name'],
            'label':      labels.get(d['ip']),
            'last_active': d['last_active'],
        })
    return jsonify(result)


@app.route('/api/fw_poll_health')
def fw_poll_health():
    """SSH-per-poll reliability: success rate + latency over the last 24h/7d."""
    range_str = request.args.get('range', '24h')
    since = int(time.time()) - _RANGES.get(range_str, 86400)
    summary = db.query_fw_poll_summary(since)
    summary['recent_failures'] = db.query_fw_poll_recent_failures(since, limit=10)
    return jsonify(summary)


@app.route('/api/fw_debug')
def fw_debug():
    """Returns raw Firewalla host data for the first 3 devices — use to inspect field names."""
    import app.firewalla as fw
    devices = fw.get_devices()
    return jsonify(devices[:3])


@app.route('/api/fw_flows_debug')
def fw_flows_debug():
    """Returns raw Firewalla flow records from the past hour — use to inspect field names."""
    import app.firewalla as fw
    import time as _time
    end   = int(_time.time())
    begin = end - 3600
    flows = fw.get_flows(begin, end, count=10)
    return jsonify({'count': len(flows), 'sample': flows[:5]})


# ── status ─────────────────────────────────────────────────────────────────────

@app.route('/api/starlink_bandwidth')
def starlink_bandwidth():
    range_str = request.args.get('range', '24h')
    iface     = request.args.get('iface') or None
    use_raw   = range_str in ('1h', '6h', '24h')
    if use_raw:
        rows = db.query_starlink_raw(_since(range_str), iface)
        data = [{'ts': r['ts'],
                 'rx': round(r['rx_rate']*8/1e6, 3),
                 'tx': round(r['tx_rate']*8/1e6, 3)} for r in rows]
    else:
        rows = db.query_starlink_hourly(_since(range_str), iface)
        data = [{'ts': r['hour_ts'],
                 'rx': round(r['rx_bytes']*8/3600/1e6, 3),
                 'tx': round(r['tx_bytes']*8/3600/1e6, 3)} for r in rows]
    return jsonify(data)


@app.route('/api/fw_wan_rates')
def fw_wan_rates():
    rates = starlink_collector.current_rates()
    return jsonify([
        {'iface': iface,
         'name': 'Cox WAN' if iface == 'eth0' else 'Starlink WAN',
         'rx_mbps': round(v['rx']*8/1e6, 3),
         'tx_mbps': round(v['tx']*8/1e6, 3)}
        for iface, v in rates.items()
    ])


@app.route('/api/fw_wan_debug')
def fw_wan_debug():
    """Shows latest WAN collector state — use to diagnose Cox WAN = 0."""
    import time as _t
    since = int(_t.time()) - 3600
    with db._db() as conn:
        rows = conn.execute(
            "SELECT ts, iface, rx_rate, tx_rate FROM starlink_bw_raw "
            "WHERE ts>=? ORDER BY ts DESC LIMIT 20", (since,)
        ).fetchall()
    return jsonify({
        'available':     starlink_collector.available(),
        'last_error':    starlink_collector.last_error(),
        'current_rates': starlink_collector.current_rates(),
        'recent_samples': [dict(r) for r in rows],
    })


@app.route('/api/status')
def status():
    ct = collector.last_collection_times()
    return jsonify({
        'ok': True, 'version': VERSION, 'ts': int(time.time()),
        'starlink_available': starlink_collector.available(),
        'last_bw_ts': ct['bw'],
        'last_conn_ts': ct['conn'],
    })


@app.route('/api/debug')
def debug():
    """Diagnostic endpoint to help troubleshoot data collection issues."""
    import os
    ct = collector.last_collection_times()
    now = int(time.time())
    conntrack_path = os.environ.get('NET_BASE', '/host/net') + '/nf_conntrack'
    conntrack_exists = os.path.exists(conntrack_path)
    conntrack_size = os.path.getsize(conntrack_path) if conntrack_exists else 0

    # Check if there's any data in the database
    since_hour = ((now - 86400) // 3600) * 3600
    conn_count = len(db.query_connections('all', since_hour, limit=1))
    fw_conn_count = len(db.query_fw_connections(since_hour, limit=1)) if db.has_fw_connections(since_hour) else 0

    return jsonify({
        'version': VERSION,
        'now': now,
        'conntrack': {
            'path': conntrack_path,
            'exists': conntrack_exists,
            'size_bytes': conntrack_size,
        },
        'collection': {
            'last_bw_ts': ct['bw'],
            'last_conn_ts': ct['conn'],
            'bw_stale_seconds': now - ct['bw'] if ct['bw'] else None,
            'conn_stale_seconds': now - ct['conn'] if ct['conn'] else None,
        },
        'database': {
            'conn_hourly_has_data': conn_count > 0,
            'fw_conn_hourly_has_data': fw_conn_count > 0,
            'conn_sample_count': conn_count,
            'fw_conn_sample_count': fw_conn_count,
        },
    })


@app.route('/api/maintenance/rebuild_hourly', methods=['POST'])
def rebuild_hourly():
    """Repair endpoint for the pre-0.11 hourly double-counting bug: rebuilds
    every hourly row still covered by raw samples exactly from raw. Run once
    after upgrading. Hours older than the 7-day raw window stay inflated."""
    try:
        rebuilt = db.rebuild_hourly_from_raw()
        return jsonify({'ok': True, 'rows_rebuilt': rebuilt})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/version_check')
def version_check():
    import urllib.request, json as _json
    try:
        url = 'https://api.github.com/repos/frindle/network-bandwidth-monitor/tags'
        req = urllib.request.Request(url, headers={'User-Agent': 'netmon'})
        with urllib.request.urlopen(req, timeout=5) as r:
            tags = _json.loads(r.read())
            latest = tags[0]['name'].lstrip('v') if tags else None
            update = bool(latest and latest != VERSION)
            return jsonify({'current': VERSION, 'latest': latest, 'update_available': update})
    except Exception as e:
        return jsonify({'current': VERSION, 'latest': None, 'update_available': False, 'error': str(e)})


# ── startup ────────────────────────────────────────────────────────────────────

def _shutdown(signum, frame):
    collector.stop()
    fw_collector.stop()
    fw_flows_collector.stop()
    starlink_collector.stop()

# Register shutdown handlers
atexit.register(_shutdown)
if os.environ.get('FLASK_ENV') != 'development':
    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

collector.start()
fw_collector.start()
fw_flows_collector.start()
starlink_collector.start()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080, debug=False)
