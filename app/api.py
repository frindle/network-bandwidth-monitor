import time

from flask import Flask, jsonify, render_template, request

import app.cloudflare as cloudflare
import app.collector as collector
import app.database as db
import app.docker_stats as docker_stats
import app.fw_collector as fw_collector
import app.resolver as resolver
import app.starlink_collector as starlink_collector

VERSION = '0.9.0'

app = Flask(__name__)

_RANGES = {
    '1h':  3600,        '6h':  21600,
    '24h': 86400,       '7d':  7  * 86400,
    '30d': 30 * 86400,  'all': 10 * 365 * 86400,
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

def _detect_service(hostname: str) -> str | None:
    if not hostname:
        return None
    h = hostname.lower()
    for frag, name in _SERVICE_PATTERNS:
        if frag in h:
            return name
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
    subject = request.args.get('subject', 'interfaces')
    result  = {}
    for label, seconds in _PERIOD_SECONDS.items():
        sh = ((int(time.time()) - seconds) // 3600) * 3600
        rows = db.query_totals_by_iface(sh) if subject == 'interfaces' \
               else db.query_totals_by_container(sh)
        result[label] = [dict(r) for r in rows]
    return jsonify(result)


# ── connections ────────────────────────────────────────────────────────────────

@app.route('/api/connections')
def connections():
    iface     = request.args.get('iface', 'all')
    source_ip = request.args.get('source') or None
    range_str = request.args.get('range', '24h')
    rows      = db.query_connections(iface, _since_hour(range_str), source_ip=source_ip)
    labels    = db.get_all_labels()
    result    = []
    for r in rows:
        hostname = db.get_dns(r['remote_ip']) or r['remote_ip']
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
        })
    ips   = [r['remote_ip'] for r in rows[:50]]
    stale = db.stale_ips(ips)
    if stale:
        resolver.resolve_batch_async(stale)
    return jsonify(result)


# ── devices ────────────────────────────────────────────────────────────────────

@app.route('/api/devices')
def devices():
    range_str = request.args.get('range', '24h')
    rows      = db.query_devices(_since_hour(range_str))
    labels    = db.get_all_labels()
    fw_all    = {d['ip']: d for d in db.get_all_fw_devices() if d['ip']}
    result    = []
    seen_ips  = set()

    for r in rows:
        ip      = r['source_ip']
        if not collector.is_local(ip):   # skip Docker 172.x.x.x and other non-LAN IPs
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

    # Include Firewalla-known devices that have no conntrack traffic yet
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

    ips   = [r['source_ip'] for r in rows[:50]]
    stale = db.stale_ips(ips)
    if stale:
        resolver.resolve_batch_async(stale)
    return jsonify(result)


@app.route('/api/device_bandwidth')
def device_bandwidth():
    ip        = request.args.get('ip', '')
    range_str = request.args.get('range', '24h')
    rows      = db.query_device_hourly(ip, _since_hour(range_str))
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


# ── containers ─────────────────────────────────────────────────────────────────

@app.route('/api/containers')
def containers():
    running    = docker_stats.list_running()   # {id: name}
    rates      = docker_stats.current_rates()  # {id: {name,rx,tx}}
    known      = db.known_containers()         # [{id,name}]
    since_hour = _since_hour('24h')
    totals     = {r['container_name']: r for r in db.query_totals_by_container(since_hour)}

    seen_names = set()
    result = []

    for cid, name in running.items():
        if name in seen_names:
            continue
        seen_names.add(name)
        r = rates.get(cid, {})
        t = totals.get(name, {})
        result.append({'id': cid, 'name': name,
                        'rx_mbps':   round(r.get('rx', 0)*8/1e6, 3),
                        'tx_mbps':   round(r.get('tx', 0)*8/1e6, 3),
                        'rx_bytes':  t.get('rx_bytes', 0) or 0,
                        'tx_bytes':  t.get('tx_bytes', 0) or 0,
                        'total_bytes': t.get('total_bytes', 0) or 0,
                        'is_cloudflare': 'cloudflare' in name.lower(),
                        'active': True})

    for c in known:
        if c['name'] in seen_names:
            continue
        seen_names.add(c['name'])
        t = totals.get(c['name'], {})
        result.append({'id': c['id'], 'name': c['name'],
                        'rx_mbps': 0, 'tx_mbps': 0,
                        'rx_bytes':  t.get('rx_bytes', 0) or 0,
                        'tx_bytes':  t.get('tx_bytes', 0) or 0,
                        'total_bytes': t.get('total_bytes', 0) or 0,
                        'is_cloudflare': 'cloudflare' in c['name'].lower(),
                        'active': False})

    result.sort(key=lambda x: (not x['active'], -(x['total_bytes'] or 0)))
    return jsonify(result)


@app.route('/api/container_purge', methods=['POST'])
def container_purge():
    cid = request.get_json(force=True).get('id', '').strip()
    if not cid:
        return jsonify({'error': 'id required'}), 400
    db.purge_container(cid)
    return jsonify({'ok': True})

@app.route('/api/container_purge_inactive', methods=['POST'])
def container_purge_inactive():
    active_ids = list(collector.current_rates().keys()) if False else []
    import app.docker_stats as ds
    active_ids = list(ds.current_rates().keys())
    db.purge_all_inactive_containers(active_ids)
    return jsonify({'ok': True})

@app.route('/api/container_bandwidth')
def container_bandwidth():
    name      = request.args.get('name') or request.args.get('id', '')
    range_str = request.args.get('range', '24h')
    use_raw   = range_str in ('1h', '6h', '24h')
    if use_raw:
        rows = db.query_container_bw_raw(name, _since(range_str))
        data = [{'ts': r['ts'],
                 'rx': round(r['rx_rate']*8/1e6, 3),
                 'tx': round(r['tx_rate']*8/1e6, 3)} for r in rows]
    else:
        rows = db.query_container_bw_hourly(name, _since(range_str))
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

_SETTING_KEYS = ['firewalla_ip', 'firewalla_port', 'firewalla_token', 'firewalla_ssh_ip', 'local_subnet', 'cf_tunnel_container']

@app.route('/api/settings', methods=['GET'])
def get_settings():
    vals = db.get_all_settings(_SETTING_KEYS)
    # Mask token — send a boolean so UI knows if it's set, not the value
    if 'firewalla_token' in vals:
        vals['firewalla_token_set'] = True
        vals['firewalla_token'] = ''
    return jsonify(vals)

@app.route('/api/settings', methods=['POST'])
def save_settings():
    body = request.get_json(force=True)
    for key in _SETTING_KEYS:
        if key in body:
            val = body[key].strip() if body[key] else None
            # Don't overwrite token if blank was sent (masked field)
            if key == 'firewalla_token' and not val:
                continue
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


@app.route('/api/fw_debug')
def fw_debug():
    """Returns raw Firewalla host data for the first 3 devices — use to inspect field names."""
    import app.firewalla as fw
    devices = fw.get_devices()
    return jsonify(devices[:3])


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


@app.route('/api/status')
def status():
    ct = collector.last_collection_times()
    return jsonify({
        'ok': True, 'version': VERSION, 'ts': int(time.time()),
        'docker_available': docker_stats.available(),
        'starlink_available': starlink_collector.available(),
        'last_bw_ts': ct['bw'],
        'last_conn_ts': ct['conn'],
    })


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

collector.start()
fw_collector.start()
starlink_collector.start()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080, debug=False)
