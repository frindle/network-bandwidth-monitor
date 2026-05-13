import socket
import threading
from concurrent.futures import ThreadPoolExecutor

import app.database as db

_executor = ThreadPoolExecutor(max_workers=10, thread_name_prefix='dns_resolver')


def _resolve(ip: str):
    try:
        hostname = socket.gethostbyaddr(ip)[0]
    except (socket.herror, socket.gaierror, OSError):
        hostname = ip
    db.cache_dns(ip, hostname)


def resolve_batch_async(ips: list):
    for ip in ips:
        _executor.submit(_resolve, ip)
