"""
Firewalla Gold Plus local API poller.
Runs on a background schedule; syncs devices every 5 minutes.
"""
import threading
import time

import app.database as db
import app.firewalla as fw

_lock    = threading.Lock()
_running = False


def _sync_devices():
    devices = fw.get_devices()
    if not devices:
        return
    now = int(time.time())
    for d in devices:
        mac = (d.get('mac') or '').upper().strip()
        if not mac:
            continue
        ip          = d.get('ip') or ''
        name        = d.get('name') or d.get('localDomain') or ''
        mac_vendor  = d.get('macVendor') or ''
        last_active = int(d.get('lastActive') or now)
        group_name  = d.get('type') or ''  # human-readable: "tv", "tablet", "smart speaker", etc.
        fs          = d.get('flowsummary') or {}
        fw_rx_bytes = int(fs.get('inbytes') or 0)
        fw_tx_bytes = int(fs.get('outbytes') or 0)
        db.upsert_fw_device(mac, ip, name, mac_vendor, last_active, group_name, fw_rx_bytes, fw_tx_bytes)


def poll_once():
    """Call from tests or manual refresh."""
    if not fw.available():
        return
    try:
        _sync_devices()
    except Exception:
        pass


def _loop():
    global _running
    while _running:
        poll_once()
        time.sleep(300)  # every 5 minutes


def start():
    global _running
    if _running:
        return
    if not fw.available():
        return
    _running = True
    t = threading.Thread(target=_loop, daemon=True, name='fw_collector')
    t.start()


def stop():
    global _running
    _running = False
