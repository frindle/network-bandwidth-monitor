import time

from flask import Flask, jsonify, render_template, request

import app.cloudflare as cloudflare
import app.collector as collector
import app.database as db
import app.docker_stats as docker_stats
import app.resolver as resolver

VERSION = '0.3.0'

app = Flask(__name__)

_RANGES = {
    '1h':  3600,        '6h':  21600,
    '24h': 86400,       '7d':  7  * 86400,
    '30d': 30 * 86400,  'all': 10 * 365 * 86400,
}
_PERIOD_SECONDS = {
    '1d': 86400, '7d': 7*86400, '14d': 14*86400, '30d': 30*86400,
}


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
    result    = []
    for r in rows:
        ip       = r['source_ip']
        hostname = db.get_dns(ip) or ip
        result.append({
            'ip':          ip,
            'hostname':    hostname,
            'label':       labels.get(ip),
            'tx_bytes':    r['tx_bytes'],
            'rx_bytes':    r['rx_bytes'],
            'total_bytes': r['total_bytes'],
            'hits':        r['hit_count'],
            'last_seen':   r['last_seen'],
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
    rates  = docker_stats.current_rates()
    known  = db.known_containers()
    seen   = set()
    result = []
    for cid, info in rates.items():
        seen.add(cid)
        result.append({'id': cid, 'name': info['name'],
                        'rx_mbps': round(info['rx']*8/1e6, 3),
                        'tx_mbps': round(info['tx']*8/1e6, 3),
                        'is_cloudflare': 'cloudflare' in info['name'].lower(),
                        'active': True})
    for c in known:
        if c['id'] not in seen:
            result.append({'id': c['id'], 'name': c['name'],
                            'rx_mbps': 0, 'tx_mbps': 0,
                            'is_cloudflare': 'cloudflare' in c['name'].lower(),
                            'active': False})
    result.sort(key=lambda x: (not x['active'], x['name']))
    return jsonify(result)


@app.route('/api/container_bandwidth')
def container_bandwidth():
    cid       = request.args.get('id', '')
    range_str = request.args.get('range', '24h')
    use_raw   = range_str in ('1h', '6h', '24h')
    if use_raw:
        rows = db.query_container_bw_raw(cid, _since(range_str))
        data = [{'ts': r['ts'],
                 'rx': round(r['rx_rate']*8/1e6, 3),
                 'tx': round(r['tx_rate']*8/1e6, 3)} for r in rows]
    else:
        rows = db.query_container_bw_hourly(cid, _since(range_str))
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

_SETTING_KEYS = ['firewalla_ip', 'firewalla_token', 'local_subnet', 'cf_tunnel_container']

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


# ── status ─────────────────────────────────────────────────────────────────────

@app.route('/api/status')
def status():
    return jsonify({
        'ok': True, 'version': VERSION, 'ts': int(time.time()),
        'docker_available': docker_stats.available(),
    })


# ── startup ────────────────────────────────────────────────────────────────────

collector.start()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080, debug=False)
