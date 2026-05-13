"""
Polls Firewalla's /v1/flow API every 5 minutes to build a per-destination
connection table for ALL LAN devices (not just Unraid containers).
"""
import threading
import time

import app.database as db
import app.firewalla as fw

_lock    = threading.Lock()
_running = False
_last_ts = 0   # track last poll time to avoid re-processing flows


def _get(flow, *keys, default=None):
    """Try multiple field names, return first non-None match."""
    for k in keys:
        v = flow.get(k)
        if v is not None:
            return v
    return default


def _proto(val) -> str:
    if isinstance(val, int):
        return 'udp' if val == 17 else 'tcp'
    if isinstance(val, str):
        return val.lower()[:3]
    return 'tcp'


def _collect():
    global _last_ts
    now   = int(time.time())
    begin = _last_ts if _last_ts else now - 360
    begin = max(begin - 30, now - 3600)   # max 1h lookback, 30s overlap
    flows = fw.get_flows(begin, now, count=2000)
    if not flows:
        return

    processed = 0
    for flow in flows:
        ts      = int(_get(flow, 'ts', 'timestamp', default=now))
        hour_ts = (ts // 3600) * 3600

        # Source: the LAN device (sh=source host, lh=local host)
        src_ip  = str(_get(flow, 'sh', 'src', 'sourceIP', default='') or '')
        # Destination: external host (dh=dest host, rh=remote host)
        dst_ip  = str(_get(flow, 'dh', 'dst', 'destIP', 'rh', default='') or '')
        # Domain name captured via DNS/SNI
        domain  = str(_get(flow, 'dn', 'domain', 'hostname', 'h', default='') or '')
        # Bytes
        ob      = int(_get(flow, 'ob', 'upload', 'tx', 'sb', default=0) or 0)
        rb      = int(_get(flow, 'rb', 'download', 'rx', 'rb', default=0) or 0)
        proto   = _proto(_get(flow, 'pr', 'protocol', default=6))
        dport   = int(_get(flow, 'dp', 'dport', 'port', 'p', default=0) or 0)
        # Direction: skip inbound-only flows (fd='in') to avoid double-counting
        fd      = _get(flow, 'fd', 'direction', default='out')
        if fd == 'in':
            continue

        if not dst_ip or dst_ip == src_ip:
            continue

        db.upsert_fw_conn(hour_ts, src_ip, dst_ip, domain, proto, dport, ob, rb)
        processed += 1

    if flows:
        _last_ts = now


def start():
    global _running
    with _lock:
        if _running or not fw.available():
            return
        _running = True
    t = threading.Thread(target=_loop, daemon=True, name='fw_flows_collector')
    t.start()


def _loop():
    global _running
    while _running:
        try:
            _collect()
        except Exception:
            import traceback
            traceback.print_exc()
        time.sleep(300)


def stop():
    global _running
    _running = False
