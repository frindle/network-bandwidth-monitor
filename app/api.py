import time

from flask import Flask, jsonify, render_template, request

import app.collector as collector
import app.database as db
import app.resolver as resolver

app = Flask(__name__)

_RANGES = {
    '1h':  3600,
    '6h':  21600,
    '24h': 86400,
    '7d':  7  * 86400,
    '30d': 30 * 86400,
    'all': 10 * 365 * 86400,
}


def _since(range_str: str) -> int:
    return int(time.time()) - _RANGES.get(range_str, 86400)


# ── routes ────────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('index.html')


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
        data = [
            {'ts': r['ts'],
             'rx': round(r['rx_rate'] * 8 / 1e6, 3),
             'tx': round(r['tx_rate'] * 8 / 1e6, 3)}
            for r in rows
        ]
    else:
        rows = db.query_bw_hourly(iface, since)
        data = [
            {'ts': r['hour_ts'],
             'rx': round(r['rx_bytes'] * 8 / 3600 / 1e6, 3),
             'tx': round(r['tx_bytes'] * 8 / 3600 / 1e6, 3)}
            for r in rows
        ]
    return jsonify(data)


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
            'remote_ip':   r['remote_ip'],
            'hostname':    hostname,
            'remote_port': r['remote_port'],
            'protocol':    r['protocol'],
            'tx_bytes':    r['tx_bytes'],
            'rx_bytes':    r['rx_bytes'],
            'total_bytes': r['total_bytes'],
            'hits':        r['hit_count'],
        })

    ips = [r['remote_ip'] for r in rows[:50]]
    stale = db.stale_ips(ips)
    if stale:
        resolver.resolve_batch_async(stale)

    return jsonify(result)


@app.route('/api/status')
def status():
    return jsonify({'ok': True, 'ts': int(time.time()), 'rates': collector.current_rates()})


# ── startup ───────────────────────────────────────────────────────────────────

collector.start()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080, debug=False)
