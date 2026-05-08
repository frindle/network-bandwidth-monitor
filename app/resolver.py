import socket
import threading

import app.database as db


def _resolve(ip: str):
    try:
        hostname = socket.gethostbyaddr(ip)[0]
    except (socket.herror, socket.gaierror, OSError):
        hostname = ip
    db.cache_dns(ip, hostname)


def resolve_batch_async(ips: list):
    for ip in ips:
        threading.Thread(target=_resolve, args=(ip,), daemon=True).start()
