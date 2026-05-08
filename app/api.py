import time

from flask import Flask, jsonify, render_template, request

import app.cloudflare as cloudflare
import app.collector as collector
import app.database as db
import app.docker_stats as docker_stats
import app.resolver as resolver

VERSION = '0.2.0'

app = Flask(__name__)

_RANGES = {
    '1h':  3600,
    '6h':  21600,
    '24h': 86400,
    '7d':  7  * 86400,
    '30d': 30 * 86400,
    'all': 10 * 365 * 86400,
}

_PERIOD_SECONDS = {
    '1d':  86400,
    '7d':  7  * 86400,
    '14d': 14 * 86400,
    '30d': 30 * 86400,
}


def _since(range_str: str) -> int:
    return int(time.time()) - _RANGES.get(range_str, 86400)


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
        if iface in seen:
            continue
        seen.add(iface)
        r = rates.get(iface, {'rx': 0, 'tx': 0})
        result.append({
            'name':    iface,
            'rx_mbps': round(r['rx'] * 8 / 1e6, 3),
            'tx_mbps': round(r['tx'] * 8 / 1e6, 3),
        })
    result.sort(key=lambda x: x['name'])
    return jsonify(result)


@app.route('/api/bandwidth')
def bandwidth():
    iface     = request.args.get('iface', 'eth0')
    range_str = request.args.get('range', '24h')
    since     = _since(range_str)
    use_raw   = range_str in ('1h', '6h', '24h')

    if use_raw:
        rows = db.query_bw_raw(iface, since)
        data = [{'ts': r['ts'],
                 'rx': round(r['rx_rate'] * 8 / 1e6, 3),
                 'tx': round(r['tx_rate'] * 8 / 1e6, 3)} for r in rows]
    else:
        rows = db.query_bw_hourly(iface, since)
        data = [{'ts': r['hour_ts'],
                 'rx': round(r['rx_bytes'] * 8 / 3600 / 1e6, 3),
                 'tx': round(r['tx_bytes'] * 8 / 3600 / 1e6, 3)} for r in rows]
    return jsonify(data)


@app.route('/api/totals')
def totals():
    """Total bytes in/out per interface or container for common time periods."""
    subject = request.args.get('subject', 'interfaces')  # interfaces | containers
    result  = {}

    for label, seconds in _PERIOD_SECONDS.items():
        since_hour = ((int(time.time()) - seconds) // 3600) * 3600
        if subject == 'interfaces':
            rows = db.query_totals_by_iface(since_hour)
        else:
            rows = db.query_totals_by_container(since_hour)
        result[label] = [dict(r) for r in rows]

    return jsonify(result)


# ── connections ────────────────────────────────────────────────────────────────

@app.route('/api/connections')
def connections():
    iface      = request.args.get('iface', 'all')
    range_str  = request.args.get('range', '24h')
    since_hour = (_since(range_str) // 3600) * 3600

    rows   = db.query_connections(iface, since_hour)
    result = []
    for r in rows:
        hostname = db.get_dns(r['remote_ip']) or r['remote_ip']
        result.append({
            'remote_ip':      r['remote_ip'],
            'hostname':       hostname,
            'remote_port':    r['remote_port'],
            'protocol':       r['protocol'],
            'tx_bytes':       r['tx_bytes'],
            'rx_bytes':       r['rx_bytes'],
            'total_bytes':    r['total_bytes'],
            'hits':           r['hit_count'],
            'is_cloudflare':  cloudflare.is_cloudflare(r['remote_ip']),
        })

    ips   = [r['remote_ip'] for r in rows[:50]]
    stale = db.stale_ips(ips)
    if stale:
        resolver.resolve_batch_async(stale)

    return jsonify(result)


# ── containers ─────────────────────────────────────────────────────────────────

@app.route('/api/containers')
def containers():
    rates = docker_stats.current_rates()
    known = db.known_containers()

    seen   = set()
    result = []

    for cid, info in rates.items():
        seen.add(cid)
        result.append({
            'id':            cid,
            'name':          info['name'],
            'rx_mbps':       round(info['rx'] * 8 / 1e6, 3),
            'tx_mbps':       round(info['tx'] * 8 / 1e6, 3),
            'is_cloudflare': 'cloudflare' in info['name'].lower(),
            'active':        True,
        })

    for c in known:
        if c['id'] not in seen:
            result.append({
                'id':            c['id'],
                'name':          c['name'],
                'rx_mbps':       0,
                'tx_mbps':       0,
                'is_cloudflare': 'cloudflare' in c['name'].lower(),
                'active':        False,
            })

    result.sort(key=lambda x: (not x['active'], x['name']))
    return jsonify(result)


@app.route('/api/container_bandwidth')
def container_bandwidth():
    cid       = request.args.get('id', '')
    range_str = request.args.get('range', '24h')
    since     = _since(range_str)
    use_raw   = range_str in ('1h', '6h', '24h')

    if use_raw:
        rows = db.query_container_bw_raw(cid, since)
        data = [{'ts': r['ts'],
                 'rx': round(r['rx_rate'] * 8 / 1e6, 3),
                 'tx': round(r['tx_rate'] * 8 / 1e6, 3)} for r in rows]
    else:
        rows = db.query_container_bw_hourly(cid, since)
        data = [{'ts': r['hour_ts'],
                 'rx': round(r['rx_bytes'] * 8 / 3600 / 1e6, 3),
                 'tx': round(r['tx_bytes'] * 8 / 3600 / 1e6, 3)} for r in rows]
    return jsonify(data)


# ── status ─────────────────────────────────────────────────────────────────────

@app.route('/api/status')
def status():
    return jsonify({
        'ok':             True,
        'version':        VERSION,
        'ts':             int(time.time()),
        'docker_available': docker_stats.available(),
        'rates':          collector.current_rates(),
    })


# ── startup ────────────────────────────────────────────────────────────────────

collector.start()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080, debug=False)
