"""Adversarial fixture for: netmon-telegraf-metrics

Drives the PURE renderer app.metrics.render_line_protocol(interface_rates,
wan_rates, poll) -- stdlib only, no netmon/DB imports -- against the inputs
that actually break InfluxDB line protocol:

  * tag values containing spaces / commas MUST be backslash-escaped
    (device & interface display names have spaces; an unescaped space would
    be read by Influx as the tag/field separator and corrupt the point)
  * empty input must produce NO stray lines and must not raise
  * numeric fields must be UNQUOTED (a quoted number becomes a string field)
  * every measurement is namespaced `netmon_*` (shared TIG store: must never
    collide with ev_* / ollama_* series)

Each case: (description, callable_returning_actual, expected)
"""
import re
import sys
import importlib.util

spec = importlib.util.spec_from_file_location("target", 'app/metrics.py')
target = importlib.util.module_from_spec(spec)
spec.loader.exec_module(target)

R = target.render_line_protocol


def _lines(out):
    return [ln for ln in out.splitlines() if ln.strip()]


def basic_interface_line():
    out = R({'eth0': {'rx': 1.5, 'tx': 2.5}}, {}, None)
    return 'netmon_interface,iface=eth0 rx_rate=1.5,tx_rate=2.5' in out


def escapes_space_and_comma_in_tags():
    # an interface/display name with a space AND a comma -- both must escape
    out = R({'br 0,x': {'rx': 1.0, 'tx': 2.0}}, {}, None)
    ok_escaped = 'iface=br\\ 0\\,x' in out       # space->'\ ' comma->'\,'
    raw_present = 'iface=br 0,x' in out           # raw unescaped must NOT appear
    return ok_escaped and not raw_present


def empty_input_no_lines():
    out = R({}, {}, None)
    return _lines(out) == []


def all_measurements_namespaced():
    out = R({'eth0': {'rx': 1.0, 'tx': 2.0}},
            {'wan_cox': {'rx': 3.0, 'tx': 4.0}},
            {'success_rate': 99.5, 'avg_latency_ms': 12.0, 'failures': 1})
    for ln in _lines(out):
        meas = ln.split(',')[0].split(' ')[0]
        if not meas.startswith('netmon_'):
            return False
    return True


def numeric_fields_unquoted():
    out = R({'eth0': {'rx': 1.0, 'tx': 2.0}}, {}, None)
    return 'rx_rate="' not in out and 'tx_rate="' not in out


def wan_series_emitted():
    out = R({}, {'wan_cox': {'rx': 10.0, 'tx': 20.0}}, None)
    return 'netmon_wan,iface=wan_cox' in out


def poll_health_emitted():
    out = R({}, {}, {'success_rate': 99.5, 'avg_latency_ms': 12.0, 'failures': 1})
    return ('netmon_fw_poll' in out and 'success_rate=99.5' in out
            and 'avg_latency_ms=12.0' in out and 'failures=1i' in out)


def missing_rate_keys_default_zero():
    # a rate dict missing 'tx' (or 'rx') must default that field to 0.0, not
    # some other constant, and must not raise
    out_tx = R({'eth0': {'rx': 5.0}}, {}, None)
    out_rx = R({'eth0': {'tx': 7.0}}, {}, None)
    return ('tx_rate=0.0' in out_tx and 'rx_rate=5.0' in out_tx
            and 'rx_rate=0.0' in out_rx and 'tx_rate=7.0' in out_rx)


def route_emits_line_protocol():
    # exercise the Flask Blueprint route end-to-end with stubbed netmon deps,
    # so the route handler (lazy imports + gather + Response) is covered without
    # the real collector/database (which need Python 3.10+ to import).
    import sys as _sys
    import types as _types
    fake_app = _types.ModuleType('app')
    fake_app.__path__ = []
    fake_collector = _types.ModuleType('app.collector')
    fake_collector.current_rates = lambda: {'eth0': {'rx': 3.0, 'tx': 4.0}}
    fake_db = _types.ModuleType('app.database')
    fake_db.query_fw_poll_summary = lambda since: {
        'success_rate': 50.0, 'avg_latency_ms': 5.0, 'failures': 2}
    saved = {k: _sys.modules.get(k) for k in ('app', 'app.collector', 'app.database')}
    _sys.modules['app'] = fake_app
    _sys.modules['app.collector'] = fake_collector
    _sys.modules['app.database'] = fake_db
    try:
        from flask import Flask
        flask_app = Flask('t')
        flask_app.register_blueprint(target.bp)
        client = flask_app.test_client()
        resp = client.get('/api/metrics')
        body = resp.get_data(as_text=True)
        ok = (resp.status_code == 200
              and 'text/plain' in resp.headers.get('Content-Type', '')
              and 'netmon_interface,iface=eth0 rx_rate=3.0,tx_rate=4.0' in body)
    finally:
        for k, v in saved.items():
            if v is None:
                _sys.modules.pop(k, None)
            else:
                _sys.modules[k] = v
    return ok


def one_unescaped_separator_per_line():
    # every emitted line is `measurement,tagset<space>fieldset` with exactly ONE
    # unescaped space (the separator). Strip escaped spaces first, then count.
    out = R({'eth 0': {'rx': 1.0, 'tx': 2.0}},
            {'wan_cox': {'rx': 3.0, 'tx': 4.0}}, None)
    for ln in _lines(out):
        unescaped = re.sub(r'\\ ', '', ln)
        if unescaped.count(' ') != 1:
            return False
    return True


CASES = [
    ("basic interface line is correct line protocol", basic_interface_line, True),
    ("tag values escape spaces and commas", escapes_space_and_comma_in_tags, True),
    ("empty input emits no lines and does not raise", empty_input_no_lines, True),
    ("every measurement is namespaced netmon_*", all_measurements_namespaced, True),
    ("numeric fields are unquoted (real floats, not strings)", numeric_fields_unquoted, True),
    ("WAN series is emitted as netmon_wan", wan_series_emitted, True),
    ("poll-health series carries all fields (success_rate/avg_latency/failures)", poll_health_emitted, True),
    ("missing rate keys default to 0.0", missing_rate_keys_default_zero, True),
    ("exactly one unescaped tag/field separator per line", one_unescaped_separator_per_line, True),
    ("GET /api/metrics route emits line protocol (text/plain)", route_emits_line_protocol, True),
]


def main():
    if len(CASES) < 3:
        print("  SCAFFOLD_INCOMPLETE: need >= 3 cases.")
        return 1
    fails = 0
    for desc, thunk, want in CASES:
        try:
            got = thunk()
        except Exception as e:
            print("  FAIL {} -- raised {}: {}".format(desc, type(e).__name__, e))
            fails += 1
            continue
        if got != want:
            print("  FAIL {} -- got {!r}, want {!r}".format(desc, got, want))
            fails += 1
    print("  {}/{} case(s) passed".format(len(CASES) - fails, len(CASES)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
