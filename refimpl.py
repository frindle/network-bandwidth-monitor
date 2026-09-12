#!/usr/bin/env python3
"""Reference impl for: netmon-telegraf-metrics.

Overwrites the app/metrics.py placeholder with a minimal implementation that
satisfies verify.sh (pure render_line_protocol with correct line-protocol
escaping + a Flask Blueprint route). The gate reverts it before dispatch; it
proves the verify is satisfiable and doubles as the review reference.
"""
import pathlib
import sys

wt = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".")
p = wt / 'app/metrics.py'

BODY = r'''"""Telegraf metrics exporter for netmon (reference impl).

Emits InfluxDB line protocol for the shared TIG stack. All measurements are
namespaced netmon_* so they never collide with ev_* / ollama_* series.
"""
import time

from flask import Blueprint, Response

bp = Blueprint('metrics', __name__)


def _esc_tag(v):
    """Escape a line-protocol tag value: space, comma and equals."""
    return str(v).replace('\\', '\\\\').replace(' ', '\\ ').replace(',', '\\,').replace('=', '\\=')


def _rate_lines(measurement, rates):
    out = []
    for iface, r in (rates or {}).items():
        rx = float(r.get('rx', 0.0))
        tx = float(r.get('tx', 0.0))
        out.append('{m},iface={i} rx_rate={rx},tx_rate={tx}'.format(
            m=measurement, i=_esc_tag(iface), rx=rx, tx=tx))
    return out


def render_line_protocol(interface_rates, wan_rates, poll):
    lines = []
    lines += _rate_lines('netmon_interface', interface_rates)
    lines += _rate_lines('netmon_wan', wan_rates)
    if poll:
        sr = float(poll.get('success_rate') or 0.0)
        al = float(poll.get('avg_latency_ms') or 0.0)
        fa = int(poll.get('failures') or 0)
        lines.append(
            'netmon_fw_poll,window=24h success_rate={sr},avg_latency_ms={al},failures={fa}i'.format(
                sr=sr, al=al, fa=fa))
    return '\n'.join(lines)


@bp.route('/api/metrics')
def metrics():
    import app.collector as collector
    import app.database as db
    interface_rates = collector.current_rates()
    wan_rates = {}
    try:
        poll = db.query_fw_poll_summary(int(time.time()) - 86400)
    except Exception:
        poll = {}
    return Response(render_line_protocol(interface_rates, wan_rates, poll),
                    mimetype='text/plain')
'''

p.write_text(BODY)
print("refimpl applied: wrote", p)
